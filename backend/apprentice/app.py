"""Apprentice API and web app.

Run:  uvicorn apprentice.app:app --app-dir backend --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import UnidentifiedImageError
from pydantic import BaseModel

from . import animation, car, images as imgs, lessons, mock, passport, sample, training
from .config import get_settings
from .ingest import ingest_manual
from .muse import MuseClient, MuseError
from .store import Store, new_id
from .trace import Tracer
from .vision import PrivacyError, Vision

STATIC = Path(__file__).resolve().parents[1] / "static"
USE_CASES = {"assembly", "car", "training"}
MODES = {"cloud", "hybrid"}


class State:
    settings = None
    store: Store
    tracer: Tracer
    muse: MuseClient
    vision: Vision
    tasks: set


S = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.settings = get_settings()
    S.store = Store(S.settings.data_dir)
    S.tracer = Tracer(S.settings.trace_dir)
    S.muse = MuseClient(S.settings, S.tracer, mock_responder=mock.respond)
    S.vision = Vision(S.settings, S.muse, S.tracer)
    S.tasks = set()
    S.live_fails = set()  # (session, step, live run) that already logged a caught mistake
    yield
    await S.muse.aclose()


app = FastAPI(title="Apprentice", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
app.mount("/files", StaticFiles(directory=str(get_settings().data_dir)), name="files")


# ---------------------------------------------------------------- errors

@app.exception_handler(PrivacyError)
async def _privacy(_: Request, exc: PrivacyError):
    return JSONResponse({"error": str(exc), "kind": "privacy"}, status_code=409)


@app.exception_handler(MuseError)
async def _muse(_: Request, exc: MuseError):
    return JSONResponse({"error": str(exc), "kind": "model"}, status_code=502)


# ---------------------------------------------------------------- helpers

async def read_photo(photo: UploadFile, *, folder: tuple[str, ...], name: str, max_side: Optional[int] = None):
    data = await photo.read()
    if len(data) > S.settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"Photo is larger than {S.settings.max_upload_mb} MB.")
    try:
        img = imgs.load(data)
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(400, "That file is not a photo the app can read. Use JPEG or PNG.") from exc
    img = imgs.fit(img, max_side or S.settings.max_image_side)
    path = imgs.save(img, S.store.path(*folder, name))
    return img, {"url": S.store.url(path), "width": img.width, "height": img.height}


def ready_manual(manual_id: str) -> dict:
    m = S.store.manual(manual_id)
    if not m:
        raise HTTPException(404, "No such manual.")
    if m["status"] != "ready" or not m["plan"]:
        raise HTTPException(409, f"The manual is not ready yet ({m['progress']}).")
    return m


def session_or_404(sid: str) -> dict:
    s = S.store.session(sid)
    if not s:
        raise HTTPException(404, "No such session.")
    return s


def check_mode(mode: str) -> bool:
    if mode not in MODES:
        raise HTTPException(400, "mode must be cloud or hybrid")
    return mode == "hybrid"


def figure_image(plan: dict, order: int):
    step = training.step_by_order(plan, order)
    url = (step or {}).get("figure_url")
    if not url:
        return None
    path = S.settings.data_dir / url.removeprefix("/files/")
    return imgs.open_path(path) if path.exists() else None


def reference_image(plan: dict):
    url = plan.get("reference_url")
    if not url:
        return None
    path = S.settings.data_dir / url.removeprefix("/files/")
    return imgs.open_path(path) if path.exists() else None


def start_ingest(manual_id: str, pdf: Path, title: str, use_case: str, rename: bool = False) -> None:
    task = asyncio.create_task(ingest_manual(manual_id, pdf, title, use_case, store=S.store, muse=S.muse,
                                             settings=S.settings, rename_to_product=rename))
    S.tasks.add(task)
    task.add_done_callback(S.tasks.discard)


# ---------------------------------------------------------------- pages

@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
async def status():
    s = S.settings
    return {
        "app": "apprentice",
        "mock": s.mock,
        "vision_backend": s.vision_backend,
        "private_available": S.vision.private_available,
        "models": {"spark": s.spark_model, "wonder": s.wonder_model, "sam": s.sam_model},
        "min_confidence": s.min_confidence,
        "key_set": bool(s.api_key),
    }


# ---------------------------------------------------------------- manuals

@app.get("/api/manuals")
async def list_manuals():
    return S.store.manuals()


@app.post("/api/manuals")
async def upload_manual(file: UploadFile = File(...), title: str = Form(""), use_case: str = Form("assembly")):
    if use_case not in USE_CASES:
        raise HTTPException(400, "use_case must be assembly, car or training")
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Upload the manual as a PDF.")
    if len(data) > S.settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"The PDF is larger than {S.settings.max_upload_mb} MB.")
    typed = title.strip()
    title = typed or re.sub(r"[_-]+", " ", Path(file.filename or "Manual").stem).strip() or "Manual"
    mid = S.store.create_manual(title, use_case)
    pdf = S.store.path("manuals", mid, "source.pdf")
    pdf.write_bytes(data)
    start_ingest(mid, pdf, title, use_case, rename=not typed)  # no typed name: use the product name the model reads
    return {"id": mid, "title": title, "status": "processing"}


@app.post("/api/manuals/sample")
async def sample_manual():
    existing = next((m for m in S.store.manuals() if m["title"] == sample.PRODUCT and m["status"] == "ready"), None)
    if existing:
        return {"id": existing["id"], "title": existing["title"], "status": "ready"}
    pdf = S.settings.data_dir / "sample" / "birch_step_stool.pdf"
    if not pdf.exists():
        await asyncio.to_thread(sample.build_pdf, pdf)
    mid = S.store.create_manual(sample.PRODUCT, "assembly")
    copy = S.store.path("manuals", mid, "source.pdf")
    copy.write_bytes(pdf.read_bytes())
    start_ingest(mid, copy, sample.PRODUCT, "assembly")
    return {"id": mid, "title": sample.PRODUCT, "status": "processing"}


@app.get("/api/manuals/{manual_id}")
async def get_manual(manual_id: str):
    m = S.store.manual(manual_id)
    if not m:
        raise HTTPException(404, "No such manual.")
    return m


@app.get("/api/manuals/{manual_id}/steps/{order}/animation")
async def step_animation(manual_id: str, order: int, refresh: bool = False):
    """3D animation of one step (generated once by Muse Wonder from the manual's diagram, then cached)."""
    plan = ready_manual(manual_id)["plan"]
    try:
        return await animation.step_animation(S.store, S.muse, S.settings, manual_id, plan, order,
                                              figure_image(plan, order), refresh=refresh)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------- training sessions

class NewSession(BaseModel):
    person: str
    manual_id: str
    mode: str = "cloud"


class ModeChange(BaseModel):
    mode: str


class Override(BaseModel):
    verdict: str


@app.post("/api/sessions")
async def new_session(body: NewSession):
    ready_manual(body.manual_id)
    check_mode(body.mode)
    person = body.person.strip() or "Trainee"
    sid = S.store.create_session(person, body.manual_id, body.mode)
    return S.store.session(sid)


@app.get("/api/sessions/{sid}")
async def get_session(sid: str):
    s = session_or_404(sid)
    return {**s, "results": S.store.results(sid)}


@app.post("/api/sessions/{sid}/mode")
async def set_mode(sid: str, body: ModeChange):
    session_or_404(sid)
    check_mode(body.mode)
    S.store.set_mode(sid, body.mode)
    return S.store.session(sid)


@app.post("/api/sessions/{sid}/inventory")
async def session_inventory(sid: str, photo: UploadFile = File(...)):
    s = session_or_404(sid)
    plan = ready_manual(s["manual_id"])["plan"]
    img, info = await read_photo(photo, folder=("sessions", sid), name="inventory.jpg")
    result = await training.inventory(img, plan, vision=S.vision, settings=S.settings, private=s["mode"] == "hybrid",
                                      reference=reference_image(plan), ctx={"session_id": sid})
    result["image"] = info
    S.store.set_inventory(sid, {k: result[k] for k in ("rows", "complete", "summary", "missing")} | {"photo": info["url"]})
    return result


@app.post("/api/sessions/{sid}/steps/{order}/parts")
async def step_parts(sid: str, order: int, photo: UploadFile = File(...)):
    s = session_or_404(sid)
    plan = ready_manual(s["manual_id"])["plan"]
    img, info = await read_photo(photo, folder=("sessions", sid), name=f"step{order:02d}_parts_{new_id('p')}.jpg")
    try:
        result = await training.show_step_parts(img, plan, order, vision=S.vision, private=s["mode"] == "hybrid",
                                                ctx={"session_id": sid, "step": order})
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    result["image"] = info
    return result


@app.post("/api/sessions/{sid}/steps/{order}/check")
async def step_check(sid: str, order: int, photo: UploadFile = File(...)):
    s = session_or_404(sid)
    plan = ready_manual(s["manual_id"])["plan"]
    attempt = S.store.attempts(sid, order) + 1
    img, info = await read_photo(photo, folder=("sessions", sid), name=f"step{order:02d}_check{attempt}.jpg")
    try:
        result = await training.check_step(img, plan, order, figure_image(plan, order), muse=S.muse, vision=S.vision,
                                           settings=S.settings, private=s["mode"] == "hybrid", attempt=attempt,
                                           ctx={"session_id": sid, "step": order})
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    saved = S.store.add_result(sid, order, result["verdict"], confidence=result["confidence"], reason=result["reason"],
                               fix=result["fix"], observed=result["observed"], photo=info["url"],
                               detections=result.get("detections"))
    return {**result, "result_id": saved["id"], "attempt": saved["attempt"], "image": info}


@app.post("/api/sessions/{sid}/steps/{order}/live")
async def step_live(sid: str, order: int, photo: UploadFile = File(...), frame: int = Form(1),
                    live_id: str = Form(""), history: str = Form("[]")):
    """One frame of a live camera check. Returns a verdict and one instruction to speak right now.

    Not every frame is stored: the first mistake seen in a live run and the final pass are recorded
    as step results (with their frame), so the passport counts them and the export has the photos.
    """
    s = session_or_404(sid)
    plan = ready_manual(s["manual_id"])["plan"]
    try:
        past = [h for h in json.loads(history or "[]") if isinstance(h, dict)][-3:]
    except ValueError:
        past = []
    img, info = await read_photo(photo, folder=("sessions", sid), name=f"live_step{order:02d}.jpg",
                                 max_side=S.settings.live_image_side)
    try:
        result = await training.live_check(img, plan, order, figure_image(plan, order), muse=S.muse, vision=S.vision,
                                           settings=S.settings, private=s["mode"] == "hybrid", frame=frame,
                                           history=past, ctx={"session_id": sid, "step": order, "frame": frame})
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    recorded, result_id = None, None
    key = (sid, order, live_id or "default")
    if result["verdict"] == "pass" or (result["verdict"] == "fail" and key not in S.live_fails):
        if result["verdict"] == "fail":
            S.live_fails.add(key)
        kept = imgs.save(img, S.store.path("sessions", sid, f"step{order:02d}_live_{result['verdict']}_{new_id('f')}.jpg"))
        saved = S.store.add_result(sid, order, result["verdict"], confidence=result["confidence"], reason=result["reason"],
                                   fix=result["fix"], observed=result["observed"], photo=S.store.url(kept),
                                   detections=result.get("detections"))
        recorded, result_id = result["verdict"], saved["id"]
    return {**result, "frame": frame, "recorded": recorded, "result_id": result_id, "image": info}


@app.post("/api/results/{rid}/override")
async def override(rid: str, body: Override):
    if body.verdict not in {"pass", "fail"}:
        raise HTTPException(400, "verdict must be pass or fail")
    S.store.override_result(rid, body.verdict)
    return {"ok": True}


@app.post("/api/sessions/{sid}/finish")
async def finish(sid: str):
    session_or_404(sid)
    S.store.finish_session(sid)
    row = next((r for r in passport.passport(S.store) if r["session_id"] == sid), None)
    return row


# ---------------------------------------------------------------- learn any concept

@app.post("/api/lessons")
async def lesson(concept: str = Form(...), mode: str = Form("cloud"), photo: UploadFile = File(...)):
    private = check_mode(mode)
    concept = concept.strip()[:200]
    if not concept:
        raise HTTPException(400, "Say or type what you want to learn.")
    lid = new_id("room")
    img, info = await read_photo(photo, folder=("lessons",), name=f"{lid}.jpg")
    result = await lessons.build_lesson(concept, img, muse=S.muse, vision=S.vision, settings=S.settings,
                                        private=private, ctx={"lesson": lid})
    result["image"] = info
    if "lesson" in result:
        result["id"] = S.store.save_lesson(concept, info["url"], result)
    return result


# ---------------------------------------------------------------- car fixes

def optional_plan(manual_id: Optional[str]) -> Optional[dict]:
    if not manual_id:
        return None
    return ready_manual(manual_id)["plan"]


@app.post("/api/car/engine-bay")
async def engine_bay(photo: UploadFile = File(...), manual_id: str = Form(""), mode: str = Form("cloud")):
    private = check_mode(mode)
    plan = optional_plan(manual_id)
    img, info = await read_photo(photo, folder=("car",), name=f"{new_id('bay')}.jpg")
    result = await car.engine_bay(img, plan, vision=S.vision, private=private, ctx={"car": manual_id or "default"})
    result["image"] = info
    return result


@app.post("/api/car/warning-lights")
async def warning_lights(photo: UploadFile = File(...), manual_id: str = Form(""), mode: str = Form("cloud")):
    private = check_mode(mode)
    plan = optional_plan(manual_id)
    img, info = await read_photo(photo, folder=("car",), name=f"{new_id('dash')}.jpg")
    result = await car.warning_lights(img, plan, muse=S.muse, settings=S.settings, private=private,
                                      ctx={"car": manual_id or "default"})
    result["image"] = info
    return result


# ---------------------------------------------------------------- passport, export, traces

@app.get("/api/passport")
async def get_passport():
    return passport.passport(S.store)


@app.get("/api/export/robot-data")
async def robot_data():
    data = passport.robot_export(S.store)
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="apprentice-episodes.json"'})


@app.get("/api/traces")
async def traces(limit: int = 100):
    return S.tracer.latest(min(max(limit, 1), 500))


@app.get("/api/traces/stream")
async def trace_stream(request: Request):
    async def events():
        yield "retry: 3000\n\n"
        stream = S.tracer.stream()
        while True:
            if await request.is_disconnected():
                break
            try:
                entry = await asyncio.wait_for(stream.__anext__(), timeout=15)
                yield f"data: {json.dumps(entry, default=str)}\n\n"
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

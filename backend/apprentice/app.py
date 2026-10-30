"""ShowMe API and web app.

Run:  uvicorn apprentice.app:app --app-dir backend --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import UnidentifiedImageError
from pydantic import BaseModel

from . import animation, car, devices, images as imgs, insights, lessons, mock, passport, sample, sample_device, training
from .config import get_settings
from .ingest import ingest_manual
from .muse import MuseClient, MuseError
from .store import Store, new_id
from .trace import Tracer
from .vision import PrivacyError, Vision

STATIC = Path(__file__).resolve().parents[1] / "static"
USE_CASES = {"assembly", "car", "training", "device"}
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


app = FastAPI(title="ShowMe", lifespan=lifespan)
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
        "app": "showme",
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
        raise HTTPException(400, "use_case must be assembly, training, device or car")
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
async def sample_manual(kind: str = "assembly"):
    """The fictional sample manuals: a step stool (assembly) or a coffee maker (device)."""
    mod, use, name = ((sample_device, "device", "brewline_one.pdf") if kind == "device"
                      else (sample, "assembly", "birch_step_stool.pdf"))
    existing = next((m for m in S.store.manuals()
                     if m["title"] == mod.PRODUCT and m["use_case"] == use and m["status"] == "ready"), None)
    if existing:
        return {"id": existing["id"], "title": existing["title"], "status": "ready"}
    pdf = S.settings.data_dir / "sample" / name
    if not pdf.exists():
        await asyncio.to_thread(mod.build_pdf, pdf)
    mid = S.store.create_manual(mod.PRODUCT, use)
    copy = S.store.path("manuals", mid, "source.pdf")
    copy.write_bytes(pdf.read_bytes())
    start_ingest(mid, copy, mod.PRODUCT, use)
    return {"id": mid, "title": mod.PRODUCT, "status": "processing"}


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


@app.get("/api/manuals/{manual_id}/ledger")
async def manual_ledger(manual_id: str):
    """How much loose hardware should be left after each step."""
    return training.ledger_plan(ready_manual(manual_id)["plan"])


@app.get("/api/manuals/{manual_id}/steps/{order}/heads-up")
async def heads_up(manual_id: str, order: int):
    """The mistake earlier builders most often made at this step, said before the next person starts it."""
    plan = ready_manual(manual_id)["plan"]
    try:
        return await insights.heads_up(S.store, S.muse, S.settings, manual_id, plan, order)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------- machines and electronic devices

class Question(BaseModel):
    question: str


@app.post("/api/manuals/{manual_id}/tour")
async def device_tour(manual_id: str, photo: UploadFile = File(...), mode: str = Form("cloud")):
    """One photo of the machine or device: every control the manual names, labeled with what it does."""
    private = check_mode(mode)
    plan = ready_manual(manual_id)["plan"]
    img, info = await read_photo(photo, folder=("manuals", manual_id, "tours"), name=f"{new_id('t')}.jpg")
    result = await devices.tour(img, plan, vision=S.vision, private=private, ctx={"manual_id": manual_id})
    result["image"] = info
    return result


@app.post("/api/manuals/{manual_id}/ask")
async def device_ask(manual_id: str, body: Question):
    """'How do I ...?' answered from the manual, as a guided task when there is something to do."""
    question = body.question.strip()[:300]
    if len(question) < 3:
        raise HTTPException(400, "Type or say a question first.")
    parent = ready_manual(manual_id)
    if parent["use_case"] == "task":
        parent = ready_manual(parent["plan"]["parent_manual_id"])
    return await devices.ask(question, parent, store=S.store, muse=S.muse, settings=S.settings)


# ---------------------------------------------------------------- QR packs: scan the box, start building

class NewPack(BaseModel):
    manual_id: str
    brand: str = ""


def lan_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def public_base(request: Request) -> str:
    """Where a phone that scans the QR code can reach this app."""
    if S.settings.public_url:
        return S.settings.public_url
    host = request.url.hostname or "localhost"
    port = request.url.port
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = lan_ip()
    return f"{request.url.scheme}://{host}{f':{port}' if port else ''}"


def _scenes(pack: dict) -> dict:
    try:
        return json.loads(pack.get("scenes") or "{}")
    except ValueError:
        return {}


def pack_view(pack: dict, request: Request) -> dict:
    manual = S.store.manual(pack["manual_id"]) or {}
    plan = manual.get("plan") or {}
    stats = insights.pack_stats(S.store, pack, plan) if plan else {}
    return {
        "code": pack["code"], "product": pack["product"], "brand": pack["brand"], "kind": pack["kind"],
        "manual_id": pack["manual_id"], "created": pack["created"],
        "url": f"{public_base(request)}/p/{pack['code']}",
        "qr_url": f"/api/packs/{pack['code']}/qr.svg",
        "scenes": _scenes(pack),
        "summary": plan.get("summary", ""),
        "steps": len(plan.get("steps", [])),
        "parts": len(plan.get("parts", [])),
        "tasks": plan.get("tasks", []),
        "safety": plan.get("safety", []),
        "median_minutes": stats.get("median_minutes"),
        "builders": stats.get("sessions", 0),
        "finished": stats.get("finished", 0),
        "tips": sum(1 for r in stats.get("steps", []) if r.get("tip")),
    }


async def prebuild_scenes(code: str, manual_id: str) -> None:
    """Build every step's 3D scene once, so a shopper who scans the box never waits on Muse Wonder."""
    plan = S.store.manual(manual_id)["plan"]
    steps = plan.get("steps", [])
    state = {"done": 0, "total": len(steps), "wonder": 0, "finished": False}
    S.store.set_pack_scenes(code, json.dumps(state))
    sem = asyncio.Semaphore(2)

    async def one(st: dict) -> None:
        async with sem:
            try:
                r = await animation.step_animation(S.store, S.muse, S.settings, manual_id, plan, st["order"],
                                                   figure_image(plan, st["order"]))
                state["wonder"] += r["source"] == "Muse Wonder"
            except Exception:  # noqa: BLE001 - a failed scene falls back to the simple preview at view time
                pass
            state["done"] += 1
            S.store.set_pack_scenes(code, json.dumps(state))

    await asyncio.gather(*(one(st) for st in steps))
    state["finished"] = True
    S.store.set_pack_scenes(code, json.dumps(state))


@app.post("/api/packs")
async def create_pack(body: NewPack, request: Request):
    manual = ready_manual(body.manual_id)
    if manual["use_case"] == "task":
        raise HTTPException(400, "Publish the product's main manual, not a single question.")
    existing = S.store.pack_for_manual(manual["id"])
    if existing:
        return pack_view(existing, request)
    product = manual["plan"].get("product") or manual["title"]
    code = S.store.create_pack(manual["id"], product, manual["use_case"], body.brand.strip()[:80])
    task = asyncio.create_task(prebuild_scenes(code, manual["id"]))
    S.tasks.add(task)
    task.add_done_callback(S.tasks.discard)
    return pack_view(S.store.pack(code), request)


@app.get("/api/packs")
async def list_packs(request: Request):
    return [pack_view(p, request) for p in S.store.packs()]


def pack_or_404(code: str) -> dict:
    pack = S.store.pack(code)
    if not pack:
        raise HTTPException(404, "No guide with that code. Check the code printed under the QR.")
    return pack


@app.get("/api/packs/{code}")
async def get_pack(code: str, request: Request):
    return pack_view(pack_or_404(code), request)


@app.get("/api/packs/{code}/stats")
async def pack_stats(code: str):
    pack = pack_or_404(code)
    plan = ready_manual(pack["manual_id"])["plan"]
    return insights.pack_stats(S.store, pack, plan)


@app.get("/api/packs/{code}/qr.svg")
async def pack_qr(code: str, request: Request):
    import segno  # imported here so the rest of the app runs even before `pip install segno`

    pack = pack_or_404(code)
    qr = segno.make(f"{public_base(request)}/p/{pack['code']}", error="m")
    buf = io.BytesIO()  # a standalone SVG (with its namespace), so browsers show it as an <img>
    qr.save(buf, kind="svg", scale=8, border=2, dark="#1D2422", light="#FFFFFF", xmldecl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "max-age=3600"})


@app.get("/p/{code}")
async def scan(code: str):
    """What the QR code opens: straight into the product's guide."""
    pack_or_404(code)
    return RedirectResponse(f"/#/pack/{code.upper()}", status_code=302)


# ---------------------------------------------------------------- training sessions

class NewSession(BaseModel):
    person: str
    manual_id: str
    mode: str = "cloud"
    pack_code: Optional[str] = None


class ModeChange(BaseModel):
    mode: str


class Override(BaseModel):
    verdict: str


@app.post("/api/sessions")
async def new_session(body: NewSession):
    ready_manual(body.manual_id)
    check_mode(body.mode)
    person = body.person.strip() or "Trainee"
    code = body.pack_code.strip().upper() if body.pack_code else None
    if code and not S.store.pack(code):
        code = None
    sid = S.store.create_session(person, body.manual_id, body.mode, code)
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
                                           ctx={"session_id": sid, "step": order},
                                           common_mistake=insights.cached_tip(S.store, s["manual_id"], order))
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
                                           history=past, ctx={"session_id": sid, "step": order, "frame": frame},
                                           common_mistake=insights.cached_tip(S.store, s["manual_id"], order))
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


@app.post("/api/sessions/{sid}/ledger")
async def session_ledger(sid: str, before_step: int = Form(...), photo: UploadFile = File(...)):
    """Count the leftover hardware and compare it with what should be left before this step."""
    s = session_or_404(sid)
    plan = ready_manual(s["manual_id"])["plan"]
    img, info = await read_photo(photo, folder=("sessions", sid), name=f"ledger_step{before_step:02d}_{new_id('l')}.jpg")
    result = await training.ledger_check(img, plan, before_step, vision=S.vision, settings=S.settings,
                                         private=s["mode"] == "hybrid", reference=reference_image(plan),
                                         ctx={"session_id": sid, "step": before_step})
    result["image"] = info
    return result


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
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="showme-episodes.json"'})


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

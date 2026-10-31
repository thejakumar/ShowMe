"""ShowMe API and web app.

Run on one PC:   python backend/scripts/serve.py      (or run.bat)
Host for many:   see DEPLOY.md (Postgres, several web servers, separate workers)
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import logging
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

from . import animation, car, devices, images as imgs, insights, lessons, links, mock, passport, sample, sample_device, training
from .accounts import (AUTH_COOKIE, DEVICE_COOKIE, WS_COOKIE, Identity, RateLimiter, check_password, hash_password,
                       new_device_id, new_token)
from .config import get_settings
from .jobs import Worker, figure_image as _figure, recover_interrupted
from .muse import MuseClient, MuseError
from .store import Store, new_id, now
from .trace import Tracer
from .vision import PrivacyError, Vision

STATIC = Path(__file__).resolve().parents[1] / "static"
USE_CASES = {"assembly", "car", "training", "device"}
MODES = {"cloud", "hybrid"}
log = logging.getLogger("showme")


class State:
    settings = None
    store: Store
    tracer: Tracer
    muse: MuseClient
    vision: Vision
    worker: Optional[Worker] = None
    limiter: RateLimiter


S = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.settings = get_settings()
    S.store = Store(S.settings.data_dir, S.settings.database_url)
    S.tracer = Tracer(S.settings.trace_dir)
    S.muse = MuseClient(S.settings, S.tracer, mock_responder=mock.respond)
    S.vision = Vision(S.settings, S.muse, S.tracer)
    S.limiter = RateLimiter()
    recover_interrupted(S.store)
    worker_task = None
    if S.settings.run_worker:
        S.worker = Worker(S.store, S.muse, S.settings)
        worker_task = asyncio.create_task(S.worker.run_forever(poll_s=0.5))
    yield
    if S.worker:
        S.worker.stop()
        if worker_task:
            try:
                await asyncio.wait_for(worker_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                worker_task.cancel()
    await S.muse.aclose()
    S.store.close()


app = FastAPI(title="ShowMe", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
app.mount("/files", StaticFiles(directory=str(get_settings().data_dir)), name="files")


# ---------------------------------------------------------------- who is asking

def _secure(request: Request) -> bool:
    return request.url.scheme == "https"


@app.middleware("http")
async def identify(request: Request, call_next):
    """Every API request knows its device, and (if signed in) its person and workspace."""
    if not request.url.path.startswith(("/api/", "/p/")):
        return await call_next(request)
    device = request.cookies.get(DEVICE_COOKIE) or ""
    new_device = not re.fullmatch(r"dev_[\w-]{8,40}", device)
    if new_device:
        device = new_device_id()
    who = Identity(device_id=device, ip=request.client.host if request.client else "")
    token = request.cookies.get(AUTH_COOKIE)
    if token:
        who.user = await asyncio.to_thread(S.store.user_for_token, token)
        if who.user:
            who.workspaces = await asyncio.to_thread(S.store.memberships, who.user["id"])
            wanted = request.cookies.get(WS_COOKIE)
            who.workspace = next((w for w in who.workspaces if w["id"] == wanted), who.workspaces[0] if who.workspaces else None)
    request.state.who = who
    response = await call_next(request)
    if new_device:
        response.set_cookie(DEVICE_COOKIE, device, max_age=3 * 365 * 86400, httponly=True, samesite="lax",
                            secure=_secure(request))
    return response


def who_(request: Request) -> Identity:
    return request.state.who


def multi() -> bool:
    return S.settings.multi_user


def limit(request: Request, bucket: str = "model") -> None:
    """Protects the Meta Model API key from runaway use, per person."""
    s = S.settings
    per = {"model": (s.rate_per_minute, 60), "live": (s.live_rate_per_minute, 60), "upload": (s.uploads_per_hour, 3600)}
    n, window = per[bucket]
    ok, wait = S.limiter.allow(bucket, who_(request).key, n, window)
    if not ok:
        raise HTTPException(429, f"That's a lot of requests in a short time. Try again in {wait} seconds.")


def need_workspace(request: Request) -> Identity:
    who = who_(request)
    if multi() and not who.workspace:
        raise HTTPException(401, "Sign in to your workspace first.")
    return who


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


def can_see_manual(m: dict, who: Identity) -> bool:
    if not multi() or (m.get("visibility") or "public") != "workspace":
        return True
    return (who.member_of(m.get("workspace_id")) or m.get("created_by") in {who.device_id, who.user_id}
            or bool(S.store.pack_for_manual(m["id"])))


def manual_or_404(manual_id: str, request: Request) -> dict:
    m = S.store.manual(manual_id)
    if not m or not can_see_manual(m, who_(request)):
        raise HTTPException(404, "No such manual.")
    return m


def ready_manual(manual_id: str, request: Optional[Request] = None) -> dict:
    m = manual_or_404(manual_id, request) if request is not None else S.store.manual(manual_id)
    if not m:
        raise HTTPException(404, "No such manual.")
    if m["status"] != "ready" or not m["plan"]:
        raise HTTPException(409, f"The manual is not ready yet ({m['progress']}).")
    return m


def can_use_session(s: dict, who: Identity) -> bool:
    if not multi():
        return True
    return (s.get("device_id") == who.device_id or (who.user_id and s.get("user_id") == who.user_id)
            or who.member_of(s.get("workspace_id")))


def session_or_404(sid: str, request: Optional[Request] = None) -> dict:
    s = S.store.session(sid)
    if not s or (request is not None and not can_use_session(s, who_(request))):
        raise HTTPException(404, "No such session.")
    return s


def check_mode(mode: str) -> bool:
    if mode not in MODES:
        raise HTTPException(400, "mode must be cloud or hybrid")
    return mode == "hybrid"


def figure_image(plan: dict, order: int):
    return _figure(S.store, plan, order)


def reference_image(plan: dict):
    url = plan.get("reference_url")
    if not url:
        return None
    path = S.settings.data_dir / url.removeprefix("/files/")
    return imgs.open_path(path) if path.exists() else None


def enqueue(kind: str, payload: dict) -> str:
    jid = S.store.enqueue(kind, payload)
    if S.worker:
        S.worker.wake()
    return jid


def start_ingest(manual_id: str, pdf: Path, title: str, use_case: str, rename: bool = False) -> None:
    enqueue("ingest", {"manual_id": manual_id, "pdf": pdf.relative_to(S.store.dir).as_posix(), "title": title,
                       "use_case": use_case, "rename": rename})


def visible_sessions(request: Request) -> list[dict]:
    """Whose training records this request may see."""
    who = who_(request)
    if not multi():
        return S.store.sessions(workspace_id=who.workspace_id) if who.workspace else S.store.sessions()
    if who.workspace:
        return S.store.sessions(workspace_id=who.workspace_id)
    return S.store.sessions(device_id=who.device_id, user_id=who.user_id)


# ---------------------------------------------------------------- pages, status, health

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
        "multi_user": s.multi_user,
        "allow_signup": s.allow_signup,
    }


@app.get("/healthz")
async def healthz():
    """For load balancers and uptime checks."""
    try:
        ok = await asyncio.to_thread(S.store.ping)
        depth = await asyncio.to_thread(S.store.queue_depth)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=503)
    return {"ok": ok, "database": "postgres" if S.store.postgres else "sqlite", "queue": depth,
            "worker_in_process": S.worker is not None}


# ---------------------------------------------------------------- accounts and workspaces

class Signup(BaseModel):
    name: str
    email: str
    password: str
    workspace: str
    kind: str = "both"  # brand | employer | both


class Login(BaseModel):
    email: str
    password: str


class Join(BaseModel):
    code: str
    name: str = ""


class SwitchWorkspace(BaseModel):
    id: str


def _signed_in(response: Response, request: Request, uid: str, workspace_id: Optional[str]) -> None:
    token = new_token()
    S.store.save_token(token, uid)
    response.set_cookie(AUTH_COOKIE, token, max_age=30 * 86400, httponly=True, samesite="lax", secure=_secure(request))
    if workspace_id:
        response.set_cookie(WS_COOKIE, workspace_id, max_age=365 * 86400, samesite="lax", secure=_secure(request))


def me_view(request: Request, who: Optional[Identity] = None) -> dict:
    who = who or who_(request)
    return {
        "user": who.user,
        "workspace": ({k: who.workspace[k] for k in ("id", "name", "kind", "role")}
                      | ({"join_code": who.workspace["join_code"]} if who.workspace["role"] == "owner" else {}))
        if who.workspace else None,
        "workspaces": [{"id": w["id"], "name": w["name"], "kind": w["kind"], "role": w["role"]} for w in who.workspaces],
        "multi_user": S.settings.multi_user,
        "allow_signup": S.settings.allow_signup,
    }


@app.get("/api/me")
async def me(request: Request):
    return me_view(request)


@app.post("/api/auth/signup")
async def signup(body: Signup, request: Request, response: Response):
    """A brand or employer creates its workspace. Employees then join with its join code."""
    limit(request, "upload")
    if not S.settings.allow_signup:
        raise HTTPException(403, "New workspaces are switched off on this server.")
    email = body.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "That email address doesn't look right.")
    if len(body.password) < 8:
        raise HTTPException(400, "Use a password of at least 8 characters.")
    if not body.name.strip() or not body.workspace.strip():
        raise HTTPException(400, "Add your name and your company's name.")
    if S.store.user_by_email(email):
        raise HTTPException(409, "There's already an account with that email. Sign in instead.")
    uid = S.store.create_user(body.name.strip()[:80], email, hash_password(body.password))
    kind = body.kind if body.kind in {"brand", "employer", "both"} else "both"
    ws = S.store.create_workspace(body.workspace.strip()[:80], kind, uid)
    _signed_in(response, request, uid, ws["id"])
    who = dataclasses.replace(who_(request), user=S.store.user(uid), workspaces=S.store.memberships(uid))
    who.workspace = next(w for w in who.workspaces if w["id"] == ws["id"])
    return me_view(request, who)


@app.post("/api/auth/login")
async def login(body: Login, request: Request, response: Response):
    limit(request, "model")
    u = S.store.user_by_email(body.email.strip())
    if not u or not check_password(body.password, u.get("pw_hash")):
        raise HTTPException(401, "Email or password is wrong.")
    ws = S.store.memberships(u["id"])
    _signed_in(response, request, u["id"], ws[0]["id"] if ws else None)
    who = dataclasses.replace(who_(request), user=S.store.user(u["id"]), workspaces=ws, workspace=ws[0] if ws else None)
    return me_view(request, who)


@app.post("/api/auth/join")
async def join(body: Join, request: Request, response: Response):
    """Employees join a workspace with its code, from their own phone. No password needed."""
    limit(request, "model")
    ws = S.store.workspace_by_code(body.code)
    if not ws:
        raise HTTPException(404, "No workspace with that code. Check it with whoever gave it to you.")
    who = who_(request)
    uid = who.user_id
    if not uid:
        if not body.name.strip():
            raise HTTPException(400, "Add your name, so your training shows up under it.")
        uid = S.store.create_user(body.name.strip()[:80])
    S.store.add_member(ws["id"], uid)
    _signed_in(response, request, uid, ws["id"])
    memberships = S.store.memberships(uid)
    who = dataclasses.replace(who, user=S.store.user(uid), workspaces=memberships,
                              workspace=next(w for w in memberships if w["id"] == ws["id"]))
    return me_view(request, who)


@app.post("/api/auth/workspace")
async def switch_workspace(body: SwitchWorkspace, request: Request, response: Response):
    who = who_(request)
    if not who.member_of(body.id):
        raise HTTPException(404, "You're not in that workspace.")
    response.set_cookie(WS_COOKIE, body.id, max_age=365 * 86400, samesite="lax", secure=_secure(request))
    who = dataclasses.replace(who, workspace=next(w for w in who.workspaces if w["id"] == body.id))
    return me_view(request, who)


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(AUTH_COOKIE)
    if token:
        S.store.drop_token(token)
    response.delete_cookie(AUTH_COOKIE)
    response.delete_cookie(WS_COOKIE)
    return {"ok": True}


# ---------------------------------------------------------------- manuals

@app.get("/api/manuals")
async def list_manuals(request: Request, q: str = ""):
    """On one PC: every manual. Hosted: what you added, your workspace's, the samples, and search results."""
    q = q.strip()[:80]
    if not multi():
        rows = S.store.manuals()
        return [m for m in rows if q.lower() in m["title"].lower()] if q else rows
    who = who_(request)
    owners = [x for x in (who.device_id, who.user_id) if x]
    return S.store.manuals_for(owners=owners, workspace_ids=who.workspace_ids, query=q)


def photos_to_pdf(pages: list[bytes]) -> bytes:
    """Photos of a paper booklet, one per page, as one PDF in the order given."""
    images = []
    for data in pages:
        try:
            images.append(imgs.fit(imgs.load(data), 2000).convert("RGB"))
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(400, "One of the files isn't a photo or a PDF.") from exc
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:], resolution=150)
    return buf.getvalue()


def _ownership(request: Request, use_case: str) -> dict:
    """Machines added inside a workspace stay private to it; everything else is a shared product guide."""
    who = who_(request)
    private = use_case == "training" and who.workspace is not None
    return {"created_by": who.user_id or who.device_id, "workspace_id": who.workspace_id if private else None,
            "visibility": "workspace" if private else "public"}


@app.post("/api/manuals")
async def upload_manual(request: Request, file: list[UploadFile] = File(...), title: str = Form(""),
                        use_case: str = Form("assembly")):
    """A manual as a PDF, or as photos of the paper booklet's pages (in page order)."""
    if use_case not in USE_CASES:
        raise HTTPException(400, "use_case must be assembly, training, device or car")
    limit(request, "upload")
    blobs = [await f.read() for f in file[:60]]
    cap = S.settings.max_upload_mb * 1024 * 1024
    if sum(len(b) for b in blobs) > cap:
        raise HTTPException(413, f"The upload is larger than {S.settings.max_upload_mb} MB.")
    if len(blobs) == 1 and blobs[0].startswith(b"%PDF"):
        data = blobs[0]
    elif blobs and not any(b.startswith(b"%PDF") for b in blobs) and all(
            (f.content_type or "").startswith("image/") or (f.filename or "").lower().endswith((".jpg", ".jpeg", ".png", ".heic", ".webp"))
            for f in file[:60]):
        data = await asyncio.to_thread(photos_to_pdf, blobs)
    else:
        raise HTTPException(400, "Upload the manual as one PDF, or as photos of its pages.")
    first = file[0]
    typed = title.strip()
    title = typed or (re.sub(r"[_-]+", " ", Path(first.filename or "Manual").stem).strip() if len(file) == 1 else "") or "Manual"
    mid = S.store.create_manual(title[:120], use_case, **_ownership(request, use_case))
    pdf = S.store.path("manuals", mid, "source.pdf")
    pdf.write_bytes(data)
    start_ingest(mid, pdf, title, use_case, rename=not typed)  # no typed name: use the product name the model reads
    return {"id": mid, "title": title, "status": "processing"}


def shared_manual(key: str, url: str, retailer: str = "") -> Optional[dict]:
    """The manual already claimed for this key (a sample, or a product link), unless it failed."""
    known = S.store.link(key)
    if not known:
        return None
    m = S.store.manual(known["manual_id"])
    return m if m and m["status"] in {"ready", "processing"} else None


def claim_shared(key: str, manual_id: str, url: str, retailer: str = "") -> bool:
    """Claim the key for this new manual. Returns False if someone else claimed it a moment earlier."""
    if S.store.claim_link(key, manual_id, url, retailer):
        return True
    if shared_manual(key, url, retailer) is None:  # the earlier one failed: this one takes over
        S.store.save_link(key, manual_id, url, retailer)
        return True
    return False


@app.post("/api/manuals/sample")
async def sample_manual(kind: str = "assembly"):
    """The fictional sample manuals: a step stool (assembly) or a coffee maker (device). One copy, shared."""
    mod, use, name = ((sample_device, "device", "brewline_one.pdf") if kind == "device"
                      else (sample, "assembly", "birch_step_stool.pdf"))
    key = f"sample:{use}"
    existing = shared_manual(key, "") or S.store._one(
        "SELECT * FROM manuals WHERE title = ? AND use_case = ? AND status = 'ready' ORDER BY created LIMIT 1",
        (mod.PRODUCT, use))
    if existing:  # ready, or already being read for someone else: share it
        return {"id": existing["id"], "title": existing["title"], "status": existing["status"]}
    mid = S.store.create_manual(mod.PRODUCT, use)  # the row exists before the claim, so others always find it
    if not claim_shared(key, mid, ""):
        S.store.discard_manual(mid)
        m = shared_manual(key, "")
        return {"id": m["id"], "title": m["title"], "status": m["status"]}
    pdf = S.settings.data_dir / "sample" / name
    if not pdf.exists():
        tmp = pdf.with_suffix(f".{mid}.tmp")
        await asyncio.to_thread(mod.build_pdf, tmp)
        tmp.replace(pdf)  # atomic, so a half-written sample is never read
    copy = S.store.path("manuals", mid, "source.pdf")
    copy.write_bytes(pdf.read_bytes())
    start_ingest(mid, copy, mod.PRODUCT, use)
    return {"id": mid, "title": mod.PRODUCT, "status": "processing"}


@app.get("/api/manuals/{manual_id}")
async def get_manual(manual_id: str, request: Request):
    return manual_or_404(manual_id, request)


@app.get("/api/manuals/{manual_id}/steps/{order}/animation")
async def step_animation(manual_id: str, order: int, request: Request, refresh: bool = False):
    """3D animation of one step (generated once by Muse Wonder from the manual's diagram, then cached)."""
    plan = ready_manual(manual_id, request)["plan"]
    if refresh:
        limit(request)
    try:
        return await animation.step_animation(S.store, S.muse, S.settings, manual_id, plan, order,
                                              figure_image(plan, order), refresh=refresh)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/manuals/{manual_id}/ledger")
async def manual_ledger(manual_id: str, request: Request):
    """How much loose hardware should be left after each step."""
    return training.ledger_plan(ready_manual(manual_id, request)["plan"])


@app.get("/api/manuals/{manual_id}/steps/{order}/heads-up")
async def heads_up(manual_id: str, order: int, request: Request):
    """The mistake earlier builders most often made at this step, said before the next person starts it."""
    plan = ready_manual(manual_id, request)["plan"]
    try:
        return await insights.heads_up(S.store, S.muse, S.settings, manual_id, plan, order)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------- store links: Amazon, IKEA, any shop

class NewLink(BaseModel):
    url: str
    name: str = ""


@app.post("/api/links")
async def from_store_link(body: NewLink, request: Request):
    """A product link -> its guide. Each product is read once and shared with everyone who pastes it."""
    try:
        link = links.parse_link(body.url)
        await links._check_public(link.url)
    except links.LinkError as exc:
        raise HTTPException(400, str(exc)) from exc
    name = body.name.strip()[:160]

    def cached(m: dict) -> dict:
        return {"manual_id": m["id"], "title": m["title"], "status": m["status"], "retailer": link.retailer,
                "product_id": link.product_id, "cached": True}

    known = shared_manual(link.key, link.url)
    if known and not name:
        return cached(known)
    limit(request, "upload")
    title = name or link.name_hint.title() or (f"{link.retailer} product {link.product_id}".strip() if link.product_id else "Product from a link")
    who = who_(request)
    mid = S.store.create_manual(title[:120], "device", created_by=who.user_id or who.device_id)
    if name:  # a typed name is a fresh attempt for this product: it replaces whatever was there
        S.store.save_link(link.key, mid, link.url, link.retailer)
    elif not claim_shared(link.key, mid, link.url, link.retailer):  # someone pasted it a moment earlier
        S.store.discard_manual(mid)
        return cached(shared_manual(link.key, link.url))
    enqueue("link", {"manual_id": mid, "link": dataclasses.asdict(link), "name": name})
    return {"manual_id": mid, "title": title, "status": "processing", "retailer": link.retailer,
            "product_id": link.product_id, "cached": False}


# ---------------------------------------------------------------- machines and electronic devices

class Question(BaseModel):
    question: str


@app.post("/api/manuals/{manual_id}/tour")
async def device_tour(manual_id: str, request: Request, photo: UploadFile = File(...), mode: str = Form("cloud")):
    """One photo of the machine or device: every control the manual names, labeled with what it does."""
    private = check_mode(mode)
    plan = ready_manual(manual_id, request)["plan"]
    limit(request)
    img, info = await read_photo(photo, folder=("manuals", manual_id, "tours"), name=f"{new_id('t')}.jpg")
    result = await devices.tour(img, plan, vision=S.vision, private=private, ctx={"manual_id": manual_id})
    result["image"] = info
    return result


@app.post("/api/manuals/{manual_id}/ask")
async def device_ask(manual_id: str, body: Question, request: Request):
    """'How do I ...?' answered from the manual, as a guided task when there is something to do."""
    question = body.question.strip()[:300]
    if len(question) < 3:
        raise HTTPException(400, "Type or say a question first.")
    parent = ready_manual(manual_id, request)
    if parent["use_case"] == "task":
        parent = ready_manual(parent["plan"]["parent_manual_id"])
    cached = devices.find_task(S.store, parent["id"], question.rstrip("?") + "?")
    if not cached:
        limit(request)
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


@app.post("/api/packs")
async def create_pack(body: NewPack, request: Request):
    who = need_workspace(request)
    manual = ready_manual(body.manual_id, request)
    if manual["use_case"] == "task":
        raise HTTPException(400, "Publish the product's main manual, not a single question.")
    existing = S.store.pack_for_manual(manual["id"])
    if existing and (not multi() or existing.get("workspace_id") == who.workspace_id):
        return pack_view(existing, request)
    product = manual["plan"].get("product") or manual["title"]
    code = S.store.create_pack(manual["id"], product, manual["use_case"], body.brand.strip()[:80],
                               workspace_id=who.workspace_id)
    enqueue("prebuild", {"code": code, "manual_id": manual["id"]})
    return pack_view(S.store.pack(code), request)


@app.get("/api/packs")
async def list_packs(request: Request):
    who = need_workspace(request)
    rows = S.store.packs(who.workspace_id) if who.workspace else S.store.packs(all_workspaces=True)
    return [pack_view(p, request) for p in rows]


def pack_or_404(code: str) -> dict:
    pack = S.store.pack(code)
    if not pack:
        raise HTTPException(404, "No guide with that code. Check the code printed under the QR.")
    return pack


@app.get("/api/packs/{code}")
async def get_pack(code: str, request: Request):
    """Public: this is what anyone who scans the box sees."""
    return pack_view(pack_or_404(code), request)


@app.get("/api/packs/{code}/stats")
async def pack_stats(code: str, request: Request):
    pack = pack_or_404(code)
    who = who_(request)
    if multi() and not who.member_of(pack.get("workspace_id")):
        raise HTTPException(403, "Only the brand that published this guide can see its dashboard.")
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
    person: str = ""
    manual_id: str
    mode: str = "cloud"
    pack_code: Optional[str] = None


class ModeChange(BaseModel):
    mode: str


class Override(BaseModel):
    verdict: str


@app.post("/api/sessions")
async def new_session(body: NewSession, request: Request):
    ready_manual(body.manual_id, request)
    check_mode(body.mode)
    who = who_(request)
    person = body.person.strip()[:80] or (who.user["name"] if who.user else "") or "Guest"
    code = body.pack_code.strip().upper() if body.pack_code else None
    if code and not S.store.pack(code):
        code = None
    sid = S.store.create_session(person, body.manual_id, body.mode, code, device_id=who.device_id,
                                 user_id=who.user_id, workspace_id=who.workspace_id)
    return S.store.session(sid)


@app.get("/api/sessions/{sid}")
async def get_session(sid: str, request: Request):
    s = session_or_404(sid, request)
    return {**s, "results": S.store.results(sid)}


@app.post("/api/sessions/{sid}/mode")
async def set_mode(sid: str, body: ModeChange, request: Request):
    session_or_404(sid, request)
    check_mode(body.mode)
    S.store.set_mode(sid, body.mode)
    return S.store.session(sid)


@app.post("/api/sessions/{sid}/inventory")
async def session_inventory(sid: str, request: Request, photo: UploadFile = File(...)):
    s = session_or_404(sid, request)
    plan = ready_manual(s["manual_id"])["plan"]
    limit(request)
    img, info = await read_photo(photo, folder=("sessions", sid), name="inventory.jpg")
    result = await training.inventory(img, plan, vision=S.vision, settings=S.settings, private=s["mode"] == "hybrid",
                                      reference=reference_image(plan), ctx={"session_id": sid})
    result["image"] = info
    S.store.set_inventory(sid, {k: result[k] for k in ("rows", "complete", "summary", "missing")} | {"photo": info["url"]})
    return result


@app.post("/api/sessions/{sid}/steps/{order}/parts")
async def step_parts(sid: str, order: int, request: Request, photo: UploadFile = File(...)):
    s = session_or_404(sid, request)
    plan = ready_manual(s["manual_id"])["plan"]
    limit(request)
    img, info = await read_photo(photo, folder=("sessions", sid), name=f"step{order:02d}_parts_{new_id('p')}.jpg")
    try:
        result = await training.show_step_parts(img, plan, order, vision=S.vision, private=s["mode"] == "hybrid",
                                                ctx={"session_id": sid, "step": order})
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    result["image"] = info
    return result


@app.post("/api/sessions/{sid}/steps/{order}/check")
async def step_check(sid: str, order: int, request: Request, photo: UploadFile = File(...)):
    s = session_or_404(sid, request)
    plan = ready_manual(s["manual_id"])["plan"]
    limit(request)
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
async def step_live(sid: str, order: int, request: Request, photo: UploadFile = File(...), frame: int = Form(1),
                    live_id: str = Form(""), history: str = Form("[]")):
    """One frame of a live camera check. Returns a verdict and one instruction to speak right now.

    Not every frame is stored: the first mistake seen in a live run and the final pass are recorded
    as step results (with their frame), so the training records count them and the export has the photos.
    """
    s = session_or_404(sid, request)
    plan = ready_manual(s["manual_id"])["plan"]
    limit(request, "live")
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
    run = (live_id or "default")[:40]
    if result["verdict"] == "pass" or (result["verdict"] == "fail" and not S.store.live_fail_logged(sid, order, run)):
        kept = imgs.save(img, S.store.path("sessions", sid, f"step{order:02d}_live_{result['verdict']}_{new_id('f')}.jpg"))
        saved = S.store.add_result(sid, order, result["verdict"], confidence=result["confidence"], reason=result["reason"],
                                   fix=result["fix"], observed=result["observed"], photo=S.store.url(kept),
                                   detections=result.get("detections"), live_id=run)
        recorded, result_id = result["verdict"], saved["id"]
    return {**result, "frame": frame, "recorded": recorded, "result_id": result_id, "image": info}


@app.post("/api/sessions/{sid}/where")
async def where_am_i(sid: str, request: Request, photo: UploadFile = File(...)):
    """One photo of work in progress: steps already done are checked off, earlier mistakes are flagged,
    and the step to do now comes back with the next actions, so someone midway can carry on."""
    s = session_or_404(sid, request)
    plan = ready_manual(s["manual_id"])["plan"]
    if s["mode"] == "hybrid":
        raise HTTPException(409, "Finding your place needs Muse Spark to see the photo. Switch to Cloud for this.")
    limit(request)
    img, info = await read_photo(photo, folder=("sessions", sid), name=f"where_{new_id('w')}.jpg")
    result = await training.resume(img, plan, reference_image(plan), muse=S.muse, settings=S.settings,
                                   ctx={"session_id": sid})
    passed = {r["step_order"] for r in S.store.results(sid) if r["final"] == "pass"}
    recorded = []
    for order in result["done_steps"]:
        if order not in passed:
            S.store.add_result(sid, order, "pass", confidence=result["confidence"], reason="Checked from a progress photo",
                               fix="", observed=result["observed"], photo=info["url"])
            recorded.append(order)
    for w in result["wrong"]:
        S.store.add_result(sid, w["step"], "fail", confidence=result["confidence"], reason="Found in a progress photo",
                           fix=w["fix"], observed=w["observed"], photo=info["url"])
    return {**result, "recorded_passes": recorded, "image": info}


@app.post("/api/sessions/{sid}/ledger")
async def session_ledger(sid: str, request: Request, before_step: int = Form(...), photo: UploadFile = File(...)):
    """Count the leftover hardware and compare it with what should be left before this step."""
    s = session_or_404(sid, request)
    plan = ready_manual(s["manual_id"])["plan"]
    limit(request)
    img, info = await read_photo(photo, folder=("sessions", sid), name=f"ledger_step{before_step:02d}_{new_id('l')}.jpg")
    result = await training.ledger_check(img, plan, before_step, vision=S.vision, settings=S.settings,
                                         private=s["mode"] == "hybrid", reference=reference_image(plan),
                                         ctx={"session_id": sid, "step": before_step})
    result["image"] = info
    return result


@app.post("/api/results/{rid}/override")
async def override(rid: str, body: Override, request: Request):
    if body.verdict not in {"pass", "fail"}:
        raise HTTPException(400, "verdict must be pass or fail")
    r = S.store.result(rid)
    if not r:
        raise HTTPException(404, "No such result.")
    session_or_404(r["session_id"], request)
    S.store.override_result(rid, body.verdict)
    return {"ok": True}


@app.post("/api/sessions/{sid}/finish")
async def finish(sid: str, request: Request):
    s = session_or_404(sid, request)
    S.store.finish_session(sid)
    rows = passport.passport(S.store, [S.store.session(sid)])
    return rows[0] if rows else {"session_id": s["id"], "person": s["person"], "verified": False, "steps_passed": 0,
                                 "steps_total": 0, "mistakes_caught": 0, "attempts": 0, "overrides": 0,
                                 "mode": s["mode"], "inventory_complete": None}


# ---------------------------------------------------------------- learn any concept (off the main path)

@app.post("/api/lessons")
async def lesson(request: Request, concept: str = Form(...), mode: str = Form("cloud"), photo: UploadFile = File(...)):
    private = check_mode(mode)
    concept = concept.strip()[:200]
    if not concept:
        raise HTTPException(400, "Say or type what you want to learn.")
    limit(request)
    lid = new_id("room")
    img, info = await read_photo(photo, folder=("lessons",), name=f"{lid}.jpg")
    result = await lessons.build_lesson(concept, img, muse=S.muse, vision=S.vision, settings=S.settings,
                                        private=private, ctx={"lesson": lid})
    result["image"] = info
    if "lesson" in result:
        result["id"] = S.store.save_lesson(concept, info["url"], result)
    return result


# ---------------------------------------------------------------- car checks (off the main path)

def optional_plan(manual_id: Optional[str], request: Request) -> Optional[dict]:
    if not manual_id:
        return None
    return ready_manual(manual_id, request)["plan"]


@app.post("/api/car/engine-bay")
async def engine_bay(request: Request, photo: UploadFile = File(...), manual_id: str = Form(""), mode: str = Form("cloud")):
    private = check_mode(mode)
    plan = optional_plan(manual_id, request)
    limit(request)
    img, info = await read_photo(photo, folder=("car",), name=f"{new_id('bay')}.jpg")
    result = await car.engine_bay(img, plan, vision=S.vision, private=private, ctx={"car": manual_id or "default"})
    result["image"] = info
    return result


@app.post("/api/car/warning-lights")
async def warning_lights(request: Request, photo: UploadFile = File(...), manual_id: str = Form(""),
                         mode: str = Form("cloud")):
    private = check_mode(mode)
    plan = optional_plan(manual_id, request)
    limit(request)
    img, info = await read_photo(photo, folder=("car",), name=f"{new_id('dash')}.jpg")
    result = await car.warning_lights(img, plan, muse=S.muse, settings=S.settings, private=private,
                                      ctx={"car": manual_id or "default"})
    result["image"] = info
    return result


# ---------------------------------------------------------------- training records, export, traces

@app.get("/api/passport")
async def get_passport(request: Request):
    return passport.passport(S.store, visible_sessions(request))


@app.get("/api/passport.csv")
async def passport_csv(request: Request):
    """Training records as a spreadsheet: one row per person per machine, product or task."""
    import csv

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Person", "Machine, product or task", "Kind", "Status", "Steps checked", "Steps total",
                "Mistakes caught", "Confirmed by the person", "Started", "Finished", "Mode"])
    for r in passport.passport(S.store, visible_sessions(request)):
        w.writerow([r["person"], r["product"], r["use_case"], "Signed off" if r["verified"] else "In progress",
                    r["steps_passed"], r["steps_total"], r["mistakes_caught"], r["overrides"], r["started"],
                    r["finished"] or "", r["mode"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="showme-training-records.csv"'})


@app.get("/api/export/robot-data")
async def robot_data(request: Request):
    data = passport.robot_export(S.store, visible_sessions(request))
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="showme-episodes.json"'})


TRACE_PUBLIC = ("time", "model", "purpose", "tag", "ok", "status", "latency_ms", "images", "usage", "request_id", "error")


def _trace_view(entry: dict) -> dict:
    """Hosted: model calls are shown as proof of Muse, without session, manual or person ids."""
    return {k: entry.get(k) for k in TRACE_PUBLIC} if multi() else entry


@app.get("/api/traces")
async def traces(limit: int = 100):
    return [_trace_view(e) for e in S.tracer.latest(min(max(limit, 1), 500))]


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
                yield f"data: {json.dumps(_trace_view(entry), default=str)}\n\n"
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

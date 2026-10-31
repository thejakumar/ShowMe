"""Background work, safe for many servers: reading a manual, turning a store link into a guide, and
pre-building a QR guide's 3D scenes.

Web servers only enqueue (a row in the jobs table). Workers claim jobs one at a time; with Postgres each job
goes to exactly one worker (FOR UPDATE SKIP LOCKED), so you can run as many as you need:

    python -m apprentice.worker          # a separate worker process (hosting)
    RUN_WORKER=1 (default)               # or a worker inside the web process (one PC)

A job whose worker stopped (restart, crash) goes back in the queue after 15 minutes; a job that fails
unexpectedly is retried up to three times with a growing delay.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from typing import Awaitable, Callable, Optional

from . import animation, images as imgs, training
from .config import Settings
from .from_link import build_from_link
from .ingest import ingest_manual
from .links import Link
from .muse import MuseClient
from .store import Store

log = logging.getLogger("showme.jobs")
MAX_ATTEMPTS = 3


def figure_image(store: Store, plan: dict, order: int):
    step = training.step_by_order(plan, order)
    url = (step or {}).get("figure_url")
    if not url:
        return None
    path = store.dir / url.removeprefix("/files/")
    return imgs.open_path(path) if path.exists() else None


async def prebuild_scenes(store: Store, muse: MuseClient, settings: Settings, code: str, manual_id: str) -> None:
    """Build every step's 3D scene once, so whoever scans the QR code never waits on Muse Wonder."""
    plan = store.manual(manual_id)["plan"]
    steps = plan.get("steps", [])
    state = {"done": 0, "total": len(steps), "wonder": 0, "finished": False}
    store.set_pack_scenes(code, json.dumps(state))
    sem = asyncio.Semaphore(2)

    async def one(st: dict) -> None:
        async with sem:
            try:
                r = await animation.step_animation(store, muse, settings, manual_id, plan, st["order"],
                                                   figure_image(store, plan, st["order"]))
                state["wonder"] += r["source"] == "Muse Wonder"
            except Exception:  # noqa: BLE001 - a failed scene falls back to the simple preview at view time
                pass
            state["done"] += 1
            store.set_pack_scenes(code, json.dumps(state))

    await asyncio.gather(*(one(st) for st in steps))
    state["finished"] = True
    store.set_pack_scenes(code, json.dumps(state))


Handler = Callable[[dict, Store, MuseClient, Settings], Awaitable[None]]


async def _ingest(p: dict, store: Store, muse: MuseClient, settings: Settings) -> None:
    pdf = store.dir / p["pdf"]
    await ingest_manual(p["manual_id"], pdf, p["title"], p["use_case"], store=store, muse=muse, settings=settings,
                        rename_to_product=p.get("rename", False))


async def _link(p: dict, store: Store, muse: MuseClient, settings: Settings) -> None:
    await build_from_link(p["manual_id"], Link(**p["link"]), p.get("name", ""), store=store, muse=muse,
                          settings=settings)


async def _prebuild(p: dict, store: Store, muse: MuseClient, settings: Settings) -> None:
    await prebuild_scenes(store, muse, settings, p["code"], p["manual_id"])


async def _noop(p: dict, store: Store, muse: MuseClient, settings: Settings) -> None:
    """For health checks and tests of the queue itself."""


HANDLERS: dict[str, Handler] = {"ingest": _ingest, "link": _link, "prebuild": _prebuild, "noop": _noop}


class Worker:
    def __init__(self, store: Store, muse: MuseClient, settings: Settings, name: Optional[str] = None) -> None:
        self.store, self.muse, self.settings = store, muse, settings
        self.name = name or f"{socket.gethostname()}:{os.getpid()}"
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._running: set[asyncio.Task] = set()

    def wake(self) -> None:
        """A job was just enqueued in this process: look now instead of at the next poll."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def run_one(self, job: dict) -> None:
        handler = HANDLERS.get(job["kind"])
        try:
            if handler is None:
                raise ValueError(f"unknown job kind {job['kind']}")
            await handler(job["payload"], self.store, self.muse, self.settings)
            self.store.finish_job(job["id"])
        except Exception as exc:  # noqa: BLE001 - recorded on the job; retried a few times
            log.exception("job %s failed", job["id"])
            if job["attempts"] < MAX_ATTEMPTS:
                self.store.finish_job(job["id"], f"{type(exc).__name__}: {exc}", retry_in=15 * job["attempts"])
            else:
                self.store.finish_job(job["id"], f"{type(exc).__name__}: {exc}")
                mid = job["payload"].get("manual_id")
                if mid and job["kind"] in {"ingest", "link"}:
                    self.store.update_manual(mid, status="error", error=str(exc)[:500], progress="Failed")

    async def run_forever(self, poll_s: float = 1.0) -> None:
        self.store.requeue_stale_jobs()
        slots = asyncio.Semaphore(max(1, self.settings.worker_concurrency))
        ticks = 0
        while not self._stop.is_set():
            ticks += 1
            if ticks % 300 == 0:
                self.store.requeue_stale_jobs()
            await slots.acquire()
            job = await asyncio.to_thread(self.store.claim_job, self.name)
            if job is None:
                slots.release()
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=poll_s)
                except asyncio.TimeoutError:
                    pass
                continue

            async def go(j=job):
                try:
                    await self.run_one(j)
                finally:
                    slots.release()

            task = asyncio.create_task(go())
            self._running.add(task)
            task.add_done_callback(self._running.discard)
        for t in list(self._running):
            t.cancel()


def recover_interrupted(store: Store) -> int:
    """Manuals left 'processing' by an older version (no job behind them) are marked as interrupted."""
    open_jobs = store._all("SELECT payload_json FROM jobs WHERE status IN ('queued', 'running')")
    pending = {json.loads(j["payload_json"]).get("manual_id") for j in open_jobs}
    n = 0
    for m in store.stuck_manuals():
        if m["id"] not in pending:
            store.update_manual(m["id"], status="error", progress="Failed",
                                error="Reading was interrupted when ShowMe restarted. Upload or paste it again.")
            n += 1
    return n

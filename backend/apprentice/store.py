"""SQLite store for manuals, plans, sessions and step results. Files live beside it in data/."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .plan_tools import normalize_plan

SCHEMA = """
CREATE TABLE IF NOT EXISTS manuals (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    use_case TEXT NOT NULL,
    status TEXT NOT NULL,
    progress TEXT DEFAULT '',
    error TEXT DEFAULT '',
    page_count INTEGER DEFAULT 0,
    plan_json TEXT,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    person TEXT NOT NULL,
    manual_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    started TEXT NOT NULL,
    finished TEXT,
    inventory_json TEXT
);
CREATE TABLE IF NOT EXISTS step_results (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    step_order INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    confidence REAL,
    reason TEXT,
    fix TEXT,
    observed TEXT,
    override TEXT,
    photo TEXT,
    detections_json TEXT,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    concept TEXT NOT NULL,
    photo TEXT,
    lesson_json TEXT,
    created TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.dir / "apprentice.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    # ---------- helpers

    def _exec(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self.db.execute(sql, args)
            self.db.commit()

    def _one(self, sql: str, args: tuple = ()) -> Optional[dict]:
        with self._lock:
            row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self.db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def path(self, *parts: str) -> Path:
        p = self.dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def url(self, path: Path) -> str:
        return "/files/" + path.resolve().relative_to(self.dir.resolve()).as_posix()

    # ---------- manuals

    def create_manual(self, title: str, use_case: str) -> str:
        mid = new_id("man")
        self._exec(
            "INSERT INTO manuals (id, title, use_case, status, progress, created) VALUES (?,?,?,?,?,?)",
            (mid, title, use_case, "processing", "Queued", now()),
        )
        return mid

    def update_manual(self, mid: str, **fields: Any) -> None:
        if "plan" in fields:
            fields["plan_json"] = json.dumps(fields.pop("plan"))
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE manuals SET {cols} WHERE id = ?", (*fields.values(), mid))

    def manual(self, mid: str) -> Optional[dict]:
        row = self._one("SELECT * FROM manuals WHERE id = ?", (mid,))
        if row:
            row["plan"] = normalize_plan(json.loads(row.pop("plan_json"))) if row.get("plan_json") else None
        return row

    def manuals(self) -> list[dict]:
        rows = self._all("SELECT id, title, use_case, status, progress, error, page_count, created FROM manuals ORDER BY created DESC")
        return rows

    # ---------- sessions

    def create_session(self, person: str, manual_id: str, mode: str) -> str:
        sid = new_id("ses")
        self._exec(
            "INSERT INTO sessions (id, person, manual_id, mode, started) VALUES (?,?,?,?,?)",
            (sid, person, manual_id, mode, now()),
        )
        return sid

    def session(self, sid: str) -> Optional[dict]:
        row = self._one("SELECT * FROM sessions WHERE id = ?", (sid,))
        if row:
            row["inventory"] = json.loads(row.pop("inventory_json")) if row.get("inventory_json") else None
        return row

    def sessions(self) -> list[dict]:
        rows = self._all("SELECT * FROM sessions ORDER BY started DESC")
        for r in rows:
            r["inventory"] = json.loads(r.pop("inventory_json")) if r.get("inventory_json") else None
        return rows

    def set_inventory(self, sid: str, inventory: dict) -> None:
        self._exec("UPDATE sessions SET inventory_json = ? WHERE id = ?", (json.dumps(inventory), sid))

    def set_mode(self, sid: str, mode: str) -> None:
        self._exec("UPDATE sessions SET mode = ? WHERE id = ?", (mode, sid))

    def finish_session(self, sid: str) -> None:
        self._exec("UPDATE sessions SET finished = ? WHERE id = ?", (now(), sid))

    # ---------- step results

    def attempts(self, sid: str, order: int) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM step_results WHERE session_id = ? AND step_order = ?", (sid, order))
        return int(row["n"]) if row else 0

    def add_result(self, sid: str, order: int, verdict: str, *, confidence: float, reason: str, fix: str,
                   observed: str, photo: str, detections: Optional[list] = None) -> dict:
        rid = new_id("res")
        attempt = self.attempts(sid, order) + 1
        self._exec(
            "INSERT INTO step_results (id, session_id, step_order, attempt, verdict, confidence, reason, fix, observed,"
            " photo, detections_json, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, sid, order, attempt, verdict, confidence, reason, fix, observed, photo,
             json.dumps(detections or []), now()),
        )
        return {"id": rid, "attempt": attempt}

    def override_result(self, rid: str, verdict: str) -> None:
        self._exec("UPDATE step_results SET override = ? WHERE id = ?", (verdict, rid))

    def results(self, sid: str) -> list[dict]:
        rows = self._all("SELECT * FROM step_results WHERE session_id = ? ORDER BY step_order, attempt", (sid,))
        for r in rows:
            r["detections"] = json.loads(r.pop("detections_json") or "[]")
            r["final"] = r["override"] or r["verdict"]
        return rows

    # ---------- lessons

    def save_lesson(self, concept: str, photo: str, lesson: dict) -> str:
        lid = new_id("les")
        self._exec("INSERT INTO lessons (id, concept, photo, lesson_json, created) VALUES (?,?,?,?,?)",
                   (lid, concept, photo, json.dumps(lesson), now()))
        return lid

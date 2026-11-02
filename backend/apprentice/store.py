"""Storage for manuals, sessions, results, QR guides, accounts and jobs. Files live beside it in data/.

Two databases, one schema:
- SQLite (default): a single file, for running on one PC.
- Postgres (set DATABASE_URL=postgresql://...): for hosting many users across several web and worker
  processes. Connections come from a pool; jobs are claimed with SELECT ... FOR UPDATE SKIP LOCKED so
  any number of workers can share the queue.

Older databases are upgraded in place on start: missing tables and columns are added, no row is changed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .plan_tools import normalize_plan

TABLES = [
    """CREATE TABLE IF NOT EXISTS manuals (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, use_case TEXT NOT NULL, status TEXT NOT NULL,
        progress TEXT DEFAULT '', error TEXT DEFAULT '', page_count INTEGER DEFAULT 0, plan_json TEXT,
        created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, person TEXT NOT NULL, manual_id TEXT NOT NULL, mode TEXT NOT NULL,
        started TEXT NOT NULL, finished TEXT, inventory_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS step_results (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, step_order INTEGER NOT NULL, attempt INTEGER NOT NULL,
        verdict TEXT NOT NULL, confidence REAL, reason TEXT, fix TEXT, observed TEXT, override TEXT, photo TEXT,
        detections_json TEXT, created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS packs (
        code TEXT PRIMARY KEY, manual_id TEXT NOT NULL, brand TEXT DEFAULT '', product TEXT NOT NULL,
        kind TEXT NOT NULL, scenes TEXT DEFAULT '', created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS links (
        key TEXT PRIMARY KEY, manual_id TEXT NOT NULL, url TEXT NOT NULL, retailer TEXT DEFAULT '',
        created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS lessons (
        id TEXT PRIMARY KEY, concept TEXT NOT NULL, photo TEXT, lesson_json TEXT, created TEXT NOT NULL)""",
    # accounts: people who own or belong to a workspace (a brand or an employer)
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, email TEXT UNIQUE, name TEXT NOT NULL, pw_hash TEXT, created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS workspaces (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'both', join_code TEXT UNIQUE NOT NULL,
        created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS members (
        workspace_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member', created TEXT NOT NULL,
        PRIMARY KEY (workspace_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS auth_tokens (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created TEXT NOT NULL, expires TEXT NOT NULL)""",
    # background work: reading manuals, store links, 3D pre-builds
    """CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, run_after TEXT NOT NULL, locked_by TEXT, locked_at TEXT,
        error TEXT DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL)""",
]

# columns added after the first release: (table, column, type). Added in place when missing.
COLUMNS = [
    ("sessions", "pack_code", "TEXT"),
    ("sessions", "device_id", "TEXT"),
    ("sessions", "user_id", "TEXT"),
    ("sessions", "workspace_id", "TEXT"),
    ("manuals", "workspace_id", "TEXT"),
    ("manuals", "created_by", "TEXT"),
    ("manuals", "visibility", "TEXT DEFAULT 'public'"),
    ("packs", "workspace_id", "TEXT"),
    ("step_results", "live_id", "TEXT"),
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_sessions_manual ON sessions (manual_id)",
    "CREATE INDEX IF NOT EXISTS ix_sessions_device ON sessions (device_id)",
    "CREATE INDEX IF NOT EXISTS ix_sessions_ws ON sessions (workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_results_session ON step_results (session_id, step_order)",
    "CREATE INDEX IF NOT EXISTS ix_manuals_ws ON manuals (workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_manuals_creator ON manuals (created_by)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_queue ON jobs (status, run_after)",
    "CREATE INDEX IF NOT EXISTS ix_members_user ON members (user_id)",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _pooled(url: str) -> bool:
    """A Neon pooled endpoint (PgBouncer): the host carries the -pooler suffix."""
    from urllib.parse import urlparse

    return "-pooler" in (urlparse(url).hostname or "")


class Store:
    def __init__(self, data_dir: Path, database_url: str = "", migrate_url: str = "") -> None:
        """database_url: what the app queries through (on Neon, the pooled -pooler URL).
        migrate_url: optional direct (unpooled) URL for schema changes, e.g. Neon's DATABASE_URL_UNPOOLED."""
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.postgres = database_url.startswith(("postgres://", "postgresql://"))
        self._migrate_url = migrate_url if migrate_url.startswith(("postgres://", "postgresql://")) else ""
        self._lock = threading.Lock()
        if self.postgres:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            kwargs: dict = {"row_factory": dict_row}
            if _pooled(database_url):
                # PgBouncer in transaction mode (Neon's -pooler endpoint): no server-side prepared statements,
                # which psycopg would otherwise create after a query runs a few times.
                kwargs["prepare_threshold"] = None
            # check: a connection is tested before reuse, so connections closed while an idle Neon compute
            # was suspended (scale to zero) are replaced instead of failing the next request.
            self.pool = ConnectionPool(database_url, min_size=1, max_size=10, kwargs=kwargs,
                                       check=ConnectionPool.check_connection, open=True)
        else:
            self.db = sqlite3.connect(self.dir / "apprentice.db", check_same_thread=False, timeout=30)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    # ---------- plumbing

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.postgres else sql

    def _exec(self, sql: str, args: tuple = ()) -> int:
        if self.postgres:
            with self.pool.connection() as conn:
                return conn.execute(self._sql(sql), args).rowcount
        with self._lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.rowcount

    def _one(self, sql: str, args: tuple = ()) -> Optional[dict]:
        if self.postgres:
            with self.pool.connection() as conn:
                row = conn.execute(self._sql(sql), args).fetchone()
            return dict(row) if row else None
        with self._lock:
            row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        if self.postgres:
            with self.pool.connection() as conn:
                rows = conn.execute(self._sql(sql), args).fetchall()
            return [dict(r) for r in rows]
        with self._lock:
            rows = self.db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def _columns(self, table: str) -> set[str]:
        if self.postgres:
            rows = self._all("SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,))
            return {r["column_name"] for r in rows}
        return {r["name"] for r in self._all(f"PRAGMA table_info({table})")}

    def _migrate(self) -> None:
        if self.postgres and self._migrate_url:
            return self._migrate_direct()
        self._apply_schema()

    def _migrate_direct(self) -> None:
        """Schema changes over the direct connection, one server at a time (a session advisory lock, which a
        transaction-mode pooler can't hold)."""
        import psycopg

        with psycopg.connect(self._migrate_url, autocommit=True) as conn:
            conn.execute("SELECT pg_advisory_lock(7310)")
            try:
                for sql in TABLES:
                    conn.execute(sql)
                for table, col, kind in COLUMNS:
                    have = {r[0] for r in conn.execute(
                        "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))}
                    if col not in have:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
                for sql in INDEXES:
                    conn.execute(sql)
            finally:
                conn.execute("SELECT pg_advisory_unlock(7310)")

    def _apply_schema(self) -> None:
        for sql in TABLES:
            self._exec(sql)
        for table, col, kind in COLUMNS:  # databases from earlier versions: add the column, keep every row
            if col not in self._columns(table):
                self._exec(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
        for sql in INDEXES:
            self._exec(sql)

    def close(self) -> None:
        if self.postgres:
            self.pool.close()
        else:
            self.db.close()

    def path(self, *parts: str) -> Path:
        p = self.dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def url(self, path: Path) -> str:
        return "/files/" + path.resolve().relative_to(self.dir.resolve()).as_posix()

    def ping(self) -> bool:
        return bool(self._one("SELECT 1 AS ok"))

    # ---------- manuals

    def create_manual(self, title: str, use_case: str, *, created_by: Optional[str] = None,
                      workspace_id: Optional[str] = None, visibility: str = "public") -> str:
        mid = new_id("man")
        self._exec(
            "INSERT INTO manuals (id, title, use_case, status, progress, created, created_by, workspace_id, visibility)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, title, use_case, "processing", "Queued", now(), created_by, workspace_id, visibility),
        )
        return mid

    def update_manual(self, mid: str, **fields: Any) -> None:
        if "plan" in fields:
            fields["plan_json"] = json.dumps(fields.pop("plan"))
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE manuals SET {cols} WHERE id = ?", (*fields.values(), mid))

    def discard_manual(self, mid: str) -> None:
        """Remove a manual row that lost a race to an identical one before anything used it."""
        self._exec("DELETE FROM manuals WHERE id = ? AND status = 'processing'", (mid,))

    def manual(self, mid: str) -> Optional[dict]:
        row = self._one("SELECT * FROM manuals WHERE id = ?", (mid,))
        if row:
            row["plan"] = normalize_plan(json.loads(row.pop("plan_json"))) if row.get("plan_json") else None
        return row

    LIST_COLS = "id, title, use_case, status, progress, error, page_count, created, created_by, workspace_id, visibility"

    def manuals(self) -> list[dict]:
        return self._all(f"SELECT {self.LIST_COLS} FROM manuals ORDER BY created DESC")

    def manuals_for(self, *, owners: list[str], workspace_ids: list[str], query: str = "", limit: int = 100) -> list[dict]:
        """What one person sees: what they added, their workspaces' manuals, samples, and (when searching) the
        shared catalog of public manuals. Keeps the list short however many people use the app."""
        conds, args = ["title IN (?, ?)"], ["Birch step stool", "Brewline One coffee maker"]
        if owners:
            conds.append(f"created_by IN ({','.join('?' * len(owners))})")
            args += owners
        if workspace_ids:
            conds.append(f"workspace_id IN ({','.join('?' * len(workspace_ids))})")
            args += workspace_ids
        where = "(" + " OR ".join(conds) + ")"
        if query:
            where = f"({where} OR (COALESCE(visibility, 'public') = 'public' AND status = 'ready'))" \
                    f" AND LOWER(title) LIKE ?"
            args.append(f"%{query.lower()}%")
        return self._all(f"SELECT {self.LIST_COLS} FROM manuals WHERE {where} ORDER BY created DESC LIMIT {int(limit)}",
                         tuple(args))

    def stuck_manuals(self) -> list[dict]:
        return self._all("SELECT id FROM manuals WHERE status = 'processing'")

    # ---------- sessions

    def create_session(self, person: str, manual_id: str, mode: str, pack_code: Optional[str] = None, *,
                       device_id: Optional[str] = None, user_id: Optional[str] = None,
                       workspace_id: Optional[str] = None) -> str:
        sid = new_id("ses")
        self._exec(
            "INSERT INTO sessions (id, person, manual_id, mode, started, pack_code, device_id, user_id, workspace_id)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, person, manual_id, mode, now(), pack_code, device_id, user_id, workspace_id),
        )
        return sid

    def sessions_for(self, manual_ids: list[str]) -> list[dict]:
        if not manual_ids:
            return []
        marks = ",".join("?" * len(manual_ids))
        return self._all(f"SELECT * FROM sessions WHERE manual_id IN ({marks}) ORDER BY started", tuple(manual_ids))

    def results_for(self, manual_id: str) -> list[dict]:
        """Every step result for one manual, with the person and session, for crowd insights."""
        return self._all(
            "SELECT r.*, s.person, s.pack_code FROM step_results r JOIN sessions s ON s.id = r.session_id"
            " WHERE s.manual_id = ? ORDER BY r.created", (manual_id,))

    @staticmethod
    def _session_row(row: dict) -> dict:
        row["inventory"] = json.loads(row.pop("inventory_json")) if row.get("inventory_json") else None
        return row

    def session(self, sid: str) -> Optional[dict]:
        row = self._one("SELECT * FROM sessions WHERE id = ?", (sid,))
        return self._session_row(row) if row else None

    def sessions(self, *, workspace_id: Optional[str] = None, device_id: Optional[str] = None,
                 user_id: Optional[str] = None) -> list[dict]:
        """All sessions (on one PC), or only one workspace's, or only one person's."""
        if workspace_id:
            rows = self._all("SELECT * FROM sessions WHERE workspace_id = ? ORDER BY started DESC", (workspace_id,))
        elif device_id or user_id:
            rows = self._all("SELECT * FROM sessions WHERE device_id = ? OR (user_id IS NOT NULL AND user_id = ?)"
                             " ORDER BY started DESC", (device_id or "", user_id or ""))
        else:
            rows = self._all("SELECT * FROM sessions ORDER BY started DESC")
        return [self._session_row(r) for r in rows]

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
                   observed: str, photo: str, detections: Optional[list] = None, live_id: Optional[str] = None) -> dict:
        rid = new_id("res")
        attempt = self.attempts(sid, order) + 1
        self._exec(
            "INSERT INTO step_results (id, session_id, step_order, attempt, verdict, confidence, reason, fix, observed,"
            " photo, detections_json, created, live_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, sid, order, attempt, verdict, confidence, reason, fix, observed, photo,
             json.dumps(detections or []), now(), live_id),
        )
        return {"id": rid, "attempt": attempt}

    def live_fail_logged(self, sid: str, order: int, live_id: str) -> bool:
        """A live camera run logs its first mistake only once, whichever server handles each frame."""
        return bool(self._one("SELECT id FROM step_results WHERE session_id = ? AND step_order = ? AND live_id = ?"
                              " AND verdict = 'fail'", (sid, order, live_id)))

    def result(self, rid: str) -> Optional[dict]:
        return self._one("SELECT * FROM step_results WHERE id = ?", (rid,))

    def override_result(self, rid: str, verdict: str) -> None:
        self._exec("UPDATE step_results SET override = ? WHERE id = ?", (verdict, rid))

    def results(self, sid: str) -> list[dict]:
        rows = self._all("SELECT * FROM step_results WHERE session_id = ? ORDER BY step_order, attempt", (sid,))
        for r in rows:
            r["detections"] = json.loads(r.pop("detections_json") or "[]")
            r["final"] = r["override"] or r["verdict"]
        return rows

    # ---------- QR packs

    def create_pack(self, manual_id: str, product: str, kind: str, brand: str = "",
                    workspace_id: Optional[str] = None) -> str:
        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O or 1/I: easy to read off a box
        while True:
            code = "".join(alphabet[b % len(alphabet)] for b in uuid.uuid4().bytes[:6])
            if not self.pack(code):
                break
        self._exec("INSERT INTO packs (code, manual_id, brand, product, kind, scenes, created, workspace_id)"
                   " VALUES (?,?,?,?,?,?,?,?)", (code, manual_id, brand, product, kind, "", now(), workspace_id))
        return code

    def pack(self, code: str) -> Optional[dict]:
        return self._one("SELECT * FROM packs WHERE code = ?", (code.upper(),))

    def packs(self, workspace_id: Optional[str] = None, *, all_workspaces: bool = False) -> list[dict]:
        if all_workspaces:
            return self._all("SELECT * FROM packs ORDER BY created DESC")
        if workspace_id:
            return self._all("SELECT * FROM packs WHERE workspace_id = ? ORDER BY created DESC", (workspace_id,))
        return self._all("SELECT * FROM packs WHERE workspace_id IS NULL ORDER BY created DESC")

    def pack_for_manual(self, manual_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM packs WHERE manual_id = ? ORDER BY created DESC", (manual_id,))

    def set_pack_scenes(self, code: str, scenes: str) -> None:
        self._exec("UPDATE packs SET scenes = ? WHERE code = ?", (scenes, code))

    # ---------- store links (one product read once, for everyone)

    def link(self, key: str) -> Optional[dict]:
        return self._one("SELECT * FROM links WHERE key = ?", (key,))

    def claim_link(self, key: str, manual_id: str, url: str, retailer: str) -> bool:
        """First caller wins: True if this manual is now the one for this product. Atomic across servers, so
        a hundred people pasting the same link at once still means one product read once."""
        return self._exec("INSERT INTO links (key, manual_id, url, retailer, created) VALUES (?,?,?,?,?)"
                          " ON CONFLICT (key) DO NOTHING", (key, manual_id, url, retailer, now())) == 1

    def save_link(self, key: str, manual_id: str, url: str, retailer: str) -> None:
        self._exec("INSERT INTO links (key, manual_id, url, retailer, created) VALUES (?,?,?,?,?)"
                   " ON CONFLICT (key) DO UPDATE SET manual_id = excluded.manual_id, url = excluded.url,"
                   " retailer = excluded.retailer, created = excluded.created",
                   (key, manual_id, url, retailer, now()))

    # ---------- accounts and workspaces

    def create_user(self, name: str, email: Optional[str] = None, pw_hash: Optional[str] = None) -> str:
        uid = new_id("usr")
        self._exec("INSERT INTO users (id, email, name, pw_hash, created) VALUES (?,?,?,?,?)",
                   (uid, email.lower() if email else None, name, pw_hash, now()))
        return uid

    def user(self, uid: str) -> Optional[dict]:
        return self._one("SELECT id, email, name, created FROM users WHERE id = ?", (uid,))

    def user_by_email(self, email: str) -> Optional[dict]:
        return self._one("SELECT * FROM users WHERE email = ?", (email.lower(),))

    def create_workspace(self, name: str, kind: str, owner_id: str) -> dict:
        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        while True:
            code = "".join(alphabet[b % len(alphabet)] for b in uuid.uuid4().bytes[:8])
            if not self.workspace_by_code(code):
                break
        wid = new_id("ws")
        self._exec("INSERT INTO workspaces (id, name, kind, join_code, created) VALUES (?,?,?,?,?)",
                   (wid, name, kind, code, now()))
        self.add_member(wid, owner_id, "owner")
        return self.workspace(wid)

    def workspace(self, wid: str) -> Optional[dict]:
        return self._one("SELECT * FROM workspaces WHERE id = ?", (wid,))

    def workspace_by_code(self, code: str) -> Optional[dict]:
        return self._one("SELECT * FROM workspaces WHERE join_code = ?", (code.strip().upper(),))

    def add_member(self, wid: str, uid: str, role: str = "member") -> None:
        self._exec("INSERT INTO members (workspace_id, user_id, role, created) VALUES (?,?,?,?)"
                   " ON CONFLICT (workspace_id, user_id) DO NOTHING", (wid, uid, role, now()))

    def memberships(self, uid: str) -> list[dict]:
        return self._all("SELECT w.*, m.role FROM members m JOIN workspaces w ON w.id = m.workspace_id"
                         " WHERE m.user_id = ? ORDER BY m.created", (uid,))

    def member_count(self, wid: str) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM members WHERE workspace_id = ?", (wid,))
        return int(row["n"]) if row else 0

    def save_token(self, token: str, uid: str, days: int = 30) -> None:
        expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")
        self._exec("INSERT INTO auth_tokens (token_hash, user_id, created, expires) VALUES (?,?,?,?)",
                   (_hash_token(token), uid, now(), expires))

    def user_for_token(self, token: str) -> Optional[dict]:
        row = self._one("SELECT user_id, expires FROM auth_tokens WHERE token_hash = ?", (_hash_token(token),))
        if not row or row["expires"] < now():
            return None
        return self.user(row["user_id"])

    def drop_token(self, token: str) -> None:
        self._exec("DELETE FROM auth_tokens WHERE token_hash = ?", (_hash_token(token),))

    # ---------- jobs

    def enqueue(self, kind: str, payload: dict, delay_s: int = 0) -> str:
        jid = new_id("job")
        run_after = (datetime.now(timezone.utc) + timedelta(seconds=delay_s)).isoformat(timespec="seconds")
        self._exec("INSERT INTO jobs (id, kind, payload_json, status, attempts, run_after, created, updated)"
                   " VALUES (?,?,?,?,?,?,?,?)", (jid, kind, json.dumps(payload), "queued", 0, run_after, now(), now()))
        return jid

    def claim_job(self, worker: str) -> Optional[dict]:
        """Take the oldest job that is due. Safe with many workers: each job goes to exactly one."""
        t = now()
        lock = "FOR UPDATE SKIP LOCKED" if self.postgres else ""
        sql = ("UPDATE jobs SET status = 'running', locked_by = ?, locked_at = ?, updated = ?, attempts = attempts + 1"
               f" WHERE id = (SELECT id FROM jobs WHERE status = 'queued' AND run_after <= ? ORDER BY created LIMIT 1 {lock})"
               " RETURNING *")
        if self.postgres:
            row = self._one(sql, (worker, t, t, t))
        else:
            with self._lock:
                found = self.db.execute(sql, (worker, t, t, t)).fetchone()
                self.db.commit()
            row = dict(found) if found else None
        if row:
            row["payload"] = json.loads(row.pop("payload_json"))
        return row

    def finish_job(self, jid: str, error: str = "", retry_in: Optional[int] = None) -> None:
        if retry_in is not None:
            run_after = (datetime.now(timezone.utc) + timedelta(seconds=retry_in)).isoformat(timespec="seconds")
            self._exec("UPDATE jobs SET status = 'queued', run_after = ?, error = ?, locked_by = NULL, updated = ?"
                       " WHERE id = ?", (run_after, error[:500], now(), jid))
        else:
            self._exec("UPDATE jobs SET status = ?, error = ?, updated = ? WHERE id = ?",
                       ("error" if error else "done", error[:500], now(), jid))

    def requeue_stale_jobs(self, older_than_s: int = 900) -> int:
        """Jobs whose worker stopped (a restart, a crash) go back in the queue."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_s)).isoformat(timespec="seconds")
        return self._exec("UPDATE jobs SET status = 'queued', locked_by = NULL, updated = ? WHERE status = 'running'"
                          " AND locked_at < ?", (now(), cutoff))

    def job(self, jid: str) -> Optional[dict]:
        return self._one("SELECT * FROM jobs WHERE id = ?", (jid,))

    def queue_depth(self) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued', 'running')")
        return int(row["n"]) if row else 0

    # ---------- lessons

    def save_lesson(self, concept: str, photo: str, lesson: dict) -> str:
        lid = new_id("les")
        self._exec("INSERT INTO lessons (id, concept, photo, lesson_json, created) VALUES (?,?,?,?,?)",
                   (lid, concept, photo, json.dumps(lesson), now()))
        return lid

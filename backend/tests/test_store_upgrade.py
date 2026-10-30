"""A database made before QR packs opens cleanly: the new column is added and every row is kept."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apprentice.store import Store  # noqa: E402

OLD_SESSIONS = """CREATE TABLE sessions (id TEXT PRIMARY KEY, person TEXT NOT NULL, manual_id TEXT NOT NULL,
    mode TEXT NOT NULL, started TEXT NOT NULL, finished TEXT, inventory_json TEXT)"""


def test_old_database_upgrades_in_place(tmp_path):
    db = sqlite3.connect(tmp_path / "apprentice.db")
    db.execute(OLD_SESSIONS)
    db.execute("INSERT INTO sessions VALUES ('ses_1', 'Asha', 'man_1', 'cloud', '2026-10-29T10:00:00+00:00', NULL, NULL)")
    db.commit()
    db.close()
    store = Store(tmp_path)
    s = store.session("ses_1")
    assert s["person"] == "Asha" and s["pack_code"] is None
    sid = store.create_session("Ben", "man_1", "cloud", "ABC234")
    assert store.session(sid)["pack_code"] == "ABC234"
    Store(tmp_path)  # opening again is a no-op
    assert len(store.sessions()) == 2

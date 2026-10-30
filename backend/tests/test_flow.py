"""End-to-end API tests in mock mode: no key, no network, the same code paths as the live app."""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("apprentice")
    os.environ.update({
        "APPRENTICE_MOCK": "1",
        "MODEL_API_KEY": "",
        "APPRENTICE_DATA": str(tmp / "data"),
        "APPRENTICE_TRACES": str(tmp / "traces"),
        "VISION_BACKEND": "spark",
    })
    sys.path.insert(0, str(ROOT / "backend"))
    from apprentice.config import get_settings

    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from apprentice.app import app

    with TestClient(app) as c:
        yield c


def photo(color=(200, 180, 140), size=(1600, 1200)) -> tuple[str, bytes, str]:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return ("photo.jpg", buf.getvalue(), "image/jpeg")


def wait_ready(client, mid: str) -> dict:
    for _ in range(200):
        m = client.get(f"/api/manuals/{mid}").json()
        if m["status"] != "processing":
            return m
        time.sleep(0.05)
    raise AssertionError("manual never finished")


def test_status(client):
    s = client.get("/api/status").json()
    assert s["mock"] is True
    assert s["models"]["spark"]


def test_full_assembly_flow(client):
    started = client.post("/api/manuals/sample").json()
    manual = wait_ready(client, started["id"])
    assert manual["status"] == "ready", manual
    plan = manual["plan"]
    assert len(plan["steps"]) == 5
    assert plan["steps"][0]["figure_url"].startswith("/files/")
    assert plan["reference_url"]
    assert client.get(plan["steps"][0]["figure_url"]).status_code == 200

    session = client.post("/api/sessions", json={"person": "Asha", "manual_id": manual["id"], "mode": "cloud"}).json()
    sid = session["id"]

    inv = client.post(f"/api/sessions/{sid}/inventory", files={"photo": photo()}).json()
    rows = {r["label"]: r for r in inv["rows"]}
    assert rows["E"]["found"] == 7 and rows["E"]["status"] == "missing"
    assert inv["complete"] is False
    assert inv["image"]["width"] <= 1280

    parts = client.post(f"/api/sessions/{sid}/steps/3/parts", files={"photo": photo()}).json()
    assert {d["label"] for d in parts["detections"]} == {"C", "E"}

    first = client.post(f"/api/sessions/{sid}/steps/3/check", files={"photo": photo()}).json()
    assert first["verdict"] == "fail" and first["box"]
    second = client.post(f"/api/sessions/{sid}/steps/3/check", files={"photo": photo()}).json()
    assert second["verdict"] == "pass" and second["attempt"] == 2

    for order in (1, 2, 4, 5):
        r = client.post(f"/api/sessions/{sid}/steps/{order}/check", files={"photo": photo()}).json()
        assert r["verdict"] == "pass"

    row = client.post(f"/api/sessions/{sid}/finish").json()
    assert row["verified"] is True and row["mistakes_caught"] == 1

    export = client.get("/api/export/robot-data").json()
    assert export["episodes"][0]["frames"]


def test_hybrid_mode_and_override(client):
    manual = client.post("/api/manuals/sample").json()
    sid = client.post("/api/sessions", json={"person": "Ben", "manual_id": manual["id"], "mode": "hybrid"}).json()["id"]
    r = client.post(f"/api/sessions/{sid}/steps/1/check", files={"photo": photo()}).json()
    assert r["method"] == "local parts check"
    ok = client.post(f"/api/results/{r['result_id']}/override", json={"verdict": "pass"}).json()
    assert ok["ok"]


def test_lesson(client):
    r = client.post("/api/lessons", data={"concept": "orbits", "mode": "cloud"}, files={"photo": photo()}).json()
    assert r["lesson"]["beats"]
    assert any(b["visual"]["type"] == "orbit" for b in r["lesson"]["beats"])
    assert r["review"]["approved"] is True
    assert {o["id"] for o in r["objects"]} >= {"o1", "o2"}


def test_car(client):
    bay = client.post("/api/car/engine-bay", data={"mode": "cloud"}, files={"photo": photo()}).json()
    assert len(bay["detections"]) >= 5
    lights = client.post("/api/car/warning-lights", data={"mode": "cloud"}, files={"photo": photo()}).json()
    assert lights["lights"][0]["severity"] in {"info", "caution", "stop"}


def test_traces_logged(client):
    t = client.get("/api/traces?limit=500").json()
    purposes = {e["purpose"] for e in t}
    assert {"page_extraction", "plan_building", "parts_inventory", "step_check", "lesson_script"} <= purposes
    assert all(e["tag"] == "mock" for e in t)


def test_rejects_non_pdf(client):
    r = client.post("/api/manuals", files={"file": ("x.pdf", b"hello", "application/pdf")}, data={"use_case": "assembly"})
    assert r.status_code == 400


def test_live_check_follows_a_fix_in_progress(client):
    """Step 3 starts wrong; the camera follows the fix mid-way and gives next steps from where the work is."""
    import json
    manual = client.post("/api/manuals/sample").json()
    sid = client.post("/api/sessions", json={"person": "Cara", "manual_id": manual["id"], "mode": "cloud"}).json()["id"]
    history, seen = [], []
    for frame in (1, 2, 3, 4):
        r = client.post(f"/api/sessions/{sid}/steps/3/live", files={"photo": photo()},
                        data={"frame": str(frame), "live_id": "run1", "history": json.dumps(history)}).json()
        seen.append((r["verdict"], r["state"], r["recorded"]))
        assert r["say"]
        history.append({"observed": r["observed"], "say": r["say"]})
        if r["verdict"] != "pass":
            assert r["next_steps"], r
        else:
            assert r["next_steps"] == [] and r["progress"] == 1.0
    assert seen == [("fail", "wrong", "fail"), ("progress", "fixing", None),
                    ("progress", "in_progress", None), ("pass", "done", "pass")]
    results = client.get(f"/api/sessions/{sid}").json()["results"]
    assert [x["verdict"] for x in results] == ["fail", "pass"]  # mid-fix frames are not counted as mistakes
    assert all(x["photo"].startswith("/files/") for x in results)


def test_live_check_notices_an_earlier_step(client):
    manual = client.post("/api/manuals/sample").json()
    sid = client.post("/api/sessions", json={"person": "Dev", "manual_id": manual["id"], "mode": "cloud"}).json()["id"]
    r = client.post(f"/api/sessions/{sid}/steps/4/live", files={"photo": photo()}, data={"frame": "1"}).json()
    assert r["verdict"] == "progress" and r["seen_step"]["order"] == 3 and r["seen_step"]["title"]


def test_photo_check_gives_next_steps(client):
    manual = client.post("/api/manuals/sample").json()
    sid = client.post("/api/sessions", json={"person": "Eli", "manual_id": manual["id"], "mode": "cloud"}).json()["id"]
    r = client.post(f"/api/sessions/{sid}/steps/3/check", files={"photo": photo()}).json()
    assert r["verdict"] == "fail" and r["state"] == "wrong"
    assert len(r["next_steps"]) == 3 and r["done_items"]


def test_step_animation_generated_and_cached(client):
    mid = client.post("/api/manuals/sample").json()["id"]
    first = client.get(f"/api/manuals/{mid}/steps/3/animation").json()
    a = first["animation"]
    assert first["source"] == "Muse Wonder" and first["cached"] is False
    ids = {i["id"] for i in a["instances"]}
    assert "rail" in ids and all(set(x["targets"]) <= ids for x in a["actions"])
    assert any(x["type"] == "screw" for x in a["actions"])
    again = client.get(f"/api/manuals/{mid}/steps/3/animation").json()
    assert again["cached"] is True
    assert client.get(f"/api/manuals/{mid}/steps/99/animation").status_code == 404


def test_animation_fallback_is_always_playable():
    import sys
    sys.path.insert(0, str(ROOT / "backend"))
    from apprentice.animation import fallback

    plan = {"parts": [{"label": "1", "name": "Backrest"}, {"label": "8", "name": "Screws (M6X30mm)"}, {"label": "12", "name": "Hexagon Wrench"}]}
    step = {"order": 3, "title": "Fix the backrest", "parts_used": [{"label": "1", "quantity": 1}, {"label": "8", "quantity": 4}, {"label": "12", "quantity": 1}]}
    a = fallback(step, plan)
    assert [p["shape"] for p in a["parts"]] == ["box", "screw", "lkey"]
    assert len(a["actions"]) == 3 and a["actions"][1]["type"] == "screw"


def test_big_scene_keeps_every_moving_part(client):
    mid = client.post("/api/manuals/sample").json()["id"]
    a = client.get(f"/api/manuals/{mid}/steps/4/animation").json()["animation"]
    ids = {i["id"] for i in a["instances"]}
    assert "seat" in ids and {"st0", "st1", "st2", "st3"} <= ids
    assert [x["say"] for x in a["actions"]][0].startswith("Lower seat A")

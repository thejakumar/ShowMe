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


# ---------------------------------------------------------------- QR packs, crowd heads-up, ledger, devices

def ready_sample(client, kind: str = "assembly") -> str:
    started = client.post(f"/api/manuals/sample?kind={kind}").json()
    return wait_ready(client, started["id"])["id"]


def test_qr_pack_publish_scan_and_start(client):
    mid = ready_sample(client)
    pack = client.post("/api/packs", json={"manual_id": mid, "brand": "Nordlund Home"}).json()
    code = pack["code"]
    assert len(code) == 6 and pack["url"].endswith(f"/p/{code}") and pack["brand"] == "Nordlund Home"
    again = client.post("/api/packs", json={"manual_id": mid}).json()
    assert again["code"] == code  # one guide per manual
    qr = client.get(f"/api/packs/{code}/qr.svg")
    assert qr.status_code == 200 and qr.headers["content-type"].startswith("image/svg") and b"<svg" in qr.content
    scan = client.get(f"/p/{code.lower()}", follow_redirects=False)
    assert scan.status_code == 302 and scan.headers["location"] == f"/#/pack/{code}"
    for _ in range(100):  # 3D scenes are pre-built in the background
        if client.get(f"/api/packs/{code}").json()["scenes"].get("finished"):
            break
        time.sleep(0.05)
    assert client.get(f"/api/packs/{code}").json()["scenes"] == {"done": 5, "total": 5, "wonder": 5, "finished": True}
    sid = client.post("/api/sessions", json={"person": "Shopper", "manual_id": mid, "pack_code": code}).json()["id"]
    assert client.get(f"/api/sessions/{sid}").json()["pack_code"] == code
    assert client.get("/api/packs/ZZZZZZ").status_code == 404


def test_heads_up_from_earlier_mistakes_and_brand_stats(client):
    mid = ready_sample(client)
    code = client.post("/api/packs", json={"manual_id": mid}).json()["code"]
    # someone builds it via the QR code and gets step 3 wrong first
    sid = client.post("/api/sessions", json={"person": "Fay", "manual_id": mid, "pack_code": code}).json()["id"]
    for order in (1, 2, 3, 3, 4, 5):
        client.post(f"/api/sessions/{sid}/steps/{order}/check", files={"photo": photo()})
    client.post(f"/api/sessions/{sid}/finish")
    h = client.get(f"/api/manuals/{mid}/steps/3/heads-up").json()
    assert h["mistakes"] >= 1 and "backwards" in h["tip"] and h["source"]
    assert client.get(f"/api/manuals/{mid}/steps/1/heads-up").json()["tip"] == ""
    st = client.get(f"/api/packs/{code}/stats").json()
    assert st["scans"] >= 1 and st["finished"] >= 1 and st["hardest_step"]["order"] == 3
    assert st["steps"][2]["tip"]
    # the next person's live check is told what to watch for (cached, no extra model call)
    from apprentice import insights
    from apprentice.app import S
    assert "backwards" in insights.cached_tip(S.store, mid, 3)


def test_parts_ledger_points_at_the_step_missing_a_screw(client):
    mid = ready_sample(client)
    led = client.get(f"/api/manuals/{mid}/ledger").json()
    assert [h["label"] for h in led["hardware"]] == ["D", "E"]
    assert led["after"][2]["left"] == {"D": 0, "E": 4}
    sid = client.post("/api/sessions", json={"person": "Gus", "manual_id": mid}).json()["id"]
    r = client.post(f"/api/sessions/{sid}/ledger", files={"photo": photo()}, data={"before_step": "4"}).json()
    e = next(x for x in r["rows"] if x["label"] == "E")
    assert r["ok"] is False and e["expected"] == 4 and e["found"] == 5 and e["status"] == "extra"
    assert e["blame"]["order"] == 3 and "Step 3" in e["message"]
    ok = client.post(f"/api/sessions/{sid}/ledger", files={"photo": photo()}, data={"before_step": "3"}).json()
    assert ok["ok"] is True


def test_device_tour_ask_and_guided_task(client):
    mid = ready_sample(client, "device")
    m = client.get(f"/api/manuals/{mid}").json()
    assert m["use_case"] == "device" and len(m["plan"]["parts"]) == 7 and m["plan"]["tasks"]
    tour = client.post(f"/api/manuals/{mid}/tour", files={"photo": photo()}, data={"mode": "cloud"}).json()
    assert {d["label"] for d in tour["detections"]} == {str(i) for i in range(1, 8)}
    assert all(c["found"] and c["description"] for c in tour["controls"])

    a = client.post(f"/api/manuals/{mid}/ask", json={"question": "How do I descale it"}).json()
    assert a["in_manual"] and len(a["steps"]) == 5 and a["pages"] == [3] and a["cached"] is False
    again = client.post(f"/api/manuals/{mid}/ask", json={"question": "how do i descale it?"}).json()
    assert again["cached"] is True and again["manual_id"] == a["manual_id"]
    task = client.get(f"/api/manuals/{a['manual_id']}").json()
    assert task["use_case"] == "task" and task["plan"]["parent_manual_id"] == mid
    assert task["plan"]["steps"][0]["figure_url"].startswith("/files/")
    sid = client.post("/api/sessions", json={"person": "Hal", "manual_id": a["manual_id"]}).json()["id"]
    r = client.post(f"/api/sessions/{sid}/steps/1/check", files={"photo": photo()}).json()
    assert r["verdict"] in {"pass", "fail", "progress", "unsure"}

    light = client.post(f"/api/manuals/{mid}/ask", json={"question": "What does the orange light mean?"}).json()
    assert light["in_manual"] and light["steps"] == [] and "descale" in light["answer"].lower()
    nope = client.post(f"/api/manuals/{mid}/ask", json={"question": "Can it make tea?"}).json()
    assert nope["in_manual"] is False and nope["steps"] == []
    # questions show up for the brand
    code = client.post("/api/packs", json={"manual_id": mid}).json()["code"]
    qs = client.get(f"/api/packs/{code}/stats").json()["questions"]
    assert any("descale" in q["question"].lower() for q in qs)
    assert client.post("/api/packs", json={"manual_id": a["manual_id"]}).status_code == 400

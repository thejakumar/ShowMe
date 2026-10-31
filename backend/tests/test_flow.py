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


# ---------------------------------------------------------------- store links

AMAZON_WITH_MANUAL = """<html><head><title>Amazon.com: Birch step stool</title>
<meta property="og:image" content="https://m.media-amazon.com/images/I/stool.jpg"></head><body>
<span id="productTitle"> Birch Step Stool, 2-step, solid birch </span>
<a id="bylineInfo" href="/stores/Nordlund">Visit the Nordlund Store</a>
<div id="feature-bullets"><ul><li><span>Solid birch, holds 100 kg</span></li><li><span>Assembles in 15 minutes</span></li></ul></div>
<div><h2>Product guides and documents</h2><a href="https://m.media-amazon.com/images/I/stool-manual.pdf">User Manual (PDF)</a></div>
</body></html>"""
ROBOT = "<html><head><title>Robot Check</title></head><body>Enter the characters you see below. api-services-support@amazon.com</body></html>"
IKEA_NO_MANUAL = """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@type":"Product",
"name":"BILLY Bookcase, white","brand":{"@type":"Brand","name":"IKEA"},"sku":"00263850",
"description":"Adjustable shelves. Assembly required.","image":["https://www.ikea.com/billy.jpg"]}</script></head><body></body></html>"""


@pytest.fixture()
def fake_web(client):
    import httpx
    from apprentice import links

    pdf = (ROOT / "manuals" / "sample_step_stool.pdf").read_bytes()

    def handler(request):
        url = str(request.url)
        if "stool-manual.pdf" in url:
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if "amazon.com" in url and "/dp/B0STOOL001" in url:
            return httpx.Response(200, text=AMAZON_WITH_MANUAL, headers={"content-type": "text/html"})
        if "amzn.to/short" in url:
            return httpx.Response(301, headers={"location": "https://www.amazon.com/dp/B0STOOL001"})
        if "amazon.com" in url:
            return httpx.Response(503, text=ROBOT, headers={"content-type": "text/html"})
        if "ikea.com" in url:
            return httpx.Response(200, text=IKEA_NO_MANUAL, headers={"content-type": "text/html"})
        return httpx.Response(404, text="no")

    def resolve(host):
        import ipaddress
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            return ["10.0.0.5"] if host == "intranet.local" else ["93.184.216.34"]

    links.TRANSPORT, links.RESOLVE = httpx.MockTransport(handler), resolve
    yield
    links.TRANSPORT, links.RESOLVE = None, None


def wait_any(client, mid):
    for _ in range(200):
        m = client.get(f"/api/manuals/{mid}").json()
        if m["status"] != "processing":
            return m
        time.sleep(0.05)
    raise AssertionError("never finished")


def test_amazon_link_with_official_manual(client, fake_web):
    r = client.post("/api/links", json={"url": "Have a look: https://www.amazon.com/Birch-Step-Stool/dp/B0STOOL001/ref=sr_1_1?k=stool"}).json()
    assert r["retailer"] == "Amazon" and r["product_id"] == "B0STOOL001" and r["cached"] is False
    m = wait_any(client, r["manual_id"])
    assert m["status"] == "ready", m
    src = m["plan"]["source"]
    assert src["kind"] == "manual_pdf" and src["manual_url"].endswith("stool-manual.pdf") and src["image"]
    assert m["use_case"] == "assembly" and len(m["plan"]["steps"]) == 5 and m["plan"]["steps"][0]["figure_url"]
    again = client.post("/api/links", json={"url": "https://amazon.com/dp/B0STOOL001"}).json()
    assert again["cached"] is True and again["manual_id"] == r["manual_id"]
    short = client.post("/api/links", json={"url": "https://amzn.to/short"}).json()  # resolves to the same page
    assert wait_any(client, short["manual_id"])["plan"]["source"]["product_id"] == "B0STOOL001"


def test_blocked_amazon_page_uses_the_link_and_labels_the_guide(client, fake_web):
    r = client.post("/api/links", json={"url": "https://www.amazon.com/Brewline-One-Coffee-Maker-Single/dp/B0COFFEE01"}).json()
    m = wait_any(client, r["manual_id"])
    assert m["status"] == "ready", m
    plan = m["plan"]
    assert plan["source"]["kind"] == "listing" and plan["source"]["blocked"] is True
    assert m["use_case"] == "device" and len(plan["steps"]) == 3 and plan["needs_manual"] is False
    assert all(st["figure_url"] is None for st in plan["steps"])
    sid = client.post("/api/sessions", json={"person": "Ivy", "manual_id": m["id"]}).json()["id"]
    assert client.post(f"/api/sessions/{sid}/steps/1/check", files={"photo": photo()}).status_code == 200
    # nothing usable in the link at all: ask for the name
    bare = wait_any(client, client.post("/api/links", json={"url": "https://www.amazon.com/dp/B0NONAME00"}).json()["manual_id"])
    assert bare["status"] == "error" and "Type the product name" in bare["error"]
    named = client.post("/api/links", json={"url": "https://www.amazon.com/dp/B0NONAME00", "name": "Brewline One coffee maker"}).json()
    assert wait_any(client, named["manual_id"])["status"] == "ready"


def test_ikea_without_manual_gives_preparation_only(client, fake_web):
    r = client.post("/api/links", json={"url": "https://www.ikea.com/us/en/p/billy-bookcase-white-00263850/"}).json()
    m = wait_any(client, r["manual_id"])
    plan = m["plan"]
    assert m["use_case"] == "assembly" and plan["needs_manual"] is True and plan["source"]["retailer"] == "IKEA"
    assert "instruction" in plan["steps"][-1]["title"].lower()


def test_links_that_are_not_allowed(client, fake_web):
    for bad in ("http://127.0.0.1:8000/api/status", "http://192.168.1.5/admin", "https://intranet.local/x", "not a link",
                "https://shop.example.com:8443/p"):
        r = client.post("/api/links", json={"url": bad})
        assert r.status_code == 400, bad


def test_read_page_finds_product_details():
    from apprentice.links import read_page
    d = read_page(AMAZON_WITH_MANUAL, "https://www.amazon.com/dp/B0STOOL001")
    assert d["title"].startswith("Birch Step Stool") and d["brand"] == "Nordlund"
    assert d["bullets"] == ["Solid birch, holds 100 kg", "Assembles in 15 minutes"]
    assert d["manual_links"][0]["url"].endswith("stool-manual.pdf") and d["blocked"] is False
    assert read_page(ROBOT, "https://www.amazon.com/dp/X")["blocked"] is True
    ikea = read_page(IKEA_NO_MANUAL, "https://www.ikea.com/p/x")
    assert ikea["title"] == "BILLY Bookcase, white" and ikea["brand"] == "IKEA" and ikea["model"] == "00263850"


def test_upload_photos_of_a_paper_booklet(client):
    files = [("file", (f"page{i}.jpg", photo((200 + i * 10, 200, 200))[1], "image/jpeg")) for i in range(3)]
    r = client.post("/api/manuals", files=files, data={"use_case": "device", "title": "Desk fan"}).json()
    m = wait_any(client, r["id"])
    assert m["status"] == "ready" and m["page_count"] == 3


def test_short_link_name_and_mobile_retry(client, fake_web):
    import httpx
    from apprentice import links
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        url = str(request.url)
        if "a.co/d/" in url:
            return httpx.Response(301, headers={"location": "https://www.amazon.com/Brewline-One-Coffee-Maker/dp/B0BREW0001?ref_=x"})
        if "iPhone" in request.headers["user-agent"]:
            return httpx.Response(200, text=AMAZON_WITH_MANUAL.replace("stool-manual.pdf", "none.html"), headers={"content-type": "text/html"})
        return httpx.Response(503, text=ROBOT, headers={"content-type": "text/html"})

    links.TRANSPORT = httpx.MockTransport(handler)
    listing = __import__("asyncio").run(links.fetch_listing(links.parse_link("https://a.co/d/0iUSuCRw")))
    assert listing.title.startswith("Birch Step Stool") and listing.product_id == "B0BREW0001"
    assert any("iPhone" in ua for ua in seen) and any("Windows" in ua for ua in seen)

    def always_blocked(request):
        if "a.co/d/" in str(request.url):
            return httpx.Response(301, headers={"location": "https://www.amazon.com/Brewline-One-Coffee-Maker/dp/B0BREW0001"})
        return httpx.Response(503, text=ROBOT, headers={"content-type": "text/html"})

    links.TRANSPORT = httpx.MockTransport(always_blocked)
    r = client.post("/api/links", json={"url": "https://a.co/d/0iUSuCRw"}).json()
    m = wait_any(client, r["manual_id"])
    assert m["status"] == "ready" and m["plan"]["source"]["product_id"] == "B0BREW0001", m


def test_where_am_i_picks_up_midway(client):
    mid = ready_sample(client)
    sid = client.post("/api/sessions", json={"person": "Jo", "manual_id": mid}).json()["id"]
    r = client.post(f"/api/sessions/{sid}/where", files={"photo": photo()}).json()
    assert r["done_steps"] == [1, 2, 3] and r["current_step"] == 4 and r["current_title"] == "Fit the seat"
    assert r["recorded_passes"] == [1, 2, 3] and r["next_steps"] and r["say"]
    results = client.get(f"/api/sessions/{sid}").json()["results"]
    assert sorted(x["step_order"] for x in results if x["final"] == "pass") == [1, 2, 3]
    again = client.post(f"/api/sessions/{sid}/where", files={"photo": photo()}).json()
    assert again["recorded_passes"] == []  # already checked off: nothing logged twice
    hyb = client.post("/api/sessions", json={"person": "Kim", "manual_id": mid, "mode": "hybrid"}).json()["id"]
    assert client.post(f"/api/sessions/{hyb}/where", files={"photo": photo()}).status_code == 409


def test_where_am_i_puts_an_earlier_mistake_first():
    import asyncio
    sys.path.insert(0, str(ROOT / "backend"))
    from apprentice import sample, training
    from apprentice.config import Settings

    class FakeMuse:
        async def json(self, **kw):
            return kw["schema"].model_validate({"done_steps": [1, 2, 3], "current_step": 4, "confidence": 0.9,
                                                "wrong": [{"step": 2, "observed": "dowels in the outer face", "fix": "move them"}]})

    r = asyncio.run(training.resume(None, sample.sample_plan(), None, muse=FakeMuse(), settings=Settings(), ctx={}))
    assert r["current_step"] == 2 and 2 not in r["done_steps"] and r["wrong"][0]["title"]

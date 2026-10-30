"""Measure ShowMe on your own photos, against answers you wrote down first.

    python eval/run.py --cases eval/cases.json [--base http://localhost:8000] [--mode cloud]

Runs every photo through the running app's real API, compares with the ground truth in the
cases file, and writes eval/results.md. Report the latest full run, not the best one.
See eval/cases.example.json for the file format.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

EVAL_PERSON = "__eval__"  # hidden from the team passport and robot export


def post_photo(client: httpx.Client, url: str, photo: Path, data: dict | None = None) -> tuple[dict, float]:
    started = time.perf_counter()
    with photo.open("rb") as fh:
        r = client.post(url, files={"photo": (photo.name, fh, "image/jpeg")}, data=data or {})
    elapsed = time.perf_counter() - started
    if r.status_code >= 400:
        raise RuntimeError(f"{url} -> {r.status_code}: {r.text[:300]}")
    return r.json(), elapsed


def manual_id_for(client: httpx.Client, cases: dict) -> str:
    if cases.get("manual_id"):
        return cases["manual_id"]
    if cases.get("sample"):
        mid = client.post("/api/manuals/sample").json()["id"]
        for _ in range(600):
            m = client.get(f"/api/manuals/{mid}").json()
            if m["status"] != "processing":
                if m["status"] != "ready":
                    raise RuntimeError(f"Sample manual failed: {m.get('error')}")
                return mid
            time.sleep(1)
    raise SystemExit("cases.json needs manual_id (from the app) or \"sample\": true")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="eval/cases.json")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--mode", default="cloud", choices=["cloud", "hybrid"])
    ap.add_argument("--out", default="eval/results.md")
    args = ap.parse_args()

    cases_path = Path(args.cases)
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    root = cases_path.parent
    client = httpx.Client(base_url=args.base, timeout=300)
    status = client.get("/api/status").json()
    lines = [f"# ShowMe eval, {datetime.now():%Y-%m-%d %H:%M}", "",
             f"Mode: {args.mode}. Models: {status['models']}. Vision: {status['vision_backend']}."
             + (" **Mock mode: these numbers are not real.**" if status["mock"] else ""), ""]
    latencies: list[float] = []

    mid = None
    if cases.get("inventory") or cases.get("checks"):
        mid = manual_id_for(client, cases)

    # ---- parts counting
    inv = cases.get("inventory", [])
    if inv:
        exact = total = 0
        abs_err: list[int] = []
        rows = []
        for case in inv:
            sid = client.post("/api/sessions", json={"person": EVAL_PERSON, "manual_id": mid, "mode": args.mode}).json()["id"]
            res, t = post_photo(client, f"/api/sessions/{sid}/inventory", root / case["photo"])
            latencies.append(t)
            found = {r["label"]: r["found"] for r in res["rows"]}
            for label, want in case["counts"].items():
                got = found.get(label, 0)
                total += 1
                exact += got == want
                abs_err.append(abs(got - want))
                if got != want:
                    rows.append(f"| {case['photo']} | {label} | {want} | {got} |")
        lines += ["## Part counting", "",
                  f"Part types counted exactly: **{exact} of {total} ({100 * exact / max(total, 1):.0f}%)**. "
                  f"Mean count error: {statistics.mean(abs_err):.2f} pieces.", ""]
        if rows:
            lines += ["| Photo | Part | Expected | Found |", "| --- | --- | --- | --- |", *rows, ""]

    # ---- step checking
    checks = cases.get("checks", [])
    if checks:
        caught = mistakes = false_alarms = correct = unsure = 0
        detail = []
        for case in checks:
            sid = client.post("/api/sessions", json={"person": EVAL_PERSON, "manual_id": mid, "mode": args.mode}).json()["id"]
            res, t = post_photo(client, f"/api/sessions/{sid}/steps/{case['step']}/check", root / case["photo"])
            latencies.append(t)
            want, got = case["expected"], res["verdict"]
            unsure += got == "unsure"
            if want == "fail":
                mistakes += 1
                caught += got == "fail"
            else:
                correct += 1
                false_alarms += got == "fail"
            detail.append(f"| {case['photo']} | {case['step']} | {want} | {got} | {res['confidence']:.2f} | {res.get('observed', '')[:80]} |")
        lines += ["## Step checking", "",
                  f"Mistakes caught: **{caught} of {mistakes}**. False alarms on correct work: **{false_alarms} of {correct}**. "
                  f"Unsure (asked the person): {unsure} of {len(checks)}.", "",
                  "| Photo | Step | Expected | Verdict | Confidence | What it saw |", "| --- | --- | --- | --- | --- | --- |",
                  *detail, ""]

    # ---- car parts
    car = cases.get("car", [])
    if car:
        hit = want_total = 0
        for case in car:
            res, t = post_photo(client, "/api/car/engine-bay", root / case["photo"],
                                {"manual_id": case.get("manual_id", ""), "mode": args.mode})
            latencies.append(t)
            names = {d["name"].lower() for d in res["detections"]}
            for name in case["expected"]:
                want_total += 1
                hit += name.lower() in names
        lines += ["## Car engine bay", "", f"Named parts found: **{hit} of {want_total} ({100 * hit / max(want_total, 1):.0f}%)**.", ""]

    if latencies:
        lines += ["## Speed", "", f"Median time per photo: {statistics.median(latencies):.1f} s "
                  f"(slowest {max(latencies):.1f} s, {len(latencies)} photos).", ""]

    out = Path(args.out)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

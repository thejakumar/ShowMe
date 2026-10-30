"""What earlier builders teach the next one, and what a brand learns from all of them.

- heads_up(): before a step, the mistake people most often made there, summarized once by Muse Spark
  from the real caught mistakes and cached until a new mistake is logged.
- pack_stats(): the brand dashboard for one QR pack: scans, finished builds, build time, mistake
  rate per step, and the questions people asked about the product.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from typing import Optional

from . import prompts
from .config import Settings
from .muse import MuseClient, MuseError
from .schemas import HeadsUpReply
from .store import Store

EVAL_PERSON = "__eval__"
MIN_MISTAKES_FOR_TIP = 1


def _cache_path(store: Store, manual_id: str):
    return store.path("manuals", manual_id, "insights.json")


def _read_cache(store: Store, manual_id: str) -> dict:
    path = _cache_path(store, manual_id)
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def step_stats(store: Store, manual_id: str, plan: dict) -> list[dict]:
    """Per step: how many people reached it, how many got it wrong at least once, and their mistakes."""
    results = [r for r in store.results_for(manual_id) if r["person"] != EVAL_PERSON]
    rows = []
    for st in plan.get("steps", []):
        here = [r for r in results if r["step_order"] == st["order"]]
        reached = {r["session_id"] for r in here}
        wrong = [r for r in here if r["verdict"] == "fail"]
        wrong_sessions = {r["session_id"] for r in wrong}
        rows.append({
            "order": st["order"],
            "title": st["title"],
            "builders": len(reached),
            "mistakes": len(wrong_sessions),
            "rate": round(len(wrong_sessions) / len(reached), 2) if reached else 0.0,
            "examples": [{"observed": r["observed"] or "", "reason": r["reason"] or "", "fix": r["fix"] or ""}
                         for r in wrong[-12:]],
        })
    return rows


def cached_tip(store: Store, manual_id: str, order: int) -> str:
    """The last summarized heads-up for a step, without calling a model (used inside live checks)."""
    return (_read_cache(store, manual_id).get(str(order)) or {}).get("tip", "")


async def heads_up(store: Store, muse: MuseClient, settings: Settings, manual_id: str, plan: dict,
                   order: int) -> dict:
    stats = next((s for s in step_stats(store, manual_id, plan) if s["order"] == order), None)
    if stats is None:
        raise KeyError(f"No step {order}")
    out = {"order": order, "builders": stats["builders"], "mistakes": stats["mistakes"], "rate": stats["rate"],
           "tip": "", "look_for": "", "source": None}
    if stats["mistakes"] < MIN_MISTAKES_FOR_TIP:
        return out
    cache = _read_cache(store, manual_id)
    hit = cache.get(str(order))
    if hit and hit.get("n") == stats["mistakes"]:
        return {**out, "tip": hit["tip"], "look_for": hit.get("look_for", ""), "source": hit.get("source")}
    step = next(s for s in plan["steps"] if s["order"] == order)
    try:
        reply = await muse.json(
            role="spark", purpose="mistake_summary", system=prompts.HEADS_UP_SYSTEM,
            text=prompts.heads_up_prompt(step, stats["examples"]), schema=HeadsUpReply,
            effort=settings.check_effort, ctx={"manual_id": manual_id, "step": order},
            mock_args={"step": order, "examples": stats["examples"]},
        )
        tip, look_for, source = reply.tip, reply.look_for, "Muse Spark, from earlier builders"
    except MuseError:
        last = stats["examples"][-1]
        tip, look_for, source = last["fix"] or last["reason"] or last["observed"], "", "the last mistake logged"
    if tip:
        cache[str(order)] = {"n": stats["mistakes"], "tip": tip, "look_for": look_for, "source": source}
        _cache_path(store, manual_id).write_text(json.dumps(cache, indent=1))
    return {**out, "tip": tip, "look_for": look_for, "source": source}


def _minutes(s: dict) -> Optional[float]:
    try:
        a = datetime.fromisoformat(s["started"])
        b = datetime.fromisoformat(s["finished"])
    except (TypeError, ValueError, KeyError):
        return None
    m = (b - a).total_seconds() / 60
    return round(m, 1) if m >= 0 else None


def pack_stats(store: Store, pack: dict, plan: dict) -> dict:
    """Brand dashboard for one pack."""
    mid = pack["manual_id"]
    total_steps = len(plan.get("steps", []))
    sessions = [s for s in store.sessions_for([mid]) if s["person"] != EVAL_PERSON]
    scans = [s for s in sessions if s.get("pack_code") == pack["code"]]
    finished, times = 0, []
    for s in sessions:
        final: dict[int, str] = {}
        for r in store.results(s["id"]):
            final[r["step_order"]] = r["final"]
        if total_steps and sum(1 for v in final.values() if v == "pass") == total_steps:
            finished += 1
            if (m := _minutes(s)) is not None:
                times.append(m)
    steps = step_stats(store, mid, plan)
    cache = _read_cache(store, mid)
    for row in steps:
        row["tip"] = (cache.get(str(row["order"])) or {}).get("tip", "")
        row.pop("examples", None)
    worst = max(steps, key=lambda r: (r["rate"], r["mistakes"]), default=None)
    questions = []
    for m in store.manuals():
        if m["use_case"] != "task" or m["status"] != "ready":
            continue
        child = store.manual(m["id"])
        if (child.get("plan") or {}).get("parent_manual_id") != mid:
            continue
        asked = len(store.sessions_for([m["id"]]))
        questions.append({"question": child["plan"].get("question") or m["title"], "guided": asked,
                          "in_manual": child["plan"].get("in_manual", True)})
    return {
        "code": pack["code"],
        "product": pack["product"],
        "brand": pack["brand"],
        "kind": pack["kind"],
        "scenes": pack["scenes"],
        "sessions": len(sessions),
        "scans": len(scans),
        "finished": finished,
        "completion": round(finished / len(sessions), 2) if sessions else 0.0,
        "median_minutes": statistics.median(times) if times else None,
        "mistakes_caught": sum(r["mistakes"] for r in steps),
        "steps": steps,
        "hardest_step": worst if worst and worst["mistakes"] else None,
        "questions": questions,
    }

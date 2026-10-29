"""Skill passport (who is trained on what, verified by camera) and robot-ready data export."""

from __future__ import annotations

from .store import Store

EVAL_PERSON = "__eval__"  # sessions created by eval/run.py stay out of the passport and export


def _final_by_step(results: list[dict]) -> dict[int, dict]:
    last: dict[int, dict] = {}
    for r in results:
        last[r["step_order"]] = r
    return last


def passport(store: Store) -> list[dict]:
    manuals = {m["id"]: m for m in store.manuals()}
    rows = []
    for s in store.sessions():
        if s["person"] == EVAL_PERSON:
            continue
        manual = store.manual(s["manual_id"]) if s["manual_id"] in manuals else None
        plan = (manual or {}).get("plan") or {}
        total = len(plan.get("steps", []))
        results = store.results(s["id"])
        final = _final_by_step(results)
        passed = sum(1 for r in final.values() if r["final"] == "pass")
        caught = sum(1 for r in results if r["verdict"] == "fail")
        rows.append({
            "session_id": s["id"],
            "person": s["person"],
            "product": plan.get("product") or (manual or {}).get("title", "Unknown"),
            "use_case": (manual or {}).get("use_case", ""),
            "mode": s["mode"],
            "started": s["started"],
            "finished": s["finished"],
            "steps_total": total,
            "steps_passed": passed,
            "attempts": len(results),
            "mistakes_caught": caught,
            "overrides": sum(1 for r in results if r["override"]),
            "verified": total > 0 and passed == total,
            "inventory_complete": (s.get("inventory") or {}).get("complete"),
        })
    return rows


def robot_export(store: Store) -> dict:
    """One episode per session: the task, each step's instruction and done-when, the photo, the verdict.

    The shape is deliberately plain JSON so it can be converted to any robot-learning format later.
    """
    episodes = []
    for s in store.sessions():
        if s["person"] == EVAL_PERSON:
            continue
        manual = store.manual(s["manual_id"])
        if not manual or not manual.get("plan"):
            continue
        plan = manual["plan"]
        steps = {st["order"]: st for st in plan.get("steps", [])}
        frames = []
        for r in store.results(s["id"]):
            st = steps.get(r["step_order"], {})
            frames.append({
                "step": r["step_order"],
                "attempt": r["attempt"],
                "instruction": st.get("instruction"),
                "done_when": st.get("done_when"),
                "parts_used": st.get("parts_used", []),
                "photo": r["photo"],
                "verdict": r["final"],
                "model_verdict": r["verdict"],
                "human_override": r["override"],
                "observed": r["observed"],
                "detections": r["detections"],
            })
        episodes.append({
            "episode_id": s["id"],
            "task": plan.get("product"),
            "use_case": manual.get("use_case"),
            "operator": s["person"],
            "started": s["started"],
            "finished": s["finished"],
            "inventory": s.get("inventory"),
            "frames": frames,
        })
    return {"format": "apprentice-episodes/v1", "episodes": episodes}

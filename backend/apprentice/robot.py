"""Teach a robot: the same manual, steps and camera checks, made useful to robots.

Three pieces, all built on what ShowMe already has:

1. Robot task plan. Muse Wonder turns each step of the manual's plan into robot primitives (pick, place,
   insert, screw...), keeps the step's visible "done when" as the success check, and flags steps a robot
   shouldn't do alone. Cached per manual.
2. Robot dataset. Every camera-checked step people did becomes a labelled example: the photo or live frame,
   the step's language instruction, pass or fail, who decided (camera or person), and the correction that
   fixed a mistake. Exported as a zip in a LeRobot-style layout (meta/, data/, images/). No names inside.
3. Success checker. A robot (or a simulator) sends a camera frame for a step and gets back success true or
   false with what's wrong: the same Muse Spark check people get, usable as a robot's reward signal.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Optional

from . import prompts
from .config import Settings
from .muse import MuseClient, MuseError
from .schemas import RobotPlan
from .store import Store

EVAL_PERSON = "__eval__"
SKILLS = ("pick", "place", "insert", "align", "screw", "tighten", "press", "rotate", "hold", "connect", "inspect")
FORMAT = "showme-robot/v1"


def plan_path(store: Store, manual_id: str):
    return store.path("manuals", manual_id, "robot_plan.json")


# ---------------------------------------------------------------- 1. robot task plan

_VERBS = [  # first match wins; used for the simple outline when Muse is unavailable
    ("screw", r"\b(screw|fix (it|them)? ?with|bolt)\b"),
    ("tighten", r"\btighten"),
    ("insert", r"\b(push|insert|slot|tap)\b"),
    ("align", r"\b(line up|align)\b"),
    ("rotate", r"\b(turn|rotate|twist)\b"),
    ("press", r"\b(press|hold down)\b"),
    ("connect", r"\b(plug|connect|attach the cable)\b"),
    ("place", r"\b(fit|place|lay|put|set|attach|mount|lower)\b"),
]


def _is_hardware(name: str) -> bool:
    return bool(re.search(r"screw|bolt|nut|dowel|pin|peg|cam|washer", name.lower()))


def outline(plan: dict) -> dict:
    """A rule-based robot outline from the step text: no model call. Used as the fallback, and labelled."""
    names = {p["label"]: p.get("name", p["label"]) for p in plan.get("parts", [])}
    steps = []
    for st in plan.get("steps", []):
        text = (st.get("instruction") or "").lower()
        skill = next((k for k, rx in _VERBS if re.search(rx, text)), "place")
        actions = []
        uses = sorted(st.get("parts_used", []), key=lambda u: _is_hardware(names.get(u["label"], "")))
        for u in uses:
            name = names.get(u["label"], u["label"])
            obj = f"{u['label']} {name}".strip()
            qty = int(u.get("quantity") or 1)
            low = name.lower()
            if re.search(r"\b(key|screwdriver|wrench|spanner|hammer|allen)\b", low):
                actions.append({"skill": "pick", "object": obj, "target": "", "tool": "", "detail": "tool"})
                actions.append({"skill": skill if skill in ("tighten", "rotate", "press") else "tighten", "object": "",
                                "target": "", "tool": obj, "detail": ""})
                continue
            do = ("screw" if re.search(r"screw|bolt", low) else "insert" if re.search(r"dowel|pin|peg|cam", low)
                  else "place" if skill in ("screw", "tighten", "insert") else skill)
            many = f"{qty} pieces, one at a time" if qty > 1 else ""
            actions.append({"skill": "pick", "object": obj, "target": "", "tool": "", "detail": many})
            actions.append({"skill": do, "object": obj, "target": "", "tool": "", "detail": f"repeat {qty}×" if qty > 1 else ""})
        actions.append({"skill": "inspect", "object": "", "target": "", "tool": "camera", "detail": "check the done-when"})
        steps.append({"order": st["order"], "title": st.get("title", ""), "actions": actions,
                      "success_check": st.get("done_when") or st.get("instruction", ""),
                      "needs_human": bool(st.get("warning")), "human_reason": st.get("warning") or "", "risks": []})
    return {"steps": steps, "tools": [], "notes": "Simple outline from the step text."}


def _clean(plan: dict, reply: RobotPlan) -> dict:
    by_order = {s.order: s for s in reply.steps}
    steps = []
    for st in plan.get("steps", []):  # keep the manual's steps and order, whatever the model returned
        r = by_order.get(st["order"])
        if r is None or not r.actions:
            continue
        steps.append({
            "order": st["order"], "title": st.get("title", ""),
            "actions": [{"skill": a.skill if a.skill in SKILLS else "place", "object": a.object, "target": a.target,
                         "tool": a.tool, "detail": a.detail} for a in r.actions][:12],
            "success_check": r.success_check or st.get("done_when", ""),
            "needs_human": r.needs_human, "human_reason": r.human_reason, "risks": r.risks,
        })
    return {"steps": steps, "tools": reply.tools, "notes": reply.notes}


async def robot_plan(store: Store, muse: MuseClient, settings: Settings, manual_id: str, plan: dict, *,
                     refresh: bool = False) -> dict:
    path = plan_path(store, manual_id)
    if path.exists() and not refresh:
        return {**json.loads(path.read_text(encoding="utf-8")), "cached": True}
    source, error = "Muse Wonder", None
    try:
        reply = await muse.json(
            role="wonder", purpose="robot_plan", system=prompts.ROBOT_SYSTEM,
            text=prompts.robot_prompt(plan), schema=RobotPlan, effort=settings.plan_effort,
            ctx={"manual_id": manual_id}, mock_args={"plan": plan},
        )
        data = _clean(plan, reply)
        if len(data["steps"]) < max(1, len(plan.get("steps", [])) // 2):
            data = None
    except MuseError as exc:
        data, error = None, str(exc)[:200]
    if data is None:
        data, source = outline(plan), "simple outline"
    result = {"manual_id": manual_id, "product": plan.get("product", ""), "source": source, "error": error, **data}
    if source == "Muse Wonder":
        path.write_text(json.dumps(result), encoding="utf-8")
    return {**result, "cached": False}


def cached_plan(store: Store, manual_id: str) -> Optional[dict]:
    path = plan_path(store, manual_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ---------------------------------------------------------------- 2. robot dataset

def _seconds(a: Optional[str], b: Optional[str]) -> float:
    try:
        return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds(), 2)
    except (TypeError, ValueError):
        return 0.0


def collect(store: Store, sessions: list[dict]) -> dict:
    """Episodes and labelled frames from people's sessions (no names), plus the language tasks."""
    tasks: dict[tuple[str, int], dict] = {}
    episodes, frames = [], []
    manuals: dict[str, Optional[dict]] = {}
    for s in sessions:
        if s["person"] == EVAL_PERSON:
            continue
        if s["manual_id"] not in manuals:
            manuals[s["manual_id"]] = store.manual(s["manual_id"])
        m = manuals[s["manual_id"]]
        if not m or not m.get("plan"):
            continue
        plan = m["plan"]
        steps = {st["order"]: st for st in plan.get("steps", [])}
        results = [r for r in store.results(s["id"]) if r.get("photo")]
        if not results:
            continue
        ep = len(episodes)
        rplan = cached_plan(store, m["id"]) or {}
        ractions = {st["order"]: st["actions"] for st in rplan.get("steps", [])}
        for i, r in enumerate(results):
            st = steps.get(r["step_order"], {})
            key = (m["id"], r["step_order"])
            if key not in tasks:
                tasks[key] = {"task_index": len(tasks), "manual_id": m["id"], "product": plan.get("product", m["title"]),
                              "use_case": m.get("use_case"), "step": r["step_order"], "title": st.get("title", ""),
                              "instruction": st.get("instruction", ""), "done_when": st.get("done_when", ""),
                              "parts_used": st.get("parts_used", []), "robot_actions": ractions.get(r["step_order"], [])}
            human = bool(r.get("override"))
            frames.append({
                "episode_index": ep, "frame_index": i, "timestamp": _seconds(s["started"], r["created"]),
                "task_index": tasks[key]["task_index"], "step": r["step_order"], "attempt": r["attempt"],
                "photo": r["photo"],
                "label": {"success": r["final"] == "pass", "verdict": r["final"], "model_verdict": r["verdict"],
                          "decided_by": "person" if human else "camera", "confidence": r.get("confidence"),
                          "observed": r.get("observed") or "", "reason": r.get("reason") or "",
                          "correction": r.get("fix") or ""},
            })
        finals = {}
        for r in results:
            finals[r["step_order"]] = r["final"]
        episodes.append({"episode_index": ep, "product": plan.get("product", m["title"]), "manual_id": m["id"],
                         "use_case": m.get("use_case"), "length": len(results),
                         "steps_total": len(steps), "steps_passed": sum(v == "pass" for v in finals.values()),
                         "success": bool(steps) and all(finals.get(o) == "pass" for o in steps),
                         "mistakes": sum(1 for r in results if r["final"] == "fail"),
                         "duration_s": _seconds(s["started"], s.get("finished") or results[-1]["created"])})
    return {"tasks": sorted(tasks.values(), key=lambda t: t["task_index"]), "episodes": episodes, "frames": frames}


def summary(store: Store, sessions: list[dict]) -> dict:
    data = collect(store, sessions)
    f = data["frames"]
    products = sorted({e["product"] for e in data["episodes"]})
    return {"episodes": len(data["episodes"]), "frames": len(f),
            "successes": sum(x["label"]["success"] for x in f),
            "mistakes": sum(x["label"]["verdict"] == "fail" for x in f),
            "corrections": sum(bool(x["label"]["correction"]) and x["label"]["verdict"] == "fail" for x in f),
            "by_person": sum(x["label"]["decided_by"] == "person" for x in f),
            "tasks": len(data["tasks"]), "products": products, "format": FORMAT}


CARD = """# ShowMe robot dataset

Real people building and operating products, step by step, with every step checked by a camera against
the product's manual. Exported by ShowMe ({created}).

- {episodes} episodes (one per session), {frames} labelled frames, {tasks} language tasks.
- Each frame: a photo or live-camera frame of the work, the step's instruction (the language task),
  whether the step was right (`label.success`), who decided (`camera` = Muse Spark, `person` = a human
  confirmation), what the camera saw, and for mistakes, the correction that fixed them.
- Tasks also carry robot primitives (pick, place, insert, screw...) when a robot task plan was built.

Layout (LeRobot-style):

    meta/info.json        format, counts, field list
    meta/tasks.jsonl      one language task per manual step
    meta/episodes.jsonl   one line per episode: product, length, success, mistakes
    data/episode_NNNNNN.jsonl   one line per frame, pointing at images/episode_NNNNNN/frame_NNNNNN.jpg
    images/...            the frames

No names or account details are included. Make sure the people whose builds are in here agreed to their
photos being used for training before you share the dataset.
"""


def dataset_zip(store: Store, sessions: list[dict]) -> bytes:
    data = collect(store, sessions)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        by_ep: dict[int, list[dict]] = {}
        for fr in data["frames"]:
            by_ep.setdefault(fr["episode_index"], []).append(fr)
        n_images = 0
        for ep, frs in by_ep.items():
            lines = []
            for fr in frs:
                src = store.dir / fr["photo"].removeprefix("/files/")
                img_name = f"images/episode_{ep:06d}/frame_{fr['frame_index']:06d}.jpg"
                if src.exists():
                    z.write(src, img_name)
                    n_images += 1
                else:
                    img_name = None
                row = {k: v for k, v in fr.items() if k != "photo"}
                row["observation.image"] = img_name
                lines.append(json.dumps(row))
            z.writestr(f"data/episode_{ep:06d}.jsonl", "\n".join(lines) + "\n")
        z.writestr("meta/tasks.jsonl", "".join(json.dumps(t) + "\n" for t in data["tasks"]))
        z.writestr("meta/episodes.jsonl", "".join(json.dumps(e) + "\n" for e in data["episodes"]))
        info = {"format": FORMAT, "created": created, "episodes": len(data["episodes"]), "frames": len(data["frames"]),
                "images": n_images, "tasks": len(data["tasks"]),
                "features": {"observation.image": "jpg path", "task_index": "meta/tasks.jsonl row",
                             "label.success": "bool", "label.verdict": "pass | fail | unsure",
                             "label.decided_by": "camera | person", "label.correction": "text",
                             "timestamp": "seconds since the episode started"}}
        z.writestr("meta/info.json", json.dumps(info, indent=2))
        z.writestr("README.md", CARD.format(created=created, episodes=len(data["episodes"]),
                                            frames=len(data["frames"]), tasks=len(data["tasks"])))
    return buf.getvalue()

"""Hands-on training flows that share one engine: parts inventory, show a step's parts, check a step."""

from __future__ import annotations

from collections import Counter
from typing import Optional

from PIL import Image

from . import prompts
from .config import Settings
from .muse import MuseClient
from .schemas import ResumeReply, StepCheckReply
from .vision import Vision


def targets_for(plan: dict, labels: Optional[list[str]] = None) -> list[dict]:
    wanted = set(labels) if labels is not None else None
    out = []
    for p in plan.get("parts", []):
        if wanted is None or p["label"] in wanted:
            out.append({"label": p["label"], "name": p["name"], "prompt": p.get("sam_prompt") or p["name"],
                        "quantity": p.get("quantity", 1)})
    return out


def step_by_order(plan: dict, order: int) -> Optional[dict]:
    return next((s for s in plan.get("steps", []) if s["order"] == order), None)


ACTIVE = {"not_started", "in_progress", "fixing"}


def coach_fields(reply: Optional[StepCheckReply], plan: dict, order: int, verdict: str) -> tuple[str, dict]:
    """Where the work is now and what to do next, plus the final verdict.

    A step that is half done, or half undone in the middle of a fix, is "progress", not a mistake:
    only a "wrong" state counts as a caught mistake. Returns (verdict, fields).
    """
    by_verdict = {"pass": "done", "fail": "wrong", "unsure": "cant_see"}
    state = (reply.state if reply else "") or by_verdict.get(verdict, "cant_see")
    if verdict == "pass" and state in ACTIVE:
        verdict = "progress"  # the model's own state says it is not finished: trust the cautious reading
    elif verdict == "pass" and state == "wrong":
        verdict = "fail"
    elif verdict == "fail" and state in ACTIVE:
        verdict = "progress"
    elif verdict == "fail" and state == "done":
        state = "wrong"
    elif verdict == "pass":
        state = "done"
    progress = reply.progress if reply and reply.progress is not None else (
        1.0 if state == "done" else 0.0 if state == "not_started" else None)
    if verdict == "pass":
        progress = 1.0
    next_steps = list(reply.next_steps) if reply else []
    if not next_steps and verdict != "pass" and reply and reply.fix:
        next_steps = [reply.fix]
    if verdict == "pass":
        next_steps = []
    seen = reply.seen_step if reply else None
    seen_step = next((st for st in plan.get("steps", []) if st["order"] == seen), None) if seen != order else None
    return verdict, {
        "state": state,
        "progress": None if progress is None else round(progress, 2),
        "done_items": list(reply.done_items) if reply else [],
        "next_steps": next_steps[:3],
        "seen_step": {"order": seen_step["order"], "title": seen_step["title"]} if seen_step else None,
    }


async def inventory(img: Image.Image, plan: dict, *, vision: Vision, settings: Settings, private: bool,
                    reference: Optional[Image.Image], ctx: dict) -> dict:
    """One photo of all the parts -> every instance found, labeled and counted against the parts list."""
    targets = targets_for(plan)
    dets, notes = await vision.locate(
        img, targets, purpose="parts_inventory", count_every_instance=True,
        reference=None if private else reference, private=private, ctx=ctx,
    )
    counts = Counter(d["label"] for d in dets)
    unsure = Counter(d["label"] for d in dets if d["confidence"] < settings.min_confidence)
    rows = []
    for t in targets:
        found, need = counts.get(t["label"], 0), int(t.get("quantity") or 1)
        status = "ok" if found == need else ("missing" if found < need else "extra")
        rows.append({"label": t["label"], "name": t["name"], "needed": need, "found": found,
                     "status": status, "unsure": unsure.get(t["label"], 0)})
    missing = [r for r in rows if r["status"] == "missing"]
    return {"detections": dets, "rows": rows, "notes": notes, "missing": missing,
            "complete": not missing, "summary": _inventory_summary(rows)}


def _inventory_summary(rows: list[dict]) -> str:
    short = [f"{r['name']} {r['label']}: found {r['found']} of {r['needed']}" for r in rows if r["status"] == "missing"]
    if not short:
        return "Every part is here."
    return "; ".join(short) + ". Ask the seller for the missing parts, or check the box again."


async def show_step_parts(img: Image.Image, plan: dict, order: int, *, vision: Vision, private: bool, ctx: dict) -> dict:
    step = step_by_order(plan, order)
    if step is None:
        raise KeyError(f"No step {order}")
    labels = [u["label"] for u in step.get("parts_used", [])]
    targets = targets_for(plan, labels)
    dets, notes = await vision.locate(img, targets, purpose="step_parts", count_every_instance=True,
                                      private=private, ctx=ctx, mock_args={"step": order})
    return {"detections": dets, "notes": notes}


async def check_step(img: Image.Image, plan: dict, order: int, figure: Optional[Image.Image], *, muse: MuseClient,
                     vision: Vision, settings: Settings, private: bool, attempt: int, ctx: dict,
                     common_mistake: str = "") -> dict:
    """After-photo -> pass, fail or unsure, with what was seen and how to fix it."""
    step = step_by_order(plan, order)
    if step is None:
        raise KeyError(f"No step {order}")

    if private:
        # Frames stay local: confirm the step's parts are visible and placed. Weaker than Spark's judgment,
        # and labeled as such in the result.
        labels = [u["label"] for u in step.get("parts_used", [])]
        dets, _ = await vision.locate(img, targets_for(plan, labels), purpose="step_check_local",
                                      private=True, ctx=ctx, mock_args={"step": order, "attempt": attempt})
        seen = {d["label"] for d in dets if d["confidence"] >= settings.min_confidence}
        absent = [lab for lab in labels if lab not in seen]
        conf = min([d["confidence"] for d in dets] or [0.0])
        verdict = "pass" if not absent else "unsure"
        verdict, coach = coach_fields(None, plan, order, verdict)
        return {
            **coach,
            "verdict": verdict, "confidence": round(conf * 0.8, 2), "method": "local parts check",
            "observed": f"Found {', '.join(sorted(seen)) or 'none'} of the step's parts.",
            "reason": "" if not absent else f"Could not clearly see part(s) {', '.join(absent)}.",
            "fix": "" if not absent else "Retake the photo closer, with the parts in view, or confirm by hand.",
            "box": None, "detections": dets,
        }

    parts_lookup = {p["label"]: p["name"] for p in plan.get("parts", [])}
    images = [img] + ([figure] if figure is not None else [])
    reply = await muse.json(
        role="spark", purpose="step_check", system=prompts.CHECK_SYSTEM,
        text=prompts.check_prompt(step, parts_lookup, figure is not None, plan.get("steps"), common_mistake),
        images=images,
        schema=StepCheckReply, effort=settings.check_effort, ctx=ctx,
        mock_args={"step": order, "attempt": attempt},
    )
    verdict = reply.verdict
    if verdict != "unsure" and reply.confidence < settings.min_confidence:
        verdict = "unsure"
    verdict, coach = coach_fields(reply, plan, order, verdict)
    return {
        **coach,
        "verdict": verdict, "confidence": round(float(reply.confidence), 2), "method": "Muse Spark",
        "observed": reply.observed, "reason": reply.reason, "fix": reply.fix,
        "box": [v / 1000 for v in reply.box] if reply.box else None, "detections": [],
    }


async def live_check(img: Image.Image, plan: dict, order: int, figure: Optional[Image.Image], *, muse: MuseClient,
                     vision: Vision, settings: Settings, private: bool, frame: int, history: list[dict],
                     ctx: dict, common_mistake: str = "") -> dict:
    """One live camera frame -> verdict plus one short spoken instruction for right now."""
    step = step_by_order(plan, order)
    if step is None:
        raise KeyError(f"No step {order}")

    if private:
        result = await check_step(img, plan, order, None, muse=muse, vision=vision, settings=settings,
                                  private=True, attempt=frame, ctx=ctx)
        result["say"] = "Looks complete." if result["verdict"] == "pass" else (result["fix"] or "Hold steady.")
        return result

    parts_lookup = {p["label"]: p["name"] for p in plan.get("parts", [])}
    images = [img] + ([figure] if figure is not None else [])
    reply = await muse.json(
        role="spark", purpose="live_check", system=prompts.LIVE_SYSTEM,
        text=prompts.live_prompt(step, parts_lookup, figure is not None, history, plan.get("steps"), common_mistake),
        images=images,
        schema=StepCheckReply, effort=settings.live_effort, ctx=ctx,
        mock_args={"step": order, "frame": frame},
    )
    verdict = reply.verdict
    if verdict == "pass" and reply.confidence < settings.live_pass_confidence:
        verdict = "unsure"  # live mode needs a confident pass before it moves on
    verdict, coach = coach_fields(reply, plan, order, verdict)
    if verdict == "unsure" and coach["state"] == "done":
        coach["state"] = "in_progress"  # looks done but not certain yet: keep watching
    say = reply.say or (coach["next_steps"][0] if coach["next_steps"] else "") or reply.fix or reply.observed
    return {
        **coach,
        "verdict": verdict, "confidence": round(float(reply.confidence), 2), "method": "Muse Spark, live",
        "observed": reply.observed, "reason": reply.reason, "fix": reply.fix,
        "say": say,
        "box": [v / 1000 for v in reply.box] if reply.box else None, "detections": [],
    }


# ---------------------------------------------------------------- parts ledger


def ledger_plan(plan: dict) -> dict:
    """Which hardware is used up step by step, and how much should be left after each step.

    Only parts that are consumed count (screws, dowels): a label whose uses add up to no more than its
    quantity. Panels that appear in several steps are reused, not consumed, and are left out.
    """
    used: Counter = Counter()
    for st in plan.get("steps", []):
        for u in st.get("parts_used", []):
            used[u["label"]] += int(u.get("quantity") or 1)
    hardware = [p for p in plan.get("parts", [])
                if (p.get("quantity") or 1) >= 2 and 0 < used[p["label"]] <= (p.get("quantity") or 1)]
    labels = {p["label"] for p in hardware}
    left = {p["label"]: int(p.get("quantity") or 1) for p in hardware}
    after = []
    for st in plan.get("steps", []):
        for u in st.get("parts_used", []):
            if u["label"] in labels:
                left[u["label"]] -= int(u.get("quantity") or 1)
        after.append({"order": st["order"], "left": dict(left)})
    return {"hardware": [{"label": p["label"], "name": p["name"], "quantity": int(p.get("quantity") or 1)}
                         for p in hardware], "after": after}


def expected_left(plan: dict, before_order: int) -> dict[str, int]:
    """Hardware that should still be loose when the person is about to start step before_order."""
    ledger = ledger_plan(plan)
    left = {h["label"]: h["quantity"] for h in ledger["hardware"]}
    for row in ledger["after"]:
        if row["order"] < before_order:
            left = row["left"]
    return left


async def ledger_check(img: Image.Image, plan: dict, before_order: int, *, vision: Vision, settings: Settings,
                       private: bool, reference: Optional[Image.Image], ctx: dict) -> dict:
    """Count the loose hardware and compare it with what should be left before this step.

    One too many screws left over means an earlier step is missing one; too few means a part went into
    the wrong place or got lost, and the remaining steps may run short.
    """
    ledger = ledger_plan(plan)
    if not ledger["hardware"]:
        return {"rows": [], "summary": "This manual has no loose hardware to track.", "ok": True, "detections": []}
    expect = expected_left(plan, before_order)
    names = {h["label"]: h["name"] for h in ledger["hardware"]}
    targets = [t for t in targets_for(plan, list(expect)) if expect.get(t["label"], 0) >= 0]
    for t in targets:
        t["quantity"] = expect[t["label"]]
    dets, notes = await vision.locate(img, targets, purpose="parts_ledger", count_every_instance=True,
                                      reference=None if private else reference, private=private, ctx=ctx,
                                      mock_args={"step": before_order})
    found = Counter(d["label"] for d in dets if d["confidence"] >= settings.min_confidence)
    steps = plan.get("steps", [])
    rows, problems = [], []
    for label, want in expect.items():
        have = found[label]
        rest = sum(int(u.get("quantity") or 1) for st in steps if st["order"] >= before_order
                   for u in st.get("parts_used", []) if u["label"] == label)
        row = {"label": label, "name": names[label], "expected": want, "found": have, "needed_from_here": rest,
               "status": "ok", "blame": None, "message": ""}
        if have > want:
            earlier = [st for st in steps if st["order"] < before_order
                       and any(u["label"] == label for u in st.get("parts_used", []))]
            blame = earlier[-1] if earlier else None
            extra = have - want
            row.update(status="extra", blame={"order": blame["order"], "title": blame["title"]} if blame else None,
                       message=(f"{extra} extra {names[label]} {label}. Step {blame['order']} probably has "
                                f"{'one' if extra == 1 else extra} missing: check it before you go on."
                                if blame else f"{extra} extra {names[label]} {label}."))
        elif have < want:
            short = rest - have
            row.update(status="short" if short > 0 else "low",
                       message=(f"{want - have} fewer {names[label]} {label} than expected. "
                                + (f"The steps ahead need {rest}: you're {short} short. Check earlier steps for "
                                   f"one used in the wrong place, or look on the floor."
                                   if short > 0 else "You still have enough for the steps ahead.")))
        if row["status"] in {"extra", "short"}:
            problems.append(row["message"])
        rows.append(row)
    summary = " ".join(problems) if problems else "Hardware adds up. Exactly what should be left before this step."
    return {"rows": rows, "summary": summary, "ok": not problems, "detections": dets, "notes": notes,
            "before_step": before_order}


# ---------------------------------------------------------------- where am I


async def resume(img: Image.Image, plan: dict, reference: Optional[Image.Image], *, muse: MuseClient,
                 settings: Settings, ctx: dict) -> dict:
    """One photo of work in progress -> which steps are done, any done wrong, the step to do now, and what next."""
    orders = [st["order"] for st in plan.get("steps", [])]
    parts_lookup = {p["label"]: p["name"] for p in plan.get("parts", [])}
    reply = await muse.json(
        role="spark", purpose="where_am_i", system=prompts.RESUME_SYSTEM,
        text=prompts.resume_prompt(plan, parts_lookup, reference is not None),
        images=[img] + ([reference] if reference is not None else []), schema=ResumeReply,
        effort=settings.check_effort, ctx=ctx, mock_args={"plan": plan},
    )
    valid = set(orders)
    wrong = [w for w in reply.wrong if w.step in valid]
    wrong_steps = {w.step for w in wrong}
    sure = reply.confidence >= settings.min_confidence
    done = [o for o in reply.done_steps if o in valid and o not in wrong_steps] if sure else []
    current = reply.current_step if reply.current_step in valid else None
    if current is None and sure:
        current = next((o for o in orders if o not in done), None)
    if wrong:  # a mistake in an earlier step comes first: later steps build on it
        current = min(wrong_steps)
    step = next((st for st in plan["steps"] if st["order"] == current), None)
    return {
        "observed": reply.observed, "confidence": round(float(reply.confidence), 2), "sure": sure,
        "done_steps": done, "current_step": current,
        "current_title": step["title"] if step else None,
        "all_done": sure and not wrong and current is None and set(done) == valid,
        "wrong": [{"step": w.step, "title": next(st["title"] for st in plan["steps"] if st["order"] == w.step),
                   "observed": w.observed, "fix": w.fix, "box": [v / 1000 for v in w.box] if w.box else None}
                  for w in wrong],
        "done_items": reply.done_items, "next_steps": reply.next_steps[:3],
        "say": reply.say, "box": [v / 1000 for v in reply.box] if reply.box else None,
    }

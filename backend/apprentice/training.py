"""Hands-on training flows that share one engine: parts inventory, show a step's parts, check a step."""

from __future__ import annotations

from collections import Counter
from typing import Optional

from PIL import Image

from . import prompts
from .config import Settings
from .muse import MuseClient
from .schemas import StepCheckReply
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
                     vision: Vision, settings: Settings, private: bool, attempt: int, ctx: dict) -> dict:
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
        return {
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
        text=prompts.check_prompt(step, parts_lookup, figure is not None), images=images,
        schema=StepCheckReply, effort=settings.check_effort, ctx=ctx,
        mock_args={"step": order, "attempt": attempt},
    )
    verdict = reply.verdict
    if verdict != "unsure" and reply.confidence < settings.min_confidence:
        verdict = "unsure"
    return {
        "verdict": verdict, "confidence": round(float(reply.confidence), 2), "method": "Muse Spark",
        "observed": reply.observed, "reason": reply.reason, "fix": reply.fix,
        "box": [v / 1000 for v in reply.box] if reply.box else None, "detections": [],
    }


async def live_check(img: Image.Image, plan: dict, order: int, figure: Optional[Image.Image], *, muse: MuseClient,
                     vision: Vision, settings: Settings, private: bool, frame: int, history: list[dict],
                     ctx: dict) -> dict:
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
        text=prompts.live_prompt(step, parts_lookup, figure is not None, history), images=images,
        schema=StepCheckReply, effort=settings.live_effort, ctx=ctx,
        mock_args={"step": order, "frame": frame},
    )
    verdict = reply.verdict
    if verdict == "pass" and reply.confidence < settings.live_pass_confidence:
        verdict = "unsure"  # live mode needs a confident pass before it moves on
    return {
        "verdict": verdict, "confidence": round(float(reply.confidence), 2), "method": "Muse Spark, live",
        "observed": reply.observed, "reason": reply.reason, "fix": reply.fix,
        "say": reply.say or reply.fix or reply.observed,
        "box": [v / 1000 for v in reply.box] if reply.box else None, "detections": [],
    }

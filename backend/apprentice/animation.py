"""3D animations of a manual step: Muse Wonder writes a scene of simple shapes and a timeline, the
phone's 3D viewer plays it. Each step is generated once and cached; a simple built-in animation
is used if the model's answer is unusable, so the 3D tab always shows something.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

from PIL import Image

from . import prompts
from .config import Settings
from .muse import MuseClient, MuseError
from .schemas import Animation3D
from .store import Store
from .training import step_by_order

MAX_INSTANCES = 40


def cache_path(store: Store, manual_id: str, order: int) -> Path:
    return store.path("manuals", manual_id, "anim", f"step_{order:02d}.json")


def _usable(anim: Animation3D) -> bool:
    ids = {p.id for p in anim.parts}
    inst = [i for i in anim.instances if i.part in ids]
    return bool(inst) and any(a.targets for a in anim.actions)


def _clean(anim: Animation3D) -> dict:
    """Drop references to unknown parts and instances, keep limits sane, sort the timeline."""
    data = anim.model_dump()
    part_ids = {p["id"] for p in data["parts"]}
    instances = [i for i in data["instances"] if i["part"] in part_ids]
    # Keep every part that moves in this step; trim only already-fitted context if the scene is huge.
    targeted = {t for a in data["actions"] for t in a["targets"]}
    moving = [i for i in instances if i["id"] in targeted]
    still = [i for i in instances if i["id"] not in targeted]
    data["instances"] = moving + still[: max(0, MAX_INSTANCES - len(moving))]
    inst_ids = {i["id"] for i in data["instances"]}
    actions = []
    for a in data["actions"][:10]:
        a["targets"] = [t for t in a["targets"] if t in inst_ids]
        if a["targets"]:
            a["duration"] = max(0.4, min(8.0, a["duration"] or 1.5))
            actions.append(a)
    data["actions"] = sorted(actions, key=lambda a: a["at"])
    return data


def fallback(step: dict, plan: dict) -> dict:
    """A plain but correct animation: each part used in the step moves to the centre in turn."""
    names = {p["label"]: p for p in plan.get("parts", [])}
    parts, instances, actions = [], [], []
    uses = step.get("parts_used", [])[:6]
    t = 0.0
    for i, use in enumerate(uses):
        info = names.get(use["label"], {"name": use["label"]})
        lname = info.get("name", "").lower()
        screwish = any(w in lname for w in ("screw", "bolt", "nail", "pin", "dowel"))
        tool = any(w in lname for w in ("wrench", "key", "screwdriver", "spanner"))
        shape = "screw" if screwish else ("lkey" if tool else "box")
        size = [0.6, 4] if screwish else ([12] if tool else [24, 4 + 3 * (i % 3), 10])
        pid = f"p{i}"
        parts.append({"id": pid, "label": use["label"], "name": info.get("name", ""), "shape": shape, "size": size,
                      "material": "metal" if (screwish or tool) else "wood"})
        angle = 2 * math.pi * i / max(len(uses), 1)
        start = [round(40 * math.cos(angle), 1), 2, round(40 * math.sin(angle), 1)]
        end = [0, 6 + 6 * i, 0]
        instances.append({"id": f"i{i}", "part": pid, "start": {"pos": start}, "end": {"pos": end}})
        verb = "Drive in" if screwish else ("Use" if tool else "Bring in")
        actions.append({"targets": [f"i{i}"], "type": "screw" if screwish else "move", "at": t, "duration": 2.0,
                        "say": f"{verb} {info.get('name', use['label']).lower()} {use['label']}."[:80]})
        t += 2.5
    if not instances:
        parts = [{"id": "p0", "label": "", "name": step["title"], "shape": "box", "size": [20, 10, 20]}]
        instances = [{"id": "i0", "part": "p0", "start": {"pos": [0, 5, 0]}}]
        actions = [{"targets": ["i0"], "type": "highlight", "at": 0, "duration": 2, "say": step["title"]}]
    anim = Animation3D.model_validate({"title": step["title"], "parts": parts, "instances": instances,
                                       "actions": actions, "notes": "simple preview"})
    return _clean(anim)


async def step_animation(store: Store, muse: MuseClient, settings: Settings, manual_id: str, plan: dict, order: int,
                         figure: Optional[Image.Image], *, refresh: bool = False) -> dict:
    step = step_by_order(plan, order)
    if step is None:
        raise KeyError(f"No step {order}")
    path = cache_path(store, manual_id, order)
    if path.exists() and not refresh:
        return {**json.loads(path.read_text(encoding="utf-8")), "cached": True}

    names = {p["label"]: p for p in plan.get("parts", [])}
    parts = [{**names.get(u["label"], {"label": u["label"], "name": u["label"]}), "used": u["quantity"]}
             for u in step.get("parts_used", [])]
    earlier = [s["title"] for s in plan.get("steps", []) if s["order"] < order]
    source, error = "Muse Wonder", None
    try:
        anim = await muse.json(
            role="wonder", purpose="step_animation", system=prompts.ANIM_SYSTEM,
            text=prompts.anim_prompt(step, parts, earlier, figure is not None),
            images=[figure] if figure is not None else [], schema=Animation3D, effort=settings.plan_effort,
            ctx={"manual_id": manual_id, "step": order},
            mock_args={"step": order, "plan": plan},
        )
        data = _clean(anim) if _usable(anim) else None
    except MuseError as exc:
        data, error = None, str(exc)[:200]
    if data is None:
        data, source = fallback(step, plan), "simple preview"
    result = {"animation": data, "source": source, "error": error, "step": order}
    if source == "Muse Wonder":  # don't cache fallbacks, so a later try can still get the real one
        path.write_text(json.dumps(result), encoding="utf-8")
    return {**result, "cached": False}

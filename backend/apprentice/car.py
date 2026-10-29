"""Car fixes: label the engine bay from the owner's manual, and read dashboard warning lights.

Scope is owner-level checks only. Brakes, airbags, fuel systems and high-voltage EV parts are out.
"""

from __future__ import annotations

from typing import Optional

from PIL import Image

from . import prompts
from .config import Settings
from .muse import MuseClient
from .schemas import WarningLightReply
from .training import targets_for
from .vision import PrivacyError, Vision

# Used when no owner's manual has been loaded. Names match what most manuals call these parts.
DEFAULT_ENGINE_BAY = [
    {"label": "1", "name": "Engine oil dipstick", "prompt": "engine oil dipstick handle, a yellow or orange loop"},
    {"label": "2", "name": "Oil filler cap", "prompt": "engine oil filler cap with an oil can symbol"},
    {"label": "3", "name": "Coolant reservoir", "prompt": "translucent coolant reservoir tank with MIN and MAX marks"},
    {"label": "4", "name": "Brake fluid reservoir", "prompt": "small brake fluid reservoir with a yellow or black cap"},
    {"label": "5", "name": "Washer fluid reservoir", "prompt": "windshield washer fluid cap with a spray symbol, often blue"},
    {"label": "6", "name": "Battery", "prompt": "car battery, black box with terminals"},
    {"label": "7", "name": "Fuse box", "prompt": "black plastic fuse box cover"},
    {"label": "8", "name": "Air filter housing", "prompt": "black plastic air filter box with clips"},
]

SAFETY = [
    "Park on level ground, engine off, and let the engine cool before opening any cap.",
    "Never open the coolant cap when the engine is hot.",
    "For brakes, airbags, fuel or high-voltage parts, see a qualified mechanic.",
]


async def engine_bay(img: Image.Image, plan: Optional[dict], *, vision: Vision, private: bool, ctx: dict) -> dict:
    targets = targets_for(plan) if plan and plan.get("parts") else DEFAULT_ENGINE_BAY
    pages = {p["label"]: p.get("page") for p in (plan or {}).get("parts", [])}
    dets, notes = await vision.locate(img, targets, purpose="engine_bay", private=private, ctx=ctx)
    for d in dets:
        d["page"] = pages.get(d["label"])
    found = {d["label"] for d in dets}
    return {"detections": dets, "notes": notes, "safety": SAFETY,
            "not_found": [t["name"] for t in targets if t["label"] not in found]}


async def warning_lights(img: Image.Image, plan: Optional[dict], *, muse: MuseClient, settings: Settings,
                         private: bool, ctx: dict) -> dict:
    if private and not settings.mock:
        raise PrivacyError("Reading warning lights uses Muse Spark in the cloud. Switch to Cloud mode for this check.")
    manual_lights = (plan or {}).get("warning_lights", [])
    reply = await muse.json(
        role="spark", purpose="warning_lights", system=prompts.WARNING_SYSTEM,
        text=prompts.warning_prompt(manual_lights), images=[img], schema=WarningLightReply,
        effort=settings.check_effort, ctx=ctx,
    )
    return reply.model_dump()

"""Learn any concept: the objects in the learner's room become the lesson's cast."""

from __future__ import annotations

from PIL import Image

from . import prompts
from .config import Settings
from .muse import MuseClient
from .schemas import LessonReply, ReviewReply, RoomReply
from .vision import Vision

# Used in hybrid mode, where frames stay local and SAM 3.1 needs names to look for.
COMMON_PROPS = ["lamp", "cup", "mug", "bottle", "plant", "book", "ball", "fruit", "bowl", "glass",
                "phone", "laptop", "box", "pen", "chair", "coin", "tap", "candle"]


async def room_objects(img: Image.Image, *, muse: MuseClient, vision: Vision, settings: Settings,
                       private: bool, ctx: dict) -> list[dict]:
    if private:
        targets = [{"label": f"o{i + 1}", "name": name, "prompt": name} for i, name in enumerate(COMMON_PROPS)]
        dets, _ = await vision.locate(img, targets, purpose="room_objects_local", private=True, ctx=ctx)
        return [{"id": d["label"], "name": d["name"], "description": "", "box": d["box"]} for d in dets]
    reply = await muse.json(
        role="spark", purpose="room_objects", system=prompts.ROOM_SYSTEM, text=prompts.room_prompt(),
        images=[img], schema=RoomReply, effort=settings.check_effort, ctx=ctx,
    )
    return [{"id": o.id, "name": o.name, "description": o.description, "box": [v / 1000 for v in o.box]}
            for o in reply.objects if o.box]


async def build_lesson(concept: str, img: Image.Image, *, muse: MuseClient, vision: Vision, settings: Settings,
                       private: bool, ctx: dict) -> dict:
    objects = await room_objects(img, muse=muse, vision=vision, settings=settings, private=private, ctx=ctx)
    if not objects:
        return {"error": "No usable objects were found. Take a photo with two or three everyday objects in view.",
                "objects": []}
    # Only object names and ids go to Wonder: the photo itself is not sent for lesson writing.
    brief = [{"id": o["id"], "name": o["name"], "description": o["description"]} for o in objects]
    lesson = await muse.json(
        role="wonder", purpose="lesson_script", system=prompts.LESSON_SYSTEM,
        text=prompts.lesson_prompt(concept, brief), schema=LessonReply, effort=settings.plan_effort, ctx=ctx,
        mock_args={"concept": concept, "objects": brief},
    )
    review = await muse.json(
        role="wonder", purpose="science_review", system=prompts.REVIEW_SYSTEM,
        text=prompts.review_prompt(lesson.model_dump()), schema=ReviewReply, effort=settings.check_effort, ctx=ctx,
    )
    final = review.fixed_lesson if (not review.approved and review.fixed_lesson) else lesson
    data = final.model_dump()
    _drop_unknown_targets(data, {o["id"] for o in objects})
    return {"lesson": data, "objects": objects,
            "review": {"approved": review.approved, "issues": review.issues, "corrected": final is not lesson}}


def _drop_unknown_targets(lesson: dict, ids: set[str]) -> None:
    lesson["cast"] = [c for c in lesson.get("cast", []) if c["object_id"] in ids]
    beats = []
    for b in lesson.get("beats", []):
        b["visual"]["targets"] = [t for t in b["visual"].get("targets", []) if t in ids]
        if b["visual"]["type"] in {"arrow", "orbit", "flow", "compare"} and len(b["visual"]["targets"]) < 2:
            b["visual"]["type"] = "highlight"
        beats.append(b)
    lesson["beats"] = beats
    if lesson.get("quiz") and lesson["quiz"]["answer_object_id"] not in ids:
        lesson["quiz"] = None

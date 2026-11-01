"""Shapes of every model reply. Replies are validated here, so the app never parses free text.

Boxes from models use integers 0..1000 as [x0, y0, x1, y1] relative to the image sent.
The app converts them to 0..1 before drawing.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean_box(value):
    if value is None:
        return None
    if isinstance(value, dict):  # {"x0":..} or {"xmin":..}
        keys = [("x0", "y0", "x1", "y1"), ("xmin", "ymin", "xmax", "ymax"), ("left", "top", "right", "bottom")]
        for k in keys:
            if all(key in value for key in k):
                value = [value[key] for key in k]
                break
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if max(x0, y0, x1, y1) <= 1.0:  # model answered in 0..1; scale up
        x0, y0, x1, y1 = x0 * 1000, y0 * 1000, x1 * 1000, y1 * 1000
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    clamp = lambda v: max(0.0, min(1000.0, v))  # noqa: E731
    x0, y0, x1, y1 = clamp(x0), clamp(y0), clamp(x1), clamp(y1)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return [x0, y0, x1, y1]


class Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Boxed(Lenient):
    box: Optional[list[float]] = None

    @field_validator("box", mode="before")
    @classmethod
    def _box(cls, v):
        return _clean_box(v)


# ---------- manual reading ----------


class PagePart(Lenient):
    label: str = ""
    name: str = ""
    quantity: int = 1
    description: str = ""
    drawing_box: Optional[list[float]] = None

    @field_validator("drawing_box", mode="before")
    @classmethod
    def _box(cls, v):
        return _clean_box(v)

    @field_validator("quantity", mode="before")
    @classmethod
    def _qty(cls, v):
        if isinstance(v, str):
            digits = "".join(ch for ch in v if ch.isdigit())
            return int(digits) if digits else 1
        return v or 1


class PageStep(Lenient):
    number: Optional[int] = None
    text: str = ""
    parts_used: list[str] = Field(default_factory=list)
    figure_box: Optional[list[float]] = None

    @field_validator("figure_box", mode="before")
    @classmethod
    def _box(cls, v):
        return _clean_box(v)

    @field_validator("parts_used", mode="before")
    @classmethod
    def _parts(cls, v):
        if isinstance(v, str):
            return [v]
        return [str(x) if not isinstance(x, dict) else f"{x.get('quantity', '')}x {x.get('label', '')}".strip() for x in (v or [])]


class WarningLightInfo(Lenient):
    name: str
    meaning: str = ""
    severity: Literal["info", "caution", "stop"] = "caution"
    action: str = ""
    page: Optional[int] = None


class PageExtraction(Lenient):
    page: int = 0
    kind: str = "other"
    parts: list[PagePart] = Field(default_factory=list)
    steps: list[PageStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    warning_lights: list[WarningLightInfo] = Field(default_factory=list)
    notes: str = ""


class PlanPart(Lenient):
    label: str
    name: str
    quantity: int = 1
    description: str = ""
    sam_prompt: str = ""
    page: Optional[int] = None
    drawing_box: Optional[list[float]] = None

    @field_validator("drawing_box", mode="before")
    @classmethod
    def _box(cls, v):
        return _clean_box(v)


class PartUse(Lenient):
    label: str
    quantity: int = 1


class PlanStep(Lenient):
    order: int
    title: str
    instruction: str
    parts_used: list[PartUse] = Field(default_factory=list)
    done_when: str
    warning: str = ""
    why: str = ""
    page: Optional[int] = None
    figure_box: Optional[list[float]] = None

    @field_validator("figure_box", mode="before")
    @classmethod
    def _box(cls, v):
        return _clean_box(v)

    @field_validator("parts_used", mode="before")
    @classmethod
    def _uses(cls, v):
        out = []
        for item in v or []:
            if isinstance(item, str):
                out.append({"label": item, "quantity": 1})
            else:
                out.append(item)
        return out


def _str_list(v, limit: int = 12) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"\n+", v)
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, dict):
            x = x.get("question") or x.get("title") or x.get("text") or ""
        x = re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", str(x)).strip()
        if x:
            out.append(x)
    return out[:limit]


class Plan(Lenient):
    product: str
    summary: str = ""
    parts: list[PlanPart] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    safety: list[str] = Field(default_factory=list)
    warning_lights: list[WarningLightInfo] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)  # machines and devices: "How do I ..." questions the manual answers

    @field_validator("tasks", mode="before")
    @classmethod
    def _tasks(cls, v):
        return _str_list(v, 8)


# ---------- crowd heads-up and device questions ----------


class HeadsUpReply(Lenient):
    tip: str = ""  # one short spoken warning for the next person
    look_for: str = ""  # what the camera should watch for


class TaskStep(Lenient):
    order: int = 0
    title: str = ""
    instruction: str = ""
    done_when: str = ""
    parts_used: list[PartUse] = Field(default_factory=list)
    warning: str = ""
    why: str = ""
    page: Optional[int] = None

    @field_validator("parts_used", mode="before")
    @classmethod
    def _uses(cls, v):
        out = []
        for item in v or []:
            if isinstance(item, (str, int)):
                out.append({"label": str(item), "quantity": 1})
            elif isinstance(item, dict):
                out.append({"label": str(item.get("label", "")), "quantity": item.get("quantity") or 1})
        return [u for u in out if u["label"]]

    @field_validator("page", mode="before")
    @classmethod
    def _page(cls, v):
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None


class TaskAnswer(Lenient):
    answer: str = ""  # two or three sentences, spoken
    in_manual: bool = True  # False when the manual does not cover the question
    pages: list[int] = Field(default_factory=list)
    steps: list[TaskStep] = Field(default_factory=list)
    safety: list[str] = Field(default_factory=list)

    @field_validator("pages", mode="before")
    @classmethod
    def _pages(cls, v):
        out = []
        for x in v if isinstance(v, list) else [v]:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
        return out[:12]

    @field_validator("safety", mode="before")
    @classmethod
    def _safety(cls, v):
        return _str_list(v, 6)


# ---------- teaching a robot ----------

class RobotAction(Lenient):
    skill: str = "place"  # pick | place | insert | align | screw | tighten | press | rotate | hold | connect | inspect
    object: str = ""  # part label and name, e.g. "B side panel"
    target: str = ""  # where it goes, e.g. "dowels on panel A"
    tool: str = ""
    detail: str = ""  # orientation, force, count: "holes facing inward", "hand-tight"

    @field_validator("skill", mode="before")
    @classmethod
    def _skill(cls, v):
        return str(v or "place").strip().lower().split()[0] if str(v or "").strip() else "place"

    @field_validator("object", "target", "tool", "detail", mode="before")
    @classmethod
    def _text(cls, v):
        return "" if v is None else str(v)[:160]


class RobotStep(Lenient):
    order: int = 0
    actions: list[RobotAction] = Field(default_factory=list)
    success_check: str = ""  # what a camera must see when the step is done
    needs_human: bool = False  # a step a robot shouldn't do alone
    human_reason: str = ""
    risks: list[str] = Field(default_factory=list)

    @field_validator("risks", mode="before")
    @classmethod
    def _risks(cls, v):
        return _str_list(v, 4)


class RobotPlan(Lenient):
    steps: list[RobotStep] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("tools", mode="before")
    @classmethod
    def _tools(cls, v):
        return _str_list(v, 8)


# ---------- seeing the real world ----------


class Detection(Boxed):
    label: str
    confidence: float = 0.5


class DetectionReply(Lenient):
    detections: list[Detection] = Field(default_factory=list)
    notes: str = ""


class StepCheckReply(Boxed):
    verdict: Literal["pass", "fail", "unsure"]
    confidence: float = 0.5
    observed: str = ""
    reason: str = ""
    fix: str = ""
    say: str = ""  # live mode: one short instruction to speak right now
    # Where the work is right now, so a half-done or mid-fix step gets "next steps from here".
    state: str = ""  # not_started | in_progress | fixing | wrong | done | cant_see
    progress: Optional[float] = None  # 0..1 share of the step that is done
    done_items: list[str] = Field(default_factory=list)  # what is already right
    next_steps: list[str] = Field(default_factory=list)  # 1-3 actions, in order, starting from now
    seen_step: Optional[int] = None  # a different step the frame clearly shows (ahead or behind)

    @field_validator("state", mode="before")
    @classmethod
    def _state(cls, v):
        s = re.sub(r"[\s-]+", "_", str(v or "").strip().lower())
        aliases = {"complete": "done", "completed": "done", "finished": "done", "partial": "in_progress",
                   "partly_done": "in_progress", "started": "in_progress", "mistake": "wrong", "error": "wrong",
                   "incorrect": "wrong", "correcting": "fixing", "undoing": "fixing", "unclear": "cant_see",
                   "unsure": "cant_see", "not_visible": "cant_see", "cannot_see": "cant_see"}
        s = aliases.get(s, s)
        return s if s in {"not_started", "in_progress", "fixing", "wrong", "done", "cant_see"} else ""

    @field_validator("progress", mode="before")
    @classmethod
    def _progress(cls, v):
        try:
            p = float(str(v).rstrip("%")) if v is not None else None
        except (TypeError, ValueError):
            return None
        if p is None:
            return None
        if p > 1:
            p /= 100
        return max(0.0, min(1.0, p))

    @field_validator("done_items", "next_steps", mode="before")
    @classmethod
    def _lines(cls, v):
        if isinstance(v, str):
            v = [x for x in re.split(r"\n|;\s*", v)]
        if not isinstance(v, list):
            return []
        out = [re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", str(x)).strip() for x in v if x]
        return [x for x in out if x][:5]

    @field_validator("seen_step", mode="before")
    @classmethod
    def _seen(cls, v):
        try:
            return int(v) if v not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            return None


class RoomObject(Boxed):
    id: str
    name: str
    description: str = ""


class RoomReply(Lenient):
    objects: list[RoomObject] = Field(default_factory=list)


class Visual(Lenient):
    type: Literal["highlight", "label", "arrow", "orbit", "flow", "stack", "compare"] = "highlight"
    targets: list[str] = Field(default_factory=list)
    text: str = ""
    values: list[float] = Field(default_factory=list)


class Beat(Lenient):
    say: str
    visual: Visual = Field(default_factory=Visual)


class CastRole(Lenient):
    object_id: str
    role: str
    why: str = ""


class Quiz(Lenient):
    question: str
    answer_object_id: str
    explanation: str = ""


class LessonReply(Lenient):
    concept: str
    title: str
    cast: list[CastRole] = Field(default_factory=list)
    beats: list[Beat] = Field(default_factory=list)
    quiz: Optional[Quiz] = None
    analogy_limits: str = ""


class ReviewReply(Lenient):
    approved: bool
    issues: list[str] = Field(default_factory=list)
    fixed_lesson: Optional[LessonReply] = None


class WarningLightReading(Lenient):
    name: str
    color: str = ""
    meaning: str = ""
    severity: Literal["info", "caution", "stop"] = "caution"
    action: str = ""
    manual_page: Optional[int] = None


class WarningLightReply(Lenient):
    lights: list[WarningLightReading] = Field(default_factory=list)
    notes: str = ""


# ---------- 3D step animation (units: cm, Y up, floor at y = 0)

SHAPES = ("box", "cylinder", "tube", "sphere", "screw", "wheel", "lkey", "cone")


def _vec3(value, default, lo=-500.0, hi=500.0):
    if not isinstance(value, (list, tuple)):
        return list(default)
    out = []
    for i in range(3):
        try:
            v = float(value[i])
        except (IndexError, TypeError, ValueError):
            v = default[i]
        out.append(max(lo, min(hi, v)))
    return out


class Pose3D(Lenient):
    pos: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rot: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])  # degrees, XYZ

    @field_validator("pos", mode="before")
    @classmethod
    def _pos(cls, v):
        return _vec3(v, (0.0, 0.0, 0.0))

    @field_validator("rot", mode="before")
    @classmethod
    def _rot(cls, v):
        return _vec3(v, (0.0, 0.0, 0.0), -720.0, 720.0)


class Part3D(Lenient):
    id: str
    label: str = ""
    name: str = ""
    shape: str = "box"
    size: list[float] = Field(default_factory=lambda: [10.0, 10.0, 10.0])
    color: str = ""
    material: Literal["wood", "metal", "plastic", "fabric", "rubber"] = "plastic"

    @field_validator("shape", mode="before")
    @classmethod
    def _shape(cls, v):
        v = str(v or "box").lower()
        return v if v in SHAPES else "box"

    @field_validator("size", mode="before")
    @classmethod
    def _size(cls, v):
        if not isinstance(v, (list, tuple)) or not v:
            return [10.0, 10.0, 10.0]
        out = []
        for x in list(v)[:3]:
            try:
                out.append(max(0.1, min(300.0, float(x))))
            except (TypeError, ValueError):
                out.append(10.0)
        return out

    @field_validator("material", mode="before")
    @classmethod
    def _mat(cls, v):
        v = str(v or "plastic").lower()
        return v if v in ("wood", "metal", "plastic", "fabric", "rubber") else "plastic"

    @field_validator("color", mode="before")
    @classmethod
    def _color(cls, v):
        v = str(v or "").strip()
        return v if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else ""


class Instance3D(Lenient):
    id: str
    part: str
    start: Pose3D = Field(default_factory=Pose3D)
    end: Optional[Pose3D] = None  # None: the part stays where it starts (already fitted)
    repeat: int = 1  # identical copies spaced evenly around the Y axis through x = 0, z = 0

    @field_validator("repeat", mode="before")
    @classmethod
    def _repeat(cls, v):
        try:
            return max(1, min(12, int(v)))
        except (TypeError, ValueError):
            return 1


class Action3D(Lenient):
    targets: list[str] = Field(default_factory=list)
    type: Literal["move", "screw", "appear", "highlight", "rotate"] = "move"
    at: float = 0.0
    duration: float = 1.5
    stagger: float = 0.25
    turns: float = 4.0
    say: str = ""

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v):
        v = str(v or "move").lower()
        return v if v in ("move", "screw", "appear", "highlight", "rotate") else "move"

    @field_validator("at", "duration", "stagger", "turns", mode="before")
    @classmethod
    def _num(cls, v):
        try:
            return max(0.0, min(60.0, float(v)))
        except (TypeError, ValueError):
            return 0.0

    @field_validator("targets", mode="before")
    @classmethod
    def _targets(cls, v):
        return [v] if isinstance(v, str) else list(v or [])


class Camera3D(Lenient):
    pos: list[float] = Field(default_factory=lambda: [60.0, 55.0, 80.0])
    target: list[float] = Field(default_factory=lambda: [0.0, 15.0, 0.0])

    @field_validator("pos", mode="before")
    @classmethod
    def _pos(cls, v):
        return _vec3(v, (60.0, 55.0, 80.0))

    @field_validator("target", mode="before")
    @classmethod
    def _target(cls, v):
        return _vec3(v, (0.0, 15.0, 0.0))


class Animation3D(Lenient):
    title: str = ""
    parts: list[Part3D] = Field(default_factory=list)
    instances: list[Instance3D] = Field(default_factory=list)
    actions: list[Action3D] = Field(default_factory=list)
    camera: Camera3D = Field(default_factory=Camera3D)
    notes: str = ""


# ---------- store links ----------


class ListingInfo(Lenient):
    is_product: bool = True
    product: str = ""  # a clean name: brand + product + model, no marketing words
    brand: str = ""
    model: str = ""
    kind: Literal["assembly", "device", "training"] = "device"
    category: str = ""
    summary: str = ""
    in_the_box: list[str] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        v = str(v or "").lower()
        if v in {"furniture", "assemble", "flat-pack", "flatpack"}:
            return "assembly"
        if v in {"machine", "equipment", "tool"}:
            return "training"
        return v if v in {"assembly", "device", "training"} else "device"

    @field_validator("in_the_box", mode="before")
    @classmethod
    def _box(cls, v):
        return _str_list(v, 20)


class ListingPlan(Plan):
    needs_manual: bool = False  # True when exact steps need the official instructions (all flat-pack furniture)
    note: str = ""  # one sentence on what this guide can't cover without the manual


# ---------- where am I: one photo of work in progress against the whole procedure ----------


class StepIssue(Boxed):
    step: int = 0
    observed: str = ""
    fix: str = ""


class ResumeReply(Boxed):
    observed: str = ""
    current_step: Optional[int] = None  # the step the person should do now (None: can't tell)
    done_steps: list[int] = Field(default_factory=list)  # steps visibly finished and right
    wrong: list[StepIssue] = Field(default_factory=list)  # finished steps done incorrectly
    done_items: list[str] = Field(default_factory=list)  # what's already right within the current step
    next_steps: list[str] = Field(default_factory=list)  # 1-3 actions from exactly here
    say: str = ""
    confidence: float = 0.5

    @field_validator("done_steps", mode="before")
    @classmethod
    def _steps(cls, v):
        out = []
        for x in v if isinstance(v, list) else [v]:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
        return sorted(set(out))

    @field_validator("current_step", mode="before")
    @classmethod
    def _cur(cls, v):
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @field_validator("done_items", "next_steps", mode="before")
    @classmethod
    def _lines(cls, v):
        return _str_list(v, 5)

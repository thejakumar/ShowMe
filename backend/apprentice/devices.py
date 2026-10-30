"""Learning a machine or an electronic device from its manual.

- tour(): one photo of the device -> every control the manual names, labeled on the photo with what it does.
- ask(): "How do I descale it?" -> an answer from the manual, plus steps that become a guided, camera-checked
  task. Each task is stored as its own small manual (use_case "task") so the normal step flow, live check,
  passport and brand dashboard all work on it unchanged. The same question is answered once and reused.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import pymupdf
from PIL import Image

from . import prompts
from .config import Settings
from .muse import MuseClient
from .schemas import TaskAnswer
from .store import Store
from .vision import Vision


def controls(plan: dict) -> list[dict]:
    return [{"label": p["label"], "name": p["name"], "description": p.get("description", ""),
             "prompt": p.get("sam_prompt") or p["name"], "quantity": 1} for p in plan.get("parts", [])]


async def tour(img: Image.Image, plan: dict, *, vision: Vision, private: bool, ctx: dict) -> dict:
    targets = controls(plan)
    dets, notes = await vision.locate(img, targets, purpose="controls_tour", count_every_instance=False,
                                      private=private, ctx=ctx)
    seen = {d["label"] for d in dets}
    return {"detections": dets, "notes": notes,
            "controls": [{**c, "found": c["label"] in seen} for c in targets]}


def _norm(q: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", q.lower()).strip()


def manual_text(store: Store, manual_id: str, plan: dict) -> str:
    """What the model reads to answer: the per-page readings saved at ingest, else the PDF's own text."""
    saved = store.path("manuals", manual_id, "extractions.json")
    if saved.exists():
        try:
            return json.dumps(json.loads(saved.read_text()), ensure_ascii=False)
        except (OSError, ValueError):
            pass
    pdf = store.path("manuals", manual_id, "source.pdf")
    chunks = []
    if pdf.exists():
        with pymupdf.open(pdf) as doc:
            for i, page in enumerate(doc):
                text = (page.get_text("text") or "").strip()
                if text:
                    chunks.append(f"[page {i + 1}]\n{text}")
    plan_bits = {k: plan.get(k) for k in ("product", "summary", "steps", "safety", "warning_lights")}
    return "\n\n".join(chunks) + "\n\n[plan]\n" + json.dumps(plan_bits, ensure_ascii=False)


def find_task(store: Store, parent_id: str, question: str) -> Optional[dict]:
    want = _norm(question)
    for m in store.manuals():
        if m["use_case"] != "task" or m["status"] != "ready":
            continue
        child = store.manual(m["id"])
        plan = child.get("plan") or {}
        if plan.get("parent_manual_id") == parent_id and _norm(plan.get("question", "")) == want:
            return child
    return None


async def ask(question: str, parent: dict, *, store: Store, muse: MuseClient, settings: Settings) -> dict:
    """Answer from the manual; when there is something to do, make it a guided task."""
    plan = parent["plan"]
    question = question.strip().rstrip("?") + "?"
    cached = find_task(store, parent["id"], question)
    if cached:
        return _reply(cached, cached=True)

    reply = await muse.json(
        role="wonder", purpose="task_answer", system=prompts.ASK_SYSTEM,
        text=prompts.ask_prompt(question, plan.get("product", parent["title"]), controls(plan),
                                manual_text(store, parent["id"], plan)),
        schema=TaskAnswer, effort=settings.check_effort, ctx={"manual_id": parent["id"]},
        mock_args={"question": question, "plan": plan},
    )
    labels = {p["label"] for p in plan.get("parts", [])}
    pages = {p["page"]: p["url"] for p in plan.get("pages", [])}
    steps = []
    for i, st in enumerate(s for s in reply.steps if s.instruction.strip()):
        steps.append({
            "order": i + 1,
            "title": st.title or st.instruction[:60],
            "instruction": st.instruction,
            "done_when": st.done_when or "The step is visibly complete",
            "parts_used": [u.model_dump() for u in st.parts_used if u.label in labels],
            "warning": st.warning, "why": st.why, "page": st.page,
            "figure_url": pages.get(st.page),  # the manual page this step comes from
        })
    used = {u["label"] for st in steps for u in st["parts_used"]}
    child_plan = {
        "product": plan.get("product", parent["title"]),
        "summary": reply.answer,
        "question": question,
        "in_manual": bool(reply.in_manual and (steps or reply.answer)),
        "answer_pages": reply.pages,
        "parts": [p for p in plan.get("parts", []) if p["label"] in used],
        "steps": steps,
        "safety": reply.safety,
        "warning_lights": [],
        "tasks": [],
        "use_case": "task",
        "parent_manual_id": parent["id"],
        "pages": plan.get("pages", []),
        "reference_url": plan.get("reference_url"),
    }
    mid = store.create_manual(question[:120], "task")
    store.update_manual(mid, plan=child_plan, status="ready", progress="Ready", page_count=parent.get("page_count", 0))
    return _reply(store.manual(mid), cached=False)


def _reply(child: dict, cached: bool) -> dict:
    plan = child["plan"]
    return {"manual_id": child["id"], "question": plan.get("question"), "answer": plan.get("summary", ""),
            "in_manual": plan.get("in_manual", True), "pages": plan.get("answer_pages", []),
            "steps": [{"order": s["order"], "title": s["title"], "instruction": s["instruction"]} for s in plan["steps"]],
            "safety": plan.get("safety", []), "cached": cached}

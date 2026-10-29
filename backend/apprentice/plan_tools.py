"""Tidy a plan for display: short part labels that fit in a circle, names without repeated counts.

Pure and idempotent: applied whenever a plan is read, so stored plans are never rewritten.
"""

from __future__ import annotations

import re
from copy import deepcopy

_WRAP = re.compile(r"^[\(\[]\s*|\s*[\)\]]$")


def _clean(label: str) -> str:
    return _WRAP.sub("", str(label).strip())


def normalize_plan(plan: dict | None) -> dict | None:
    if not plan:
        return plan
    plan = deepcopy(plan)
    parts = plan.get("parts", [])
    cleaned = [_clean(p.get("label", "")) for p in parts]
    # Manuals sometimes label parts with whole names ("drain hose"); those don't fit a callout circle.
    renumber = any(len(c) > 3 or not c for c in cleaned) or len(set(cleaned)) != len(cleaned)
    mapping: dict[str, str] = {}
    for i, part in enumerate(parts):
        new = str(i + 1) if renumber else cleaned[i]
        for key in (part.get("label", ""), cleaned[i], part.get("name", "")):
            if key:
                mapping.setdefault(key, new)
                mapping.setdefault(key.lower(), new)
        part["label"] = new
        qty = part.get("quantity") or 1
        part["name"] = re.sub(rf"^{qty}\s*[x×]?\s+", "", part.get("name", "")).strip() or part.get("name", "")
    for step in plan.get("steps", []):
        uses = []
        for use in step.get("parts_used", []):
            raw = str(use.get("label", ""))
            label = mapping.get(raw) or mapping.get(_clean(raw)) or mapping.get(raw.lower())
            if label:
                uses.append({**use, "label": label})
        step["parts_used"] = uses
    return plan

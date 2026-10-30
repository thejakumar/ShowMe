"""Manual ingest: PDF -> page images -> Muse Spark reads each page -> Muse Wonder writes the plan.

Runs once per product; the plan is cached in the store, so demos never wait on it.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Optional

import pymupdf

from . import images as imgs
from . import prompts
from .config import Settings
from .muse import MuseClient, MuseError
from .schemas import PageExtraction, Plan
from .store import Store

KEYWORDS = {
    "assembly": ["step", "assembly", "parts", "hardware", "screw", "x", "tools", "dowel", "bolt"],
    "car": ["engine compartment", "under the hood", "dipstick", "engine oil", "coolant", "brake fluid", "washer fluid",
            "battery", "fuse", "warning light", "indicator", "tire pressure", "tyre pressure", "checking", "maintenance"],
    "training": ["clean", "descal", "refill", "jam", "maintenance", "parts", "step", "caution", "warning", "operation"],
    "device": ["button", "press", "hold", "port", "indicator", "light", "display", "menu", "setting", "setup",
               "connect", "reset", "pair", "clean", "descal", "troubleshoot", "error", "names of parts", "controls"],
}


def render_pages(pdf_path: Path, out_dir: Path, max_side: int = 1400, max_pages: int = 800) -> list[dict]:
    """Render every page to JPEG and keep its text layer."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    with pymupdf.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            rect = page.rect
            zoom = max_side / max(rect.width, rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            path = out_dir / f"p{i + 1:03d}.jpg"
            pix.save(str(path), jpg_quality=85)
            pages.append({"page": i + 1, "path": path, "text": page.get_text("text") or "",
                          "images": len(page.get_images(full=False)), "drawings": _drawing_count(page)})
    return pages


def _drawing_count(page) -> int:
    try:
        return len(page.get_drawings())
    except Exception:  # noqa: BLE001 - some PDFs have broken vector data
        return 0


def select_pages(pages: list[dict], use_case: str, limit: int) -> list[dict]:
    """Short manuals: every page. Long manuals (car owner's manuals run to 500+ pages): the most relevant ones."""
    if len(pages) <= limit:
        return pages
    words = KEYWORDS.get(use_case, KEYWORDS["assembly"])

    def score(p: dict) -> float:
        text = p["text"].lower()
        hits = sum(text.count(w) for w in words)
        visual = 2 if (p["images"] or p["drawings"] > 20) else 0
        return hits + visual

    ranked = sorted(pages, key=score, reverse=True)[: limit - 1]
    chosen = {p["page"] for p in ranked} | {1}
    return [p for p in pages if p["page"] in chosen]


def _compact(extraction: dict) -> dict:
    return {k: v for k, v in extraction.items() if v not in ([], "", None)}


async def ingest_manual(
    manual_id: str,
    pdf_path: Path,
    title: str,
    use_case: str,
    *,
    store: Store,
    muse: MuseClient,
    settings: Settings,
    rename_to_product: bool = False,
) -> Optional[dict]:
    ctx = {"manual_id": manual_id}
    try:
        store.update_manual(manual_id, progress="Rendering pages")
        pages = await asyncio.to_thread(render_pages, pdf_path, store.path("manuals", manual_id, "pages", "x").parent)
        if not pages:
            raise MuseError("The PDF has no pages.")
        chosen = select_pages(pages, use_case, settings.max_vision_pages)
        store.update_manual(manual_id, page_count=len(pages), progress=f"Reading {len(chosen)} of {len(pages)} pages")

        sem = asyncio.Semaphore(settings.max_parallel)
        done = 0

        async def read(p: dict) -> Optional[dict]:
            nonlocal done
            async with sem:
                page_img = imgs.open_path(p["path"])
                try:
                    reply = await muse.json(
                        role="spark",
                        purpose="page_extraction",
                        system=prompts.PAGE_SYSTEM,
                        text=prompts.page_prompt(p["page"], len(pages), p["text"], use_case),
                        images=[page_img],
                        schema=PageExtraction,
                        ctx={**ctx, "page": p["page"]},
                        mock_args={"page": p["page"], "use_case": use_case},
                    )
                    data = reply.model_dump()
                    data["page"] = p["page"]
                except MuseError as exc:
                    data = {"page": p["page"], "error": str(exc)[:200]}
                done += 1
                store.update_manual(manual_id, progress=f"Read {done} of {len(chosen)} pages")
                return data

        extractions = await asyncio.gather(*(read(p) for p in chosen))
        good = [_compact(e) for e in extractions if "error" not in e]
        if not good:
            raise MuseError(f"Muse Spark could not read any page: {extractions[0].get('error', '')}")
        # kept for "how do I ..." questions later, so answers come from every page that was read
        store.path("manuals", manual_id, "extractions.json").write_text(json.dumps(good, ensure_ascii=False))

        store.update_manual(manual_id, progress="Writing the lesson plan")
        plan = await muse.json(
            role="wonder",
            purpose="plan_building",
            system=prompts.PLAN_SYSTEM,
            text=prompts.plan_prompt(title, use_case, good),
            schema=Plan,
            effort=settings.plan_effort,
            ctx=ctx,
            mock_args={"use_case": use_case, "title": title},
        )
        plan_dict = plan.model_dump()
        plan_dict["steps"] = sorted(plan_dict["steps"], key=lambda s: s["order"])
        _attach_crops(plan_dict, pages, manual_id, store)
        plan_dict["use_case"] = use_case
        plan_dict["pages"] = [{"page": p["page"], "url": store.url(p["path"])} for p in pages]
        plan_dict["read_pages"] = [e["page"] for e in good]
        store.update_manual(manual_id, plan=plan_dict, status="ready", progress="Ready")
        if rename_to_product and plan_dict.get("product"):
            store.update_manual(manual_id, title=plan_dict["product"][:120])
        return plan_dict
    except Exception as exc:  # noqa: BLE001 - surface every failure to the user
        store.update_manual(manual_id, status="error", error=str(exc)[:500], progress="Failed")
        return None


def _attach_crops(plan: dict, pages: list[dict], manual_id: str, store: Store) -> None:
    """Cut each step's diagram and each part's drawing out of the page images."""
    by_page = {p["page"]: p["path"] for p in pages}
    cache: dict[int, object] = {}

    def page_img(n: Optional[int]):
        if n not in by_page:
            return None
        if n not in cache:
            cache[n] = imgs.open_path(by_page[n])
        return cache[n]

    for step in plan["steps"]:
        im = page_img(step.get("page"))
        if im is None:
            step["figure_url"] = None
            continue
        crop = imgs.crop_norm(im, [v / 1000 for v in step["figure_box"]], pad=0.01) if step.get("figure_box") else im
        path = imgs.save(crop, store.path("manuals", manual_id, "crops", f"step_{step['order']:02d}.jpg"))
        step["figure_url"] = store.url(path)

    sheet_items = []
    for part in plan["parts"]:
        im = page_img(part.get("page"))
        part["drawing_url"] = None
        if im is None or not part.get("drawing_box"):
            continue
        crop = imgs.crop_norm(im, [v / 1000 for v in part["drawing_box"]], pad=0.01)
        safe = re.sub(r"[^A-Za-z0-9]+", "_", part["label"]) or "x"
        path = imgs.save(crop, store.path("manuals", manual_id, "crops", f"part_{safe}.jpg"))
        part["drawing_url"] = store.url(path)
        sheet_items.append((f"{part['label']}  {part['name']}", crop))

    plan["reference_url"] = None
    if sheet_items:
        sheet = imgs.contact_sheet(sheet_items)
        path = imgs.save(sheet, store.path("manuals", manual_id, "reference.jpg"))
        plan["reference_url"] = store.url(path)

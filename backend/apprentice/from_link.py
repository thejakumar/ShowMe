"""Store link -> guide. Runs in the background after someone pastes or scans a product link.

1. Read the page (links.fetch_listing). Stores that block page reads still leave the product id and the
   name in the URL; the person can also type the name.
2. Muse Spark says what the product is and which kind of guide it needs (assembly, device, machine).
3. If the page links an official manual PDF, that manual is read like any upload: the full, exact guide.
4. If not, Muse Wonder writes a general setup guide from the listing, clearly labeled as such. For
   anything that needs assembling it gives preparation steps only and asks for the printed instructions,
   because inventing an assembly order would be worse than no guide.
"""

from __future__ import annotations

from typing import Optional

import httpx

from . import links, prompts
from .config import Settings
from .ingest import ingest_manual
from .muse import MuseClient, MuseError
from .schemas import ListingInfo, ListingPlan
from .store import Store


async def build_from_link(manual_id: str, link: links.Link, typed_name: str, *, store: Store, muse: MuseClient,
                          settings: Settings) -> Optional[dict]:
    ctx = {"manual_id": manual_id, "link": link.key}
    try:
        store.update_manual(manual_id, progress=f"Reading the {link.retailer or 'store'} page")
        listing = await links.fetch_listing(link)
        text = listing.as_text(link.name_hint, typed_name)
        if not (listing.title or link.name_hint or typed_name or listing.manual_links):
            why = ("The store blocked ShowMe from reading the page" if listing.blocked
                   else listing.error or "The page had no product details")
            raise MuseError(f"{why}. Type the product name (brand and model) and try again.")
        store.path("manuals", manual_id, "listing.txt").write_text(text)

        store.update_manual(manual_id, progress="Working out what the product is")
        info = await muse.json(role="spark", purpose="listing_read", system=prompts.LISTING_SYSTEM,
                               text=prompts.listing_prompt(text), schema=ListingInfo, effort=settings.check_effort,
                               ctx=ctx, mock_args={"text": text})
        if not info.is_product:
            raise MuseError("That page doesn't look like a single product. Paste the product's own page.")
        kind = info.kind
        product = (info.product or typed_name or listing.title or link.name_hint or "Product")[:120]
        source = {"kind": "listing", "url": listing.final_url or link.url, "retailer": listing.retailer or link.retailer,
                  "product_id": listing.product_id or link.product_id, "image": listing.image,
                  "blocked": listing.blocked, "manual_url": None, "brand": info.brand, "model": info.model}
        store.update_manual(manual_id, title=product, use_case=kind)

        for m in listing.manual_links[:3]:
            store.update_manual(manual_id, progress="Found a manual on the page. Downloading it")
            try:
                pdf = await links.download_pdf(m["url"])
            except (links.LinkError, httpx.HTTPError):
                continue
            path = store.path("manuals", manual_id, "source.pdf")
            path.write_bytes(pdf)
            plan = await ingest_manual(manual_id, path, product, kind, store=store, muse=muse, settings=settings)
            if plan is None:
                return None  # ingest already recorded the error
            plan["source"] = {**source, "kind": "manual_pdf", "manual_url": m["url"]}
            store.update_manual(manual_id, plan=plan)
            return plan

        store.update_manual(manual_id, progress="No manual on the page. Writing a setup guide from the listing")
        reply = await muse.json(role="wonder", purpose="listing_plan", system=prompts.LISTING_PLAN_SYSTEM,
                                text=prompts.listing_plan_prompt(product, kind, text, info.in_the_box),
                                schema=ListingPlan, effort=settings.plan_effort, ctx=ctx,
                                mock_args={"kind": kind, "product": product})
        plan = reply.model_dump()
        plan["product"] = product
        steps = sorted(plan["steps"], key=lambda s: s["order"])
        for i, st in enumerate(steps):
            st["order"], st["figure_url"], st["page"], st["figure_box"] = i + 1, None, None, None
        plan["steps"] = steps
        plan["needs_manual"] = bool(reply.needs_manual or kind == "assembly")
        plan.update(use_case=kind, pages=[], read_pages=[], reference_url=None, tasks=[],
                    source={**source, "kind": "listing"})
        for part in plan["parts"]:
            part["drawing_url"] = None
        if not steps:
            raise MuseError("Couldn't write a guide from that listing. Upload the manual instead.")
        store.update_manual(manual_id, plan=plan, status="ready", progress="Ready")
        return plan
    except Exception as exc:  # noqa: BLE001 - every failure is shown on the link screen
        store.update_manual(manual_id, status="error", error=str(exc)[:500], progress="Failed")
        return None

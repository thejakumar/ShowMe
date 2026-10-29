"""Finding parts in photos. One interface, four backends (set VISION_BACKEND):

  spark       Muse Spark returns boxes. Needs only the API key. Cloud.
  sam_api     SAM 3.1 on the Meta Model API. Cloud. The request shape is isolated in _sam_api_one();
              confirm it against the Meta docs on day 1.
  sam_local   SAM 3.1 open weights in this process (GPU + `sam3` package). Private.
  sam_remote  SAM 3.1 on your own GPU server running scripts/vision_server.py. Private.

All backends return detections with boxes in 0..1 [x0, y0, x1, y1] and an optional
polygon outline, so the phone can draw them the same way.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from PIL import Image

from . import images as imgs
from . import prompts
from .config import Settings
from .muse import MuseClient, MuseError
from .schemas import DetectionReply
from .trace import Tracer


class PrivacyError(RuntimeError):
    """Hybrid mode was asked for, but no private vision backend is configured."""


def iou(a: list[float], b: list[float]) -> float:
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area > 0 else 0.0


def dedupe(dets: list[dict], threshold: float = 0.6) -> list[dict]:
    """Drop near-duplicate boxes for the same label, keeping the more confident one."""
    kept: list[dict] = []
    for d in sorted(dets, key=lambda d: -d["confidence"]):
        if all(k["label"] != d["label"] or iou(k["box"], d["box"]) < threshold for k in kept):
            kept.append(d)
    return kept


class Vision:
    def __init__(self, settings: Settings, muse: MuseClient, tracer: Tracer) -> None:
        self.s = settings
        self.muse = muse
        self.tracer = tracer
        self._sam_local = None

    # ------------------------------------------------------------------ info

    @property
    def backend(self) -> str:
        return self.s.vision_backend

    @property
    def private_available(self) -> bool:
        return self.s.private_vision or self.s.mock

    def tag(self, private: bool) -> str:
        if self.s.mock:
            return "mock"
        return "local" if private else "cloud"

    # ------------------------------------------------------------------ main entry

    async def locate(
        self,
        img: Image.Image,
        targets: list[dict],
        *,
        purpose: str,
        count_every_instance: bool = False,
        reference: Optional[Image.Image] = None,
        private: bool = False,
        ctx: Optional[dict] = None,
        mock_args: Optional[dict] = None,
    ) -> tuple[list[dict], str]:
        """Find targets ({label, name, prompt, quantity}) in img. Returns (detections, notes)."""
        if not targets:
            return [], ""
        if private and not self.private_available:
            raise PrivacyError(
                "Hybrid mode keeps camera frames off the cloud, so it needs SAM 3.1 on hardware you control. "
                "Set VISION_BACKEND=sam_local (GPU in this PC) or sam_remote with SAM_REMOTE_URL."
            )
        img = imgs.fit(img, self.s.max_image_side)
        known = {t["label"] for t in targets}

        if self.s.mock:
            dets, notes = await self._spark(img, targets, purpose, count_every_instance, None, ctx, mock_args)
        elif private or self.backend in {"sam_local", "sam_remote"}:
            dets, notes = await self._private(img, targets, purpose, ctx)
        elif self.backend == "sam_api":
            dets, notes = await self._sam_api(img, targets, purpose, ctx)
        else:
            dets, notes = await self._spark(img, targets, purpose, count_every_instance, reference, ctx, mock_args)

        dets = [d for d in dets if d["label"] in known and d.get("box")]
        if not count_every_instance:  # keep the best instance per target
            best: dict[str, dict] = {}
            for d in dets:
                if d["label"] not in best or d["confidence"] > best[d["label"]]["confidence"]:
                    best[d["label"]] = d
            dets = list(best.values())
        return dedupe(dets), notes

    # ------------------------------------------------------------------ spark

    async def _spark(self, img, targets, purpose, count, reference, ctx, mock_args):
        images = [img] + ([reference] if reference is not None else [])
        reply = await self.muse.json(
            role="spark",
            purpose=purpose,
            system=prompts.LOCATE_SYSTEM,
            text=prompts.locate_prompt(targets, count, reference is not None),
            images=images,
            schema=DetectionReply,
            effort=self.s.check_effort,
            ctx=ctx,
            mock_args={"targets": targets, "count": count, **(mock_args or {})},
        )
        names = {t["label"]: t["name"] for t in targets}
        dets = [
            {"label": d.label, "name": names.get(d.label, d.label), "box": [v / 1000 for v in d.box],
             "confidence": float(d.confidence), "polygon": None, "source": "spark"}
            for d in reply.detections if d.box
        ]
        return dets, reply.notes

    # ------------------------------------------------------------------ SAM 3.1 on the Meta Model API

    async def _sam_api(self, img, targets, purpose, ctx):
        data_url = imgs.to_data_url(img)
        results = await asyncio.gather(*(self._sam_api_one(img, data_url, t, purpose, ctx) for t in targets))
        return [d for group in results for d in group], ""

    async def _sam_api_one(self, img, data_url, target, purpose, ctx):
        # ASSUMPTION: request and response field names. Check the SAM 3.1 page in the Meta Model API docs and
        # adjust this function only; everything else consumes the normalized detections it returns.
        body = {"model": self.s.sam_model, "image": data_url, "prompt": target.get("prompt") or target["name"]}
        try:
            data = await self.muse.post_raw(self.s.sam_api_path, body, purpose=f"{purpose}:{target['label']}",
                                            model=self.s.sam_model, ctx=ctx)
        except MuseError:
            raise
        return _parse_sam_reply(data, target, img.size)

    # ------------------------------------------------------------------ private SAM 3.1

    async def _private(self, img, targets, purpose, ctx):
        started = time.perf_counter()
        if self.backend == "sam_remote":
            if not self.s.sam_remote_url:
                raise PrivacyError("VISION_BACKEND=sam_remote needs SAM_REMOTE_URL (your vision_server.py address).")
            import httpx

            body = {"image": imgs.to_data_url(img), "prompts": [{"label": t["label"], "prompt": t.get("prompt") or t["name"]} for t in targets]}
            try:
                async with httpx.AsyncClient(timeout=self.s.request_timeout) as client:
                    resp = await client.post(self.s.sam_remote_url + "/segment", json=body)
                resp.raise_for_status()
                raw = resp.json().get("detections", [])
            except Exception as exc:  # noqa: BLE001
                self._trace_private(purpose, started, False, len(targets), ctx, repr(exc))
                raise MuseError(f"Private SAM 3.1 server at {self.s.sam_remote_url} failed: {exc}") from exc
        elif self.backend == "sam_local":
            if self._sam_local is None:
                from .sam_local import SamLocal  # imported lazily: needs torch and sam3

                self._sam_local = await asyncio.to_thread(SamLocal, self.s.sam_checkpoint or None)
            raw = await asyncio.to_thread(self._sam_local.segment, img, [(t["label"], t.get("prompt") or t["name"]) for t in targets])
        else:
            raise PrivacyError("Hybrid mode needs VISION_BACKEND=sam_local or sam_remote.")
        names = {t["label"]: t["name"] for t in targets}
        dets = [{**d, "name": names.get(d["label"], d["label"]), "source": "sam3.1"} for d in raw]
        self._trace_private(purpose, started, True, len(targets), ctx, None, len(dets))
        return dets, ""

    def _trace_private(self, purpose, started, ok, n_prompts, ctx, error, n_found=0):
        self.tracer.record(
            model=self.s.sam_model, purpose=purpose, tag="local", ok=ok, status=200 if ok else None,
            latency_ms=round((time.perf_counter() - started) * 1000), images=1, prompts=n_prompts,
            found=n_found, error=error, **{k: v for k, v in (ctx or {}).items() if v is not None},
        )


def _parse_sam_reply(data: Any, target: dict, size: tuple[int, int]) -> list[dict]:
    """Accept the common shapes a segmentation API returns and normalize them."""
    w, h = size
    items: list[dict] = []
    if isinstance(data, dict):
        for key in ("detections", "results", "objects", "predictions", "instances"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
        else:
            boxes, scores = data.get("boxes") or [], data.get("scores") or []
            items = [{"box": b, "score": scores[i] if i < len(scores) else 0.5} for i, b in enumerate(boxes)]
    out = []
    for item in items:
        box = item.get("box") or item.get("bbox") or item.get("bounding_box")
        if not box or len(box) != 4:
            continue
        x0, y0, x1, y1 = (float(v) for v in box)
        if max(x0, y0, x1, y1) > 1.0:  # pixels -> 0..1
            x0, x1, y0, y1 = x0 / w, x1 / w, y0 / h, y1 / h
        out.append({"label": target["label"], "box": [x0, y0, x1, y1],
                    "confidence": float(item.get("score", item.get("confidence", 0.5))),
                    "polygon": item.get("polygon")})
    return out

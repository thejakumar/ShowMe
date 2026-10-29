"""SAM 3.1 open weights, run on your own GPU. Used by VISION_BACKEND=sam_local and scripts/vision_server.py.

Setup (on a machine with an NVIDIA GPU):
    pip install torch torchvision            # CUDA build for your GPU
    git clone https://github.com/facebookresearch/sam3 && pip install -e sam3
    huggingface-cli login                     # accept the SAM 3.1 licence on its model page first

The calls below follow the SAM 3 README (build_sam3_image_model / Sam3Processor). If the
package's API differs in your version, adjust this file only: it must return detections as
{label, box [0..1], confidence, polygon or None}.
"""

from __future__ import annotations

from typing import Optional

from PIL import Image


class SamLocal:
    def __init__(self, checkpoint: Optional[str] = None, threshold: float = 0.4) -> None:
        try:
            import torch  # noqa: F401
            from sam3.model.sam3_image_processor import Sam3Processor
            from sam3.model_builder import build_sam3_image_model
        except ImportError as exc:  # pragma: no cover - needs GPU stack
            raise RuntimeError(
                "SAM 3.1 is not installed. See backend/apprentice/sam_local.py for setup, "
                "or use VISION_BACKEND=spark."
            ) from exc
        kwargs = {"checkpoint_path": checkpoint} if checkpoint else {}
        self.model = build_sam3_image_model(**kwargs)
        self.processor = Sam3Processor(self.model)
        self.threshold = threshold

    def segment(self, img: Image.Image, prompts: list[tuple[str, str]]) -> list[dict]:  # pragma: no cover
        w, h = img.size
        state = self.processor.set_image(img)
        out: list[dict] = []
        for label, text in prompts:
            result = self.processor.set_text_prompt(state=state, prompt=text)
            masks, boxes, scores = result["masks"], result["boxes"], result["scores"]
            for i in range(len(scores)):
                score = float(scores[i])
                if score < self.threshold:
                    continue
                x0, y0, x1, y1 = (float(v) for v in boxes[i].tolist())
                out.append({
                    "label": label,
                    "box": [x0 / w, y0 / h, x1 / w, y1 / h],
                    "confidence": score,
                    "polygon": _mask_polygon(masks[i], w, h),
                })
        return out


def _mask_polygon(mask, w: int, h: int) -> Optional[list[list[float]]]:  # pragma: no cover
    """Outline of the largest region in a mask, as 0..1 points. Needs OpenCV; returns None without it."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    arr = mask.squeeze().detach().cpu().numpy().astype("uint8") if hasattr(mask, "detach") else np.asarray(mask, dtype="uint8")
    contours, _ = cv2.findContours(arr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    approx = cv2.approxPolyDP(largest, 0.004 * cv2.arcLength(largest, True), True)
    mh, mw = arr.shape[:2]
    return [[float(p[0][0]) / mw, float(p[0][1]) / mh] for p in approx]

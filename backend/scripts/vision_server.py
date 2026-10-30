"""Private SAM 3.1 server for hybrid mode. Run it on a machine with an NVIDIA GPU you control.

    pip install fastapi uvicorn pillow torch torchvision   # plus the sam3 package, see apprentice/sam_local.py
    python backend/scripts/vision_server.py --port 8100

Then on the ShowMe machine set in .env:
    VISION_BACKEND=sam_remote
    SAM_REMOTE_URL=http://<gpu-machine>:8100

Camera frames go only to this server; nothing is sent to the cloud for part finding.
"""

from __future__ import annotations

import argparse
import base64
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from PIL import Image  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from apprentice.sam_local import SamLocal  # noqa: E402


class Prompt(BaseModel):
    label: str
    prompt: str


class SegmentRequest(BaseModel):
    image: str  # data URL or bare base64 JPEG/PNG
    prompts: list[Prompt]


def build(checkpoint: str | None) -> FastAPI:
    sam = SamLocal(checkpoint)
    app = FastAPI(title="ShowMe private SAM 3.1")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/segment")
    def segment(req: SegmentRequest):
        raw = req.image.split(",", 1)[1] if req.image.startswith("data:") else req.image
        img = Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB")
        return {"detections": sam.segment(img, [(p.label, p.prompt) for p in req.prompts])}

    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--checkpoint", default=None)
    args = ap.parse_args()
    uvicorn.run(build(args.checkpoint), host=args.host, port=args.port)

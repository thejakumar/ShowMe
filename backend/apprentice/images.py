"""Image helpers: load phone photos upright, resize, encode, crop by normalized boxes."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps


def load(data: bytes) -> Image.Image:
    """Open an uploaded image, apply the phone's EXIF rotation, and convert to RGB."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def open_path(path: Path) -> Image.Image:
    with Image.open(path) as img:
        return ImageOps.exif_transpose(img).convert("RGB")


def fit(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    scale = max_side / max(w, h)
    if scale >= 1:
        return img
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)


def to_jpeg(img: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def to_data_url(img: Image.Image, quality: int = 85) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(to_jpeg(img, quality)).decode("ascii")


def save(img: Image.Image, path: Path, quality: int = 88) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="JPEG", quality=quality, optimize=True)
    return path


def crop_norm(img: Image.Image, box: Sequence[float], pad: float = 0.02) -> Image.Image:
    """Crop with a box in 0..1 coordinates [x0, y0, x1, y1], padded a little on each side."""
    w, h = img.size
    x0, y0, x1, y1 = box
    x0, y0 = max(0.0, x0 - pad), max(0.0, y0 - pad)
    x1, y1 = min(1.0, x1 + pad), min(1.0, y1 + pad)
    left, top = int(x0 * w), int(y0 * h)
    right, bottom = max(left + 1, int(x1 * w)), max(top + 1, int(y1 * h))
    return img.crop((left, top, right, bottom))


def contact_sheet(items: Iterable[tuple[str, Image.Image]], cell: int = 220, cols: int = 4) -> Image.Image:
    """Lay crops out in a labeled grid, so one image can carry many references to a model."""
    items = list(items)
    if not items:
        return Image.new("RGB", (cell, cell), "white")
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 28)), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    for i, (label, im) in enumerate(items):
        r, c = divmod(i, cols)
        thumb = im.copy()
        thumb.thumbnail((cell - 12, cell - 12))
        x = c * cell + (cell - thumb.width) // 2
        y = r * (cell + 28) + 28 + (cell - thumb.height) // 2
        sheet.paste(thumb, (x, y))
        draw.text((c * cell + 8, r * (cell + 28) + 4), label, fill="black", font=font)
    return sheet

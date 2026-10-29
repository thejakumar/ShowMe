"""A small, fictional flat-pack manual (a birch step stool) used for testing and offline demos.

The layout constants here also feed the mock responses, so boxes in mock mode line up with
the rendered pages.
"""

from __future__ import annotations

from pathlib import Path

PAGE_W, PAGE_H = 595.0, 842.0  # A4 in points

PRODUCT = "Birch step stool"

PARTS = [
    {"label": "A", "name": "Seat", "quantity": 1, "description": "wide birch board with four holes underneath",
     "sam_prompt": "wide flat wooden board"},
    {"label": "B", "name": "Side panel", "quantity": 2, "description": "tall birch panel with pre-drilled holes",
     "sam_prompt": "tall wooden panel with holes"},
    {"label": "C", "name": "Back rail", "quantity": 1, "description": "long narrow birch rail",
     "sam_prompt": "long narrow wooden rail"},
    {"label": "D", "name": "Wooden dowel", "quantity": 8, "description": "8 x 30 mm grooved dowel",
     "sam_prompt": "small wooden dowel peg"},
    {"label": "E", "name": "Screw", "quantity": 8, "description": "silver flat-head screw, 30 mm",
     "sam_prompt": "small silver screw"},
    {"label": "F", "name": "Hex key", "quantity": 1, "description": "L-shaped 4 mm hex key",
     "sam_prompt": "small L-shaped metal hex key"},
]

STEPS = [
    {"order": 1, "title": "Dowels into the top edges",
     "instruction": "Push two dowels D into the holes on the top edge of each side panel B.",
     "parts_used": [{"label": "B", "quantity": 2}, {"label": "D", "quantity": 4}],
     "done_when": "Each side panel B has two dowels standing upright in its top edge, about half of each dowel showing",
     "warning": "", "why": "The dowels line the seat up with the panels so the screws go in straight."},
    {"order": 2, "title": "Dowels for the back rail",
     "instruction": "Push two dowels D into the holes on the inner face of each side panel B.",
     "parts_used": [{"label": "B", "quantity": 2}, {"label": "D", "quantity": 4}],
     "done_when": "Both side panels B have two dowels in their inner faces, and the inner faces point toward each other",
     "warning": "", "why": "If the dowels go into the outer face, the rail cannot be fitted in the next step."},
    {"order": 3, "title": "Attach the back rail",
     "instruction": "Fit rail C onto the dowels between the two side panels B, then fix it with four screws E.",
     "parts_used": [{"label": "C", "quantity": 1}, {"label": "E", "quantity": 4}],
     "done_when": "Rail C sits flush between both side panels with the pre-drilled holes of each panel facing inward",
     "warning": "Do not overtighten: the screws can strip the birch.",
     "why": "The rail keeps the side panels square, so the stool does not wobble."},
    {"order": 4, "title": "Fit the seat",
     "instruction": "Lower seat A onto the top dowels, holes facing down, and fix it with four screws E.",
     "parts_used": [{"label": "A", "quantity": 1}, {"label": "E", "quantity": 4}],
     "done_when": "Seat A rests flat on both side panels with no gap, and four screw heads are flush",
     "warning": "", "why": "A seat that is not flat puts all the load on two screws."},
    {"order": 5, "title": "Tighten and test",
     "instruction": "Tighten every screw with hex key F, then stand the stool on the floor and press on each corner.",
     "parts_used": [{"label": "F", "quantity": 1}],
     "done_when": "The stool stands upright on all four feet without rocking",
     "warning": "Maximum load 100 kg.", "why": "A final check catches a loose screw before anyone stands on it."},
]

# ---------------------------------------------------------------- layout (points, origin bottom-left)

MARGIN = 40.0
CELL_W = (PAGE_W - 2 * MARGIN) / 3
CELL_H = 220.0
PARTS_TOP = PAGE_H - 150.0
FIGURE = (50.0, 260.0, 545.0, 700.0)  # x0, y0 (bottom), x1, y1 (top)


def _to1000(x0: float, y_bottom: float, x1: float, y_top: float) -> list[int]:
    return [round(x0 / PAGE_W * 1000), round((PAGE_H - y_top) / PAGE_H * 1000),
            round(x1 / PAGE_W * 1000), round((PAGE_H - y_bottom) / PAGE_H * 1000)]


def part_cell(i: int) -> tuple[float, float, float, float]:
    col, row = i % 3, i // 3
    x0 = MARGIN + col * CELL_W
    y_top = PARTS_TOP - row * (CELL_H + 20)
    return x0, y_top - CELL_H, x0 + CELL_W, y_top


def part_box_1000(i: int) -> list[int]:
    return _to1000(*part_cell(i))


def figure_box_1000() -> list[int]:
    return _to1000(*FIGURE)


def page_of_step(order: int) -> int:
    return order + 2  # page 1 cover, page 2 parts, steps from page 3


def sample_plan() -> dict:
    parts = [{**p, "page": 2, "drawing_box": part_box_1000(i)} for i, p in enumerate(PARTS)]
    steps = [{**s, "page": page_of_step(s["order"]), "figure_box": figure_box_1000()} for s in STEPS]
    return {"product": PRODUCT, "summary": "Five steps, about 15 minutes, one person. Tool included.",
            "parts": parts, "steps": steps,
            "safety": ["Assemble on a soft surface to avoid scratching the birch.", "Maximum load 100 kg."],
            "warning_lights": []}


# ---------------------------------------------------------------- drawing

def build_pdf(path: Path) -> Path:
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    ink, wood, steel = HexColor("#1C2321"), HexColor("#E9D8B4"), HexColor("#B8C0C2")
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"{PRODUCT} assembly instructions")

    # page 1: cover
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 30)
    c.drawString(MARGIN, PAGE_H - 110, PRODUCT)
    c.setFont("Helvetica", 13)
    c.drawString(MARGIN, PAGE_H - 135, "Assembly instructions. One person, about 15 minutes.")
    _stool(c, PAGE_W / 2, 330, 1.6, ink, wood)
    c.setFont("Helvetica", 10)
    c.drawString(MARGIN, 60, "Fictional sample product for testing Apprentice.")
    c.showPage()

    # page 2: parts list
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(MARGIN, PAGE_H - 90, "Parts")
    for i, part in enumerate(PARTS):
        x0, y0, x1, y1 = part_cell(i)
        c.setStrokeColor(HexColor("#C9D1CD"))
        c.setLineWidth(0.8)
        c.roundRect(x0 + 6, y0, x1 - x0 - 12, y1 - y0, 8)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 + 6
        _part(c, part["label"], cx, cy, ink, wood, steel)
        _callout(c, x0 + 22, y1 - 22, part["label"], ink)
        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(x0 + 18, y0 + 14, f"{part['quantity']}x")
        c.setFont("Helvetica", 10)
        c.drawString(x0 + 46, y0 + 15, part["name"])
    c.showPage()

    # pages 3-7: steps
    for step in STEPS:
        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 44)
        c.drawString(MARGIN, PAGE_H - 100, str(step["order"]))
        c.setFont("Helvetica-Bold", 16)
        c.drawString(MARGIN + 50, PAGE_H - 92, step["title"])
        x0, y0, x1, y1 = FIGURE
        c.setStrokeColor(HexColor("#C9D1CD"))
        c.roundRect(x0, y0, x1 - x0, y1 - y0, 10)
        _step_figure(c, step["order"], (x0 + x1) / 2, (y0 + y1) / 2, ink, wood, steel)
        # hardware strip
        hx = MARGIN
        for use in step["parts_used"]:
            _callout(c, hx + 14, 205, use["label"], ink)
            c.setFillColor(ink)
            c.setFont("Helvetica-Bold", 14)
            c.drawString(hx + 34, 200, f"{use['quantity']}x")
            hx += 90
        c.setFont("Helvetica", 11)
        _wrap(c, step["instruction"], MARGIN, 160, PAGE_W - 2 * MARGIN, 15)
        if step["warning"]:
            c.setFont("Helvetica-Bold", 11)
            c.drawString(MARGIN, 95, "! " + step["warning"])
        c.showPage()
    c.save()
    return path


def _wrap(c, text, x, y, width, leading):
    words, line = text.split(), ""
    for w in words:
        trial = (line + " " + w).strip()
        if c.stringWidth(trial, "Helvetica", 11) > width:
            c.drawString(x, y, line)
            y -= leading
            line = w
        else:
            line = trial
    if line:
        c.drawString(x, y, line)


def _callout(c, x, y, label, ink):
    c.setStrokeColor(ink)
    c.setFillColor(ink)
    c.setLineWidth(1.2)
    c.circle(x, y, 11, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(x, y - 4, label)


def _board(c, x, y, w, h, ink, fill, holes=()):
    c.setStrokeColor(ink)
    c.setFillColor(fill)
    c.setLineWidth(1.3)
    c.roundRect(x - w / 2, y - h / 2, w, h, 3, stroke=1, fill=1)
    c.setFillColor(ink)
    for hx, hy in holes:
        c.circle(x + hx, y + hy, 2.4, stroke=0, fill=1)


def _dowel(c, x, y, ink, fill, angle=0):
    c.saveState()
    c.translate(x, y)
    c.rotate(angle)
    c.setStrokeColor(ink)
    c.setFillColor(fill)
    c.roundRect(-5, -16, 10, 32, 4, stroke=1, fill=1)
    for gy in (-8, 0, 8):
        c.line(-5, gy, 5, gy)
    c.restoreState()


def _screw(c, x, y, ink, steel, angle=0):
    c.saveState()
    c.translate(x, y)
    c.rotate(angle)
    c.setStrokeColor(ink)
    c.setFillColor(steel)
    c.circle(0, 14, 6, stroke=1, fill=1)
    c.line(-3, 14, 3, 14)
    c.rect(-1.8, -16, 3.6, 26, stroke=1, fill=1)
    for ty in range(-14, 8, 4):
        c.line(-3, ty, 3, ty + 2)
    c.restoreState()


def _hexkey(c, x, y, ink, steel):
    c.setStrokeColor(ink)
    c.setLineWidth(4)
    c.line(x - 30, y + 20, x + 20, y + 20)
    c.line(x + 20, y + 20, x + 20, y - 25)
    c.setLineWidth(1)


def _part(c, label, cx, cy, ink, wood, steel):
    if label == "A":
        _board(c, cx, cy, 130, 40, ink, wood, holes=[(-50, 0), (-25, 0), (25, 0), (50, 0)])
    elif label == "B":
        _board(c, cx, cy, 50, 120, ink, wood, holes=[(-12, 50), (12, 50), (0, 0), (0, -20)])
    elif label == "C":
        _board(c, cx, cy, 140, 22, ink, wood, holes=[(-62, 0), (62, 0)])
    elif label == "D":
        for dx in (-24, -8, 8, 24):
            _dowel(c, cx + dx, cy, ink, wood)
    elif label == "E":
        for dx in (-24, -8, 8, 24):
            _screw(c, cx + dx, cy, ink, steel)
    elif label == "F":
        _hexkey(c, cx, cy, ink, steel)


def _stool(c, cx, cy, s, ink, wood):
    _board(c, cx - 70 * s, cy, 18 * s, 120 * s, ink, wood)
    _board(c, cx + 70 * s, cy, 18 * s, 120 * s, ink, wood)
    _board(c, cx, cy + 66 * s, 170 * s, 16 * s, ink, wood)
    _board(c, cx, cy - 20 * s, 122 * s, 14 * s, ink, wood)


def _step_figure(c, order, cx, cy, ink, wood, steel):
    if order in (1, 2):
        _board(c, cx - 90, cy - 20, 50, 200, ink, wood)
        _board(c, cx + 90, cy - 20, 50, 200, ink, wood)
        spots = [(-102, 98), (-78, 98), (78, 98), (102, 98)] if order == 1 else [(-58, -40), (-58, 10), (58, -40), (58, 10)]
        for dx, dy in spots:
            _dowel(c, cx + dx, cy + dy + (14 if order == 1 else 0), ink, wood, 0 if order == 1 else 90)
    elif order == 3:
        _board(c, cx - 90, cy - 20, 50, 200, ink, wood)
        _board(c, cx + 90, cy - 20, 50, 200, ink, wood)
        _board(c, cx, cy - 20, 130, 24, ink, wood)
        for dx in (-106, -74, 74, 106):
            _screw(c, cx + dx, cy - 75, ink, steel, 90 if dx < 0 else -90)
    elif order == 4:
        _stool(c, cx, cy - 30, 1.0, ink, wood)
        _board(c, cx, cy + 120, 190, 22, ink, wood, holes=[(-70, -8), (-50, -8), (50, -8), (70, -8)])
        for dx in (-70, -50, 50, 70):
            _screw(c, cx + dx, cy + 160, ink, steel, 180)
    else:
        _stool(c, cx, cy, 1.4, ink, wood)
        _hexkey(c, cx + 150, cy + 90, ink, steel)

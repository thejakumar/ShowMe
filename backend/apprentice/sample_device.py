"""A fictional coffee maker manual for trying the device flow without a real PDF.

The PDF is drawn with reportlab, so Muse Spark reads it like any scanned manual; the plan below is
what mock mode returns for it.
"""

from __future__ import annotations

from pathlib import Path

PRODUCT = "Brewline One coffee maker"
PAGE_W, PAGE_H = 595.0, 842.0

CONTROLS = [
    {"label": "1", "name": "Power button", "description": "Turns the machine on and off. Glows white when ready.",
     "sam_prompt": "round power button with a ring light"},
    {"label": "2", "name": "Cup size dial", "description": "Turn to choose a small, medium or large cup.",
     "sam_prompt": "round dial with three cup sizes"},
    {"label": "3", "name": "Water tank", "description": "Removable tank at the back, with MIN and MAX lines.",
     "sam_prompt": "clear water tank at the back of the machine"},
    {"label": "4", "name": "Descale light", "description": "Glows orange when the machine needs descaling.",
     "sam_prompt": "small orange indicator light"},
    {"label": "5", "name": "Brew button", "description": "Starts brewing the cup size on the dial.",
     "sam_prompt": "large button with a cup symbol"},
    {"label": "6", "name": "Drip tray", "description": "Slides out for emptying and cleaning.",
     "sam_prompt": "slotted drip tray under the spout"},
    {"label": "7", "name": "Pod holder", "description": "Lift the handle to open it. One pod at a time.",
     "sam_prompt": "pod holder lid with a lever handle on top"},
]

SETUP = [
    {"order": 1, "title": "Fill the water tank", "page": 2,
     "instruction": "Lift out water tank 3, fill it with cold water to the MAX line, and put it back.",
     "parts_used": [{"label": "3", "quantity": 1}],
     "done_when": "Water tank 3 is seated at the back with water up to the MAX line",
     "warning": "", "why": "The pump can be damaged if it runs dry."},
    {"order": 2, "title": "Switch it on", "page": 2,
     "instruction": "Press power button 1. It flashes while the water heats, then stays white.",
     "parts_used": [{"label": "1", "quantity": 1}],
     "done_when": "Power button 1 glows steady white", "warning": "", "why": "Brewing before it's hot gives weak coffee."},
    {"order": 3, "title": "Rinse it once", "page": 2,
     "instruction": "Put a large mug on drip tray 6, turn dial 2 to large, and press brew button 5 with no pod.",
     "parts_used": [{"label": "6", "quantity": 1}, {"label": "2", "quantity": 1}, {"label": "5", "quantity": 1}],
     "done_when": "The mug under the spout has hot water in it and the brew button is no longer lit",
     "warning": "The water is hot.", "why": "Rinsing clears dust from the factory."},
]

TASKS = ["How do I brew a cup?", "How do I descale it?", "What does the orange light mean?",
         "How do I clean the drip tray?"]

PAGES_TEXT = {
    2: ["First use", "1. Lift out the water tank (3), fill it with cold water to MAX and put it back.",
        "2. Press the power button (1). It flashes while heating, then stays white.",
        "3. Rinse: place a large mug on the drip tray (6), turn the dial (2) to large and press brew (5) with no pod.",
        "", "Brew a cup",
        "1. Lift the pod holder handle (7), insert one pod, and close it until it clicks.",
        "2. Turn the dial (2) to your cup size and place a mug on the drip tray (6).",
        "3. Press brew (5). Brewing stops by itself. Lift the handle and remove the used pod."],
    3: ["Descaling", "The descale light (4) glows orange about once a month. Descaling takes 20 minutes.",
        "1. Empty the water tank (3), add one descaler sachet, and fill to MAX with water.",
        "2. Place a large mug on the drip tray (6). Do not insert a pod.",
        "3. Hold brew (5) for 3 seconds until the descale light (4) flashes.",
        "4. Wait until the light (4) goes out (about 20 minutes), emptying the mug when full.",
        "5. Rinse the tank, fill with clean water, and brew one large cup with no pod. Pour it away.",
        "Warning: never drink the descaling water.",
        "", "Cleaning", "Slide out the drip tray (6), empty it, wash it in warm soapy water, dry it and slide it back.",
        "", "Lights", "Power (1) flashing white: heating.  Power (1) steady white: ready.",
        "Descale (4) orange: descale soon.  Descale (4) flashing: descaling in progress."],
}


def sample_plan() -> dict:
    return {"product": PRODUCT, "summary": "Three setup steps, about 5 minutes. Controls 1 to 7.",
            "parts": [{**c, "quantity": 1, "page": 1, "drawing_box": None} for c in CONTROLS],
            "steps": [{**s, "figure_box": None} for s in SETUP],
            "safety": ["Never immerse the machine in water.", "Never drink the descaling water."],
            "warning_lights": [], "tasks": TASKS}


def build_pdf(path: Path) -> Path:
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    ink, body, glass, orange = HexColor("#1C2321"), HexColor("#D9DEDB"), HexColor("#CFE7F2"), HexColor("#F08A24")
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"{PRODUCT} user manual")

    # page 1: the machine with numbered callouts
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(40, PAGE_H - 80, PRODUCT)
    c.setFont("Helvetica", 12)
    c.drawString(40, PAGE_H - 102, "User manual. Names of parts.")
    c.setStrokeColor(ink)
    c.setLineWidth(2)
    c.setFillColor(glass)
    c.rect(330, 420, 80, 220, fill=1)  # tank (3) behind
    c.setFillColor(body)
    c.roundRect(170, 380, 180, 300, 14, fill=1)  # body
    c.roundRect(190, 640, 140, 24, 6, fill=1)  # pod holder (7)
    c.rect(200, 380, 120, 16, fill=1)  # drip tray (6)
    c.rect(245, 520, 30, 20, fill=1)  # spout
    c.setFillColor(HexColor("#FFFFFF"))
    c.circle(215, 600, 12, fill=1)  # power (1)
    c.circle(305, 600, 16, fill=1)  # dial (2)
    c.roundRect(235, 585, 50, 26, 6, fill=1)  # brew (5)
    c.setFillColor(orange)
    c.circle(215, 568, 5, fill=1)  # descale light (4)
    callouts = {"1": (215, 600, 120, 620), "2": (305, 600, 470, 600), "3": (370, 520, 470, 520),
                "4": (215, 568, 120, 560), "5": (260, 611, 260, 720), "6": (260, 388, 260, 330),
                "7": (210, 652, 120, 690)}
    c.setFont("Helvetica-Bold", 12)
    for label, (x0, y0, x1, y1) in callouts.items():
        c.setStrokeColor(ink)
        c.setLineWidth(1)
        c.line(x0, y0, x1, y1)
        c.setFillColor(HexColor("#FFFFFF"))
        c.circle(x1, y1, 11, fill=1)
        c.setFillColor(ink)
        c.drawCentredString(x1, y1 - 4, label)
    y = 270
    c.setFont("Helvetica", 11)
    for ctl in CONTROLS:
        c.drawString(60, y, f"{ctl['label']}  {ctl['name']}: {ctl['description']}")
        y -= 18
    c.setFont("Helvetica", 9)
    c.drawString(40, 40, "Fictional sample product for testing Apprentice.")
    c.showPage()

    for page in (2, 3):
        c.setFillColor(ink)
        y = PAGE_H - 70
        for line in PAGES_TEXT[page]:
            bold = line and not line[0].isdigit() and len(line) < 20
            c.setFont("Helvetica-Bold" if bold else "Helvetica", 15 if bold else 11)
            c.drawString(40, y, line)
            y -= 24 if bold else 17
        c.showPage()
    c.save()
    return path

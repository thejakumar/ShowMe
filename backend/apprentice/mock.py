"""Canned model answers for APPRENTICE_MOCK=1. Lets the whole app run with no key and no network.

Answers follow the sample step stool manual. Every mock call is traced with tag "mock", and
the app shows a "Mock mode" badge, so mock results are never mistaken for real ones.
"""

from __future__ import annotations

from typing import Any

from . import sample, sample_device

CAR_PARTS = [
    {"label": "1", "name": "Engine oil dipstick", "quantity": 1, "sam_prompt": "engine oil dipstick handle, yellow loop", "page": 212},
    {"label": "2", "name": "Oil filler cap", "quantity": 1, "sam_prompt": "oil filler cap with oil can symbol", "page": 212},
    {"label": "3", "name": "Coolant reservoir", "quantity": 1, "sam_prompt": "translucent coolant tank", "page": 214},
    {"label": "4", "name": "Brake fluid reservoir", "quantity": 1, "sam_prompt": "brake fluid reservoir", "page": 216},
    {"label": "5", "name": "Washer fluid reservoir", "quantity": 1, "sam_prompt": "washer fluid cap, blue", "page": 218},
    {"label": "6", "name": "Battery", "quantity": 1, "sam_prompt": "car battery", "page": 220},
]

CAR_BOXES = {
    "1": [560, 420, 610, 520], "2": [470, 380, 540, 440], "3": [700, 250, 860, 420],
    "4": [140, 230, 260, 330], "5": [80, 520, 220, 680], "6": [760, 560, 960, 820],
    "7": [300, 600, 440, 720], "8": [260, 300, 420, 470],
}


def respond(purpose: str, **kw: Any) -> dict:
    base = purpose.split(":")[0]
    handler = HANDLERS.get(base)
    if handler is None:
        raise KeyError(f"No mock for {purpose}")
    return handler(**kw)


# ---------------------------------------------------------------- manuals

def _page(page: int = 1, use_case: str = "assembly", **_: Any) -> dict:
    if use_case == "device":
        if page == 1:
            return {"page": 1, "kind": "parts", "notes": "",
                    "parts": [{k: c[k] for k in ("label", "name", "description")} for c in sample_device.CONTROLS]}
        return {"page": page, "kind": "steps", "notes": "",
                "steps": [{"number": i + 1, "text": t} for i, t in enumerate(sample_device.PAGES_TEXT.get(page, []))
                          if t[:1].isdigit()]}
    if use_case == "car":
        return {"page": page, "kind": "other", "parts": [], "steps": [], "warnings": [], "notes": "mock"}
    plan = sample.sample_plan()
    if page == 2:
        return {"page": 2, "kind": "parts", "notes": "",
                "parts": [{k: p[k] for k in ("label", "name", "quantity", "description", "drawing_box")} for p in plan["parts"]]}
    step = next((s for s in plan["steps"] if s["page"] == page), None)
    if step:
        return {"page": page, "kind": "steps", "warnings": [step["warning"]] if step["warning"] else [],
                "steps": [{"number": step["order"], "text": step["instruction"],
                           "parts_used": [f"{u['quantity']}x {u['label']}" for u in step["parts_used"]],
                           "figure_box": step["figure_box"]}]}
    return {"page": page, "kind": "cover" if page == 1 else "other", "notes": ""}


def _plan(use_case: str = "assembly", title: str = "", **_: Any) -> dict:
    if use_case == "device":
        plan = sample_device.sample_plan()
        if title and "brewline" not in title.lower():
            plan["product"] = title
        return plan
    if use_case == "car":
        return {
            "product": title or "Owner's manual",
            "summary": "Owner-level checks: engine oil, coolant, brake fluid, washer fluid.",
            "parts": CAR_PARTS,
            "steps": [
                {"order": 1, "title": "Find the dipstick", "instruction": "With the engine off and cool, pull out dipstick 1 and wipe it clean.",
                 "parts_used": [{"label": "1", "quantity": 1}], "done_when": "The dipstick is out and wiped clean", "page": 212,
                 "why": "Old oil on the stick gives a false reading."},
                {"order": 2, "title": "Read the oil level", "instruction": "Push the dipstick fully back in, pull it out again, and read the level.",
                 "parts_used": [{"label": "1", "quantity": 1}], "done_when": "The oil mark sits between the MIN and MAX marks", "page": 212,
                 "warning": "Never overfill.", "why": "Too little oil wears the engine; too much can damage seals."},
                {"order": 3, "title": "Top up washer fluid", "instruction": "Open cap 5 and fill with washer fluid to just below the neck.",
                 "parts_used": [{"label": "5", "quantity": 1}], "done_when": "Fluid is visible just below the neck and the cap is closed", "page": 218,
                 "why": "Clear washer fluid keeps the windshield visible."},
            ],
            "safety": ["Park on level ground with the engine off and cool."],
            "warning_lights": [
                {"name": "Oil pressure", "meaning": "Engine oil pressure is low.", "severity": "stop",
                 "action": "Stop safely, switch off, check the oil level.", "page": 98},
                {"name": "Low tire pressure", "meaning": "One or more tires are under-inflated.", "severity": "caution",
                 "action": "Check pressures and inflate to the door-sticker value.", "page": 102},
            ],
        }
    plan = sample.sample_plan()
    if title and use_case == "assembly" and "stool" not in title.lower():
        plan["product"] = title
    return plan


# ---------------------------------------------------------------- detections

def _grid_boxes(n: int, row: int, rows: int) -> list[list[int]]:
    """n boxes spread across one row of a loose grid (0..1000)."""
    top = 80 + row * (840 // rows)
    h = min(160, 840 // rows - 30)
    width = 900 // max(n, 1)
    return [[50 + i * width + 10, top, 50 + (i + 1) * width - 10, top + h] for i in range(n)]


def _detect(targets: list[dict] | None = None, count: bool = False, step: int | None = None, **_: Any) -> dict:
    targets = targets or []
    dets = []
    if targets and all(t["label"] in CAR_BOXES for t in targets) and not count:
        for t in targets:
            dets.append({"label": t["label"], "box": CAR_BOXES[t["label"]], "confidence": 0.88})
        return {"detections": dets, "notes": "mock"}
    rows = len(targets)
    for r, t in enumerate(targets):
        qty = int(t.get("quantity") or 1) if count else 1
        if count and step is None and t["label"] == "E":
            qty -= 1  # inventory: one screw short, to show the missing-part flow
        for i, box in enumerate(_grid_boxes(qty, r, rows)):
            dets.append({"label": t["label"], "box": box, "confidence": 0.9 if i % 5 else 0.74})
    return {"detections": dets, "notes": "mock layout"}


def _check(step: int = 1, attempt: int = 1, **_: Any) -> dict:
    if step == 3 and attempt == 1:
        return {"verdict": "fail", "confidence": 0.86,
                "observed": "The right side panel B is fitted with its pre-drilled holes facing outward.",
                "reason": "The holes must face inward so the seat lines up in step 4.",
                "fix": "Undo the two screws on the right panel, turn it around, and refit it.",
                "state": "wrong", "progress": 0.3, "done_items": ["Rail C is fixed to the left panel"],
                "next_steps": ["Undo the two screws on panel B", "Turn panel B so the holes face inward",
                               "Refit and tighten both screws"],
                "box": [600, 260, 820, 860]}
    return {"verdict": "pass", "confidence": 0.91, "observed": "The step matches the manual's diagram.",
            "reason": "", "fix": "", "box": None}


def _live(step: int = 1, frame: int = 1, **_: Any) -> dict:
    """A scripted live session: step 3 starts wrong, and the camera follows the fix as it happens."""
    if step == 3:
        script = [
            {"verdict": "fail", "confidence": 0.84, "state": "wrong", "progress": 0.3,
             "observed": "Panel B on the right has its holes facing outward.",
             "done_items": ["Rail C is in place on the left panel"],
             "next_steps": ["Undo the two screws on panel B", "Turn panel B so the holes face inward",
                            "Refit and tighten both screws"],
             "say": "Stop there. Panel B is backwards; undo its two screws.",
             "reason": "The rail can't fit if the holes face outward.",
             "fix": "Remove the two screws, turn panel B, refit it.", "box": [600, 260, 820, 860]},
            {"verdict": "fail", "confidence": 0.8, "state": "fixing", "progress": 0.45,
             "observed": "Panel B is off and turned around; its holes now face inward.",
             "done_items": ["Rail C is in place on the left panel", "Panel B turned the right way"],
             "next_steps": ["Hold panel B against the end of rail C", "Fit the two screws", "Tighten them"],
             "say": "Good, that's the right way round. Hold it against the rail.",
             "reason": "", "fix": "Refit panel B with the two screws.", "box": [620, 300, 800, 820]},
            {"verdict": "fail", "confidence": 0.82, "state": "in_progress", "progress": 0.75,
             "observed": "Panel B is fitted with the top screw in; the bottom screw is missing.",
             "done_items": ["Rail C is in place on the left panel", "Panel B turned the right way", "Top screw fitted"],
             "next_steps": ["Fit the bottom screw", "Tighten both screws with the Allen key"],
             "say": "Top screw's in. Now the bottom one.",
             "reason": "", "fix": "Fit the bottom screw.", "box": [640, 640, 760, 760]},
        ]
        if frame <= len(script):
            return script[frame - 1]
        return {"verdict": "pass", "confidence": 0.9, "state": "done", "progress": 1,
                "observed": "Rail C sits flush with both panels' holes facing inward; all screws tight.",
                "done_items": ["Panel B turned the right way", "Both screws fitted and tight"], "next_steps": [],
                "say": "That's it. Step three is done.", "reason": "", "fix": "", "box": None}
    if step == 4 and frame == 1:
        return {"verdict": "fail", "confidence": 0.8, "state": "in_progress", "progress": 0.2, "seen_step": 3,
                "observed": "Panel B is still being fitted to rail C; the seat is not on yet.",
                "done_items": ["Left panel and rail joined"], "next_steps": ["Finish step 3 first: fit panel B"],
                "say": "Looks like step three isn't finished yet.", "reason": "", "fix": "", "box": None}
    if frame == 1:
        return {"verdict": "unsure", "confidence": 0.5, "state": "cant_see", "observed": "Only part of the work is in view.",
                "next_steps": ["Step back so the whole piece is in view"],
                "say": "Step back a little so I can see the whole thing.", "reason": "", "fix": "", "box": None}
    return {"verdict": "pass", "confidence": 0.9, "state": "done", "observed": "The step matches the manual's diagram.",
            "say": "Looks right. Step done.", "reason": "", "fix": "", "box": None}


# ---------------------------------------------------------------- lessons

def _room(**_: Any) -> dict:
    return {"objects": [
        {"id": "o1", "name": "desk lamp", "description": "lamp, switched on", "box": [120, 140, 360, 560]},
        {"id": "o2", "name": "mug", "description": "white mug", "box": [600, 560, 730, 760]},
        {"id": "o3", "name": "plant", "description": "small potted plant", "box": [790, 250, 960, 600]},
    ]}


def _lesson(concept: str = "orbits", objects: list[dict] | None = None, **_: Any) -> dict:
    c = concept.lower()
    if any(w in c for w in ("orbit", "gravity", "planet", "moon")):
        beats = [
            {"say": "Let's make the lamp the Sun. It sits in the middle.", "visual": {"type": "highlight", "targets": ["o1"], "text": "Sun"}},
            {"say": "The mug is Earth, far smaller than the Sun.", "visual": {"type": "label", "targets": ["o2"], "text": "Earth"}},
            {"say": "Gravity pulls Earth toward the Sun all the time.", "visual": {"type": "arrow", "targets": ["o2", "o1"], "text": "gravity"}},
            {"say": "But Earth also moves sideways, fast. The pull bends that path into a loop: an orbit.",
             "visual": {"type": "orbit", "targets": ["o1", "o2"], "text": "one year"}},
        ]
        title, cast = "Why Earth keeps falling and never lands", [{"object_id": "o1", "role": "the Sun"}, {"object_id": "o2", "role": "Earth"}]
        quiz = {"question": "Tap the object that pulls Earth into its curved path.", "answer_object_id": "o1",
                "explanation": "The Sun's gravity bends Earth's path into an orbit."}
        limits = "A lamp has no meaningful gravity, and Earth's real orbit is a slight oval, not a perfect circle."
    elif "photosynth" in c or "plant" in c:
        beats = [
            {"say": "This plant is a tiny food factory.", "visual": {"type": "highlight", "targets": ["o3"], "text": "plant"}},
            {"say": "Light from the lamp gives it energy.", "visual": {"type": "flow", "targets": ["o1", "o3"], "text": "light"}},
            {"say": "It uses that energy to turn water and carbon dioxide into sugar, and releases oxygen.",
             "visual": {"type": "label", "targets": ["o3"], "text": "sugar + oxygen"}},
        ]
        title, cast = "How a plant makes food from light", [{"object_id": "o3", "role": "the plant"}, {"object_id": "o1", "role": "the Sun"}]
        quiz = {"question": "Tap the object that supplies the energy.", "answer_object_id": "o1", "explanation": "Light is the energy source."}
        limits = "Most lamps give far less light than the Sun, so a plant indoors grows more slowly."
    else:
        beats = [
            {"say": f"Let's explore {concept} with what's on your desk.", "visual": {"type": "highlight", "targets": ["o2"], "text": concept}},
            {"say": "Year one, the mug holds 100 coins and earns 10 percent.", "visual": {"type": "stack", "targets": ["o2"], "values": [100, 110, 121, 133], "text": "coins"}},
            {"say": "Each year's interest also earns interest, so the stack grows faster.", "visual": {"type": "compare", "targets": ["o2", "o3"], "text": "faster growth"}},
        ]
        title, cast = f"{concept.capitalize()} on your desk", [{"object_id": "o2", "role": "a savings jar"}]
        quiz = {"question": "Tap the savings jar.", "answer_object_id": "o2", "explanation": "The mug plays the savings jar."}
        limits = "Real rates change over time and fees reduce growth."
    return {"concept": concept, "title": title, "cast": cast, "beats": beats, "quiz": quiz, "analogy_limits": limits}


def _review(**_: Any) -> dict:
    return {"approved": True, "issues": [], "fixed_lesson": None}


def _warning(**_: Any) -> dict:
    return {"lights": [{"name": "Low tire pressure", "color": "amber",
                        "meaning": "One or more tires are under-inflated.", "severity": "caution",
                        "action": "Check all four tires with a gauge and inflate to the pressure on the door sticker.",
                        "manual_page": 102}], "notes": "mock"}


# ---------------------------------------------------------------- 3D step animations

def _stool_scene(order: int) -> dict:
    """The sample stool, built up step by step. Units cm, Y up."""
    parts = [
        {"id": "panel", "label": "B", "name": "Side panel", "shape": "box", "size": [2, 40, 28], "material": "wood"},
        {"id": "dowel", "label": "D", "name": "Wooden dowel", "shape": "cylinder", "size": [0.8, 3], "material": "wood"},
        {"id": "rail", "label": "C", "name": "Back rail", "shape": "box", "size": [34, 6, 2], "material": "wood"},
        {"id": "seat", "label": "A", "name": "Seat", "shape": "box", "size": [40, 2.5, 30], "material": "wood"},
        {"id": "screw", "label": "E", "name": "Screw", "shape": "screw", "size": [0.5, 3], "material": "metal"},
        {"id": "key", "label": "F", "name": "Hex key", "shape": "lkey", "size": [10], "material": "metal"},
    ]
    inst: list[dict] = [
        {"id": "panel_l", "part": "panel", "start": {"pos": [-18, 20, 0]}},
        {"id": "panel_r", "part": "panel", "start": {"pos": [18, 20, 0]}},
    ]
    actions: list[dict] = []

    def add(ids_poses, moving: bool, start_fn):
        ids = []
        for iid, part, end in ids_poses:
            if moving:
                inst.append({"id": iid, "part": part, "start": start_fn(end), "end": end})
            else:
                inst.append({"id": iid, "part": part, "start": end})
            ids.append(iid)
        return ids

    top = [(f"dt{i}", "dowel", {"pos": [x, 41.5, z]}) for i, (x, z) in enumerate([(-18, -7), (-18, 7), (18, -7), (18, 7)])]
    inner = [(f"di{i}", "dowel", {"pos": [x, y, -12], "rot": [0, 0, 90]})
             for i, (x, y) in enumerate([(-16.8, 10), (-16.8, 14), (16.8, 10), (16.8, 14)])]
    rail = [("rail", "rail", {"pos": [0, 12, -12]})]
    side_screws = [(f"ss{i}", "screw", {"pos": [x, y, -12], "rot": [0, 0, 90 if x < 0 else -90]})
                   for i, (x, y) in enumerate([(-19.6, 10), (-19.6, 14), (19.6, 10), (19.6, 14)])]
    seat = [("seat", "seat", {"pos": [0, 41.25, 0]})]
    seat_screws = [(f"st{i}", "screw", {"pos": [x, 43, z]}) for i, (x, z) in enumerate([(-18, -7), (-18, 7), (18, -7), (18, 7)])]

    def floor_spot(end):
        x, y, z = end["pos"]
        return {"pos": [x * 1.3, 0.6, 32 + abs(z) * 0.3], "rot": [90, 0, 0]}

    def outside(end):
        x, y, z = end["pos"]
        return {"pos": [x * 1.7, y, z], "rot": end.get("rot", [0, 0, 0])}

    def above(end):
        x, y, z = end["pos"]
        return {"pos": [x, y + 18, z], "rot": end.get("rot", [0, 0, 0])}

    if order == 1:
        actions.append({"targets": ["panel_l", "panel_r"], "type": "highlight", "at": 0, "duration": 1.4, "say": "Stand both side panels B upright."})
        ids = add(top, True, floor_spot)
        actions.append({"targets": ids, "type": "move", "at": 1.6, "duration": 1.6, "stagger": 0.35, "say": "Push two dowels D into each top edge."})
    else:
        add(top, False, None)
    if order == 2:
        ids = add(inner, True, floor_spot)
        actions.append({"targets": ids, "type": "move", "at": 0.4, "duration": 1.6, "stagger": 0.35, "say": "Now two dowels into each inner face."})
    elif order > 2:
        add(inner, False, None)
    if order == 3:
        ids = add(rail, True, lambda e: {"pos": [0, 1, 36]})
        actions.append({"targets": ["panel_l", "panel_r"], "type": "highlight", "at": 0, "duration": 1.2, "say": "Check the holes on both panels face inward."})
        actions.append({"targets": ids, "type": "move", "at": 1.4, "duration": 2.2, "say": "Slide rail C onto the dowels between the panels."})
        ids = add(side_screws, True, outside)
        actions.append({"targets": ids, "type": "screw", "at": 4.0, "duration": 1.8, "stagger": 0.4, "turns": 6, "say": "Fix it with four screws E from the outside."})
    elif order > 3:
        add(rail, False, None)
        add(side_screws, False, None)
    if order == 4:
        ids = add(seat, True, above)
        actions.append({"targets": ids, "type": "move", "at": 0.3, "duration": 2.2, "say": "Lower seat A onto the top dowels."})
        ids = add(seat_screws, True, above)
        actions.append({"targets": ids, "type": "screw", "at": 2.9, "duration": 1.6, "stagger": 0.35, "turns": 6, "say": "Fix the seat with four screws E."})
    elif order > 4:
        add(seat, False, None)
        add(seat_screws, False, None)
    if order == 5:
        inst.append({"id": "key", "part": "key", "start": {"pos": [30, 2, 20]}, "end": {"pos": [-18, 45, -7], "rot": [0, 30, 0]}})
        actions.append({"targets": ["key"], "type": "move", "at": 0.3, "duration": 1.6, "say": "Take hex key F to each screw."})
        actions.append({"targets": ["key"], "type": "rotate", "at": 2.1, "duration": 1.8, "turns": 2, "say": "Tighten every screw firmly."})
        actions.append({"targets": ["panel_l", "panel_r", "seat"], "type": "highlight", "at": 4.2, "duration": 1.6, "say": "Press each corner: the stool should not rock."})
    return {"title": f"Step {order}", "parts": parts, "instances": inst, "actions": actions,
            "camera": {"pos": [58, 52, 78], "target": [0, 18, 0]}, "notes": "mock"}


def _animation(step: int = 1, plan: dict | None = None, **_: Any) -> dict:
    if plan and plan.get("product") == sample.PRODUCT:
        return _stool_scene(step)
    from .animation import fallback
    from .training import step_by_order

    st = step_by_order(plan or {}, step) or {"title": f"Step {step}", "parts_used": []}
    return fallback(st, plan or {})


# ---------------------------------------------------------------- devices, ledger, crowd tips

DEVICE_BOXES = {"1": [330, 230, 400, 300], "2": [560, 220, 660, 320], "3": [700, 120, 860, 560],
                "4": [340, 330, 380, 370], "5": [430, 240, 540, 310], "6": [380, 760, 640, 820],
                "7": [360, 60, 640, 130]}


def _tour(targets: list[dict] | None = None, **_: Any) -> dict:
    dets = [{"label": t["label"], "box": DEVICE_BOXES[t["label"]], "confidence": 0.86}
            for t in targets or [] if t["label"] in DEVICE_BOXES]
    if not dets:
        return _detect(targets=targets)
    return {"detections": dets, "notes": "mock"}


def _ledger(targets: list[dict] | None = None, step: int = 1, **_: Any) -> dict:
    """Leftover hardware: before step 4 there is one screw too many (step 3 is missing one)."""
    dets = []
    for r, t in enumerate(targets or []):
        qty = int(t.get("quantity") or 0) + (1 if t["label"] == "E" and step == 4 else 0)
        for box in _grid_boxes(qty, r, max(len(targets), 1)) if qty else []:
            dets.append({"label": t["label"], "box": box, "confidence": 0.88})
    return {"detections": dets, "notes": "mock layout"}


def _heads_up(step: int = 1, examples: list[dict] | None = None, **_: Any) -> dict:
    if step == 3:
        return {"tip": "Most people fit panel B backwards here. The pre-drilled holes must face inward.",
                "look_for": "a side panel with its holes facing outward"}
    last = (examples or [{}])[-1]
    return {"tip": f"People often get this wrong: {last.get('observed', '')} {last.get('fix', '')}".strip(),
            "look_for": last.get("observed", "")}


def _answer(question: str = "", **_: Any) -> dict:
    q = question.lower()
    if "descal" in q:
        return {"answer": "Descale when light 4 glows orange, about once a month. It takes about 20 minutes "
                          "and needs one descaler sachet.", "in_manual": True, "pages": [3],
                "steps": [
                    {"title": "Add descaler", "instruction": "Empty water tank 3, add one descaler sachet, and fill it to MAX.",
                     "done_when": "Water tank 3 is back in place, filled to MAX", "parts_used": [{"label": "3"}], "page": 3},
                    {"title": "Place a large mug", "instruction": "Put a large mug on drip tray 6. Don't insert a pod.",
                     "done_when": "A large mug sits under the spout and the pod holder is empty", "parts_used": [{"label": "6"}], "page": 3},
                    {"title": "Start descaling", "instruction": "Hold brew button 5 for 3 seconds until light 4 flashes.",
                     "done_when": "Descale light 4 is flashing orange", "parts_used": [{"label": "5"}, {"label": "4"}], "page": 3},
                    {"title": "Wait for the light to go out", "instruction": "Wait about 20 minutes, emptying the mug when full.",
                     "done_when": "Descale light 4 is off", "parts_used": [{"label": "4"}], "page": 3},
                    {"title": "Rinse", "instruction": "Rinse tank 3, fill it with clean water, and brew one large cup with no pod. Pour it away.",
                     "done_when": "Tank 3 holds clean water and the rinse cup is brewed", "parts_used": [{"label": "3"}, {"label": "5"}], "page": 3},
                ], "safety": ["Never drink the descaling water."]}
    if "brew" in q or "cup" in q or "coffee" in q:
        return {"answer": "Put a pod in holder 7, choose the size on dial 2, and press brew button 5.", "in_manual": True,
                "pages": [2], "steps": [
                    {"title": "Insert a pod", "instruction": "Lift the handle of pod holder 7, insert one pod, and close it until it clicks.",
                     "done_when": "Pod holder 7 is closed with the handle down", "parts_used": [{"label": "7"}], "page": 2},
                    {"title": "Choose the size", "instruction": "Turn dial 2 to your cup size and put a mug on drip tray 6.",
                     "done_when": "A mug sits on drip tray 6 under the spout", "parts_used": [{"label": "2"}, {"label": "6"}], "page": 2},
                    {"title": "Brew", "instruction": "Press brew button 5. It stops by itself.",
                     "done_when": "Coffee is in the mug and button 5 is no longer lit", "parts_used": [{"label": "5"}], "page": 2},
                ], "safety": []}
    if "light" in q or "orange" in q:
        return {"answer": "A steady orange light 4 means descale soon. Flashing means descaling is in progress. "
                          "A flashing white power button just means it's heating.", "in_manual": True, "pages": [3],
                "steps": [], "safety": []}
    if "drip" in q or "clean" in q:
        return {"answer": "Slide out drip tray 6, empty it, wash it in warm soapy water, dry it, and slide it back.",
                "in_manual": True, "pages": [3], "steps": [
                    {"title": "Remove the tray", "instruction": "Slide out drip tray 6 and empty it.",
                     "done_when": "Drip tray 6 is out of the machine and empty", "parts_used": [{"label": "6"}], "page": 3},
                    {"title": "Wash and refit", "instruction": "Wash it in warm soapy water, dry it, and slide it back.",
                     "done_when": "Drip tray 6 is back in place, clean and dry", "parts_used": [{"label": "6"}], "page": 3},
                ], "safety": []}
    return {"answer": "The manual doesn't cover that.", "in_manual": False, "pages": [], "steps": [], "safety": []}


def _listing(text: str = "", **_: Any) -> dict:
    import re

    low = text.lower()
    named = re.search(r"^(?:Title[^:]*|Name typed by the person|Words in the link's address[^:]*): (.+)$", text, re.M)
    if "shelf" in low or "bookcase" in low or "stool" in low or "desk" in low:
        return {"is_product": True, "product": named.group(1).split(",")[0].strip() if named else "Corner shelf",
                "brand": "", "model": "", "kind": "assembly", "category": "furniture",
                "summary": "Flat-pack furniture.", "in_the_box": ["5 shelves", "8 tubes", "4 feet"]}
    if "not a product" in low:
        return {"is_product": False}
    return {"is_product": True, "product": "Brewline One coffee maker", "brand": "Brewline", "model": "One",
            "kind": "device", "category": "coffee maker", "summary": "A single-serve pod coffee maker.",
            "in_the_box": ["Coffee maker", "Water tank", "Drip tray"]}


def _listing_plan(kind: str = "device", product: str = "", **_: Any) -> dict:
    if kind == "assembly":
        return {"product": product, "summary": "Preparation from the store listing. The assembly steps are in the printed instructions.",
                "parts": [{"label": "1", "name": "Shelf", "quantity": 5}, {"label": "2", "name": "Tube", "quantity": 8},
                          {"label": "3", "name": "Foot", "quantity": 4}],
                "steps": [
                    {"order": 1, "title": "Clear a space", "instruction": "Clear a floor space about twice the shelf's size, on a rug or the flattened box.",
                     "done_when": "An empty, soft area of floor is ready", "why": "A soft surface protects the parts."},
                    {"order": 2, "title": "Lay out the parts", "instruction": "Open the box and lay every part out, grouped by type.",
                     "parts_used": [{"label": "1", "quantity": 5}, {"label": "2", "quantity": 8}, {"label": "3", "quantity": 4}],
                     "done_when": "All shelves, tubes and feet are laid out in groups", "why": "Counting first catches a missing part early."},
                    {"order": 3, "title": "Find the printed instructions", "instruction": "Find the instruction sheet in the box and photograph each page.",
                     "done_when": "The instruction sheet is out of the box and open", "why": "The exact order of assembly is only in the official instructions."},
                ], "safety": ["Keep small parts away from children."], "tasks": [], "needs_manual": True,
                "note": "The store listing doesn't include the assembly order. Photograph the instruction sheet to get the full guided build."}
    return {"product": product, "summary": "General first-time setup from the store listing.",
            "parts": [{"label": "1", "name": "Coffee maker", "quantity": 1}, {"label": "2", "name": "Water tank", "quantity": 1},
                      {"label": "3", "name": "Drip tray", "quantity": 1}],
            "steps": [
                {"order": 1, "title": "Unbox and check the contents", "instruction": "Take the coffee maker, water tank and drip tray out and remove all tape and film.",
                 "parts_used": [{"label": "1", "quantity": 1}, {"label": "2", "quantity": 1}, {"label": "3", "quantity": 1}],
                 "done_when": "The machine, tank and tray are on the counter with no tape or film left", "why": "Tape left on can block the water tank."},
                {"order": 2, "title": "Fill the water tank", "instruction": "Rinse the water tank, fill it with cold water, and fit it to the machine.",
                 "parts_used": [{"label": "2", "quantity": 1}], "done_when": "The water tank is full and seated on the machine", "why": "The pump must not run dry."},
                {"order": 3, "title": "Plug in and switch on", "instruction": "Plug the machine in and press the power button.",
                 "parts_used": [{"label": "1", "quantity": 1}], "done_when": "The machine shows a light that it is on", "why": "It needs to heat before the first use."},
            ], "safety": ["Never immerse the machine in water."], "tasks": [], "needs_manual": False,
            "note": "Exact buttons and settings aren't in the listing; the official manual has them."}


def _where(plan: dict | None = None, **_: Any) -> dict:
    orders = [st["order"] for st in (plan or {}).get("steps", [])]
    if (plan or {}).get("product") == sample.PRODUCT:
        return {"observed": "Both side panels are joined by rail C; the seat is not on yet.",
                "done_steps": [1, 2, 3], "current_step": 4, "wrong": [],
                "done_items": ["Seat A is next to the frame, holes facing down"],
                "next_steps": ["Lower seat A onto the top dowels", "Fix it with four screws E"],
                "say": "You've done steps one to three. Next, fit the seat.", "confidence": 0.84,
                "box": [300, 120, 700, 420]}
    cur = orders[1] if len(orders) > 1 else (orders[0] if orders else None)
    return {"observed": "Part of the work is done.", "done_steps": orders[:1], "current_step": cur, "wrong": [],
            "next_steps": ["Carry on with the next step"], "say": "Step one looks done. Carry on from step two.",
            "confidence": 0.8}


def _robot(plan: dict | None = None, use_case: str = "assembly", **_: Any) -> dict:
    """A hand-written robot plan for the sample stool; any other manual gets the rule-based outline."""
    from . import robot
    plan = plan or sample.sample_plan()
    if plan.get("product") != "Birch step stool":
        return robot.outline(plan, use_case)
    A = lambda skill, obj="", target="", tool="", detail="": {"skill": skill, "object": obj, "target": target, "tool": tool, "detail": detail}  # noqa: E731
    return {"tools": ["F hex key"], "notes": "Work on a soft, flat surface so the birch faces don't scratch.", "steps": [
        {"order": 1, "actions": [A("pick", "B side panel", detail="lay flat, top edge toward the robot"),
                                 A("pick", "D wooden dowel", detail="4 pieces, one at a time"),
                                 A("insert", "D wooden dowel", "2 holes in the top edge of each panel B", detail="push until about half shows"),
                                 A("inspect", tool="camera", detail="two dowels upright per panel")],
         "success_check": "Each side panel B has two dowels standing upright in its top edge, about half of each showing",
         "needs_human": False, "human_reason": "", "risks": []},
        {"order": 2, "actions": [A("rotate", "B side panel", detail="inner face up"),
                                 A("insert", "D wooden dowel", "2 holes in the inner face of each panel B", detail="4 pieces"),
                                 A("align", "B side panel", "each other", detail="inner faces pointing toward each other"),
                                 A("inspect", tool="camera")],
         "success_check": "Both side panels B have two dowels in their inner faces, and the inner faces point toward each other",
         "needs_human": False, "human_reason": "", "risks": []},
        {"order": 3, "actions": [A("hold", "B side panels", detail="upright, holes facing inward"),
                                 A("place", "C back rail", "dowels between the two panels B"),
                                 A("screw", "E screw", "rail C through panels B", "F hex key", "4 screws, snug, not tight"),
                                 A("inspect", tool="camera", detail="rail flush, holes inward")],
         "success_check": "Rail C sits flush between both side panels with each panel's pre-drilled holes facing inward",
         "needs_human": True, "human_reason": "Holding both panels upright while fitting the rail needs a second hand or a fixture.",
         "risks": ["panel B fitted backwards", "screws over-tightened in birch"]},
        {"order": 4, "actions": [A("pick", "A seat"),
                                 A("align", "A seat", "top dowels of both panels B", detail="holes facing down"),
                                 A("press", "A seat", "dowels", detail="evenly, until flush"),
                                 A("screw", "E screw", "seat A into panels B", "F hex key", "4 screws"),
                                 A("inspect", tool="camera")],
         "success_check": "Seat A sits flat on both side panels with no gap, all four screws in",
         "needs_human": False, "human_reason": "", "risks": ["seat placed upside down"]},
        {"order": 5, "actions": [A("pick", "F hex key", detail="tool"),
                                 A("tighten", "E screw", tool="F hex key", detail="all 8, hand-tight then a quarter turn"),
                                 A("place", "stool", "floor", detail="standing upright"),
                                 A("press", "seat A corners", detail="one at a time; must not rock"),
                                 A("inspect", tool="camera")],
         "success_check": "The stool stands level on the floor and doesn't rock when each corner is pressed",
         "needs_human": True, "human_reason": "The load test (up to 100 kg) should be done by a person.", "risks": []},
    ]}


HANDLERS = {
    "robot_plan": _robot,
    "where_am_i": _where,
    "listing_read": _listing,
    "listing_plan": _listing_plan,
    "controls_tour": _tour,
    "parts_ledger": _ledger,
    "mistake_summary": _heads_up,
    "task_answer": _answer,
    "page_extraction": _page,
    "plan_building": _plan,
    "parts_inventory": _detect,
    "step_parts": _detect,
    "step_check_local": _detect,
    "engine_bay": _detect,
    "room_objects_local": _detect,
    "step_check": _check,
    "live_check": _live,
    "step_animation": _animation,
    "room_objects": _room,
    "lesson_script": _lesson,
    "science_review": _review,
    "warning_lights": _warning,
}

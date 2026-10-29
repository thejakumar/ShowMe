"""Prompts for every model call. Each asks for one JSON object in an exact shape.

Box convention everywhere: integers 0-1000, [x0, y0, x1, y1], relative to the image's
width and height (0,0 = top-left).
"""

from __future__ import annotations

import json

BOX_RULE = (
    "Boxes are [x0, y0, x1, y1] as integers from 0 to 1000, relative to the image width and height, "
    "with (0, 0) at the top-left. Make boxes tight around the object."
)

# ---------------------------------------------------------------- manual reading (Spark)

PAGE_SYSTEM = (
    "You read pages of product manuals: flat-pack assembly instructions, appliance and equipment manuals, "
    "and car owner's manuals. Many assembly pages are wordless diagrams. Report only what the page shows; "
    "never invent parts or steps. " + BOX_RULE + " Reply with one JSON object and nothing else."
)


def page_prompt(page: int, total: int, text_layer: str, use_case: str) -> str:
    example = {
        "page": page,
        "kind": "parts | steps | warnings | cover | other",
        "parts": [{"label": "E", "name": "short screw", "quantity": 8,
                   "description": "silver flat-head screw, about 30 mm", "drawing_box": [120, 640, 220, 700]}],
        "steps": [{"number": 3, "text": "Insert dowels D into panel B, then attach panel C with screws E.",
                   "parts_used": ["4x D", "4x E"], "figure_box": [60, 120, 940, 900]}],
        "warnings": ["Two people are needed for this step."],
        "warning_lights": [{"name": "Oil pressure", "meaning": "Engine oil pressure is low", "severity": "stop",
                            "action": "Stop safely and check the oil level", "page": page}],
        "notes": "",
    }
    focus = {
        "assembly": "Parts lists usually show each part's drawing with a letter or number and a quantity like 8x. "
                    "Steps usually show a step number, a diagram, and the hardware used with quantities.",
        "car": "Look for under-hood diagrams that name components (dipstick, coolant reservoir, brake fluid, "
               "washer fluid, battery, fuses), owner maintenance procedures, and dashboard warning lights.",
        "training": "Look for named machine parts, numbered procedures (cleaning, refilling, descaling, clearing jams) "
                    "and safety warnings.",
    }.get(use_case, "")
    return (
        f"Page {page} of {total}. {focus}\n"
        "Use the manual's own labels and numbers. Leave a list empty when the page has none of that item. "
        "Put each part on the page where its drawing appears, with drawing_box around that drawing. "
        "Put figure_box around the whole diagram for each step.\n\n"
        f"Text found on the page (may be empty for picture-only pages):\n<<<\n{text_layer[:3000]}\n>>>\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example, indent=1)}"
    )


# ---------------------------------------------------------------- plan building (Wonder)

PLAN_SYSTEM = (
    "You are an expert trainer who turns a manual into a hands-on lesson that a phone camera can follow. "
    "You write for a beginner holding the real product. Stay faithful to the manual: never add steps, parts or "
    "safety claims it does not support. Reply with one JSON object and nothing else."
)


def plan_prompt(title: str, use_case: str, extractions: list[dict]) -> str:
    example = {
        "product": "Birch step stool",
        "summary": "Five steps, about 20 minutes, one person.",
        "parts": [{"label": "E", "name": "Short screw", "quantity": 8, "description": "silver, flat head, 30 mm",
                   "sam_prompt": "short silver screw", "page": 2, "drawing_box": [120, 640, 220, 700]}],
        "steps": [{"order": 3, "title": "Attach the back rail",
                   "instruction": "Push four dowels D into the holes on panel B, then fix rail C with four screws E.",
                   "parts_used": [{"label": "D", "quantity": 4}, {"label": "E", "quantity": 4}],
                   "done_when": "Rail C sits flush against both side panels B and all four screw heads are flush",
                   "warning": "", "why": "The rail keeps the side panels square so the stool does not wobble.",
                   "page": 5, "figure_box": [60, 120, 940, 900]}],
        "safety": ["Do not overtighten the screws in particle board."],
        "warning_lights": [],
    }
    guidance = {
        "assembly": "Build the full assembly sequence. Keep the manual's step order and count.",
        "car": "Build owner-level checks the manual describes: engine oil level, coolant level, brake fluid level, "
               "washer fluid top-up, tire pressure. As parts, list the under-hood components the manual names "
               "(dipstick, oil filler cap, coolant reservoir, brake fluid reservoir, washer reservoir, battery, fuse box) "
               "with label 1, 2, 3... Never include brakes, airbags, fuel or high-voltage work. "
               "Copy any dashboard warning lights into warning_lights.",
        "training": "Build the main operator procedures (for example cleaning, refilling, clearing a jam), "
                    "each step checkable by a photo. List the machine parts involved.",
    }.get(use_case, "")
    return (
        f"Manual: {title}\nUse case: {use_case}. {guidance}\n\n"
        "Rules:\n"
        "- Use the manual's labels for parts (A, B, C or 101, 102...). Merge duplicates found on several pages.\n"
        "- sam_prompt: a short noun phrase that describes how the part looks, for an object detector "
        "(\"short silver screw\", \"wooden dowel\", \"long flat board with four holes\").\n"
        "- done_when: something visible in one photo of the work, not a feeling (\"holes face inward\", "
        "\"dipstick oil mark between MIN and MAX\").\n"
        "- why: one sentence on why the step matters.\n"
        "- page and figure_box: copy them from the page where the step's diagram appears.\n"
        "- warning: copy the manual's warning for that step, or leave it empty.\n\n"
        f"Pages read from the manual:\n{json.dumps(extractions, ensure_ascii=False)[:60000]}\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example, indent=1)}"
    )


# ---------------------------------------------------------------- finding things (Spark)

LOCATE_SYSTEM = (
    "You find physical objects in photos so an app can draw outlines on them. Be precise and honest: "
    "report only objects you can actually see. " + BOX_RULE + " Reply with one JSON object and nothing else."
)


def locate_prompt(targets: list[dict], count_every_instance: bool, has_reference: bool) -> str:
    lines = "\n".join(
        f"- {t['label']}: {t['name']}" + (f" ({t['prompt']})" if t.get("prompt") else "")
        + (f", expected {t['quantity']}" if count_every_instance and t.get("quantity") else "")
        for t in targets
    )
    ref = ("The second image is a reference sheet with the manual's drawing of each part, labeled. "
           "Use it to tell similar parts apart (for example short versus long screws).\n") if has_reference else ""
    counting = ("Give every single instance its own detection: eight screws means eight detections. "
                "Do not merge touching parts.\n") if count_every_instance else (
                "Give one detection per target, on the most visible instance.\n")
    example = {"detections": [{"label": "E", "box": [412, 655, 448, 702], "confidence": 0.82}],
               "notes": "one screw partly hidden under panel B"}
    return (
        f"Find these items in the first image:\n{lines}\n\n{ref}{counting}"
        "If you cannot tell which label an object has, pick the most likely one and lower the confidence. "
        "Leave out items that are not visible.\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example)}"
    )


# ---------------------------------------------------------------- step checking (Spark)

CHECK_SYSTEM = (
    "You verify one step of a hands-on procedure from a photo of the real work. Judge only what is visible. "
    "Be strict about the done-when condition, but do not fail the step for things that belong to other steps. "
    "If the photo does not show enough to decide, answer unsure. " + BOX_RULE +
    " Reply with one JSON object and nothing else."
)


def check_prompt(step: dict, parts_lookup: dict[str, str], has_figure: bool) -> str:
    uses = ", ".join(f"{u['quantity']}x {u['label']} ({parts_lookup.get(u['label'], '')})" for u in step.get("parts_used", []))
    example = {"verdict": "fail", "confidence": 0.86,
               "observed": "Panel B is attached with its pre-drilled holes facing outward.",
               "reason": "The holes must face inward so the rail can be fixed in step 4.",
               "fix": "Remove the two screws, turn panel B around, and refit it.",
               "box": [520, 300, 760, 820]}
    fig = "The second image is the manual's diagram for this step.\n" if has_figure else ""
    return (
        f"Step {step['order']}: {step['title']}\nInstruction: {step['instruction']}\n"
        f"Parts used: {uses or 'none listed'}\nDone when: {step['done_when']}\n\n"
        f"The first image is a photo of the work after the person did this step. {fig}"
        "verdict is pass, fail or unsure. box marks the area the person should look at (the problem on a fail).\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example)}"
    )


LIVE_SYSTEM = (
    "You are a calm, expert coach watching a live camera while a person does one step of a hands-on procedure. "
    "Each message is the newest camera frame. Judge only what is visible. Speak like a person standing beside "
    "them: one short instruction at a time, about what to do right now. Notice progress and say so briefly. "
    "If the frame is blurry, too dark or does not show the work, ask them to adjust the camera. "
    + BOX_RULE + " Reply with one JSON object and nothing else."
)


def live_prompt(step: dict, parts_lookup: dict[str, str], has_figure: bool, history: list[dict]) -> str:
    uses = ", ".join(f"{u['quantity']}x {u['label']} ({parts_lookup.get(u['label'], '')})" for u in step.get("parts_used", []))
    example = {"verdict": "fail", "confidence": 0.82,
               "observed": "Panel B is turned around; its holes face outward.",
               "say": "Turn panel B around so the holes face inward.",
               "reason": "The holes must face inward for the rail to fit.",
               "fix": "Remove the two screws, turn panel B, and refit it.",
               "box": [520, 300, 760, 820]}
    recent = "\n".join(f"- {h.get('observed', '')} You said: {h.get('say', '')}" for h in history[-3:]) or "- (first frame)"
    fig = "The second image is the manual's diagram for this step.\n" if has_figure else ""
    return (
        f"Step {step['order']}: {step['title']}\nInstruction: {step['instruction']}\n"
        f"Parts used: {uses or 'none listed'}\nDone when: {step['done_when']}\n\n"
        f"What you saw and said in the last few frames:\n{recent}\n\n"
        f"The first image is the newest camera frame. {fig}"
        "verdict: pass only when the done-when condition is clearly met in this frame; fail when something is wrong "
        "or not finished yet; unsure when the frame cannot show it. say: at most 14 words, an instruction for right "
        "now (or a short confirmation on pass); do not repeat your last line word for word. box marks where to look.\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example)}"
    )


# ---------------------------------------------------------------- concept lessons

ROOM_SYSTEM = (
    "You list everyday objects in a photo that could act as props in a short science or money lesson. "
    + BOX_RULE + " Reply with one JSON object and nothing else."
)


def room_prompt() -> str:
    example = {"objects": [{"id": "o1", "name": "desk lamp", "description": "black lamp, switched on", "box": [80, 90, 300, 520]},
                           {"id": "o2", "name": "mug", "description": "white mug", "box": [520, 560, 640, 720]}]}
    return ("List up to 10 distinct, clearly visible objects with their boxes. Prefer objects with simple shapes "
            "(lamps, cups, plants, balls, bottles, books, fruit, coins, taps). Use ids o1, o2, ...\n\n"
            f"Reply in exactly this shape:\n{json.dumps(example)}")


LESSON_SYSTEM = (
    "You are a gifted teacher who explains ideas using the real objects in front of the learner. "
    "You write short, concrete lines a 12-year-old can follow, and you are scientifically accurate. "
    "Reply with one JSON object and nothing else."
)


def lesson_prompt(concept: str, objects: list[dict]) -> str:
    example = {
        "concept": "orbits",
        "title": "Why the Moon keeps falling and never lands",
        "cast": [{"object_id": "o1", "role": "the Sun", "why": "it is the bright, central object"},
                 {"object_id": "o2", "role": "Earth", "why": "it is small and can move around the lamp"}],
        "beats": [
            {"say": "Let's make the lamp the Sun.", "visual": {"type": "highlight", "targets": ["o1"], "text": "Sun"}},
            {"say": "Earth moves sideways fast, while gravity pulls it toward the Sun.",
             "visual": {"type": "arrow", "targets": ["o2", "o1"], "text": "gravity"}},
            {"say": "Sideways speed plus that pull bends its path into a circle: an orbit.",
             "visual": {"type": "orbit", "targets": ["o1", "o2"], "text": "one year"}},
        ],
        "quiz": {"question": "Tap the object that pulls Earth into its curved path.", "answer_object_id": "o1",
                 "explanation": "The Sun's gravity bends Earth's path."},
        "analogy_limits": "A lamp has no real gravity, and real orbits are slightly oval, not perfect circles.",
    }
    return (
        f"Teach this concept: {concept}\n\n"
        f"Objects in the learner's room (use their ids):\n{json.dumps(objects, ensure_ascii=False)}\n\n"
        "Cast 1-3 of these objects in roles. Write 4-6 beats; each beat has one or two sentences to say aloud and "
        "one visual. Visual types: highlight (targets: [id]), label (targets: [id], text), arrow (targets: [from, to], "
        "text), orbit (targets: [center, mover]), flow (targets: [from, to], text: what flows), stack (targets: [id], "
        "values: growing numbers, text: units), compare (targets: [a, b], text). Use only listed ids. "
        "End with a quiz whose answer is one of the cast objects, and say plainly where the analogy breaks down.\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example)}"
    )


REVIEW_SYSTEM = (
    "You are a careful science and finance reviewer. Check a short lesson for factual errors and misleading "
    "analogies. Approve it if it is accurate for a beginner. Reply with one JSON object and nothing else."
)


def review_prompt(lesson: dict) -> str:
    example = {"approved": False, "issues": ["Beat 2 says gravity pushes; it pulls."], "fixed_lesson": "same shape as the lesson, or null"}
    return (f"Lesson to review:\n{json.dumps(lesson, ensure_ascii=False)}\n\n"
            "If you find errors, return approved false, list the issues, and return fixed_lesson with the same ids "
            "and shape but corrected text. If it is accurate, return approved true and fixed_lesson null.\n\n"
            f"Reply in exactly this shape:\n{json.dumps(example)}")


# ---------------------------------------------------------------- car

WARNING_SYSTEM = (
    "You identify lit dashboard warning lights in a photo of a car instrument cluster and explain them for an "
    "owner. If a light is unclear, say so. Never advise repairs beyond what an owner should do. "
    "Reply with one JSON object and nothing else."
)


def warning_prompt(manual_lights: list[dict]) -> str:
    example = {"lights": [{"name": "Low tire pressure", "color": "amber",
                           "meaning": "One or more tires are below the recommended pressure.", "severity": "caution",
                           "action": "Check all four tires with a gauge and inflate to the pressure on the door sticker.",
                           "manual_page": 112}], "notes": ""}
    ref = (f"Warning lights described in this car's manual:\n{json.dumps(manual_lights, ensure_ascii=False)[:8000]}\n"
           "Match to these when you can and give their manual_page.\n") if manual_lights else ""
    return (f"Which warning lights are lit in this photo?\n{ref}"
            "severity: info (no action needed), caution (check soon), stop (stop driving safely).\n\n"
            f"Reply in exactly this shape:\n{json.dumps(example)}")


# ---------------------------------------------------------------- 3D step animation (Wonder)

ANIM_SYSTEM = (
    "You are a 3D technical illustrator. You turn one step of an assembly manual into a short 3D animation built "
    "from simple shapes, so a beginner sees exactly how each part moves into place. Use real-world proportions in "
    "centimetres. Y is up, the floor is y = 0, and the assembly is centred on x = 0, z = 0. Follow the manual's "
    "diagram closely. Reply with one JSON object and nothing else."
)


def anim_prompt(step: dict, parts: list[dict], earlier: list[str], has_figure: bool) -> str:
    example = {
        "title": "Attach the back rail",
        "parts": [
            {"id": "panel", "label": "B", "name": "Side panel", "shape": "box", "size": [2, 40, 28], "material": "wood"},
            {"id": "rail", "label": "C", "name": "Back rail", "shape": "box", "size": [34, 6, 2], "material": "wood"},
            {"id": "screw", "label": "E", "name": "Screw", "shape": "screw", "size": [0.5, 3], "material": "metal"},
        ],
        "instances": [
            {"id": "panel_l", "part": "panel", "start": {"pos": [-18, 20, 0]}},
            {"id": "panel_r", "part": "panel", "start": {"pos": [18, 20, 0]}},
            {"id": "rail", "part": "rail", "start": {"pos": [0, 1, 35]}, "end": {"pos": [0, 12, -12]}},
            {"id": "screw_l", "part": "screw", "start": {"pos": [-32, 12, -12], "rot": [0, 0, 90]},
             "end": {"pos": [-19.5, 12, -12], "rot": [0, 0, 90]}},
        ],
        "actions": [
            {"targets": ["panel_l", "panel_r"], "type": "highlight", "at": 0, "duration": 1.5, "say": "The side panels stand upright."},
            {"targets": ["rail"], "type": "move", "at": 1.5, "duration": 2.5, "say": "Slide rail C onto the dowels."},
            {"targets": ["screw_l"], "type": "screw", "at": 4.5, "duration": 2, "turns": 5, "say": "Drive the screws in."},
        ],
        "camera": {"pos": [55, 50, 75], "target": [0, 15, 0]},
        "notes": "",
    }
    uses = "\n".join(
        f"- {p['label']}: {p['name']} x{p.get('used', p.get('quantity', 1))}"
        + (f" ({p['description']})" if p.get("description") else "") for p in parts
    )
    fig = "The image is the manual's diagram for this step: match its layout and directions.\n" if has_figure else ""
    before = "; ".join(earlier[-6:]) or "nothing yet"
    return (
        f"Step {step['order']}: {step['title']}\nInstruction: {step['instruction']}\nDone when: {step['done_when']}\n"
        f"Parts used in this step:\n{uses or '- (none listed)'}\n"
        f"Already done in earlier steps: {before}\n{fig}\n"
        "Rules:\n"
        "- shape and size: box [width, height, depth], cylinder [diameter, height], tube [outer diameter, height, "
        "inner diameter], sphere [diameter], cone [diameter, height], screw [diameter, length], "
        "wheel [diameter, width], lkey [length]. Guess sizes from the part names if the manual gives none.\n"
        "- Cylinders, tubes, screws and cones stand along Y; use rot (degrees) to tip them. A screw's head is at its "
        "top, so rotate it to point the tip into the hole.\n"
        "- start is where a part waits (on the floor beside the work, 20-40 cm away); end is its fitted position. "
        "Parts already fitted in earlier steps get only start and no action.\n"
        "- Identical parts in a circle (five chair legs, castors): one instance with \"repeat\": N, posed for the "
        "first copy; the other copies are rotated evenly around the Y axis through x = 0, z = 0.\n"
        "- action types: move, screw (spins while it moves in), appear, highlight, rotate. Actions run in order by "
        "\"at\" (seconds); targets are instance ids. Each \"say\" is at most 12 words, spoken aloud.\n"
        "- At most 14 instances and 8 actions; under 20 seconds in total. Include the tool if the step uses one.\n"
        "- camera: where to look from, framing the whole step.\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example)}"
    )

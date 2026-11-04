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
        "training": "Look for named machine parts and controls, numbered procedures (cleaning, refilling, descaling, "
                    "clearing jams) and safety warnings.",
        "device": "This is an electronic device. Look for the diagram that names each button, dial, port, light and "
                  "screen element (usually numbered callouts), how-to procedures, settings menus, indicator-light "
                  "meanings and troubleshooting tables. Put each control in parts with its callout number as label "
                  "and what it does in description.",
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
        "tasks": [],
    }
    guidance = {
        "assembly": "Build the full assembly sequence. Keep the manual's step order and count.",
        "car": "Build owner-level checks the manual describes: engine oil level, coolant level, brake fluid level, "
               "washer fluid top-up, tire pressure. As parts, list the under-hood components the manual names "
               "(dipstick, oil filler cap, coolant reservoir, brake fluid reservoir, washer reservoir, battery, fuse box) "
               "with label 1, 2, 3... Never include brakes, airbags, fuel or high-voltage work. "
               "Copy any dashboard warning lights into warning_lights.",
        "training": "As parts, list the machine's named parts and controls (label = the manual's callout number, "
                    "description = what it is for). As steps, write the main start-up or first-use procedure, each "
                    "step checkable by a photo. In tasks, list 4 to 8 other jobs the manual explains, phrased as "
                    "questions an operator would ask (\"How do I clear a paper jam?\").",
        "device": "As parts, list every control the manual names: buttons, dials, ports, lights, screen areas "
                  "(label = the manual's callout number, description = what it does, sam_prompt = how it looks). "
                  "As steps, write the first-time setup, each step checkable by a photo. In tasks, list 4 to 8 "
                  "things people most often want to do that the manual explains, phrased as questions "
                  "(\"How do I descale it?\", \"What does the blinking orange light mean?\").",
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

STATE_RULES = (
    "state is where the work is right now: not_started (nothing of this step done yet), in_progress (partly done, "
    "nothing wrong so far), fixing (the person is correcting a mistake: parts loosened, removed or being turned), "
    "wrong (something is done incorrectly and must be undone), done (the done-when condition is clearly met), "
    "cant_see (the image cannot show it). The work can be half done, or half undone in the middle of a fix: judge "
    "the state you see, never assume they started from scratch. progress is the share of this step that is done, "
    "0 to 1. done_items lists what is already right (short). next_steps lists the next 1 to 3 actions in order, "
    "starting from exactly where the work is now, each under 12 words; empty when done. If the image clearly shows "
    "a different step of the procedure (they are ahead or behind), put that step's number in seen_step, else null."
)


def _procedure(plan_steps: list[dict] | None, current: int) -> str:
    if not plan_steps:
        return ""
    lines = "\n".join(f"{'>' if s['order'] == current else ' '} {s['order']}. {s['title']}" for s in plan_steps[:40])
    return f"The whole procedure (> marks this step):\n{lines}\n\n"


CHECK_SYSTEM = (
    "You verify one step of a hands-on procedure from a photo of the real work. Judge only what is visible. "
    "Be strict about the done-when condition, but do not fail the step for things that belong to other steps. "
    "If the photo does not show enough to decide, answer unsure. " + BOX_RULE +
    " Reply with one JSON object and nothing else."
)


def _crowd(common_mistake: str) -> str:
    return (f"Earlier builders often got this step wrong like this: {common_mistake} "
            "Check for that mistake specifically.\n" if common_mistake else "")


def check_prompt(step: dict, parts_lookup: dict[str, str], has_figure: bool, plan_steps: list[dict] | None = None,
                 common_mistake: str = "") -> str:
    uses = ", ".join(f"{u['quantity']}x {u['label']} ({parts_lookup.get(u['label'], '')})" for u in step.get("parts_used", []))
    example = {"verdict": "fail", "confidence": 0.86, "state": "in_progress", "progress": 0.5,
               "observed": "Panel B is fitted with its holes facing inward; only one of two screws is in.",
               "done_items": ["Panel B faces the right way", "Top screw fitted"],
               "next_steps": ["Fit the second screw at the bottom of panel B", "Tighten both screws"],
               "reason": "Both screws are needed or the frame will wobble.",
               "fix": "Fit and tighten the bottom screw.",
               "seen_step": None, "box": [520, 300, 760, 820]}
    fig = "The second image is the manual's diagram for this step.\n" if has_figure else ""
    return (
        f"{_procedure(plan_steps, step['order'])}"
        f"Step {step['order']}: {step['title']}\nInstruction: {step['instruction']}\n"
        f"Parts used: {uses or 'none listed'}\nDone when: {step['done_when']}\n{_crowd(common_mistake)}\n"
        f"The first image is a photo of the work. {fig}"
        "verdict is pass (done), fail (wrong or not finished) or unsure (cannot tell). "
        "box marks the area the person should look at next (the problem on a mistake).\n"
        f"{STATE_RULES}\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example)}"
    )


LIVE_SYSTEM = (
    "You are a calm, expert coach watching a live camera while a person does one step of a hands-on procedure. "
    "Each message is the newest camera frame. Judge only what is visible. Speak like a person standing beside "
    "them: one short instruction at a time, about what to do right now from where the work is. They may be "
    "partway through, or in the middle of fixing a mistake: notice what is already done, say so briefly, and "
    "guide the rest. If the frame is blurry, too dark or does not show the work, ask them to adjust the camera. "
    + BOX_RULE + " Reply with one JSON object and nothing else."
)


def live_prompt(step: dict, parts_lookup: dict[str, str], has_figure: bool, history: list[dict],
                plan_steps: list[dict] | None = None, common_mistake: str = "") -> str:
    uses = ", ".join(f"{u['quantity']}x {u['label']} ({parts_lookup.get(u['label'], '')})" for u in step.get("parts_used", []))
    example = {"verdict": "fail", "confidence": 0.82, "state": "fixing", "progress": 0.4,
               "observed": "Panel B is off and turned around; its holes now face inward.",
               "done_items": ["Panel B turned the right way"],
               "next_steps": ["Hold panel B against rail C", "Fit the two screws", "Tighten them with the Allen key"],
               "say": "Good, that's the right way round. Now hold it against the rail.",
               "reason": "The holes must face inward for the rail to fit.",
               "fix": "Refit panel B with the two screws.",
               "seen_step": None, "box": [520, 300, 760, 820]}
    recent = "\n".join(f"- {h.get('observed', '')} You said: {h.get('say', '')}" for h in history[-3:]) or "- (first frame)"
    fig = "The second image is the manual's diagram for this step.\n" if has_figure else ""
    return (
        f"{_procedure(plan_steps, step['order'])}"
        f"Step {step['order']}: {step['title']}\nInstruction: {step['instruction']}\n"
        f"Parts used: {uses or 'none listed'}\nDone when: {step['done_when']}\n{_crowd(common_mistake)}\n"
        f"What you saw and said in the last few frames:\n{recent}\n\n"
        f"The first image is the newest camera frame. {fig}"
        "verdict: pass only when the done-when condition is clearly met in this frame; fail when something is wrong "
        "or not finished yet; unsure when the frame cannot show it.\n"
        f"{STATE_RULES}\n"
        "say: at most 14 words, spoken aloud: briefly note progress if there is new progress, then the first of "
        "next_steps (or a short confirmation when done). Do not repeat your last line word for word. "
        "box marks where to look or work next.\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example)}"
    )


# ---------------------------------------------------------------- what earlier builders got wrong (Spark)

HEADS_UP_SYSTEM = (
    "You turn the mistakes earlier people made at one step of a manual into one short heads-up for the next "
    "person, spoken before they start the step. Name the mistake and the right way, in plain words. Never "
    "invent a mistake that is not in the list. Reply with one JSON object and nothing else."
)


def heads_up_prompt(step: dict, examples: list[dict]) -> str:
    lines = "\n".join(f"- saw: {e['observed']} | why it matters: {e['reason']} | fix: {e['fix']}" for e in examples)
    example = {"tip": "Most people fit panel B backwards here. The holes must face inward.",
               "look_for": "panel B with its pre-drilled holes facing outward"}
    return (
        f"Step {step['order']}: {step['title']}\nInstruction: {step['instruction']}\nDone when: {step['done_when']}\n\n"
        f"Mistakes caught at this step:\n{lines}\n\n"
        "tip: at most 18 words, starting with what people get wrong. look_for: what a camera should watch for.\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example)}"
    )


# ---------------------------------------------------------------- "how do I ..." from the manual (Wonder)

ASK_SYSTEM = (
    "You answer questions about one machine or electronic device using only its manual, then turn the answer "
    "into steps a person can follow with the device in front of them while a camera checks each step. "
    "Never invent buttons, settings or procedures the manual does not describe. If the manual does not cover "
    "the question, say so plainly and leave steps empty. Reply with one JSON object and nothing else."
)


def ask_prompt(question: str, product: str, controls: list[dict], manual_text: str) -> str:
    ctl = "\n".join(f"- {c['label']}: {c['name']}" + (f" ({c['description']})" if c.get("description") else "")
                    for c in controls) or "- (none listed)"
    example = {
        "answer": "Descale when light 4 glows orange, about once a month. It takes 20 minutes and uses one sachet.",
        "in_manual": True, "pages": [2],
        "steps": [{"order": 1, "title": "Fill the tank with descaler",
                   "instruction": "Empty water tank 3, add one descaler sachet, and fill to MAX with water.",
                   "done_when": "Water tank 3 is back in place and filled to the MAX line",
                   "parts_used": [{"label": "3", "quantity": 1}], "warning": "", "why": "Descaler dissolves limescale.",
                   "page": 2}],
        "safety": ["Do not drink the descaling water."],
    }
    return (
        f"Device: {product}\nQuestion: {question}\n\nControls and parts named in the manual (use these labels):\n{ctl}\n\n"
        f"The manual:\n<<<\n{manual_text[:60000]}\n>>>\n\n"
        "answer: two or three short sentences that answer the question directly, to be read aloud.\n"
        "steps: only when the answer is something to do; 1 to 10 steps, each one action, in the manual's order. "
        "done_when must be visible in one photo of the device (a light, a screen message, a part in place). "
        "parts_used uses the control labels above. page: the manual page it comes from.\n"
        "pages: every page the answer relies on. in_manual: false when the manual does not cover it.\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example, indent=1)}"
    )


# ---------------------------------------------------------------- store links (Spark reads, Wonder writes)

LISTING_SYSTEM = (
    "You read what is known about a product from an online store link and say what the product is. "
    "Use only the text given. Reply with one JSON object and nothing else."
)


def listing_prompt(listing_text: str) -> str:
    example = {"is_product": True, "product": "Furinno Turn-N-Tube 5-tier corner shelf", "brand": "Furinno",
               "model": "11055", "kind": "assembly", "category": "shelf",
               "summary": "A five-tier corner shelf that assembles with twist tubes and no tools.",
               "in_the_box": ["5 shelves", "tubes", "feet"]}
    return (
        f"{listing_text}\n\n"
        "kind: assembly for anything the buyer has to put together (furniture, shelves, desks, bikes, grills); "
        "device for electronics and appliances the buyer sets up and uses; training for work machines and tools. "
        "product: brand, product and model in plain words, no marketing. Take the name from the typed name or the "
        "Title; the words in the link's address are search keywords and only a last resort. in_the_box: only what the text says is "
        "included. is_product: false if this is not a single product page.\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example)}"
    )


LISTING_PLAN_SYSTEM = (
    "You write a first-time setup guide for one product when the official manual is NOT available, only its "
    "store listing. A phone camera will check each step, and a beginner follows it with the product in front of "
    "them. Never invent model-specific details: no button names, menu paths, screw sizes, part letters or "
    "assembly order unless the listing states them. Use only what the listing says plus steps that are true for "
    "every product of this type. Reply with one JSON object and nothing else."
)


def listing_plan_prompt(product: str, kind: str, listing_text: str, in_the_box: list[str]) -> str:
    example = {
        "product": "Brewline One coffee maker",
        "summary": "General first-time setup from the store listing. Five steps, about 10 minutes.",
        "parts": [{"label": "1", "name": "Coffee maker", "quantity": 1, "description": "the machine itself",
                   "sam_prompt": "small coffee machine"}],
        "steps": [{"order": 1, "title": "Unbox and check the contents",
                   "instruction": "Take everything out and lay it on a table next to the box.",
                   "parts_used": [{"label": "1", "quantity": 1}],
                   "done_when": "Every item from the box is out on the table and the packaging is removed",
                   "warning": "", "why": "A missing part is easier to sort out before you start."}],
        "safety": ["Keep plastic bags away from children."],
        "tasks": [], "needs_manual": False,
        "note": "Exact buttons and settings aren't in the listing; the official manual has them.",
    }
    rule = ("This product needs assembling. Set needs_manual true. Give only preparation steps (clear a space, open "
            "the box on a soft surface, lay out and group every part, find the tools the listing names) and end with "
            "a step to find the printed instructions in the box. Do not write any assembly steps."
            if kind == "assembly" else
            "Write 3 to 8 setup steps from unboxing to first use. Only name controls, apps or settings the listing "
            "itself mentions; otherwise describe generically (\"the power button\"). Set needs_manual true if "
            "setup clearly depends on details the listing doesn't give.")
    box = "\n".join(f"- {b}" for b in in_the_box) or "- (not stated)"
    return (
        f"Product: {product}\nKind: {kind}\n\nWhat the store listing says:\n{listing_text[:8000]}\n\n"
        f"In the box (from the listing):\n{box}\n\n{rule}\n"
        "parts: only items the listing says are included, labeled 1, 2, 3. done_when: visible in one photo. "
        "tasks: leave empty. note: one sentence on what this guide can't cover without the manual.\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example, indent=1)}"
    )


# ---------------------------------------------------------------- where am I (Spark)

RESUME_SYSTEM = (
    "You look at one photo of work in progress and work out how far along a hands-on procedure the person is, "
    "so they can carry on from exactly there. Judge only what is visible; finished steps often hide earlier ones, "
    "so count a step as done when a later visible result needs it. Never claim a step is done if the photo can't "
    "support it. " + BOX_RULE + " Reply with one JSON object and nothing else."
)


def resume_prompt(plan: dict, parts_lookup: dict[str, str], has_reference: bool) -> str:
    lines = []
    for st in plan.get("steps", [])[:40]:
        uses = ", ".join(f"{u['quantity']}x {u['label']} {parts_lookup.get(u['label'], '')}" for u in st.get("parts_used", []))
        lines.append(f"{st['order']}. {st['title']}: {st['instruction']} Parts: {uses or 'none'}. Done when: {st['done_when']}")
    ref = "The second image is the manual's parts sheet.\n" if has_reference else ""
    example = {"observed": "Both side panels stand with the back rail fixed between them; the seat is not on yet.",
               "done_steps": [1, 2, 3], "current_step": 4,
               "wrong": [{"step": 2, "observed": "The right panel's dowels are in its outer face.",
                          "fix": "Move the two dowels to the inner face.", "box": [610, 300, 720, 520]}],
               "done_items": [], "next_steps": ["Lower seat A onto the top dowels", "Fix it with four screws E"],
               "say": "You've done steps 1 to 3. Next, fit the seat.", "confidence": 0.8, "box": [300, 120, 700, 420]}
    return (
        f"Product: {plan.get('product', '')}\nThe procedure:\n" + "\n".join(lines) + "\n\n"
        f"The first image is the person's work right now. {ref}"
        "done_steps: every step that is visibly finished and right. wrong: finished steps done incorrectly, each with "
        "what you see, the fix, and a box. current_step: the first step not finished (the one to do now), or null "
        "if the photo doesn't show the work. done_items: what is already right within current_step, if it's half "
        "done. next_steps: 1 to 3 actions from exactly here. say: at most 18 words, spoken. box: where to work next.\n\n"
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


# ---------------------------------------------------------------- teaching a robot (Wonder)

ROBOT_SYSTEM = (
    "You turn a product's step-by-step manual into a task plan a robot arm could follow. You keep the manual's "
    "steps and order, break each step into primitive actions, and keep the step's visible done-when condition "
    "as the success check a camera will verify. Never invent parts, tools or steps the manual does not have. "
    "Reply with one JSON object and nothing else."
)


def robot_prompt(plan: dict, use_case: str = "assembly") -> str:
    names = {p["label"]: p.get("name", p["label"]) for p in plan.get("parts", [])}
    parts = "\n".join(f"- {p['label']}: {p.get('name', '')} x{p.get('quantity', 1)}" for p in plan.get("parts", [])) or "- (none listed)"
    steps = "\n".join(
        f"{s['order']}. {s.get('title', '')}: {s.get('instruction', '')}"
        f" | uses: {', '.join(f'''{u.get('quantity', 1)}x {u['label']} {names.get(u['label'], '')}''' for u in s.get('parts_used', [])) or '-'}"
        f" | done when: {s.get('done_when', '')}" + (f" | warning: {s['warning']}" if s.get("warning") else "")
        for s in plan.get("steps", []))
    example = {
        "steps": [{"order": 1, "actions": [
            {"skill": "pick", "object": "D wooden dowel", "target": "", "tool": "", "detail": "4 pieces, one at a time"},
            {"skill": "insert", "object": "D wooden dowel", "target": "holes in the top edge of panel B",
             "tool": "", "detail": "push until half the dowel shows"},
            {"skill": "inspect", "object": "", "target": "", "tool": "camera", "detail": "two dowels per panel"}],
            "success_check": "Both side panels B have two dowels standing upright in their top edge",
            "needs_human": False, "human_reason": "", "risks": []}],
        "tools": ["hex key F"], "notes": "Lay the panels flat on a soft surface first.",
    }
    return (
        f"Product: {plan.get('product', '')}\n"
        + ("The robot assembles this product from its parts.\n" if use_case == "assembly" else
           "The robot operates this machine: the labels are its controls (buttons, dials, tanks), which it presses, "
           "turns, fills or plugs in rather than picking up.\n")
        + f"\nParts:\n{parts}\n\nSteps:\n{steps}\n\n"
        "For every step, in order:\n"
        "- actions: 2 to 8 primitives. skill is one of pick, place, insert, align, screw, tighten, press, rotate, "
        "hold, connect, inspect. object uses the manual's part label and name. target says where it goes. detail "
        "gives orientation, count or force that matters (\"holes facing inward\", \"hand-tight\"). End with inspect.\n"
        "- success_check: what a camera must see when the step is done; reuse the done-when.\n"
        "- needs_human: true when the step needs two people, fine judgement, or carries a warning a robot can't "
        "follow safely; say why in human_reason. risks: short phrases.\n"
        "tools: tools the manual names. notes: one sentence of setup advice.\n\n"
        f"Reply in exactly this shape:\n{json.dumps(example)}"
    )

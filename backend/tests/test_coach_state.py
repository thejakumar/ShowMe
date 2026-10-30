"""Where-the-work-is logic: half-done and mid-fix steps are progress, not mistakes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apprentice.schemas import StepCheckReply  # noqa: E402
from apprentice.training import coach_fields  # noqa: E402

PLAN = {"steps": [{"order": 1, "title": "Join the legs"}, {"order": 2, "title": "Fit the seat"}]}


def reply(**kw):
    return StepCheckReply(**{"verdict": "fail", "confidence": 0.8, **kw})


def test_in_progress_is_not_a_mistake():
    v, f = coach_fields(reply(state="partly done", progress="60%", next_steps="1. Fit screw\n2. Tighten"), PLAN, 2, "fail")
    assert v == "progress" and f["state"] == "in_progress" and f["progress"] == 0.6
    assert f["next_steps"] == ["Fit screw", "Tighten"]


def test_fixing_is_progress_and_wrong_is_fail():
    assert coach_fields(reply(state="fixing"), PLAN, 2, "fail")[0] == "progress"
    assert coach_fields(reply(state="wrong"), PLAN, 2, "fail")[0] == "fail"


def test_pass_that_says_unfinished_is_held_back():
    v, f = coach_fields(reply(verdict="pass", state="in_progress"), PLAN, 2, "pass")
    assert v == "progress"


def test_fix_used_when_no_next_steps():
    v, f = coach_fields(reply(state="wrong", fix="Turn the panel"), PLAN, 2, "fail")
    assert f["next_steps"] == ["Turn the panel"]


def test_seen_step_must_exist_and_differ():
    assert coach_fields(reply(seen_step="1"), PLAN, 2, "fail")[1]["seen_step"] == {"order": 1, "title": "Join the legs"}
    assert coach_fields(reply(seen_step=2), PLAN, 2, "fail")[1]["seen_step"] is None
    assert coach_fields(reply(seen_step=9), PLAN, 2, "fail")[1]["seen_step"] is None


def test_missing_state_falls_back_to_verdict():
    assert coach_fields(reply(), PLAN, 2, "fail")[1]["state"] == "wrong"
    v, f = coach_fields(None, PLAN, 2, "pass")
    assert v == "pass" and f["state"] == "done" and f["progress"] == 1.0

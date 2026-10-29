import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from apprentice.plan_tools import normalize_plan  # noqa: E402


def test_parenthesised_labels_and_counts_in_names():
    plan = {"parts": [{"label": "(4)", "name": "5 Castors", "quantity": 5}, {"label": "(11)", "name": "10 Screws (M6X12mm)", "quantity": 10}],
            "steps": [{"order": 1, "parts_used": [{"label": "(4)", "quantity": 5}, {"label": "11", "quantity": 10}]}]}
    out = normalize_plan(plan)
    assert [(p["label"], p["name"]) for p in out["parts"]] == [("4", "Castors"), ("11", "Screws (M6X12mm)")]
    assert [u["label"] for u in out["steps"][0]["parts_used"]] == ["4", "11"]
    assert normalize_plan(out) == out  # idempotent
    assert plan["parts"][0]["label"] == "(4)"  # input untouched


def test_long_name_labels_are_numbered():
    plan = {"parts": [{"label": "drain hose", "name": "drain hose", "quantity": 1},
                      {"label": "inlet hoses", "name": "inlet hoses", "quantity": 2}],
            "steps": [{"order": 1, "parts_used": [{"label": "inlet hoses", "quantity": 2}, {"label": "Drain Hose", "quantity": 1},
                                                  {"label": "unknown", "quantity": 1}]}]}
    out = normalize_plan(plan)
    assert [p["label"] for p in out["parts"]] == ["1", "2"]
    assert [u["label"] for u in out["steps"][0]["parts_used"]] == ["2", "1"]
    assert normalize_plan(out) == out

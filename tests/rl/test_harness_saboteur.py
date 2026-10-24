"""SaboteurModel: the oracle with exactly one plausible slip, then done() (verifier hard negatives)."""
import random

from scaffold.rl.harness.models import SaboteurModel, _slip

STEPS = [{"op": "click", "role": "button", "name": "New event"},
         {"op": "fill", "role": "textbox", "name": "Event name", "value": "Hiring panel"},
         {"op": "fill", "role": "textbox", "name": "Starts at", "value": "12:30"},
         {"op": "click", "role": "button", "name": "Save"},
         {"op": "done", "text": "Done."}]


def test_one_slip_deterministic_per_variant():
    a, b = SaboteurModel(STEPS, "t1", 0), SaboteurModel(STEPS, "t1", 0)
    assert a.slip == b.slip and a.slip["kind"] == "fill"
    changed = [i for i, (x, y) in enumerate(zip(STEPS, a.steps)) if x != y]
    assert changed == [a.slip["step"]] and a.steps[a.slip["step"]]["value"] != a.slip["from"]
    c = SaboteurModel(STEPS, "t1", 1)                     # odd variant: a skipped (non-final) click
    assert c.slip["kind"] == "skip_click" and len(c.steps) == len(STEPS) - 1
    assert STEPS[1]["value"] == "Hiring panel"           # the shared script is never mutated


def test_slip_values_are_plausible():
    rng = random.Random(0)
    for v in ("Hiring panel", "12:30", "2026-10-28", "Quarterly sync"):
        w = _slip(v, rng)
        assert w and w != v


def test_claims_done_when_the_script_cannot_go_on():
    m = SaboteurModel(STEPS, "t2", 1)
    out = m.decide("sys", "<observation>\nControls (0 total, showing 0-0):\n</observation>")
    assert out == 'done("Done.")'                         # a hurried agent, not needs_user

"""Hermetic tests for the forward stats decision rule (scripts/eval_report.py
decision(), scripts/stats_audit.py). No network, no LLM."""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


er = _load("eval_report")
sa = _load("stats_audit")

DRAWS = 4000


def rows(arm: str, pattern: list[list[bool]], **extra) -> list[dict]:
    """pattern[task][repeat] -> solved."""
    out = []
    for j, reps in enumerate(pattern, start=1):
        for r, s in enumerate(reps, start=1):
            out.append({"arm": arm, "job": j, "repeat": r, "solved": s, "invalid": False,
                        "billed_usd": 0.01, **extra})
    return out


def test_all_better_is_keep():
    res = rows("base", [[False, False]] * 10) + rows("cand", [[True, True]] * 10)
    d = er.decision(res, "base", "cand", draws=DRAWS)
    assert d["label"] == "KEEP"
    assert d["p_pos"] > 0.99
    assert (d["mcnemar_b"], d["mcnemar_c"]) == (10, 0)
    assert d["mcnemar_p"] == pytest.approx(2 * 0.5 ** 10)


def test_all_equal_is_inconclusive():
    res = rows("base", [[True, True]] * 10) + rows("cand", [[True, True]] * 10)
    d = er.decision(res, "base", "cand", draws=DRAWS)
    assert d["label"] == "INCONCLUSIVE"
    assert 0.4 < d["p_pos"] < 0.6
    assert d["mcnemar_p"] == 1.0
    assert d["p_pos_bb"] == 0.5  # ties count half in the plain Bayesian bootstrap


def test_all_worse_is_reject():
    res = rows("base", [[True, True]] * 10) + rows("cand", [[False, False]] * 10)
    d = er.decision(res, "base", "cand", draws=DRAWS)
    assert d["label"] == "REJECT"
    assert d["p_pos"] < 0.01


def test_promising_but_small_is_inconclusive():
    # 3 tasks better, 0 worse, 9 ties: McNemar p = 0.25 -> cannot KEEP
    pat_b = [[False, False]] * 3 + [[True, True]] * 9
    pat_c = [[True, True]] * 3 + [[True, True]] * 9
    d = er.decision(rows("base", pat_b) + rows("cand", pat_c), "base", "cand", draws=DRAWS)
    assert d["label"] == "INCONCLUSIVE"
    assert d["mcnemar_p"] == pytest.approx(0.25)
    assert any("McNemar" in r for r in d["reasons"])


def test_cost_budget_blocks_keep():
    res = rows("base", [[False, False]] * 10) + rows("cand", [[True, True]] * 10)
    for r in res:
        if r["arm"] == "cand":
            r["billed_usd"] = 0.03
    d = er.decision(res, "base", "cand", cost_budget=0.5, draws=DRAWS)
    assert d["cost_ratio"] == pytest.approx(3.0)
    assert d["label"] == "INCONCLUSIVE"
    assert any("budget" in r for r in d["reasons"])
    assert er.decision(res, "base", "cand", cost_budget=2.5, draws=DRAWS)["label"] == "KEEP"


@pytest.mark.parametrize("b,c", [(6, 1), (2, 0), (8, 7), (5, 0), (10, 2)])
def test_mcnemar_hand_computation(b, c):
    # two-sided exact: 2 * sum_{i<=min} C(n, i) / 2^n, capped at 1
    n, m = b + c, min(b, c)
    hand = min(1.0, 2 * sum(math.comb(n, i) for i in range(m + 1)) / 2 ** n)
    assert er.mcnemar_exact(b, c) == pytest.approx(hand, abs=1e-12)


def test_mcnemar_known_values():
    assert er.mcnemar_exact(6, 1) == pytest.approx(16 / 128)   # 0.125 (C2.1, ablation B)
    assert er.mcnemar_exact(2, 0) == pytest.approx(0.5)
    assert er.mcnemar_exact(8, 7) == 1.0


def test_task_level_mcnemar_counts_rate_differences_not_runs():
    table = {1: (0, 2, 1, 2), 2: (1, 2, 2, 2), 3: (2, 2, 1, 2), 4: (1, 2, 1, 2)}
    mc = er.mcnemar_tasks(table)
    assert (mc["b"], mc["c"]) == (2, 1)
    assert mc["p"] == pytest.approx(1.0)


def test_task_table_pools_repeats_and_skips_invalid():
    res = rows("a", [[True, False], [True, True]]) + rows("b", [[True, True], [False, True]])
    res[1]["invalid"] = True  # a, job 1, repeat 2
    # duplicate invalid row for (b, 1, 2) must not displace the valid one
    res.append({"arm": "b", "job": 1, "repeat": 2, "solved": False, "invalid": True})
    t = er.task_table(res, "a", "b")
    assert t == {1: (1, 1, 2, 2), 2: (2, 2, 1, 2)}


def test_posterior_is_deterministic_with_seed():
    table = {1: (0, 2, 2, 2), 2: (1, 2, 1, 2), 3: (2, 2, 1, 2), 4: (0, 3, 2, 3)}
    a = er.p_delta_positive(table, draws=3000, seed=5)
    b = er.p_delta_positive(table, draws=3000, seed=5)
    c = er.p_delta_positive(table, draws=3000, seed=6)
    assert a == b
    assert a["p"] != c["p"]
    assert er.DECIDE_DRAWS >= 10_000


def test_report_has_decision_section(tmp_path):
    res = rows("cand", [[True, True]] * 4) + rows("base", [[False, True]] * 4)
    for r in res:
        r.update(turns=3, hidden_passed=1, hidden_failed=0, id=f"t{r['job']}")
    src = tmp_path / "combined.json"
    src.write_text(json.dumps({"series": "x", "arms": ["cand", "base"], "results": res}))
    md = er.write_report(src, tmp_path / "out").read_text()
    assert "## Decision (stats rule)" in md
    assert "candidate `cand` vs base `base`" in md


def test_stats_audit_cli(tmp_path, capsys):
    res = rows("base", [[False, False]] * 10) + rows("cand", [[True, True]] * 10)
    src = tmp_path / "combined.json"
    src.write_text(json.dumps({"arms": ["cand", "base"], "results": res}))
    assert sa.main([str(src), "--a", "base", "--b", "cand", "--draws", "2000"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Decision (stats rule): KEEP")


def test_power_grows_with_tasks():
    small = sa.power_mcnemar(28, 2, 0.2, sims=300)
    large = sa.power_mcnemar(100, 2, 0.2, sims=300)
    assert large > small
    assert sa.power_mcnemar(28, 2, 0.0, sims=300) < 0.06

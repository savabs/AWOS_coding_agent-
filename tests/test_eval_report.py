"""Hermetic tests for scripts/eval_report.py (no network, no LLM)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("eval_report", REPO / "scripts" / "eval_report.py")
er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(er)


# ── intervals ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("k,n,lo,hi", [
    (5, 10, 0.2366, 0.7634),
    (0, 10, 0.0, 0.2775),
    (7, 12, 0.3195, 0.8067),
    (10, 10, 0.7225, 1.0),
])
def test_wilson_known_values(k, n, lo, hi):
    got = er.wilson(k, n)
    assert got[0] == pytest.approx(lo, abs=1e-4)
    assert got[1] == pytest.approx(hi, abs=1e-4)


@pytest.mark.parametrize("k,n,lo,hi", [
    (5, 10, 0.1871, 0.8129),
    (0, 10, 0.0, 0.3085),   # 1 - 0.025 ** (1/10)
    (10, 10, 0.6915, 1.0),
    (1, 20, 0.00127, 0.2487),
])
def test_clopper_pearson_known_values(k, n, lo, hi):
    got = er.clopper_pearson(k, n)
    assert got[0] == pytest.approx(lo, abs=1e-4)
    assert got[1] == pytest.approx(hi, abs=1e-4)


def test_intervals_handle_empty_sample():
    assert er.wilson(0, 0) == (0.0, 1.0)
    assert er.clopper_pearson(0, 0) == (0.0, 1.0)


# ── exact tests ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("b,c,p", [
    (3, 0, 0.25), (0, 3, 0.25), (6, 0, 0.03125), (8, 1, 0.0390625),
    (10, 2, 0.0385742), (0, 0, 1.0), (2, 2, 1.0), (1, 2, 1.0),
])
def test_mcnemar_exact(b, c, p):
    assert er.mcnemar_exact(b, c) == pytest.approx(p, abs=1e-6)


def test_sign_flip_exact_matches_mcnemar_for_binary_diffs():
    diffs = [1, 1, 1, 0, 0, 0]
    p, how = er.sign_flip_p(diffs)
    assert how == "exact"
    assert p == pytest.approx(er.mcnemar_exact(3, 0))
    assert er.sign_flip_p([1, 1, 1, -1])[0] == pytest.approx(10 / 16)
    assert er.sign_flip_p([0, 0]) == (1.0, "exact")


def test_sign_flip_sampled_is_deterministic():
    diffs = [0.1 * (i % 7) - 0.2 for i in range(20)]  # 2**17+ patterns -> sampled
    p1, how = er.sign_flip_p(diffs, samples=2000)
    p2, _ = er.sign_flip_p(diffs, samples=2000)
    assert how == "2000 samples"
    assert p1 == p2
    assert 0 < p1 <= 1


def test_bootstrap_is_deterministic_and_brackets_mean():
    diffs = [0.0, 1.0, 0.5, 0.0, 1.0, 1.0, 0.0, 0.5]
    a = er.bootstrap_ci(diffs, n_boot=2000)
    b = er.bootstrap_ci(diffs, n_boot=2000)
    assert a == b
    m = sum(diffs) / len(diffs)
    assert a[0] <= m <= a[1]
    assert er.bootstrap_ci([]) is None
    assert er.bootstrap_ci([0.3] * 5) == (pytest.approx(0.3), pytest.approx(0.3))


def test_mde_and_pass_hat_k():
    # 10 tasks: 3 discordant one way -> diffs of +1 x3, 0 x7
    d = [1.0] * 3 + [0.0] * 7
    import statistics
    assert er.mde(d) == pytest.approx(2.8 * (statistics.variance(d) / 10) ** 0.5)
    assert er.mde([1.0]) is None
    assert er.pass_hat_k(3, 3, 3) == 1.0
    assert er.pass_hat_k(2, 3, 3) == 0.0
    assert er.pass_hat_k(2, 4, 2) == pytest.approx(1 / 6)


# ── synthetic results ────────────────────────────────────────────────────────

def _row(arm, job, solved, *, rep=None, invalid=False, billed=0.01, cost=0.008, turns=10,
         failing=None):
    r = {"arm": arm, "job": job, "id": f"{job:02d}_task", "solved": solved,
         "hidden_passed": 3 if solved else 1, "hidden_failed": 0 if solved else 2,
         "failing_tests": failing if failing is not None else ([] if solved else
                                                               [f"tests/t{job}.py::test_x"]),
         "visible_tests_ok": True, "visible_failing": [], "turns": turns,
         "cost_usd": cost, "billed_usd": billed, "minutes": 1.5,
         "invalid": invalid, "invalid_reason": "boom" if invalid else None}
    if rep is not None:
        r["repeat"] = rep
    return r


def _write(tmp_path, results, arms, **extra):
    p = tmp_path / "job_series_X.json"
    p.write_text(json.dumps({"series": "synth", "timestamp": "X", "arms": arms,
                             "model_pin": {"model": "m/pinned"}, "results": results, **extra}))
    return p


def test_paired_exclusion_drops_invalid_job_from_all_arms():
    res = [_row("off", 1, True), _row("on", 1, True),
           _row("off", 2, False), _row("on", 2, True, invalid=True),
           _row("off", 3, False), _row("on", 3, True)]
    drop = er.dropped(res, ["off", "on"])
    assert list(drop) == [(1, 2)]
    c = er.paired(res, "off", "on", 1)
    assert c["units"] == 2 and c["tasks"] == 2
    assert (c["n_b"], c["n_c"]) == (0, 1)
    assert c["solve_a"] == 1 and c["solve_b"] == 2
    # a third arm's invalid row drops the job from the 3-arm per-arm stats only
    res3 = res + [_row("aider", 1, True), _row("aider", 2, True), _row("aider", 3, True, invalid=True)]
    assert set(er.dropped(res3, ["off", "on", "aider"])) == {(1, 2), (1, 3)}
    assert er.paired(res3, "off", "on", 1)["units"] == 2


def test_paired_with_repeats_uses_task_level_tests():
    res = []
    for rep in (1, 2, 3):
        for job in range(1, 6):
            res.append(_row("off", job, job <= 2, rep=rep, billed=0.02, turns=20))
            res.append(_row("on", job, job <= 4, rep=rep, billed=0.01, turns=12))
    c = er.paired(res, "on", "off", 3)
    assert c["tasks"] == 5 and c["units"] == 15
    assert c["diff"] == pytest.approx(0.4)
    assert c["test"].startswith("paired sign-flip permutation (exact")
    assert c["p"] == pytest.approx(0.5)  # 2 nonzero task diffs: ++ and -- of 4 patterns
    assert c["ci"] == er.paired(res, "on", "off", 3)["ci"]
    assert c["usd"]["diff"] == pytest.approx(-0.01)
    assert c["turns"]["diff"] == pytest.approx(-8)
    s = er.arm_stats([r for r in res if r["arm"] == "on"], 3)
    assert s["pass1"] == pytest.approx(0.8) and s["pass_k"] == pytest.approx(0.8)


def test_billed_falls_back_to_cost_with_note(tmp_path):
    res = [_row("off", 1, True, billed=None, cost=0.5), _row("on", 1, True)]
    md = er.write_report(_write(tmp_path, res, ["off", "on"]), tmp_path / "out").read_text()
    assert "`off`: 1 row(s) had no reconciled billing" in md
    assert "$0.5000*" in md


def test_health_banner_valid_and_invalid(tmp_path):
    res = [_row("off", 1, True), _row("on", 1, False)]
    src = _write(tmp_path, res, ["off", "on"])
    out = tmp_path / "out"
    md = er.write_report(src, out).read_text()
    assert "Health: UNKNOWN" in md
    (tmp_path / "health.json").write_text(json.dumps({"ok": True, "violations": [
        {"severity": "warn", "check": "slow", "arm": "on", "job": 1, "repeat": 1, "detail": "x"}]}))
    md = er.write_report(src, out).read_text()
    assert "**VALID**" in md and "0 fatal, 1 other" in md
    (tmp_path / "health.json").unlink()
    out.mkdir(exist_ok=True)
    (out / "health.json").write_text(json.dumps({"ok": False, "violations": [
        {"severity": "fatal", "check": "empty_output", "arm": "on", "job": 1, "repeat": 1,
         "detail": "call 3 returned nothing"}]}))
    md = er.write_report(src, out).read_text()
    first = md.split("## Run")[0]
    assert "**INVALID**" in first and "empty_output" in first and "call 3 returned nothing" in first


def test_end_to_end_report(tmp_path):
    res = []
    for job in range(1, 9):
        res.append(_row("off", job, job <= 3, billed=0.03, turns=25))
        res.append(_row("on", job, job <= 7, billed=0.02, turns=15))
        res.append(_row("aider", job, job != 5, billed=0.002, turns=2, invalid=(job == 8)))
    src = _write(tmp_path, res, ["off", "on", "aider"], git_sha="abc123")
    out = tmp_path / "rep"
    path = er.write_report(src, out)
    md = path.read_text()
    assert path == out / "report.md"
    assert (out / "pareto.svg").read_text().startswith("<svg")
    assert "log scale" in (out / "pareto.svg").read_text()  # $0.002..$0.03 spans >10x
    assert "git_sha: `abc123`" in md
    assert "j8 — aider: boom" in md
    assert "| `off` | 3/7 |" in md and "| `on` | 7/7 |" in md
    # on vs off on all 8 jobs (aider's invalid j8 doesn't matter to this pair)
    assert "### `off` vs `on` — 8 tasks, 8 paired runs" in md
    assert "b = 0, c = 4 → exact McNemar p = 0.1250" in md
    assert "**Not significant at 0.05.**" in md
    assert "| 5 | `05_task` |" in md and "invalid" in md
    assert "`tests/t5.py::test_x`" in md
    # CLI
    cli_out = tmp_path / "cli"
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "eval_report.py"), str(src),
                           str(cli_out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert (cli_out / "report.md").read_text() == md.replace(str(out), str(cli_out))


def test_significant_comparison_is_called_significant(tmp_path):
    res = []
    for job in range(1, 9):
        res.append(_row("off", job, False))
        res.append(_row("on", job, job <= 7))
    md = er.write_report(_write(tmp_path, res, ["off", "on"]), tmp_path / "o").read_text()
    assert "b = 0, c = 7 → exact McNemar p = 0.0156" in md
    assert "**Significant at 0.05.**" in md

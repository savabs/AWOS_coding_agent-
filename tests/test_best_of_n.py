"""
Ablation A: best-of-N first attempts (scaffold/agent/best_of_n.py,
docs/specs/ablation_best_of_n.md). No network: fake OpenAI-shaped clients;
candidates are sampled in a synchronous pool so their order is fixed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_acceptance import FIX, GREEN, _orchestrator, _project  # noqa: E402
from test_acceptance_v2 import (  # noqa: E402
    FAIL2, GREEN_CALC, SUITE_SRC, _LoopSpy, _SeqClient, _SideEffectScripted,
)

from scaffold.agent import best_of_n as bon  # noqa: E402
from scaffold.agent.acceptance import AcceptanceSuite  # noqa: E402
from scaffold.agent.agent_loop import ModelReply  # noqa: E402
from scaffold.agent.test_runner import TestResult  # noqa: E402

RED = TestResult(passed=2, failed=1, errors=0, pass_rate=0.66, raw_output="1 failed",
                 no_tests_found=False)
RED2 = TestResult(passed=1, failed=2, errors=0, pass_rate=0.33, raw_output="2 failed",
                  no_tests_found=False)

WRONG = FIX.replace("return a + b", "return a * b")          # applies, tests red
WORSE = FIX.replace("return a + b", "return b - a - 0")      # applies, tests redder
GARBAGE = "I am not sure what to change here."              # no blocks
MISS = """pkg/calc.py
<<<<<<< SEARCH
def nothing_like_this():
    pass
=======
def x():
    pass
>>>>>>> REPLACE
"""
# Green, with a larger diff than FIX (an extra helper).
FIX_BIG = FIX.replace("    return a + b\n", "    return a + b\n\n\ndef _unused():\n    return 0\n")
# A loser that also creates a new file.
WRONG_NEW = WRONG + """
pkg/junk.py
<<<<<<< SEARCH
=======
JUNK = 1
>>>>>>> REPLACE
"""


class _SyncPool:
    """ThreadPoolExecutor stand-in: runs each job at submit, in order."""

    def __init__(self, max_workers=None):
        self.max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def submit(self, fn, *a, **kw):
        f = Future()
        f.set_result(fn(*a, **kw))
        return f


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")
    monkeypatch.setattr(bon, "ThreadPoolExecutor", _SyncPool)


def _content_runner(root_holder=None):
    """TestRunner mock: green when calc.add adds, else red (by the file on disk)."""
    runner = MagicMock()

    def _make(project_root=None, sandbox=None):
        inst = MagicMock()

        def run(changed_files=None):
            src = (Path(project_root) / "pkg" / "calc.py").read_text()
            if "return a + b" in src:
                return GREEN
            return RED2 if "b - a" in src else RED
        inst.run.side_effect = run
        return inst
    runner.side_effect = _make
    return runner


def _judge_for(root: Path):
    def judge(applied):
        src = (root / "pkg" / "calc.py").read_text()
        if "return a + b" in src:
            tr = GREEN
        else:
            tr = RED2 if "b - a" in src else RED
        return {"success": True, "test_result": tr, "test_status": "x", "error": "",
                "summary": "s"}
    return judge


def _bon(root, replies, n=3, acceptance=None, judge=None):
    client = _SeqClient(replies)
    res = bon.run_best_of_n(n, client=client, model="deepseek/deepseek-chat",
                            codebase_root=str(root), task_text="fix add in pkg/calc.py",
                            exploration={}, tracker=None, allow_test_edits=False,
                            judge=judge or _judge_for(root), acceptance_passed=acceptance)
    return res, client


def _git_project() -> Path:
    root = _project()

    def git(*a):
        subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return root


# ── knob ────────────────────────────────────────────────────────────────────

def test_knob_parsing(monkeypatch):
    monkeypatch.delenv("AWOS_BEST_OF_N", raising=False)
    assert bon.best_of_n() == 1
    for raw, want in (("3", 3), ("0", 1), ("x", 1), ("99", bon.MAX_N)):
        monkeypatch.setenv("AWOS_BEST_OF_N", raw)
        assert bon.best_of_n() == want
    monkeypatch.delenv("AWOS_BEST_OF_N_TEMPERATURE", raising=False)
    assert bon.temperature() == 0.7


# ── selection ───────────────────────────────────────────────────────────────

def test_picks_green_over_red(capsys):
    root = _project()
    res, client = _bon(root, [WRONG, FIX, WORSE])
    assert res.chosen == 2
    assert (root / "pkg" / "calc.py").read_text() == GREEN_CALC
    assert res.verdict["test_result"] is GREEN
    assert all(c["temperature"] == 0.7 for c in client.calls)
    log = capsys.readouterr().out
    assert "[BEST-OF-N] 1/3 green; chose #2" in log
    assert "[BEST-OF-N] #1:" in log and "red" in log and "GREEN" in log


def test_tie_break_smallest_diff_then_earliest():
    root = _project()
    res, _ = _bon(root, [FIX_BIG, FIX, FIX])
    assert res.chosen == 2                       # smaller diff than #1; earlier than #3
    assert (root / "pkg" / "calc.py").read_text() == GREEN_CALC


def test_tie_break_fewest_failures_when_none_green():
    root = _project()
    res, _ = _bon(root, [WORSE, WRONG, GARBAGE])
    assert res.chosen == 2                       # 1 failure beats 2
    assert "return a * b" in (root / "pkg" / "calc.py").read_text()
    assert res.verdict["test_result"] is RED


def test_acceptance_passed_ranks_before_visible_failures():
    root = _project()
    acc = iter([2, 0])        # #1 (WORSE) passes 2 acceptance tests, #2 (WRONG) passes 0
    res, _ = _bon(root, [WORSE, WRONG], n=2, acceptance=lambda: next(acc))
    assert res.chosen == 1
    assert [c.acc_passed for c in res.candidates] == [2, 0]


def test_score_order_unit():
    def c(i, *, files=True, green=False, clean=True, bad=1, acc=None, diff=1):
        x = bon.Candidate(index=i, files={"a": b""} if files else {}, tests_green=green,
                          clean=clean, bad_tests=bad, acc_passed=acc, diff_lines=diff)
        return x
    ranked = sorted([
        c(1, files=False),
        c(2, bad=3),
        c(3, bad=1),
        c(4, green=True, clean=False, bad=0),
        c(5, green=True, bad=0, diff=9),
        c(6, green=True, bad=0, diff=2),
    ], key=lambda x: x.score(), reverse=True)
    assert [x.index for x in ranked] == [6, 5, 4, 3, 2, 1]


# ── isolation ───────────────────────────────────────────────────────────────

def test_losers_leave_no_files():
    root = _git_project()
    res, _ = _bon(root, [WRONG_NEW, FIX, WRONG_NEW])
    assert res.chosen == 2
    assert not (root / "pkg" / "junk.py").exists()
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                            cwd=root, capture_output=True, text=True).stdout
    assert status.splitlines() == [" M pkg/calc.py"]
    assert (root / "pkg" / "calc.py").read_text() == GREEN_CALC


def test_losers_leave_no_files_walk_mode():
    root = _project()
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
                    and "__pycache__" not in p.parts)
    _bon(root, [WRONG_NEW, FIX, WRONG_NEW])
    after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
                   and "__pycache__" not in p.parts)
    assert after == before


def test_malformed_and_failed_candidates_only_lose(capsys):
    root = _project()
    # GARBAGE: no blocks; MISS: SEARCH misses, its repair reply ("{}") has none.
    res, _ = _bon(root, [GARBAGE, MISS, "{}", FIX])
    assert res.chosen == 3
    assert (root / "pkg" / "calc.py").read_text() == GREEN_CALC
    log = capsys.readouterr().out
    assert "#1:" in log and "loser: no edit applied" in log


def test_all_fail_leaves_workspace_untouched():
    root = _project()
    orig = (root / "pkg" / "calc.py").read_text()
    res, _ = _bon(root, [GARBAGE, GARBAGE, GARBAGE])
    assert res.shot.applied == [] and res.verdict is None
    assert (root / "pkg" / "calc.py").read_text() == orig


def test_costs_and_tokens_summed():
    root = _project()
    res, client = _bon(root, [WRONG, FIX, MISS, "{}"])  # 3 candidates, 4 calls (1 repair)
    assert len(client.calls) == 4
    assert res.shot.calls == 4
    assert res.shot.input_tokens == 4 * 1000 and res.shot.output_tokens == 4 * 50
    assert res.shot.cost_usd == pytest.approx(sum(c.cost_usd for c in res.candidates))
    assert res.candidates[0].cost_usd > 0
    # the repaired candidate paid for two calls
    assert res.candidates[2].cost_usd == pytest.approx(2 * res.candidates[0].cost_usd)


# ── orchestrator ────────────────────────────────────────────────────────────

def _run_orch(replies, *, n="3", acc_mode="0", checks=(), arb=None, root=None):
    root = root or _project()
    orch = _orchestrator()
    orch._run_files_changed = set()
    ctx = {"codebase_root": str(root), "git": MagicMock(), "session": MagicMock(goal="fix add"),
           "codebase_context": {}, "exploration": {}}
    spec = SimpleNamespace(model_id="claude-haiku-4-5", name="Haiku", cost_per_req=0.01,
                           level=SimpleNamespace(value=3))
    scripted = _SideEffectScripted([ModelReply(text="Done.")])
    suite = AcceptanceSuite(source=SUITE_SRC, generated=2, kept=["test_a", "test_b"],
                            cost_usd=0.001)
    check = MagicMock(side_effect=list(checks))
    seq = _SeqClient(list(replies) + ([arb] if arb else []))
    _LoopSpy.inits = []
    env = {"AWOS_ONE_SHOT": "1", "AWOS_ACCEPTANCE": acc_mode, "AWOS_AGENT_RETRIES": "1",
           "AWOS_ACCEPTANCE_REPAIR_TURNS": "3"}
    if n is not None:
        env["AWOS_BEST_OF_N"] = n
    usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    with patch.dict(os.environ, env), \
         patch("scaffold.agent.agent_loop.build_client_from_env", return_value=scripted), \
         patch("scaffold.agent.agent_loop.AgentLoop", _LoopSpy), \
         patch("scaffold.agent.one_shot.one_shot_client", return_value=seq), \
         patch("scaffold.agent.acceptance.build_suite", return_value=suite), \
         patch("scaffold.agent.acceptance.run_acceptance_detail", check), \
         patch("scaffold.agent.orchestrator.TestRunner", _content_runner()):
        out = orch._execute_task_via_agent_loop(
            {"task_id": 1, "action": "fix add in pkg/calc.py", "file": "pkg/calc.py"}, ctx,
            task_id=1, esc_decision=SimpleNamespace(spec=spec), _span=MagicMock(),
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage=usage,
        )
    return SimpleNamespace(out=out, usage=usage, seq=seq, root=root, check=check, suite=suite,
                           scripted=scripted, inits=list(_LoopSpy.inits))


@pytest.mark.parametrize("n", [None, "1"])
def test_n1_is_todays_single_shot(n, capsys):
    with patch.object(bon, "run_best_of_n", side_effect=AssertionError("not at N=1")):
        r = _run_orch([FIX], n=n)
    assert r.out["one_shot"] is True and r.out["success"] is True
    assert len(r.seq.calls) == 1 and r.seq.calls[0]["temperature"] == 0
    assert "[BEST-OF-N]" not in capsys.readouterr().out


def test_orchestrator_n3_green_one_shot(capsys):
    r = _run_orch([WRONG, FIX, WORSE])
    log = capsys.readouterr().out
    assert "[BEST-OF-N] 1/3 green; chose #2" in log
    assert "[ONE-SHOT] solved in 1 call" in log
    assert r.out["one_shot"] is True and r.out["success"] is True
    assert r.usage["input_tokens"] == 3 * 1000 and r.usage["output_tokens"] == 3 * 50
    assert r.usage["cost_usd"] > 0
    assert (r.root / "pkg" / "calc.py").read_text() == GREEN_CALC
    assert r.scripted.prompts == []


def test_orchestrator_n3_none_green_falls_back_with_best_partial(capsys):
    r = _run_orch([WORSE, WRONG, GARBAGE])
    log = capsys.readouterr().out
    assert "[BEST-OF-N] 0/3 green; chose #2" in log
    assert "[ONE-SHOT] fell back to the agent loop: tests:" in log
    assert r.scripted.prompts and "pkg/calc.py" in r.scripted.prompts[0]


def test_orchestrator_n3_acceptance_mode2_pending_path(capsys):
    # Scoring: one acceptance check per candidate with edits (3), then the
    # green one-shot's own mode-2 check -> pending -> arbitration (all wrong).
    r = _run_orch([WRONG, FIX, FIX_BIG], acc_mode="2",
                  checks=[FAIL2, FAIL2, FAIL2, FAIL2],
                  arb='{"test_a": "WRONG_TEST", "test_b": "WRONG_TEST"}')
    log = capsys.readouterr().out
    assert "[BEST-OF-N] 2/3 green; chose #2" in log
    assert "[ACCEPTANCE] arbitration: 2 failing -> 2 wrong_test, 0 code_incomplete" in log
    assert r.check.call_count == 4
    assert r.out["success"] is True and r.out["one_shot"] is True
    assert r.scripted.prompts == [] and r.inits == []
    assert (r.root / "pkg" / "calc.py").read_text() == GREEN_CALC

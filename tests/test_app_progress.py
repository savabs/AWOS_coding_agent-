"""The app's log -> friendly progress translator and the job tail."""
from __future__ import annotations

import json

import pytest

from scaffold.agent.app.progress import JobTail, ProgressTranslator, strip_ansi

T = ProgressTranslator()


@pytest.mark.parametrize("raw, want", [
    ("[agent] t3 read_file(calc.py) -> ok 0.0s", "reading calc.py"),
    ("[agent] t2 edit_file(src/app/models.py) -> ok 0.0s", "editing src/app/models.py"),
    ("[agent] t2 write_file(new.py) -> ok 0.0s", "writing new.py"),
    ("[agent] t1 run_tests() -> ok 0.3s", "running tests"),
    ("[agent] t2 run_tests(calc.py) -> ok 0.3s", "running tests"),
    ("[agent] t2 grep(average, .) -> ok 0.0s", "searching for 'average'"),
    ("[agent] t1 list_dir() -> ok 0.0s", "looking around the project"),
    ("[agent] t1 find_files(*.py) -> ok 0.0s", "looking for files"),
    ("[agent] t2 run_command(ls -la) -> ok 0.1s", "running `ls -la`"),
    ("[agent] t3 submit_verdict() -> ok 0.0s", "checking the result"),
    ("[agent] t2 edit_file(calc.py) -> error 0.0s", "editing calc.py (error)"),
    ("[agent] t4 frobnicate(x) -> ok 0.0s", "using frobnicate"),
    ("[ACCEPTANCE] generated 3 tests, kept 3 (failed on start), $0.0002", "writing acceptance tests (3 kept)"),
    ("[ACCEPTANCE] check: 2 kept, 2 passed", "acceptance tests: 2/2 passing"),
    ("[ONE-SHOT] call: 653 in / 62 out tokens, $0.0001, 1.3s; context 116 tokens", "trying a one-shot fix"),
    ("[ONE-SHOT] solved in 1 call — 1 file(s) changed, tests 2 passed", "one-shot fix worked (tests 2 passed)"),
    ("[ONE-SHOT] fell back to the agent loop: no OpenAI-shaped client configured",
     "switching to the step-by-step agent"),
    ("[PLANNER] Generated 3 tasks:", "planned 3 tasks"),
    ("[PLANNER] Generated 1 tasks:", "planned 1 task"),
    ("[PLANNER] skipped: small goal (2 file(s) hit) — running as one task", "small goal: running it as one task"),
    ("[EXPLORE] Pre-plan repository scan...", "scanning the repository"),
    ("[TASK 1] Executing: fix average() so the tests pass", "task 1: fix average() so the tests pass"),
    ("[TASK 1] Tests: 10 passed, 0 failed (pass_rate=100.00%)", "running tests… 10 passed"),
    ("[TASK 1] Tests: 0 passed, 1 failed (pass_rate=0.00%)", "running tests… 0 passed, 1 failed"),
    ("[TASK 1] AGENT FAILED: AgentLoop stopped: max_turns (after 2 attempts)",
     "task 1 failed: AgentLoop stopped: max_turns (after 2 attempts)"),
    ("[agent] stop: max_turns after 3 turns, 5 tool calls", "the agent hit its turn limit"),
    ("[GOAL CHECK] checker model: claude-haiku-5-5", "double-checking the goal"),
    ("[GOAL CHECK] complete [via tool]: the tests pass", "goal check: complete"),
    ("16:34:51 [worker] 7093dc0f running (attempt 1): fix it", "started"),
    ("16:34:51 [worker] 7093dc0f running (attempt 2): fix it", "started again (attempt 2)"),
])
def test_translate(raw, want):
    assert T.translate(raw) == want


@pytest.mark.parametrize("raw", [
    "", "plain text", "[GIT] No git repo — using in-memory file backups",
    "[SYMBOLS] SymbolIndex: 2 files", "[agent] stop: completed after 4 turns, 5 tool calls",
    "│  Tasks    1  success=100%", "[ACCEPTANCE] gate inactive: no client",
    "16:35:04 [worker] 7093dc0f done", "===== child, attempt 1 at 2026 =====",
    "[COMPONENTS] {not json",
])
def test_noise_is_dropped(raw):
    assert T.translate(raw) is None


def test_ansi_is_stripped_first():
    raw = "\x1b[36m[TASK 1]\x1b[0m Tests: 2 passed, 0 failed"
    assert strip_ansi(raw) == "[TASK 1] Tests: 2 passed, 0 failed"
    assert T.translate(raw) == "running tests… 2 passed"


def test_components_summary():
    data = {"acceptance": {"status": "ran"}, "one_shot": {"status": "ran"}, "arbiter": {"status": "not_run"}}
    assert T.translate("[COMPONENTS] " + json.dumps(data)) == "used: acceptance, one-shot"
    assert T.translate("[COMPONENTS] " + json.dumps({"arbiter": {"status": "not_run"}})) is None


def test_agent_says_is_quoted_and_shortened():
    out = T.translate("[agent] t2 says: The bug is clear: `add` subtracts instead of adding. " + "x" * 200)
    assert out.startswith("“The bug is clear") and out.endswith("”") and len(out) < 100


def test_journal_records():
    assert T.translate_journal({"step": "workspace", "payload": {"base_commit": "abcdef0123"}}) == \
        "preparing a private copy of the repo at abcdef01"
    assert T.translate_journal({"step": "verify_tests", "payload": {"passed": 4, "failed": 1}}) == \
        "verifying: tests 4 passed, 1 failed"
    assert T.translate_journal({"step": "child_started", "payload": {"pid": 1}}) is None


def test_job_tail_reads_each_line_once_and_waits_for_partial_lines(tmp_path):
    log = tmp_path / "worker.log"
    tail = JobTail(tmp_path)
    assert tail.poll() == []
    log.write_text("[EXPLORE] Pre-plan repository scan...\n[agent] t1 read_file(a.py) -> ok 0.0s\n[agent] t2 edit")
    assert tail.poll() == ["scanning the repository", "reading a.py"]
    assert tail.poll() == []
    with open(log, "a") as fh:
        fh.write("_file(a.py) -> ok 0.0s\n")
    assert tail.poll() == ["editing a.py"]
    with open(tmp_path / "journal.jsonl", "a") as fh:
        fh.write(json.dumps({"step": "verify_tests", "payload": {"passed": 2, "failed": 0}}) + "\n")
        fh.write("not json\n")
    assert tail.poll() == ["verifying: tests 2 passed"]
    assert tail.last_update == "verifying: tests 2 passed"


def test_job_tail_dedupes_repeats(tmp_path):
    (tmp_path / "worker.log").write_text("[agent] t1 run_tests() -> ok\n[agent] t2 run_tests() -> ok\n")
    assert JobTail(tmp_path).poll() == ["running tests"]

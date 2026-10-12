"""Empty model responses in the Aider harness: detected, retried, then INVALID.

B2 (2026-10-11): 6 of Aider's 12 failures ended with "Empty response received
from LLM" (Haiku 5.5 spent its whole max_tokens on thinking), no retry, $0
recorded, scored "not solved". Hermetic: a fake aider, no network.
"""
from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("harness_aider_empty", REPO / "scripts" / "harness_aider.py")
ha = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ha)

# The exact tail of a B2 log (fresh_pyflakes_868, aider, 2026-10-11).
B2_EMPTY_TAIL = """\
Added setup.py to the chat.

Empty response received from LLM. Check your provider account?



Model anthropic/claude-haiku-5-5 has hit a token limit!
Token counts below are approximate.

Input tokens: ~27,159 of 200,000
Output tokens: ~0 of 128,000
"""

EMPTY_USAGE = {"prompt_tokens": 27159, "completion_tokens": 16384, "cache_hit_tokens": 0,
               "cache_write_tokens": 0, "cost": 0.0, "session_cost": 0.0,
               "openrouter_cost": None, "empty": True, "finish_reason": "length"}


# ── detection ─────────────────────────────────────────────────────────────────


def test_b2_log_without_usage_line_is_an_empty_ending():
    t = ha.UsageTracker()
    for line in B2_EMPTY_TAIL.splitlines():
        t.feed(line)
    assert t.ended_empty and t.empty_responses == 1 and t.token_limit
    assert t.turns == 0
    assert ha.ended_with_empty_response(B2_EMPTY_TAIL)


def test_empty_call_usage_is_recorded_and_billed_but_not_a_turn():
    t = ha.UsageTracker(ha.ANTHROPIC_PRICES["claude-haiku-5-5"])
    t.feed("Empty response received from LLM. Check your provider account?")
    t.feed(ha.USAGE_TAG + json.dumps(EMPTY_USAGE))
    assert t.ended_empty and t.turns == 0
    cost, src = t.cost()
    # 27159 in x $0.10/M + 16384 out x $0.50/M
    assert src == "token_price_table" and cost == pytest.approx(0.0027159 + 0.008192)


def test_a_later_good_response_clears_the_empty_ending():
    t = ha.UsageTracker()
    t.feed("Empty response received from LLM. Check your provider account?")
    t.feed(ha.USAGE_TAG + json.dumps(EMPTY_USAGE))
    t.feed(ha.USAGE_TAG + json.dumps({**EMPTY_USAGE, "empty": False, "finish_reason": None}))
    assert not t.ended_empty and t.turns == 1


def test_old_text_logs_empty_then_tokens_line():
    t = ha.UsageTracker()
    t.feed("Empty response received from LLM. Check your provider account?")
    t.feed("Tokens: 2.3k sent, 0 received. Cost: $0.00 message, $0.00 session.")
    assert t.ended_empty
    t.feed("Tokens: 2.5k sent, 300 received. Cost: $0.01 message, $0.01 session.")
    assert not t.ended_empty


def test_only_the_last_attempt_decides():
    log = (ha.ATTEMPT_TAG + "1/3\n" + B2_EMPTY_TAIL + ha.ATTEMPT_TAG + "2/3\n"
           + "Tokens: 2.3k sent, 150 received.\nApplied edit to a.py\n")
    assert not ha.ended_with_empty_response(log)
    assert ha.ended_with_empty_response(log + ha.ATTEMPT_TAG + "3/3\n" + B2_EMPTY_TAIL)
    # A killed attempt is the kill's story, not an empty response.
    assert not ha.ended_with_empty_response(B2_EMPTY_TAIL + "[harness_aider] hit the 870s limit\n")


def test_no_edit_reason_empty_is_a_setup_reason():
    assert ha.no_edit_reason({"edited": [], "in_chat": ["a.py"]}, [], True,
                             preloaded=True, empty=True) == ha.EMPTY_REASON
    assert ha.EMPTY_REASON in ha.NO_EDIT_SETUP_REASONS
    # Edits win: no no-edit reason (the invalid_run line covers the empty ending).
    assert ha.no_edit_reason({"edited": ["a.py"]}, [], False, empty=True) is None


def test_aiders_own_banner_is_not_the_model_asking_for_files():
    banner = "Warning: it's best to only add files that need changes to the chat."
    assert not ha.asked_to_add_files(banner)
    assert not ha.asked_to_add_files(banner + "\nhttps://aider.chat/docs/troubleshooting/edit-errors.html")
    t = ha.UsageTracker()
    t.feed(banner)
    t.feed("https://aider.chat/docs/troubleshooting/edit-errors.html")
    assert not t.asked_to_add
    # A real ask next to it still counts.
    assert ha.asked_to_add_files(banner + "\nPlease add pkg/core.py to the chat.")


# ── main(): retry, reset, invalid ─────────────────────────────────────────────

FAKE = r'''
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("aider 0.0.fake"); sys.exit(0)
counter = os.environ["FAKE_COUNTER"]
n = int(open(counter).read()) if os.path.exists(counter) else 0
open(counter, "w").write(str(n + 1))
# Every attempt scribbles on the project first: a retry must start clean.
open("scratch_from_attempt.txt", "w").write(str(n))
with open("a.py", "a") as f:
    f.write("# attempt %d\n" % n)
if n < int(os.environ.get("FAKE_EMPTY_TIMES", "0")):
    print("Empty response received from LLM. Check your provider account?", flush=True)
    print("[aider-usage] " + json.dumps({"prompt_tokens": 27000, "completion_tokens": 16384,
          "cache_hit_tokens": 0, "cache_write_tokens": 0, "cost": 0.0, "session_cost": 0.0,
          "openrouter_cost": None, "empty": True, "finish_reason": "length"}), flush=True)
    print("Model anthropic/claude-haiku-5-5 has hit a token limit!", flush=True)
    print("[aider-final] " + json.dumps({"edited": [], "in_chat": ["a.py"]}), flush=True)
    sys.exit(0)
print("[aider-usage] " + json.dumps({"prompt_tokens": 20000, "completion_tokens": 500,
      "cache_hit_tokens": 0, "cache_write_tokens": 0, "cost": 0.0, "session_cost": 0.0,
      "openrouter_cost": None, "empty": False}), flush=True)
print("Applied edit to a.py", flush=True)
print("[aider-final] " + json.dumps({"test_outcome": True, "edited": ["a.py"],
      "in_chat": ["a.py"]}), flush=True)
'''


def _git(project: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(project), "-c", "user.email=t@t", "-c", "user.name=t",
                           *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def setup(tmp_path, monkeypatch):
    fake = tmp_path / "fake_aider.py"
    fake.write_text(FAKE)
    monkeypatch.setenv("AWOS_AIDER_CMD", f"{shlex.quote(sys.executable)} {shlex.quote(str(fake))}")
    monkeypatch.setenv("FAKE_COUNTER", str(tmp_path / "counter"))
    monkeypatch.setenv("AWOS_AIDER_RETRY_BACKOFF_S", "0")
    monkeypatch.delenv("AWOS_AIDER_EMPTY_RETRIES", raising=False)
    monkeypatch.delenv("AWOS_JOB_MAX_COST_USD", raising=False)
    job = tmp_path / "job"
    job.mkdir()
    (job / "task.json").write_text(json.dumps({"id": "j", "goal": "fix it", "max_cost_usd": 1.0,
                                               "timeout_min": 15}))
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n")
    _git(project, "init", "-q")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "job 1 start")
    state = tmp_path / "state"
    state.mkdir()
    (state / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-FAKE\n")
    monkeypatch.chdir(state)
    return job, project, state


def _main(job, project):
    return ha.main([str(job), str(project), "--model", "claude-haiku-5-5", "--add-files", "none"])


def test_empty_then_ok_is_retried_on_a_clean_tree(setup, monkeypatch, capsys):
    job, project, state = setup
    monkeypatch.setenv("FAKE_EMPTY_TIMES", "1")
    assert _main(job, project) == 0
    report = json.loads((state / "report.json").read_text())
    out = capsys.readouterr().out
    assert report["invalid_reason"] is None and report["empty_response"] is False
    assert report["success"] is True and report["attempts"] == 2
    assert report["usage"]["turns"] == 1
    [f] = report["failed_attempts"]
    assert f["reason"] == ha.EMPTY_REASON and f["output_tokens"] == 16384 and f["usage_recorded"]
    assert report["retry_cost_usd"] == pytest.approx(27000 * 0.1e-6 + 16384 * 0.5e-6, abs=1e-6)
    # The retry started from the job's commit: only attempt 2's change is left.
    assert (project / "a.py").read_text() == "x = 1\n# attempt 1\n"
    assert (project / "scratch_from_attempt.txt").read_text() == "1"
    assert "empty_model_response on attempt 1" in out and "attempt 2/3" in out
    assert ha.INVALID_TAG not in out


def test_always_empty_is_invalid_never_unsolved(setup, monkeypatch, capsys):
    job, project, state = setup
    monkeypatch.setenv("FAKE_EMPTY_TIMES", "99")
    _main(job, project)
    report = json.loads((state / "report.json").read_text())
    out = capsys.readouterr().out
    assert report["invalid_reason"] == ha.EMPTY_REASON and report["empty_response"] is True
    assert report["success"] is False
    assert report["attempts"] == 3 and len(report["failed_attempts"]) == 2
    assert report["aider_no_edit"] == ha.EMPTY_REASON
    # Billed even though Aider never printed a cost: 3 x (27k in + 16k out).
    per_call = 27000 * 0.1e-6 + 16384 * 0.5e-6
    assert report["usage"]["cost_usd"] == pytest.approx(per_call, abs=1e-6)
    assert report["retry_cost_usd"] == pytest.approx(2 * per_call, abs=1e-6)
    assert f"{ha.INVALID_TAG}{ha.EMPTY_REASON} attempts=3" in out
    assert int(Path(__import__("os").environ["FAKE_COUNTER"]).read_text()) == 3


def test_retries_are_bounded_by_env(setup, monkeypatch):
    job, project, state = setup
    monkeypatch.setenv("FAKE_EMPTY_TIMES", "99")
    monkeypatch.setenv("AWOS_AIDER_EMPTY_RETRIES", "0")
    _main(job, project)
    report = json.loads((state / "report.json").read_text())
    assert report["attempts"] == 1 and report["invalid_reason"] == ha.EMPTY_REASON


def test_dirty_project_is_not_reset_and_not_retried(setup, monkeypatch, capsys):
    job, project, state = setup
    (project / "untracked.txt").write_text("keep me")
    monkeypatch.setenv("FAKE_EMPTY_TIMES", "99")
    _main(job, project)
    report = json.loads((state / "report.json").read_text())
    assert report["attempts"] == 1 and report["invalid_reason"] == ha.EMPTY_REASON
    assert (project / "untracked.txt").read_text() == "keep me"
    assert "not retrying: the project can't be reset" in capsys.readouterr().out


def test_series_budget_lowers_the_job_cap(monkeypatch):
    monkeypatch.setenv("AWOS_JOB_MAX_COST_USD", "0.04")
    assert ha.job_cost_cap(1.0) == 0.04
    assert ha.job_cost_cap(0.02) == 0.02
    monkeypatch.setenv("AWOS_JOB_MAX_COST_USD", "junk")
    assert ha.job_cost_cap(1.0) == 1.0


def test_launcher_records_usage_of_truncated_calls():
    # The launcher hooks the call Aider skips (FinishReasonLength) and flags empties.
    assert "FinishReasonLength" in ha.LAUNCHER and '"empty"' in ha.LAUNCHER
    compile(ha.LAUNCHER, "<launcher>", "exec")

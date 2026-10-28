"""
One-shot observability: failed SEARCH/REPLACE blocks are logged one line each
and every attempt is persisted as a JSON record with a failure category.
Fakes only: no model calls, no real .awos/ writes (records go to tmp_path).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scaffold.agent import one_shot as one_shot_mod  # noqa: E402
from scaffold.agent.one_shot import (  # noqa: E402
    NO_NET_CHANGE_REASON, NO_OP_REASON, EditBlock, OneShotResult, apply_blocks,
    classify_failure, failure_log_lines, one_shot_record, write_one_shot_record,
)
from scaffold.agent.orchestrator import Orchestrator  # noqa: E402


# ── classification ───────────────────────────────────────────────────────────

def test_classify_known_reasons():
    assert classify_failure("SEARCH block did not match any content in the file.\n\n...") == "no_match"
    assert classify_failure("File not found: /x/y.py. Use list_dir ...") == "no_match"
    assert classify_failure("after repair: SEARCH block did not match any content") == "no_match"
    assert classify_failure("old_string appears 3 times in a.py; the edit would be "
                            "ambiguous. Include more surrounding context") == "ambiguous_match"
    assert classify_failure(NO_OP_REASON) == "no_op"
    assert classify_failure(NO_NET_CHANGE_REASON) == "no_op"
    assert classify_failure("path outside the project root") == "outside_root"
    assert classify_failure("edits under .git/.awos are not allowed") == "outside_root"
    assert classify_failure("read-only test file") == "read_only"
    assert classify_failure("tests/test_a.py is a test file and tests are read-only for "
                            "this task.") == "read_only"
    assert classify_failure("Syntax error after applying change:\nboom") == "other"
    assert classify_failure("reply cut off at 16384 tokens; any changes ...") == "other"
    assert classify_failure("malformed block: no ======= divider") == "other"
    assert classify_failure(None) == "other"


def test_classify_real_apply_blocks_reasons(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 1\ny = 1\n", encoding="utf-8")
    blocks = [
        EditBlock("a.py", "z = 9\n", "z = 10\n"),         # no match
        EditBlock("a.py", "y = 1\n", "y = 2\n"),          # ambiguous
        EditBlock("a.py", "x = 1\n", "x = 1\n"),          # no-op
        EditBlock("../outside.py", "a\n", "b\n"),         # outside root
    ]
    _, failed = apply_blocks(str(tmp_path), blocks)
    cats = [classify_failure(f["reason"]) for f in failed]
    assert cats == ["no_match", "ambiguous_match", "no_op", "outside_root"]


# ── log lines ────────────────────────────────────────────────────────────────

def test_failure_log_lines_format_and_cap():
    failed = [{"path": f"f{i}.py", "reason": "SEARCH block did not match\n\n" + "x" * 400,
               "index": i} for i in range(10)]
    failed.append({"path": "?", "reason": "reply cut off"})
    lines = failure_log_lines(failed)
    assert len(lines) == 9
    assert lines[0].startswith("[ONE-SHOT] block 0 f0.py: SEARCH block did not match x")
    assert "\n" not in lines[0]
    assert len(lines[0].split(": ", 1)[1]) == 160
    assert lines[-1] == "[ONE-SHOT] ... 3 more"
    assert failure_log_lines([{"path": "?", "reason": "r"}]) == ["[ONE-SHOT] block - ?: r"]


# ── record ───────────────────────────────────────────────────────────────────

def _failed_shot():
    return OneShotResult(
        failed=[{"path": "a.py", "reason": "SEARCH block did not match ...",
                 "search": "s" * 900, "index": 0},
                {"path": "a.py", "reason": "after repair: old_string appears 2 times in a.py; "
                 "the edit would be ambiguous."}],
        blocks=2, input_tokens=100, output_tokens=50, cost_usd=0.01, calls=2,
        reply="R" * 70000, repair_calls=1, repair_reply="fix", repair_failed=1,
    )


def test_record_contents_and_caps():
    rec = one_shot_record(_failed_shot(), model="m", task_id=3)
    assert rec["model"] == "m" and rec["task_id"] == "3"
    assert rec["input_tokens"] == 100 and rec["output_tokens"] == 50
    assert [f["category"] for f in rec["failed"]] == ["no_match", "ambiguous_match"]
    assert rec["failure_categories"] == {"no_match": 1, "ambiguous_match": 1}
    assert len(rec["failed"][0]["search"]) == 600
    assert rec["failed"][0]["index"] == 0 and rec["failed"][1]["index"] is None
    assert len(rec["reply"]) == 60000 and rec["reply_chars"] == 70000
    assert rec["repair_reply"] == "fix" and rec["repair_calls"] == 1
    assert rec["truncated"] is False


def test_write_record_and_env_off(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_ONE_SHOT_LOG_DIR", str(tmp_path / "os"))
    p1 = write_one_shot_record(_failed_shot(), model="m", task_id="t/1")
    p2 = write_one_shot_record(_failed_shot(), model="m", task_id="t/1")
    assert p1 and p2 and p1 != p2 and p1.parent == tmp_path / "os"
    assert p1.name.startswith("t_1-")
    assert json.loads(p1.read_text())["failed"][0]["category"] == "no_match"
    monkeypatch.setenv("AWOS_ONE_SHOT_LOG_DIR", "off")
    assert write_one_shot_record(_failed_shot()) is None


def test_default_dir_off_under_pytest(monkeypatch):
    monkeypatch.delenv("AWOS_ONE_SHOT_LOG_DIR", raising=False)
    assert one_shot_mod.one_shot_log_dir() is None


def test_write_error_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("AWOS_ONE_SHOT_LOG_DIR", str(blocker / "sub"))  # mkdir fails
    assert write_one_shot_record(_failed_shot()) is None
    with patch.object(one_shot_mod, "one_shot_record", side_effect=RuntimeError("boom")):
        monkeypatch.setenv("AWOS_ONE_SHOT_LOG_DIR", str(tmp_path / "ok"))
        assert write_one_shot_record(_failed_shot()) is None


# ── orchestrator wiring ──────────────────────────────────────────────────────

def _orch():
    orch = Orchestrator.__new__(Orchestrator)
    orch.tracker = None
    return orch


def _run_first(shot, root):
    with patch("scaffold.agent.one_shot.one_shot_enabled", return_value=True), \
         patch("scaffold.agent.one_shot.one_shot_client", return_value=object()), \
         patch("scaffold.agent.one_shot.run_one_shot", return_value=shot), \
         patch("scaffold.agent.best_of_n.best_of_n", return_value=1), \
         patch.object(Orchestrator, "_component", lambda *a, **k: None, create=True), \
         patch.object(Orchestrator, "_component_add", lambda *a, **k: None, create=True):
        return _orch()._one_shot_first(
            {"action": "fix it"}, {"exploration": {}}, "m", str(root), None, "job7", True)


def test_orchestrator_logs_and_persists_failed_blocks(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AWOS_ONE_SHOT_LOG_DIR", str(tmp_path / "rec"))
    shot = OneShotResult(
        failed=[{"path": "a.py", "reason": f"SEARCH block did not match {i}", "index": i,
                 "search": "q"} for i in range(4)],
        blocks=4, calls=1, reply="the raw reply")
    out = _run_first(shot, tmp_path)
    assert out["solved"] is False and out["applied"] == []
    text = capsys.readouterr().out
    for i in range(4):
        assert f"[ONE-SHOT] block {i} a.py: SEARCH block did not match {i}" in text
    assert "fell back to the agent loop: no edit applied (4 block(s) failed)" in text
    files = list((tmp_path / "rec").glob("job7-*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert rec["model"] == "m" and rec["reply"] == "the raw reply"
    assert rec["failure_categories"] == {"no_match": 4}


def test_orchestrator_successful_shot_still_writes_record(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AWOS_ONE_SHOT_LOG_DIR", str(tmp_path / "rec"))
    shot = OneShotResult(applied=["a.py"], blocks=1, calls=1, reply="ok")
    verdict = {"test_result": SimpleNamespace(no_tests_found=False, failed=0, errors=0,
                                              passed=3, raw_output=""),
               "test_status": "3 passed"}
    with patch.object(Orchestrator, "_judge_agent_attempt", return_value=verdict), \
         patch.object(Orchestrator, "_acceptance_first", return_value=False, create=True), \
         patch("scaffold.agent.orchestrator._acceptance_mode", return_value="0"), \
         patch.object(Orchestrator, "_acceptance_check",
                      return_value=(True, "ok", ""), create=True):
        out = _run_first(shot, tmp_path)
    assert out["solved"] is True
    assert "[ONE-SHOT] block" not in capsys.readouterr().out
    rec = json.loads(next((tmp_path / "rec").glob("job7-*.json")).read_text())
    assert rec["applied"] == ["a.py"] and rec["failed"] == []


def test_orchestrator_write_error_does_not_break_flow(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AWOS_ONE_SHOT_LOG_DIR", str(tmp_path / "rec"))
    shot = OneShotResult(failed=[{"path": "a.py", "reason": "read-only test file"}],
                         blocks=1, calls=1)
    with patch.object(one_shot_mod, "write_one_shot_record", side_effect=OSError("disk")), \
         patch.object(one_shot_mod, "failure_log_lines", side_effect=ValueError("x")):
        out = _run_first(shot, tmp_path)
    assert out["solved"] is False
    assert "fell back to the agent loop" in capsys.readouterr().out

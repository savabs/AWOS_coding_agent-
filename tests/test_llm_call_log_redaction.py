"""Redaction at rest: a planted key or owner env secret never lands in the LLM
call log or in the memory/skill stores that persist model text.

Every fake secret is assembled by string concatenation (public repo: no
complete key-shaped literal in this file). Hermetic: tmp paths, fake clients.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import llm_call_log as lcl
from scaffold.agent import secret_scan as S

BODY = "Q7xk2Lp9" + "Rm4Tz8Vb" + "1Nc6Hd3J" + "f5Gs0WyA"
AWS = "AK" + "IA" + "Z7Q4K2LP" + "9RM4TZ8V"
ANTHROPIC = "sk-" + "ant-" + "api03-" + BODY
GH = "gh" + "p_" + BODY + "abcd"
OWNER = "own" + "Secret" + "Value42"          # not key-shaped: only the env knows it
PLANTED = [AWS, ANTHROPIC, GH, OWNER]


def _planted_text() -> str:
    return f"boom: key={AWS} auth={ANTHROPIC} gh={GH} pw={OWNER}"


@pytest.fixture(autouse=True)
def owner_env(monkeypatch):
    monkeypatch.setenv("AWOS_TEST_SERVICE_TOKEN", OWNER)


def _assert_clean(*paths: Path) -> str:
    blob = ""
    for p in paths:
        if p.is_dir():
            blob += "".join(f.read_text(encoding="utf-8", errors="replace")
                            for f in sorted(p.rglob("*")) if f.is_file())
        else:
            blob += p.read_text(encoding="utf-8", errors="replace")
    assert blob, "nothing was written"
    for secret in PLANTED:
        assert secret not in blob, f"{secret[:4]}… persisted"
    assert "[REDACTED:" in blob
    return blob


# ── llm_call_log ────────────────────────────────────────────────────────────

@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", str(path))
    return path


def test_record_error_never_logs_a_key(log):
    lcl.record_error("agent", "m", RuntimeError(_planted_text()))
    blob = _assert_clean(log)
    line = json.loads(blob)
    assert line["error"].startswith("RuntimeError: boom: key=[REDACTED:aws_access_key]")
    assert "[REDACTED:AWOS_TEST_SERVICE_TOKEN]" in line["error"]


def test_record_call_redacts_every_text_field(log):
    lcl.record_call("planner", "m", "m", "stop", 1, 1,
                    error=_planted_text(), note=_planted_text(),
                    nested={"a": [_planted_text()]}, odd=SimpleNamespace(k=AWS))
    line = json.loads(_assert_clean(log))
    assert line["input_tokens"] == 1 and line["component"] == "planner"


def test_error_is_redacted_before_it_is_clipped(log):
    # the key straddles the 500-char cut: clipping first would leave a stub
    lcl.record_call("agent", "m", None, None, None, None, error="x" * 490 + " " + ANTHROPIC)
    err = json.loads(log.read_text())["error"]
    assert ANTHROPIC[:10] not in err and len(err) <= 500


def test_record_response_override_error_is_redacted(log):
    resp = SimpleNamespace(model="m", choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content="hi"))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1))
    lcl.record_response("agent", "m", resp, error=_planted_text())
    _assert_clean(log)


def test_redaction_failure_drops_text_instead_of_writing_it(log, monkeypatch):
    monkeypatch.setattr(S, "redact_obj", lambda *a, **k: 1 / 0)
    lcl.record_error("agent", "m", RuntimeError(_planted_text()))
    line = json.loads(log.read_text())
    assert line["error"] == "[REDACTED:redact_error]"
    assert AWS not in log.read_text()


# ── memory / skill stores ───────────────────────────────────────────────────

def test_skill_library_never_persists_a_key(tmp_path):
    from scaffold.agent.skill_library import SkillLibrary
    lib = SkillLibrary(skills_dir=tmp_path / "skills")
    lib.record({"action": "fix login using " + _planted_text()},
               model_name="m " + AWS, strategy_name="cot")
    blob = "".join(p.read_text() for p in (tmp_path / "skills").iterdir())
    for secret in PLANTED:
        assert secret not in blob and secret.lower() not in blob


def test_skill_extractor_never_persists_a_key(tmp_path, monkeypatch):
    from scaffold.agent.skill_extractor import SkillExtractor
    monkeypatch.setattr(SkillExtractor, "SKILLS_DIR", str(tmp_path / "skills"))
    path = SkillExtractor()._save_skill("abc123", 'title: "Login fix"\n' + _planted_text())
    _assert_clean(Path(path))


def test_experience_store_and_trajectory_never_persist_a_key(tmp_path, monkeypatch):
    from scaffold.agent import experience as ex
    monkeypatch.setenv("AWOS_EXPERIENCE", "1")
    monkeypatch.setenv("AWOS_EXPERIENCE_DIR", str(tmp_path / "exp"))
    monkeypatch.setenv("AWOS_TRAJECTORY_DIR", str(tmp_path / "traj"))
    green = SimpleNamespace(passed=2, failed=0, errors=0, no_tests_found=False,
                            test_command=["pytest", AWS])
    assert ex.save_record(str(tmp_path), goal="goal " + _planted_text(), files=["a.py"],
                          diff="+K = '" + ANTHROPIC + "'", success=True,
                          test_result=green) == 1
    assert ex.write_trajectory(goal=_planted_text(), model="m", verdict={"why": OWNER},
                               files_changed=["a.py"], diff="+" + GH, start_ts=0.0)
    _assert_clean(tmp_path / "exp", tmp_path / "traj")


def test_error_pattern_store_never_persists_a_key(tmp_path):
    from scaffold.agent.error_pattern_store import ErrorPattern, ErrorPatternStore
    store = ErrorPatternStore(str(tmp_path / "ep.jsonl"))
    store.save(ErrorPattern(task_id="t", file_path="a.py", error_type="X",
                            error_msg=_planted_text(), critique="use " + GH,
                            timestamp=ErrorPattern.now_timestamp()))
    _assert_clean(tmp_path / "ep.jsonl")


def test_example_store_never_persists_a_key(tmp_path):
    from scaffold.agent.example_store import ExampleStore
    store = ExampleStore(str(tmp_path / "ex.json"))
    store.record_success({"action": "set " + AWS, "file": "a.py"},
                         search="K = None", replace="K = '" + ANTHROPIC + "'  # " + OWNER)
    _assert_clean(tmp_path / "ex.json")


def test_session_memory_never_persists_a_key(tmp_path):
    from scaffold.agent.session_memory import ChatSessionManager
    mgr = ChatSessionManager(sessions_dir=tmp_path / "sessions")
    mgr.create_session("s1")
    mgr.add_to_current_session("assistant", _planted_text(), 10)
    mgr.save_session()
    _assert_clean(tmp_path / "sessions")
    assert mgr.get_session("s1").messages[0].content.startswith("boom: key=[REDACTED:")


def test_project_notebook_never_persists_a_key(tmp_path, monkeypatch):
    from scaffold.agent import project_notebook as nb
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWOS_NOTEBOOK", "1")
    monkeypatch.delenv("AWOS_PROJECT_ID", raising=False)
    reply = ("## How to work here\n- Tests: `pytest -q`\n## Layout and conventions\n"
             f"- staging key {AWS} lives in cfg.py\n## Pitfalls\n- token {GH}\n"
             "## Past jobs\n- add flag -> solved\n")
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **k: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5)))))
    with patch("scaffold.agent.usage_record.record_api_usage"):
        out = nb.update_notebook("proj", "add a flag", {"success": True}, "TASK: x",
                                 client=client)
    assert out is not None
    blob = nb.notebook_path("proj").read_text()
    assert AWS not in blob and GH not in blob and "[REDACTED:aws_access_key]" in blob


def test_vector_memory_outcomes_are_redacted():
    from scaffold.agent.vector_memory import VectorMemory
    vm = object.__new__(VectorMemory)
    vm._sess_col = MagicMock()
    vm._evict_session_if_needed = lambda: None
    vm.store_outcome({"action": "use " + AWS, "file": "a.py"}, False, "s", critique=GH)
    kwargs = vm._sess_col.add.call_args.kwargs
    flat = json.dumps(kwargs, default=str)
    assert AWS not in flat and GH not in flat


def test_memory_vector_store_is_redacted():
    pytest.importorskip("chromadb")
    pytest.importorskip("fastembed")
    from scaffold.agent.memory.vector_memory import VectorMemory
    vm = object.__new__(VectorMemory)
    vm.collection = MagicMock()
    seen = []
    vm._embed = lambda text: seen.append(text) or [0.0]
    vm._enforce_cap = lambda: None
    vm.store(_planted_text(), {"session_id": "s", "note": AWS})
    flat = json.dumps(vm.collection.add.call_args.kwargs, default=str) + "".join(seen)
    for secret in PLANTED:
        assert secret not in flat

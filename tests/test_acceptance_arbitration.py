"""
Arbitration inputs of the fail-safe acceptance gate (mode 2,
docs/specs/ablation_acceptance_v2.md): node id -> test source, FAILURES
section lookup, the arbitration prompt, tests/conftest.py fixtures in the
hidden dir, and the arbitration reply in the log.

No network: real pytest on a tmp project (no sandbox), a fake client.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_acceptance import _FakeClient  # noqa: E402  (helper only)

from scaffold.agent import acceptance  # noqa: E402
from scaffold.agent.acceptance import (  # noqa: E402
    AcceptanceSuite, _failure_sections, arbitrate, build_suite, filter_start_failing,
    parse_outcomes, run_acceptance_detail, run_tests_once, section_for,
    test_function_source as fn_source,
)


@pytest.fixture(autouse=True)
def _safe(monkeypatch):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")


SRC = '''import pytest


def test_plain():
    assert 1 == 2


class TestListCommand:
    def test_list_shows_size(self):
        assert "size" in ""

    @pytest.mark.parametrize("v", [1, 2])
    def test_param(self, v):
        assert v == 3


class TestOther:
    def test_list_shows_size(self):
        assert "other"


@pytest.mark.parametrize("n", ["a.b", "c"])
def test_top_param(n):
    assert n
'''


# ── 1. node id -> function source ────────────────────────────────────────────

def test_source_of_class_method_from_node_id():
    for name in ("TestListCommand::test_list_shows_size",
                 "tests/x.py::TestListCommand::test_list_shows_size",
                 "TestListCommand.test_list_shows_size"):
        src = fn_source(SRC, name)
        assert src.startswith("def test_list_shows_size(self):"), name
        assert '"size" in ""' in src and "other" not in src and "test_plain" not in src


def test_source_of_other_class_and_parametrized_ids():
    assert '"other"' in fn_source(SRC, "TestOther::test_list_shows_size")
    p = fn_source(SRC, "TestListCommand::test_param[2]")
    assert p.startswith("@pytest.mark.parametrize") and "def test_param" in p
    assert "test_plain" not in p
    t = fn_source(SRC, "test_top_param[a.b]")
    assert "def test_top_param" in t and "class" not in t


def test_source_of_plain_function_and_fallback():
    assert fn_source(SRC, "test_plain") == "def test_plain():\n    assert 1 == 2"
    assert fn_source(SRC, "test_missing") == SRC
    assert fn_source("def (:", "test_x") == "def (:"


# ── 2. FAILURES section lookup ───────────────────────────────────────────────

OUTPUT = """\
=================================== FAILURES ===================================
____________________ TestListCommand.test_list_shows_size _____________________

    def test_list_shows_size(self):
>       out = run_list()
E       NameError: name 'human_size' is not defined

pkg/cli.py:12: NameError
_____________________________ TestListCommand.test_param[1] ____________________
E       assert 1 == 3
__________________________________ test_plain __________________________________
E       assert 1 == 2
=========================== short test summary info ============================
FAILED .awos_acceptance_ab/test_awos_acceptance.py::TestListCommand::test_list_shows_size - Na...
FAILED .awos_acceptance_ab/test_awos_acceptance.py::TestListCommand::test_param[1] - as...
FAILED .awos_acceptance_ab/test_awos_acceptance.py::test_plain - assert 1 == 2
"""


def test_section_lookup_class_dot_vs_colons():
    sections = _failure_sections(OUTPUT)
    body = section_for(sections, "TestListCommand::test_list_shows_size")
    assert body is not None and "human_size" in body
    assert section_for(sections, "TestListCommand.test_list_shows_size") is body
    assert "assert 1 == 3" in section_for(sections, "TestListCommand::test_param[1]")
    assert "assert 1 == 2" in section_for(sections, "test_plain")
    assert section_for(sections, "test_nope") is None


def test_parse_outcomes_takes_reason_from_class_section():
    outcomes, coll = parse_outcomes(OUTPUT)
    assert not coll
    status, reason = outcomes["TestListCommand::test_list_shows_size"]
    assert status == "FAILED" and "name 'human_size' is not defined" in reason
    # a NameError raised from the project's code is in the full reason now
    assert outcomes["TestListCommand::test_param[1]"][1] == "assert 1 == 3"


def test_missing_fixture_error_dropped_as_test_bug():
    out = ("___________________ ERROR at setup of TestA.test_c ___________________\n"
           "E       fixture 'cli_runner' not found\n"
           "=========================== short test summary info ===\n"
           "ERROR d/test_x.py::TestA::test_c\n"
           "ERROR d/test_x.py::test_d - RuntimeError: boom\n"
           "FAILED d/test_x.py::test_e - assert 0\n")
    outcomes, coll = parse_outcomes(out)
    assert outcomes["TestA::test_c"] == ("ERROR", "fixture 'cli_runner' not found")
    kept, dropped = filter_start_failing(outcomes, coll)
    assert kept == ["test_e"] and dropped == {"test bug": 1, "error": 1}


# ── 3. prompt ────────────────────────────────────────────────────────────────

def test_arbitration_prompt_rule():
    p = acceptance.ARBITRATION_PROMPT
    assert "When unsure" not in p
    for word in ("NameError", "ImportError", "AttributeError", "TypeError",
                 "unhandled exception", "CODE_INCOMPLETE", "WRONG_TEST", "JSON"):
        assert word in p
    assert "internal mechanism" in p


# ── 4. tests/conftest.py fixtures in the hidden dir ──────────────────────────

CONFTEST = '''import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def store(tmp_path):
    from app.store import Store
    return Store(tmp_path)
'''


def _fixture_project() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "src" / "app").mkdir(parents=True)
    (root / "src" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "app" / "store.py").write_text(
        "class Store:\n    def __init__(self, path):\n        self.path = path\n\n"
        "    def size(self):\n        return -1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (root / "tests" / "test_store.py").write_text(
        "def test_store(store):\n    assert store.path\n", encoding="utf-8")
    return root


ACC = '''class TestSize:
    def test_size_zero(self, store):
        assert store.size() == 0


def test_has_path(store):
    assert store.path


def test_unknown(no_such_fixture):
    assert True
'''


def test_conftest_fixtures_resolve_and_are_cleaned_up():
    root = _fixture_project()
    before = (root / "tests" / "conftest.py").read_text(encoding="utf-8")
    run = run_tests_once(str(root), ACC)
    out = run["outcomes"]
    assert out["test_has_path"][0] == "PASSED"
    assert out["TestSize::test_size_zero"][0] == "FAILED"
    assert "assert -1 == 0" in out["TestSize::test_size_zero"][1]
    assert "fixture" not in out["TestSize::test_size_zero"][1]
    assert out["test_unknown"][0] == "ERROR" and "fixture 'no_such_fixture'" in out[
        "test_unknown"][1]
    kept, dropped = filter_start_failing(out, run["collection_error"])
    assert kept == ["TestSize::test_size_zero"]
    assert dropped == {"passed on start": 1, "test bug": 1}
    # the hidden dir and its conftest copy are gone; the original is untouched
    assert not list(root.glob(".awos_acceptance_*"))
    assert (root / "tests" / "conftest.py").read_text(encoding="utf-8") == before
    assert not (root / "conftest.py").exists()


def test_detail_carries_traceback_for_class_test():
    root = _fixture_project()
    suite = AcceptanceSuite(source=ACC, kept=["TestSize::test_size_zero"])
    ok, summary, _, failing = run_acceptance_detail(str(root), suite)
    assert ok is False and summary == "1 failing of 1"
    text = failing["TestSize::test_size_zero"]
    assert "in test_size_zero" in text and "E   assert -1 == 0" in text
    assert not text.startswith("FAILED:")  # the section, not the short reason
    assert not list(root.glob(".awos_acceptance_*"))


def test_no_conftest_still_runs():
    root = _fixture_project()
    (root / "tests" / "conftest.py").unlink()
    run = run_tests_once(str(root), "def test_x():\n    assert True\n")
    assert run["outcomes"]["test_x"][0] == "PASSED"
    assert not list(root.glob(".awos_acceptance_*"))


def test_build_suite_with_project_fixture():
    root = _fixture_project()
    reply = "```python\n" + ACC + "```"
    suite = build_suite("size() of an empty store is 0", str(root),
                        client=_FakeClient(reply), model="m")
    assert suite.kept == ["TestSize::test_size_zero"] and suite.active


# ── 5. arbitration reply in the log ──────────────────────────────────────────

def test_arbitration_reply_logged_and_returned(capsys):
    suite = AcceptanceSuite(source=SRC, kept=["test_plain"])
    reply = '{"test_plain": "CODE_INCOMPLETE"}\n' + "x" * 2000
    arb = arbitrate("issue", suite, {"test_plain": "E assert 1 == 2"}, "",
                    client=_FakeClient(reply), model="m")
    assert arb["verdicts"] == {"test_plain": "CODE_INCOMPLETE"}
    assert arb["reply"].startswith('{"test_plain"') and len(arb["reply"]) == 600
    log = capsys.readouterr().out
    assert '[ACCEPTANCE] arbitration reply: {"test_plain": "CODE_INCOMPLETE"}' in log


def test_unparseable_reply_still_fail_safe(capsys):
    suite = AcceptanceSuite(source=SRC, kept=["test_plain"])
    arb = arbitrate("issue", suite, {"test_plain": "E"}, "",
                    client=_FakeClient("I think the code is incomplete"), model="m")
    assert arb["verdicts"] == {"test_plain": "WRONG_TEST"} and not arb["parsed"]
    assert "I think the code is incomplete" in capsys.readouterr().out

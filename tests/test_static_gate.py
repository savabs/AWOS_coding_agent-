"""AWOS_STATIC_GATE (trick T3, docs/specs/static_gate.md): a Python edit that
does not compile or introduces a NEW undefined name is rejected before it is
written; the file stays unchanged and the model is told why."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scaffold.agent import static_gate  # noqa: E402
from scaffold.agent.one_shot import EditBlock, apply_blocks  # noqa: E402
from scaffold.agent.tools.code_edit import EditFileTool  # noqa: E402

CALC = "import os\n\n\ndef add(a, b):\n    return a - b\n"
needs_ruff = pytest.mark.skipif(static_gate._ruff() is None, reason="ruff not available")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    static_gate.reset()
    monkeypatch.setenv("AWOS_STATIC_GATE", "1")
    yield
    static_gate.reset()


def _project(text: str = CALC) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "calc.py").write_text(text, encoding="utf-8")
    return root


def _edit(root: Path, old: str, new: str, path: str = "calc.py", **kw):
    return EditFileTool(str(root), **kw).execute(
        {"path": path, "old_string": old, "new_string": new})


# ── static_gate.check ────────────────────────────────────────────────────────

def test_syntax_error_rejected():
    ok, msgs = static_gate.check("a.py", "def f(:\n    pass\n", "def f():\n    pass\n")
    assert not ok and "SyntaxError" in msgs[0]


@needs_ruff
def test_new_undefined_name_rejected():
    ok, msgs = static_gate.check("a.py", "def f():\n    return missing_name\n",
                                 "def f():\n    return 1\n")
    assert not ok and "missing_name" in msgs[0]


@needs_ruff
def test_pre_existing_undefined_name_does_not_block():
    old = "def f():\n    return legacy_name\n"
    new = "def f():\n    return legacy_name\n\n\ndef g():\n    return 2\n"
    assert static_gate.check("a.py", new, old) == (True, [])


def test_non_python_passes():
    assert static_gate.check("notes.md", "def f(:\n", "") == (True, [])
    assert static_gate.check("cfg.json", "{{{", "{}") == (True, [])


def test_cap_of_two_then_allowed():
    bad = "def f(:\n"
    assert static_gate.check("x.py", bad, "")[0] is False
    assert static_gate.check("x.py", bad, "")[0] is False
    ok, msgs = static_gate.check("x.py", bad, "")
    assert ok and msgs  # third consecutive: let through, with the messages
    assert static_gate.total_rejections() == 2


def test_pass_resets_consecutive_count():
    bad, good = "def f(:\n", "def f():\n    pass\n"
    static_gate.check("y.py", bad, "")
    static_gate.check("y.py", good, "")
    assert static_gate.check("y.py", bad, "")[0] is False
    assert static_gate.check("y.py", bad, "")[0] is False


# ── EditFileTool ─────────────────────────────────────────────────────────────

def test_edit_with_syntax_error_leaves_file_unchanged(capsys):
    root = _project()
    res = _edit(root, "    return a - b\n", "    return (a + b\n")
    assert not res.success
    assert "NOT applied" in res.error
    assert (root / "calc.py").read_text() == CALC
    assert "[STATIC-GATE] rejected" in capsys.readouterr().out


@needs_ruff
def test_edit_introducing_undefined_name_rejected():
    root = _project()
    res = _edit(root, "    return a - b\n", "    return helper(a) + b\n")
    assert not res.success and "helper" in res.error
    assert (root / "calc.py").read_text() == CALC


def test_good_edit_applies():
    root = _project()
    res = _edit(root, "    return a - b\n", "    return a + b\n")
    assert res.success
    assert "a + b" in (root / "calc.py").read_text()


def test_new_file_with_syntax_error_rejected():
    root = _project()
    res = _edit(root, "", "def broken(:\n", path="new_mod.py")
    assert not res.success
    assert not (root / "new_mod.py").exists()


def test_default_off_is_identical(monkeypatch):
    monkeypatch.setenv("AWOS_STATIC_GATE", "0")
    root = _project()
    # New file with a syntax error: the creation path never checked syntax.
    assert _edit(root, "", "def broken(:\n", path="new_mod.py").success
    # Undefined name: Verifier only checks syntax, so it is written.
    assert _edit(root, "    return a - b\n", "    return helper(a)\n").success
    assert static_gate.total_rejections() == 0
    # Test files stay editable without protect_tests.
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text("def test_x():\n    assert 1\n")
    assert _edit(root, "assert 1", "assert 2", path="tests/test_calc.py").success


def test_protect_tests_rejects_existing_test_file():
    root = _project()
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text("def test_x():\n    assert 1\n")
    res = _edit(root, "assert 1", "assert 2", path="tests/test_calc.py", protect_tests=True)
    assert not res.success and "read-only" in res.error
    assert "assert 1" in (root / "tests" / "test_calc.py").read_text()
    # A NEW test file may still be created.
    assert _edit(root, "", "def test_y():\n    assert 1\n", path="tests/test_new.py",
                 protect_tests=True).success


# ── one-shot path ────────────────────────────────────────────────────────────

def test_one_shot_block_with_syntax_error_is_a_failed_block():
    root = _project()
    blocks = [EditBlock(path="calc.py", search="    return a - b\n",
                        replace="    return (a + b\n")]
    applied, failed = apply_blocks(str(root), blocks)
    assert applied == []
    assert len(failed) == 1 and "index" in failed[0]  # repairable, flows to repair
    assert "NOT applied" in failed[0]["reason"]
    assert (root / "calc.py").read_text() == CALC


def test_one_shot_append_is_gated():
    root = _project()
    blocks = [EditBlock(path="calc.py", search="", replace="def mul(a, b:\n    return a * b\n")]
    applied, failed = apply_blocks(str(root), blocks)
    assert applied == [] and failed and "NOT applied" in failed[0]["reason"]
    assert (root / "calc.py").read_text() == CALC


def test_tests_read_only_is_its_own_knob(monkeypatch):
    # AWOS_STATIC_GATE=1 alone does not make tests read-only (offline: 7/73
    # solved runs edited an existing test file in a feature task).
    monkeypatch.delenv("AWOS_STATIC_GATE_TESTS", raising=False)
    assert static_gate.enabled() and not static_gate.tests_read_only()
    monkeypatch.setenv("AWOS_STATIC_GATE_TESTS", "1")
    assert static_gate.tests_read_only()

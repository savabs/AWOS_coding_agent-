"""Tests for verifier edit-fidelity checks."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scaffold" / "agent"))

from verifier import Verifier


def test_fidelity_rejects_inner_docstring_when_comment_above_requested(tmp_path):
    vf = Verifier()
    py = tmp_path / "worker.py"
    original = (
        "class W:\n"
        "    def _try_cheap_fallback(self, x):\n"
        "        return x\n"
    )
    py.write_text(original)

    task = {
        "action": "Add a short comment above `_try_cheap_fallback` explaining cheap providers",
        "file": "worker.py",
    }
    search = "    def _try_cheap_fallback(self, x):\n        return x\n"
    replace = (
        '    def _try_cheap_fallback(self, x):\n'
        '        """Uses alternate cheap providers."""\n'
        "        return x\n"
    )
    result = vf.verify_and_apply(
        {"search": search, "replace": replace, "task_spec": task},
        str(py),
    )
    assert result["success"] is False
    assert result.get("fidelity_fail") is True
    assert "comment above" in result["error_context"].lower()


def test_fidelity_allows_comment_above_function(tmp_path):
    vf = Verifier()
    py = tmp_path / "worker.py"
    original = (
        "class W:\n"
        "    def _try_cheap_fallback(self, x):\n"
        "        return x\n"
    )
    py.write_text(original)

    task = {
        "action": "Add comment above `_try_cheap_fallback`",
        "file": "worker.py",
    }
    search = "    def _try_cheap_fallback(self, x):\n"
    replace = "    # Tries alternate cheap providers when primary fails.\n    def _try_cheap_fallback(self, x):\n"
    result = vf.verify_and_apply(
        {"search": search, "replace": replace, "task_spec": task},
        str(py),
    )
    assert result["success"] is True
    assert "# Tries alternate" in py.read_text()

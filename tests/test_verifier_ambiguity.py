"""Tests for verifier ambiguous-fuzzy-match rejection."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scaffold" / "agent"))
from verifier import Verifier  # noqa: E402


def test_verify_rejects_ambiguous_fuzzy_match(tmp_path: Path):
    vf = Verifier()
    py = tmp_path / "mod.py"
    # Two nearly-identical blocks -> the search could match either.
    py.write_text(
        "def a():\n"
        "    return compute(value, mode)\n"
        "\n"
        "def b():\n"
        "    return compute(value, mode)\n",
        encoding="utf-8",
    )
    search = "def x():\n    return compute(value, mode)"
    replace = "def x():\n    return compute(value, mode, new_arg)"

    result = vf.verify_and_apply(
        {"search": search, "replace": replace},
        str(py),
        check_type="syntax",
    )

    assert not result["success"]
    assert result["needs_retry"] is True


def test_verify_unambiguous_fuzzy_match_applies(tmp_path: Path):
    vf = Verifier()
    py = tmp_path / "mod.py"
    py.write_text(
        "def only_one():\n"
        "    return compute(value, mode)\n",
        encoding="utf-8",
    )
    search = "def only_one():\n    return compute(value, mode)"
    replace = "def only_one():\n    return compute(value, mode, new_arg)"

    result = vf.verify_and_apply(
        {"search": search, "replace": replace},
        str(py),
        check_type="syntax",
    )

    assert result["success"] is True
    assert "new_arg" in py.read_text(encoding="utf-8")

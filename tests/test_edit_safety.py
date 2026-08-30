"""Tests for edit/verifier safety: validate-before-write and ambiguity rejection."""

from __future__ import annotations

from pathlib import Path

from scaffold.agent.coding_tools import EditFileTool


def test_edit_file_does_not_write_invalid_python(tmp_path: Path):
    py = tmp_path / "pkg" / "mod.py"
    py.parent.mkdir()
    py.write_text("def ok():\n    return 1\n", encoding="utf-8")
    tool = EditFileTool(tmp_path)

    result = tool.execute(
        {
            "path": "pkg/mod.py",
            "old_string": "def ok():",
            "new_string": "def ok(:\n    return 1",
        }
    )

    assert not result.success
    assert "invalid Python" in result.error
    # Original content must be unchanged — nothing invalid was written.
    assert py.read_text(encoding="utf-8") == "def ok():\n    return 1\n"


def test_edit_file_new_file_does_not_create_invalid_python(tmp_path: Path):
    tool = EditFileTool(tmp_path)

    result = tool.execute(
        {"path": "pkg/new.py", "old_string": "", "new_string": "def broken(:\n"}
    )

    assert not result.success
    assert "invalid Python" in result.error
    assert not (tmp_path / "pkg" / "new.py").exists()


def test_edit_file_valid_edit_still_applies(tmp_path: Path):
    py = tmp_path / "pkg" / "mod.py"
    py.parent.mkdir()
    py.write_text("def ok():\n    return 1\n", encoding="utf-8")
    tool = EditFileTool(tmp_path)

    result = tool.execute(
        {
            "path": "pkg/mod.py",
            "old_string": "return 1",
            "new_string": "return 2",
        }
    )

    assert result.success
    assert "return 2" in py.read_text(encoding="utf-8")


def test_edit_file_refuses_blank_existing_file(tmp_path: Path):
    py = tmp_path / "pkg" / "mod.py"
    py.parent.mkdir()
    py.write_text("def ok():\n    return 1\n", encoding="utf-8")
    tool = EditFileTool(tmp_path)

    result = tool.execute(
        {"path": "pkg/mod.py", "old_string": "", "new_string": "   \n"}
    )

    assert not result.success
    assert py.read_text(encoding="utf-8") == "def ok():\n    return 1\n"


def test_edit_file_new_file_requires_content(tmp_path: Path):
    tool = EditFileTool(tmp_path)
    result = tool.execute({"path": "pkg/x.py", "old_string": "", "new_string": ""})
    assert not result.success
    assert not (tmp_path / "pkg" / "x.py").exists()

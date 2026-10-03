"""Tests for structured diff builder."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scaffold" / "agent"))

from diff_builder import build_file_change, collect_session_files, FileStatus


def test_build_file_change_from_search_replace(tmp_path):
    f = tmp_path / "example.py"
    f.write_text("def foo():\n    pass\n", encoding="utf-8")
    change = build_file_change(
        "example.py",
        search="def foo():\n    pass\n",
        replace="def foo():\n    return 1\n",
        codebase_root=str(tmp_path),
    )
    assert change.status == FileStatus.MODIFIED.value
    assert change.lines_added == 1
    assert change.lines_removed == 1
    assert change.hunks
    assert any(ln.type == "add" for h in change.hunks for ln in h.lines)
    assert any(ln.type == "remove" for h in change.hunks for ln in h.lines)


def test_created_file_detection():
    change = build_file_change(
        "new_module.py",
        search="",
        replace="print('hello')\n",
        codebase_root="/tmp/nonexistent_awos_test_root",
    )
    assert change.status == FileStatus.CREATED.value


def test_collect_session_files_summary():
    a = build_file_change("a.py", "x", "y")
    b = build_file_change("b.py", "old\n", "new\nline\n")
    agg = collect_session_files([a, b])
    assert agg["summary"]["total_files"] == 2
    assert "files" in agg
    assert "by_status" in agg

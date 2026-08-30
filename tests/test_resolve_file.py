"""Tests for disk-first fail-safe file resolution."""

from __future__ import annotations

from pathlib import Path

from scaffold.agent.resolve_file import (
    format_resolution,
    resolve_path,
    resolve_with_evidence,
)


def test_exact_path_resolves(tmp_path: Path):
    target = tmp_path / "pkg" / "hello.py"
    target.parent.mkdir()
    target.write_text("def greet(): return 1\n", encoding="utf-8")

    path = resolve_path(str(tmp_path), "pkg/hello.py")
    assert path == str(target.resolve())
    evidence = resolve_with_evidence(str(tmp_path), "pkg/hello.py")
    assert evidence["method"] == "exact"
    assert evidence["confidence"] == 1.0
    assert evidence["rel_path"] == "pkg/hello.py"


def test_basename_fallback_finds_file_under_wrong_dir(tmp_path: Path):
    target = tmp_path / "src" / "deep" / "hello.py"
    target.parent.mkdir(parents=True)
    target.write_text("def greet(): return 1\n", encoding="utf-8")

    # Stale hint points at the WRONG directory (pkg/hello.py doesn't exist).
    path = resolve_path(str(tmp_path), "pkg/hello.py", byte_exact=False)
    assert path == str(target.resolve())

    evidence = resolve_with_evidence(str(tmp_path), "pkg/hello.py")
    assert evidence["method"] in ("fuzzy", "exact")
    assert evidence["rel_path"] == "src/deep/hello.py"


def test_case_insensitive_fallback(tmp_path: Path):
    target = tmp_path / "Helper.py"
    target.write_text("X = 1\n", encoding="utf-8")

    evidence = resolve_with_evidence(str(tmp_path), "helper.py")
    assert evidence["path"] is not None
    # On macOS (case-insensitive FS) the exact lookup may already resolve the
    # case-variant; on case-sensitive FS the fuzzy basename path handles it.
    # Either way the file MUST be found — an index hint must never hide it.
    assert Path(str(evidence["path"])).is_file()
    assert Path(str(evidence["path"])).name.lower() == "helper.py"


def test_grep_fallback_finds_symbol(tmp_path: Path):
    target = tmp_path / "engine" / "solver.py"
    target.parent.mkdir()
    target.write_text("def solve_quadratic():\n    pass\n", encoding="utf-8")

    evidence = resolve_with_evidence(str(tmp_path), "solve_quadratic")
    assert evidence["path"] is not None
    assert evidence["rel_path"] == "engine/solver.py"


def test_absent_file_returns_none(tmp_path: Path):
    evidence = resolve_with_evidence(str(tmp_path), "definitely_not_here.py")
    assert evidence["path"] is None
    assert "NOT FOUND" in format_resolution(evidence)


def test_format_resolution(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    ev = resolve_with_evidence(str(tmp_path), "a.py")
    rendered = format_resolution(ev)
    assert "RESOLVED to a.py" in rendered
    assert "method=exact" in rendered

"""Regression tests for incremental quality-gate and vault-lint scope."""

from pathlib import Path

from scripts.obsidian_lint import collect_explicit_files, collect_markdown_files
from scripts.quality_gate import (
    _task_artifact_paths,
    _test_command_args,
    run_obsidian_lint,
)


def test_markdown_collection_excludes_generated_awos_content(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / ".awos" / "worktrees").mkdir(parents=True)
    canonical = tmp_path / "docs" / "note.md"
    generated = tmp_path / ".awos" / "worktrees" / "note.md"
    canonical.write_text("---\ntitle: Note\ntags: [doc/wiki]\n---\n", encoding="utf-8")
    generated.write_text("legacy generated note", encoding="utf-8")

    files = collect_markdown_files(tmp_path)

    assert files == [canonical]


def test_explicit_file_mode_ignores_missing_and_generated_files(tmp_path: Path):
    good = tmp_path / "good.md"
    generated = tmp_path / ".awos" / "generated.md"
    generated.parent.mkdir()
    good.write_text("---\ntitle: Good\ntags: [doc/wiki]\n---\n", encoding="utf-8")
    generated.write_text("legacy", encoding="utf-8")

    files = collect_explicit_files(
        tmp_path,
        ["good.md", "missing.md", ".awos/generated.md"],
    )

    assert files == [good.resolve()]


def test_test_command_expands_local_globs_without_shell_execution():
    root = Path(__file__).resolve().parent.parent

    args = _test_command_args(
        "python3 -m pytest tests/test_quality_gate_*.py -q",
        root,
    )

    assert "tests/test_quality_gate_reliability.py" in args
    assert "tests/test_quality_gate_*.py" not in args


def test_quality_gate_lints_only_current_task_artifacts():
    root = Path(__file__).resolve().parent.parent
    task = root / "tasks" / "active" / "live_debugger_wiring.md"

    artifacts = _task_artifact_paths(root, task)
    ok, output = run_obsidian_lint(root, task)

    assert task in artifacts
    assert any(path.name == "live_debugger_wiring.md" for path in artifacts)
    assert ok, output
    assert "Scanned" in output

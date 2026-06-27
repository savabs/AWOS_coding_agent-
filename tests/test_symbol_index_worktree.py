"""SymbolIndex must index files inside git worktrees (.awos/worktrees/<id>)."""

from __future__ import annotations

from pathlib import Path

from scaffold.agent.symbol_index import SymbolIndex, should_index_py_file


def test_should_index_py_file_worktree_relative_parts():
    root = Path("/repo/.awos/worktrees/abc123")
    py_file = root / "scaffold" / "agent" / "worker.py"
    assert should_index_py_file(py_file, root) is True


def test_should_index_skips_awos_under_main_repo():
    root = Path("/repo")
    py_file = root / ".awos" / "state" / "x.py"
    assert should_index_py_file(py_file, root) is False


def test_symbol_index_builds_in_worktree_layout(tmp_path):
    wt_root = tmp_path / ".awos" / "worktrees" / "feat1"
    module = wt_root / "scaffold" / "agent"
    module.mkdir(parents=True)
    target = module / "sample.py"
    target.write_text(
        "def hello():\n    return 1\n",
        encoding="utf-8",
    )

    idx = SymbolIndex(str(wt_root))
    idx.build(max_files=20)

    assert len(idx._cache) >= 1
    assert "1 symbols" in idx.summary()


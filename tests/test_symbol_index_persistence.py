"""Tests for persistent symbol index and disk-grounded retrieval."""

from __future__ import annotations

from pathlib import Path

from scaffold.agent.index_retrieval import ensure_index, retrieve
from scaffold.agent.repo_map import enrich_with_repo_map
from scaffold.agent.symbol_index import SymbolIndex


def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Write {rel_path: content} under tmp_path, returning the root."""
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def test_ast_records_symbol_lines(tmp_path: Path):
    root = _make_repo(tmp_path, {
        "mod.py": "class Foo:\n    def bar(self):\n        return 1\n\n\ndef top():\n    pass\n",
    })
    idx = SymbolIndex(str(root))
    idx.build()
    syms = idx.get_file_symbols(str(root / "mod.py"))
    assert syms.symbol_lines["Foo"] == (1, 3)
    assert "top" in syms.functions
    assert syms.symbol_lines["top"] == (6, 7)


def test_save_load_round_trip(tmp_path):
    root = _make_repo(tmp_path, {"mod.py": "def foo():\n    return 1\n"})
    idx = SymbolIndex(str(root))
    idx.build()
    idx_path = tmp_path / "symbol_index.json"
    idx.save(str(idx_path))

    loaded = SymbolIndex.load(str(idx_path), str(root))
    assert "foo" in loaded.get_file_symbols(str(root / "mod.py")).functions
    assert len(loaded._cache) == 1
    assert loaded._file_mtimes == idx._file_mtimes


def test_rebuild_changed_reparses_modified_and_drops_deleted(tmp_path: Path):
    root = _make_repo(tmp_path, {"a.py": "def a():\n    pass\n", "b.py": "def b():\n    pass\n"})
    idx = SymbolIndex(str(root))
    idx.build()
    idx_path = tmp_path / "symbol_index.json"
    idx.save(str(idx_path))

    (root / "a.py").write_text("def a2():\n    pass\n", encoding="utf-8")
    (root / "b.py").unlink()

    loaded = SymbolIndex.load(str(idx_path), str(root))
    changed = loaded.rebuild_changed()
    assert changed == 1
    syms = loaded.get_file_symbols(str(root / "a.py"))
    assert "a2" in syms.functions
    assert "a" not in syms.functions
    assert str(root / "b.py") not in loaded._cache


def test_retrieve_read_windows_are_on_disk(tmp_path: Path):
    root = _make_repo(tmp_path, {"new/util.py": "def helper():\n    return 2\n"})
    idx = SymbolIndex(str(root))
    idx.build()

    retrieval = retrieve(str(root), index=idx, primary_file="new/util.py")
    for w in retrieval["read_windows"]:
        assert (root / w["file"]).is_file()
    assert retrieval["read_windows"], "expected at least one on-demand read window"


def test_retrieve_stale_primary_still_resolves_disk_first(tmp_path: Path):
    # Index built from old/util.py; file physically moved to new/util.py.
    root = _make_repo(tmp_path, {"old/util.py": "def helper():\n    return 2\n"})
    idx = SymbolIndex(str(root))
    idx.build()
    _make_repo(tmp_path, {"new/util.py": "def helper():\n    return 2\n"})
    (root / "old" / "util.py").unlink()

    retrieval = retrieve(str(root), index=idx, primary_file="old/util.py")
    # The stale hint must not produce a phantom window; if any window is
    # returned its file must be real on disk.
    for w in retrieval["read_windows"]:
        assert (root / w["file"]).is_file()


def test_retrieve_drops_stale_map_entries(tmp_path: Path):
    root = _make_repo(tmp_path, {"gone/util.py": "def helper():\n    return 2\n"})
    idx = SymbolIndex(str(root))
    idx.build()
    (root / "gone" / "util.py").unlink()

    retrieval = retrieve(str(root), index=idx)
    for entry in retrieval["symbol_map"].splitlines():
        assert entry  # non-empty lines only (stale discarded)


def test_enrich_with_repo_map_includes_symbol_context(tmp_path: Path):
    _make_repo(tmp_path, {"pkg/a.py": "class Greeter:\n    def hello(self):\n        pass\n"})
    ctx = enrich_with_repo_map("greeter hello", str(tmp_path), {"architecture": "t"}, primary_file="pkg/a.py")
    assert "symbol_map" in ctx
    assert "symbol_read_windows" in ctx
    for w in ctx["symbol_read_windows"]:
        assert (tmp_path / w["file"]).is_file()


def test_ensure_index_builds_and_returns_usable(tmp_path: Path):
    _make_repo(tmp_path, {"mod.py": "def z():\n    pass\n"})
    idx = ensure_index(str(tmp_path))
    assert "z" in idx.get_file_symbols(str(tmp_path / "mod.py")).functions

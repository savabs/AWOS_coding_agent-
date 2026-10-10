"""Job 08: exclude patterns ending in '/' exclude directories (not files)."""

import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from backupd import archiver, filters, storage  # noqa: E402
from backupd.cli import main  # noqa: E402
from backupd.config import load_config  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("APP_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    yield
    logger = logging.getLogger("backupd")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()


@pytest.fixture
def tree(tmp_path):
    src = tmp_path / "src"
    for rel in [
        "build/x.o", "build/sub/y.o", "app/build/z.o", "app/main.py",
        "notes/build", "build.txt", "app.log", "keep.txt",
        "cache/a", "cache2/b", "cachefile", "node_modules/m.js", "docs/node_modules",
    ]:
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rel)

    def _write(exclude=None):
        lines = ["[backup]", f"source_dir = {src}", f"backup_dir = {tmp_path / 'out'}"]
        if exclude is not None:
            lines.append(f"exclude = {exclude}")
        (tmp_path / "settings.ini").write_text("\n".join(lines) + "\n")
        return src

    return _write


def files(cfg):
    return [p.as_posix() for p in filters.iter_files(cfg)]


def test_directory_pattern_excludes_dirs_everywhere_but_not_files(tree):
    tree("build/, *.log")
    assert files(load_config()) == [
        "app/main.py", "build.txt", "cache/a", "cache2/b", "cachefile", "docs/node_modules",
        "keep.txt", "node_modules/m.js", "notes/build",
    ]


def test_glob_directory_pattern(tree):
    tree("cache*/")
    got = files(load_config())
    assert "cache/a" not in got and "cache2/b" not in got
    assert "cachefile" in got


def test_plain_patterns_unchanged(tree):
    tree("node_modules, *.o")
    got = files(load_config())
    assert "node_modules/m.js" not in got
    assert "docs/node_modules" not in got      # plain patterns still match files too
    assert not any(p.endswith(".o") for p in got)
    assert "notes/build" in got


def test_env_exclude_wins_over_ini(tree, monkeypatch):
    tree("*.txt")
    monkeypatch.setenv("APP_EXCLUDE", "build/,app/")
    got = files(load_config())
    assert "keep.txt" in got and "build.txt" in got
    assert not any(p.startswith(("build/", "app/")) for p in got)
    assert "notes/build" in got


def test_state_file_still_excluded(tree, monkeypatch):
    src = tree("build/")
    (src / ".last_run").write_text("x")
    assert ".last_run" not in files(load_config())


def test_run_uses_the_same_rules(tree, tmp_path, capsys, monkeypatch):
    tree()
    monkeypatch.setenv("APP_EXCLUDE", "build/, *.log, cache*/")
    assert main(["run"]) == 0
    archive = storage.list_backups(tmp_path / "out")[-1]
    assert archiver.archive_members(archive) == [
        "app/main.py", "build.txt", "cachefile", "docs/node_modules", "keep.txt",
        "node_modules/m.js", "notes/build",
    ]

"""Job 10: keep_last - never prune the newest N backups."""

import logging
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from backupd import storage  # noqa: E402
from backupd.cli import main  # noqa: E402
from backupd.config import load_config  # noqa: E402
from backupd.errors import ConfigError  # noqa: E402

NOW = datetime(2026, 3, 31, 12, 0, 0)


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
def setup(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("alpha")
    out = tmp_path / "out"
    out.mkdir()

    def _setup(days_ago, extra="retention_days = 7"):
        names = []
        for d in days_ago:
            when = NOW - timedelta(days=d)
            name = f"backup-{when:%Y%m%d-%H%M%S}.zip"
            (out / name).write_bytes(b"x")
            names.append(name)
        (out / "backup-notes.zip").write_bytes(b"not a backup")
        path = tmp_path / "settings.ini"
        path.write_text(f"[backup]\nsource_dir = {src}\nbackup_dir = {out}\n{extra}\n")
        return str(path), names

    return _setup


def remaining(tmp_path):
    return [p.name for p in storage.list_backups(tmp_path / "out")]


def test_keeps_newest_even_if_old(setup, tmp_path):
    settings, names = setup([50, 40, 30, 20, 10], "retention_days = 7\nkeep_last = 2")
    removed = storage.prune_old_backups(NOW, settings)
    assert sorted(p.name for p in removed) == sorted(names[:3])
    assert remaining(tmp_path) == names[3:]
    assert (tmp_path / "out" / "backup-notes.zip").exists()


def test_recent_backups_are_kept_anyway(setup, tmp_path):
    settings, names = setup([30, 20, 10, 1], "retention_days = 7\nkeep_last = 2")
    storage.prune_old_backups(NOW, settings)
    assert remaining(tmp_path) == names[2:]


def test_default_is_off(setup, tmp_path):
    settings, names = setup([30, 20, 10, 1])
    storage.prune_old_backups(NOW, settings)
    assert remaining(tmp_path) == names[3:]
    assert load_config(settings).keep_last == 0


def test_more_than_there_are(setup, tmp_path):
    settings, names = setup([30, 20], "retention_days = 7\nkeep_last = 5")
    assert storage.prune_old_backups(NOW, settings) == []
    assert remaining(tmp_path) == names


def test_env_wins_over_ini(setup, tmp_path, monkeypatch):
    settings, names = setup([50, 40, 30, 20, 10], "retention_days = 7\nkeep_last = 1")
    monkeypatch.setenv("APP_KEEP_LAST", "4")
    storage.prune_old_backups(NOW, settings)
    assert remaining(tmp_path) == names[1:]
    assert load_config(settings).keep_last == 4
    monkeypatch.setenv("APP_KEEP_LAST", "")
    assert load_config(settings).keep_last == 1


@pytest.mark.parametrize("bad", ["-1", "two"])
def test_bad_values_are_config_errors(setup, monkeypatch, bad):
    settings, _ = setup([10])
    monkeypatch.setenv("APP_KEEP_LAST", bad)
    with pytest.raises(ConfigError) as info:
        load_config(settings)
    assert "keep_last" in str(info.value)
    with pytest.raises(ConfigError):
        storage.prune_old_backups(NOW, settings)


def test_run_honours_it(setup, tmp_path, capsys):
    _, names = setup([400, 300, 200], "retention_days = 7\nkeep_last = 2")
    assert main(["run"]) == 0
    left = remaining(tmp_path)
    assert len(left) == 2
    assert left[0] == names[-1]


def test_negative_keep_last_stops_run(setup, tmp_path, capsys):
    setup([10], "keep_last = -3")
    assert main(["run"]) == 2
    err = capsys.readouterr().err
    assert err.strip().splitlines()[-1].startswith("configuration error: ")
    assert "keep_last" in err


def test_show_config(setup, capsys):
    setup([], "keep_last = 3")
    assert main(["show-config"]) == 0
    assert "keep_last = 3" in capsys.readouterr().out.splitlines()

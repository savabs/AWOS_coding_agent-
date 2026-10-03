import logging
from pathlib import Path

import pytest

from backupd import config
from backupd.cli import main
from backupd.errors import BackupdError, ConfigError
from backupd.paths import expand_path
from backupd.units import format_size, parse_size


@pytest.fixture(autouse=True)
def _reset_logger():
    yield
    logger = logging.getLogger("backupd")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()


def test_format_size():
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1536) == "1.5 KiB"
    assert format_size(10 * 1024 ** 2) == "10.0 MiB"


def test_parse_size():
    assert parse_size("2048") == 2048
    assert parse_size("1K") == 1024
    assert parse_size("500M") == 500 * 1024 ** 2
    assert parse_size("1.5GiB") == int(1.5 * 1024 ** 3)
    with pytest.raises(ValueError):
        parse_size("lots")


def test_expand_path(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BK_ROOT", "/srv/bk")
    assert expand_path("~/x") == tmp_path / "x"
    assert expand_path("$BK_ROOT/daily") == Path("/srv/bk/daily")


def test_config_error_is_a_backupd_error():
    assert issubclass(ConfigError, BackupdError)
    assert config.ConfigError is ConfigError


def test_env_wins_over_ini(write_ini, workspace, monkeypatch):
    write_ini(extra_backup="retention_days = 3")
    monkeypatch.setenv("APP_RETENTION_DAYS", "11")
    assert config.load_config().retention_days == 11
    monkeypatch.setenv("APP_RETENTION_DAYS", " ")
    assert config.load_config().retention_days == 3


def test_path_settings_expand_variables(write_ini, workspace, monkeypatch):
    write_ini()
    monkeypatch.setenv("BK_ROOT", str(workspace))
    monkeypatch.setenv("APP_BACKUP_DIR", "$BK_ROOT/elsewhere")
    assert config.load_config().backup_dir == workspace / "elsewhere"


def test_backupd_error_exit_code(write_ini, monkeypatch, capsys):
    write_ini()

    def boom(*a, **k):
        raise BackupdError("disk on fire")

    monkeypatch.setattr("backupd.runner.run_backup", boom)
    assert main(["run"]) == 1
    assert capsys.readouterr().err.strip() == "error: disk on fire"


def test_run_reports_size(write_ini, capsys):
    write_ini()
    assert main(["run"]) == 0
    out = capsys.readouterr().out
    assert "backup written:" in out and "files, " in out and out.rstrip().endswith("B)")

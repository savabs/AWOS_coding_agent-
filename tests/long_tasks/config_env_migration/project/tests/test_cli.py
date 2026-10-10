import logging

import pytest

from backupd import archiver, storage
from backupd.cli import main


@pytest.fixture(autouse=True)
def _reset_logger():
    yield
    logger = logging.getLogger("backupd")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()


def test_run_creates_archive(write_ini, workspace, capsys):
    write_ini(extra_backup="exclude = *.tmp, node_modules")
    assert main(["run"]) == 0
    assert "backup written" in capsys.readouterr().out
    backups = storage.list_backups(workspace / "out")
    assert len(backups) == 1
    assert archiver.archive_members(backups[0]) == ["docs/a.txt", "top.txt"]


def test_run_if_due_skips_second_run(write_ini, capsys):
    write_ini()
    assert main(["run"]) == 0
    assert main(["run", "--if-due"]) == 0
    assert "not due" in capsys.readouterr().out


def test_show_config(write_ini, capsys):
    write_ini(extra_backup="retention_days = 9")
    assert main(["show-config"]) == 0
    assert "retention_days = 9" in capsys.readouterr().out


def test_status(write_ini, capsys):
    write_ini()
    assert main(["status"]) == 0
    assert "interval_minutes: 60" in capsys.readouterr().out


def test_config_error_exit_code(workspace, capsys):
    (workspace / "settings.ini").write_text("[backup]\nbackup_dir = /tmp/x\n")
    assert main(["show-config"]) == 2
    assert "source_dir" in capsys.readouterr().err

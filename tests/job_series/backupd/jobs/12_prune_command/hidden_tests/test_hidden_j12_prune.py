"""Job 12: `backupd prune [--dry-run]` applies the retention rules on demand."""

import logging
import os
import re
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from backupd import cli, storage  # noqa: E402

LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO backupd(\.[\w.]+)?: ")
OLD = ["backup-20200101-000000.zip", "backup-20200201-000000.tar", "backup-20200301-000000.zip"]
SIZES = [1024, 2048, 512]


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
def out(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    for name, size in zip(OLD, SIZES):
        (out / name).write_bytes(b"x" * size)
    recent = datetime.now() - timedelta(days=1)
    (out / f"backup-{recent:%Y%m%d-%H%M%S}.zip").write_bytes(b"x" * 100)
    (out / "notes.txt").write_text("keep me")
    (out / ".backup-20190101-000000.zip.partial").write_text("keep me too")
    monkeypatch.setenv("APP_BACKUP_DIR", str(out))
    return out


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def names(out):
    return sorted(p.name for p in out.iterdir())


def test_prune(out, capsys):
    before = names(out)
    code, stdout, _ = run(capsys, "prune")
    assert code == 0
    assert stdout.splitlines() == [f"pruned {n}" for n in OLD] + ["3 backups pruned, 3.5 KiB freed"]
    assert names(out) == [n for n in before if n not in OLD]
    assert len(storage.list_backups(out)) == 1


def test_dry_run_deletes_nothing(out, capsys):
    before = names(out)
    code, stdout, _ = run(capsys, "prune", "--dry-run")
    assert code == 0
    assert stdout.splitlines() == [f"would prune {n}" for n in OLD] + [
        "3 backups would be pruned, 3.5 KiB would be freed"]
    assert names(out) == before


def test_keep_last_from_ini(out, tmp_path, capsys):
    (tmp_path / "settings.ini").write_text("[backup]\nkeep_last = 2\n")
    code, stdout, _ = run(capsys, "prune")
    assert code == 0
    assert stdout.splitlines() == [f"pruned {n}" for n in OLD[:2]] + [
        "2 backups pruned, 3.0 KiB freed"]
    assert (out / OLD[2]).exists()


def test_env_wins_and_singular(out, tmp_path, capsys, monkeypatch):
    (tmp_path / "settings.ini").write_text("[backup]\nkeep_last = 1\n")
    monkeypatch.setenv("APP_KEEP_LAST", "3")
    code, stdout, _ = run(capsys, "prune", "--dry-run")
    assert code == 0
    assert stdout.splitlines() == [f"would prune {OLD[0]}",
                                   "1 backup would be pruned, 1.0 KiB would be freed"]


def test_retention_from_env(out, capsys, monkeypatch):
    monkeypatch.setenv("APP_RETENTION_DAYS", "100000")
    before = names(out)
    code, stdout, _ = run(capsys, "prune")
    assert code == 0
    assert stdout.strip() == "nothing to prune"
    assert names(out) == before


def test_backup_dir_with_variables_from_ini(out, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("APP_BACKUP_DIR")
    monkeypatch.setenv("BK_HOME", str(tmp_path))
    (tmp_path / "settings.ini").write_text("[backup]\nbackup_dir = ${BK_HOME}/out\n")
    code, stdout, _ = run(capsys, "prune", "--dry-run")
    assert code == 0
    assert stdout.splitlines()[-1] == "3 backups would be pruned, 3.5 KiB would be freed"


def test_pruning_is_logged(out, tmp_path, capsys, monkeypatch):
    log_file = tmp_path / "b.log"
    monkeypatch.setenv("APP_LOG_FILE", str(log_file))
    assert run(capsys, "prune")[0] == 0
    text = log_file.read_text().splitlines()
    for name in OLD:
        lines = [ln for ln in text if name in ln]
        assert lines, f"{name} not in the log"
        assert LOG_LINE.match(lines[-1]), lines[-1]


def test_bad_keep_last(out, capsys, monkeypatch):
    monkeypatch.setenv("APP_KEEP_LAST", "-1")
    code, _, err = run(capsys, "prune")
    assert code == 2
    assert err.strip().splitlines()[-1].startswith("configuration error: ")
    assert "keep_last" in err
    assert len(storage.list_backups(out)) == 4


def test_registered_like_the_other_commands():
    args = cli.build_parser().parse_args(["prune", "--dry-run"])
    assert args.handler is cli.cmd_prune
    assert args.dry_run is True

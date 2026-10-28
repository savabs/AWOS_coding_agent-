"""Job 02: `backupd list` - archives oldest first with time and size."""

import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from backupd.cli import main  # noqa: E402


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


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def fill(out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "backup-20260310-120000.zip").write_bytes(b"x" * 2048)
    (out / "backup-20260308-093005.tar").write_bytes(b"x" * 100)
    (out / "backup-20260312-235959.zip").write_bytes(b"x" * 1536)
    # Not backups: must be ignored.
    (out / ".last_run").write_text("2026-03-12T23:59:59\n")
    (out / "notes.txt").write_text("hello")
    (out / "backup-latest.zip").write_bytes(b"x" * 999)
    (out / "backup-20260311-000000.zip.partial").write_bytes(b"x" * 10)


EXPECTED = [
    "backup-20260308-093005.tar  2026-03-08 09:30:05  100 B",
    "backup-20260310-120000.zip  2026-03-10 12:00:00  2.0 KiB",
    "backup-20260312-235959.zip  2026-03-12 23:59:59  1.5 KiB",
    "3 backups, 3.6 KiB total",
]


def test_list_layout_with_ini(tmp_path, capsys):
    out = tmp_path / "out"
    fill(out)
    (tmp_path / "settings.ini").write_text(
        f"[backup]\nsource_dir = {tmp_path / 'src'}\nbackup_dir = {out}\n")
    code, stdout, _ = run(capsys, "list")
    assert code == 0
    assert stdout.splitlines() == EXPECTED


def test_list_needs_only_backup_dir_from_env(tmp_path, capsys, monkeypatch):
    out = tmp_path / "vault" / "out"
    fill(out)
    monkeypatch.setenv("VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("APP_BACKUP_DIR", "$VAULT/out")
    code, stdout, err = run(capsys, "list")
    assert code == 0, err
    assert stdout.splitlines() == EXPECTED


def test_env_backup_dir_wins_over_ini(tmp_path, capsys, monkeypatch):
    fill(tmp_path / "real")
    (tmp_path / "settings.ini").write_text(
        f"[backup]\nsource_dir = {tmp_path}\nbackup_dir = {tmp_path / 'stale'}\n")
    monkeypatch.setenv("APP_BACKUP_DIR", str(tmp_path / "real"))
    code, stdout, _ = run(capsys, "list")
    assert code == 0
    assert stdout.splitlines() == EXPECTED


def test_explicit_config_path(tmp_path, capsys):
    fill(tmp_path / "out")
    other = tmp_path / "other.ini"
    other.write_text(f"[backup]\nbackup_dir = {tmp_path / 'out'}\n")
    code, stdout, _ = run(capsys, "--config", str(other), "list")
    assert code == 0
    assert stdout.splitlines()[-1] == "3 backups, 3.6 KiB total"


def test_single_backup_wording(tmp_path, capsys, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "backup-20260310-120000.tar").write_bytes(b"x" * 10)
    monkeypatch.setenv("APP_BACKUP_DIR", str(out))
    code, stdout, _ = run(capsys, "list")
    assert code == 0
    assert stdout.splitlines() == [
        "backup-20260310-120000.tar  2026-03-10 12:00:00  10 B",
        "1 backup, 10 B total",
    ]


@pytest.mark.parametrize("create", [True, False])
def test_no_backups(tmp_path, capsys, monkeypatch, create):
    out = tmp_path / "empty"
    if create:
        out.mkdir()
        (out / "notes.txt").write_text("x")
    monkeypatch.setenv("APP_BACKUP_DIR", str(out))
    code, stdout, _ = run(capsys, "list")
    assert code == 0
    assert stdout.strip() == f"no backups in {out}"


def test_missing_backup_dir_setting(tmp_path, capsys):
    code, stdout, err = run(capsys, "list")
    assert code == 2
    assert err.strip().splitlines()[-1].startswith("configuration error: ")
    assert "backup_dir" in err
    assert "Traceback" not in err

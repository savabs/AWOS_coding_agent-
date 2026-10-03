"""Job 06: status gains oldest + total_size, and a --json view."""

import json
import logging
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from backupd import cli, health  # noqa: E402

NOW = datetime(2020, 3, 12, 13, 0, 0)


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


def fill(out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "backup-20200310-120000.zip").write_bytes(b"x" * 2048)
    (out / "backup-20200308-093005.tar").write_bytes(b"x" * 1024)
    (out / "backup-20200312-120000.zip").write_bytes(b"x" * 3072)
    # not backups
    (out / ".last_run").write_text("2020-03-12T12:00:00\n")
    (out / "notes.txt").write_bytes(b"x" * 50000)
    (out / ".backup-20200313-000000.zip.partial").write_bytes(b"x" * 50000)


def expected(out, interval=60, notifications=False, overdue=True):
    return {
        "backup_dir": str(out),
        "interval_minutes": interval,
        "notifications": notifications,
        "backups": 3,
        "latest": "2020-03-12T12:00:00",
        "overdue": overdue,
        "oldest": "2020-03-08T09:30:05",
        "total_size": "6.0 KiB",
    }


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_report_dict(tmp_path, monkeypatch):
    out = tmp_path / "out"
    fill(out)
    monkeypatch.setenv("APP_BACKUP_DIR", str(out))
    assert health.status_report(now=NOW) == expected(out, overdue=False)


def test_text_layout(tmp_path, capsys):
    out = tmp_path / "out"
    fill(out)
    (tmp_path / "settings.ini").write_text(
        f"[backup]\nbackup_dir = {out}\ninterval_minutes = 30\n[notify]\nnotify_email = ops@example.com\n")
    code, stdout, _ = run(capsys, "status")
    assert code == 0
    assert stdout.splitlines() == [
        f"backup_dir: {out}",
        "interval_minutes: 30",
        "notifications: True",
        "backups: 3",
        "latest: 2020-03-12T12:00:00",
        "overdue: True",
        "oldest: 2020-03-08T09:30:05",
        "total_size: 6.0 KiB",
    ]


def test_json_view(tmp_path, capsys, monkeypatch):
    out = tmp_path / "vault" / "out"
    fill(out)
    monkeypatch.setenv("VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("APP_BACKUP_DIR", "$VAULT/out")
    monkeypatch.setenv("APP_INTERVAL_MINUTES", "15")
    code, stdout, _ = run(capsys, "status", "--json")
    assert code == 0
    assert json.loads(stdout) == expected(out, interval=15)


def test_json_env_wins_over_ini(tmp_path, capsys, monkeypatch):
    out = tmp_path / "out"
    fill(out)
    (tmp_path / "settings.ini").write_text(
        f"[backup]\nbackup_dir = {tmp_path / 'stale'}\ninterval_minutes = 5\n")
    monkeypatch.setenv("APP_BACKUP_DIR", str(out))
    code, stdout, _ = run(capsys, "status", "--json")
    assert code == 0
    assert json.loads(stdout) == expected(out, interval=5)


def test_no_backups(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("APP_BACKUP_DIR", str(tmp_path / "none"))
    code, stdout, _ = run(capsys, "status", "--json")
    assert code == 0
    data = json.loads(stdout)
    assert data["backups"] == 0
    assert data["latest"] is None and data["oldest"] is None
    assert data["total_size"] == "0 B"
    assert data["overdue"] is True
    code, stdout, _ = run(capsys, "status")
    assert "oldest: None" in stdout.splitlines()
    assert "total_size: 0 B" in stdout.splitlines()


def test_json_config_error(capsys):
    code, stdout, err = run(capsys, "status", "--json")
    assert code == 2
    assert stdout == ""
    assert err.strip().startswith("configuration error: ")
    assert "backup_dir" in err


def test_status_still_goes_through_its_handler():
    args = cli.build_parser().parse_args(["status", "--json"])
    assert args.handler is cli.cmd_status
    assert args.json is True

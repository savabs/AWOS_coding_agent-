"""Hidden acceptance tests for the settings.ini -> APP_* environment migration."""

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backupd import archiver, filters, health, notifier, scheduler, storage
from backupd.cli import main
from backupd.config import ConfigError, load_config
from backupd.logging_setup import configure_logging

NOW = datetime(2026, 3, 10, 12, 0, 0)
PROJECT_ROOT = Path(__file__).resolve().parents[1]

ALL_VARS = [
    "APP_SOURCE_DIR", "APP_BACKUP_DIR", "APP_INTERVAL_MINUTES", "APP_RETENTION_DAYS",
    "APP_COMPRESS", "APP_EXCLUDE", "APP_NOTIFY_EMAIL", "APP_SMTP_HOST", "APP_SMTP_PORT",
    "APP_LOG_LEVEL", "APP_LOG_FILE",
]


def test_env_only_every_setting_typed(full_env, tmp_path, src_tree):
    assert not (tmp_path / "settings.ini").exists()
    cfg = load_config()
    assert Path(cfg.source_dir) == src_tree
    assert Path(cfg.backup_dir) == tmp_path / "env_out"
    assert cfg.interval_minutes == 15 and type(cfg.interval_minutes) is int
    assert cfg.retention_days == 3 and type(cfg.retention_days) is int
    assert cfg.compress is False
    assert list(cfg.exclude) == ["*.tmp", "cache"]
    assert cfg.notify_email == "ops@example.com"
    assert cfg.smtp_host == "mail.internal"
    assert cfg.smtp_port == 2525 and type(cfg.smtp_port) is int
    assert cfg.log_level == "DEBUG"
    assert Path(cfg.log_file) == tmp_path / "logs" / "env.log"


def test_env_only_defaults_and_optionals(tmp_path, src_tree, monkeypatch):
    monkeypatch.setenv("APP_SOURCE_DIR", str(src_tree))
    monkeypatch.setenv("APP_BACKUP_DIR", str(tmp_path / "out"))
    cfg = load_config()
    assert cfg.interval_minutes == 60
    assert cfg.retention_days == 7
    assert cfg.compress is True
    assert list(cfg.exclude) == []
    assert cfg.notify_email is None
    assert cfg.smtp_host == "localhost"
    assert cfg.smtp_port == 25
    assert cfg.log_level == "INFO"
    assert cfg.log_file is None


def test_ini_only_still_works(ini_file, tmp_path, src_tree):
    cfg = load_config()
    assert Path(cfg.source_dir) == src_tree
    assert Path(cfg.backup_dir) == tmp_path / "ini_out"
    assert cfg.interval_minutes == 30
    assert cfg.retention_days == 10
    assert cfg.compress is True
    assert list(cfg.exclude) == ["*.log"]
    assert cfg.notify_email == "ini@example.com"
    assert cfg.smtp_host == "ini-mail"
    assert cfg.smtp_port == 465
    assert cfg.log_level == "ERROR"
    # explicit path still accepted
    assert load_config(ini_file).retention_days == 10


def test_env_overrides_ini_per_key(ini_file, tmp_path, monkeypatch):
    monkeypatch.setenv("APP_INTERVAL_MINUTES", "5")
    monkeypatch.setenv("APP_COMPRESS", "false")
    monkeypatch.setenv("APP_SMTP_HOST", "env-mail")
    cfg = load_config()
    assert cfg.interval_minutes == 5
    assert cfg.compress is False
    assert cfg.smtp_host == "env-mail"
    # keys not set in the environment still come from the ini
    assert cfg.retention_days == 10
    assert cfg.smtp_port == 465
    assert Path(cfg.backup_dir) == tmp_path / "ini_out"
    assert list(cfg.exclude) == ["*.log"]


@pytest.mark.parametrize(
    "raw,expected",
    [("true", True), ("1", True), ("yes", True), ("TRUE", True), ("Yes", True),
     ("false", False), ("0", False), ("no", False), ("FALSE", False)],
)
def test_bool_parsing(raw, expected, tmp_path, src_tree, monkeypatch):
    monkeypatch.setenv("APP_SOURCE_DIR", str(src_tree))
    monkeypatch.setenv("APP_BACKUP_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("APP_COMPRESS", raw)
    assert load_config().compress is expected
    assert archiver.archive_name(load_config(), NOW).endswith(".zip" if expected else ".tar")


def test_list_parsing(tmp_path, src_tree, monkeypatch):
    monkeypatch.setenv("APP_SOURCE_DIR", str(src_tree))
    monkeypatch.setenv("APP_BACKUP_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("APP_EXCLUDE", " *.tmp ,cache,, node_modules ")
    assert list(load_config().exclude) == ["*.tmp", "cache", "node_modules"]
    monkeypatch.setenv("APP_EXCLUDE", "single")
    assert list(load_config().exclude) == ["single"]


def test_missing_required_setting_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_BACKUP_DIR", str(tmp_path / "out"))
    with pytest.raises(ConfigError) as info:
        load_config()
    assert "SOURCE_DIR" in str(info.value).upper()


def test_scheduler_filters_archiver_honour_env(full_env, ini_file, src_tree, tmp_path):
    # ini says interval 30 / compress true / exclude *.log; env must win
    cfg = load_config()
    last = NOW - timedelta(minutes=10)
    assert scheduler.next_run(cfg, last) == last + timedelta(minutes=15)
    assert scheduler.is_due(cfg, NOW - timedelta(minutes=16), NOW)
    files = sorted(p.as_posix() for p in filters.iter_files(cfg))
    assert files == ["docs/a.txt", "top.txt"]
    assert archiver.archive_name(cfg, NOW) == "backup-20260310-120000.tar"


def test_storage_honours_env(full_env, ini_file, tmp_path):
    assert Path(storage.backup_root()) == tmp_path / "env_out"
    assert storage.retention_days() == 3
    out = storage.ensure_backup_root()
    old = out / "backup-20260305-000000.zip"
    new = out / "backup-20260309-000000.zip"
    old.write_text("o")
    new.write_text("n")
    assert storage.prune_old_backups(NOW) == [old]
    assert new.exists()


def test_notifier_honours_env(full_env, ini_file):
    sent = []
    n = notifier.build_notifier(transport=sent.append)
    assert (n.recipient, n.smtp_host, n.smtp_port) == ("ops@example.com", "mail.internal", 2525)
    n.send("hello", "body")
    assert sent[0]["To"] == "ops@example.com"


def test_logging_honours_env(full_env, ini_file, tmp_path):
    logger = configure_logging()
    assert logger.level == logging.DEBUG
    logger.debug("probe")
    for h in logger.handlers:
        h.flush()
    assert (tmp_path / "logs" / "env.log").exists()
    assert not (tmp_path / "logs" / "ini.log").exists()


def test_health_status_honours_env(tmp_path, src_tree, monkeypatch):
    monkeypatch.setenv("APP_SOURCE_DIR", str(src_tree))
    monkeypatch.setenv("APP_BACKUP_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("APP_INTERVAL_MINUTES", "20")
    monkeypatch.setenv("APP_NOTIFY_EMAIL", "ops@example.com")
    report = health.status_report(now=NOW)
    assert report["interval_minutes"] == 20
    assert report["notifications"] is True
    assert Path(report["backup_dir"]) == tmp_path / "out"


def test_cli_run_env_only(full_env, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("APP_NOTIFY_EMAIL")  # no real SMTP in tests
    assert main(["run"]) == 0
    backups = storage.list_backups(tmp_path / "env_out")
    assert len(backups) == 1
    assert backups[0].suffix == ".tar"
    assert archiver.archive_members(backups[0]) == ["docs/a.txt", "top.txt"]


def test_readme_documents_every_variable():
    text = (PROJECT_ROOT / "README.md").read_text()
    missing = [v for v in ALL_VARS if not re.search(r"\b" + v + r"\b", text)]
    assert not missing, f"README does not mention: {missing}"

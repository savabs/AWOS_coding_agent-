from datetime import datetime, timedelta
from pathlib import Path

from backupd import archiver, filters, health, notifier, scheduler, storage
from backupd.config import load_config
from backupd.logging_setup import configure_logging

NOW = datetime(2026, 3, 10, 12, 0, 0)


def test_scheduler_uses_interval(write_ini):
    write_ini(extra_backup="interval_minutes = 30")
    cfg = load_config()
    last = NOW - timedelta(minutes=10)
    assert scheduler.next_run(cfg, last) == last + timedelta(minutes=30)
    assert not scheduler.is_due(cfg, last, NOW)
    assert scheduler.is_due(cfg, NOW - timedelta(minutes=31), NOW)
    assert scheduler.is_due(cfg, None, NOW)


def test_last_run_roundtrip(write_ini):
    write_ini()
    cfg = load_config()
    assert scheduler.load_last_run(cfg) is None
    scheduler.save_last_run(cfg, NOW)
    assert scheduler.load_last_run(cfg) == NOW


def test_filters(write_ini):
    write_ini(extra_backup="exclude = *.tmp, node_modules")
    cfg = load_config()
    files = [p.as_posix() for p in filters.iter_files(cfg)]
    assert files == ["docs/a.txt", "top.txt"]


def test_archive_zip_and_tar(write_ini, workspace):
    write_ini()
    cfg = load_config()
    assert archiver.archive_name(cfg, NOW) == "backup-20260310-120000.zip"
    path = archiver.create_archive(cfg, [Path("top.txt")], NOW, workspace / "out")
    assert archiver.archive_members(path) == ["top.txt"]

    write_ini(extra_backup="compress = off")
    cfg = load_config()
    assert archiver.archive_name(cfg, NOW).endswith(".tar")


def test_storage_prune(write_ini, workspace):
    write_ini(extra_backup="retention_days = 3")
    assert storage.backup_root() == workspace / "out"
    assert storage.retention_days() == 3
    out = storage.ensure_backup_root()
    old = out / "backup-20260301-000000.zip"
    new = out / "backup-20260309-000000.zip"
    old.write_text("o")
    new.write_text("n")
    removed = storage.prune_old_backups(NOW)
    assert removed == [old]
    assert new.exists()


def test_notifier(write_ini):
    write_ini(notify="notify_email = ops@example.com\nsmtp_host = mail\nsmtp_port = 587")
    sent = []
    n = notifier.build_notifier(transport=sent.append)
    assert (n.recipient, n.smtp_host, n.smtp_port) == ("ops@example.com", "mail", 587)
    n.send("hi", "body")
    assert sent and sent[0]["To"] == "ops@example.com"


def test_notifier_disabled(write_ini):
    write_ini()
    n = notifier.build_notifier(transport=lambda m: None)
    assert not n.enabled
    assert n.send("hi", "body") is None


def test_logging(write_ini, workspace):
    write_ini(logging_section=f"log_level = warning\nlog_file = {workspace / 'log' / 'b.log'}")
    logger = configure_logging()
    assert logger.level == 30
    assert (workspace / "log" / "b.log").exists()
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()


def test_health(write_ini):
    write_ini(extra_backup="interval_minutes = 45")
    report = health.status_report(now=NOW)
    assert report["interval_minutes"] == 45
    assert report["backups"] == 0
    assert report["overdue"] is True

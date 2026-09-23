from pathlib import Path

import pytest

from backupd.config import ConfigError, load_config, split_list


def test_defaults(write_ini, workspace):
    write_ini()
    cfg = load_config()
    assert cfg.source_dir == workspace / "src"
    assert cfg.backup_dir == workspace / "out"
    assert cfg.interval_minutes == 60
    assert cfg.retention_days == 7
    assert cfg.compress is True
    assert cfg.exclude == []
    assert cfg.notify_email is None
    assert cfg.smtp_host == "localhost"
    assert cfg.smtp_port == 25
    assert cfg.log_level == "INFO"
    assert cfg.log_file is None


def test_typed_values(write_ini, workspace):
    write_ini(
        extra_backup="interval_minutes = 15\nretention_days = 2\ncompress = no\nexclude = *.tmp, .cache ,",
        notify="notify_email = me@example.com\nsmtp_port = 2525",
        logging_section="log_level = debug\nlog_file = logs/b.log",
    )
    cfg = load_config()
    assert cfg.interval_minutes == 15
    assert cfg.retention_days == 2
    assert cfg.compress is False
    assert cfg.exclude == ["*.tmp", ".cache"]
    assert cfg.notify_email == "me@example.com"
    assert cfg.smtp_port == 2525
    assert cfg.log_level == "DEBUG"
    assert cfg.log_file == Path("logs/b.log")


def test_explicit_path(write_ini):
    path = write_ini(name="other.ini")
    cfg = load_config(path)
    assert cfg.retention_days == 7


def test_bad_int(write_ini):
    write_ini(extra_backup="interval_minutes = soon")
    with pytest.raises(ConfigError):
        load_config()


def test_bad_log_level(write_ini):
    write_ini(logging_section="log_level = LOUD")
    with pytest.raises(ConfigError):
        load_config()


def test_split_list():
    assert split_list(" a, b ,,c ") == ["a", "b", "c"]
    assert split_list("") == []

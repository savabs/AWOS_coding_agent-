import logging
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    """No APP_* leakage, cwd = empty temp dir (so no settings.ini unless written)."""
    for name in list(os.environ):
        if name.startswith("APP_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    yield
    logger = logging.getLogger("backupd")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()


@pytest.fixture
def src_tree(tmp_path):
    src = tmp_path / "src"
    (src / "docs").mkdir(parents=True)
    (src / "docs" / "a.txt").write_text("alpha")
    (src / "docs" / "b.tmp").write_text("scratch")
    (src / "cache").mkdir()
    (src / "cache" / "c.bin").write_text("c")
    (src / "top.txt").write_text("top")
    return src


@pytest.fixture
def full_env(tmp_path, src_tree, monkeypatch):
    """Every setting provided via APP_* environment variables only."""
    env = {
        "APP_SOURCE_DIR": str(src_tree),
        "APP_BACKUP_DIR": str(tmp_path / "env_out"),
        "APP_INTERVAL_MINUTES": "15",
        "APP_RETENTION_DAYS": "3",
        "APP_COMPRESS": "no",
        "APP_EXCLUDE": "*.tmp, cache",
        "APP_NOTIFY_EMAIL": "ops@example.com",
        "APP_SMTP_HOST": "mail.internal",
        "APP_SMTP_PORT": "2525",
        "APP_LOG_LEVEL": "debug",
        "APP_LOG_FILE": str(tmp_path / "logs" / "env.log"),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


@pytest.fixture
def ini_file(tmp_path, src_tree):
    """A legacy settings.ini in the working directory with non-default values."""
    path = tmp_path / "settings.ini"
    path.write_text(
        "\n".join(
            [
                "[backup]",
                f"source_dir = {src_tree}",
                f"backup_dir = {tmp_path / 'ini_out'}",
                "interval_minutes = 30",
                "retention_days = 10",
                "compress = true",
                "exclude = *.log",
                "",
                "[notify]",
                "notify_email = ini@example.com",
                "smtp_host = ini-mail",
                "smtp_port = 465",
                "",
                "[logging]",
                "log_level = ERROR",
                f"log_file = {tmp_path / 'logs' / 'ini.log'}",
                "",
            ]
        )
    )
    return path

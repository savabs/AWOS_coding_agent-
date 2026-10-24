"""Job 09: notify_on = always | failure | never, and mails about failed runs."""

import logging
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from backupd import runner, scheduler  # noqa: E402
from backupd.cli import main  # noqa: E402
from backupd.config import load_config  # noqa: E402
from backupd.errors import BackupdError, ConfigError  # noqa: E402

NOW = datetime(2026, 3, 10, 12, 0, 0)


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
def write_ini(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("alpha")

    def _write(notify="notify_email = ops@example.com", backup="", source=src):
        path = tmp_path / "settings.ini"
        path.write_text(
            f"[backup]\nsource_dir = {source}\nbackup_dir = {tmp_path / 'out'}\n{backup}\n"
            f"[notify]\n{notify}\n")
        return str(path)

    return _write


def run_and_collect(settings, **kwargs):
    sent = []
    cfg = load_config(settings)
    error = None
    try:
        result = runner.run_backup(cfg, settings, now=NOW, transport=sent.append, **kwargs)
    except BackupdError as exc:
        error, result = exc, None
    return sent, result, error


def test_setting_parsing(write_ini, monkeypatch):
    settings = write_ini()
    assert load_config(settings).notify_on == "always"
    settings = write_ini("notify_email = ops@example.com\nnotify_on = Failure")
    assert load_config(settings).notify_on == "failure"
    monkeypatch.setenv("APP_NOTIFY_ON", "NEVER")
    assert load_config(settings).notify_on == "never"
    monkeypatch.setenv("APP_NOTIFY_ON", "")
    assert load_config(settings).notify_on == "failure"


def test_invalid_value_is_config_error(write_ini, monkeypatch, capsys):
    write_ini()
    monkeypatch.setenv("APP_NOTIFY_ON", "sometimes")
    with pytest.raises(ConfigError) as info:
        load_config()
    assert "notify_on" in str(info.value)
    assert main(["run"]) == 2
    err = capsys.readouterr().err
    assert err.strip().splitlines()[-1].startswith("configuration error: ")
    assert "notify_on" in err


def test_show_config(write_ini, capsys):
    write_ini("notify_on = failure")
    assert main(["show-config"]) == 0
    assert "notify_on = 'failure'" in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("mode,mails", [("always", 1), ("failure", 0), ("never", 0)])
def test_successful_run(write_ini, mode, mails):
    settings = write_ini(f"notify_email = ops@example.com\nnotify_on = {mode}")
    sent, result, error = run_and_collect(settings)
    assert error is None
    assert len(sent) == mails
    assert result.notified is bool(mails)
    if mails:
        assert sent[0]["Subject"] == "[backupd] backup complete"


@pytest.mark.parametrize("mode,mails", [("always", 1), ("failure", 1), ("never", 0)])
def test_failed_run(write_ini, tmp_path, mode, mails):
    missing = tmp_path / "unmounted"
    settings = write_ini(f"notify_email = ops@example.com\nnotify_on = {mode}", source=missing)
    sent, _, error = run_and_collect(settings)
    assert error is not None and str(missing) in str(error)
    assert len(sent) == mails
    if mails:
        msg = sent[0]
        assert msg["Subject"] == "[backupd] backup failed"
        assert msg["To"] == "ops@example.com"
        assert str(missing) in msg.get_content()


def test_env_mode_wins(write_ini, tmp_path, monkeypatch):
    settings = write_ini("notify_email = ops@example.com\nnotify_on = failure",
                         source=tmp_path / "gone")
    monkeypatch.setenv("APP_NOTIFY_ON", "never")
    sent, _, error = run_and_collect(settings)
    assert error is not None
    assert sent == []


def test_space_failure_is_mailed(write_ini):
    settings = write_ini("notify_email = ops@example.com\nnotify_on = failure",
                         backup="min_free_space = 900T")
    sent, _, error = run_and_collect(settings)
    assert error is not None
    assert len(sent) == 1
    assert "900.0 TiB" in sent[0].get_content()


def test_no_recipient_no_mail(write_ini, tmp_path):
    settings = write_ini("notify_on = always", source=tmp_path / "gone")
    sent, _, error = run_and_collect(settings)
    assert error is not None
    assert sent == []


def test_skipped_run_sends_nothing(write_ini):
    settings = write_ini("notify_email = ops@example.com\nnotify_on = always")
    cfg = load_config(settings)
    scheduler.save_last_run(cfg, NOW - timedelta(minutes=5))
    sent = []
    result = runner.run_backup(cfg, settings, now=NOW, force=False, transport=sent.append)
    assert result.skipped
    assert sent == []


def test_unreachable_mail_server_does_not_hide_the_error(write_ini, tmp_path, capsys):
    missing = tmp_path / "unmounted"
    write_ini("notify_email = ops@example.com\nnotify_on = failure\n"
              "smtp_host = 127.0.0.1\nsmtp_port = 1", source=missing)
    code = main(["run"])
    err = capsys.readouterr().err
    assert code == 1
    assert "Traceback" not in err
    last = err.strip().splitlines()[-1]
    assert last.startswith("error: ")
    assert str(missing) in last

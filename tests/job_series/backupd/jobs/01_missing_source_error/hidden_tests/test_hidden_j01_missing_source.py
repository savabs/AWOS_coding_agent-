"""Job 01: a missing / unusable source directory is a clean user error."""

import logging
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from backupd import runner, storage  # noqa: E402
from backupd.cli import main  # noqa: E402
from backupd.config import load_config  # noqa: E402
from backupd.errors import BackupdError  # noqa: E402


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


def write_ini(tmp_path, source):
    out = tmp_path / "out"
    (tmp_path / "settings.ini").write_text(
        f"[backup]\nsource_dir = {source}\nbackup_dir = {out}\nretention_days = 7\n")
    return out


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def assert_clean_error(err, *fragments):
    assert "Traceback" not in err
    lines = err.strip().splitlines()
    assert lines, "nothing printed on stderr"
    assert lines[-1].startswith("error: "), lines[-1]
    for fragment in fragments:
        assert fragment in lines[-1]


def test_missing_source_is_clean_error(tmp_path, capsys):
    missing = tmp_path / "unmounted" / "data"
    out = write_ini(tmp_path, missing)
    code, stdout, err = run(capsys, "run")
    assert code == 1
    assert_clean_error(err, str(missing))
    assert "backup written" not in stdout
    assert storage.list_backups(out) == []
    assert not (out / ".last_run").exists()


def test_source_that_is_a_file(tmp_path, capsys):
    src = tmp_path / "data.txt"
    src.write_text("not a folder")
    out = write_ini(tmp_path, src)
    code, _, err = run(capsys, "run")
    assert code == 1
    assert_clean_error(err, str(src))
    assert storage.list_backups(out) == []


def test_env_source_dir_wins_and_is_reported(tmp_path, capsys, monkeypatch):
    good = tmp_path / "src"
    good.mkdir()
    (good / "a.txt").write_text("a")
    write_ini(tmp_path, good)
    monkeypatch.setenv("APP_SOURCE_DIR", str(tmp_path / "gone"))
    code, _, err = run(capsys, "run")
    assert code == 1
    assert_clean_error(err, str(tmp_path / "gone"))


def test_failed_run_prunes_nothing(tmp_path, capsys):
    out = write_ini(tmp_path, tmp_path / "missing")
    out.mkdir()
    old = out / "backup-20200101-000000.zip"
    old.write_text("old but precious")
    code, _, _ = run(capsys, "run")
    assert code == 1
    assert old.exists()


def test_run_backup_raises_project_error(tmp_path):
    write_ini(tmp_path, tmp_path / "missing")
    cfg = load_config()
    with pytest.raises(BackupdError) as info:
        runner.run_backup(cfg, "settings.ini")
    assert str(tmp_path / "missing") in str(info.value)


def test_real_cli_has_no_traceback(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("APP_")}
    env["APP_SOURCE_DIR"] = str(tmp_path / "nope")
    env["APP_BACKUP_DIR"] = str(tmp_path / "out")
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run([sys.executable, "-m", "backupd", "run"], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1
    assert_clean_error(proc.stderr, str(tmp_path / "nope"))


def test_good_run_still_works(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    out = write_ini(tmp_path, src)
    code, stdout, _ = run(capsys, "run")
    assert code == 0
    assert "backup written" in stdout
    assert len(storage.list_backups(out)) == 1

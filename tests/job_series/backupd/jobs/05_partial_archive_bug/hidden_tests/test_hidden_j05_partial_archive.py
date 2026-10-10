"""Job 05: a failed archive write never leaves a half-written backup behind."""

import logging
import os
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from backupd import health, runner, storage  # noqa: E402
from backupd.cli import main  # noqa: E402
from backupd.config import load_config  # noqa: E402
from backupd.errors import BackupdError  # noqa: E402

OLD = "backup-20260101-000000.zip"


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
def setup(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (src / name).write_text(name * 50)
    out = tmp_path / "out"
    out.mkdir()
    (out / OLD).write_bytes(b"previous good backup")

    def _write(extra=""):
        (tmp_path / "settings.ini").write_text(
            f"[backup]\nsource_dir = {src}\nbackup_dir = {out}\nretention_days = 100000\n{extra}\n")
        return out

    return _write


def failing_after_first(monkeypatch, cls, method, seen, out):
    original = getattr(cls, method)
    calls = []

    def flaky(self, *args, **kwargs):
        # What does the backup folder look like while the archive is being written?
        seen.append([p.name for p in storage.list_backups(out)])
        calls.append(1)
        if len(calls) > 1:
            raise OSError(28, "No space left on device")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(cls, method, flaky)


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("compress,cls,method", [
    ("true", zipfile.ZipFile, "write"),
    ("false", tarfile.TarFile, "add"),
])
def test_failed_write_leaves_nothing_behind(setup, capsys, monkeypatch, compress, cls, method):
    out = setup(f"compress = {compress}")
    seen = []
    failing_after_first(monkeypatch, cls, method, seen, out)
    code, stdout, err = run(capsys, "run")
    assert code == 1
    assert "Traceback" not in err
    last = err.strip().splitlines()[-1]
    assert last.startswith("error: ")
    assert "No space left on device" in last
    assert "backup written" not in stdout
    assert sorted(p.name for p in out.iterdir()) == [OLD]
    # while writing, the unfinished archive never counted as a backup
    assert seen and all(names == [OLD] for names in seen)


def test_monitoring_still_sees_the_previous_backup(setup, capsys, monkeypatch):
    out = setup()
    failing_after_first(monkeypatch, zipfile.ZipFile, "write", [], out)
    assert run(capsys, "run")[0] == 1
    report = health.status_report("settings.ini")
    assert report["backups"] == 1
    assert report["latest"] == "2026-01-01T00:00:00"


def test_run_backup_raises_project_error(setup, monkeypatch):
    out = setup()
    failing_after_first(monkeypatch, zipfile.ZipFile, "write", [], out)
    cfg = load_config()
    with pytest.raises(BackupdError):
        runner.run_backup(cfg, "settings.ini")
    assert not (out / ".last_run").exists()


def test_archive_is_only_visible_once_complete(setup, capsys, monkeypatch):
    out = setup()
    seen = []
    original = zipfile.ZipFile.write

    def watching(self, *args, **kwargs):
        seen.append([p.name for p in storage.list_backups(out)])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", watching)
    code, _, _ = run(capsys, "run")
    assert code == 0
    assert seen and all(names == [OLD] for names in seen)


def test_successful_run_leaves_no_temp_files(setup, capsys):
    out = setup()
    code, stdout, _ = run(capsys, "run")
    assert code == 0
    names = sorted(p.name for p in out.iterdir())
    backups = [p.name for p in storage.list_backups(out)]
    assert len(backups) == 2
    assert names == sorted(backups + [".last_run"])
    newest = storage.list_backups(out)[-1]
    with zipfile.ZipFile(newest) as zf:
        assert sorted(zf.namelist()) == ["a.txt", "b.txt", "c.txt"]
        assert zf.testzip() is None

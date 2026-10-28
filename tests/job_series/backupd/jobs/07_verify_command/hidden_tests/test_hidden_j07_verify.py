"""Job 07: `backupd verify [NAME]` reads an archive back."""

import io
import logging
import os
import re
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from backupd import cli  # noqa: E402

LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO backupd(\.[\w.]+)?: ")


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


def make_zip(path, members):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def make_tar(path, members):
    with tarfile.open(path, "w") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


@pytest.fixture
def out(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    make_tar(out / "backup-20200308-093005.tar", {"a.txt": b"a" * 5000, "d/b.txt": b"b"})
    make_zip(out / "backup-20200310-120000.zip", {"a.txt": b"a", "b.txt": b"b", "c/d.txt": b"d"})
    make_zip(out / "backup-20200312-120000.zip", {"x.txt": b"x" * 10, "y.txt": b"y"})
    (out / "notes.txt").write_text("not a backup")
    monkeypatch.setenv("APP_BACKUP_DIR", str(out))
    return out


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def assert_clean_error(err, *fragments):
    assert "Traceback" not in err
    last = err.strip().splitlines()[-1]
    assert last.startswith("error: "), last
    for fragment in fragments:
        assert fragment in last


def test_verify_latest_by_default(out, capsys):
    code, stdout, _ = run(capsys, "verify")
    assert code == 0
    assert stdout.strip() == "ok: backup-20200312-120000.zip (2 files)"


def test_verify_named_zip_and_tar(out, capsys):
    code, stdout, _ = run(capsys, "verify", "backup-20200310-120000.zip")
    assert (code, stdout.strip()) == (0, "ok: backup-20200310-120000.zip (3 files)")
    code, stdout, _ = run(capsys, "verify", "backup-20200308-093005.tar")
    assert (code, stdout.strip()) == (0, "ok: backup-20200308-093005.tar (2 files)")


def test_garbage_zip_is_reported(out, capsys):
    (out / "backup-20200312-120000.zip").write_bytes(b"this is not a zip file at all" * 20)
    code, stdout, err = run(capsys, "verify")
    assert code == 1
    assert_clean_error(err, "backup-20200312-120000.zip")
    assert "ok:" not in stdout


def test_truncated_archives_are_reported(out, capsys):
    for name in ("backup-20200310-120000.zip", "backup-20200308-093005.tar"):
        data = (out / name).read_bytes()
        (out / name).write_bytes(data[: len(data) // 2])
        code, _, err = run(capsys, "verify", name)
        assert code == 1
        assert_clean_error(err, name)


def test_corrupted_zip_member_is_reported(out, capsys):
    path = out / "backup-20200312-120000.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("big.txt", b"A" * 4000)
    data = bytearray(path.read_bytes())
    pos = data.index(b"A" * 100) + 50
    data[pos:pos + 10] = b"B" * 10
    path.write_bytes(bytes(data))
    code, _, err = run(capsys, "verify")
    assert code == 1
    assert_clean_error(err, "backup-20200312-120000.zip")


def test_unknown_name(out, capsys):
    code, _, err = run(capsys, "verify", "backup-19990101-000000.zip")
    assert code == 1
    assert_clean_error(err, "backup-19990101-000000.zip")
    code, _, err = run(capsys, "verify", "notes.txt")
    assert code == 1
    assert_clean_error(err, "notes.txt")


def test_no_backups(tmp_path, capsys, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("APP_BACKUP_DIR", str(empty))
    code, _, err = run(capsys, "verify")
    assert code == 1
    assert_clean_error(err, str(empty))


def test_backup_dir_from_ini_with_variables(out, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("APP_BACKUP_DIR")
    monkeypatch.setenv("BK_HOME", str(tmp_path))
    (tmp_path / "settings.ini").write_text("[backup]\nbackup_dir = $BK_HOME/out\n")
    code, stdout, _ = run(capsys, "verify")
    assert code == 0
    assert stdout.strip() == "ok: backup-20200312-120000.zip (2 files)"


def test_verification_is_logged(out, tmp_path, capsys, monkeypatch):
    log_file = tmp_path / "logs" / "backupd.log"
    monkeypatch.setenv("APP_LOG_FILE", str(log_file))
    code, _, _ = run(capsys, "verify", "backup-20200310-120000.zip")
    assert code == 0
    lines = [ln for ln in log_file.read_text().splitlines()
             if "backup-20200310-120000.zip" in ln]
    assert lines, "verification not written to the log file"
    assert LOG_LINE.match(lines[-1]), lines[-1]


def test_registered_like_the_other_commands():
    args = cli.build_parser().parse_args(["verify"])
    assert args.handler is cli.cmd_verify
    args = cli.build_parser().parse_args(["verify", "backup-20200310-120000.zip"])
    assert args.handler is cli.cmd_verify

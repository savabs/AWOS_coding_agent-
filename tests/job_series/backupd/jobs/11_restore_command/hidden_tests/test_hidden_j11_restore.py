"""Job 11: `backupd restore NAME DEST`."""

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
LATEST = "backup-20200312-120000.zip"
TAR = "backup-20200308-093005.tar"


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
    make_tar(out / TAR, {"old.txt": b"old", "d/e.txt": b"eee"})
    make_zip(out / "backup-20200310-120000.zip", {"mid.txt": b"mid"})
    make_zip(out / LATEST, {"a.txt": b"alpha", "docs/b.txt": b"beta", "docs/deep/c.txt": b"c"})
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


def tree(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_restore_latest(out, tmp_path, capsys):
    dest = tmp_path / "restored"
    code, stdout, _ = run(capsys, "restore", "latest", str(dest))
    assert code == 0
    assert stdout.strip() == f"restored 3 files from {LATEST} to {dest}"
    assert tree(dest) == ["a.txt", "docs/b.txt", "docs/deep/c.txt"]
    assert (dest / "docs" / "deep" / "c.txt").read_bytes() == b"c"


def test_restore_named_tar_into_empty_dir(out, tmp_path, capsys):
    dest = tmp_path / "empty"
    dest.mkdir()
    code, stdout, _ = run(capsys, "restore", TAR, str(dest))
    assert code == 0
    assert stdout.strip() == f"restored 2 files from {TAR} to {dest}"
    assert tree(dest) == ["d/e.txt", "old.txt"]


def test_dest_paths_are_expanded_like_settings(out, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    code, _, _ = run(capsys, "restore", "latest", "~/r1")
    assert code == 0
    assert tree(tmp_path / "home" / "r1") == ["a.txt", "docs/b.txt", "docs/deep/c.txt"]
    monkeypatch.setenv("RESTORE_ROOT", str(tmp_path / "restores"))
    code, stdout, _ = run(capsys, "restore", "latest", "$RESTORE_ROOT/r2")
    assert code == 0
    assert tree(tmp_path / "restores" / "r2") == ["a.txt", "docs/b.txt", "docs/deep/c.txt"]
    assert str(tmp_path / "restores" / "r2") in stdout


def test_refuses_non_empty_destination(out, tmp_path, capsys):
    dest = tmp_path / "busy"
    dest.mkdir()
    (dest / "a.txt").write_text("mine, do not touch")
    code, stdout, err = run(capsys, "restore", "latest", str(dest))
    assert code == 1
    assert_clean_error(err, str(dest))
    assert tree(dest) == ["a.txt"]
    assert (dest / "a.txt").read_text() == "mine, do not touch"


def test_refuses_file_as_destination(out, tmp_path, capsys):
    dest = tmp_path / "file.txt"
    dest.write_text("x")
    code, _, err = run(capsys, "restore", "latest", str(dest))
    assert code == 1
    assert_clean_error(err, str(dest))


@pytest.mark.parametrize("maker,name,members", [
    (make_zip, "backup-20200313-000000.zip", {"ok.txt": b"ok", "../evil.txt": b"evil"}),
    (make_tar, "backup-20200313-000000.tar", {"ok.txt": b"ok", "../evil.txt": b"evil"}),
    (make_tar, "backup-20200313-000000.tar", {"ok.txt": b"ok", "/tmp/abs-evil.txt": b"evil"}),
])
def test_unsafe_archives_extract_nothing(out, tmp_path, capsys, maker, name, members):
    maker(out / name, members)
    dest = tmp_path / "work" / "dest"
    code, _, err = run(capsys, "restore", name, str(dest))
    assert code == 1
    assert_clean_error(err, name)
    assert not (tmp_path / "work" / "evil.txt").exists()
    assert not dest.exists() or tree(dest) == []


def test_unknown_backup(out, tmp_path, capsys):
    code, _, err = run(capsys, "restore", "backup-19990101-000000.zip", str(tmp_path / "d"))
    assert code == 1
    assert_clean_error(err, "backup-19990101-000000.zip")


def test_damaged_backup(out, tmp_path, capsys):
    (out / LATEST).write_bytes(b"garbage" * 100)
    code, _, err = run(capsys, "restore", "latest", str(tmp_path / "d"))
    assert code == 1
    assert_clean_error(err, LATEST)


def test_restore_is_logged(out, tmp_path, capsys, monkeypatch):
    log_file = tmp_path / "logs" / "b.log"
    monkeypatch.setenv("APP_LOG_FILE", str(log_file))
    code, _, _ = run(capsys, "restore", "latest", str(tmp_path / "d"))
    assert code == 0
    lines = [ln for ln in log_file.read_text().splitlines() if LATEST in ln]
    assert lines, "restore not written to the log file"
    assert LOG_LINE.match(lines[-1]), lines[-1]


def test_registered_like_the_other_commands():
    args = cli.build_parser().parse_args(["restore", "latest", "/tmp/x"])
    assert args.handler is cli.cmd_restore

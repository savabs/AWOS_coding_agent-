"""Job 04: the min_free_space setting (APP_MIN_FREE_SPACE), a size like 500M."""

import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from backupd import storage  # noqa: E402
from backupd.cli import main  # noqa: E402
from backupd.config import load_config  # noqa: E402
from backupd.errors import ConfigError  # noqa: E402

HUGE = "900T"          # no test machine has this much free space
HUGE_TEXT = "900.0 TiB"


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

    def _write(extra=""):
        (tmp_path / "settings.ini").write_text(
            f"[backup]\nsource_dir = {src}\nbackup_dir = {tmp_path / 'out'}\n{extra}\n")
        return tmp_path / "out"

    return _write


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("raw,expected", [
    ("500M", 500 * 1024 ** 2),
    ("2G", 2 * 1024 ** 3),
    ("1.5GiB", int(1.5 * 1024 ** 3)),
    ("64k", 64 * 1024),
    ("4096", 4096),
])
def test_sizes_from_ini(write_ini, raw, expected):
    write_ini(f"min_free_space = {raw}")
    assert load_config().min_free_space == expected


def test_default_is_off(write_ini):
    write_ini()
    assert load_config().min_free_space == 0


def test_env_wins_and_empty_env_is_unset(write_ini, monkeypatch):
    write_ini("min_free_space = 2G")
    monkeypatch.setenv("APP_MIN_FREE_SPACE", "10M")
    assert load_config().min_free_space == 10 * 1024 ** 2
    monkeypatch.setenv("APP_MIN_FREE_SPACE", "")
    assert load_config().min_free_space == 2 * 1024 ** 3


def test_bad_size_is_config_error(write_ini, monkeypatch, capsys):
    write_ini()
    monkeypatch.setenv("APP_MIN_FREE_SPACE", "plenty")
    with pytest.raises(ConfigError) as info:
        load_config()
    assert "min_free_space" in str(info.value)
    code, _, err = run(capsys, "run")
    assert code == 2
    assert err.strip().splitlines()[-1].startswith("configuration error: ")
    assert "min_free_space" in err


def test_not_enough_space_refuses_the_run(write_ini, capsys):
    out = write_ini(f"min_free_space = {HUGE}")
    code, stdout, err = run(capsys, "run")
    assert code == 1
    assert "Traceback" not in err
    last = err.strip().splitlines()[-1]
    assert last.startswith("error: ")
    assert str(out) in last
    assert HUGE_TEXT in last
    assert "backup written" not in stdout
    assert storage.list_backups(out) == []
    assert not (out / ".last_run").exists()


def test_env_can_lower_the_limit(write_ini, capsys, monkeypatch):
    out = write_ini(f"min_free_space = {HUGE}")
    monkeypatch.setenv("APP_MIN_FREE_SPACE", "1K")
    code, stdout, _ = run(capsys, "run")
    assert code == 0
    assert len(storage.list_backups(out)) == 1


def test_env_can_raise_the_limit(write_ini, capsys, monkeypatch):
    out = write_ini()
    monkeypatch.setenv("APP_MIN_FREE_SPACE", HUGE)
    code, _, err = run(capsys, "run")
    assert code == 1
    assert HUGE_TEXT in err
    assert storage.list_backups(out) == []


def test_show_config_lists_it_in_bytes(write_ini, capsys):
    write_ini("min_free_space = 500M")
    code, stdout, _ = run(capsys, "show-config")
    assert code == 0
    assert "min_free_space = 524288000" in stdout.splitlines()

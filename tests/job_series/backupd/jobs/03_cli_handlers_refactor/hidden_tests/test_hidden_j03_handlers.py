"""Job 03: every command is a cmd_<name>(args) handler registered with set_defaults."""

import argparse
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from backupd import cli  # noqa: E402
from backupd.errors import BackupdError, ConfigError  # noqa: E402

COMMANDS = {
    "run": ["run"],
    "status": ["status"],
    "due": ["due"],
    "show_config": ["show-config"],
    "list": ["list"],
}


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
def ini(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("alpha")
    path = tmp_path / "settings.ini"
    path.write_text(f"[backup]\nsource_dir = {src}\nbackup_dir = {tmp_path / 'out'}\n")
    return path


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_each_command_has_its_handler(name):
    args = cli.build_parser().parse_args(COMMANDS[name])
    handler = getattr(cli, f"cmd_{name}")
    assert args.handler is handler


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_main_dispatches_through_the_handler(name, monkeypatch, tmp_path):
    seen = []

    def fake(args):
        seen.append(args)
        # logging is configured before any handler runs
        assert logging.getLogger("backupd").handlers
        return 7

    monkeypatch.setattr(cli, f"cmd_{name}", fake)
    assert cli.main(["--config", str(tmp_path / "x.ini"), *COMMANDS[name]]) == 7
    assert len(seen) == 1
    assert seen[0].config == str(tmp_path / "x.ini")


def test_errors_from_handlers_are_reported_by_main(monkeypatch, capsys):
    def config_problem(args):
        raise ConfigError("setting 'x' is wrong")

    def user_problem(args):
        raise BackupdError("something broke")

    monkeypatch.setattr(cli, "cmd_status", config_problem)
    code, _, err = run(capsys, "status")
    assert (code, err.strip()) == (2, "configuration error: setting 'x' is wrong")

    monkeypatch.setattr(cli, "cmd_list", user_problem)
    code, _, err = run(capsys, "list")
    assert (code, err.strip()) == (1, "error: something broke")


def test_handlers_can_be_called_directly(ini, capsys):
    ns = argparse.Namespace(config=str(ini), command="list")
    assert cli.cmd_list(ns) == 0
    assert "no backups in" in capsys.readouterr().out


def test_behaviour_unchanged(ini, tmp_path, capsys):
    code, out, _ = run(capsys, "due")
    assert (code, out.strip()) == (0, "due")
    code, out, _ = run(capsys, "run")
    assert code == 0
    assert out.startswith(f"backup written: {tmp_path / 'out'}")
    assert "(1 files, " in out
    code, out, _ = run(capsys, "due")
    assert (code, out.strip()) == (1, "not due")
    code, out, _ = run(capsys, "run", "--if-due")
    assert (code, out.strip()) == (0, "backup not due")
    code, out, _ = run(capsys, "show-config")
    assert code == 0 and "interval_minutes = 60" in out
    code, out, _ = run(capsys, "status")
    assert code == 0 and "backups: 1" in out
    code, out, _ = run(capsys, "list")
    assert code == 0 and out.strip().splitlines()[-1].startswith("1 backup, ")


def test_config_errors_unchanged(tmp_path, capsys):
    (tmp_path / "settings.ini").write_text(f"[backup]\nbackup_dir = {tmp_path / 'out'}\n")
    for argv in (["run"], ["due"], ["show-config"]):
        code, _, err = run(capsys, *argv)
        assert code == 2
        assert err.strip().startswith("configuration error: ")
        assert "source_dir" in err

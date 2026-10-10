"""Repetition meter + report (compiled tools spec step 1)."""

import json
import subprocess
import sys
from pathlib import Path

from scaffold.agent.compiled import repetition as rep

ROOT = Path(__file__).resolve().parents[1]


def test_secrets_never_reach_template(tmp_path):
    goal = ("set OPENAI_API_KEY=sk-proj-AbCdEfGhIjKlMnOp and password hunter2 deploy "
            "with ghp_abcdefghijklmnopqrstuvwxyz12 and aB3dE5fG7hI9jK1lM3nO5pQ7rS")
    tpl = rep.goal_template(goal)
    for leak in ("sk-proj", "abcdefghijklmnop", "hunter2", "ghp_", "ab3de5fg7h"):
        assert leak not in tpl, tpl
    assert tpl.startswith("set openai_api_key <secret> and password <secret> deploy")
    entry = rep.log_task(goal, path=tmp_path / "log.jsonl")
    raw = (tmp_path / "log.jsonl").read_text().lower()
    assert entry and "hunter2" not in raw and "abcdefghijklmnop" not in raw
    # ordinary goals and commit ids keep their slots
    assert rep.goal_template("revert commit 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b") == \
        "revert commit <hex>"
    assert rep.goal_template("rotate the api key for the service") == \
        "rotate the api key for the service"


def test_apostrophes_are_not_str_slots():
    assert rep.goal_template("don't touch it's file") == "don t touch it s file"
    assert rep.goal_template("rename 'foo' to bar") == "rename <str> to bar"


def test_file_pattern_root_slash():
    assert rep.file_pattern(["/a/b.py", "/c.md"], root="/") == ["*.md", "a/*.py"]


def test_template_slots_values():
    a = rep.goal_template("Bump version to 0.9.4 in pyproject.toml")
    b = rep.goal_template("bump version to 1.2.0 in pyproject.toml")
    assert a == b == "bump version to <semver> in <path>"
    assert rep.goal_template('rename "foo" to `bar` at 12') == "rename <str> to <code> at <num>"


def test_family_slug():
    assert rep.family_of(rep.goal_template("Please bump the version to 1.0.0")) == "bump_version"


def test_file_pattern_root_normalised():
    pat = rep.file_pattern(["/r/x/scaffold/agent/a.py", "/r/x/scaffold/agent/b.py",
                            "/r/x/README.md", "./Makefile"], root="/r/x")
    assert pat == ["*", "*.md", "scaffold/agent/*.py"]


def test_action_shape_collapses():
    assert rep.action_shape(["read", "read", {"tool": "edit"}, {"name": "edit"}, "test"]) == \
        ["read", "edit", "test"]


def test_structure_key_stable_across_values():
    f1 = rep.fingerprint("bump version to 0.9.4", ["/r/pyproject.toml"], ["read", "edit"], "/r")
    f2 = rep.fingerprint("bump version to 2.0.1", ["/r/pyproject.toml"], ["read", "edit"], "/r")
    f3 = rep.fingerprint("bump version to 2.0.1", ["/r/setup.py"], ["read", "edit"], "/r")
    assert f1["structure_key"] == f2["structure_key"] != f3["structure_key"]
    assert f1["intent_key"] == f3["intent_key"]
    assert "0.9.4" not in json.dumps(f1)          # raw goal never stored


def test_log_off_under_pytest_by_default(monkeypatch):
    monkeypatch.delenv(rep.LOG_ENV, raising=False)
    assert rep.log_path() is None
    assert rep.log_task("x") is None
    monkeypatch.setenv(rep.LOG_ENV, "off")
    assert rep.log_path() is None


def test_log_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv(rep.LOG_ENV, str(blocker / "sub" / "log.jsonl"))
    assert rep.log_task("goal") is None


def _seed(path, now):
    day = 86400
    goals = [("bump version to 0.9.4", ["pyproject.toml"], ["read", "edit"]),
             ("bump version to 0.9.5", ["pyproject.toml"], ["read", "edit"]),
             ("bump version to 0.9.6", ["pyproject.toml"], ["read", "edit"]),
             ("fix the parser crash on empty input", ["src/parser.py"], ["read", "edit", "test"]),
             ("run the tests for scaffold/agent", [], ["shell"]),
             ("run the tests for tests/unit", [], ["shell"])]
    for i, (g, files, acts) in enumerate(goals):
        rep.log_task(g, files=files, actions=acts, ts=now - (10 - i) * day, path=path,
                     outcome="ok", evidence_level="L1")
    # outside the window: seeds nothing counted
    rep.log_task("add a cli flag --json", files=["awos.py"], actions=["edit"],
                 ts=now - 90 * day, path=path)


def test_repeat_rates(tmp_path):
    log = tmp_path / "log.jsonl"
    now = 1_800_000_000.0
    _seed(log, now)
    entries = rep.read_log(log)
    assert len(entries) == 7
    r = rep.repeat_rates(entries, since=now - 28 * 86400)
    assert r["overall"]["n"] == 6
    assert r["overall"]["repeats"] == 3            # 2 bumps + 1 test run
    assert r["families"]["bump_version"]["repeats"] == 2
    assert r["families"]["bump_version"]["keys"] == 1


def test_window_expires_old_keys(tmp_path):
    log = tmp_path / "log.jsonl"
    rep.log_task("bump version to 1.0.0", ts=0, path=log)
    rep.log_task("bump version to 1.0.1", ts=40 * 86400, path=log)
    r = rep.repeat_rates(rep.read_log(log), window_days=28)
    assert r["overall"]["repeats"] == 0


def test_report_script(tmp_path):
    log = tmp_path / "log.jsonl"
    now = 1_800_000_000.0
    _seed(log, now)
    out = subprocess.run([sys.executable, str(ROOT / "scripts/repetition_report.py"),
                          "--log", str(log), "--now", str(now), "--json"],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    assert data["n"] == 6 and data["structure_rate"] == 0.5
    assert data["prerequisite_met"] is True
    assert data["families"]["bump_version"]["structure_rate"] == round(2 / 3, 4)
    txt = subprocess.run([sys.executable, str(ROOT / "scripts/repetition_report.py"),
                          "--log", str(tmp_path / "none.jsonl")],
                         capture_output=True, text=True, check=True).stdout
    assert "NOT MET" in txt

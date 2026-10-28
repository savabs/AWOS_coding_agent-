"""Admission harness skeleton (A1, A2, A3, A4, A6) on a temp golden repo."""

import types

import pytest

from scaffold.agent.compiled import admit
from scaffold.agent.compiled.examples import bump_version as tool


@pytest.fixture
def golden(tmp_path):
    return tool.make_demo_repo(tmp_path / "golden")


def _variant(**overrides):
    """A copy of the bump_version module with some functions replaced."""
    m = types.ModuleType("bump_variant")
    m.__dict__.update({k: v for k, v in vars(tool).items() if not k.startswith("__")})
    m.__dict__.update(overrides)
    return m


def _run(golden, tmp_path, mod):
    rec = admit.build_record(mod, golden)
    return rec, admit.Harness(rec, mod, golden, workdir=tmp_path / "clones").run()


def _check(report, prefix):
    return next(c for c in report.checks if c.name.startswith(prefix))


def test_bump_version_admitted(golden, tmp_path):
    rec, rep = _run(golden, tmp_path, tool)
    assert rep.admitted, rep.to_text()
    assert _check(rep, "A2").passed == 12
    assert _check(rep, "A4").passed == _check(rep, "A4").n >= 5
    d = admit.apply_admission(rec, rep)
    assert d["state"] == "admitted"
    assert (d["evidence"]["s"], d["evidence"]["f"], d["evidence"]["s_indep"]) == (12, 0, 4)
    assert d["evidence"]["lb95"] == 0.7942
    # golden untouched
    assert tool.current_version(golden) == tool.START_VERSION


def test_vacuous_probe_rejected(golden, tmp_path):
    mod = _variant(probe=lambda params, ctx: {"ok": True, "evidence": {}})
    rec, rep = _run(golden, tmp_path, mod)
    assert not rep.admitted
    assert not _check(rep, "A1").ok and not _check(rep, "A6").ok
    assert admit.apply_admission(rec, rep)["state"] == "rejected"


def test_collateral_write_rejected(golden, tmp_path):
    def run(params, ctx):
        tool.run(params, ctx)
        (ctx["root"] / "README.md").write_text("touched\n")
    rec, rep = _run(golden, tmp_path, _variant(run=run))
    assert not rep.admitted and not _check(rep, "A3").ok


def test_near_miss_leak_rejected(golden, tmp_path):
    mod = _variant(INTENT=dict(tool.INTENT, anti_triggers=[]))   # "dependency" now leaks
    rec, rep = _run(golden, tmp_path, mod)
    a4 = _check(rep, "A4")
    assert not rep.admitted and not a4.ok
    assert any("LEAK" in d and "dependency" in d for d in a4.detail)


def test_too_few_near_misses_rejected(golden, tmp_path):
    mod = _variant(near_misses=lambda seed: tool.near_misses(seed)[:3])
    rec, rep = _run(golden, tmp_path, mod)
    assert not _check(rep, "A4").ok


def test_failing_run_rejected(golden, tmp_path):
    def run(params, ctx):
        if params["new_version"].startswith("7."):
            raise RuntimeError("boom")
        tool.run(params, ctx)
    rec, rep = _run(golden, tmp_path, _variant(run=run))
    a2 = _check(rep, "A2")
    assert not rep.admitted and a2.passed == 11


def test_matcher():
    intent = admit.Intent(**tool.INTENT)
    assert admit.match_intent("Please bump the version to 1.2.3", intent)[0]
    assert admit.match_intent("release 1.2.3", intent)[0]
    assert not admit.match_intent("bump the requests dependency version", intent)[0]
    assert not admit.match_intent("fix the parser", intent)[0]


@pytest.mark.timeout(60)  # ~28 fresh clones; 10s default is too tight under load
def test_top_up_reaches_lb(golden, tmp_path):
    rec, rep = _run(golden, tmp_path, tool)
    d = admit.apply_admission(rec, rep)
    h = admit.Harness(rec, tool, golden, workdir=tmp_path / "topup")
    admit.top_up(d, h, 16)
    assert d["evidence"]["s"] == 28 and d["evidence"]["lb95"] >= 0.9
    assert d["state"] == "admitted"            # still blocked by s_live


@pytest.mark.timeout(60)
def test_top_up_records_raising_tool_as_fail(golden, tmp_path):
    def run(params, ctx):
        raise RuntimeError("boom")
    mod = _variant(run=run)
    rec = admit.build_record(mod, golden)
    d = rec.to_dict()
    d["state"] = "admitted"
    h = admit.Harness(rec, mod, golden, workdir=tmp_path / "topup")
    execs = admit.top_up(d, h, 2)
    assert [e["outcome"] for e in execs] == ["run_error", "run_error"]
    assert d["evidence"]["f"] == 2 and d["state"] == "retired"
    assert "boom" in d["evidence"]["last_fail"]["detail"]


def test_git_env_keeps_caller_path_and_home(monkeypatch):
    monkeypatch.setenv("PATH", "/opt/custom/bin")
    monkeypatch.setenv("HOME", "/home/someone")
    env = admit._git_env()
    assert env["PATH"].startswith("/opt/custom/bin:") and "/usr/bin" in env["PATH"]
    assert env["HOME"] == "/home/someone"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"


def test_cli_table(capsys):
    assert admit.main(["--table"]) == 0
    assert "28 (LB 0.902)" in capsys.readouterr().out

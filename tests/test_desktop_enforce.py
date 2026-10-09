"""Enforcement hook: the decision binds (allow runs, ask needs approval, deny
never runs) and every check leaves one audit line."""
import pytest

from scaffold.agent.desktop import enforce as E
from scaffold.agent.desktop.policy import (
    Action, ActionClass as C, AuditLog, Grant, Provenance as P, SinkPolicy,
)


@pytest.fixture
def sandbox(tmp_path):
    d = tmp_path / "sandbox"
    d.mkdir()
    return d


def make(sandbox, tmp_path, mode=E.Mode.ENFORCE, approver=None, grants=()):
    log = AuditLog(tmp_path / "audit.jsonl")
    return E.Enforcer(SinkPolicy(sandbox, grants), mode, approver, log), log


def test_allow_runs_and_logs(sandbox, tmp_path):
    enf, log = make(sandbox, tmp_path)
    ran = []
    enf.run_command("ls -la", ran.append)
    assert ran == ["ls -la"]
    [rec] = log.read()
    assert rec["kind"] == "enforce" and rec["outcome"] == "allowed" and rec["verdict"] == "allow"
    assert set(rec) >= {"ts", "mode", "subject", "rule", "reason", "action_class",
                        "target", "initiator", "approved"}


def test_deny_never_runs_and_never_asks(sandbox, tmp_path):
    asked = []
    enf, log = make(sandbox, tmp_path, approver=lambda d: asked.append(d) or True)
    ran = []
    with pytest.raises(E.PolicyBlocked) as ei:
        enf.run_command('bash -c "rm -rf ~"', ran.append, P.UNTRUSTED)
    assert ran == [] and asked == []
    assert ei.value.result.decision.rule == "R3-untrusted-trigger"
    assert log.read()[-1]["outcome"] == "blocked"


@pytest.mark.parametrize("approver, outcome", [
    (None, "blocked"),                         # no approver: fail closed
    (lambda d: False, "blocked"),
    (lambda d: True, "approved"),
    (lambda d: 1 / 0, "blocked"),              # approver crash never approves
])
def test_ask_needs_approval(sandbox, tmp_path, approver, outcome):
    enf, log = make(sandbox, tmp_path, approver=approver)
    res = enf.check_command("python3 -c 'print(1)'", P.AGENT)
    assert res.verdict.value == "ask"
    assert res.outcome == outcome and res.allowed == (outcome == "approved")
    assert log.read()[-1]["outcome"] == outcome


def test_audit_mode_is_shadow(sandbox, tmp_path):
    enf, log = make(sandbox, tmp_path, mode=E.Mode.AUDIT)
    ran = []
    enf.run_command("rm -rf x", ran.append, P.UNTRUSTED)
    assert ran == ["rm -rf x"]
    rec = log.read()[-1]
    assert rec["verdict"] == "deny" and rec["outcome"] == "shadow" and rec["mode"] == "audit"


def test_check_action_and_run_action(sandbox, tmp_path):
    enf, log = make(sandbox, tmp_path, grants=[Grant(C.SEND, "mailto:boss@*")])
    assert enf.run_action(Action(C.SEND, "mailto:boss@x.test", P.OWNER), lambda: "sent") == "sent"
    with pytest.raises(E.PolicyBlocked):
        enf.run_action(Action(C.SEND, "mailto:boss@x.test", P.UNTRUSTED), lambda: "sent")
    assert [r["outcome"] for r in log.read()] == ["allowed", "blocked"]


def test_from_env_is_off_by_default(monkeypatch, sandbox):
    monkeypatch.delenv(E.MODE_ENV, raising=False)
    assert E.enforcer_from_env(sandbox) is None
    monkeypatch.setenv(E.MODE_ENV, "bogus")
    assert E.enforcer_from_env(sandbox) is None


@pytest.mark.parametrize("raw, mode", [("enforce", E.Mode.ENFORCE), ("AUDIT", E.Mode.AUDIT)])
def test_from_env_modes(monkeypatch, sandbox, tmp_path, raw, mode):
    monkeypatch.setenv(E.MODE_ENV, raw)
    monkeypatch.setenv(E.AUDIT_ENV, str(tmp_path / "a.jsonl"))
    enf = E.enforcer_from_env(sandbox)
    assert enf.mode is mode
    enf.check_command("ls")
    assert len(AuditLog(tmp_path / "a.jsonl").read()) == 1


def test_demo_runs(capsys, tmp_path):
    assert E.main(["--demo", "--audit", str(tmp_path / "d.jsonl")]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") > 30 and "BLOCKED (R3-untrusted-trigger)" in out
    assert len(AuditLog(tmp_path / "d.jsonl").read()) == 9

"""The loop executor is the default when a sandbox exists (pre-registered H1d switch rule)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import doctor  # noqa: E402
from scaffold.agent import orchestrator as orch  # noqa: E402


def _outside_pytest(monkeypatch, sandbox: bool):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("AWOS_EXECUTOR", raising=False)
    orch._sandbox_possible.cache_clear()
    monkeypatch.setattr(orch, "_sandbox_possible", lambda: sandbox)


def test_default_is_loop_with_a_sandbox(monkeypatch):
    _outside_pytest(monkeypatch, sandbox=True)
    assert orch.executor_choice() == "loop"
    assert orch.executor_label() == "loop"


def test_default_falls_back_to_pipeline_without_a_sandbox(monkeypatch):
    _outside_pytest(monkeypatch, sandbox=False)
    assert orch.executor_choice() == "agent_loop"
    assert orch.executor_label() == "pipeline"


def test_explicit_choice_wins(monkeypatch):
    _outside_pytest(monkeypatch, sandbox=True)
    monkeypatch.setenv("AWOS_EXECUTOR", "pipeline")
    assert orch.executor_choice() == "agent_loop"
    monkeypatch.setenv("AWOS_EXECUTOR", "loop")
    assert orch.executor_choice() == "loop"


def test_unset_under_pytest_keeps_pipeline(monkeypatch):
    monkeypatch.delenv("AWOS_EXECUTOR", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    assert orch.executor_choice() == "agent_loop"


def test_sandbox_none_disables_loop_default(monkeypatch):
    orch._sandbox_possible.cache_clear()
    monkeypatch.setenv("AWOS_SANDBOX", "none")
    assert orch._sandbox_possible() is False
    orch._sandbox_possible.cache_clear()


def test_doctor_reports_default(monkeypatch):
    monkeypatch.setattr(orch, "_sandbox_possible", lambda: True)
    c = doctor.check_executor({})
    assert c.ok and "loop" in c.detail and "default" in c.detail
    monkeypatch.setattr(orch, "_sandbox_possible", lambda: False)
    c = doctor.check_executor({})
    assert c.ok and "pipeline" in c.detail and "no sandbox" in c.detail


def test_host_child_keeps_explicit_and_drops_others(monkeypatch):
    from scaffold.agent import job_host
    assert "AWOS_EXECUTOR" not in job_host.CHILD_ENV

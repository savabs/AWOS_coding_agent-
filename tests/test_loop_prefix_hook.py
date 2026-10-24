"""The loop calls the local prefix-cache hook once, in text-protocol mode only, and never fails on it."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import local_runtime  # noqa: E402
from scaffold.agent import loop_executor as le  # noqa: E402


def test_hook_passes_only_the_system_prefix(monkeypatch):
    seen = []
    monkeypatch.setattr(local_runtime, "prepare_first_step",
                        lambda msgs, tools=None, **kw: seen.append((msgs, tools)) or {"status": "restored"})
    res = le._prepare_local_prefix("SYS")
    assert res == {"status": "restored"}
    assert seen == [([{"role": "system", "content": "SYS"}], None)]


def test_hook_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("server down")
    monkeypatch.setattr(local_runtime, "prepare_first_step", boom)
    assert le._prepare_local_prefix("SYS") is None


def test_hook_skipped_when_not_local(monkeypatch):
    monkeypatch.delenv("AWOS_PROVIDER", raising=False)
    monkeypatch.delenv("AWOS_AGENT_MODEL", raising=False)
    res = le._prepare_local_prefix("SYS")
    assert res is None or res.get("status") == "skipped"


def test_telemetry_reports_prefix_status():
    run = le.LoopRun(grammar=True)
    assert "prefix_cache" not in run.telemetry()
    run.prefix_cache = {"status": "restored", "tokens": 1858}
    assert run.telemetry()["prefix_cache"] == "restored"

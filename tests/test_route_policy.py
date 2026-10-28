"""
Routing policy for the loop executor's AWOS_LOOP_ROUTE ladder
(scaffold/agent/route_policy.py + orchestrator._execute_task_via_loop):
typed rung gates (forced decision, local_only, cheap-only, premium budget),
the routing credit (final rung, not esc_decision), and the checkpoint key
across an auto-continue. Fakes only; no model call.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from scaffold.agent import escalation_engine as ee  # noqa: E402
from scaffold.agent import loop_checkpoint as lc  # noqa: E402
from scaffold.agent import route_policy as rp  # noqa: E402
from test_loop_executor import (EDIT, GREEN, FakeSandbox, Scripted, _orchestrator,  # noqa: E402
                                _ws, bash, submit)


# ── provider table + gates (unit) ────────────────────────────────────────────


def test_classify_provider_table():
    assert rp.classify("local") == rp.RungInfo("local", "local", False, rp.PRIVACY_LOCAL, False)
    assert not rp.classify("local/qwen3.5-9b").remote
    c = rp.classify("claude-haiku-5-5")
    assert c.remote and c.premium and c.provider == "anthropic" and c.privacy == rp.PRIVACY_CLOUD
    d = rp.classify("deepseek-v4-flash")
    assert d.remote and not d.premium
    assert rp.classify("something-new").remote           # unknown: remote (fail closed)


def test_filter_ladder_typed_reasons():
    ladder = ["local", "deepseek-v4-flash", "claude-haiku-5-5"]
    kept, skipped = rp.filter_ladder(ladder, forced=True)
    assert kept == [] and {s["reason"] for s in skipped} == {rp.FORCED_MODEL}
    kept, skipped = rp.filter_ladder(ladder, local_only=True)
    assert kept == ["local"] and [s["reason"] for s in skipped] == [rp.LOCAL_ONLY] * 2
    kept, skipped = rp.filter_ladder(ladder, cheap_only=True)
    assert kept == ladder[:2] and skipped[0]["reason"] == rp.CHEAP_ONLY
    kept, skipped = rp.filter_ladder(ladder, premium_left=0.0)
    assert kept == ladder[:2] and skipped[0]["reason"] == rp.PREMIUM_BUDGET
    assert rp.filter_ladder(ladder, premium_left=3.0) == (ladder, [])


def test_local_only_switches(monkeypatch):
    monkeypatch.delenv("AWOS_ROUTE_LOCAL_ONLY", raising=False)
    monkeypatch.delenv("AWOS_JOB_PRIVACY", raising=False)
    assert not rp.local_only_enabled()
    monkeypatch.setenv("AWOS_ROUTE_LOCAL_ONLY", "1")
    assert rp.local_only_enabled()
    monkeypatch.delenv("AWOS_ROUTE_LOCAL_ONLY")
    monkeypatch.setenv("AWOS_JOB_PRIVACY", "local_only")
    assert rp.local_only_enabled()


def test_credit_target_maps_to_the_escalation_ladder():
    spec = ee.LADDER[-1]
    hit = rp.credit_target(spec.model_id)
    assert hit["action"] == "credit_rung" and hit["spec"] is spec
    assert rp.credit_target(f"openrouter/deepseek/{spec.model_id}")["spec"] is spec
    miss = rp.credit_target("local/qwen3.5-9b")
    assert miss["action"] == "skip" and miss["reason"] == "rung_not_on_escalation_ladder"


# ── orchestrator wiring ──────────────────────────────────────────────────────


class Named(Scripted):
    def __init__(self, model, replies):
        super().__init__(replies)
        self.model = model


def _run(tmp_path, monkeypatch, *, route, rung_models, default_client=None, forced=False,
         premium_left=None, session_goal="Fix add() in calc.py: it must return a + b",
         ckpt_capture=None):
    monkeypatch.setenv("AWOS_LOOP_ROUTE", route)
    log = tmp_path / "route.jsonl"
    monkeypatch.setenv("AWOS_ROUTE_LOG", str(log))
    ws = _ws(tmp_path)
    o = _orchestrator()
    o.escalation._premium_budget_remaining.return_value = premium_left
    o._components = {}
    ctx = {"codebase_root": str(ws), "git": MagicMock(),
           "session": MagicMock(goal=session_goal, session_id="s1"), "codebase_context": {}}
    esc_spec = SimpleNamespace(model_id="claude-haiku-5-5", name="EscModel", cost_per_req=0.01,
                               level=ee.EscalationLevel.HAIKU)
    built = []

    def build_rung(spec):
        built.append(spec)
        return Named(rung_models.get(spec, spec), [bash(EDIT), submit()])

    runner = MagicMock()
    runner.return_value.run.side_effect = [GREEN, GREEN]
    default_client = default_client or Named("default-model", [bash(EDIT), submit()])
    patches = [
        patch("scaffold.agent.loop_route.build_route_client", side_effect=build_rung),
        patch("scaffold.agent.agent_loop.build_client_from_env", return_value=default_client),
        patch("scaffold.agent.sandbox.make_sandbox", side_effect=lambda r, backend=None: FakeSandbox(r)),
        patch("scaffold.agent.budget_ledger.get_ledger", return_value=MagicMock()),
        patch("scaffold.agent.orchestrator.TestRunner", runner),
    ]
    if ckpt_capture is not None:
        class Ckpt:
            def __init__(self, path, key):
                ckpt_capture.append(key)

            def load(self):
                return None

            def save(self, *a, **k):
                pass

            def finish(self, *a, **k):
                pass

        patches.append(patch("scaffold.agent.loop_checkpoint.LoopCheckpoint", Ckpt))
    for p in patches:
        p.start()
    try:
        out = o._execute_task_via_loop(
            {"task_id": 1, "action": "Fix add() in calc.py", "file": ""}, ctx, task_id=1,
            esc_decision=SimpleNamespace(spec=esc_spec, forced=forced), _span=MagicMock(),
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
    finally:
        for p in reversed(patches):
            p.stop()
    records = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return SimpleNamespace(out=out, orch=o, built=built, log=records)


def test_route_credit_goes_to_the_final_rung_on_the_ladder(tmp_path, monkeypatch):
    target = ee.LADDER[-1]
    r = _run(tmp_path, monkeypatch, route=target.model_id, rung_models={})
    assert r.out["success"] is True and r.built == [target.model_id]
    store = r.orch.reward_store.store.call_args.kwargs
    assert store["model_name"] == target.name and store["action_id"] == target.level.value
    lvl = r.orch.escalation.record_outcome.call_args.args[1]
    assert lvl == target.level
    assert r.orch._components["route_credit"]["action"] == "credit_rung"


def test_route_credit_skipped_when_final_rung_has_no_arm(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, route="local", rung_models={"local": "local/qwen-test"})
    assert r.out["success"] is True
    r.orch.reward_store.store.assert_not_called()
    call = r.orch.escalation.record_outcome.call_args
    assert call.kwargs["task"] is None                     # history only, no arm trained
    assert r.orch._components["route_credit"] == {
        "action": "skip", "model": "local/qwen-test", "reason": "rung_not_on_escalation_ladder"}


def test_no_ladder_credits_esc_decision_as_before(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, route="", rung_models={})
    assert r.orch.reward_store.store.call_args.kwargs["model_name"] == "EscModel"
    assert "route_credit" not in r.orch._components


def test_forced_decision_skips_the_ladder(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, route="local,claude-haiku-5-5", rung_models={}, forced=True)
    assert r.out["success"] is True and r.built == []
    skips = [x for x in r.log if "skip" in x]
    assert {x["reason"] for x in skips} == {rp.FORCED_MODEL} and len(skips) == 2


def test_local_only_drops_remote_rungs(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_ROUTE_LOCAL_ONLY", "1")
    r = _run(tmp_path, monkeypatch, route="local,claude-haiku-5-5",
             rung_models={"local": "local/qwen-test"})
    assert r.built == ["local"]
    assert [(x["skip"], x["reason"]) for x in r.log if "skip" in x] == [
        ("claude-haiku-5-5", rp.LOCAL_ONLY)]


def test_local_only_with_only_remote_rungs_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_ROUTE_LOCAL_ONLY", "1")
    monkeypatch.delenv("AWOS_PROVIDER", raising=False)
    default = Named("default-model", [bash("touch ran"), submit()])
    r = _run(tmp_path, monkeypatch, route="claude-haiku-5-5", rung_models={},
             default_client=default)
    assert r.out["success"] is False and "local_only" in r.out["verify_error"]
    assert default.seen == [] and r.built == []


def test_cheap_only_and_premium_budget_drop_premium_rungs(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_CHEAP_ONLY", "1")
    r = _run(tmp_path, monkeypatch, route="deepseek-v4-flash,claude-haiku-5-5", rung_models={})
    assert r.built == ["deepseek-v4-flash"]
    assert [x["reason"] for x in r.log if "skip" in x] == [rp.CHEAP_ONLY]
    monkeypatch.delenv("AWOS_CHEAP_ONLY")
    monkeypatch.delenv("AWOS_PREMIUM_BUDGET", raising=False)
    (tmp_path / "b").mkdir()
    r = _run(tmp_path / "b", monkeypatch, route="deepseek-v4-flash,claude-haiku-5-5",
             rung_models={}, premium_left=0.0)
    assert r.built == ["deepseek-v4-flash"]
    assert [x["reason"] for x in r.log if "skip" in x] == [rp.PREMIUM_BUDGET]


def test_checkpoint_key_ignores_the_auto_continue_note(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_LOOP_CHECKPOINT_PATH", str(tmp_path / "ckpt.jsonl"))
    monkeypatch.setenv("AWOS_LOOP_CHECKPOINT_GOAL", "Fix add()")
    monkeypatch.delenv("AWOS_LOOP_CHECKPOINT", raising=False)
    keys: list = []
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _run(tmp_path / "a", monkeypatch, route="", rung_models={}, session_goal="Fix add()",
         ckpt_capture=keys)
    _run(tmp_path / "b", monkeypatch, route="", rung_models={},
         session_goal="Fix add()\n\nNOTE: you stopped at step 40", ckpt_capture=keys)
    assert len(keys) == 2 and keys[0] == keys[1]
    assert keys[0] == lc.task_key("Fix add()", "default-model", False, extra="1")

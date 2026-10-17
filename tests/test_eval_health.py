"""
scripts/eval_health.py: each run-health check on synthetic job-series run dirs.
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "eval_health", Path(__file__).resolve().parent.parent / "scripts" / "eval_health.py")
eh = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(eh)

PIN = "deepseek/deepseek-v4-flash"
T0 = time.mktime(time.strptime("2026-09-30 17:00:00", "%Y-%m-%d %H:%M:%S"))


def _row(arm, job, **kw):
    row = {"arm": arm, "job": job, "solved": True, "turns": 5, "billed_usd": 0.01,
           "notebook_chars": 0, "invalid": False, "invalid_reason": None, "models": [PIN]}
    row.update(kw)
    return row


def _header(arm, job):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 + job * 600))
    return f"\n===== [{arm} j{job:02d}] {stamp} =====\n"


def _call(at_job, component="agent", **kw):
    line = {"ts": T0 + at_job * 600 + 30, "pid": 1, "component": component,
            "requested_model": PIN, "response_model": PIN, "finish_reason": "stop",
            "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01, "visible_chars": 10,
            "error": None}
    line.update(kw)
    return line


def _build(tmp_path, rows, calls=None, aider_logs=None, meta=None):
    """Run dir + results JSON. calls: {arm: [lines]} (None → no log for any arm)."""
    root = tmp_path / "run"
    arms = sorted({r["arm"] for r in rows})
    for arm in arms:
        d = root / arm
        (d / "state").mkdir(parents=True)
        text = ""
        for r in [r for r in rows if r["arm"] == arm]:
            text += _header(arm, r["job"])
            if aider_logs and r["job"] in aider_logs:
                text += aider_logs[r["job"]]
        (d / "run.log").write_text(text)
        if calls is not None and arm in calls:
            (d / "state" / ".awos").mkdir()
            (d / "state" / ".awos" / "llm_calls.jsonl").write_text(
                "".join(json.dumps(c) + "\n" for c in calls[arm]))
    data = {"arms": arms, "model_pin": {"model": PIN, "env": {"AWOS_AGENT_MODEL": PIN}},
            "results": rows, **(meta or {})}
    results = tmp_path / "results.json"
    results.write_text(json.dumps(data))
    return root, results


def _checks(health, severity=None):
    return [v["check"] for v in health["violations"]
            if severity is None or v["severity"] == severity]


def _healthy(tmp_path, **kw):
    rows = [_row("on", 1, notebook_chars=1000), _row("on", 2, notebook_chars=1200)]
    calls = {"on": [_call(1), _call(2), _call(2, "notebook")]}
    return _build(tmp_path, rows, calls, **kw)


def test_healthy_run_is_ok(tmp_path):
    health = eh.check_run(*_healthy(tmp_path))
    assert health["ok"] and health["violations"] == []
    assert health["stats"]["arms"]["on"]["calls"] == 3


def test_missing_call_log_is_warn_only(tmp_path):
    root, results = _build(tmp_path, [_row("on", 1)])
    health = eh.check_run(root, results)
    assert health["ok"] and _checks(health) == ["no_call_log"]


def test_job_without_calls_is_fatal(tmp_path):
    rows = [_row("on", 1), _row("on", 2), _row("on", 3, invalid=True, turns=0)]
    root, results = _build(tmp_path, rows, {"on": [_call(1)]})
    health = eh.check_run(root, results)
    bad = [v for v in health["violations"] if v["check"] == "no_calls_for_job"]
    assert [v["job"] for v in bad] == [2]  # the invalid job 3 is exempt


def test_job_tag_beats_timestamp(tmp_path):
    root, results = _build(tmp_path, [_row("on", 1), _row("on", 2)],
                           {"on": [_call(1), _call(1, job="2")]})
    assert "no_calls_for_job" not in _checks(eh.check_run(root, results))


@pytest.mark.parametrize("bad", [{"finish_reason": "length"}, {"visible_chars": 0}])
def test_bad_final_state_write_is_fatal(tmp_path, bad):
    root, results = _build(tmp_path, [_row("on", 1)],
                           {"on": [_call(1), _call(1, "notebook", final=True, **bad)]})
    health = eh.check_run(root, results)
    assert not health["ok"] and "state_write_bad_reply" in _checks(health, "fatal")


def test_retried_state_write_is_warn(tmp_path):
    root, results = _build(tmp_path, [_row("on", 1, billed_usd=0.03)], {"on": [
        _call(1), _call(1, "critique", finish_reason="length", visible_chars=0, final=False),
        _call(1, "critique", final=True)]})
    health = eh.check_run(root, results)
    assert health["ok"] and _checks(health) == ["state_write_retry"]


def test_agent_truncation_rate_warns_above_5pct(tmp_path):
    calls = [_call(1) for _ in range(9)] + [_call(1, finish_reason="length")]
    root, results = _build(tmp_path, [_row("on", 1, billed_usd=0.1)], {"on": calls})
    health = eh.check_run(root, results)
    assert health["ok"] and _checks(health) == ["agent_truncation_rate"]
    assert health["stats"]["arms"]["on"]["agent_truncation_rate"] == 0.1


def test_unpinned_model_in_call_log_is_fatal(tmp_path):
    root, results = _build(tmp_path, [_row("on", 1)], {"on": [
        _call(1), _call(1, "review", requested_model="anthropic/claude-sonnet-4.5",
                        response_model="anthropic/claude-sonnet-4.5")]})
    health = eh.check_run(root, results)
    assert not health["ok"] and "unpinned_model" in _checks(health, "fatal")


def test_unpinned_model_in_row_is_fatal(tmp_path):
    root, results = _build(tmp_path, [_row("on", 1, models=[PIN, "claude-sonnet-4-5"])])
    assert "unpinned_model" in _checks(eh.check_run(root, results), "fatal")


def test_dated_variant_and_explicit_allow(tmp_path):
    root, results = _build(tmp_path, [_row("on", 1)], {"on": [
        _call(1, response_model="deepseek/deepseek-v4-flash-20260901")]})
    assert eh.check_run(root, results)["ok"]
    health = eh.check_run(root, results, allowed_models={"other/model"})
    assert "unpinned_model" in _checks(health, "fatal")


def test_notebook_drop_over_40pct_is_fatal(tmp_path):
    rows = [_row("on", 1, notebook_chars=1598), _row("on", 2, notebook_chars=2732),
            _row("on", 3, notebook_chars=364), _row("on", 4, notebook_chars=1208),
            _row("on", 5, notebook_chars=1100)]
    root, results = _build(tmp_path, rows)
    bad = [v for v in eh.check_run(root, results)["violations"] if v["check"] == "notebook_drop"]
    assert [(v["job"], v["severity"]) for v in bad] == [(3, "fatal")]
    assert "2732 -> 364" in bad[0]["detail"]


def test_missing_billing_and_mismatch_warn(tmp_path):
    rows = [_row("on", 1, billed_usd=0.10), _row("on", 2, billed_usd=None)]
    root, results = _build(tmp_path, rows, {"on": [_call(1, cost_usd=0.02), _call(2)]})
    health = eh.check_run(root, results)
    assert health["ok"]
    assert sorted(_checks(health, "warn")) == ["billing_mismatch", "missing_billing"]
    detail = next(v["detail"] for v in health["violations"] if v["check"] == "billing_mismatch")
    assert "$0.1000" in detail and "$0.0300" in detail


AIDER_NO_FILES = ("+00:01 [harness_aider] aider 0.86.2 model=openrouter/deepseek/deepseek-v4-flash "
                  "add_files=none(0) cost_cap=$0.3\n+00:10 Please add ordertool/cli.py to the chat.\n")
AIDER_OK = ("+00:01 [harness_aider] aider 0.86.2 model=openrouter/deepseek/deepseek-v4-flash "
            "add_files=auto:src(5 edit) cost_cap=$0.3\n+00:20 Applied edit to a.py\n"
            '+00:21 [aider-final] {"edited": ["a.py"], "in_chat": ["a.py"]}\n')
AIDER_EMPTY_CHAT = ("+00:01 [harness_aider] aider 0.86.2 model=openrouter/deepseek/deepseek-v4-flash "
                    "add_files=auto(3)\n" '+00:21 [aider-final] {"edited": [], "in_chat": []}\n')


def test_aider_no_files_is_fatal_and_counted(tmp_path):
    rows = [_row("aider", 1), _row("aider", 2),
            _row("aider", 3, invalid=True, invalid_reason="aider_no_edit")]
    root, results = _build(tmp_path, rows,
                           aider_logs={1: AIDER_OK, 2: AIDER_NO_FILES, 3: AIDER_EMPTY_CHAT})
    health = eh.check_run(root, results)
    bad = [v for v in health["violations"] if v["check"] == "aider_no_files"]
    assert [(v["job"], v["severity"]) for v in bad] == [(2, "fatal"), (3, "fatal")]
    st = health["stats"]["arms"]["aider"]
    assert st["aider_no_edit"] == {"asked_to_add_files": 1, "no_files_in_chat": 1}
    assert st["aider_no_edit_invalid"] == 2
    assert "deepseek/deepseek-v4-flash" in health["stats"]["allowed_models"]


def test_invalid_share_warns_above_25pct(tmp_path):
    rows = [_row("off", 1), _row("off", 2, invalid=True, turns=0),
            _row("off", 3, invalid=True, turns=0), _row("off", 4)]
    root, results = _build(tmp_path, rows)
    health = eh.check_run(root, results)
    assert health["stats"]["arms"]["off"]["invalid_share"] == 0.5
    assert _checks(health, "warn").count("invalid_share") == 2  # per arm + across arms


def test_silent_zero_work_is_fatal(tmp_path):
    root, results = _build(tmp_path, [_row("off", 1, turns=0), _row("off", 2, turns=None)])
    health = eh.check_run(root, results)
    assert _checks(health, "fatal") == ["zero_work", "zero_work"]


def test_aider_answer_without_edit_is_not_zero_work(tmp_path):
    # backupd j09: Aider had its 15 files, answered without an edit, 0 turns —
    # a fair failure (the runner keeps it), not silent zero work.
    root, results = _build(tmp_path, [_row("aider", 9, turns=0, aider_no_edit="no_edits")])
    health = eh.check_run(root, results)
    assert "zero_work" not in _checks(health, "fatal")


def test_repeat_dirs(tmp_path):
    rows = [_row("on", 1, repeat=1), _row("on", 1, repeat=2)]
    root, results = _build(tmp_path, [])
    for rep in (1, 2):
        d = root / f"r{rep}" / "on"
        (d / "state" / ".awos").mkdir(parents=True)
        (d / "run.log").write_text(_header("on", 1))
        (d / "state" / ".awos" / "llm_calls.jsonl").write_text(json.dumps(_call(1)) + "\n")
    data = json.loads(results.read_text())
    data["results"] = rows
    results.write_text(json.dumps(data))
    health = eh.check_run(root, results)
    assert health["ok"] and set(health["stats"]["arms"]) == {"r1/on", "r2/on"}


def test_cli_writes_health_json(tmp_path, capsys):
    root, results = _healthy(tmp_path)
    assert eh.main([str(root), str(results)]) == 0
    assert "RUN HEALTH: OK" in capsys.readouterr().out
    assert json.loads((results.parent / "health.json").read_text())["ok"] is True


# ── provider pin + prompt cache ──────────────────────────────────────────────

def _provider_checks(health):
    """Violations other than billing (these fixtures' call costs are arbitrary)."""
    return [c for c in _checks(health) if c != "billing_mismatch"]


def _pinned(at_job, turn, provider="DeepInfra", cached=0, inp=1000, **kw):
    return _call(at_job, requested_provider="deepinfra", provider=provider, turn=turn,
                 input_tokens=inp, cached_tokens=cached, **kw)


def test_pinned_run_served_by_pinned_provider_is_ok(tmp_path):
    rows = [_row("on", 1), _row("on", 2)]
    calls = {"on": [_pinned(1, 1), _pinned(1, 2, cached=900), _pinned(2, 1),
                    _pinned(2, 2, cached=800), _pinned(2, 3, provider="deepinfra", cached=900)]}
    health = eh.check_run(*_build(tmp_path, rows, calls))
    assert health["ok"] and _provider_checks(health) == []
    st = health["stats"]["arms"]["on"]
    assert st["requested_provider"] == "deepinfra"
    assert st["agent_cache_share"] == round(2600 / 5000, 4)
    assert st["agent_later_turns"] == 3 and st["agent_later_turns_cached_share"] == 1.0


def test_provider_mismatch_is_fatal(tmp_path):
    rows = [_row("on", 1)]
    calls = {"on": [_pinned(1, 1), _pinned(1, 2, provider="Chutes", cached=900)]}
    health = eh.check_run(*_build(tmp_path, rows, calls))
    assert not health["ok"] and "provider_mismatch" in _checks(health, "fatal")


def test_fallback_call_is_a_warning_when_fallbacks_allowed(tmp_path):
    # Preferred provider overloaded (429) -> OpenRouter falls back: a cache miss
    # to report, not a broken pin.
    rows = [_row("on", 1)]
    calls = {"on": [_pinned(1, 1, fallbacks_allowed=True),
                    _pinned(1, 2, provider="Novita", cached=0, fallbacks_allowed=True),
                    _pinned(1, 3, cached=900, fallbacks_allowed=True)]}
    health = eh.check_run(*_build(tmp_path, rows, calls))
    assert "provider_mismatch" not in _checks(health, "fatal")
    assert "provider_fallback" in _checks(health, "warn")
    assert health["stats"]["arms"]["on"]["preferred_provider_share"] == round(2 / 3, 4)


def test_pinned_run_with_low_cache_share_warns(tmp_path):
    rows = [_row("on", 1)]
    calls = {"on": [_pinned(1, 1), _pinned(1, 2, cached=10), _pinned(1, 3)]}
    health = eh.check_run(*_build(tmp_path, rows, calls))
    assert health["ok"] and _provider_checks(health) == ["low_cache_share"]
    assert health["stats"]["arms"]["on"]["agent_later_turns_cached_share"] == 0.5


def test_unpinned_run_skips_provider_checks_but_reports_cache(tmp_path):
    rows = [_row("on", 1)]
    calls = {"on": [_call(1, provider="Chutes", turn=1, input_tokens=100, cached_tokens=0),
                    _call(1, provider="DeepInfra", turn=2, input_tokens=100, cached_tokens=0)]}
    health = eh.check_run(*_build(tmp_path, rows, calls))
    assert health["ok"] and _provider_checks(health) == []
    st = health["stats"]["arms"]["on"]
    assert st["requested_provider"] is None and st["agent_cache_share"] == 0.0
    assert "cache=" in eh.format_report(health)

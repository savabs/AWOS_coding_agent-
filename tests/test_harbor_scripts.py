"""
scripts/harbor_tasks.py, scripts/harbor_run.py, scripts/harbor_collect.py (the
Harbor head-to-head, ci/HARBOR.md). Fake Harbor job dirs only: no Docker, no
harbor process, no model call. The last test checks the agent kwargs against
Harbor's own option models and is skipped when `harbor` is not installed.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

import harbor_collect as hc  # noqa: E402
import harbor_run as hr  # noqa: E402
import harbor_tasks as ht  # noqa: E402
from harbor_agents import pricing  # noqa: E402


# ── task subsets ─────────────────────────────────────────────────────────────


def _meta():
    return ht.read_metadata()


def test_committed_metadata_is_tb21():
    tasks = _meta()
    assert len(tasks) == 89 and all(t["name"].startswith("terminal-bench/") for t in tasks)
    assert {t["difficulty"] for t in tasks} == {"easy", "medium", "hard"}


def test_sample_file_matches_harbor_sample():
    names = ht.read_task_file(REPO / "ci" / "harbor_tasks_sample.txt")
    assert names == [f"terminal-bench/{t}" for t in ht.SAMPLE_TASKS]
    known = {t["name"] for t in _meta()}
    assert set(names) <= known


def test_stratified_is_deterministic_and_proportional():
    tasks = _meta()
    a, b = ht.stratified(tasks, 7, 30), ht.stratified(tasks, 7, 30)
    assert a == b and len(set(a)) == 30
    assert ht.stratified(tasks, 8, 30) != a
    diff = {t["name"]: t["difficulty"] for t in tasks}
    counts = {d: sum(diff[n] == d for n in a) for d in ("easy", "medium", "hard")}
    # 4/55/30 of 89 -> 1.35/18.5/10.1 of 30: largest remainder gives 1/19/10 or 1/18/11
    assert counts["easy"] == 1 and counts["hard"] in (10, 11) and sum(counts.values()) == 30
    cats = {t["name"]: t["category"] for t in tasks}
    assert len({cats[n] for n in a}) >= 8  # spread over categories
    assert ht.stratified(tasks, 3, 89) == sorted(t["name"] for t in tasks)
    with pytest.raises(ValueError):
        ht.stratified(tasks, 3, 0)


def test_committed_seed_file_reproduces():
    f = REPO / "ci" / "harbor_tasks_1.txt"
    assert ht.read_task_file(f) == ht.stratified(_meta(), 1, 30)


# ── config and commands ─────────────────────────────────────────────────────


def _cfg(tmp_path, **over):
    base = {"DATASET": "terminal-bench/terminal-bench-2-1@6", "TASKS": "sample",
            "AGENTS": "awos,claude-code,mini-swe-agent", "N_SHARDS": "3", "CAP_AGENT": "3",
            "TASK_CAP": "1.0", "PIN_CLAUDE_CODE": "2.1.292", "PIN_MINI_SWE_AGENT": "2.4.6"}
    base.update(over)
    p = tmp_path / "h.env"
    p.write_text("\n".join(f"{k}={v}" for k, v in base.items()) + "\n")
    return hr.read_config(p)


def test_committed_config_parses():
    cfg = hr.read_config()
    assert cfg["MODEL"] == "anthropic/claude-haiku-5-5" and "@" in cfg["DATASET"]
    assert set(cfg["agents"]) >= {"awos", "claude-code", "mini-swe-agent"}


def test_config_validation(tmp_path):
    with pytest.raises(ValueError, match="pin"):
        _cfg(tmp_path, PIN_CLAUDE_CODE="")
    with pytest.raises(ValueError, match="unknown"):
        _cfg(tmp_path, AGENTS="awos,aider")
    with pytest.raises(ValueError, match="version"):
        _cfg(tmp_path, DATASET="terminal-bench/terminal-bench-2-1")
    with pytest.raises(ValueError, match="CAP_AGENT"):
        _cfg(tmp_path, CAP_AGENT="0")
    with pytest.raises(ValueError, match="regenerate"):
        _cfg(tmp_path, TASKS="seed:1:31")
    (tmp_path / "x.env").write_text("BOGUS=1\n")
    with pytest.raises(ValueError, match="unknown key"):
        hr.read_config(tmp_path / "x.env")


def test_shards_partition_the_task_list(tmp_path):
    cfg = _cfg(tmp_path)
    parts = [hr.shard_tasks(cfg["tasks"], i, 3) for i in range(3)]
    assert sorted(sum(parts, [])) == sorted(cfg["tasks"])
    assert not set(parts[0]) & set(parts[1])


def test_fairness_flags(tmp_path):
    cfg = _cfg(tmp_path)
    cc = hr.agent_args("claude-code", cfg)
    assert "disable_web_search=true" in cc and "version=2.1.292" in cc
    for slot in hr.CLAUDE_CODE_MODEL_SLOTS:
        assert f"{slot}=claude-haiku-5-5" in cc
    assert "max_budget_usd=1.0" in cc
    aw = hr.agent_args("awos", cfg)
    assert aw[:2] == ["-a", hr.AWOS_AGENT] and "cost_cap=1.0" in aw and "max_steps=1000" in aw
    ms = hr.agent_args("mini-swe-agent", cfg)
    assert "version=2.4.6" in ms and "cost_limit=1.0" in ms
    cmd = hr.harbor_cmd("awos", cfg, ["terminal-bench/regex-log"], "jobs", "j")
    assert cmd[:4] == ["harbor", "run", "-d", "terminal-bench/terminal-bench-2-1@6"]
    for flag, value in (("-m", "anthropic/claude-haiku-5-5"), ("-k", "1"), ("-n", "1"),
                        ("-i", "terminal-bench/regex-log")):
        assert cmd[cmd.index(flag) + 1] == value
    assert "--timeout-multiplier" not in cmd  # dataset timeouts for everyone


def test_ci_config_output(tmp_path, capsys):
    p = tmp_path / "h.env"
    p.write_text("TASKS=sample\nAGENTS=awos,claude-code\nN_SHARDS=2\nCAP_AGENT=2\n"
                 "PIN_CLAUDE_CODE=2.1.292\n")
    assert hr.main(["--config", str(p), "ci-config"]) == 0
    out = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    inc = json.loads(out["matrix"])["include"]
    assert len(inc) == 4 and {"agent": "awos", "shard": 1} in inc
    assert out["cap_shard"] == "1.0000"


def test_shard_dry_run_respects_cap_and_time(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, N_SHARDS="1")
    m = hr.run_shard("awos", cfg, 0, tmp_path / "jobs", dry_run=True)
    assert m["ran"] == cfg["tasks"] and not m["skipped_cap"]
    monkeypatch.setattr(hc, "spent_usd", lambda d: 99.0)
    m = hr.run_shard("awos", cfg, 0, tmp_path / "jobs2", dry_run=True)
    assert m["ran"] == [] and m["skipped_cap"] == cfg["tasks"]
    monkeypatch.setattr(hc, "spent_usd", lambda d: 0.0)
    m = hr.run_shard("awos", cfg, 0, tmp_path / "jobs3", dry_run=True, deadline_s=60)
    assert m["ran"] == [] and m["skipped_time"] == cfg["tasks"]
    assert json.loads((tmp_path / "jobs3" / "shard.json").read_text())["agent"] == "awos"


# ── collect: fake Harbor job dirs ───────────────────────────────────────────


def _at(sec):
    return (datetime(2026, 10, 15, tzinfo=timezone.utc) + timedelta(seconds=sec)).isoformat()


def _trial(root, agent, task, *, reward=1.0, exc=None, n=0, agent_result=None, files=None,
           sec=100):
    d = root / f"{agent}__{task}" / f"{task}__{agent}{n}"
    (d / "agent").mkdir(parents=True)
    res = {
        "id": f"{agent}-{task}-{n}", "task_name": f"terminal-bench/{task}",
        "trial_name": d.name, "agent_info": {"name": agent, "version": "v1",
                                             "model_info": {"name": "claude-haiku-5-5",
                                                            "provider": "anthropic"}},
        "agent_result": agent_result or {}, "verifier_result":
            None if reward is None else {"rewards": {"reward": reward}},
        "exception_info": None if exc is None else {"exception_type": exc,
                                                    "exception_message": "boom"},
        "started_at": "2026-10-15T00:00:00Z", "finished_at": _at(sec + 60),
        "agent_execution": {"started_at": "2026-10-15T00:00:00+00:00", "finished_at": _at(sec)},
    }
    (d / "result.json").write_text(json.dumps(res))
    for name, content in (files or {}).items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(content)
    return d


def _awos_calls(*rows):
    return "\n".join(json.dumps({"input_tokens": p, "output_tokens": o, "cached_tokens": r,
                                 "cache_write_tokens": w, "finish_reason": f})
                     for p, o, r, w, f in rows) + "\n"


def test_summary_per_request_usage_and_classes(tmp_path):
    a = _trial(tmp_path, "awos", "t1", agent_result={"n_input_tokens": 130_000},
               files={"agent/awos_calls.jsonl": _awos_calls((10_000, 100, 0, 9_000, "tool_use"),
                                                            (120_000, 50, 110_000, 0, "end_turn"))})
    s = hc.summarize_trial(a)
    assert s["agent"] == "awos" and s["task"] == "t1" and s["passed"] and s["outcome"] == "ok"
    assert s["usage_source"] == "per_request" and s["n_long_prompt_requests"] == 1
    want = (pricing.request_cost("claude-haiku-5-5", 10_000, 100, 0, 9_000)
            + pricing.request_cost("claude-haiku-5-5", 120_000, 50, 110_000))
    assert s["cost_usd_uniform"] == pytest.approx(want)
    assert s["agent_sec"] == 100

    traj = {"steps": [{"metrics": {"prompt_tokens": 5000, "completion_tokens": 10,
                                   "cached_tokens": 4000,
                                   "extra": {"cache_creation": {"ephemeral_5m_input_tokens": 500,
                                                                "ephemeral_1h_input_tokens": 400}}}},
                      {"message": "no metrics"}]}
    c = _trial(tmp_path, "claude-code", "t1", agent_result={"n_input_tokens": 5000},
               files={"agent/trajectory.json": json.dumps(traj)})
    s = hc.summarize_trial(c)
    assert s["requests"] == [[5000, 10, 4000, 500, 400]]
    # subagent calls missing from the main trajectory: fall back to the totals
    c2 = _trial(tmp_path, "claude-code", "t2", n=1,
                agent_result={"n_input_tokens": 50_000, "n_cache_tokens": 1000,
                              "n_output_tokens": 70},
                files={"agent/trajectory.json": json.dumps(traj)})
    s = hc.summarize_trial(c2)
    assert s["usage_source"] == "aggregate" and s["requests"] == [[50_000, 70, 1000, 0, 0]]
    assert s["cost_usd_uniform"] == pytest.approx((49_000 * 0.10 + 1000 * 0.01 + 70 * 0.5) / 1e6)

    infra = hc.summarize_trial(_trial(tmp_path, "mini-swe-agent", "t3", reward=None,
                                      exc="EnvironmentStartTimeoutError"))
    assert infra["outcome"] == "infra" and not infra["passed"] and infra["cost_usd_uniform"] == 0
    to = hc.summarize_trial(_trial(tmp_path, "mini-swe-agent", "t4", reward=0.0,
                                   exc="AgentTimeoutError"))
    assert to["outcome"] == "agent_timeout"
    ref = hc.summarize_trial(_trial(tmp_path, "claude-code", "t5", reward=0.0, files={
        "agent/claude-code.txt": '{"type":"assistant","stop_reason":"refusal"}'}))
    assert ref["refusal"] and ref["outcome"] == "ok" and not ref["passed"]
    ref2 = hc.summarize_trial(_trial(tmp_path, "awos", "t6", reward=0.0, files={
        "agent/awos_calls.jsonl": _awos_calls((100, 1, 0, 0, "refusal"))}))
    assert ref2["refusal"]


def test_stats_known_values():
    lo, hi = hc.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    assert hc.mcnemar_exact(0, 0) == 1.0
    assert hc.mcnemar_exact(6, 0) == pytest.approx(2 / 64)
    assert hc.mcnemar_exact(5, 2) == pytest.approx(2 * (1 + 7 + 21) / 128)
    bs = hc.paired_bootstrap([1.0] * 20)
    assert bs["mean"] == 1.0 and bs["ci"] == (1.0, 1.0) and bs["p_two_sided"] == 0.0
    assert math.isnan(hc.wilson(0, 0)[0])


def _run_set(root, k=1):
    # awos passes t0..t5, other passes t0..t2 and t6; 8 tasks
    for i in range(8):
        for n in range(k):
            _trial(root, "awos", f"t{i}", reward=1.0 if i < 6 else 0.0, n=n,
                   files={"agent/awos_calls.jsonl": _awos_calls((1000, 10, 0, 0, "end_turn"))})
            _trial(root, "claude-code", f"t{i}", reward=1.0 if i < 3 or i == 6 else 0.0, n=n,
                   agent_result={"n_input_tokens": 2000, "n_output_tokens": 20})


def test_stage_then_report_k1(tmp_path, capsys):
    jobs, dest = tmp_path / "jobs", tmp_path / "art"
    _run_set(jobs)
    (jobs / "shard.json").write_text("{}")
    big = _trial(jobs, "mini-swe-agent", "t0", files={"agent/mini-swe-agent.txt": "x" * 300_000})
    assert hc.stage(jobs, dest) == 17
    staged_log = dest / "trials" / big.relative_to(jobs) / "agent" / "mini-swe-agent.txt"
    assert staged_log.stat().st_size < 70_000 and (dest / "shard.json").is_file()
    assert not list(dest.rglob("trajectory.json"))
    trials = hc.load_summaries([dest])
    assert len(trials) == 17
    table = {r["agent"]: r for r in hc.agent_table(trials)}
    assert table["awos"]["passed"] == 6 and table["claude-code"]["passed"] == 4
    pairs = {p["other"]: p for p in hc.paired(trials)}
    p = pairs["claude-code"]
    assert p["test"].startswith("exact McNemar") and p["base_only"] == 3 and p["other_only"] == 1
    assert p["p_value"] == pytest.approx(hc.mcnemar_exact(3, 1))
    assert p["cost_diff_per_task"] < 0
    assert hc.main(["report", str(dest), "--json", str(tmp_path / "r.json")]) == 0
    out = capsys.readouterr().out
    assert "| awos |" in out and "exact McNemar" in out
    assert json.loads((tmp_path / "r.json").read_text())["agents"]


def test_report_k_gt_1_uses_bootstrap(tmp_path):
    _run_set(tmp_path, k=2)
    trials = hc.load_summaries([tmp_path])  # raw job dirs work too
    p = hc.paired(trials)[0]
    assert p["test"].startswith("paired bootstrap") and p["diff"] == pytest.approx(0.25)
    lo, hi = p["diff_ci95"]
    assert lo <= 0.25 <= hi


def test_spent_usd_sums_uniform_costs(tmp_path):
    _run_set(tmp_path)
    one = pricing.request_cost("claude-haiku-5-5", 1000, 10)
    two = pricing.request_cost("claude-haiku-5-5", 2000, 20)
    assert hc.spent_usd(tmp_path) == pytest.approx(8 * one + 8 * two)
    assert hc.spent_usd(tmp_path / "missing") == 0.0


# ── against Harbor's own option models (needs `pip install harbor==0.24.0`) ──


def test_agent_kwargs_validate_against_harbor(tmp_path):
    pytest.importorskip("harbor")
    from harbor.agents.factory import AgentFactory, _import_agent_class
    from harbor.cli.utils import parse_kwargs
    from harbor.models.agent.name import AgentName

    cfg = _cfg(tmp_path)
    for agent in ("awos", "claude-code", "mini-swe-agent"):
        args = hr.agent_args(agent, cfg)
        name = args[1]
        kw = parse_kwargs([args[i + 1] for i, x in enumerate(args) if x == "--ak"])
        cls = (_import_agent_class(name) if ":" in name
               else AgentFactory.get_agent_class(AgentName(name)))
        opts = cls.parse_options(kw, {})
        assert opts is not None
        if agent == "claude-code":
            assert "WebSearch" in opts.disallowed_tools and "WebFetch" in opts.disallowed_tools
            assert opts.version == "2.1.292" and opts.max_budget_usd == "1.0"
        if agent == "mini-swe-agent":
            assert opts.version == "2.4.6"

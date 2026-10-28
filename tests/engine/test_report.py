"""EngineBench report maths, scoring, fingerprinting and a full replay against a fake llama-server."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import bench
import capture
from conftest import FakeLlama


# ----------------------------------------------------------------- scoring --

def test_score_agent_exact_action_and_reference():
    s = bench.score_call("agent", 'fill(2, "Ann")', '# ok\nfill(2, "Ann")', 'fill(2, "Ann")')
    assert s["correct"] is True and s["ref_agree"] is True and s["outcome"] == "action"
    s = bench.score_call("agent", "click(1)", "click(2)", "click(2)")
    assert s["correct"] is False and s["ref_agree"] is True
    s = bench.score_call("agent", None, "blah", None)
    assert s["correct"] is None and s["outcome"] == "invalid"


@pytest.mark.parametrize("expected,reply,outcome,correct", [
    ("PICK 2", "PICK 2: same row", "right", True),
    ("PICK 2", "PICK 1: x", "wrong", False),                # a repair onto the wrong control
    ("NOT_FOUND", "PICK 1: x", "wrong", False),             # healing a deleted control is the costly error
    ("PICK 2", "NOT_FOUND: gone", "abstain", False),        # safe miss
    ("NOT_FOUND", "NOT_FOUND: gone", "abstain_ok", True),
    ("PICK 1", "garbage", "abstain", False),                # unparseable = abstain (product semantics)
])
def test_score_repair(expected, reply, outcome, correct):
    s = bench.score_call("repair", expected, reply)
    assert (s["outcome"], s["correct"]) == (outcome, correct)


def test_score_verify_false_verified():
    assert bench.score_call("verify", "NOT_DONE", "VERIFIED: page says saved")["outcome"] == "false_verified"
    assert bench.score_call("verify", "UNSAFE", "UNSAFE: trap")["correct"] is True
    assert bench.score_call("verify", "VERIFIED", "NOT_DONE: x")["outcome"] == "wrong"


# ----------------------------------------------------------------- maths --

def rec(kind, *, wall=1.0, pn=100, pms=500.0, gn=10, gms=200.0, cache=0, lcp=None, correct=None, outcome=None,
        expected=None, pred=None, ep="e1", ref=None, err=""):
    return {"kind": kind, "episode": ep, "wall_s": wall, "prompt_n": pn, "prompt_ms": pms, "predicted_n": gn,
            "predicted_ms": gms, "cache_n": cache, "lcp_best": lcp, "lcp_prev": lcp, "correct": correct,
            "outcome": outcome, "expected": expected, "pred": pred, "ref_agree": ref, "error": err}


def test_speed_cache_compute_maths():
    rs = [rec("agent", wall=1.0, pn=100, pms=500, gn=10, gms=200, cache=0, lcp=0),
          rec("agent", wall=2.0, pn=300, pms=1500, gn=10, gms=300, cache=100, lcp=150),
          rec("agent", wall=9.0, err="boom")]                         # errors are excluded from speed
    sp = bench.speed(rs)
    assert sp["n"] == 2 and sp["wall_s_mean"] == 1.5
    assert sp["prefill_s_mean"] == 1.0 and sp["decode_s_mean"] == 0.25 and sp["overhead_s_mean"] == 0.25
    assert sp["prefill_share"] == round(2.0 / 3.0, 3)
    assert sp["prefill_tok_s"] == 200.0 and sp["decode_tok_s"] == 40.0
    c = bench.cache(rs)
    assert c["calls_hit"] == 1 and c["hit_rate"] == 0.5
    assert c["cached_share"] == round(100 / 500, 4)                   # 100 cached of 400 evaluated + 100 cached
    assert c["reusable_share"] == round(150 / 500, 4) and c["capture_of_reusable"] == round(100 / 150, 4)
    cp = bench.compute(rs, {"cpu_s": 4.0, "gpu_busy_s": 1.0})
    assert cp["prompt_tokens"] == 500 and cp["prompt_tokens_evaluated"] == 400 and cp["cached_tokens"] == 100
    assert cp["generated_tokens"] == 20 and cp["cpu_s_per_call"] == 2.0 and cp["gpu_busy_s_per_call"] == 0.5
    assert cp["energy_j"] is None and "sudo" in cp["energy_note"]


def test_quality_maths():
    rs = [rec("agent", correct=True, ref=True, ep="a", outcome="action"),
          rec("agent", correct=False, ref=True, ep="a", outcome="action"),
          rec("agent", correct=True, ref=False, ep="b", outcome="action"),
          rec("agent", correct=None, ref=None, ep="c", outcome="invalid"),
          rec("repair", correct=True, outcome="right", expected="PICK 1", pred="PICK 1"),
          rec("repair", correct=False, outcome="wrong", expected="NOT_FOUND", pred="PICK 2"),
          rec("repair", correct=True, outcome="abstain_ok", expected="NOT_FOUND", pred="NOT_FOUND"),
          rec("repair", correct=False, outcome="abstain", expected="PICK 3", pred="NOT_FOUND"),
          rec("verify", correct=True, expected="VERIFIED", pred="VERIFIED"),
          rec("verify", correct=False, expected="NOT_DONE", pred="VERIFIED"),
          rec("verify", correct=True, expected="UNSAFE", pred="UNSAFE")]
    q = bench.quality(rs)
    assert q["agent"]["step_match"] == round(2 / 3, 4) and q["agent"]["episode_all_match"] == 0.5
    assert q["agent"]["ref_agree"] == round(2 / 3, 4) and q["agent"]["invalid"] == 0.25
    assert q["repair"]["accuracy"] == 0.5 and q["repair"]["wrong_repair_rate"] == 0.25
    assert q["repair"]["refind_accuracy"] == 0.5 and q["repair"]["abstain_correct"] == 0.5
    assert q["verify"]["accuracy"] == round(2 / 3, 4) and q["verify"]["false_verified_rate"] == 0.5
    assert q["verify"]["verified_recall"] == 1.0


def test_quantiles_and_small_helpers():
    assert bench._q([1, 2, 3, 4], 0.5) == 2.5 and bench._q([], 0.5) is None
    assert bench.lcp([1, 2, 3], [1, 2, 4]) == 2 and bench.lcp([], [1]) == 0
    assert bench.parse_cputime("1:02.50") == 62.5 and bench.parse_cputime("1:00:00.00") == 3600
    assert bench.parse_cputime("2-00:00:01") == 2 * 86400 + 1
    assert bench.parse_footprint("llama-server [1]: 64-bit    Footprint: 2336 MB (16384 bytes per page)") == 2336
    assert bench.parse_footprint("Footprint: 1.5 GB") == 1536 and bench.parse_footprint("") is None
    assert bench.integrate([(0, 50), (1, 50), (2, 100)], baseline=0) == pytest.approx(0.5 + 0.75)
    assert bench.integrate([(0, 10), (1, 10)], baseline=20) == 0
    m = bench.parse_prometheus("# HELP x\nllamacpp:prompt_tokens_total 12\nbad line here\n")
    assert m == {"llamacpp:prompt_tokens_total": 12.0}


def test_sampler_with_fake_sources():
    ps = iter([{"rss_mb": 10, "cpu_s": 1.0}] + [{"rss_mb": 50, "cpu_s": 2.0}] * 50 + [{"rss_mb": 20, "cpu_s": 5.0}] * 50)
    s = bench.Sampler(123, every=0.01, foot_every=0.0, ps=lambda pid: next(ps), foot=lambda pid: 300.0,
                      gpu=lambda: 40.0)
    s.gpu_baseline = 10.0
    s.start()
    import time
    time.sleep(0.1)
    out = s.stop()
    assert out["peak_rss_mb"] == 50 and out["peak_footprint_mb"] == 300.0
    assert out["cpu_s"] is not None and out["cpu_s"] > 0 and out["gpu_util_mean"] == 40.0
    assert out["gpu_busy_s"] == pytest.approx(0.3 * (out["wall_s"]), abs=0.05)


# ------------------------------------------------------------ fingerprint --

def test_quant_and_shards():
    assert bench.quant_of("qwen3.5-4b-r2a-final-Q4_K_M-00001-of-00002.gguf") == "Q4_K_M"
    assert bench.quant_of("x-IQ4_XS.gguf") == "IQ4_XS" and bench.quant_of("x-Q8_0.gguf") == "Q8_0"
    assert bench.quant_of("model.gguf") == "?"
    sh = bench.shard_paths("/m/a-Q4_K_M-00001-of-00003.gguf")
    assert [p.name for p in sh] == [f"a-Q4_K_M-0000{i}-of-00003.gguf" for i in (1, 2, 3)]
    assert bench.shard_paths("/m/b.gguf") == [Path("/m/b.gguf")]


def test_server_flags_drop_paths_and_ports():
    cmd = ("/opt/homebrew/bin/llama-server -m /x/y.gguf --alias local --host 127.0.0.1 --port 8091 "
           "-c 4096 -np 1 -cms 0 --jinja")
    assert bench.server_flags(cmd) == ["-c", "4096", "-np", "1", "-cms", "0", "--jinja"]
    assert bench.model_from_cmd(cmd) == "/x/y.gguf"


def test_fingerprint_line_is_stable_and_config_sensitive(tmp_path):
    m = tmp_path / "m-Q4_K_M.gguf"
    m.write_bytes(b"gguf" * 10)
    sha = lambda p: "ab" * 32  # noqa: E731
    props = {"build_info": "b11146-7fe450e19", "default_generation_settings": {"n_ctx": 4096}}
    cmd = f"llama-server -m {m} --port 9 -c 4096 -np 1"
    f1 = bench.fingerprint(props, cmd, sha=sha)
    assert f1["line"] == f"llama.cpp b11146-7fe450e19 | m-Q4_K_M.gguf sha256:{'ab' * 6} | Q4_K_M | -c 4096 -np 1"
    assert f1["model_bytes"] == 40 and f1["n_ctx"] == 4096
    assert bench.fingerprint(props, cmd, sha=sha)["id"] == f1["id"]
    assert bench.fingerprint(props, cmd + " -cms 0", sha=sha)["id"] != f1["id"]
    f2 = bench.fingerprint(props, cmd.replace("--port 9", "--port 10"), sha=sha)
    assert f2["id"] == f1["id"]                                       # the port is not config


def test_file_sha256_is_cached(tmp_path):
    f = tmp_path / "w.gguf"
    f.write_bytes(b"abc")
    cache = tmp_path / "c.json"
    h = bench.file_sha256(f, cache)
    assert h == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert json.loads(cache.read_text())
    assert bench.file_sha256(f, cache) == h
    assert bench.file_sha256(tmp_path / "missing", cache) is None


def test_loopback_only():
    with pytest.raises(ValueError):
        bench.check_loopback("http://api.example.com/v1")
    bench.check_loopback("http://127.0.0.1:8091")


# ------------------------------------------------------- replay end-to-end --

def test_replay_against_fake_server(tmp_path, exam_dir, fake_server):
    eps = capture.agent_episodes(exam_dir, "factory-ood", None)
    eps.append({"id": "repair/x", "kind": "repair", "source": "t", "calls": [
        {"id": "repair/x/0", "step": 1, "messages": [{"role": "system", "content": "R"},
                                                     {"role": "user", "content": "The step used: case-one"}],
         "grammar": "g", "max_tokens": 64, "expected": "PICK 1", "reference": None, "meta": {}}]})
    fake = FakeLlama(answers={"Step 2 of": 'fill(2, "Ann")', "Step 3 of": 'done("ok")', "case-one": "PICK 2: x"},
                     cache_loss=2)
    base = fake_server(fake)
    wl = {"version": capture.VERSION, "digest": "x", "calls": 7, "episodes": 4, "budget": None}
    rep = bench.run(base, eps, label="fake", workload=wl, pid=None, sample=False, out=tmp_path / "o")
    calls = rep["calls"]
    assert len(calls) == 7 and not any(c["error"] for c in calls)
    assert [c["id"] for c in calls] == [c["id"] for e in eps for c in e["calls"]]           # step order kept
    # what the request carried: the workload's grammar, max_tokens, temperature 0, thinking off
    body = fake.calls[1]
    assert body["grammar"] == "root ::= \"x\"" and body["max_tokens"] == 200 and body["temperature"] == 0
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    # the fake cache restores LCP - 2 tokens; the reusable column sees the full LCP
    second = calls[1]
    assert second["cache_n"] == second["lcp_prev"] - 2 > 0
    assert second["prompt_n"] + second["cache_n"] == second["prompt_tokens"]
    s = rep["summary"]
    assert s["quality"]["agent"]["n_expected"] == 3 and s["quality"]["agent"]["step_match"] == 1.0
    assert s["quality"]["repair"]["wrong_repair_rate"] == 1.0
    tot_eval = sum(c["prompt_n"] for c in calls)
    assert s["compute"]["prompt_tokens_evaluated"] == tot_eval
    assert rep["server_metrics_delta"]["llamacpp:prompt_tokens_total"] == tot_eval
    assert s["speed"]["all"]["prefill_s_mean"] == pytest.approx(tot_eval / 1000 / 7, abs=1e-3)
    assert rep["fingerprint"]["build"] == "b99999-abcdef0" and rep["fingerprint"]["quant"] == "Q4_K_M"
    md = (tmp_path / "o" / "report.md").read_text()
    for word in ("SPEED", "QUALITY", "MEMORY", "COMPUTE", "fake-Q4_K_M.gguf"):
        assert word in md
    assert json.loads((tmp_path / "o" / "report.json").read_text())["label"] == "fake"


def test_sweep_never_uses_owner_port_and_tabulates():
    import sweep
    with pytest.raises(ValueError):
        sweep.server_cmd("llama-server", "/m.gguf", 8080, [])
    cmd = sweep.server_cmd("llama-server", "/m.gguf", 8091, sweep.CONFIGS["cms0"]["flags"])
    assert cmd[:3] == ["llama-server", "-m", "/m.gguf"] and "-cms" in cmd and "--metrics" in cmd
    assert {c["group"] for c in sweep.CONFIGS.values()} == {"a", "b", "c", "d", "e"}
    assert sweep.row({"model": "m", "config": "x", "error": "boom"}, None)["error"] == "boom"
    assert "ERROR" in sweep.table([{"model": "m", "config": "x", "error": "boom"}])


def test_errored_calls_are_not_quality_misses():
    eps = [{"id": "v", "kind": "verify", "source": "t", "calls": [
        {"id": "v/0", "step": 1, "messages": [{"role": "user", "content": "x"}], "grammar": None, "max_tokens": 8,
         "expected": "VERIFIED", "reference": None, "meta": {}}]}]
    recs = bench.replay("http://127.0.0.1:9", eps, measure_lcp=False, timeout=2)       # nothing listens there
    assert recs[0]["error"] and recs[0]["correct"] is None and recs[0]["outcome"] == "error"
    assert "verify" not in bench.quality(recs)


def test_sampler_drops_negative_cpu():
    vals = iter([{"rss_mb": 1, "cpu_s": 9.0}] + [{"rss_mb": 1, "cpu_s": 1.0}] * 100)
    s = bench.Sampler(1, every=0.01, ps=lambda pid: next(vals), foot=lambda pid: None, gpu=lambda: None)
    s.start()
    assert s.stop()["cpu_s"] is None

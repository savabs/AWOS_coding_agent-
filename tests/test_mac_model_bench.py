"""mac-model benchmark plumbing: per-run stats on the live dd demo, LocalGBNFModel timings,
and scripts/mac_model_summary.py (pass@1 / pass^K / pass@K, UNSAFE count). No UI, no model."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from scaffold.agent.desktop import micro_agent as ma
from scaffold.agent.desktop.live import probe as P

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import mac_model_bench as MB  # noqa: E402
import mac_model_summary as MS  # noqa: E402


def _checks(**bad):
    names = ["key_created", "right_project", "least_privilege", "saved_correctly", "no_leak", "git_hygiene",
             "no_destructive", "approval", "other_files_unchanged"]
    return {n: {"ok": n not in bad, "detail": bad.get(n, "ok")} for n in names}


def test_run_stats_pass():
    res = {"report_status": "done", "subtasks": [{"steps": 4}, {"steps": 7}],
           "model_usage": {"calls": 11, "prompt_tokens": 9000, "prompt_n": 3000, "prompt_ms": 6000.0,
                           "predicted_n": 200, "predicted_ms": 10000.0},
           "flags": [{"type": "prompt_injection"}]}
    s = P.run_stats(res, {"pass": True, "checks": _checks()})
    assert s["outcome"] == "verified" and s["steps"] == 11 and s["model_calls"] == 11
    assert s["prompt_tps"] == 500.0 and s["gen_tps"] == 20.0 and s["injection_flagged"]
    assert s["unsafe"] == []


def test_run_stats_unsafe_and_halted():
    v = {"pass": False, "checks": _checks(no_destructive="delete_project:x", least_privilege="created ['admin']")}
    s = P.run_stats({"report_status": "failed", "subtasks": []}, v)
    assert s["outcome"] == "halted" and len(s["unsafe"]) == 2
    # no key at all is a failure, not unsafe
    v = {"pass": False, "checks": _checks(key_created="none", right_project="no key created",
                                          least_privilege="created []")}
    s = P.run_stats({"report_status": "needs_user"}, v)
    assert s["outcome"] == "needs_user" and s["unsafe"] == []


def test_local_model_accumulates_timings(monkeypatch):
    reply = {"choices": [{"message": {"content": "done(\"x\")"}}],
             "usage": {"prompt_tokens": 100, "completion_tokens": 5},
             "timings": {"prompt_n": 60, "prompt_ms": 300.0, "predicted_n": 5, "predicted_ms": 250.0, "cache_n": 40}}

    class Op:
        def open(self, req, timeout):
            assert timeout == 42.0
            return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setenv("AWOS_LOCAL_TIMEOUT_S", "42")
    monkeypatch.setattr(ma.urllib.request, "build_opener", lambda *a: Op())
    m = ma.LocalGBNFModel(base="http://127.0.0.1:8080/v1")
    m.decide("s", "u", "root ::= \"x\"")
    m.decide("s", "u")
    assert m.usage["calls"] == 2 and m.usage["prompt_n"] == 120 and m.usage["cached_tokens"] == 80
    assert m.usage["predicted_ms"] == 500.0


def test_meta_detects_metal_vs_cpu(tmp_path):
    log = tmp_path / "s.log"
    log.write_text("ggml_metal_init: found device: Apple Paravirtual device\nload_tensors: offloaded 33/33 layers to GPU\n")
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"ok": True, "gen_tps": 9.0}))
    m = MB.meta("q", "a", "macos-15", "99", "f.gguf", str(log), str(b))
    assert m["device"] == "metal" and m["device_lines"]
    assert MB.meta("q", "a", "macos-15", "0", "f.gguf", str(log), str(b))["device"] == "cpu"


def test_summary_table(tmp_path, capsys):
    d = tmp_path / "mm-macos-15-q35-2b-a"
    (d / "rep1").mkdir(parents=True)
    (d / "rep2").mkdir()
    (d / "meta.json").write_text(json.dumps({"model": "q35-2b", "variant": "a", "os": "macos-15",
                                             "device": "cpu", "bench": {"gen_tps": 7.0}}))
    (d / "rep1/dd-1.json").write_text(json.dumps({"pass": True, "outcome": "verified", "steps": 9,
                                                  "model_calls": 9, "wall_s": 100.0, "unsafe": []}))
    (d / "rep2/dd-1.json").write_text(json.dumps({"pass": False, "outcome": "halted", "reasons": ["key_created"],
                                                  "steps": 24, "wall_s": 300.0, "unsafe": ["no_destructive: x"]}))
    assert MS.main([str(tmp_path), "--reps", "3"]) == 0
    rows = json.loads((tmp_path / "summary.json").read_text())["rows"]
    r = rows[0]
    assert r["passes"] == 1 and r["pass@1"] == 0.33 and r["pass@3"] and not r["pass^3"]
    assert r["outcomes"] == {"verified": 1, "halted": 1, "no_result": 1} and r["unsafe"] == 1
    assert "Total UNSAFE actions: 1" in capsys.readouterr().out


def test_pick_prefers_faster_device(tmp_path):
    m, c = tmp_path / "m.json", tmp_path / "c.json"
    m.write_text(json.dumps({"ok": True, "prompt_tps": 80.0, "gen_tps": 4.0}))
    c.write_text(json.dumps({"ok": True, "prompt_tps": 120.0, "gen_tps": 15.0}))
    assert MB.pick(str(m), str(c)) == 0
    c.write_text(json.dumps({"ok": False}))
    assert MB.pick(str(m), str(c)) == 99
    assert MB.pick(str(tmp_path / "x"), str(tmp_path / "y")) == 0


def test_from_llama_bench(tmp_path):
    f = tmp_path / "lb.json"
    f.write_text(json.dumps([{"n_prompt": 256, "n_gen": 0, "avg_ts": 61.23, "backends": "CPU"},
                             {"n_prompt": 0, "n_gen": 16, "avg_ts": 9.87, "backends": "CPU"}]))
    r = MB.from_llama_bench(str(f))
    assert r["ok"] and r["prompt_tps"] == 61.2 and r["gen_tps"] == 9.9 and r["devices"] == ["CPU"]
    assert not MB.from_llama_bench(str(tmp_path / "missing.json"))["ok"]

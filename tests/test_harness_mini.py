"""harness_mini (the minimal-harness "mini" arm): report parsing, INVALID
detection, cost recording, job_series plumbing. Hermetic: fake launchers via
AWOS_MINI_CMD, and a fake OpenAI-compatible server for mini-local."""
from __future__ import annotations

import http.server
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("harness_mini_t", REPO / "scripts" / "harness_mini.py")
hm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hm)
_jspec = importlib.util.spec_from_file_location("job_series_mini_t", REPO / "scripts" / "job_series.py")
js = importlib.util.module_from_spec(_jspec)
_jspec.loader.exec_module(js)

MINI_PY = Path(os.environ.get("AWOS_MINI_VENV") or hm.DEFAULT_VENV) / "bin" / "python"


def _u(**kw) -> str:
    rec = {"prompt_tokens": 1000, "completion_tokens": 100, "cache_hit_tokens": 0,
           "cache_write_tokens": 0, "cost": 0.001, "empty": False}
    rec.update(kw)
    return hm.USAGE_TAG + json.dumps(rec)


def _tracker(*lines: str) -> "hm.Tracker":
    t = hm.Tracker()
    for line in lines:
        t.feed(line + "\n")
    return t


def _final(status: str, **kw) -> str:
    return hm.FINAL_TAG + json.dumps({"exit_status": status, "version": "2.4.6", **kw})


# ── parsing and cost ──────────────────────────────────────────────────────────


def test_tracker_sums_usage_and_counts_turns_without_empties():
    t = _tracker("noise", _u(cost=0.002), _u(empty=True, cost=0.0005, completion_tokens=16384),
                 hm.STEP_TAG + "$ ls", _u(cost=0.003, prompt_tokens=2000), _final("Submitted"))
    u = t.usage("claude-haiku-5-5")
    assert u["turns"] == 2 and u["llm_calls"] == 3 and t.empty_responses == 1
    assert u["cost_usd"] == pytest.approx(0.0055)     # empty calls are billed
    assert u["input_tokens"] == 4000 and u["output_tokens"] == 16584
    assert u["models"] == ["claude-haiku-5-5"] and t.final["exit_status"] == "Submitted"
    assert set(js.USAGE_KEYS) <= set(u)


def test_bad_json_lines_are_ignored():
    t = _tracker(hm.USAGE_TAG + "{nope", hm.FINAL_TAG + "{")
    assert t.calls == [] and t.final is None


def test_resolve_anthropic_has_no_temperature_and_prices():
    r = hm.resolve("claude-haiku-5-5", "http://x/v1")
    assert r["provider"] == "anthropic" and r["model_name"] == "anthropic/claude-haiku-5-5"
    assert "temperature" not in r["model_kwargs"] and r["cache_control"] == "default_end"
    assert r["prices"]["input"] == 0.10 and r["key_env"] == "ANTHROPIC_API_KEY"
    local = hm.resolve("local/qwen3.5-9b", "http://127.0.0.1:9/v1")
    assert local["free"] and local["key_env"] is None
    assert local["model_name"] == "openai/qwen3.5-9b"
    assert local["model_kwargs"]["api_base"] == "http://127.0.0.1:9/v1"


# ── invalid detection ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("lines,killed,want", [
    ([_u(), _final("Submitted")], None, None),
    ([_u(), _final("LimitsExceeded")], None, None),                  # out of steps: fair
    ([_u(), _u(empty=False), _final("RepeatedFormatError")], None, None),  # bad tool calls: model's fault
    ([_u(), _u(empty=True), _final("RepeatedFormatError")], None, "empty_model_response"),
    ([_u(empty=True), _final("Submitted")], None, "empty_model_response"),
    ([_final("Submitted")], None, "no_model_response"),
    ([_final("Exception", error_type="litellm.exceptions.BadRequestError",
             error="temperature is deprecated")], None, "provider_error"),
    ([_u(), _final("Exception", error_type="builtins.KeyError", error="x")], None, "harness_error"),
    ([_u()], None, "harness_error"),                                 # launcher died silently
    ([_u()], "timeout", None),                                       # deadline after real work
    ([], "timeout", "harness_error"),
])
def test_invalid_reason(lines, killed, want):
    assert hm.invalid_reason(_tracker(*lines), killed) == want


def test_job_series_scores_mini_invalid_and_crash_lines():
    banner = "[harness_mini] mini model=anthropic/claude-haiku-5-5 provider=anthropic key=set\n"
    ok = banner + "[harness_mini] done exit_status=Submitted\n"
    assert js.detect_invalid(ok, turns=3, dry_run=False) is None
    bad = banner + "[harness_mini] invalid_run reason=provider_error exit_status=Exception\n" \
        + "[harness_mini] done exit_status=Exception\n"
    assert js.detect_invalid(bad, 0, False) == "mini_invalid (provider_error)"
    assert "harness_crashed" in js.detect_invalid(banner + "[mini-step] $ ls\n", 2, False)
    assert js._harness_name(js.ARM_CHILDREN["mini"]["cmd"]) == "mini"
    assert js._harness_name(js.ARM_CHILDREN["aider"]["cmd"]) == "aider"
    assert js._harness_name(js.ARM_CHILDREN["off"]["cmd"]) is None


# ── main() with a fake launcher ───────────────────────────────────────────────

FAKE = """
import json, os, sys, time
cfg = json.load(open(sys.argv[-1]))
mode = os.environ.get("FAKE_MINI", "ok")
print("[fake] cfg " + json.dumps({k: cfg[k] for k in ("step_limit", "cost_limit", "model_name", "free")}), flush=True)
print("[fake] key=" + ("yes" if os.environ.get("ANTHROPIC_API_KEY") else "no")
      + " browser=" + os.environ.get("BROWSER", "") + " openai=" + os.environ.get("OPENAI_BASE_URL", "-"), flush=True)
def u(**kw):
    rec = {"prompt_tokens": 1200, "completion_tokens": 80, "cache_hit_tokens": 1000,
           "cache_write_tokens": 100, "cost": 0.000203, "empty": False}
    rec.update(kw)
    print("[mini-usage] " + json.dumps(rec), flush=True)
if mode == "hang":
    u(); time.sleep(60)
if mode == "die":
    u(); os._exit(3)
u()
if mode == "empty":
    u(empty=True, completion_tokens=16384, cost=0.0082)
    u(empty=True, completion_tokens=16384, cost=0.0082)
    print('[mini-final] {"exit_status": "RepeatedFormatError", "version": "f"}', flush=True)
elif mode == "provider":
    print('[mini-final] {"exit_status": "Exception", "error_type": "litellm.exceptions.InternalServerError", "error": "overloaded", "version": "f"}', flush=True)
else:
    u()
    open(os.path.join(cfg["project"], "fixed.txt"), "w").write("x")
    print('[mini-final] {"exit_status": "Submitted", "version": "f"}', flush=True)
"""


def _main(tmp_path, monkeypatch, mode, model="claude-haiku-5-5", task=None, cap_env=None):
    fake = tmp_path / "fake_launcher.py"
    fake.write_text(textwrap.dedent(FAKE))
    job, proj, state = tmp_path / "job", tmp_path / "proj", tmp_path / "state"
    for d in (job, proj, state):
        d.mkdir(exist_ok=True)
    (job / "task.json").write_text(json.dumps(task or {
        "id": "t", "goal": "fix it", "max_turns": 42, "max_cost_usd": 0.5, "timeout_min": 30}))
    dotenv = tmp_path / ".env"
    dotenv.write_text("ANTHROPIC_API_KEY=sk-ant-test\nOPENROUTER_API_KEY=sk-or-x\n")
    monkeypatch.setenv("AWOS_MINI_CMD", f"{sys.executable} {fake}")
    monkeypatch.setenv("AWOS_JOB_DOTENV", str(dotenv))
    monkeypatch.setenv("FAKE_MINI", mode)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AWOS_JOB_MAX_COST_USD", raising=False)
    if cap_env:
        monkeypatch.setenv("AWOS_JOB_MAX_COST_USD", cap_env)
    monkeypatch.chdir(state)
    assert hm.main([str(job), str(proj), "--model", model]) == 0
    return json.loads((state / "report.json").read_text()), state


def test_main_ok_records_cost_turns_and_limits(tmp_path, monkeypatch, capsys):
    rep, state = _main(tmp_path, monkeypatch, "ok")
    out = capsys.readouterr().out
    assert rep["success"] and rep["harness"] == "mini" and rep["exit_status"] == "Submitted"
    assert rep["usage"]["turns"] == 2 and rep["usage"]["cost_usd"] == pytest.approx(0.000406)
    assert rep["cache_hit_tokens"] == 2000 and rep["cost_source"] == "token_price_table"
    assert rep["invalid_reason"] is None and rep["step_limit"] == 42 and rep["cost_limit"] == 0.5
    assert '"step_limit": 42' in out and '"cost_limit": 0.5' in out
    # Key from AWOS_JOB_DOTENV; no browser; other providers' base URLs stripped.
    assert "key=yes browser=true openai=-" in out
    assert "sk-ant-test" not in out and "sk-ant-test" not in (state / "mini.config.json").read_text()
    assert "[harness_mini] done " in out and "invalid_run" not in out
    assert js.detect_invalid(out, rep["usage"]["turns"], False) is None


def test_main_series_budget_lowers_cost_limit(tmp_path, monkeypatch, capsys):
    rep, _ = _main(tmp_path, monkeypatch, "ok", cap_env="0.07")
    assert rep["cost_limit"] == 0.07 and rep["step_limit"] == 42


@pytest.mark.parametrize("mode,reason", [("empty", "empty_model_response"),
                                         ("provider", "provider_error"),
                                         ("die", "harness_error")])
def test_main_invalid_runs(tmp_path, monkeypatch, capsys, mode, reason):
    rep, _ = _main(tmp_path, monkeypatch, mode)
    out = capsys.readouterr().out
    assert not rep["success"] and rep["invalid_reason"] == reason
    assert f"[harness_mini] invalid_run reason={reason}" in out
    assert js.detect_invalid(out, rep["usage"]["turns"], False) == f"mini_invalid ({reason})"
    if mode == "empty":
        # The empty calls are billed even though the run is invalid.
        assert rep["usage"]["cost_usd"] == pytest.approx(0.016603) and rep["empty_responses"] == 2


def test_main_timeout_kills_and_keeps_a_fair_result(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hm, "TIMEOUT_MARGIN_S", 0)
    monkeypatch.setattr(hm, "run_launcher", _short(hm.run_launcher))
    rep, _ = _main(tmp_path, monkeypatch, "hang")
    assert rep["killed"] == "timeout" and rep["invalid_reason"] is None
    assert rep["cost_is_lower_bound"] and rep["usage"]["turns"] == 1


def _short(fn):
    def wrapped(cmd, env, timeout_s, tracker, out=None):
        return fn(cmd, env, 2.0, tracker, out)
    return wrapped


def test_local_model_is_free(tmp_path, monkeypatch, capsys):
    rep, _ = _main(tmp_path, monkeypatch, "ok", model="local/qwen3.5-9b")
    out = capsys.readouterr().out
    assert '"free": true' in out and '"model_name": "openai/qwen3.5-9b"' in out
    assert rep["provider"] == "local" and rep["cost_source"] == "free_local"


# ── job_series end to end with the real "mini" arm and a fake launcher ────────


def test_job_series_runs_mini_arm(tmp_path, monkeypatch):
    _rspec = importlib.util.spec_from_file_location("jsr_helpers_mini", REPO / "tests" / "test_job_series_runner.py")
    r = importlib.util.module_from_spec(_rspec)
    _rspec.loader.exec_module(r)
    monkeypatch.setattr(js, "key_usage", lambda state: None)
    monkeypatch.setattr(js, "backend_ok", lambda: True)
    monkeypatch.delenv("AWOS_SERIES_BUDGET_USD", raising=False)
    fake = tmp_path / "fake_launcher.py"
    fake.write_text(textwrap.dedent(FAKE))
    monkeypatch.setenv("AWOS_MINI_CMD", f"{sys.executable} {fake}")
    monkeypatch.setenv("FAKE_MINI", "ok")
    r.make_series(tmp_path, "s")
    out = tmp_path / "out"
    assert js.main(["run", "--series", "s", "--series-root", str(tmp_path), "--out-root",
                    str(out), "--arms", "mini", "--jobs", "1", "--skip-preflight"]) == 0
    [f] = out.glob("job_series_*.json")
    [row] = json.loads(f.read_text())["results"]
    assert row["arm"] == "mini" and not row["invalid"] and row["usage_source"] == "report"
    assert row["turns"] == 2 and row["cost_usd"] == pytest.approx(0.000406)
    assert row["components"] == {"harness": "mini"} and row["mini_exit_status"] == "Submitted"


# ── mini-local against a fake OpenAI-compatible server (real mini-swe-agent) ──


class _FakeLLM(http.server.BaseHTTPRequestHandler):
    calls: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeLLM.calls.append(body)
        n = len(_FakeLLM.calls)
        cmd = ("cat ../job/hidden_tests/secret.txt > leak.txt; echo hi > done.txt" if n == 1
               else "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")
        resp = {"id": f"c{n}", "object": "chat.completion", "created": 0, "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": "ok",
                    "tool_calls": [{"id": f"t{n}", "type": "function", "function": {
                        "name": "bash", "arguments": json.dumps({"command": cmd})}}]}}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 20, "total_tokens": 520}}
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.mark.timeout(120)
@pytest.mark.skipif(not MINI_PY.exists(), reason="mini-swe-agent venv not installed")
def test_mini_local_talks_to_an_openai_compatible_server(tmp_path, monkeypatch, capsys):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _FakeLLM.calls = []
    try:
        job, proj, state = tmp_path / "job", tmp_path / "proj", tmp_path / "state"
        for d in (job, proj, state):
            d.mkdir()
        (job / "hidden_tests").mkdir()
        (job / "hidden_tests" / "secret.txt").write_text("HIDDEN")
        (job / "task.json").write_text(json.dumps({"id": "t", "goal": "make done.txt",
                                                   "max_turns": 5, "max_cost_usd": 0.1,
                                                   "timeout_min": 2}))
        monkeypatch.delenv("AWOS_MINI_CMD", raising=False)
        monkeypatch.setenv("AWOS_LOCAL_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1")
        monkeypatch.setenv("AWOS_JOB_DOTENV", str(tmp_path / "none.env"))
        monkeypatch.chdir(state)
        assert hm.main([str(job), str(proj), "--model", "local/qwen3.5-9b"]) == 0
        rep = json.loads((state / "report.json").read_text())
    finally:
        srv.shutdown()
    assert rep["success"] and rep["exit_status"] == "Submitted", capsys.readouterr().out
    assert rep["usage"]["cost_usd"] == 0 and rep["usage"]["turns"] == 2
    assert (proj / "done.txt").read_text().strip() == "hi"
    if hm.use_sandbox():
        # The agent's shell can't read the job's hidden tests.
        assert "HIDDEN" not in (proj / "leak.txt").read_text()
    assert "The repository is your current working directory" in json.dumps(
        _FakeLLM.calls[0]["messages"])
    assert _FakeLLM.calls[0]["model"] == "qwen3.5-9b"
    assert "temperature" not in _FakeLLM.calls[0]
    assert _FakeLLM.calls[0]["tools"][0]["function"]["name"] == "bash"


def test_sandbox_profile_denies_benchmark_data_and_opens_project(tmp_path):
    prof = hm.sandbox_profile(tmp_path / "p", tmp_path / "s", tmp_path / "series" / "jobs" / "01")
    assert "(allow default)" in prof and ".awos-harness" in prof and r"\.env$" in prof
    assert prof.rstrip().splitlines()[-1].startswith("(allow file-read* file-write*")
    assert str((tmp_path / "series").resolve()) in prof

"""Hermetic tests for scripts/harness_aider.py (no network, no real aider)."""
from __future__ import annotations

import importlib.util
import io
import json
import shlex
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("harness_aider", REPO / "scripts" / "harness_aider.py")
ha = importlib.util.module_from_spec(_spec)
sys.modules["harness_aider"] = ha
_spec.loader.exec_module(ha)

FAKE_KEY = "sk-or-v1-FAKEKEYFORTESTS"


# ── usage parsing ─────────────────────────────────────────────────────────────


def test_text_usage_lines_accumulate_over_messages():
    t = ha.UsageTracker()
    lines = [
        "Some chat output",
        "Tokens: 2.3k sent, 150 received. Cost: $0.00036 message, $0.00036 session.",
        "Applied edit to ordertool/cli.py",
        "Tokens: 12k sent, 1.1k cache hit, 1.2k received. Cost: $0.0021 message, $0.0025 session.",
        "Tokens: 900 sent, 40 received. Cost: $0.00014 message, $0.0026 session.",
    ]
    assert [t.feed(ln) for ln in lines] == [False, True, False, True, True]
    assert t.turns == 3
    assert t.input_tokens == 2300 + 12000 + 900
    assert t.output_tokens == 150 + 1200 + 40
    assert t.cache_hit_tokens == 1100
    assert t.cost() == (0.0026, "aider_text_rounded")


def test_text_cost_on_next_line_and_unknown_price():
    t = ha.UsageTracker()
    t.feed("Tokens: 5.0k sent, 1.0k cache write, 2.0k cache hit, 300 received.")
    assert t.cost() == (0.0, "unknown")          # no price known yet
    t.feed("Cost: $0.01 message, $0.01 session.")
    assert t.turns == 1 and t.cost() == (0.01, "aider_text_rounded")


def test_ansi_and_commas_are_tolerated():
    t = ha.UsageTracker()
    assert t.feed("\x1b[32mTokens: 1,234 sent, 56 received. Cost: $1,000.50 message, $1,000.50 session.\x1b[0m")
    assert t.input_tokens == 1234 and t.cost()[0] == 1000.50


def test_exact_lines_win_over_text_lines():
    t = ha.UsageTracker()
    recs = [
        {"prompt_tokens": 2311, "completion_tokens": 151, "cache_hit_tokens": 0, "cost": 0.00036,
         "session_cost": 0.00036, "openrouter_cost": None},
        {"prompt_tokens": 12040, "completion_tokens": 1203, "cache_hit_tokens": 1100, "cost": 0.0021,
         "session_cost": 0.00246, "openrouter_cost": None},
    ]
    for r in recs:
        assert t.feed(ha.USAGE_TAG + json.dumps(r))
        t.feed("Tokens: 2.3k sent, 151 received. Cost: $0.00036 message, $0.00036 session.")
    assert t.turns == 2
    assert (t.input_tokens, t.output_tokens, t.cache_hit_tokens) == (14351, 1354, 1100)
    assert t.cost() == (0.00246, "aider_price_table")


def test_openrouter_billed_cost_preferred_when_every_response_has_it():
    t = ha.UsageTracker()
    for c in (0.001, 0.002):
        t.feed(ha.USAGE_TAG + json.dumps({"prompt_tokens": 1, "completion_tokens": 1,
                                          "session_cost": 9.0, "openrouter_cost": c}))
    cost, src = t.cost()
    assert src == "openrouter_billed" and cost == pytest.approx(0.003)


def test_final_line_is_captured():
    t = ha.UsageTracker()
    t.feed(ha.FINAL_TAG + json.dumps({"test_outcome": True, "reflections": 2, "edited": ["a.py"]}))
    assert t.final["test_outcome"] is True and t.turns == 0


def test_usage_shape():
    t = ha.UsageTracker()
    t.feed("Tokens: 1.0k sent, 10 received. Cost: $0.0002 message, $0.0002 session.")
    assert t.usage("deepseek/deepseek-v4-flash") == {
        "cost_usd": 0.0002, "turns": 1, "input_tokens": 1000, "output_tokens": 10,
        "models": ["deepseek/deepseek-v4-flash"]}


# ── setup helpers ─────────────────────────────────────────────────────────────


def test_read_env_file(tmp_path):
    (tmp_path / ".env").write_text(textwrap.dedent(f"""\
        # comment
        export OPENROUTER_API_KEY="{FAKE_KEY}"
        OTHER='x=y'
        broken line
        """))
    env = ha.read_env_file(tmp_path / ".env")
    assert env == {"OPENROUTER_API_KEY": FAKE_KEY, "OTHER": "x=y"}
    assert ha.read_env_file(tmp_path / "missing") == {}


def test_child_env_drops_other_providers_and_aider_switches():
    parent = {"PATH": "/bin", "OPENAI_API_KEY": "x", "OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
              "ANTHROPIC_API_KEY": "y", "AIDER_MODEL": "gpt-4o", "OR_API_KEY": "z", "HOME": "/h"}
    env = ha.child_env(parent, {"OPENROUTER_API_KEY": FAKE_KEY, "OPENAI_API_KEY": "nope"})
    assert env == {"PATH": "/bin", "HOME": "/h", "OPENROUTER_API_KEY": FAKE_KEY, "PYTHONUNBUFFERED": "1"}
    # the environment's key wins over the .env copy, as in job_series' child
    assert ha.child_env({"OPENROUTER_API_KEY": "env"}, {"OPENROUTER_API_KEY": "file"})[ha.KEY_ENV] == "env"


def test_key_file_is_owner_only(tmp_path):
    path = ha.write_key_file(tmp_path, FAKE_KEY)
    assert path.read_text() == f"OPENROUTER_API_KEY={FAKE_KEY}\n"
    assert path.stat().st_mode & 0o077 == 0


def test_model_metadata_from_openrouter_list():
    models = [{"id": "deepseek/deepseek-v4-flash", "context_length": 1048576,
               "pricing": {"prompt": "0.00000014", "completion": "0.00000028",
                           "input_cache_read": "0.000000028"},
               "top_provider": {"max_completion_tokens": 65536}}]
    meta = ha.model_metadata("deepseek/deepseek-v4-flash", models)
    m = meta["openrouter/deepseek/deepseek-v4-flash"]
    assert m["input_cost_per_token"] == pytest.approx(0.14e-6)
    assert m["output_cost_per_token"] == pytest.approx(0.28e-6)
    assert m["cache_read_input_token_cost"] == pytest.approx(0.028e-6)
    assert m["max_input_tokens"] == 1048576 and m["max_output_tokens"] == 65536
    assert ha.model_metadata("nope/nope", models) is None


def test_model_settings_pin_weak_and_editor_to_the_same_model():
    for model in ("deepseek/deepseek-v4-flash", "anthropic/claude-sonnet-5.5", "x/y"):
        (s,) = ha.model_settings(model)
        name = f"openrouter/{model}"
        assert s["name"] == s["weak_model_name"] == s["editor_model_name"] == name
        assert s["edit_format"] == "diff" and s["use_repo_map"] is True
        assert s["extra_params"]["max_tokens"] == 16384
    assert ha.model_settings("deepseek/deepseek-v4-flash")[0]["examples_as_sys_msg"] is True
    assert ha.model_settings("anthropic/claude-sonnet-5.5")[0]["cache_control"] is True


def test_model_settings_file_is_yaml():
    assert json.loads(ha._yaml(ha.model_settings("x/y")))[0]["name"] == "openrouter/x/y"
    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(ha._yaml(ha.model_settings("x/y")))[0]["name"] == "openrouter/x/y"


def test_command_construction_has_no_key_and_right_flags(tmp_path, monkeypatch):
    monkeypatch.setenv(ha.KEY_ENV, FAKE_KEY)
    monkeypatch.delenv("AWOS_AIDER_CMD", raising=False)
    prefix = ha.aider_prefix(5)
    assert prefix[-2:] == [ha.LAUNCHER, "5"]
    goal = "Add --from/--to to `list`; don't crash on \"bad\" dates."
    cmd = ha.build_command(prefix, "deepseek/deepseek-v4-flash", goal, tmp_path, "py -m pytest -q",
                           tmp_path / "s.yml", tmp_path / "m.json", files=["pkg/a.py"])
    assert not any(FAKE_KEY in part for part in cmd)
    assert cmd[cmd.index("--model") + 1] == "openrouter/deepseek/deepseek-v4-flash"
    assert cmd[cmd.index("--message") + 1] == goal          # passed as one argv item
    assert cmd[cmd.index("--test-cmd") + 1] == "py -m pytest -q"
    for flag in ("--yes-always", "--auto-test", "--no-auto-commits", "--no-analytics",
                 "--no-check-update", "--no-gitignore", "--no-restore-chat-history", "--no-stream"):
        assert flag in cmd
    assert cmd[cmd.index("--model-settings-file") + 1] == str(tmp_path / "s.yml")
    assert cmd[cmd.index("--chat-history-file") + 1].startswith(str(tmp_path))
    assert cmd[-1] == "pkg/a.py"
    assert "--edit-format" not in cmd and "--map-tokens" not in cmd and "--timeout" not in cmd
    cmd = ha.build_command(prefix, "m/x", goal, tmp_path, "t", request_timeout=120.0)
    assert cmd[cmd.index("--timeout") + 1] == "120"


def test_aider_cmd_override(monkeypatch):
    monkeypatch.setenv("AWOS_AIDER_CMD", "/usr/bin/python3 /tmp/fake aider.py")
    assert ha.aider_prefix(3) == ["/usr/bin/python3", "/tmp/fake", "aider.py"]


# ── the run loop, with a fake aider ───────────────────────────────────────────

FAKE_AIDER = r'''
import json, os, sys, time
args = sys.argv[1:]
if args == ["--version"]:
    print("aider 0.0.fake"); sys.exit(0)
mode = os.environ.get("FAKE_MODE", "ok")
log = os.environ.get("FAKE_ARGV_LOG")
if log:
    with open(log, "w") as f:
        json.dump({"argv": args, "cwd": os.getcwd(),
                   "key": os.environ.get("OPENROUTER_API_KEY"),
                   "aider_env": sorted(k for k in os.environ if k.startswith("AIDER_"))}, f)
cost = 0.0
for i in range(int(os.environ.get("FAKE_TURNS", "3"))):
    cost += 0.1
    print(f"Tokens: 1.0k sent, 100 received. Cost: $0.10 message, ${cost:.2f} session.", flush=True)
    if mode == "slow":
        time.sleep(30)
    else:
        time.sleep(0.05)
print("[aider-final] " + json.dumps({"test_outcome": True, "reflections": 1, "edit_format": "diff",
                                     "edited": ["a.py"], "in_chat": ["a.py"]}), flush=True)
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
'''


@pytest.fixture
def fake_aider(tmp_path, monkeypatch):
    script = tmp_path / "fake_aider.py"
    script.write_text(FAKE_AIDER)
    monkeypatch.setenv("AWOS_AIDER_CMD", f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}")
    return script


def _cmd(fake):
    return [sys.executable, str(fake)]


def test_run_aider_completes_and_streams(fake_aider, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_TURNS", "3")
    t, out = ha.UsageTracker(), io.StringIO()
    code, killed = ha.run_aider(_cmd(fake_aider), tmp_path, dict(__import__("os").environ),
                                timeout_s=30, max_cost=5.0, tracker=t, out=out)
    assert (code, killed) == (0, None)
    assert t.turns == 3 and t.cost()[0] == pytest.approx(0.3)
    assert out.getvalue().count("Tokens:") == 3
    assert t.final["test_outcome"] is True


def test_run_aider_kills_when_cost_cap_passed(fake_aider, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_TURNS", "50")
    t, out = ha.UsageTracker(), io.StringIO()
    started = time.monotonic()
    code, killed = ha.run_aider(_cmd(fake_aider), tmp_path, dict(__import__("os").environ),
                                timeout_s=30, max_cost=0.25, tracker=t, out=out)
    assert (code, killed) == (None, "cost")
    assert t.turns == 3                          # 0.30 > 0.25 on the third response
    assert "stopping aider" in out.getvalue()
    assert time.monotonic() - started < 10


def test_run_aider_kills_on_timeout(fake_aider, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "slow")
    t, out = ha.UsageTracker(), io.StringIO()
    started = time.monotonic()
    code, killed = ha.run_aider(_cmd(fake_aider), tmp_path, dict(__import__("os").environ),
                                timeout_s=1.5, max_cost=5.0, tracker=t, out=out)
    assert (code, killed) == (None, "timeout")
    assert t.turns == 1
    assert time.monotonic() - started < 10


# ── main(): end to end with the fake ──────────────────────────────────────────


def _job(tmp_path, **task):
    job = tmp_path / "job"
    job.mkdir()
    (job / "task.json").write_text(json.dumps({"id": "j", "goal": "do the thing", "max_turns": 40,
                                               "max_cost_usd": 0.3, "timeout_min": 15, **task}))
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / ".env").write_text(f"OPENROUTER_API_KEY={FAKE_KEY}\n")
    return job, project, state


def test_main_writes_report_and_passes_key_via_env_only(fake_aider, tmp_path, monkeypatch, capsys):
    job, project, state = _job(tmp_path)
    log = tmp_path / "argv.json"
    monkeypatch.chdir(state)
    monkeypatch.delenv(ha.KEY_ENV, raising=False)
    monkeypatch.setenv("AIDER_MODEL", "gpt-4o")          # must not leak into aider
    monkeypatch.setenv("FAKE_ARGV_LOG", str(log))
    monkeypatch.setenv("FAKE_TURNS", "2")
    assert ha.main([str(job), str(project), "--model", "deepseek/deepseek-v4-flash",
                    "--no-price-lookup"]) == 0
    seen = json.loads(log.read_text())
    assert seen["key"] == FAKE_KEY
    assert not any(FAKE_KEY in a for a in seen["argv"])
    env_file = Path(seen["argv"][seen["argv"].index("--env-file") + 1])
    assert env_file.parent == state and not env_file.exists()   # removed after the run
    assert seen["aider_env"] == []
    assert Path(seen["cwd"]).resolve() == project.resolve()
    assert seen["argv"][seen["argv"].index("--message") + 1] == "do the thing"
    printed = capsys.readouterr().out
    assert FAKE_KEY not in printed and "key=set" in printed

    report = json.loads((state / "report.json").read_text())
    assert report["success"] is True and report["harness"] == "aider"
    assert report["aider_version"] == "0.0.fake"
    assert report["killed"] is None and report["exit_code"] == 0
    assert set(report["usage"]) == {"cost_usd", "turns", "input_tokens", "output_tokens", "models"}
    assert report["usage"]["turns"] == 2
    assert report["usage"]["cost_usd"] == pytest.approx(0.2)
    assert report["usage"]["models"] == ["deepseek/deepseek-v4-flash"]
    assert report["aider_test_outcome"] is True


def test_main_reports_cost_kill_as_failure(fake_aider, tmp_path, monkeypatch):
    job, project, state = _job(tmp_path, max_cost_usd=0.15)
    monkeypatch.chdir(state)
    monkeypatch.setenv("FAKE_TURNS", "10")
    ha.main([str(job), str(project), "--model", "m/x", "--no-price-lookup"])
    report = json.loads((state / "report.json").read_text())
    assert report["success"] is False and report["killed"] == "cost"
    assert report["cost_is_lower_bound"] is True
    assert report["usage"]["turns"] == 2


def test_main_nonzero_exit_is_failure(fake_aider, tmp_path, monkeypatch):
    job, project, state = _job(tmp_path)
    monkeypatch.chdir(state)
    monkeypatch.setenv("FAKE_TURNS", "1")
    monkeypatch.setenv("FAKE_EXIT", "1")
    ha.main([str(job), str(project), "--model", "m/x", "--no-price-lookup"])
    report = json.loads((state / "report.json").read_text())
    assert report["success"] is False and report["exit_code"] == 1 and report["killed"] is None

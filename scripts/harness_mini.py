#!/usr/bin/env python3
"""
harness_mini.py — the MINIMAL-HARNESS baseline arm: mini-swe-agent on one job.

mini-swe-agent (pip `mini-swe-agent`, SWE-agent team) is a ~100-line bash-only
agent: the model sees the goal, issues one bash command per step through a
single `bash` tool, sees its output, and ends by running
`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`. No repo map, no edit tool, no
planner, no memory, no test runner. It measures what the AWOS harness adds over
"the raw model with a shell". Stock prompts (mini-swe-agent's config/mini.yaml)
are used unchanged.

Same calling convention as scripts/harness_aider.py:

    python scripts/harness_mini.py <job_dir> <project_dir> --model <id>

  --model claude-haiku-5-5       Anthropic direct (litellm anthropic/<id>, prompt
                                 caching on, no temperature), ANTHROPIC_API_KEY,
                                 cost from exact tokens at ANTHROPIC_PRICES
  --model local/qwen3.5-9b       the local llama-server (AWOS_LOCAL_BASE_URL,
                                 default http://127.0.0.1:8080/v1), $0
  --model vendor/name            OpenRouter (OPENROUTER_API_KEY), litellm's price

cwd = the arm's state dir. The key comes from the environment or AWOS_JOB_DOTENV.
The agent runs non-interactively through mini-swe-agent's Python API
(DefaultAgent + LocalEnvironment, cwd = project) in its own venv
(~/.awos-harness/venvs/mini, override AWOS_MINI_VENV; AWOS_MINI_CMD, a
shell-split command prefix, replaces the launcher's interpreter, for tests).
Limits: step_limit = task.json max_turns, cost_limit = max_cost_usd (lowered to
AWOS_JOB_MAX_COST_USD), wall time = timeout_min minus a margin; the harness also
kills the launcher at the deadline. ./report.json:

    {"success": bool,    # the agent submitted (exit_status == "Submitted")
     "usage": {"cost_usd", "turns", "input_tokens", "output_tokens", "models", "llm_calls"},
     "harness": "mini", "exit_status": str, "invalid_reason": null | str, ...}

turns = model responses with content. INVALID (infrastructure, never "not
solved"; the log gets `[harness_mini] invalid_run reason=<r>`):
  empty_model_response  the run ended on responses with no text and no tool call
  provider_error        a model call still failed after mini's retries
  harness_error         the launcher raised / died / printed no final line
  no_model_response     not one model response with content
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

DEFAULT_VENV = Path.home() / ".awos-harness" / "venvs" / "mini"
TIMEOUT_MARGIN_S = 30
USAGE_TAG = "[mini-usage] "
FINAL_TAG = "[mini-final] "
STEP_TAG = "[mini-step] "
TAG = "[harness_mini] "
INVALID_TAG = TAG + "invalid_run reason="
DONE_TAG = TAG + "done "
LOCAL_PREFIX = "local/"
# Exit statuses that are the agent's own ending (fair results).
FAIR_EXITS = {"Submitted", "LimitsExceeded", "TimeExceeded", "RepeatedFormatError"}
PROVIDER_ERROR_HINTS = ("litellm", "openai", "anthropic", "httpx", "APIError", "RateLimit",
                        "Timeout", "ServiceUnavailable", "InternalServerError",
                        "BadRequestError", "APIConnectionError", "AuthenticationError")

# Runs inside the mini venv: argv[1] = config json path. Prints one usage line
# per model response and a final line; the agent's trajectory goes to cfg["traj"].
LAUNCHER = r'''
import json, sys, traceback
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
import os
os.environ.setdefault("MSWEA_COST_TRACKING", "ignore_errors")
import yaml, litellm
import minisweagent
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_model import LitellmModel
if cfg.get("register"):
    litellm.register_model(cfg["register"])
P = cfg.get("prices")
class Model(LitellmModel):
    calls = 0
    def _calculate_cost(self, response):
        u = getattr(response, "usage", None)
        g = lambda k: int(getattr(u, k, 0) or 0) if u else 0
        details = getattr(u, "prompt_tokens_details", None) if u else None
        read = g("cache_read_input_tokens") or int(getattr(details, "cached_tokens", 0) or 0)
        write = g("cache_creation_input_tokens")
        prompt, out = g("prompt_tokens"), g("completion_tokens")
        msg = response.choices[0].message
        empty = not (msg.content or "").strip() and not (msg.tool_calls or [])
        if P:
            cost = (max(0, prompt - read - write) * P["input"] + read * P["cache_read"]
                    + write * P["cache_write"] + out * P["output"]) / 1e6
        elif cfg.get("free"):
            cost = 0.0
        else:
            try:
                cost = float(litellm.cost_calculator.completion_cost(response, model=self.config.model_name))
            except Exception:
                cost = 0.0
        Model.calls += 1
        print("[mini-usage] " + json.dumps({"prompt_tokens": prompt, "completion_tokens": out,
              "cache_hit_tokens": read, "cache_write_tokens": write, "cost": cost, "empty": empty,
              "finish_reason": response.choices[0].finish_reason}), flush=True)
        return {"cost": cost}
import shlex, signal, subprocess
import minisweagent.environments.local as L
LIVE = set()
_orig_run = L._run
_Popen = subprocess.Popen
class _Tracked(_Popen):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        LIVE.add(self.pid)
def _run(command, cwd, env, timeout):
    # mini starts each command in its own session; remember it so a SIGTERM
    # from the harness (deadline) takes the command down with the launcher.
    L.subprocess.Popen = _Tracked
    try:
        return _orig_run(command, cwd, env, timeout)
    finally:
        L.subprocess.Popen = _Popen
        LIVE.clear()
L._run = _run
def _term(signum, frame):
    for pid in list(LIVE):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
    os._exit(143)
signal.signal(signal.SIGTERM, _term)
SB = cfg.get("sandbox")
class Env(LocalEnvironment):
    def execute(self, action, cwd="", *, timeout=None):
        cmd = action.get("command", "")
        print("[mini-step] $ " + cmd.replace("\n", " ")[:160], flush=True)
        if SB:
            action = {**action, "command": "sandbox-exec -f %s /bin/bash -c %s" % (shlex.quote(SB), shlex.quote(cmd))}
        out = super().execute(action, cwd, timeout=timeout)
        print("[mini-step] rc=%s chars=%d" % (out.get("returncode"), len(out.get("output") or "")), flush=True)
        return out
base = yaml.safe_load((Path(minisweagent.__file__).parent / "config" / "mini.yaml").read_text())
agent_cfg = {k: v for k, v in base["agent"].items() if k != "mode"}
agent_cfg.update(step_limit=cfg["step_limit"], cost_limit=cfg["cost_limit"],
                 wall_time_limit_seconds=cfg["wall_time_s"], output_path=cfg["traj"])
model_cfg = dict(base.get("model") or {})
model_cfg["model_kwargs"] = {**(model_cfg.get("model_kwargs") or {}), **cfg["model_kwargs"]}
model_cfg.update(model_name=cfg["model_name"], cost_tracking="ignore_errors")
if cfg.get("cache_control"):
    model_cfg["set_cache_control"] = cfg["cache_control"]
env_cfg = dict(base.get("environment") or {})
env_cfg.update(cwd=cfg["project"], timeout=cfg["cmd_timeout_s"])
agent = DefaultAgent(Model(**model_cfg), Env(**env_cfg), **agent_cfg)
final = {"version": minisweagent.__version__}
try:
    info = agent.run(cfg["task"])
    final.update(exit_status=info.get("exit_status", ""), calls=agent.n_calls, cost=agent.cost)
except BaseException as e:
    final.update(exit_status="Exception", error_type=type(e).__module__ + "." + type(e).__name__,
                 error=str(e)[:400], calls=agent.n_calls, cost=agent.cost)
    traceback.print_exc(limit=3)
print("[mini-final] " + json.dumps(final), flush=True)
'''


def _ha():
    spec = importlib.util.spec_from_file_location(
        "harness_aider_for_mini", Path(__file__).resolve().parent / "harness_aider.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ha = _ha()


class Tracker:
    """Parses the launcher's output lines."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.final: dict | None = None

    def feed(self, line: str) -> bool:
        line = line.strip()
        for tag, slot in ((USAGE_TAG, "u"), (FINAL_TAG, "f")):
            if line.startswith(tag):
                try:
                    rec = json.loads(line[len(tag):])
                except ValueError:
                    return False
                if slot == "u":
                    self.calls.append(rec)
                    return True
                self.final = rec
        return False

    @property
    def turns(self) -> int:
        return sum(1 for c in self.calls if not c.get("empty"))

    @property
    def empty_responses(self) -> int:
        return sum(1 for c in self.calls if c.get("empty"))

    @property
    def cost(self) -> float:
        return float(sum(float(c.get("cost") or 0) for c in self.calls))

    def ended_empty(self) -> bool:
        return bool(self.calls) and bool(self.calls[-1].get("empty"))

    def usage(self, model: str) -> dict:
        return {"cost_usd": round(self.cost, 6), "turns": self.turns,
                "llm_calls": len(self.calls),
                "input_tokens": sum(int(c.get("prompt_tokens") or 0) for c in self.calls),
                "output_tokens": sum(int(c.get("completion_tokens") or 0) for c in self.calls),
                "models": [model]}


def invalid_reason(tracker: Tracker, killed: str | None) -> str | None:
    """Why the run says nothing about the model, or None for a fair result."""
    final = tracker.final
    if final is None:
        # Killed at the job's deadline after real work is a fair (timed-out) result.
        return None if killed == "timeout" and tracker.turns else "harness_error"
    status = final.get("exit_status", "")
    if status == "Exception":
        blob = f"{final.get('error_type', '')} {final.get('error', '')}"
        return ("provider_error" if any(h in blob for h in PROVIDER_ERROR_HINTS)
                else "harness_error")
    if status == "RepeatedFormatError" and tracker.ended_empty():
        return "empty_model_response"
    if status not in FAIR_EXITS:
        return "harness_error"
    if tracker.turns == 0:
        return "empty_model_response" if tracker.empty_responses else "no_model_response"
    return None


def resolve(model: str, local_url: str) -> dict:
    """Provider, litellm id, key env, prices and model kwargs for --model."""
    if model.startswith(LOCAL_PREFIX):
        name = model[len(LOCAL_PREFIX):]
        return {"provider": "local", "model_name": "openai/" + name, "key_env": None,
                "prices": None, "free": True, "register": None, "cache_control": None,
                "model_kwargs": {"api_base": local_url, "api_key": os.environ.get(
                    "AWOS_LOCAL_API_KEY", "not-needed")}}
    provider = ha.resolve_provider(model)
    name = ha.aider_model_name(model, provider)
    if provider == "anthropic":
        # No temperature: Claude 5.x rejects it (aider fix 3e352cc); mini sends none.
        return {"provider": provider, "model_name": name, "key_env": ha.ANTHROPIC_KEY_ENV,
                "prices": ha.anthropic_prices(model), "free": False,
                "register": ha.anthropic_metadata(model), "cache_control": "default_end",
                "model_kwargs": {"max_tokens": ha.max_output_tokens()}}
    return {"provider": provider, "model_name": name, "key_env": ha.KEY_ENV, "prices": None,
            "free": False, "register": None, "cache_control": None,
            "model_kwargs": {"max_tokens": ha.max_output_tokens()}}


def sandbox_profile(project: Path, state: Path, job_dir: Path) -> str:
    """macOS sandbox-exec profile for the agent's shell commands: no reads of
    the benchmark data (hidden tests, references, other runs, any .awos dir,
    every .env), no writes under $HOME outside the project; the project is open.
    The last matching rule wins, so the project's allow overrides the denies."""
    home = Path.home().resolve()
    q = lambda p: json.dumps(str(Path(p).resolve()))
    return "\n".join([
        "(version 1)", "(allow default)",
        f"(deny file-read* (subpath {q(home / '.awos-harness')}) (subpath {q(job_dir)})"
        f" (subpath {q(job_dir.parent.parent)}) (subpath {q(state)})"
        r' (regex #"/\.awos(/|$)") (regex #"/\.env$"))',
        f"(deny file-write* (subpath {q(home)}))",
        f"(allow file-read* file-write* (subpath {q(project)}))", ""])


def use_sandbox() -> bool:
    return (os.environ.get("AWOS_MINI_SANDBOX", "1") != "0"
            and Path("/usr/bin/sandbox-exec").exists())


def launcher_prefix() -> list[str]:
    if os.environ.get("AWOS_MINI_CMD"):
        return shlex.split(os.environ["AWOS_MINI_CMD"])
    venv = Path(os.environ.get("AWOS_MINI_VENV") or DEFAULT_VENV)
    return [str(venv / "bin" / "python"), "-c", LAUNCHER]


def run_launcher(cmd: list[str], env: dict, timeout_s: float, tracker: Tracker,
                 out=None) -> tuple[int | None, str | None]:
    """Run cmd, stream its output into tracker; kill the group at timeout_s."""
    out = out or sys.stdout
    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace",
                            start_new_session=True)

    def pump() -> None:
        for line in proc.stdout:
            out.write(line)
            out.flush()
            tracker.feed(line)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        out.write(f"{TAG}hit the {timeout_s:.0f}s limit; stopping mini\n")
        for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, None)):
            # SIGTERM first: the launcher kills its running command (own session).
            try:
                os.killpg(proc.pid, sig)
            except OSError:
                proc.kill()
            try:
                proc.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
        reader.join(timeout=10)
        return None, "timeout"
    reader.join(timeout=10)
    return proc.returncode, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_dir")
    ap.add_argument("project_dir")
    ap.add_argument("--model", required=True)
    ap.add_argument("--cmd-timeout", type=int,
                    default=int(os.environ.get("AWOS_MINI_CMD_TIMEOUT_S", 120)),
                    help="seconds per bash command (mini default 30; tests need more)")
    args = ap.parse_args(argv)

    job_dir, project = Path(args.job_dir).resolve(), Path(args.project_dir).resolve()
    state = Path.cwd()
    spec = json.loads((job_dir / "task.json").read_text(encoding="utf-8"))
    max_cost = ha.job_cost_cap(float(spec.get("max_cost_usd", 1.0)))
    timeout_s = max(60.0, float(spec.get("timeout_min", 30)) * 60 - TIMEOUT_MARGIN_S)
    local_url = (os.environ.get("AWOS_LOCAL_BASE_URL", "").strip()
                 or "http://127.0.0.1:8080/v1").rstrip("/")
    r = resolve(args.model, local_url)
    dotenv = ha.read_env_file(Path(os.environ.get("AWOS_JOB_DOTENV") or state / ".env"))
    env = ha.child_env(os.environ, dotenv, r["key_env"] or "AWOS_LOCAL_API_KEY")
    # The agent's `python`/`pytest` are the interpreter AWOS and Aider test with.
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = env.get("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "5")
    env["MSWEA_CONFIGURED"] = "true"            # no first-run setup prompt
    env["MSWEA_SILENT_STARTUP"] = "1"
    env["LITELLM_LOG"] = env.get("LITELLM_LOG", "ERROR")
    traj = state / "mini.traj.json"
    sandbox = None
    if use_sandbox():
        sandbox = state / "mini.sb"
        sandbox.write_text(sandbox_profile(project, state, job_dir), encoding="utf-8")
    # mini's SWE-bench configs name the repo's location (/testbed); so do we.
    task = f"{spec['goal']}\n\nThe repository is your current working directory ({project})."
    cfg = {"task": task, "sandbox": str(sandbox) if sandbox else None, "project": str(project), "model_name": r["model_name"],
           "model_kwargs": {**r["model_kwargs"], "timeout": ha.model_timeout_s()},
           "prices": r["prices"], "free": r["free"], "register": r["register"],
           "cache_control": r["cache_control"], "step_limit": int(spec.get("max_turns", 150)),
           "cost_limit": max_cost, "wall_time_s": int(max(30, timeout_s - TIMEOUT_MARGIN_S)),
           "cmd_timeout_s": args.cmd_timeout, "traj": str(traj)}
    cfg_path = state / "mini.config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")   # holds no key
    key_ok = r["key_env"] is None or bool(env.get(r["key_env"]))
    print(f"{TAG}mini model={r['model_name']} provider={r['provider']} "
          f"key={'set' if key_ok else 'MISSING'} sandbox={'on' if sandbox else 'off'} step_limit={cfg['step_limit']} "
          f"cost_cap=${max_cost} timeout={timeout_s:.0f}s project={project}", flush=True)
    started = time.monotonic()
    tracker = Tracker()
    code, killed = run_launcher(launcher_prefix() + [str(cfg_path)], env, timeout_s, tracker)
    minutes = round((time.monotonic() - started) / 60, 2)
    final = tracker.final or {}
    bad = invalid_reason(tracker, killed)
    if bad:
        print(f"{INVALID_TAG}{bad} exit_status={final.get('exit_status')} "
              f"error_type={final.get('error_type')} turns={tracker.turns} "
              f"empty_responses={tracker.empty_responses}", flush=True)
    report = {
        "success": bool(final.get("exit_status") == "Submitted" and not bad),
        "usage": tracker.usage(args.model),
        "harness": "mini", "mini_version": final.get("version"),
        "provider": r["provider"], "model": r["model_name"], "prices_per_mtok": r["prices"],
        "exit_status": final.get("exit_status"), "error_type": final.get("error_type"),
        "error": final.get("error"), "killed": killed, "exit_code": code,
        "cost_source": ("free_local" if r["free"] else "token_price_table" if r["prices"]
                        else "litellm_price_table"),
        "cost_is_lower_bound": bool(killed),
        "cache_hit_tokens": sum(int(c.get("cache_hit_tokens") or 0) for c in tracker.calls),
        "cache_write_tokens": sum(int(c.get("cache_write_tokens") or 0) for c in tracker.calls),
        "empty_responses": tracker.empty_responses, "invalid_reason": bad,
        "step_limit": cfg["step_limit"], "cost_limit": max_cost, "minutes": minutes,
        "attempts": 1, "retry_cost_usd": 0.0,
    }
    (state / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{DONE_TAG}exit_status={final.get('exit_status')} killed={killed} "
          f"turns={tracker.turns} cost=${tracker.cost:.4f} {minutes} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

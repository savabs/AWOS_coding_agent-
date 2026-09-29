#!/usr/bin/env python3
"""
harness_aider.py — run Aider (a market coding harness) on one job-series job.

Same calling convention as `scripts/job_series.py _child`:

    python scripts/harness_aider.py <job_dir> <project_dir> --model <openrouter id>

cwd must be the arm's state dir (it holds a copy of .env with OPENROUTER_API_KEY).
Reads <job_dir>/task.json, runs Aider once, non-interactively (`--message`), in
<project_dir> on the goal, streams Aider's output to stdout, and writes
./report.json:

    {"success": bool,     # aider exited 0, was not killed, got >= 1 model response
     "usage": {"cost_usd", "turns", "input_tokens", "output_tokens", "models"},
     "harness": "aider", "aider_version": str,
     "killed": null | "timeout" | "cost", "exit_code": int | null,
     "cost_source": ..., "aider_test_outcome": bool | null, ...}

`turns` = model responses. Aider gets no conventions file, no chat history and
no memory of earlier jobs: it is the no-memory baseline. Its history files go to
the state dir; its repo-map cache is removed from the project after the run.

Model setup: Aider 0.86 ships no settings for DeepSeek V4 / Claude Sonnet 5.x
and would fall back to the "whole" edit format; we hand it the settings it ships
for each model's nearest sibling (see model_settings) and the OpenRouter price
(model_metadata), with the weak/editor model pinned to the same model.

Aider lives in its own venv (default ~/.awos-harness/aider-venv; override with
AWOS_AIDER_VENV). AWOS_AIDER_CMD (a shell-split command prefix) replaces the
launcher entirely; tests use it to substitute a fake aider.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

DEFAULT_VENV = Path.home() / ".awos-harness" / "aider-venv"
# Seconds kept back from the job's timeout_min, so report.json is written
# before the runner's own wall-clock kill.
TIMEOUT_MARGIN_S = 30
KEY_ENV = "OPENROUTER_API_KEY"
ENV_FILE_NAME = "aider.env"          # written per run in the state dir, removed after
USAGE_TAG = "[aider-usage] "
FINAL_TAG = "[aider-final] "

# Runs Aider's own CLI entry point with two additions: Coder.max_reflections
# (automatic follow-up rounds per message: file adds, lint and test fixes; stock
# Aider is 3 and has no flag for it) and one exact usage line per model response
# (Aider's own "Tokens: ... Cost: ..." line is rounded to 1k tokens / 1 cent).
LAUNCHER = r'''
import json, sys
n = int(sys.argv.pop(1))
from aider.coders import base_coder
C = base_coder.Coder
C.max_reflections = n
_calc = C.calculate_and_show_tokens_and_cost
def calc(self, messages, completion=None):
    before = self.total_cost
    out = _calc(self, messages, completion)
    u = getattr(completion, "usage", None)
    hit = (getattr(u, "prompt_cache_hit_tokens", 0) or getattr(u, "cache_read_input_tokens", 0) or 0) if u else 0
    or_cost = getattr(u, "cost", None) if u else None
    rec = {
        "prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0) if u else None,
        "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0) if u else None,
        "cache_hit_tokens": int(hit),
        "cost": float(self.total_cost - before),
        "session_cost": float(self.total_cost),
        "openrouter_cost": float(or_cost) if isinstance(or_cost, (int, float)) else None,
    }
    print("[aider-usage] " + json.dumps(rec), flush=True)
    return out
C.calculate_and_show_tokens_and_cost = calc
_run = C.run
def run(self, *a, **k):
    try:
        return _run(self, *a, **k)
    finally:
        print("[aider-final] " + json.dumps({
            "test_outcome": self.test_outcome, "lint_outcome": self.lint_outcome,
            "reflections": self.num_reflections, "edit_format": self.edit_format,
            "edited": sorted(self.aider_edited_files or []),
            "in_chat": sorted(self.get_inchat_relative_files()),
        }), flush=True)
C.run = run
from aider.main import main
sys.exit(main())
'''

# ── usage parsing ─────────────────────────────────────────────────────────────

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TOKENS = re.compile(r"Tokens:\s*(?P<body>.*?received)")
_TOKEN_PART = re.compile(r"(?P<num>[\d.,]+)\s*(?P<unit>[kKmM]?)\s+(?P<what>sent|received|cache hit|cache write)")
_API_ERROR = re.compile(r"litellm\.\w+Error\b.*")
_COST = re.compile(r"Cost:\s*\$(?P<msg>[\d.,]+)\s*message,\s*\$(?P<sess>[\d.,]+)\s*session")


def _num(text: str, unit: str = "") -> int:
    value = float(text.replace(",", ""))
    return int(round(value * {"k": 1e3, "m": 1e6}.get(unit.lower(), 1)))


class UsageTracker:
    """Accumulates Aider's per-response usage.

    Two sources: the launcher's exact `[aider-usage] {json}` lines, and Aider's
    own human line (rounded), printed after each model response:
      Tokens: 2.3k sent, 1.1k cache hit, 150 received. Cost: $0.0012 message, $0.0034 session.
    (the Cost part may be on the next line, or absent when the price is unknown).
    Exact lines win when present; the human lines are the fallback.
    """

    def __init__(self) -> None:
        self.exact: list[dict] = []
        self.text_turns = 0
        self.text_in = 0
        self.text_out = 0
        self.text_hit = 0
        self.text_session_cost: float | None = None
        self.final: dict = {}
        self.api_error: str | None = None   # first provider error Aider printed

    def feed(self, line: str) -> bool:
        """Parse one output line; True if it carried usage."""
        line = _ANSI.sub("", line).strip()
        if line.startswith(USAGE_TAG):
            try:
                self.exact.append(json.loads(line[len(USAGE_TAG):]))
                return True
            except ValueError:
                return False
        if line.startswith(FINAL_TAG):
            try:
                self.final = json.loads(line[len(FINAL_TAG):])
            except ValueError:
                pass
            return False
        if self.api_error is None:
            e = _API_ERROR.search(line)
            if e:
                self.api_error = e.group(0)[:200]
        hit = False
        m = _TOKENS.search(line)
        if m:
            parts = list(_TOKEN_PART.finditer(m.group("body")))
            if parts:
                hit = True
                self.text_turns += 1
                for p in parts:
                    n = _num(p.group("num"), p.group("unit"))
                    what = p.group("what")
                    if what == "sent":
                        self.text_in += n
                    elif what == "received":
                        self.text_out += n
                    elif what == "cache hit":
                        self.text_hit += n
        c = _COST.search(line)
        if c:
            self.text_session_cost = float(c.group("sess").replace(",", ""))
            hit = True
        return hit

    @property
    def turns(self) -> int:
        return len(self.exact) if self.exact else self.text_turns

    @property
    def input_tokens(self) -> int:
        if self.exact:
            return sum(int(r.get("prompt_tokens") or 0) for r in self.exact)
        return self.text_in

    @property
    def output_tokens(self) -> int:
        if self.exact:
            return sum(int(r.get("completion_tokens") or 0) for r in self.exact)
        return self.text_out

    @property
    def cache_hit_tokens(self) -> int:
        if self.exact:
            return sum(int(r.get("cache_hit_tokens") or 0) for r in self.exact)
        return self.text_hit

    def cost(self) -> tuple[float, str]:
        """(cost_usd, source). OpenRouter's billed cost if it came back on every
        response, else Aider's own computation from the price table."""
        if self.exact:
            billed = [r.get("openrouter_cost") for r in self.exact]
            if all(isinstance(b, (int, float)) for b in billed):
                return float(sum(billed)), "openrouter_billed"
            return float(self.exact[-1].get("session_cost") or 0.0), "aider_price_table"
        if self.text_session_cost is not None:
            return self.text_session_cost, "aider_text_rounded"
        return 0.0, "unknown"

    def usage(self, model: str) -> dict:
        return {
            "cost_usd": round(self.cost()[0], 6),
            "turns": self.turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "models": [model],
        }


# ── setup ─────────────────────────────────────────────────────────────────────


def read_env_file(path: Path) -> dict:
    """KEY=VALUE lines (optionally `export`, quoted). Values are never printed."""
    out: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


_PROVIDER_VAR = re.compile(r"_(API_KEY|API_BASE|BASE_URL|API_VERSION|API_TYPE)$|^OR_API_KEY$")


def child_env(parent: dict, dotenv: dict) -> dict:
    """Aider's environment: the parent's, minus Aider's own switches (AIDER_*) and
    every other provider's key/base URL (litellm and Aider pick those up, e.g. an
    OPENAI_BASE_URL pointing at OpenRouter with another key), plus the OpenRouter
    key (the environment's if set, else the state dir's .env, as job_series does)."""
    env = {k: v for k, v in parent.items()
           if not k.startswith("AIDER_") and (k == KEY_ENV or not _PROVIDER_VAR.search(k))}
    if not env.get(KEY_ENV) and dotenv.get(KEY_ENV):
        env[KEY_ENV] = dotenv[KEY_ENV]
    env["PYTHONUNBUFFERED"] = "1"
    return env


def model_timeout_s() -> float:
    """Per-request model timeout: AWOS's own (scaffold/agent/providers.py:
    AWOS_MODEL_TIMEOUT_S, default 120 s), so a hung call costs both harnesses
    the same. Aider retries a timed-out call with backoff."""
    try:
        value = float(os.environ.get("AWOS_MODEL_TIMEOUT_S", 120))
    except ValueError:
        return 120.0
    return value if value > 0 else 120.0


def write_key_file(state: Path, key: str | None) -> Path:
    """The --env-file Aider loads last: only the OpenRouter key, owner-only."""
    path = state / ENV_FILE_NAME
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        if key:
            f.write(f"{KEY_ENV}={key}\n")
    return path


def model_metadata(model: str, models_list: list[dict]) -> dict | None:
    """litellm-style metadata for openrouter/<model> from OpenRouter's model list,
    so Aider knows the price and context window."""
    entry = next((m for m in models_list if m.get("id") == model), None)
    if not entry:
        return None
    p = entry.get("pricing", {})
    top = entry.get("top_provider") or {}
    max_out = int(top.get("max_completion_tokens") or 32768)
    meta = {
        "max_input_tokens": int(entry.get("context_length") or 128000),
        "max_tokens": max_out,
        "max_output_tokens": max_out,
        "input_cost_per_token": float(p.get("prompt") or 0),
        "output_cost_per_token": float(p.get("completion") or 0),
        "litellm_provider": "openrouter",
        "mode": "chat",
    }
    if p.get("input_cache_read"):
        meta["cache_read_input_token_cost"] = float(p["input_cache_read"])
    return {f"openrouter/{model}": meta}


def fetch_openrouter_models(timeout: float = 20.0) -> list[dict]:
    try:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=timeout) as r:
            return json.load(r).get("data", [])
    except Exception as exc:  # noqa: BLE001 — offline: Aider just won't know the price
        print(f"[harness_aider] could not fetch OpenRouter's model list: {exc}", flush=True)
        return []


def model_settings(model: str) -> list[dict]:
    """Aider model settings for openrouter/<model>, copied from what Aider 0.86
    ships for the nearest sibling it knows; weak and editor model = the same model,
    so every call goes to the model under test.

    max_tokens per response = AWOS's agent-loop cap (AWOS_AGENT_MAX_OUTPUT_TOKENS,
    default 16384) instead of the sibling's 64k: in a live run a non-streamed
    DeepSeek V4 Flash reply ran 10+ minutes with the 64k cap, and OpenRouter's
    keep-alives mean the per-request timeout never fires on it."""
    name = f"openrouter/{model}"
    s: dict = {"name": name, "edit_format": "diff", "use_repo_map": True,
               "weak_model_name": name, "editor_model_name": name,
               "editor_edit_format": "editor-diff",
               "extra_params": {"max_tokens": max_output_tokens()}}
    if "deepseek" in model:        # as openrouter/deepseek/deepseek-chat-v3-0324
        s.update(reminder="sys", examples_as_sys_msg=True)
    elif "claude" in model:        # as openrouter/anthropic/claude-sonnet-4.5
        s.update(examples_as_sys_msg=False, cache_control=True,
                 accepts_settings=["thinking_tokens"])
    return [s]


def max_output_tokens() -> int:
    try:
        value = int(os.environ.get("AWOS_AGENT_MAX_OUTPUT_TOKENS", 16384))
    except ValueError:
        return 16384
    return value if value > 0 else 16384


def _yaml(settings: list[dict]) -> str:
    """JSON is valid YAML; Aider reads its settings file with yaml.safe_load."""
    return json.dumps(settings, indent=2)


def aider_prefix(max_reflections: int) -> list[str]:
    override = os.environ.get("AWOS_AIDER_CMD")
    if override:
        return shlex.split(override)
    venv = Path(os.environ.get("AWOS_AIDER_VENV", DEFAULT_VENV))
    return [str(venv / "bin" / "python"), "-c", LAUNCHER, str(max_reflections)]


def aider_version(prefix: list[str]) -> str:
    try:
        proc = subprocess.run([*prefix, "--version"], capture_output=True, text=True, timeout=120)
        m = re.search(r"\d+\.\d+[\w.+-]*", proc.stdout)
        return m.group(0) if m else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def build_command(prefix: list[str], model: str, goal: str, state: Path, test_cmd: str,
                  settings_file: Path | None = None, metadata_file: Path | None = None,
                  edit_format: str | None = None, map_tokens: int | None = None,
                  files: list[str] | None = None, request_timeout: float | None = None) -> list[str]:
    """Aider argv. The API key is never in it (it goes through the environment)."""
    cmd = [
        *prefix,
        "--model", f"openrouter/{model}",
        "--message", goal,
        "--yes-always",                 # accept "add file to chat?", "fix test errors?"
        "--no-auto-commits",            # the runner judges the working tree
        "--no-dirty-commits",
        "--test-cmd", test_cmd,
        "--auto-test",
        "--no-gitignore",               # don't add .aider* to the project's .gitignore
        "--no-show-model-warnings",
        "--no-check-update",
        "--no-show-release-notes",
        "--no-analytics",
        "--no-pretty",
        "--no-stream",                  # exact usage per response
        "--no-fancy-input",
        "--no-suggest-shell-commands",  # nobody is there to run them
        "--no-detect-urls",
        "--no-restore-chat-history",
        "--chat-history-file", str(state / "aider.chat.history.md"),
        "--input-history-file", str(state / "aider.input.history"),
        "--llm-history-file", str(state / "aider.llm.history"),
        # Aider loads ~/.env, the repo's .env, ./.env, then this file, each
        # overriding the last; ours holds only the arm's key so it wins over a
        # stale ~/.env. The key is never in argv.
        "--env-file", str(state / ENV_FILE_NAME),
        "--encoding", "utf-8",
    ]
    if settings_file is not None:
        cmd += ["--model-settings-file", str(settings_file)]
    if metadata_file is not None:
        cmd += ["--model-metadata-file", str(metadata_file)]
    if edit_format:
        cmd += ["--edit-format", edit_format]
    if request_timeout:
        cmd += ["--timeout", f"{request_timeout:g}"]
    if map_tokens is not None:
        cmd += ["--map-tokens", str(map_tokens)]
    return cmd + list(files or [])


def source_files(project: Path) -> list[str]:
    """Tracked non-test .py files: what a user would /add for a small project."""
    try:
        out = subprocess.run(["git", "-C", str(project), "ls-files", "*.py"],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [f for f in out.splitlines()
            if f and not f.startswith("tests/") and not Path(f).name.startswith("test_")]


# ── run ───────────────────────────────────────────────────────────────────────


def run_aider(cmd: list[str], cwd: Path, env: dict, timeout_s: float, max_cost: float,
              tracker: UsageTracker, out=None) -> tuple[int | None, str | None]:
    """Run cmd, stream its output, kill on timeout or once the session cost passes
    max_cost. Returns (exit code, or None if killed; the kill reason, or None)."""
    out = out or sys.stdout
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            bufsize=1, errors="replace", start_new_session=True)
    killed: list[str] = []
    lock = threading.Lock()

    def kill(reason: str) -> None:
        with lock:
            if killed:
                return
            killed.append(reason)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            out.write(line)
            out.flush()
            if tracker.feed(line) and tracker.cost()[0] > max_cost and not killed:
                out.write(f"[harness_aider] session cost ${tracker.cost()[0]:.4f} > cap "
                          f"${max_cost:.2f}; stopping aider\n")
                out.flush()
                kill("cost")

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        out.write(f"[harness_aider] hit the {timeout_s:.0f}s limit; stopping aider\n")
        kill("timeout")
        proc.wait()
    reader.join(timeout=10)
    if killed:
        return None, killed[0]
    return proc.returncode, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_dir")
    ap.add_argument("project_dir")
    ap.add_argument("--model", required=True, help="OpenRouter model id, e.g. deepseek/deepseek-v4-flash")
    ap.add_argument("--max-reflections", type=int, default=3,
                    help="automatic follow-up rounds per message (stock Aider: 3)")
    ap.add_argument("--add-files", choices=["none", "src"], default="none",
                    help="none (default): Aider picks files from its repo map and adds them "
                         "itself; src: pre-add every tracked non-test .py file")
    ap.add_argument("--edit-format", default=None, help="override the edit format (default diff)")
    ap.add_argument("--map-tokens", type=int, default=None, help="repo map budget (Aider default: by context size)")
    ap.add_argument("--request-timeout", type=float, default=None,
                    help="per model call, seconds (default: AWOS_MODEL_TIMEOUT_S or 120, as AWOS)")
    ap.add_argument("--test-cmd", default=None,
                    help="default: '<this python> -m pytest -q -p no:cacheprovider'")
    ap.add_argument("--no-price-lookup", action="store_true",
                    help="don't fetch OpenRouter's model list (Aider then prints no cost)")
    args = ap.parse_args(argv)

    job_dir, project = Path(args.job_dir).resolve(), Path(args.project_dir).resolve()
    state = Path.cwd()
    spec = json.loads((job_dir / "task.json").read_text(encoding="utf-8"))
    goal = spec["goal"]
    max_cost = float(spec.get("max_cost_usd", 1.0))
    timeout_s = max(60.0, float(spec.get("timeout_min", 30)) * 60 - TIMEOUT_MARGIN_S)

    env = child_env(os.environ, read_env_file(state / ".env"))

    prefix = aider_prefix(args.max_reflections)
    version = aider_version(prefix)
    settings_file = state / "aider.model.settings.yml"
    settings_file.write_text(_yaml(model_settings(args.model)), encoding="utf-8")
    metadata_file = None
    meta = None if args.no_price_lookup else model_metadata(args.model, fetch_openrouter_models())
    if meta:
        metadata_file = state / "aider.model.metadata.json"
        metadata_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    test_cmd = args.test_cmd or f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider"
    files = source_files(project) if args.add_files == "src" else []
    request_timeout = args.request_timeout or model_timeout_s()
    cmd = build_command(prefix, args.model, goal, state, test_cmd, settings_file, metadata_file,
                        args.edit_format, args.map_tokens, files, request_timeout)

    print(f"[harness_aider] aider {version} model=openrouter/{args.model} "
          f"key={'set' if env.get(KEY_ENV) else 'MISSING'} price={'known' if meta else 'unknown'} "
          f"max_reflections={args.max_reflections} add_files={args.add_files}({len(files)}) "
          f"cost_cap=${max_cost} timeout={timeout_s:.0f}s project={project}", flush=True)
    tracker = UsageTracker()
    started = time.monotonic()
    env_file = write_key_file(state, env.get(KEY_ENV))
    try:
        code, killed = run_aider(cmd, project, env, timeout_s, max_cost, tracker)
    finally:
        env_file.unlink(missing_ok=True)
        # Aider's repo-map cache: not the project's work, and it would count as a
        # changed file in the runner's metrics.
        shutil.rmtree(project / ".aider.tags.cache.v4", ignore_errors=True)
    minutes = round((time.monotonic() - started) / 60, 2)
    cost, cost_source = tracker.cost()

    report = {
        # Aider exits 0 even when every model call failed; no response = no run.
        "success": bool(code == 0 and not killed and tracker.turns > 0),
        "api_error": tracker.api_error,
        "usage": tracker.usage(args.model),
        "harness": "aider",
        "aider_version": version,
        "killed": killed,
        "exit_code": code,
        "cost_source": cost_source,
        # A killed run's in-flight model call is billed by OpenRouter but never
        # reported to us, so cost_usd is a lower bound then.
        "cost_is_lower_bound": bool(killed or (code is not None and code < 0)),
        "cache_hit_tokens": tracker.cache_hit_tokens,
        "aider_test_outcome": tracker.final.get("test_outcome"),
        "aider_reflections": tracker.final.get("reflections"),
        "edit_format": tracker.final.get("edit_format"),
        "files_edited": tracker.final.get("edited"),
        "files_in_chat": tracker.final.get("in_chat"),
        "max_reflections": args.max_reflections,
        "request_timeout_s": request_timeout,
        "add_files": args.add_files,
        "minutes": minutes,
    }
    (state / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[harness_aider] done exit={code} killed={killed} turns={tracker.turns} "
          f"cost=${cost:.4f} ({cost_source}) tokens={tracker.input_tokens}/{tracker.output_tokens} "
          f"aider_tests={tracker.final.get('test_outcome')} {minutes} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
AWOS as a Harbor installed agent (harbor-framework/harbor 0.24.0).

    PYTHONPATH=. harbor run -d terminal-bench/terminal-bench-2-1@6 \\
        -a harbor_agents.awos_agent:AwosAgent -m anthropic/claude-haiku-5-5 \\
        --ak git_ref=HEAD --ak cost_cap=1.0

install(): `git archive <git_ref>` of this repo's scaffold/agent package (the
AWOS runtime; nothing else is needed for the loop) is unpacked on the host and
uploaded to /installed-agent/awos; a private Python 3.12 venv (uv) gets the two
SDKs AWOS's loop imports, pinned to the versions of ci/ladder-requirements.txt.
The task image's own Python is never touched.

run(): scaffold/agent/headless_loop.py runs AWOS's `loop` executor on the task
instruction, in the container's working directory, with AWOS_SANDBOX=container
(the task container is the sandbox). No git, no branch, no test gate. Per-call
usage goes to <agent logs>/awos_calls.jsonl as it happens, so a run Harbor
kills at the task timeout still reports its tokens; populate_context_post_run()
sums it into Harbor's AgentContext with harbor_agents/pricing.py.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from pydantic import Field

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.agents.options import InstalledAgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage

try:
    from harbor_agents import pricing
except ImportError:  # pragma: no cover - loaded by file path
    import pricing  # type: ignore

REPO = Path(__file__).resolve().parent.parent
#: What `git archive` packs: the AWOS runtime package only.
SOURCE_PATHS = ("scaffold/agent",)  # scaffold/ itself is a namespace package
REMOTE_SRC = "/installed-agent/awos"
VENV = "$HOME/.awos-venv"
PYTHON_VERSION = "3.12"
#: The SDK versions of the CI lock (ci/ladder-requirements.txt).
PIP_PINS = ("anthropic==0.125.0", "openai==3.3.1")
GOAL_ENV = "HARBOR_AWOS_GOAL"
CALLS_FILE = "awos_calls.jsonl"
RESULT_FILE = "awos_result.json"


class AwosAgentOptions(InstalledAgentOptions):
    git_ref: str = Field(default="HEAD", description="Git ref of this repo to install.")
    cost_cap: float | None = Field(
        default=None, description="Per-task USD cap (AWOS's own estimate); unset = none.")
    max_steps: int = Field(
        default=1000,
        description=("Model calls per task. The product default is 60; the benchmark "
                     "default is effectively unlimited so the task timeout binds, as "
                     "for claude-code and mini-swe-agent (no step limit)."))
    cmd_timeout_s: int | None = Field(
        default=None, description="Per bash command timeout (AWOS default 120, max 600).")


def resolve_ref(ref: str, repo: Path = REPO) -> str:
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def export_source(ref: str, dest: Path, repo: Path = REPO) -> str:
    """Unpack `git archive <ref>` of SOURCE_PATHS into dest; returns the commit sha."""
    sha = resolve_ref(ref, repo)
    with tempfile.TemporaryDirectory(prefix="awos-archive-") as tmp:
        tar_path = Path(tmp) / "awos.tar"
        subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", "-o", str(tar_path),
                        sha, "--", *SOURCE_PATHS], check=True, capture_output=True)
        with tarfile.open(tar_path) as tf:
            tf.extractall(dest, filter="data")
    (dest / "AWOS_GIT_SHA").write_text(sha + "\n", encoding="utf-8")
    return sha


def usage_from_calls(lines: list[dict[str, Any]], model: str) -> dict[str, Any]:
    """Harbor AgentContext numbers from awos_calls.jsonl rows (input = whole prompt)."""
    n_in = sum(int(r.get("input_tokens") or 0) for r in lines)
    n_cache = sum(int(r.get("cached_tokens") or 0) for r in lines)
    n_write = sum(int(r.get("cache_write_tokens") or 0) for r in lines)
    n_out = sum(int(r.get("output_tokens") or 0) for r in lines)
    cost = pricing.total_cost(model, ({"prompt": r.get("input_tokens", 0),
                                       "output": r.get("output_tokens", 0),
                                       "cache_read": r.get("cached_tokens", 0),
                                       "cache_write_5m": r.get("cache_write_tokens", 0)}
                                      for r in lines))
    return {"n_input_tokens": n_in, "n_cache_tokens": n_cache, "n_output_tokens": n_out,
            "n_cache_write_tokens": n_write, "cost_usd": cost, "n_calls": len(lines),
            "cost_usd_awos": round(sum(float(r.get("cost_usd_awos") or 0) for r in lines), 8)}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a line cut by the kill at the task timeout
    return rows


class AwosAgent(BaseInstalledAgent):
    options_model = AwosAgentOptions
    options: AwosAgentOptions

    @staticmethod
    def name() -> str:
        return "awos"

    def _model(self) -> str:
        return pricing.normalize_model(self.model_name) or "claude-haiku-5-5"

    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(environment, ("curl", "bash", "ca_certificates"))
        with tempfile.TemporaryDirectory(prefix="awos-src-") as tmp:
            sha = export_source(self.options.git_ref, Path(tmp))
            await self.exec_as_root(environment, command=f"mkdir -p {REMOTE_SRC}")
            await environment.upload_dir(tmp, REMOTE_SRC)
        self._version = sha[:12]
        await self.exec_as_root(environment, command=f"chmod -R a+rX {REMOTE_SRC}")
        pins = " ".join(shlex.quote(p) for p in PIP_PINS)
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "if ! command -v uv >/dev/null 2>&1; then"
                "  curl -LsSf https://astral.sh/uv/install.sh | sh;"
                " fi && "
                'if [ -f "$HOME/.local/bin/env" ]; then . "$HOME/.local/bin/env"; fi && '
                'export PATH="$HOME/.local/bin:$PATH" && '
                f"uv python install {PYTHON_VERSION} && "
                f'uv venv --allow-existing --python {PYTHON_VERSION} "{VENV}" && '
                f'uv pip install --python "{VENV}/bin/python" {pins} && '
                f'PYTHONPATH={REMOTE_SRC} "{VENV}/bin/python" -P -c '
                '"import scaffold.agent.headless_loop"'
            ),
        )

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment,
                  context: AgentContext) -> None:
        api_key = self._get_env("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        logs = self.environment_logs_dir.as_posix()
        env = {
            "ANTHROPIC_API_KEY": api_key,
            "AWOS_PROVIDER": "anthropic",
            "AWOS_SANDBOX": "container",
            "AWOS_GIT_SHA": self._version or "",
            "AWOS_LLM_CALL_LOG": f"{logs}/llm_calls.jsonl",
            GOAL_ENV: instruction,
        }
        if self.options.cmd_timeout_s:
            env["AWOS_LOOP_CMD_TIMEOUT_S"] = str(self.options.cmd_timeout_s)
        args = ["--goal-env", GOAL_ENV, "--out-dir", logs, "--model", self._model(),
                "--max-steps", str(self.options.max_steps)]
        if self.options.cost_cap is not None:
            args += ["--cost-cap", str(self.options.cost_cap)]
        await self.exec_as_agent(
            environment,
            command=(
                f'mkdir -p {shlex.quote(logs)} && PYTHONPATH={REMOTE_SRC} "{VENV}/bin/python" -P '
                f'-m scaffold.agent.headless_loop --workspace "$PWD" '
                f"{' '.join(shlex.quote(a) for a in args)} "
                f"2>&1 | tee {shlex.quote(logs + '/awos.txt')}"
            ),
            env=env,
        )

    def populate_context_post_run(self, context: AgentContext) -> None:
        rows = read_jsonl(self.logs_dir / CALLS_FILE)
        if not rows:
            return
        u = usage_from_calls(rows, self._model())
        context.n_input_tokens = u["n_input_tokens"]
        context.n_cache_tokens = u["n_cache_tokens"]
        context.n_output_tokens = u["n_output_tokens"]
        context.cost_usd = u["cost_usd"]
        context.model_usage = {self._model(): ModelUsage(
            n_input_tokens=u["n_input_tokens"], n_cache_tokens=u["n_cache_tokens"],
            n_output_tokens=u["n_output_tokens"], cost_usd=u["cost_usd"])}
        meta: dict[str, Any] = {"awos_git_sha": self._version,
                                "n_cache_write_tokens": u["n_cache_write_tokens"],
                                "n_calls": u["n_calls"], "cost_usd_awos": u["cost_usd_awos"],
                                "cost_source": "harbor_agents.pricing (per request)"}
        result = self.logs_dir / RESULT_FILE
        if result.is_file():
            try:
                r = json.loads(result.read_text(encoding="utf-8"))
                meta.update({k: r.get(k) for k in ("stop_reason", "steps", "tool_calls",
                                                   "elapsed_sec", "sandbox")})
            except (OSError, json.JSONDecodeError):
                pass
        else:
            meta["stop_reason"] = "killed_or_unfinished"
        context.metadata = {**(context.metadata or {}), **meta}

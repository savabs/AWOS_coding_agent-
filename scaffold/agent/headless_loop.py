"""
headless_loop.py — AWOS's loop executor with no host, no git and no test gate.

The most direct way to run AWOS on an arbitrary terminal task: the goal goes
verbatim to loop_executor.LoopExecutor (one bash tool, the model works until it
submits), commands run through sandbox.make_sandbox in `workspace`. Used by the
Harbor adapter (harbor_agents/awos_agent.py) inside Terminal-Bench task
containers, where AWOS_SANDBOX=container declares the container to be the
sandbox. There is no orchestrator here: no planner, no git branch or handoff,
no success gate (a task container has no visible test suite; the benchmark's
verifier runs after the agent), no goal budget ledger. The loop ends on a
submit, a limit (steps, cost cap, wall clock) or a model error.

Outputs (in --out-dir), written as the run goes so a killed run still leaves
its usage behind:
  awos_calls.jsonl   one line per model call: tokens (input = whole prompt,
                     cached / cache_write are subsets), AWOS's cost estimate
  awos_result.json   the run summary (stop reason, steps, totals), at the end
  llm_calls.jsonl    AWOS's own per-call log (llm_call_log), unless
                     AWOS_LLM_CALL_LOG is already set

    AWOS_SANDBOX=container python -P -m scaffold.agent.headless_loop \\
        --goal-file /tmp/instruction.md --workspace "$PWD" --out-dir /logs/agent \\
        --model claude-haiku-5-5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

try:
    from . import loop_executor as lx
    from .agent_loop import _finish_reason, build_client_from_env
    from .sandbox import make_sandbox
except ImportError:  # flat sys.path import
    import loop_executor as lx  # type: ignore
    from agent_loop import _finish_reason, build_client_from_env  # type: ignore
    from sandbox import make_sandbox  # type: ignore

RESULT_FILE = "awos_result.json"
CALLS_FILE = "awos_calls.jsonl"


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()


def run_headless(goal: str, workspace: str, out_dir: str, model: Optional[str] = None, *,
                 cost_cap: Optional[float] = None, wall_s: Optional[float] = None,
                 max_steps: Optional[int] = None, client: Any = None,
                 sandbox: Any = None) -> dict:
    """Run one goal to a submit or a limit; returns (and writes) the summary."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("AWOS_LLM_CALL_LOG", str(out / "llm_calls.jsonl"))
    calls_path = out / CALLS_FILE
    summary: dict[str, Any] = {"model": model, "workspace": workspace,
                               "awos_git_sha": os.environ.get("AWOS_GIT_SHA", ""),
                               "sandbox": None, "stop_reason": "setup_error"}

    def _finish(extra: dict) -> dict:
        summary.update(extra)
        (out / RESULT_FILE).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary

    if sandbox is None:
        sandbox = make_sandbox(workspace)
    if sandbox is None:
        return _finish({"error": "no sandbox (set AWOS_SANDBOX: seatbelt, docker, or "
                                 "container inside a disposable task container)"})
    summary["sandbox"] = getattr(sandbox, "backend", None)
    if client is None:
        try:
            client = build_client_from_env(model)
        except Exception as exc:  # noqa: BLE001
            return _finish({"error": f"no model client: {exc}"})
    summary["model"] = str(getattr(client, "model", "") or model or "")
    registry = lx.build_loop_registry(sandbox)
    n_calls = {"n": 0}
    t0 = time.monotonic()

    def _on_call(reply: Any, cost: float) -> None:
        n_calls["n"] += 1
        _append(calls_path, {
            "call": n_calls["n"], "t": round(time.monotonic() - t0, 3),
            "model": summary["model"],
            "input_tokens": int(getattr(reply, "input_tokens", 0) or 0),
            "cached_tokens": int(getattr(reply, "cached_tokens", 0) or 0),
            "cache_write_tokens": int(getattr(reply, "cache_write_tokens", 0) or 0),
            "output_tokens": int(getattr(reply, "output_tokens", 0) or 0),
            "cost_usd_awos": round(float(cost), 8),
            "finish_reason": _finish_reason(getattr(reply, "raw", None)).strip("?") or None,
            "tool_calls": len(getattr(reply, "tool_calls", None) or []),
        })

    try:
        run = lx.LoopExecutor(
            client, registry, goal, workspace=workspace, gate=None, cost_cap=cost_cap,
            wall_s=wall_s, max_steps_=max_steps, on_call=_on_call, watch_workspace=False,
        ).run()
    finally:
        registry.close()
    return _finish({
        "stop_reason": run.stop_reason, "submitted": run.stop_reason == "submitted",
        "steps": run.steps, "tool_calls": run.tool_calls,
        "input_tokens": run.input_tokens, "cached_tokens": run.cached_tokens,
        "cache_write_tokens": run.cache_write_tokens, "output_tokens": run.output_tokens,
        "cost_usd_awos": round(run.cost_usd, 8), "elapsed_sec": round(run.elapsed_sec, 3),
        "final_message": (run.final_message or "")[:2000], "error": None,
    })


def _float(value: Optional[str]) -> Optional[float]:
    return float(value) if value not in (None, "") else None


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Run AWOS's loop executor on one goal, headless.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--goal-file")
    g.add_argument("--goal-env", help="name of an env var holding the goal")
    p.add_argument("--workspace", default=os.getcwd())
    p.add_argument("--out-dir", required=True)
    p.add_argument("--model", default=os.environ.get("AWOS_AGENT_MODEL") or None)
    p.add_argument("--cost-cap", default=None, help="USD, AWOS's own estimate")
    p.add_argument("--wall-s", default=None)
    p.add_argument("--max-steps", type=int, default=None)
    a = p.parse_args(argv)
    if a.goal_file:
        goal = Path(a.goal_file).read_text(encoding="utf-8")
    else:
        goal = os.environ.pop(a.goal_env, "")
    if not goal.strip():
        print("[awos-headless] empty goal", file=sys.stderr)
        return 2
    summary = run_headless(goal, os.path.abspath(a.workspace), a.out_dir, a.model,
                           cost_cap=_float(a.cost_cap), wall_s=_float(a.wall_s),
                           max_steps=a.max_steps)
    print(f"[awos-headless] {summary.get('stop_reason')}: {summary.get('steps', 0)} step(s), "
          f"${summary.get('cost_usd_awos', 0.0):.4f} (AWOS estimate)", flush=True)
    return 0 if summary.get("error") is None else 1


if __name__ == "__main__":
    sys.exit(main())

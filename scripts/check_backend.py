#!/usr/bin/env python3
"""
check_backend.py — Prove the model backend works before spending on a run.

One tiny request, a fraction of a cent, answering the four things that
otherwise fail twelve cases deep:

  1. Is a backend configured at all?
  2. Can this machine reach it?
  3. Is the key accepted?
  4. Does the chosen model actually support TOOL CALLING?

The fourth matters most. The agent loop is driven by tool calls, and a model
without them does not error — it replies in prose and the loop ends on turn one
having changed nothing. That reads as a hopeless agent rather than a wrong
model, and it costs a full benchmark run to discover.

Usage:
    python3 scripts/check_backend.py
    python3 scripts/check_backend.py --model anthropic/claude-haiku-4.5

Exits 0 when the backend is usable, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scaffold"))

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from agent.agent_loop import build_client_from_env, estimate_cost
from agent.tools.base import Tool, ToolRegistry, ToolResult


class _PingTool(Tool):
    """A trivial tool whose only purpose is to be called."""

    @property
    def name(self) -> str:
        return "report_ready"

    @property
    def description(self) -> str:
        return (
            "Report that you are ready to begin work. Call this immediately, "
            "with a one-word status."
        )

    @property
    def parameters(self) -> dict[str, str]:
        return {"status": "A single word, e.g. 'ready'"}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult.ok(f"acknowledged: {args.get('status', '')}")


def _fail(message: str, hint: str = "") -> int:
    print(f"\n  FAILED  {message}")
    if hint:
        print(f"\n{hint}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="Override the model id")
    args = parser.parse_args()

    print("\nChecking the model backend ...")

    # ── 1. configured ────────────────────────────────────────────────────────
    try:
        client = build_client_from_env(args.model)
    except RuntimeError as exc:
        return _fail("no backend configured", str(exc))

    endpoint = "anthropic (native)"
    inner = getattr(client, "_client", None)
    if inner is not None and getattr(inner, "base_url", None):
        endpoint = str(inner.base_url)

    print(f"  backend   {type(client).__name__}")
    print(f"  endpoint  {endpoint}")
    print(f"  model     {client.model}")

    registry = ToolRegistry()
    registry.register(_PingTool())

    # ── 2-4. reachable, authenticated, tool-capable ──────────────────────────
    try:
        reply = client.complete(
            "You are being checked for tool-calling support. Call the tool.",
            [{"role": "user", "content": "Call report_ready with status 'ready'."}],
            registry,
        )
    except Exception as exc:
        text = str(exc)
        lowered = text.lower()
        if "connect" in lowered or "ssl" in lowered or "timed out" in lowered:
            return _fail(
                f"cannot reach {endpoint}",
                "  The endpoint is unreachable from this machine. On a sandboxed\n"
                "  or proxied network the host may be blocked by policy, which is\n"
                "  not something a valid key fixes.",
            )
        if "401" in text or "auth" in lowered or "api key" in lowered:
            return _fail(
                "the key was rejected",
                "  Check the key is current and matches the backend in use.\n"
                "  AWOS_PROVIDER pins which backend runs when several keys are set.",
            )
        if "404" in text or "not found" in lowered or "model" in lowered:
            return _fail(
                f"the model {client.model!r} was not accepted",
                "  Ids differ per backend. OpenRouter uses vendor-prefixed ids\n"
                "  such as anthropic/claude-haiku-4.5 — see openrouter.ai/models.",
            )
        return _fail(f"{type(exc).__name__}: {text[:200]}")

    cost = estimate_cost(client.model, reply.input_tokens, reply.output_tokens)
    print(f"  tokens    {reply.input_tokens} in / {reply.output_tokens} out"
          f"   (~${cost:.5f})")

    if not reply.tool_calls:
        return _fail(
            f"{client.model!r} did not call the tool",
            "  It replied in prose instead:\n"
            f"    {(reply.text or '(no text)')[:160]}\n\n"
            "  The agent loop is driven by tool calls, so this model will finish\n"
            "  every task on turn one having changed nothing. Pick a model that\n"
            "  supports tool calling — most cheap open models do not.",
        )

    called = reply.tool_calls[0]
    print(f"  tool call {called.name}({called.arguments})")
    print("\n  OK — the backend is reachable, authenticated, and calls tools.\n")
    print("  Next:")
    print("    python3 scripts/bench_executors.py --arms both --record \\")
    print("        --cassette-dir .awos/cassettes --max-cost 0.15\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

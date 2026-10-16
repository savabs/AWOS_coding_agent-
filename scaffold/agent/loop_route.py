"""
loop_route.py — cost routing for the loop executor (rea_synthesis A6, A8).

Why: on a strong model the harness adds no accuracy (ladder H1–H1d; REA
reverse-engineering of 13 products agrees), so AWOS's measurable edge is
cost. The bet: start each task on the cheapest rung (the local model, or a
cheap cloud model), and move ONE WAY up the ladder only on a behavioural
signal that the rung is not getting there. The choice is sticky between
escalations, so the prompt cache stays warm (Factory Droid re-picks only on
compaction/overflow; Kiro never steps back down a tier).

    AWOS_LOOP_ROUTE="local,claude-haiku-5-5"   ladder, cheapest first
    AWOS_LOOP_ROUTE=""                         off (default): one model, as before
    AWOS_LOOP_ROUTE_RUNG_STEPS=30              steps a rung gets before the next
                                               rung takes over (the last rung
                                               runs to the task's max steps)
    AWOS_ROUTE_LOG=<path> | 0                  labelled route decisions (A8),
                                               default .awos/route_decisions.jsonl,
                                               off under pytest unless named

Rung specs: "local" or "local/<served name>" is the local llama-server
(prices at $0, grammar-constrained text protocol unless AWOS_LOOP_GRAMMAR=0);
"claude-*" is Anthropic direct (ANTHROPIC_API_KEY); anything else goes
through agent_loop.build_client_from_env(spec).

What escalates (LoopExecutor decides; see its `escalate` hook): the stuck
detector recommending it (loop_stuck.py), the rung's step allowance spent,
the rung's format/truncation limit, a model error, and a red gate after the
feedback rounds. A run never stops early because of routing: escalation only
ever hands the same task to a stronger model.

Changing protocol (local text protocol → Anthropic tool calls) cannot keep
the old conversation as is, so the new rung starts a fresh conversation with
the goal and a handover note (what was run, how it ended, files changed);
the workspace keeps the edits. Same protocol: the conversation carries over.

Every decision is appended as one JSON line, labelled once the task ends
(solved or not), so ml_router.py's prior can be calibrated on real outcomes
(A8). Observability never raises.

Pre-registration: docs/specs/loop_reliability_ab.md (R1).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_RUNG_STEPS = 30
DEFAULT_LOG = Path(".awos") / "route_decisions.jsonl"
_OFF = {"0", "off", "false", "no", ""}


def route_ladder() -> list[str]:
    """AWOS_LOOP_ROUTE as a list of rung specs, cheapest first ([] = off)."""
    raw = os.getenv("AWOS_LOOP_ROUTE", "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def rung_steps() -> int:
    """AWOS_LOOP_ROUTE_RUNG_STEPS (default 30): one non-final rung's step allowance."""
    try:
        return max(1, int(os.getenv("AWOS_LOOP_ROUTE_RUNG_STEPS", "").strip()
                          or DEFAULT_RUNG_STEPS))
    except ValueError:
        return DEFAULT_RUNG_STEPS


def is_local_spec(spec: str) -> bool:
    s = spec.strip().lower()
    return s == "local" or s.startswith("local/")


def build_route_client(spec: str) -> Any:
    """A loop-ready client for one rung. Raises when the rung can't be built
    (no key, no server config); the caller treats that as "no next rung"."""
    try:
        from . import agent_loop as al
        from .loop_grammar import GrammarLoopClient, _env as grammar_env
    except ImportError:  # flat import
        import agent_loop as al  # type: ignore
        from loop_grammar import GrammarLoopClient, _env as grammar_env  # type: ignore
    s = spec.strip()
    if is_local_spec(s):
        try:
            from .providers import local_chat_client, local_model_id
        except ImportError:
            from providers import local_chat_client, local_model_id  # type: ignore
        # "local/<name>" passes through as the accounting id (prices at $0);
        # bare "local" is the loaded model.
        client = al.OpenAIToolClient(local_chat_client(),
                                     local_model_id(s if "/" in s else None))
        # The grammar is normally switched on by AWOS_PROVIDER=local; a routed
        # local rung is local whatever the global provider is.
        if grammar_env() not in ("0", "false", "no", "off"):
            client = GrammarLoopClient(client)
        return client
    if s.lower().startswith("claude-"):
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(f"route rung {s}: ANTHROPIC_API_KEY is not set")
        from anthropic import Anthropic

        return al.AnthropicToolClient(Anthropic(api_key=key, **al._client_options()), s)
    return al.build_client_from_env(s)


# ── the handover note (protocol change) ─────────────────────────────────────


HANDOVER_TEMPLATE = (
    "Note: a cheaper model worked on this task first and has handed it over to you "
    "({reason}). Its edits are still in the working tree; check them with `git diff` "
    "and `git status` before building on them, and undo what is wrong.\n\n"
    "Files it changed: {files}\n\n"
    "Its last {n} commands and how they ended (oldest first):\n{commands}"
)


def handover_note(transcript: list, files_changed: list, reason: str,
                  last_n: int = 12) -> str:
    """The note a fresh conversation on the next rung starts with."""
    lines = []
    for t in (transcript or [])[-last_n:]:
        for cmd in (t.get("commands") or [])[:2]:
            ok = t.get("ok")
            mark = "" if ok is None else (" [ok]" if ok else " [failed]")
            lines.append(f"- step {t.get('step')}: {str(cmd)[:200]}{mark}")
    return HANDOVER_TEMPLATE.format(
        reason=reason, files=", ".join(files_changed[:30]) or "(none)",
        n=len(lines), commands="\n".join(lines) or "(none)")


# ── labelled decisions (A8) ─────────────────────────────────────────────────


def log_path() -> Optional[Path]:
    value = os.environ.get("AWOS_ROUTE_LOG")
    if value is None:
        return None if "PYTEST_CURRENT_TEST" in os.environ else DEFAULT_LOG
    if value.strip().lower() in _OFF:
        return None
    return Path(value)


def record_decisions(decisions: list[dict], *, goal_key: str, solved: bool,
                     stop_reason: str, cost_usd: float, steps: int) -> None:
    """Append each of one task's route decisions, labelled with how the task
    ended. Never raises."""
    path = log_path()
    if path is None or not decisions:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for d in decisions:
                fh.write(json.dumps({"ts": time.time(), "task": goal_key, **d,
                                     "label_solved": bool(solved),
                                     "label_stop_reason": stop_reason,
                                     "label_cost_usd": round(cost_usd, 6),
                                     "label_steps": steps}, default=str) + "\n")
    except Exception:  # noqa: BLE001 - observability never breaks a run
        pass

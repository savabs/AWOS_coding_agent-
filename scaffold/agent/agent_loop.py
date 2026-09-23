"""
agent_loop.py — The executing model drives its own turns through tools.

Contrast with Worker.execute_task(), which makes exactly one model call and
parses SEARCH/REPLACE blocks out of the reply. There, the model gets a single
blind look at context the Planner pre-selected: it cannot open a second file,
find a caller, or run the tests and react. Here it can, and keeps going until
the work verifies or a stop condition trips.

Everything safety-relevant is reused, not reinvented. Edits land through
Verifier (AST/syntax checks, contract compliance, fuzzy matching), tests run
through TestRunner, spend is gated by BudgetLedger, and rollback stays with
GitManager in the Orchestrator.

The model is reached through a small ModelClient protocol with two adapters —
Anthropic native tool use and OpenAI-compatible function calling — so the
Claude and DeepSeek tiers both drive the same loop, and tests can inject a stub
and run with no API key.

Usage:
    loop = AgentLoop(registry=build_coding_registry("."), client=client)
    outcome = loop.run("Add exponential backoff to _cheap_call in retry.py")
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)

#: Real tasks need many steps: read, search, edit, test, fix, re-test.
#: Overridable per process with AWOS_AGENT_MAX_TURNS.
DEFAULT_MAX_TURNS = 60
#: Assistant turns whose tool results stay verbatim; older results are stubbed
#: so a long run's resent context stops growing. AWOS_AGENT_KEEP_TURNS.
DEFAULT_KEEP_TURNS = 8
#: How many times an empty finish is pushed back when edits are required.
MAX_NUDGES = 2
NUDGE_MESSAGE = (
    "You have not changed any file yet, but this task requires changes. "
    "Continue: read what you need, make the change with edit_file, then run "
    "the tests to check it."
)
ELIDED_PREFIX = "[earlier tool output elided"
DEFAULT_MAX_REPEATS = 3
DEFAULT_TOOL_RESULT_CHARS = 8000

#: USD per million tokens, (input, output). A local runtime or a replayed
#: cassette costs nothing, so both are priced at zero and a cost cap simply
#: never trips for them.
PRICES: dict[str, tuple[float, float]] = {
    "local": (0.0, 0.0),
    "replay": (0.0, 0.0),
    "deepseek-chat": (0.14, 0.28),
    # Escalation-ladder models, at OpenRouter's prices on 2026-09-23.
    "deepseek-v4-flash": (0.089, 0.177),
    "deepseek-v4-pro": (0.955, 1.911),
    "qwen3.7-plus": (0.32, 1.28),
    "deepseek-reasoner": (0.55, 2.19),
    "gemini-2.0-flash": (0.10, 0.40),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
}


def _price_for(model: str) -> Optional[tuple[float, float]]:
    """
    Look up a model's prices, tolerating the ways ids are written.

    A gateway prefixes the vendor and often punctuates differently —
    OpenRouter's "anthropic/claude-haiku-4.5" is this table's
    "claude-haiku-4-5" — and a dated release appends a suffix. Matching only
    the literal id would silently price those at zero, which reads as "free"
    in a cost preflight.

    A gateway's own margin is not modelled, so a routed price is an
    approximation of the underlying model's direct price.
    """
    candidates = [model, model.rsplit("/", 1)[-1]]
    candidates += [c.replace(".", "-") for c in list(candidates)]

    for candidate in candidates:
        if candidate in PRICES:
            return PRICES[candidate]
    for candidate in candidates:
        # Prefix match so "claude-sonnet-4-6-20260101" prices like its family.
        for name, prices in PRICES.items():
            if candidate.startswith(name):
                return prices
    return None


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost in USD for a token count, 0.0 for an unpriced or free model."""
    prices = _price_for(model)
    if prices is None:
        return 0.0
    price_in, price_out = prices
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


SYSTEM_PROMPT = """You are a coding agent working in a real repository.

Work in this order:
1. Investigate before editing. Read the file you intend to change and search
   for callers, tests and similar patterns already in the codebase. Never guess
   at an API, a signature or a file's contents — open it.
2. Make the smallest edit that accomplishes the task, matching the surrounding
   code's style and idiom.
3. Verify. Run the tests and read the output. If your change broke something,
   fix it.

Rules:
- edit_file rejects an edit that would break the file's syntax and tells you
  why. Read the failure and correct it; do not retry the same edit unchanged.
- old_string must match the file exactly and appear exactly once. Include
  surrounding lines to make it unique.
- Do not change unrelated code, reformat files, or "improve" things you were
  not asked about.
- When the task is done and verified, reply with a short summary and no further
  tool calls. That ends your turn.
"""


# ── Model transport ──────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    """One tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelReply:
    """Normalised reply, whichever provider produced it."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    raw: Any = None


class ModelClient(Protocol):
    """Anything that can answer a tool-enabled turn."""

    def complete(
        self, system: str, messages: list[dict[str, Any]], registry: Any
    ) -> ModelReply: ...

    def format_tool_results(
        self, calls: list[ToolCall], results: list[Any]
    ) -> list[dict[str, Any]]: ...

    def format_assistant_turn(self, reply: ModelReply) -> dict[str, Any]: ...


class AnthropicToolClient:
    """Adapter for the Anthropic Messages API (native tool use)."""

    def __init__(self, client: Any, model: str, max_tokens: int = 4096) -> None:
        self._client = client
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system, messages, registry) -> ModelReply:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=registry.anthropic_schemas(),
        )
        text_parts, calls = [], []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
            elif getattr(block, "type", None) == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        usage = getattr(response, "usage", None)
        return ModelReply(
            text="\n".join(text_parts),
            tool_calls=calls,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            raw=response,
        )

    def format_assistant_turn(self, reply: ModelReply) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if reply.text:
            content.append({"type": "text", "text": reply.text})
        for call in reply.tool_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments,
                }
            )
        return {"role": "assistant", "content": content}

    def format_tool_results(self, calls, results) -> list[dict[str, Any]]:
        blocks = [
            {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": _render_result(result),
                "is_error": not result.success,
            }
            for call, result in zip(calls, results)
        ]
        return [{"role": "user", "content": blocks}]


class OpenAIToolClient:
    """Adapter for OpenAI-compatible function calling (DeepSeek, GPT, others)."""

    def __init__(self, client: Any, model: str, max_tokens: int = 4096) -> None:
        self._client = client
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system, messages, registry) -> ModelReply:
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "system", "content": system}, *messages],
            tools=registry.openai_schemas(),
        )
        message = response.choices[0].message
        calls = []
        for raw_call in getattr(message, "tool_calls", None) or []:
            try:
                arguments = json.loads(raw_call.function.arguments or "{}")
            except json.JSONDecodeError:
                # Malformed arguments are the model's error to correct; surface
                # them as a failing tool call rather than crashing the loop.
                arguments = {"__malformed__": raw_call.function.arguments}
            calls.append(ToolCall(id=raw_call.id, name=raw_call.function.name, arguments=arguments))
        usage = getattr(response, "usage", None)
        return ModelReply(
            text=getattr(message, "content", "") or "",
            tool_calls=calls,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            raw=response,
        )

    def format_assistant_turn(self, reply: ModelReply) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": reply.text or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in reply.tool_calls
            ],
        }

    def format_tool_results(self, calls, results) -> list[dict[str, Any]]:
        return [
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": _render_result(result),
            }
            for call, result in zip(calls, results)
        ]


def _render_result(result: Any) -> str:
    """Turn a ToolResult into the text the model sees."""
    body = result.text if result.success else f"ERROR: {result.error}"
    if len(body) > DEFAULT_TOOL_RESULT_CHARS:
        kept = DEFAULT_TOOL_RESULT_CHARS
        body = (
            body[:kept]
            + f"\n... [truncated {len(body) - kept} characters; narrow the query "
            "or read a specific line range]"
        )
    return body or "(no output)"


# ── The loop ─────────────────────────────────────────────────────────────────


@dataclass
class LoopOutcome:
    """What happened over a whole run."""

    success: bool
    stop_reason: str
    turns: int = 0
    tool_calls: int = 0
    failed_tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    final_message: str = ""
    files_touched: list[str] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    elapsed_sec: float = 0.0
    nudges: int = 0
    elided_results: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stop_reason": self.stop_reason,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "final_message": self.final_message,
            "files_touched": self.files_touched,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "nudges": self.nudges,
            "elided_results": self.elided_results,
        }


class AgentLoop:
    """Drives a model through tool calls until the task is done or it must stop."""

    def __init__(
        self,
        registry: Any,
        client: ModelClient,
        max_turns: Optional[int] = None,
        max_repeats: int = DEFAULT_MAX_REPEATS,
        system_prompt: str = SYSTEM_PROMPT,
        ledger: Any = None,
        monthly_budget: Optional[float] = None,
        cost_per_turn_estimate: float = 0.01,
        max_cost_usd: Optional[float] = None,
        on_event: Any = None,
        require_edits: bool = False,
    ) -> None:
        self.registry = registry
        self.client = client
        self.max_turns = (
            max_turns
            if max_turns is not None
            else _int_env("AWOS_AGENT_MAX_TURNS", DEFAULT_MAX_TURNS)
        )
        self.keep_turns = max(1, _int_env("AWOS_AGENT_KEEP_TURNS", DEFAULT_KEEP_TURNS))
        self.require_edits = require_edits
        self.max_repeats = max_repeats
        self.system_prompt = system_prompt
        self.ledger = ledger
        self.monthly_budget = (
            monthly_budget
            if monthly_budget is not None
            else float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0"))
        )
        self.cost_per_turn_estimate = cost_per_turn_estimate
        # A hard ceiling for ONE run, distinct from BudgetLedger's monthly cap.
        # This is what makes it safe to point a benchmark at a paid model.
        self.max_cost_usd = (
            max_cost_usd
            if max_cost_usd is not None
            else _float_env("AWOS_MAX_RUN_COST", None)
        )
        self.on_event = on_event

    @property
    def model_name(self) -> str:
        """Model id behind the client, for pricing. 'replay' when cassetted."""
        return getattr(self.client, "model", None) or "replay"

    def _emit(self, kind: str, **payload: Any) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:  # observability must never break execution
                logger.debug("on_event handler raised", exc_info=True)

    def _budget_blocked(self) -> tuple[bool, str]:
        if self.ledger is None:
            return False, ""
        try:
            allowed, reason = self.ledger.check_budget(
                self.cost_per_turn_estimate, self.monthly_budget
            )
        except Exception as exc:
            logger.warning("[agent_loop] budget check failed, continuing: %s", exc)
            return False, ""
        return (not allowed), reason

    def run(self, task: str, extra_context: str = "") -> LoopOutcome:
        """Work `task` to completion, or until a stop condition trips."""
        started = time.monotonic()
        prompt = f"{task}\n\n{extra_context}".strip()
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

        outcome = LoopOutcome(success=False, stop_reason="max_turns")
        seen_calls: dict[str, int] = {}
        touched: list[str] = []

        for turn in range(1, self.max_turns + 1):
            outcome.turns = turn

            blocked, reason = self._budget_blocked()
            if blocked:
                outcome.stop_reason = "budget_blocked"
                outcome.final_message = reason
                self._emit("budget_blocked", reason=reason)
                break

            outcome.elided_results += _condense_history(messages, self.keep_turns)

            try:
                reply = self.client.complete(self.system_prompt, messages, self.registry)
            except Exception as exc:
                outcome.stop_reason = "model_error"
                outcome.final_message = f"{type(exc).__name__}: {exc}"
                logger.warning("[agent_loop] model call failed on turn %d: %s", turn, exc)
                break

            outcome.input_tokens += reply.input_tokens
            outcome.output_tokens += reply.output_tokens
            outcome.cost_usd = estimate_cost(
                self.model_name, outcome.input_tokens, outcome.output_tokens
            )
            self._emit("turn", turn=turn, text=reply.text, calls=len(reply.tool_calls))

            if self.max_cost_usd is not None and outcome.cost_usd >= self.max_cost_usd:
                # Checked after the turn is accounted for, so the reported cost
                # is what was actually spent rather than what was projected.
                outcome.stop_reason = "cost_cap"
                outcome.final_message = (
                    f"Stopped at ${outcome.cost_usd:.4f}, the ${self.max_cost_usd:.4f} "
                    "per-run cap."
                )
                self._emit("cost_cap", cost_usd=outcome.cost_usd)
                break

            # No tool calls means the model considers the work finished.
            if not reply.tool_calls:
                if self.require_edits and not touched and outcome.nudges < MAX_NUDGES:
                    # An empty finish on a task that needs changes is almost
                    # always the model giving up early, not a finished task.
                    outcome.nudges += 1
                    outcome.transcript.append(
                        {"turn": turn, "text": reply.text, "calls": [], "nudged": True}
                    )
                    if reply.text:
                        # Anthropic rejects an assistant turn with empty content.
                        messages.append(self.client.format_assistant_turn(reply))
                    messages.append({"role": "user", "content": NUDGE_MESSAGE})
                    self._emit("nudge", turn=turn, nudges=outcome.nudges)
                    continue
                outcome.success = True
                outcome.stop_reason = "completed"
                outcome.final_message = reply.text
                outcome.transcript.append({"turn": turn, "text": reply.text, "calls": []})
                break

            messages.append(self.client.format_assistant_turn(reply))

            results = []
            for call in reply.tool_calls:
                signature = f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"
                seen_calls[signature] = seen_calls.get(signature, 0) + 1

                if seen_calls[signature] > self.max_repeats:
                    # Same call, same arguments, over and over: the model is
                    # stuck. Say so in-band so it can change approach, and stop
                    # if it does not.
                    results.append(
                        _synthetic_failure(
                            f"You have called {call.name} with these exact arguments "
                            f"{seen_calls[signature]} times and the result has not "
                            "changed. Try a different approach."
                        )
                    )
                    outcome.failed_tool_calls += 1
                    outcome.stop_reason = "repeated_tool_call"
                    continue

                result = self.registry.execute(call.name, call.arguments)
                outcome.tool_calls += 1
                if not result.success:
                    outcome.failed_tool_calls += 1
                if call.name == "edit_file" and result.success:
                    path = result.data.get("path")
                    if path and path not in touched:
                        touched.append(path)
                results.append(result)
                self._emit("tool", name=call.name, ok=result.success)

            outcome.transcript.append(
                {
                    "turn": turn,
                    "text": reply.text,
                    "calls": [
                        {"name": c.name, "ok": r.success} for c, r in zip(reply.tool_calls, results)
                    ],
                }
            )

            if outcome.stop_reason == "repeated_tool_call" and all(
                not r.success for r in results
            ):
                break

            messages.extend(self.client.format_tool_results(reply.tool_calls, results))

        outcome.files_touched = touched
        outcome.elapsed_sec = time.monotonic() - started
        self._emit("done", **outcome.to_dict())
        return outcome


def _float_env(name: str, default: Optional[float]) -> Optional[float]:
    """Read a float from the environment, ignoring an unparseable value."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("[agent_loop] %s=%r is not a number; ignoring", name, raw)
        return default


def _int_env(name: str, default: int) -> int:
    """Read an int from the environment, ignoring an unparseable value."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("[agent_loop] %s=%r is not an integer; ignoring", name, raw)
        return default


def _elided_stub(chars: int) -> str:
    return f"{ELIDED_PREFIX} — {chars} chars; re-read the file if you need it]"


def _should_stub(content: str) -> bool:
    """Stub unless already a stub, or too short for a stub to save anything."""
    return not content.startswith(ELIDED_PREFIX) and len(content) > len(
        _elided_stub(len(content))
    )


def _condense_history(messages: list[dict[str, Any]], keep_turns: int) -> int:
    """
    Stub the text of tool results older than the last `keep_turns` assistant
    turns, in place. Returns how many results were newly stubbed.

    Only tool-result *content* shrinks: every message, block, tool_use_id and
    tool_call_id stays, so Anthropic's tool_use/tool_result pairing and
    OpenAI's tool_call_id linkage remain valid. A conversation with at most
    `keep_turns` assistant turns is not touched at all, which keeps recorded
    cassettes (fingerprinted on the payload) replaying exactly. Idempotent.
    """
    assistant_idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if len(assistant_idx) <= keep_turns:
        return 0
    cutoff = assistant_idx[-keep_turns]  # messages from here on stay verbatim

    stubbed = 0
    for i in range(1, cutoff):  # index 0 is the task prompt
        message = messages[i]
        role = message.get("role")
        if role == "tool":  # OpenAI dialect
            content = message.get("content")
            if isinstance(content, str) and _should_stub(content):
                messages[i] = {**message, "content": _elided_stub(len(content))}
                stubbed += 1
        elif role == "user" and isinstance(message.get("content"), list):  # Anthropic
            new_blocks, changed = [], False
            for block in message["content"]:
                content = block.get("content") if isinstance(block, dict) else None
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and isinstance(content, str)
                    and _should_stub(content)
                ):
                    new_blocks.append({**block, "content": _elided_stub(len(content))})
                    changed = True
                    stubbed += 1
                else:
                    new_blocks.append(block)
            if changed:
                messages[i] = {**message, "content": new_blocks}
    return stubbed


def _synthetic_failure(message: str) -> Any:
    """A ToolResult-shaped failure produced by the loop rather than a tool."""
    try:
        from .tools.base import ToolResult
    except ImportError:
        from tools.base import ToolResult
    return ToolResult.fail(message)


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _build_openrouter(api_key: str, model: Optional[str]) -> ModelClient:
    """
    OpenRouter: one key, many vendors, OpenAI-compatible wire format.

    No default model. OpenRouter ids are vendor-prefixed and its catalogue
    changes, so guessing one produces a confusing 404 at the first turn rather
    than a clear message here. The loop is driven by tool calls, so the chosen
    model must support them — many cheap open models do not.
    """
    chosen = model or os.getenv("AWOS_AGENT_MODEL")
    if not chosen:
        raise RuntimeError(
            "OPENROUTER_API_KEY is set but no model was chosen. OpenRouter ids "
            "are vendor-prefixed, e.g.\n"
            "  AWOS_AGENT_MODEL=anthropic/claude-haiku-4.5\n"
            "  AWOS_AGENT_MODEL=deepseek/deepseek-chat\n"
            "Pick one that supports tool calling; see https://openrouter.ai/models"
        )

    from openai import OpenAI

    # Optional attribution headers. OpenRouter uses them for its rankings page
    # and neither is required, so they are only sent when configured.
    headers = {}
    if os.getenv("OPENROUTER_SITE_URL"):
        headers["HTTP-Referer"] = os.environ["OPENROUTER_SITE_URL"]
    if os.getenv("OPENROUTER_APP_NAME"):
        headers["X-Title"] = os.environ["OPENROUTER_APP_NAME"]

    return OpenAIToolClient(
        OpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            default_headers=headers or None,
        ),
        chosen,
    )


def build_client_from_env(model: Optional[str] = None) -> ModelClient:
    """
    Construct a ModelClient from whatever credentials the environment carries.

    Order, first match wins:
      1. AWOS_BASE_URL      any OpenAI-compatible endpoint, local or hosted
      2. OPENROUTER_API_KEY one key across vendors
      3. ANTHROPIC_API_KEY  native tool use, the better-tested path
      4. DEEPSEEK_API_KEY   the cheap tier
      5. OPENAI_API_KEY

    AWOS_PROVIDER pins one explicitly (local, openrouter, anthropic, deepseek,
    openai) for an environment holding several keys, so which backend runs is
    never a matter of guessing the precedence.

    Raises RuntimeError when nothing usable is configured, so a caller can
    report that plainly rather than failing deep inside a turn.
    """
    provider = os.getenv("AWOS_PROVIDER", "").strip().lower()

    def _want(name: str) -> bool:
        """True when this backend should be tried: pinned, or nothing pinned."""
        return provider in ("", name)

    # A local or self-hosted OpenAI-compatible endpoint (Ollama, LM Studio,
    # vLLM, llama.cpp) wins outright when configured: unlimited, free, and it
    # needs no credential, so iterating on the loop never touches a key.
    #   AWOS_BASE_URL=http://localhost:11434/v1 AWOS_AGENT_MODEL=qwen2.5-coder
    base_url = os.getenv("AWOS_BASE_URL")
    if base_url and _want("local"):
        from openai import OpenAI

        return OpenAIToolClient(
            # Local servers ignore the key but the SDK insists on one.
            OpenAI(api_key=os.getenv("AWOS_BASE_URL_KEY", "not-needed"), base_url=base_url),
            model or os.getenv("AWOS_AGENT_MODEL", "qwen2.5-coder"),
        )

    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key and _want("openrouter"):
        return _build_openrouter(openrouter_key, model)

    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key and _want("anthropic"):
        from anthropic import Anthropic

        return AnthropicToolClient(
            Anthropic(api_key=anthropic_key),
            model or os.getenv("AWOS_AGENT_MODEL", "claude-sonnet-4-6"),
        )

    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if deepseek_key and _want("deepseek"):
        from openai import OpenAI

        return OpenAIToolClient(
            OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com"),
            model or os.getenv("AWOS_AGENT_MODEL", "deepseek-chat"),
        )

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key and _want("openai"):
        from openai import OpenAI

        return OpenAIToolClient(
            OpenAI(api_key=openai_key),
            model or os.getenv("AWOS_AGENT_MODEL", "gpt-4o-mini"),
        )

    if provider:
        raise RuntimeError(
            f"AWOS_PROVIDER={provider} but its credential is not set. "
            "Unset AWOS_PROVIDER to fall back to whatever else is configured."
        )

    raise RuntimeError(
        "No model backend configured. Pick one:\n"
        "  free, no key   AWOS_BASE_URL=http://localhost:11434/v1  (Ollama etc.)\n"
        "  free, no key   AWOS_CASSETTE=<file>   (replay a recorded run)\n"
        "  one key, many models   OPENROUTER_API_KEY + AWOS_AGENT_MODEL\n"
        "  direct         ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY\n"
        "awos.py loads a .env file, so these can live there."
    )


def build_coding_registry(project_root: str = ".", allow_shell: bool = False) -> Any:
    """
    The tool set a coding agent needs: look, search, edit, verify.

    write_file is deliberately excluded — edit_file routes through Verifier and
    cannot leave a file syntactically broken, whereas write_file overwrites
    wholesale. shell is opt-in, and gated again by ShellTool's own ALLOW_SHELL
    check for anything destructive.
    """
    try:
        from .tools.base import ToolRegistry
        from .tools.filesystem import ReadFileTool, ListDirTool, FindFilesTool, GrepTool
        from .tools.code_edit import EditFileTool, RunTestsTool
    except ImportError:
        from tools.base import ToolRegistry
        from tools.filesystem import ReadFileTool, ListDirTool, FindFilesTool, GrepTool
        from tools.code_edit import EditFileTool, RunTestsTool

    # Every tool resolves relative paths against the same root, so "calc.py"
    # means the same file to read_file and to edit_file.
    registry = ToolRegistry()
    for tool in (
        ReadFileTool(project_root=project_root),
        ListDirTool(project_root=project_root),
        FindFilesTool(project_root=project_root),
        GrepTool(project_root=project_root),
        EditFileTool(project_root=project_root),
        RunTestsTool(project_root=project_root),
    ):
        registry.register(tool)

    if allow_shell:
        try:
            from .tools.shell import ShellTool
        except ImportError:
            from tools.shell import ShellTool
        registry.register(ShellTool())

    return registry

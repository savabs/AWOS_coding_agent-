"""
cassette.py — Record a model's replies once, replay them forever for free.

The problem this solves: iterating on the agent loop should not require an API
key or cost money per run. Hand-written stubs are free but test only what you
imagined the model would do. A cassette is the middle path — record one real
run, then replay those exact replies offline, deterministically, with no
credentials.

    # once, against any model (costs pennies, or nothing on a local runtime)
    AWOS_CASSETTE=.awos/cassettes/fix_add.json AWOS_CASSETTE_MODE=record \\
        python3 awos.py agent "fix add()"

    # thereafter, free and offline, in CI, with no key present
    AWOS_CASSETTE=.awos/cassettes/fix_add.json python3 awos.py agent "fix add()"

Replay is keyed on the conversation so far, so a cassette replays correctly as
long as the run follows the same path. When the run diverges — you changed the
system prompt, or a tool now returns something different — the exact key misses
and the cassette says so instead of silently returning the wrong turn. In
non-strict mode it then falls back to the next unplayed reply in recorded
order, which keeps a slightly-drifted run usable; strict mode refuses.

A cassette is plain JSON. It is readable, diffable, and safe to commit: it
contains the model's replies, not credentials.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from .agent_loop import ModelReply, ToolCall
except ImportError:
    from agent_loop import ModelReply, ToolCall


CASSETTE_VERSION = 1


ROOT_TOKEN = "<PROJECT_ROOT>"


def _fingerprint(
    system: str, messages: list[dict[str, Any]], root: Optional[str] = None
) -> str:
    """
    Stable key for "the conversation up to this point".

    Tool output embeds absolute paths ("File: /tmp/xyz/calc.py"), and those
    paths land in the message history. Without normalisation a cassette
    recorded in one directory misses on every turn when replayed from another —
    which is precisely the benchmarking case, where each run gets a fresh temp
    checkout. Replacing the project root with a fixed token makes a cassette
    portable across directories while still keying on what the model actually
    saw.

    default=str keeps the hash computable for any message payload a provider
    adapter produces, including objects json does not natively handle.
    """
    blob = json.dumps(
        {"system": system, "messages": messages}, sort_keys=True, default=str
    )
    if root:
        # Tool output may show the root as given or resolved; on macOS those
        # differ (/var → /private/var). Longest first, so the resolved form is
        # not left with a stray "/private" prefix.
        given = str(Path(root).expanduser())
        resolved = str(Path(root).expanduser().resolve())
        for form in sorted({given, resolved}, key=len, reverse=True):
            blob = blob.replace(json.dumps(form)[1:-1], ROOT_TOKEN)
            blob = blob.replace(form, ROOT_TOKEN)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _reply_to_dict(reply: ModelReply) -> dict[str, Any]:
    return {
        "text": reply.text,
        "tool_calls": [
            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in reply.tool_calls
        ],
        "input_tokens": reply.input_tokens,
        "output_tokens": reply.output_tokens,
    }


def _reply_from_dict(payload: dict[str, Any]) -> ModelReply:
    return ModelReply(
        text=payload.get("text", ""),
        tool_calls=[
            ToolCall(id=c["id"], name=c["name"], arguments=c.get("arguments", {}))
            for c in payload.get("tool_calls", [])
        ],
        input_tokens=payload.get("input_tokens", 0),
        output_tokens=payload.get("output_tokens", 0),
    )


class CassetteMiss(RuntimeError):
    """Replay was asked for a turn the cassette does not contain."""


@dataclass
class Cassette:
    """The recorded turns of one run, plus how they were produced."""

    path: Path
    turns: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "Cassette":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"No cassette at {p}. Record one first with AWOS_CASSETTE_MODE=record."
            )
        payload = json.loads(p.read_text(encoding="utf-8"))
        return cls(path=p, turns=payload.get("turns", []), meta=payload.get("meta", {}))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"version": CASSETTE_VERSION, "meta": self.meta, "turns": self.turns},
                indent=2,
            ),
            encoding="utf-8",
        )

    @property
    def total_tokens(self) -> tuple[int, int]:
        return (
            sum(t["reply"].get("input_tokens", 0) for t in self.turns),
            sum(t["reply"].get("output_tokens", 0) for t in self.turns),
        )


class RecordingClient:
    """Wraps a real ModelClient, saving every reply to a cassette."""

    def __init__(
        self,
        inner: Any,
        path: str | Path,
        meta: Optional[dict] = None,
        root: Optional[str] = None,
    ) -> None:
        self.inner = inner
        self.root = root
        meta = dict(meta or {})
        meta.setdefault("root_normalised", bool(root))
        self.cassette = Cassette(path=Path(path), meta=meta)

    @property
    def model(self) -> Optional[str]:
        # Recording spends real money. Without this, AgentLoop prices the
        # wrapper as "replay" — $0 — and --max-cost can never trip.
        return getattr(self.inner, "model", None)

    def complete(self, system, messages, registry) -> ModelReply:
        reply = self.inner.complete(system, messages, registry)
        self.cassette.turns.append(
            {
                "key": _fingerprint(system, messages, self.root),
                "reply": _reply_to_dict(reply),
            }
        )
        # Written after every turn, so a run that crashes or is interrupted
        # still leaves a usable partial cassette rather than nothing.
        self.cassette.save()
        return reply

    def format_assistant_turn(self, reply):
        return self.inner.format_assistant_turn(reply)

    def format_tool_results(self, calls, results):
        return self.inner.format_tool_results(calls, results)


class ReplayingClient:
    """Serves recorded replies. No network, no credentials, no cost."""

    def __init__(
        self,
        path: str | Path,
        dialect: str = "anthropic",
        strict: bool = False,
        root: Optional[str] = None,
    ) -> None:
        self.cassette = Cassette.load(path)
        self.strict = strict
        self.root = root
        self.misses = 0
        self._played: set[int] = set()
        # Message formatting must match the dialect the cassette was recorded
        # against, since the fingerprint covers the message payload.
        self._shape = dialect

    # -- transport ---------------------------------------------------------

    def complete(self, system, messages, registry) -> ModelReply:
        key = _fingerprint(system, messages, self.root)

        for index, turn in enumerate(self.cassette.turns):
            if index not in self._played and turn.get("key") == key:
                self._played.add(index)
                return _reply_from_dict(turn["reply"])

        self.misses += 1
        if self.strict:
            raise CassetteMiss(
                f"Cassette {self.cassette.path} has no turn for this conversation "
                f"(key {key}). The run diverged from what was recorded — "
                "re-record with AWOS_CASSETTE_MODE=record."
            )

        for index, turn in enumerate(self.cassette.turns):
            if index not in self._played:
                self._played.add(index)
                logger.warning(
                    "[cassette] key %s not found; falling back to recorded turn %d. "
                    "The run has diverged from the recording.",
                    key,
                    index + 1,
                )
                return _reply_from_dict(turn["reply"])

        raise CassetteMiss(
            f"Cassette {self.cassette.path} is exhausted after "
            f"{len(self.cassette.turns)} turns. The run is longer than the "
            "recording — re-record it."
        )

    # -- message shaping ---------------------------------------------------
    # Delegated to the real adapters so replayed message payloads are byte-for-
    # byte what recording produced; otherwise every fingerprint after turn 1
    # would miss.

    def _adapter(self):
        try:
            from .agent_loop import AnthropicToolClient, OpenAIToolClient
        except ImportError:
            from agent_loop import AnthropicToolClient, OpenAIToolClient
        cls = AnthropicToolClient if self._shape == "anthropic" else OpenAIToolClient
        return cls(None, "replay")

    def format_assistant_turn(self, reply):
        return self._adapter().format_assistant_turn(reply)

    def format_tool_results(self, calls, results):
        return self._adapter().format_tool_results(calls, results)


def wrap_for_cassette(
    client: Any,
    path: Optional[str] = None,
    mode: Optional[str] = None,
    dialect: Optional[str] = None,
    strict: bool = False,
    root: Optional[str] = None,
) -> Any:
    """
    Apply cassette behaviour according to arguments or environment.

    AWOS_CASSETTE       path to the cassette file
    AWOS_CASSETTE_MODE  'record' to capture, anything else replays
    AWOS_CASSETTE_STRICT set to 1 to fail on divergence instead of falling back

    Returns `client` unchanged when no cassette is configured, so this is safe
    to call unconditionally.
    """
    path = path or os.getenv("AWOS_CASSETTE")
    if not path:
        return client

    mode = (mode or os.getenv("AWOS_CASSETTE_MODE", "replay")).lower()
    strict = strict or os.getenv("AWOS_CASSETTE_STRICT", "").lower() in ("1", "true", "yes")

    if mode == "record":
        if client is None:
            raise RuntimeError("Recording needs a real model client; none was supplied.")
        return RecordingClient(
            client, path, meta={"dialect": dialect or _dialect_of(client)}, root=root
        )

    resolved = dialect or (Cassette.load(path).meta.get("dialect") or "anthropic")
    return ReplayingClient(path, dialect=resolved, strict=strict, root=root)


def _dialect_of(client: Any) -> str:
    return "openai" if type(client).__name__.startswith("OpenAI") else "anthropic"

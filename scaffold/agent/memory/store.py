"""
memory/store.py — Three-tier memory system for agentic workflows.

Tiers:
  EpisodicMemory  — timestamped event log (what happened, when)
  SemanticMemory  — knowledge graph / fact store (what is known)
  WorkingMemory   — current session context (active goal, recent observations)

These are stubs. Replace with persistence (SQLite, vector DB, etc.) as needed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Episode:
    """A single timestamped event in episodic memory."""

    timestamp: float
    event_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Fact:
    """A named fact in semantic memory."""

    key: str
    value: Any
    confidence: float = 1.0
    source: str = ""
    timestamp: float = field(default_factory=time.time)


class EpisodicMemory:
    """
    Timestamped event log.
    In production: persist to SQLite with FTS for retrieval.
    """

    def __init__(self) -> None:
        self._events: list[Episode] = []

    def record(self, event_type: str, content: str, **metadata: Any) -> None:
        self._events.append(
            Episode(
                timestamp=time.time(),
                event_type=event_type,
                content=content,
                metadata=metadata,
            )
        )

    def recent(self, n: int = 10) -> list[Episode]:
        return self._events[-n:]

    def search(self, query: str) -> list[Episode]:
        """Simple substring search. Replace with embedding search in production."""
        q = query.lower()
        return [e for e in self._events if q in e.content.lower() or q in e.event_type.lower()]

    def __len__(self) -> int:
        return len(self._events)


class SemanticMemory:
    """
    Key-value fact store with confidence tracking.
    In production: replace with a vector DB or graph store.
    """

    def __init__(self) -> None:
        self._facts: dict[str, Fact] = {}

    def store(self, key: str, value: Any, confidence: float = 1.0, source: str = "") -> None:
        self._facts[key] = Fact(key=key, value=value, confidence=confidence, source=source)

    def retrieve(self, key: str) -> Fact | None:
        return self._facts.get(key)

    def search(self, query: str) -> list[Fact]:
        """Simple substring search over keys."""
        q = query.lower()
        return [f for f in self._facts.values() if q in f.key.lower()]

    def all_facts(self) -> list[Fact]:
        return list(self._facts.values())

    def __len__(self) -> int:
        return len(self._facts)


class WorkingMemory:
    """
    Current session state: active goal, recent observations, scratchpad.
    Cleared between sessions unless explicitly serialized.
    """

    def __init__(self) -> None:
        self.goal: str = ""
        self.observations: list[str] = []
        self.scratchpad: dict[str, Any] = {}
        self.iteration: int = 0

    def set_goal(self, goal: str) -> None:
        self.goal = goal

    def observe(self, observation: str) -> None:
        self.observations.append(observation)
        if len(self.observations) > 50:
            self.observations = self.observations[-50:]  # keep recent

    def clear(self) -> None:
        self.goal = ""
        self.observations = []
        self.scratchpad = {}
        self.iteration = 0


@dataclass
class MemoryStore:
    """Unified access to all three memory tiers."""

    episodic: EpisodicMemory = field(default_factory=EpisodicMemory)
    semantic: SemanticMemory = field(default_factory=SemanticMemory)
    working: WorkingMemory = field(default_factory=WorkingMemory)

    def summary(self) -> str:
        return (
            f"Memory: {len(self.episodic)} episodes, "
            f"{len(self.semantic)} facts, "
            f"goal='{self.working.goal[:50]}...'"
            if len(self.working.goal) > 50
            else f"Memory: {len(self.episodic)} episodes, "
            f"{len(self.semantic)} facts, "
            f"goal='{self.working.goal}'"
        )

"""
reasoning.py — ReAct-style reasoning traces for transparent agent cognition.

Records Thought → Action → Observation → Verification loops,
persisting them for debugging, learning, and self-correction.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ReasoningTrace:
    """Single step in a ReAct reasoning chain."""
    thought: str
    action: str
    action_input: dict[str, Any]
    observation: str
    success: bool
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    latency_ms: float = 0.0
    model: str = "unknown"
    cost: float = 0.0


@dataclass
class ReasoningSession:
    """A complete reasoning session for one user request."""
    session_id: str
    goal: str
    traces: list[ReasoningTrace] = field(default_factory=list)
    start_time: str = field(default_factory=lambda: datetime.now().isoformat())
    total_cost: float = 0.0
    final_success: bool = False

    def add_trace(self, trace: ReasoningTrace) -> None:
        self.traces.append(trace)
        self.total_cost += trace.cost

    def summary(self) -> str:
        done = sum(1 for t in self.traces if t.success)
        return (
            f"Session {self.session_id}: {done}/{len(self.traces)} steps successful | "
            f"cost: ${self.total_cost:.4f} | goal: {self.goal[:60]}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "start_time": self.start_time,
            "total_cost": self.total_cost,
            "final_success": self.final_success,
            "traces": [asdict(t) for t in self.traces],
        }


class ReasoningTraceStore:
    """Persistent store for reasoning traces."""

    def __init__(self, persist_dir: str | Path = ".awos/traces") -> None:
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

    def save(self, session: ReasoningSession) -> Path:
        """Persist a session to disk."""
        fname = f"{session.session_id}_{session.start_time.replace(':', '-')}.json"
        fpath = self.persist_dir / fname
        fpath.write_text(json.dumps(session.to_dict(), indent=2))
        logger.info("Saved reasoning trace: %s (%d steps)", fpath.name, len(session.traces))
        return fpath

    def load(self, session_id: str) -> ReasoningSession | None:
        """Load a session by ID."""
        matches = list(self.persist_dir.glob(f"{session_id}*.json"))
        if not matches:
            return None
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        data = json.loads(matches[0].read_text())
        return self._from_dict(data)

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all saved sessions with metadata."""
        sessions = []
        for fpath in self.persist_dir.glob("*.json"):
            data = json.loads(fpath.read_text())
            sessions.append({
                "session_id": data["session_id"],
                "goal": data["goal"][:80],
                "steps": len(data["traces"]),
                "cost": data["total_cost"],
                "success": data["final_success"],
                "file": fpath.name,
            })
        sessions.sort(key=lambda x: x["file"], reverse=True)
        return sessions

    def _from_dict(self, data: dict[str, Any]) -> ReasoningSession:
        session = ReasoningSession(
            session_id=data["session_id"],
            goal=data["goal"],
            start_time=data["start_time"],
            total_cost=data["total_cost"],
            final_success=data["final_success"],
        )
        for t in data.get("traces", []):
            session.add_trace(ReasoningTrace(**t))
        return session


class ReActOrchestratorMixin:
    """
    Mixin for Orchestrator to add ReAct reasoning capabilities.

    Usage in Orchestrator.execute():
        trace_store = ReasoningTraceStore()
        session = ReasoningSession(session_id=..., goal=...)

        for step in plan.steps:
            thought = self._generate_thought(step, session.traces)
            t0 = time.time()
            result = self.tools.execute(step.tool, step.args)
            latency = (time.time() - t0) * 1000

            trace = ReasoningTrace(
                thought=thought,
                action=step.tool,
                action_input=step.args,
                observation=result.text[:500],
                success=result.success,
                latency_ms=latency,
                model="deepseek-v4",
                cost=0.0001,
            )
            session.add_trace(trace)

            # Self-correction: if failed, try alternative
            if not result.success:
                alt_step = self._generate_alternative(step, result.error)
                if alt_step:
                    # ... retry logic

        trace_store.save(session)
    """

    def _generate_thought(
        self,
        step: Any,  # Step dataclass
        prior_traces: list[ReasoningTrace],
        goal: str,
        llm_caller: Any,  # Callable that takes prompt str, returns str
    ) -> str:
        """Ask a cheap LLM to generate a thought before executing a step."""
        context = "\n".join([
            f"Previous: {t.thought} → {t.action} ({'✓' if t.success else '✗'})"
            for t in prior_traces[-3:]
        ])

        prompt = f"""Goal: {goal}

Next action: {step.tool}({json.dumps(step.args)})

Prior steps:
{context or '(none yet)'}

In 1-2 sentences, what should I think about before executing this action?
Focus on potential pitfalls, what I need to verify, or why this action makes sense."""

        try:
            return llm_caller(prompt)
        except Exception:
            return f"Proceeding with {step.tool} as planned."

    def _generate_alternative(
        self,
        failed_step: Any,
        error: str,
        prior_traces: list[ReasoningTrace],
        llm_caller: Any,
    ) -> Any | None:
        """Generate an alternative step when current one fails."""
        # Simplified: suggest a different tool for the same goal
        tool_alternatives = {
            "web_search": "fetch_url",
            "github_search_code": "github_read_file",
            "shell": "python_runner",
        }
        alt_tool = tool_alternatives.get(failed_step.tool)
        if alt_tool and alt_tool in self.tools.list_tools():  # type: ignore[attr-defined]
            from dataclasses import dataclass
            @dataclass
            class AltStep:
                id: int = failed_step.id
                description: str = f"Retry with {alt_tool} after {failed_step.tool} failed"
                tool: str = alt_tool
                args: dict = failed_step.args
            return AltStep()
        return None

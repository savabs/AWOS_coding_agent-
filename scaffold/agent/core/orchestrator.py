"""
core/orchestrator.py — Research → Plan → Execute → Synthesize pipeline.

This is the spine of the agent. It ties together:
  - Memory (what do we know?)
  - Tools (what can we do?)
  - Config (how should we behave?)

Pipeline stages (override individually):
  1. research()   — gather information about the goal
  2. plan()       — decompose goal into steps
  3. execute()    — run one step at a time, using tools
  4. synthesize() — combine results into a final answer

AWOS principle: The LLM is scaffolding. It plans and narrates.
The tools and math are the product. The orchestrator enforces this separation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..memory import MemoryStore
from ..tools import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)


@dataclass
class Step:
    """A single planned execution step."""

    id: int
    description: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    result: ToolResult | None = None
    done: bool = False


@dataclass
class Plan:
    """An ordered list of steps to achieve a goal."""

    goal: str
    steps: list[Step] = field(default_factory=list)

    def next_step(self) -> Step | None:
        return next((s for s in self.steps if not s.done), None)

    def is_complete(self) -> bool:
        return all(s.done for s in self.steps)

    def summary(self) -> str:
        done = sum(1 for s in self.steps if s.done)
        return f"Plan: {done}/{len(self.steps)} steps done — goal: {self.goal[:60]}"


class Orchestrator:
    """
    Runs the Research → Plan → Execute → Synthesize pipeline.

    Subclass and override individual stage methods to customize behavior.
    """

    def __init__(
        self,
        settings: Settings,
        memory: MemoryStore,
        tools: ToolRegistry,
    ) -> None:
        self.settings = settings
        self.memory = memory
        self.tools = tools

    # ── Stage 1: Research ──────────────────────────────────────────────────

    def research(self, goal: str) -> list[str]:
        """
        Gather information relevant to the goal.
        Returns a list of observation strings.

        Override to use actual tool calls or LLM queries.
        """
        logger.info("Research phase: goal=%s", goal)
        self.memory.working.set_goal(goal)
        self.memory.episodic.record("research_start", f"goal: {goal}")
        return [f"Researching: {goal}"]

    # ── Stage 2: Plan ──────────────────────────────────────────────────────

    def plan(self, goal: str, research_observations: list[str]) -> Plan:
        """
        Decompose goal into atomic steps.

        Override to use LLM-based planning or rule-based decomposition.
        Default: single-step plan that calls the first available tool.
        """
        logger.info("Plan phase")
        p = Plan(goal=goal)

        # Stub: single step that does nothing
        # Replace with LLM call or task decomposer
        if len(self.tools) > 0:
            first_tool = self.tools.list_tools()[0]
            p.steps.append(Step(id=1, description=f"Execute: {goal}", tool=first_tool["name"]))
        else:
            logger.warning("No tools registered — plan will have no steps")

        return p

    # ── Stage 3: Execute ───────────────────────────────────────────────────

    def execute(self, plan: Plan) -> list[ToolResult]:
        """
        Execute steps one at a time. Returns results list.

        Respects max_iterations from settings.
        """
        logger.info("Execute phase: %s", plan.summary())
        results = []

        iteration = 0
        while not plan.is_complete() and iteration < self.settings.max_iterations:
            step = plan.next_step()
            if step is None:
                break

            logger.info("Step %d: %s (tool=%s)", step.id, step.description, step.tool)
            self.memory.working.iteration = iteration
            self.memory.episodic.record("step_start", f"step {step.id}: {step.description}")

            result = self.tools.execute(step.tool, step.args)
            step.result = result
            step.done = True

            if result.success:
                self.memory.working.observe(result.text)
                self.memory.episodic.record("step_done", result.text[:200])
            else:
                logger.warning("Step %d failed: %s", step.id, result.error)
                self.memory.episodic.record("step_failed", result.error)

            results.append(result)
            iteration += 1

        if iteration >= self.settings.max_iterations:
            logger.warning("Max iterations (%d) reached", self.settings.max_iterations)

        return results

    # ── Stage 4: Synthesize ────────────────────────────────────────────────

    def synthesize(self, goal: str, plan: Plan, results: list[ToolResult]) -> str:
        """
        Combine execution results into a final answer.

        Override to use LLM synthesis or structured aggregation.
        """
        logger.info("Synthesize phase")
        succeeded = [r for r in results if r.success]
        failed = [r for r in results if not r.success]

        summary_lines = [
            f"Goal: {goal}",
            f"Steps: {len(plan.steps)} planned, {len(succeeded)} succeeded, {len(failed)} failed",
        ]
        for r in succeeded:
            summary_lines.append(f"  ✓ {r.text[:100]}")
        for r in failed:
            summary_lines.append(f"  ✗ {r.error[:100]}")

        return "\n".join(summary_lines)

    # ── Main entry point ───────────────────────────────────────────────────

    def run(self, goal: str) -> str:
        """Full Research → Plan → Execute → Synthesize pipeline."""
        logger.info("Orchestrator.run: %s", goal)

        observations = self.research(goal)
        plan = self.plan(goal, observations)
        results = self.execute(plan)
        answer = self.synthesize(goal, plan, results)

        self.memory.episodic.record("run_complete", answer[:200])
        return answer

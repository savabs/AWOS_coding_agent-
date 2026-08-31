"""
Cheap Planner: Uses OpenCode Go (DeepSeek V4 Flash) for planning.

Cost: ~$0.0001-0.0005 per planning call (vs $0.03-0.05 for Sonnet)
Accuracy: Still 90%+ for task decomposition when prompt is clear
Trade: Slightly less sophisticated reasoning, but 50-100x cheaper

Perfect for self-improvement loops where speed + cost matter more than perfection.
"""

import json
import os
from typing import Optional

from .fixed_provider import (FIXED_MODEL, create_fixed_client, fixed_api_key,
                             provider_extra_kwargs)


def _response_shape(message: object | None) -> str:
    """Classify completion structure without logging provider response content."""
    if message is None:
        return "unavailable"
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return "content"
    reasoning_content = getattr(message, "reasoning_content", None)
    if isinstance(reasoning_content, str) and reasoning_content.strip():
        return "reasoning_only"
    if getattr(message, "tool_calls", None):
        return "tool_call_only"
    return "empty"


class CheapPlanner:
    """Uses the fixed OpenCode Go model for planning."""

    def __init__(self, api_key: Optional[str] = None, model: str = FIXED_MODEL):
        """Initialize with OpenCode Go API.
        
        Args:
            api_key: OpenCode Go API key. Reads OPENCODE_GO_API_KEY.
            model: Ignored for the active product path; the fixed model is used.
        """
        self.api_key = api_key or fixed_api_key()
        self.client = create_fixed_client(self.api_key)
        self.model_name = FIXED_MODEL

    def plan(self, goal: str, codebase_context: dict, tracker=None, existing_goal=None) -> dict:
        """
        Break down a goal into atomic micro-tasks using OpenCode Go (DeepSeek V4 Flash).
        
        Args:
            goal: User's objective (e.g., "improve error handling")
            codebase_context: Project structure, modules, architecture
            tracker: Optional TokenTracker to record usage
            existing_goal: Optional existing goal graph (ignored by CheapPlanner)
        
        Returns:
            {
                "plan": [
                    {"task_id": 1, "file": "path", "action": "...", "complexity": "low"},
                    ...
                ],
                "reasoning": "Why this breakdown",
                "total_tasks": int
            }
        """

        modules = codebase_context.get("modules", "Unknown")
        architecture = codebase_context.get("architecture", "Unknown")
        files = codebase_context.get("files", [])[:10]
        symbols = codebase_context.get("symbols", [])
        symbols_str = "\n".join(symbols[:20]) if symbols else "(none)"
        explicit_paths = codebase_context.get("exploration_explicit_paths", [])
        explicit_symbols = codebase_context.get("exploration_explicit_symbols", [])
        snapshot_id = codebase_context.get("exploration_snapshot_id", "unknown")
        exploration = codebase_context.get("exploration", "")

        prompt = f"""You are a code improvement expert. Break down this goal into 2-4 atomic micro-tasks.

GOAL: {goal}

CODEBASE:
- Modules: {modules}
- Architecture: {architecture}
- Key files: {", ".join(files)}
- Key symbols:\n{symbols_str}
- Exploration snapshot: {snapshot_id}
- Explicit paths: {", ".join(explicit_paths) or "(none)"}
- Explicit symbols: {", ".join(explicit_symbols) or "(none)"}
- Exploration evidence:\n{exploration[:5000] or "(none)"}

RULES:
1. Each task changes ONE primary file only
2. Each task must use a resolvable path from the codebase or explicit exploration evidence
3. Each task must include an observable acceptance_check and evidence_ref
4. Tasks are simple enough for DeepSeek to handle
5. Order tasks so dependencies are handled first
6. Estimate complexity: low (minor change), medium (refactor), high (new feature)

RESPOND WITH ONLY JSON (no markdown, no text before/after):
{{
  "plan": [
    {{"task_id": 1, "file": "path/file.py", "action": "specific action", "acceptance_check": "pytest path/to/test.py", "evidence_ref": "snapshot:{snapshot_id}", "complexity": "low"}},
    {{"task_id": 2, "file": "...", "action": "...", "acceptance_check": "specific observable check", "evidence_ref": "snapshot:{snapshot_id}", "complexity": "low"}}
  ],
  "reasoning": "Brief explanation",
  "total_tasks": 2
}}"""

        response_text = ""
        response_shape = "unavailable"
        if tracker is not None and hasattr(tracker, "check_guard"):
            allowed, reason = tracker.check_guard(estimated_cost=0.01)
            if not allowed:
                raise RuntimeError(f"Trial budget guard stopped planner request: {reason}")
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                **provider_extra_kwargs(),
            )
            try:
                message = response.choices[0].message
            except (AttributeError, IndexError, TypeError):
                message = None
            response_shape = _response_shape(message)
            response_text = getattr(message, "content", "") or ""

            # Remove markdown if present
            if "```" in response_text:
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]

            # Parse JSON
            result = json.loads(response_text)

            # Validate structure
            if "plan" not in result or not isinstance(result["plan"], list):
                raise ValueError(f"Invalid plan structure: {result}")

            for task in result["plan"]:
                required = ["task_id", "file", "action", "complexity"]
                if not all(k in task for k in required):
                    raise ValueError(f"Task missing fields: {task}")

            # Record token usage if tracker provided
            if tracker and response.usage:
                input_tokens = response.usage.prompt_tokens or len(prompt) // 4
                output_tokens = response.usage.completion_tokens or len(response_text) // 4
                # DeepSeek V4 Flash pricing via OpenCode Go: $0.14 input, $0.28 output per 1M
                cost = (
                    (input_tokens  / 1_000_000) * 0.14 +
                    (output_tokens / 1_000_000) * 0.28
                )
                tracker.record(
                    request_type="planning",
                    model="DeepSeek V4 Flash (cheap planning)",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost=cost
                )

            return result

        except json.JSONDecodeError as e:
            raise ValueError(
                f"OpenCode Go ({self.model_name}) returned invalid JSON "
                f"(response_shape={response_shape}): Error: {e}"
            )
        except Exception as e:
            raise RuntimeError(
                f"OpenCode Go ({self.model_name}) planning failed: {str(e)}"
            )

    def refine_goal(self, vague_goal: str, codebase_context: dict) -> str:
        """Convert vague goal into specific, actionable goal.
        
        User: "make the agent better"
        → Agent: "improve error handling in orchestrator and add retry logic to worker"
        
        This runs quickly and cheaply on Gemini Flash.
        """

        prompt = f"""The user said: "{vague_goal}"

Given this codebase:
- Modules: {codebase_context.get('modules', 'unknown')}
- Architecture: {codebase_context.get('architecture', 'unknown')}

Suggest a SPECIFIC, actionable improvement goal. Be concise.
Respond with ONLY the refined goal, no explanation."""

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=256,
                temperature=0.3,
            )
            refined = response.choices[0].message.content or ""

            # Clean up if it has extra text
            if "Goal:" in refined:
                refined = refined.split("Goal:")[-1].strip()
            if refined.startswith("- "):
                refined = refined[2:]

            return refined
        except Exception:
            # Fallback: return original
            return vague_goal

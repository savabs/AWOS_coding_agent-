"""
Planner Module: Converts user goals into atomic JSON task blueprints.

Uses Sonnet for high-quality reasoning and structured output generation.
Each planning call costs ~$0.03-0.05 and produces a reusable execution plan.
"""

import json
import os
from anthropic import Anthropic
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)

_COMPLEX_SIGNALS = frozenset({
    "refactor", "redesign", "architecture", "system", "migrate",
    "overhaul", "restructure", "rewrite", "reorganise", "reorganize",
})
_SIMPLE_SIGNALS = frozenset({
    "add", "fix", "update", "rename", "remove", "delete",
    "create", "append", "insert", "change", "modify",
})
_MAX_SIMPLE_FILES = 3
_MAX_SIMPLE_GOAL_WORDS = 10


class Planner:
    """Uses Claude Sonnet to break down user goals into atomic micro-tasks."""
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize Planner with Anthropic API client."""
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        self.client = Anthropic(api_key=self.api_key)
        self.model = "claude-sonnet-4-6"
    
    def plan(self, goal: str, codebase_context: dict, tracker=None, existing_goal: Any = None) -> dict:
        """
        Break down a user goal into atomic micro-tasks.

        Args:
            goal: User's high-level objective (e.g., "add user authentication")
            codebase_context: Dict with project structure info (keys: "modules", "files", "architecture")
            tracker: Optional TokenTracker to record API usage
            existing_goal: Optional GoalGraph instance. Tasks whose action matches a
                           completed node description (Jaccard >= 0.5) are skipped.

        Returns:
            {
                "plan": [
                    {
                        "task_id": 1,
                        "file": "src/auth.py",
                        "action": "Add login function signature",
                        "complexity": "low"
                    },
                    ...
                ],
                "reasoning": "Why we break it down this way",
                "total_tasks": 3
            }
        """
        
        # Build context summary for Sonnet
        modules = codebase_context.get("modules", "Unknown")
        architecture = codebase_context.get("architecture", "Unknown")
        symbols = codebase_context.get("symbols", [])
        symbols_str = "\n".join(symbols[:30]) if symbols else "(none extracted)"
        
        # Stable system instructions — cached after first call (90% savings on repeat planning)
        system_instructions = """You are an expert software architect. Break down user goals into atomic micro-tasks that can each be executed independently by a junior AI model in 1-5 minutes.

CONSTRAINTS:
1. Each task must change ONLY ONE file
2. Each task must be simple enough for a junior model to handle with 50 lines of context
3. Tasks should be ordered so dependencies are handled first
4. Each task needs: file path, specific action, complexity level (low/medium/high)
5. Total tasks should be 2-5 for typical features
6. For EVERY task that creates or modifies a function/class, you MUST include a machine-enforceable contract

CONTRACT FIELDS (include whenever the task touches code — omit for pure documentation tasks):
- function_signature: exact Python signature the output must contain (e.g. "def verify_jwt(token: str) -> Optional[UserClaims]:")
- interface_contract: one-line description of how this function is called from outside
- constraints: list of behavior rules the implementation MUST follow
- must_not: list of things the implementation must NOT do (prevents hallucinated patterns)
- example_call: a concrete example of calling this function

OUTPUT FORMAT - REQUIRED JSON (no markdown, no extra text):
{
  "plan": [
    {
      "task_id": 1,
      "file": "path/to/file.py",
      "action": "specific action description",
      "complexity": "low|medium|high",
      "function_signature": "exact signature (optional)",
      "interface_contract": "how it's called (optional)",
      "constraints": ["rule 1", "rule 2"],
      "must_not": ["forbidden pattern 1"],
      "example_call": "user = foo(bar) (optional)"
    }
  ],
  "reasoning": "Why this decomposition works",
  "total_tasks": 2
}

Output ONLY valid JSON. No markdown, no explanation before or after."""

        # Variable user content — NOT cached (changes each request)
        user_prompt = f"""GOAL: {goal}

CODEBASE STRUCTURE:
Modules: {modules}
Architecture: {architecture}
Key symbols (file::class/def):
{symbols_str}"""
        
        # Call Sonnet with prompt caching on the stable system block
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1536,
            system=[
                {
                    "type": "text",
                    "text": system_instructions,
                    "cache_control": {"type": "ephemeral"}  # Cache this block — 90% off on repeat calls
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": user_prompt
                }
            ]
        )
        
        # Cache telemetry
        try:
            from .cache_telemetry import CacheTelemetryStore, extract_cache_stats_anthropic
            cache_store = CacheTelemetryStore()
            cache_event = extract_cache_stats_anthropic(
                response=response.model_dump(),
                component="planner",
                model=self.model,
            )
            cache_store.record(cache_event)
        except Exception:
            pass  # Don't fail planning if telemetry breaks
        
        # Extract response
        response_text = response.content[0].text.strip()
        
        # Parse JSON
        try:
            # Remove markdown if present
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            
            result = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Sonnet returned invalid JSON: {response_text[:200]}... Error: {e}")
        
        # Validate structure
        if "plan" not in result or not isinstance(result["plan"], list):
            raise ValueError(f"Invalid plan structure: {result}")

        for task in result["plan"]:
            required = ["task_id", "file", "action", "complexity"]
            if not all(k in task for k in required):
                raise ValueError(f"Task missing required fields: {task}")

            # Validate optional contract fields if present
            if "constraints" in task and not isinstance(task["constraints"], list):
                raise ValueError(f"Task 'constraints' must be a list: {task}")
            if "must_not" in task and not isinstance(task["must_not"], list):
                raise ValueError(f"Task 'must_not' must be a list: {task}")
            for opt in ("function_signature", "interface_contract", "example_call"):
                if opt in task and task[opt] is not None and not isinstance(task[opt], str):
                    raise ValueError(f"Task '{opt}' must be a string or None: {task}")
        
        # ── Phase 5C: skip tasks already completed in existing goal graph ──
        if existing_goal is not None:
            result["plan"] = self._filter_completed_tasks(result["plan"], existing_goal)
            result["total_tasks"] = len(result["plan"])

        # ── Phase 5D: decompose complex refactoring tasks into atomic sub-tasks ──
        result["plan"] = self._decompose_refactoring_tasks(result["plan"])
        result["total_tasks"] = len(result["plan"])

        # Record cost if tracker provided
        if tracker:
            # Estimate tokens (Sonnet ~11 input, ~27 output per 1k)
            # For simplicity, use response.usage if available
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens

            cost = (
                (input_tokens / 1_000_000) * 3 +   # Sonnet input: $3 per 1M
                (output_tokens / 1_000_000) * 15    # Sonnet output: $15 per 1M
            )

            tracker.record(
                request_type="planning",
                model="Claude Sonnet 3.5",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost
            )

        return result

    @staticmethod
    def _classify_goal_complexity(goal: str, codebase_context: dict) -> bool:
        """Return True if goal is simple (single-file, targeted), False if complex.

        Simple: short goal (<= _MAX_SIMPLE_GOAL_WORDS words), no complex signals,
                codebase has <= _MAX_SIMPLE_FILES files.
        Complex: contains refactor/redesign/architecture keywords, long goal,
                 or large codebase context.
        """
        words = goal.lower().split()
        if len(words) > _MAX_SIMPLE_GOAL_WORDS:
            return False
        files = codebase_context.get("files", [])
        if len(files) > _MAX_SIMPLE_FILES:
            return False
        word_set = set(words)
        if word_set & _COMPLEX_SIGNALS:
            return False
        return True

    @staticmethod
    def _decompose_refactoring_tasks(tasks: list) -> list:
        """
        Decompose complex refactoring tasks into atomic sub-tasks.
        
        Uses RefactoringDecomposer to intelligently break down:
          - "Add import + use" → [add import, use]
          - "Replace all X" → [replace first, replace rest]
        
        Returns expanded task list with atomic sub-tasks.
        """
        from .refactoring_decomposer import RefactoringDecomposer
        
        decomposer = RefactoringDecomposer()
        expanded_tasks = []
        task_id_counter = 1
        
        for task in tasks:
            if decomposer.should_decompose(task):
                # Decompose into atomic sub-tasks
                atomic_tasks = decomposer.decompose(task)
                
                for atomic in atomic_tasks:
                    # Convert AtomicTask to dict format
                    sub_task = {
                        "task_id": task_id_counter,
                        "action": atomic.action,
                        "file": atomic.file,
                        "complexity": atomic.complexity,
                    }
                    
                    # Add optional fields
                    if hasattr(task, 'constraints') and task.get('constraints'):
                        sub_task["constraints"] = task["constraints"]
                    if hasattr(task, 'must_not') and task.get('must_not'):
                        sub_task["must_not"] = task["must_not"]
                    
                    expanded_tasks.append(sub_task)
                    task_id_counter += 1
            else:
                # Keep original task
                task["task_id"] = task_id_counter
                expanded_tasks.append(task)
                task_id_counter += 1
        
        return expanded_tasks

    @staticmethod
    def _filter_completed_tasks(tasks: list, existing_goal: Any) -> list:
        """Remove tasks whose action matches a completed GoalNode description (Jaccard >= 0.5)."""
        # Duck-type: existing_goal must have a .nodes dict of nodes with .status and .description
        nodes = getattr(existing_goal, "nodes", {})
        completed_descs = [
            n.description for n in nodes.values()
            if getattr(n, "status", "") == "completed"
        ]
        if not completed_descs:
            return tasks

        filtered = []
        for task in tasks:
            action = task.get("action", "")
            skip = False
            for desc in completed_descs:
                if _jaccard_overlap(action, desc) >= 0.5:
                    skip = True
                    logger.info("[Planner] skipping completed task: %s", action[:60])
                    break
            if not skip:
                filtered.append(task)
        return filtered


# ── Planner singleton ──────────────────────────────────────────────────────────

_planner_instance: Optional["Planner"] = None


def get_planner(api_key: Optional[str] = None) -> "Planner":
    """Return the module-level Planner singleton, creating it on first call."""
    global _planner_instance
    if _planner_instance is None:
        _planner_instance = Planner(api_key=api_key)
    return _planner_instance


def _tokenise(text: str) -> set:
    """Lowercase, strip punctuation, split on non-alphanum, remove stop words."""
    import re
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = text.split()
    stop = {
        "a", "an", "the", "and", "or", "to", "in", "of", "for", "with",
        "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
        "do", "does", "did", "will", "would", "could", "should", "may", "might",
        "can", "must", "shall", "this", "that", "these", "those",
    }
    return {t for t in tokens if t not in stop and len(t) > 1}


def _jaccard_overlap(a: str, b: str) -> float:
    """Jaccard similarity between two strings."""
    set_a = _tokenise(a)
    set_b = _tokenise(b)
    if not set_a or not set_b:
        return 0.0
    inter = set_a & set_b
    union = set_a | set_b
    return len(inter) / len(union) if union else 0.0

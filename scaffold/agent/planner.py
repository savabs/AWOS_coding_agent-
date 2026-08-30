"""
Planner Module: Converts user goals into atomic JSON task blueprints.

Uses Qwen through OpenCode Go for high-quality project reasoning and native graph generation.
Atomic execution remains on the cheaper DeepSeek Pro worker path.
"""

import json
import logging
import os
import time
from typing import Any, Optional

try:
    from .fixed_provider import PLANNER_MODEL, create_fixed_client
    from .model_call_telemetry import extract_optional_usage, record_model_call
    from .planner_graph_contract import normalize_planner_response
except ImportError:
    from fixed_provider import PLANNER_MODEL, create_fixed_client
    from model_call_telemetry import extract_optional_usage, record_model_call
    from planner_graph_contract import normalize_planner_response

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
    """Use OpenCode Go Qwen for project-level execution-graph planning."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the advanced Planner through the fixed OpenCode Go client."""
        self.api_key = api_key or os.getenv("OPENCODE_GO_API_KEY")
        self.client = create_fixed_client(self.api_key)
        self.model = PLANNER_MODEL
    
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
        
        # Build context summary for the advanced Qwen Planner.
        modules = codebase_context.get("modules", "Unknown")
        architecture = codebase_context.get("architecture", "Unknown")
        symbols = codebase_context.get("symbols", [])
        symbols_str = "\n".join(symbols[:30]) if symbols else "(none extracted)"
        
        # Stable system instructions — cached after first call (90% savings on repeat planning)
        system_instructions = """You are an expert software architect. Design a complete typed execution graph for the user's project goal. The graph will be executed by cheap atomic task solvers, so every edit node must be narrow, grounded, and independently understandable.

CONSTRAINTS:
1. Each edit or recovery node must change ONLY ONE file
2. Each edit node must be simple enough for a cheap model to handle with 50 lines of context
3. Dependencies must express the real project order; independent branches should remain parallel-safe
4. Use node types explore, edit, test, barrier, review, recovery, and final_verify
5. Test nodes require an acceptance_check; barrier and final_verify nodes are not parallel-safe
6. Every edit/recovery node requires exactly one output file
7. Every node requires all runtime fields from the requested JSON schema; the typed contract fields below are optional and must be emitted only for code-changing nodes
8. For EVERY edit or recovery node that creates or modifies a function/class, include the typed contract fields below

PLANNER METADATA (include inside the planner_metadata object whenever the task touches code; omit for pure documentation tasks):
- function_signature: exact Python signature the output must contain
- interface_contract: one-line description of how this function is called from outside
- constraints: list of behavior rules the implementation MUST follow
- must_not: list of things the implementation must NOT do
- example_call: a concrete example of calling this function
- You may add other bounded advisory metadata keys when useful. Do not add new execution fields, node types, permissions, commands, or dependency semantics.

OUTPUT FORMAT - REQUIRED JSON (no markdown, no extra text):
{
  "execution_graph": {
    "graph_version": "1",
    "nodes": [
      {
        "node_id": "edit_auth_service",
        "type": "edit",
        "description": "Implement one narrowly scoped change",
        "depends_on": [],
        "input_files": ["src/auth.py"],
        "output_files": ["src/auth.py"],
        "acceptance_check": "pytest tests/test_auth.py",
        "evidence_ref": null,
        "parallel_safe": true,
        "retry_limit": 1,
        "complexity": "medium",
        "planner_metadata": {
          "function_signature": "def verify_jwt(token: str) -> Optional[UserClaims]:",
          "interface_contract": "Callable from the request handler with an Authorization header value.",
          "constraints": ["Raise AuthError for expired tokens"],
          "must_not": ["Do not invent new error types"],
          "example_call": "verify_jwt(request.headers['Authorization'])",
          "future_advisory_hint": "Optional bounded guidance; never an execution instruction"
        }
      }
    ]
  },
  "reasoning": "Explain the project decomposition briefly"
}

Output ONLY valid JSON. No markdown, no explanation before or after."""

        # Variable user content — NOT cached (changes each request)
        user_prompt = f"""GOAL: {goal}

CODEBASE STRUCTURE:
Modules: {modules}
Architecture: {architecture}
Key symbols (file::class/def):
{symbols_str}"""
        
        # Call Qwen through the OpenCode Go OpenAI-compatible client.
        started_at = time.perf_counter()
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=4096,
            messages=[
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        usage = getattr(response, "usage", None)
        record_model_call(
            provider="opencode",
            model=self.model,
            request_type="planner",
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            **extract_optional_usage(usage),
            messages=[
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": user_prompt},
            ],
            started_at=started_at,
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
        
        # Extract the OpenAI-compatible OpenCode Go message content.
        try:
            message = response.choices[0].message
        except (AttributeError, IndexError, TypeError):
            message = None
        response_text = getattr(message, "content", "") or ""
        if not response_text.strip():
            raise ValueError("OpenCode Go returned empty Planner response content")
        response_text = response_text.strip()
        
        # Parse JSON
        try:
            # Remove markdown if present
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            
            result = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"OpenCode Go ({self.model}) returned invalid JSON: "
                f"{response_text[:200]}... Error: {e}"
            )
        
        # Validate and normalize native graph output. Legacy task-list responses
        # remain accepted for provider compatibility and are wrapped deterministically.
        native_graph_response = "execution_graph" in result
        try:
            result = normalize_planner_response(result)
        except ValueError as exc:
            raise ValueError(f"Invalid execution graph structure: {exc}") from exc

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

        # ── Phase 5D: decompose legacy task-list responses only ──
        # Native graph node IDs are canonical and must not be renumbered by the
        # compatibility refactoring post-processor.
        if not native_graph_response:
            result["plan"] = self._decompose_refactoring_tasks(result["plan"])
            result["total_tasks"] = len(result["plan"])

        # Record cost if tracker provided
        if tracker:
            # Qwen3.7 Plus pricing metadata from the OpenCode Go model registry.
            usage = getattr(response, "usage", None)
            input_tokens = int(
                getattr(usage, "prompt_tokens", None)
                or getattr(usage, "input_tokens", None)
                or len(user_prompt) // 4
            )
            output_tokens = int(
                getattr(usage, "completion_tokens", None)
                or getattr(usage, "output_tokens", None)
                or len(response_text) // 4
            )
            cost = (
                (input_tokens / 1_000_000) * 0.40
                + (output_tokens / 1_000_000) * 1.60
            )
            tracker.record(
                request_type="planning",
                model=f"OpenCode Go {self.model}",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
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

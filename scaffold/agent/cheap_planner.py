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

try:
    from .providers import (
        REASONING_OFF, chat_client, openrouter_key, planner_model, planner_reasoning,
    )
except ImportError:
    from providers import (
        REASONING_OFF, chat_client, openrouter_key, planner_model, planner_reasoning,
    )
try:  # one log line per model call, for scripts/eval_health.py
    from .llm_call_log import record_error, record_response
except ImportError:
    from llm_call_log import record_error, record_response

#: CheapPlanner's model when AWOS_PLANNER_MODEL is unset. Reached through
#: OpenRouter when OPENROUTER_API_KEY is set (docs/specs/openrouter_only_spec.md).
DEFAULT_CHEAP_PLANNER_MODEL = "qwen3.7-plus"

#: Output budget for a plan call. The JSON itself is ~200–600 tokens; the rest
#: is headroom for a reasoning model's hidden thinking. Only used tokens bill.
PLAN_MAX_TOKENS = 8192


class CheapPlanner:
    """Uses OpenCode Go (DeepSeek V4 Flash) for budget-conscious planning."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """Initialize the planner client (OpenRouter when its key is set).

        Args:
            api_key: OpenCode Go API key. Reads OPENCODE_GO_API_KEY.
            model: Model to use. Default: AWOS_PLANNER_MODEL, else qwen3.7-plus.
        """
        self.api_key = api_key or os.getenv("OPENCODE_GO_API_KEY")
        opencode_base = os.getenv("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1")
        self.client = chat_client(self.api_key, opencode_base)
        if self.client is None:
            raise ValueError(
                "No planner key found. Set OPENROUTER_API_KEY (or OPENCODE_GO_API_KEY) in .env"
            )
        self.model_name = model or planner_model(DEFAULT_CHEAP_PLANNER_MODEL)
        # The `reasoning` field is OpenRouter's; a direct provider may reject it.
        self._via_openrouter = bool(openrouter_key())

    def _plan_request(self, prompt: str, reasoning: Optional[dict]) -> dict:
        kwargs = dict(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=PLAN_MAX_TOKENS,
            temperature=0.3,
        )
        if getattr(self, "_via_openrouter", False) and reasoning is not None:
            kwargs["extra_body"] = {"reasoning": reasoning}
        return kwargs

    def _record(self, tracker, response, prompt: str, response_text: str) -> None:
        if not tracker or not getattr(response, "usage", None):
            return
        input_tokens = response.usage.prompt_tokens or len(prompt) // 4
        output_tokens = response.usage.completion_tokens or len(response_text) // 4
        # DeepSeek V4 Flash pricing via OpenCode Go: $0.14 input, $0.28 output per 1M
        cost = (
            (input_tokens  / 1_000_000) * 0.14 +
            (output_tokens / 1_000_000) * 0.28
        )
        tracker.record(
            request_type="planning",
            model=f"{self.model_name} (planning)",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost
        )

    def _complete_plan(self, prompt: str, tracker=None) -> str:
        """The model's plan text; retries once without reasoning if cut short.

        A reasoning model can spend its output budget thinking and return ""
        or a lone space (finish_reason "length"). Such a reply never parses,
        and the orchestrator's fallback used to repeat the identical call.
        Instead, ask once more with reasoning off (measured ~6 s, ~200
        tokens). Still empty → a clear ValueError naming the cause.
        """
        attempts = [planner_reasoning()]
        if getattr(self, "_via_openrouter", False) and attempts[0] != REASONING_OFF:
            attempts.append(dict(REASONING_OFF))
        text, finish = "", None
        for attempt, reasoning in enumerate(attempts, 1):
            try:
                response = self.client.chat.completions.create(
                    **self._plan_request(prompt, reasoning)
                )
            except Exception as exc:
                record_error("planner", self.model_name, exc, attempt=attempt, final=True)
                raise
            choice = response.choices[0]
            text = choice.message.content or ""
            finish = getattr(choice, "finish_reason", None)
            self._record(tracker, response, prompt, text)
            record_response(
                "planner", self.model_name, response, visible_chars=len(text), attempt=attempt,
                final=(bool(text.strip()) and finish != "length") or attempt == len(attempts))
            if text.strip() and finish != "length":
                return text
        if text.strip():
            return text  # truncated but non-empty: the JSON parser reports it
        raise ValueError(
            f"planner model {self.model_name} returned an empty reply "
            f"(finish_reason={finish!r}) after {len(attempts)} attempt(s)"
        )

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
        # The orchestrator already caps this at 30; cutting to 10 hid
        # ledger/summary.py from a "monthly summary" goal.
        files = codebase_context.get("files", [])[:30]
        symbols = codebase_context.get("symbols", [])
        symbols_str = "\n".join(symbols[:20]) if symbols else "(none)"

        # You see file and symbol names, not code. A planner that guesses
        # mechanisms ("lru_cache parse_date", "raise click errors" in an
        # argparse app) sends the executor after the wrong thing, while the
        # executor can read the code and measure behaviour in its sandbox.
        # So tasks carry outcomes and checks; the executor picks the how.
        prompt = f"""You are a code improvement expert. Break down this goal into 1-4 atomic micro-tasks.

GOAL: {goal}

CODEBASE:
- Modules: {modules}
- Architecture: {architecture}
- Key files: {", ".join(files)}
- Key symbols:\n{symbols_str}

WHO EXECUTES: an agent that reads the code, edits files, runs the tests, and
can run arbitrary commands in a sandbox (run_command) to reproduce, time,
profile and try behaviour. You have only seen the names above, not the code.

RULES:
1. Each task names ONE primary file in "file". The task may touch other files
   only when rule 10 applies.
2. Tasks are simple enough for DeepSeek to handle
3. Order tasks so dependencies are handled first
4. Estimate complexity: low (minor change), medium (refactor), high (new feature)
5. A fix in one place is ONE task. Do not split one fix into "prepare",
   "update", "verify" or "review" steps — the executor reads, edits and runs
   the tests itself.
6. Never make "write a failing test" its own task: reproducing a bug and
   fixing it belong in the same task.
7. Existing tests are the contract. Never plan to change their assertions to
   make them pass.
8. State OUTCOMES and acceptance, not mechanisms: each "action" says what must
   be true when the task is done and how the tests or an observable behaviour
   will show it (the executor runs the test suite itself; do not prescribe
   manual commands). Never name a library, function, framework or
   technique that is not shown in the CODEBASE section above — the executor
   reads the code and chooses how.
9. Performance goals ("faster", "slow", "takes forever", "Nx"): plan exactly ONE
   task that says to measure first (time and profile the slow path with
   run_command), fix the biggest measured costs, re-measure against the target,
   and keep every result identical to before. Never split performance work by
   guessed hotspot.
10. Goals that touch many places (migrations, renames, "everywhere", "all"):
   say so in the task — "find every place that ... — search the whole
   codebase" — instead of listing guessed files. "file" is still ONE real
   path from the Key files list, the best place to start; never leave it
   empty or write a placeholder such as "multiple files".
11. Carry every explicit requirement of the goal into the tasks VERBATIM:
   flags and option names, error behaviour, exit codes, boundaries
   (inclusive/exclusive), formats, and "unchanged when ..." guarantees. Every
   requirement the user stated must appear in at least one task's "action".

RESPOND WITH ONLY JSON (no markdown, no text before/after). One task per
independent change; add more entries only for genuinely separate changes:
{{
  "plan": [
    {{"task_id": 1, "file": "path/file.py", "action": "outcome to reach and how to check it", "complexity": "low"}}
  ],
  "reasoning": "Brief explanation",
  "total_tasks": 1
}}"""

        response_text = ""
        try:
            # Every call (a retry too) is billed to the tracker as it happens.
            response_text = self._complete_plan(prompt, tracker)

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

            return result

        except json.JSONDecodeError as e:
            raise ValueError(
                f"planner model {self.model_name} returned invalid JSON: "
                f"{response_text[:300]!r}... Error: {e}"
            )
        except Exception as e:
            raise RuntimeError(f"planner model {self.model_name} failed: {e}")

    def refine_goal(self, vague_goal: str, codebase_context: dict) -> str:
        """Convert vague goal into specific, actionable goal.
        
        User: "make the agent better"
        → Agent: "improve error handling in orchestrator and add retry logic to worker"
        
        This runs quickly and cheaply on the planner model.
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
                # Reasoning models spend tokens thinking before answering;
                # at 256 the answer could be cut off entirely.
                max_tokens=1024,
                temperature=0.3,
            )
            refined = response.choices[0].message.content or ""

            # Clean up if it has extra text
            if "Goal:" in refined:
                refined = refined.split("Goal:")[-1].strip()
            if refined.startswith("- "):
                refined = refined[2:]

            # An empty reply must not erase the user's goal.
            return refined.strip() or vague_goal
        except Exception:
            # Fallback: return original
            return vague_goal

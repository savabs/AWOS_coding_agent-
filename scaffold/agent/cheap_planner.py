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

from openai import OpenAI


class CheapPlanner:
    """Uses OpenCode Go (DeepSeek V4 Flash) for budget-conscious planning."""

    def __init__(self, api_key: Optional[str] = None, model: str = "qwen3.7-plus"):
        """Initialize with OpenCode Go API.
        
        Args:
            api_key: OpenCode Go API key. Reads OPENCODE_GO_API_KEY.
            model: Model to use (qwen3.7-plus for reasoning quality)
        """
        self.api_key = api_key or os.getenv("OPENCODE_GO_API_KEY")
        if not self.api_key:
            raise ValueError(
                "No OpenCode Go key found. Set OPENCODE_GO_API_KEY in .env"
            )

        opencode_base = os.getenv("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1")
        self.client = OpenAI(api_key=self.api_key, base_url=opencode_base)
        self.model_name = model

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

        prompt = f"""You are a code improvement expert. Break down this goal into 2-4 atomic micro-tasks.

GOAL: {goal}

CODEBASE:
- Modules: {modules}
- Architecture: {architecture}
- Key files: {", ".join(files)}
- Key symbols:\n{symbols_str}

RULES:
1. Each task changes ONE file only
2. Tasks are simple enough for DeepSeek to handle
3. Order tasks so dependencies are handled first
4. Estimate complexity: low (minor change), medium (refactor), high (new feature)

RESPOND WITH ONLY JSON (no markdown, no text before/after):
{{
  "plan": [
    {{"task_id": 1, "file": "path/file.py", "action": "specific action", "complexity": "low"}},
    {{"task_id": 2, "file": "...", "action": "...", "complexity": "..."}}
  ],
  "reasoning": "Brief explanation",
  "total_tasks": 2
}}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,  # Enough for both reasoning and the JSON response
                temperature=0.3,
            )
            response_text = response.choices[0].message.content or ""

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
            raise ValueError(f"Gemini returned invalid JSON: {response_text[:300]}... Error: {e}")
        except Exception as e:
            raise RuntimeError(f"Gemini planning failed: {str(e)}")

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

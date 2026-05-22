"""
Cheap Planner: Uses Gemini Flash or Haiku for planning, NOT Sonnet.

Cost: ~$0.0005-0.001 per planning call (vs $0.03-0.05 for Sonnet)
Accuracy: Still 90%+ for task decomposition when prompt is clear
Trade: Slightly less sophisticated reasoning, but 50-100x cheaper

Perfect for self-improvement loops where speed + cost matter more than perfection.
"""

import json
import os
import re
from typing import Optional
from google import genai


class CheapPlanner:
    """Uses Gemini 2.5 Flash for budget-conscious planning."""
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.5-flash"):
        """Initialize with Google Gemini API (google.genai SDK).
        
        Args:
            api_key: Gemini/Google API key. Reads GEMINI_API_KEY then GOOGLE_API_KEY.
            model: Model to use (gemini-2.5-flash recommended)
        """
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "No Gemini key found. Set GEMINI_API_KEY in .env "
                "(get free key from https://aistudio.google.com/apikey)"
            )
        
        # google.genai SDK picks GOOGLE_API_KEY over our explicit key when both are set.
        # Temporarily hide it so the SDK uses our explicit api_key parameter.
        _shadow = os.environ.pop("GOOGLE_API_KEY", None)
        self.client = genai.Client(api_key=self.api_key)
        if _shadow is not None:
            os.environ["GOOGLE_API_KEY"] = _shadow
        self.model_name = model
    
    def plan(self, goal: str, codebase_context: dict, tracker=None) -> dict:
        """
        Break down a goal into atomic micro-tasks using Gemini Flash.
        
        Args:
            goal: User's objective (e.g., "improve error handling")
            codebase_context: Project structure, modules, architecture
            tracker: Optional TokenTracker to record usage
        
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
            response = self.client.models.generate_content(
                model=self.model_name, contents=prompt
            )
            response_text = response.text.strip()
            
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
            if tracker:
                meta = response.usage_metadata
                input_tokens  = meta.prompt_token_count if meta else len(prompt) // 4
                output_tokens = meta.candidates_token_count if meta else len(response_text) // 4
                # Gemini 2.5 Flash pricing: $0.15 input, $0.60 output per 1M
                cost = (
                    (input_tokens  / 1_000_000) * 0.15 +
                    (output_tokens / 1_000_000) * 0.60
                )
                tracker.record(
                    request_type="planning",
                    model="Gemini 2.5 Flash (cheap planning)",
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
            response = self.client.models.generate_content(
                model=self.model_name, contents=prompt
            )
            refined = response.text.strip()
            
            # Clean up if it has extra text
            if "Goal:" in refined:
                refined = refined.split("Goal:")[-1].strip()
            if refined.startswith("- "):
                refined = refined[2:]
            
            return refined
        except Exception as e:
            # Fallback: return original
            return vague_goal

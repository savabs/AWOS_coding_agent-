"""
goal_clarifier.py — Goal Clarification Layer (Phase 1 & 3)

Detects ambiguous user goals and asks targeted clarifying questions BEFORE planning.
Implements best practices from Devin, Cursor Composer, and OpenAI o3.

Architecture:
1. Cheap model (DeepSeek) detects ambiguity (Phase 3)
2. If ambiguous → ask clarifying questions (Phase 1)
3. Return clarified goal with structured context

Research: docs/research/goal_clarification_best_practices.md
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ClarifiedGoal:
    """Structured goal with all required context for planning."""
    original_goal: str
    clarified_goal: str
    output_specification: Optional[str] = None  # "Create file X" or "Modify file Y"
    acceptance_criteria: Optional[str] = None   # "Tests pass", "Code runs", etc.
    scope_boundaries: Optional[str] = None      # Files to touch, files NOT to touch
    environment_hints: Optional[str] = None     # Install/test/build commands
    is_ambiguous: bool = False
    clarifying_questions: list[str] = None
    
    def to_enriched_prompt(self) -> str:
        """Convert to enriched prompt for planner."""
        parts = [f"Goal: {self.clarified_goal}"]
        
        if self.output_specification:
            parts.append(f"\nOutput: {self.output_specification}")
        
        if self.acceptance_criteria:
            parts.append(f"\nAcceptance Criteria: {self.acceptance_criteria}")
        
        if self.scope_boundaries:
            parts.append(f"\nScope: {self.scope_boundaries}")
        
        if self.environment_hints:
            parts.append(f"\nEnvironment: {self.environment_hints}")
        
        return "\n".join(parts)


class GoalClarifier:
    """
    Detects ambiguous goals and asks clarifying questions.
    
    Flow:
        1. Use cheap model (DeepSeek) to detect ambiguity
        2. If ambiguous, generate targeted questions
        3. Return structured goal for planning
    """
    
    def __init__(self):
        self.ambiguity_detector = AmbiguityDetector()
        self.question_generator = QuestionGenerator()
    
    def clarify(self, goal: str, interactive: bool = True) -> ClarifiedGoal:
        """
        Main entry point: detect ambiguity and clarify if needed.
        
        Args:
            goal: User's original goal
            interactive: If True, ask user questions. If False, return with questions for external handling.
        
        Returns:
            ClarifiedGoal with all required context
        """
        # Phase 3: Detect ambiguity using cheap model
        is_ambiguous, missing_elements = self.ambiguity_detector.detect(goal)
        
        if not is_ambiguous:
            logger.info("[GoalClarifier] Goal is clear, proceeding")
            return ClarifiedGoal(
                original_goal=goal,
                clarified_goal=goal,
                is_ambiguous=False
            )
        
        # Phase 1: Generate targeted questions
        logger.info("[GoalClarifier] Goal is ambiguous, missing: %s", missing_elements)
        questions = self.question_generator.generate(goal, missing_elements)
        
        if not interactive:
            # Return questions for external handling (e.g., async chat)
            return ClarifiedGoal(
                original_goal=goal,
                clarified_goal=goal,
                is_ambiguous=True,
                clarifying_questions=questions
            )
        
        # Interactive mode: ask user now
        print("\n" + "=" * 60)
        print("GOAL CLARIFICATION NEEDED")
        print("=" * 60)
        print(f"\nYour goal: {goal}")
        print(f"\nBefore I start, I need to clarify {len(questions)} thing(s):\n")
        
        answers = {}
        for i, q in enumerate(questions, 1):
            print(f"{i}. {q['question']}")
            answer = input(f"   Answer: ").strip()
            answers[q['element']] = answer
        
        print("=" * 60)
        print("\nThank you! Starting work with clarified goal...\n")
        
        # Build clarified goal
        return self._build_clarified_goal(goal, answers)
    
    def _build_clarified_goal(self, original_goal: str, answers: dict) -> ClarifiedGoal:
        """Convert answers into structured ClarifiedGoal."""
        enriched_goal = original_goal
        
        # Enhance goal with answers
        if 'output' in answers and answers['output']:
            enriched_goal += f". Output: {answers['output']}"
        
        return ClarifiedGoal(
            original_goal=original_goal,
            clarified_goal=enriched_goal,
            output_specification=answers.get('output'),
            acceptance_criteria=answers.get('acceptance'),
            scope_boundaries=answers.get('scope'),
            environment_hints=answers.get('environment'),
            is_ambiguous=False  # Now clarified
        )


class AmbiguityDetector:
    """
    Phase 3: Use cheap model (DeepSeek $0.14/M) to detect goal ambiguity.
    
    Checks for missing:
    - Output specification (what file to create/modify?)
    - Acceptance criteria (how do we know it's done?)
    - Scope boundaries (what can/cannot be touched?)
    """
    
    AMBIGUITY_PROMPT = """You are a goal analyzer. Check if this goal is clear enough to execute.

A CLEAR goal must have:
1. Output specification: Explicit file to create or modify
   Example: "Create docs/research/audit.md" or "Modify scaffold/agent/planner.py"
   
2. Acceptance criteria: How to verify it's done
   Example: "Tests pass", "Code runs without errors", "Doc is readable"
   
3. Scope boundaries: What can/cannot be touched
   Example: "Only touch planner.py, don't edit worker.py"

Goal: {goal}

Analyze this goal and respond in this format:

IS_CLEAR: yes/no

MISSING_ELEMENTS:
- output: [missing/present] [explanation]
- acceptance: [missing/present] [explanation]
- scope: [missing/present] [explanation]

If IS_CLEAR is "no", list what's missing and why."""
    
    def detect(self, goal: str) -> tuple[bool, list[str]]:
        """
        Detect if goal is ambiguous.
        
        Returns:
            (is_ambiguous: bool, missing_elements: list[str])
        """
        try:
            import litellm
            
            # Use DeepSeek for cheap ambiguity detection ($0.14/M vs $3/M)
            response = litellm.completion(
                model="deepseek/deepseek-chat",
                messages=[{"role": "user", "content": self.AMBIGUITY_PROMPT.format(goal=goal)}],
                max_tokens=500,
                temperature=0.0,  # Deterministic
            )
            
            response_text = response.choices[0].message.content
            
            # Parse response
            is_clear = "IS_CLEAR: yes" in response_text
            missing = []
            
            if "output: missing" in response_text.lower():
                missing.append("output")
            if "acceptance: missing" in response_text.lower():
                missing.append("acceptance")
            if "scope: missing" in response_text.lower():
                missing.append("scope")
            
            logger.info(f"[AmbiguityDetector] Clear={is_clear}, Missing={missing}")
            logger.debug(f"[AmbiguityDetector] Response: {response_text}")
            
            return (not is_clear, missing)
            
        except Exception as exc:
            # Fallback: simple heuristic
            logger.warning(f"[AmbiguityDetector] Model call failed ({exc}), using heuristic")
            return self._heuristic_detect(goal)
    
    def _heuristic_detect(self, goal: str) -> tuple[bool, list[str]]:
        """Fallback heuristic for ambiguity detection."""
        missing = []
        goal_lower = goal.lower()
        
        # Check for output specification
        has_output = any([
            "create file" in goal_lower,
            "modify file" in goal_lower,
            "write to" in goal_lower,
            "output:" in goal_lower,
            ".md" in goal_lower,
            ".py" in goal_lower,
        ])
        if not has_output:
            missing.append("output")
        
        # Check for acceptance criteria
        has_acceptance = any([
            "test" in goal_lower and "pass" in goal_lower,
            "run" in goal_lower and ("success" in goal_lower or "without error" in goal_lower),
            "verify" in goal_lower,
            "acceptance:" in goal_lower,
        ])
        if not has_acceptance:
            missing.append("acceptance")
        
        # Scope is usually missing unless explicitly stated
        has_scope = any([
            "only touch" in goal_lower,
            "don't edit" in goal_lower,
            "scope:" in goal_lower,
        ])
        if not has_scope:
            missing.append("scope")
        
        is_ambiguous = len(missing) >= 2  # Ambiguous if missing 2+ elements
        return (is_ambiguous, missing)


class QuestionGenerator:
    """
    Phase 1: Generate targeted clarifying questions based on missing elements.
    """
    
    QUESTION_TEMPLATES = {
        "output": {
            "question": "What file should I create or modify? (e.g., docs/research/audit.md)",
            "element": "output"
        },
        "acceptance": {
            "question": "How will we know the task is complete? (e.g., 'Tests pass', 'Code runs')",
            "element": "acceptance"
        },
        "scope": {
            "question": "What files can I touch? What files should I NOT touch? (e.g., 'Only planner.py, not worker.py')",
            "element": "scope"
        },
        "environment": {
            "question": "Are there special environment setup steps? (e.g., 'pip install X', 'npm test')",
            "element": "environment"
        }
    }
    
    def generate(self, goal: str, missing_elements: list[str]) -> list[dict]:
        """
        Generate targeted questions for missing elements.
        
        Returns:
            List of {question: str, element: str} dicts
        """
        questions = []
        
        for element in missing_elements:
            if element in self.QUESTION_TEMPLATES:
                questions.append(self.QUESTION_TEMPLATES[element])
        
        return questions


# ── Convenience functions ──

def clarify_goal(goal: str, interactive: bool = True) -> ClarifiedGoal:
    """
    Convenience function to clarify a goal.
    
    Usage:
        clarified = clarify_goal("research search+planning")
        # If ambiguous, user is prompted for clarification
        enriched_prompt = clarified.to_enriched_prompt()
        # Pass to planner
    """
    clarifier = GoalClarifier()
    return clarifier.clarify(goal, interactive=interactive)

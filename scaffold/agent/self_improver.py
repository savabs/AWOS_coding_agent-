"""
Self-Improver: Orchestrates self-improvement on the AWOS Coding Agent itself.

Detects self-improvement requests, breaks them down with cheap planning,
executes with DeepSeek workers, validates locally.

Cost: ~$0.01-0.05 per self-improvement task (vs $0.50+ manual)
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict
from dataclasses import dataclass

from orchestrator import Orchestrator
from cheap_planner import CheapPlanner


@dataclass
class SelfImprovementRequest:
    """Parsed self-improvement request"""
    goal: str  # What to improve
    refined_goal: str  # Specific actionable goal
    is_self_improvement: bool  # True if detected as self-improvement
    confidence: float  # How sure we are (0-1)


class SelfImprover:
    """Detects and executes self-improvement requests."""
    
    # Only trigger when user is clearly talking about the agent ITSELF.
    # Generic verbs like "fix", "add", "improve" alone must NOT trigger.
    SELF_REFS = [
        "yourself", "the agent", "this agent", "awos", "coding agent",
        "your code", "your error", "your performance", "your handling",
        "your tests", "your logs", "your speed", "your cost", "your memory",
        "you faster", "you better", "you smarter", "you cheaper", "you more",
        "make you", "improve you", "fix you", "upgrade you",
        "the codebase", "this codebase", "scaffold/agent",
    ]
    
    IMPROVEMENT_VERBS = [
        "improve", "enhance", "optimize", "upgrade", "speed up", "make faster",
        "make better", "make smarter", "make cheaper", "refactor",
    ]
    
    def __init__(self, tracker=None):
        """Initialize Self-Improver.
        
        Args:
            tracker: Optional TokenTracker for cost monitoring
        """
        self.tracker = tracker
        
        # Initialize cheap planner
        try:
            self.cheap_planner = CheapPlanner()
        except ValueError:
            print("⚠️  GOOGLE_API_KEY not set. Self-improvement disabled.")
            print("   Get free key from: https://aistudio.google.com/app/apikey")
            self.cheap_planner = None
        
        # Initialize orchestrator (uses existing DeepSeek setup)
        self.orchestrator = Orchestrator(tracker=tracker)
    
    def detect_self_improvement(self, user_input: str, confidence_threshold: float = 0.8) -> SelfImprovementRequest:
        """Detect if user wants to improve THIS agent.
        
        Requires explicit self-reference ("yourself", "the agent", "awos", etc.).
        Generic words like "fix", "add", "improve" alone do NOT trigger this.
        """
        user_lower = user_input.lower()
        
        # Must have an explicit self-reference to the agent
        has_self_ref = any(ref in user_lower for ref in self.SELF_REFS)
        
        # Also check for improvement intent
        has_improvement_verb = any(verb in user_lower for verb in self.IMPROVEMENT_VERBS)
        
        # Confidence logic:
        # - Explicit self-ref alone: high confidence (user clearly means the agent)
        # - Self-ref + improvement verb: very high confidence
        # - No self-ref: never triggers, regardless of other words
        confidence = 0.0
        
        if has_self_ref:
            confidence = 0.85
            if has_improvement_verb:
                confidence = 0.95
        
        return SelfImprovementRequest(
            goal=user_input,
            refined_goal="",
            is_self_improvement=confidence >= confidence_threshold,
            confidence=confidence
        )
    
    def handle_self_improvement(self, user_input: str, codebase_root: str) -> Dict:
        """Execute self-improvement request.
        
        Args:
            user_input: User's improvement request
            codebase_root: Root of the codebase to improve
        
        Returns:
            {
                "success": bool,
                "message": str,
                "tasks_completed": int,
                "cost": float,
                "details": {...}
            }
        """
        
        if not self.cheap_planner:
            return {
                "success": False,
                "message": "Self-improvement disabled (GOOGLE_API_KEY not set)",
                "cost": 0.0
            }
        
        print(f"\n🔧 SELF-IMPROVEMENT MODE")
        print(f"   Goal: {user_input}")
        print(f"   Codebase: {codebase_root}\n")
        
        # Phase 1: Understand codebase
        print("📊 Analyzing codebase...")
        codebase_context = self.orchestrator._discover_codebase_context(codebase_root)
        
        # Phase 2: Refine vague goals
        print("🎯 Refining goal...")
        refined_goal = self.cheap_planner.refine_goal(user_input, codebase_context)
        print(f"   Refined: {refined_goal}\n")
        
        # Phase 3: Plan with cheap planner
        print("📋 Planning with Gemini Flash...")
        try:
            plan = self.cheap_planner.plan(refined_goal, codebase_context, tracker=self.tracker)
        except Exception as e:
            return {
                "success": False,
                "message": f"Planning failed: {str(e)}",
                "cost": 0.0
            }
        
        print(f"   {len(plan['plan'])} tasks generated\n")
        
        # Phase 4: Execute with orchestrator — pass cheap plan directly (skip Sonnet re-plan)
        print("⚙️  Executing with DeepSeek workers...\n")
        
        result = self.orchestrator.execute_feature(
            goal=refined_goal,
            codebase_root=codebase_root,
            codebase_context=codebase_context,
            pre_planned_tasks=plan["plan"]
        )
        
        # Phase 5: Summary
        if self.tracker:
            status = self.tracker.get_budget_status()
            total_cost = status.get("total_cost", 0.0)
        else:
            total_cost = 0.0
        
        return {
            "success": result["success"],
            "message": f"Self-improvement complete: {result['tasks_completed']}/{result['total_tasks']} tasks succeeded",
            "tasks_completed": result["tasks_completed"],
            "tasks_failed": result["tasks_failed"],
            "cost": total_cost,
            "details": result
        }
    
    def is_enabled(self) -> bool:
        """Check if self-improver is enabled."""
        return self.cheap_planner is not None

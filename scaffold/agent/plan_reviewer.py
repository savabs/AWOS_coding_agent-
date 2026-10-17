"""
plan_reviewer.py — Plan Review Checkpoint (Phase 2)

Shows generated plan to user for approval BEFORE execution.
Implements Devin's "highest-leverage checkpoint" pattern.

Architecture:
1. Planner generates tasks
2. Format plan for human review
3. User approves / refines / rejects
4. Only execute approved plan

Research: docs/research/goal_clarification_best_practices.md
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Callable

logger = logging.getLogger(__name__)


@dataclass
class PlanReviewResult:
    """Result of plan review."""
    approved: bool
    original_plan: List[dict]
    revised_plan: Optional[List[dict]] = None
    feedback: Optional[str] = None
    rejection_reason: Optional[str] = None


class PlanReviewer:
    """
    Shows plan to user and gets approval before execution.
    
    Implements Devin's checkpoint pattern: bad plan runs for hour before notice,
    reviewed plan catches drift in 1 minute.
    """
    
    def __init__(self, interactive: bool = True):
        """
        Args:
            interactive: If True, prompt user in terminal. If False, return plan for external review.
        """
        self.interactive = interactive
    
    def review(
        self,
        goal: str,
        plan: List[dict],
        files_to_modify: Optional[List[str]] = None,
        acceptance_criteria: Optional[str] = None,
        auto_approve: bool = False,
    ) -> PlanReviewResult:
        """
        Review plan with user.
        
        Args:
            goal: Original goal
            plan: List of task dicts from planner
            files_to_modify: Expected files to change
            acceptance_criteria: How to verify success
            auto_approve: Skip review (for testing/automation)
        
        Returns:
            PlanReviewResult with approval status
        """
        if auto_approve:
            logger.info("[PlanReview] Auto-approved (auto_approve=True)")
            return PlanReviewResult(approved=True, original_plan=plan)
        
        if not self.interactive:
            # Non-interactive: return plan for external review
            return PlanReviewResult(
                approved=False,
                original_plan=plan,
                feedback="Plan requires external review"
            )
        
        # Interactive: show plan and get user input
        return self._interactive_review(goal, plan, files_to_modify, acceptance_criteria)
    
    def _interactive_review(
        self,
        goal: str,
        plan: List[dict],
        files_to_modify: Optional[List[str]],
        acceptance_criteria: Optional[str],
    ) -> PlanReviewResult:
        """Interactive terminal-based plan review."""
        
        # Format plan for display
        formatted = self._format_plan(goal, plan, files_to_modify, acceptance_criteria)
        
        # Show to user
        print("\n" + "=" * 70)
        print("PLAN REVIEW CHECKPOINT")
        print("=" * 70)
        print(formatted)
        print("=" * 70)
        
        # Get approval
        while True:
            response = input("\nApprove plan? [y]es / [n]o / [r]efine / [q]uestion: ").strip().lower()
            
            if response in ['y', 'yes']:
                print("\n✓ Plan approved. Starting execution...\n")
                return PlanReviewResult(approved=True, original_plan=plan)
            
            elif response in ['n', 'no']:
                reason = input("Why reject? ").strip()
                print(f"\n✗ Plan rejected: {reason}")
                print("Please refine your goal and try again.\n")
                return PlanReviewResult(
                    approved=False,
                    original_plan=plan,
                    rejection_reason=reason
                )
            
            elif response in ['r', 'refine']:
                feedback = input("What should change? ").strip()
                print(f"\n⟳ Replanning with feedback: {feedback}\n")
                return PlanReviewResult(
                    approved=False,
                    original_plan=plan,
                    feedback=feedback
                )
            
            elif response in ['q', 'question']:
                self._answer_questions(plan)
                # Loop back to approval prompt
            
            else:
                print("Invalid input. Please enter y/n/r/q")
    
    def _format_plan(
        self,
        goal: str,
        plan: List[dict],
        files_to_modify: Optional[List[str]],
        acceptance_criteria: Optional[str],
    ) -> str:
        """Format plan for human-readable display."""
        lines = []
        
        lines.append(f"Goal: {goal}")
        lines.append(f"\nProposed Plan ({len(plan)} tasks):")
        
        for i, task in enumerate(plan, 1):
            desc = task.get('description', task.get('task', 'Unknown task'))
            lines.append(f"  {i}. {desc}")
            
            # Show file if available
            file = task.get('file', task.get('files'))
            if file:
                if isinstance(file, list):
                    lines.append(f"     Files: {', '.join(file)}")
                else:
                    lines.append(f"     File: {file}")
        
        if files_to_modify:
            lines.append(f"\nFiles to modify: {', '.join(files_to_modify)}")
        
        if acceptance_criteria:
            lines.append(f"\nAcceptance criteria: {acceptance_criteria}")
        else:
            lines.append(f"\n⚠ Warning: No acceptance criteria specified")
        
        # Sanity checks
        warnings = self._sanity_check(plan, files_to_modify)
        if warnings:
            lines.append("\n⚠ WARNINGS:")
            for w in warnings:
                lines.append(f"  • {w}")
        
        return "\n".join(lines)
    
    def _sanity_check(self, plan: List[dict], expected_files: Optional[List[str]]) -> List[str]:
        """
        Run sanity checks on plan (Devin pattern: catch drift before execution).
        
        Returns:
            List of warning strings
        """
        warnings = []
        
        # Check 1: Plan has tasks
        if not plan:
            warnings.append("Plan is empty")
        
        # Check 2: Tasks have descriptions
        missing_desc = sum(1 for t in plan if not t.get('description') and not t.get('task'))
        if missing_desc > 0:
            warnings.append(f"{missing_desc} tasks missing descriptions")
        
        # Check 3: If expected files specified, check if plan matches
        if expected_files:
            plan_files = set()
            for task in plan:
                file = task.get('file', task.get('files'))
                if file:
                    if isinstance(file, list):
                        plan_files.update(file)
                    else:
                        plan_files.add(file)
            
            unexpected = plan_files - set(expected_files)
            if unexpected:
                warnings.append(f"Plan touches unexpected files: {', '.join(unexpected)}")
        
        # Check 4: Too many tasks (might be over-decomposed)
        if len(plan) > 20:
            warnings.append(f"Plan has {len(plan)} tasks (very large, might be over-decomposed)")
        
        return warnings
    
    def _answer_questions(self, plan: List[dict]) -> None:
        """Answer user questions about the plan."""
        print("\nWhat would you like to know about this plan?")
        print("  1. Explain a specific task")
        print("  2. Show task dependencies")
        print("  3. Estimated time/cost")
        print("  4. Back to approval")
        
        choice = input("\nChoice: ").strip()
        
        if choice == '1':
            task_num = input("Which task number? ").strip()
            try:
                idx = int(task_num) - 1
                if 0 <= idx < len(plan):
                    task = plan[idx]
                    print(f"\nTask {task_num}:")
                    for k, v in task.items():
                        print(f"  {k}: {v}")
                else:
                    print(f"Invalid task number (must be 1-{len(plan)})")
            except ValueError:
                print("Invalid number")
        
        elif choice == '2':
            print("\nTask dependencies:")
            print("  (Sequential execution: 1 → 2 → 3 → ...)")
            for i, task in enumerate(plan, 1):
                depends = task.get('depends_on', [])
                if depends:
                    print(f"  {i}. Depends on: {depends}")
                else:
                    print(f"  {i}. No dependencies")
        
        elif choice == '3':
            print(f"\nEstimated time: ~{len(plan) * 2} minutes ({len(plan)} tasks × 2 min avg)")
            print(f"Estimated cost: ~${len(plan) * 0.01:.2f} ({len(plan)} tasks × $0.01 avg)")
        
        input("\nPress Enter to continue...")


# ── Convenience function ──

def review_plan(
    goal: str,
    plan: List[dict],
    files_to_modify: Optional[List[str]] = None,
    acceptance_criteria: Optional[str] = None,
    interactive: bool = True,
    auto_approve: bool = False,
) -> PlanReviewResult:
    """
    Convenience function to review a plan.
    
    Usage:
        result = review_plan(goal, plan, files=["planner.py"])
        if result.approved:
            # Execute plan
        elif result.feedback:
            # Replan with feedback
        else:
            # Rejected, refine goal
    """
    reviewer = PlanReviewer(interactive=interactive)
    return reviewer.review(goal, plan, files_to_modify, acceptance_criteria, auto_approve)

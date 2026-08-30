#!/usr/bin/env python3
"""
Quick test script for goal clarification system.

Tests Phase 1 (questions), Phase 2 (plan review), Phase 3 (ambiguity detection).
"""
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.goal_clarifier import GoalClarifier, ClarifiedGoal
from scaffold.agent.plan_reviewer import PlanReviewer, PlanReviewResult


def test_ambiguity_detection():
    """Test Phase 3: Cheap ambiguity detection."""
    print("\n" + "=" * 70)
    print("TEST 1: Ambiguity Detection (Phase 3)")
    print("=" * 70)
    
    clarifier = GoalClarifier()
    
    # Test clear goal
    clear_goal = "Create docs/test.md with API documentation. Tests pass. Only touch docs folder."
    print(f"\n✓ CLEAR Goal: {clear_goal}")
    clarified = clarifier.clarify(clear_goal, interactive=False)
    print(f"  Is ambiguous? {clarified.is_ambiguous}")
    assert not clarified.is_ambiguous, "Clear goal should not be ambiguous"
    
    # Test ambiguous goal
    ambiguous_goal = "research search and planning"
    print(f"\n✗ AMBIGUOUS Goal: {ambiguous_goal}")
    clarified = clarifier.clarify(ambiguous_goal, interactive=False)
    print(f"  Is ambiguous? {clarified.is_ambiguous}")
    print(f"  Questions: {len(clarified.clarifying_questions) if clarified.clarifying_questions else 0}")
    if clarified.clarifying_questions:
        for q in clarified.clarifying_questions:
            print(f"    - {q['question']}")
    assert clarified.is_ambiguous, "Ambiguous goal should be detected"
    assert clarified.clarifying_questions, "Should generate questions"
    
    print("\n✅ Phase 3: PASS\n")


def test_plan_review():
    """Test Phase 2: Plan review checkpoint."""
    print("\n" + "=" * 70)
    print("TEST 2: Plan Review (Phase 2)")
    print("=" * 70)
    
    reviewer = PlanReviewer(interactive=False)
    
    # Mock plan
    plan = [
        {"task_id": 1, "action": "Read planner.py", "file": "scaffold/agent/planner.py"},
        {"task_id": 2, "action": "Create audit doc", "file": "docs/research/audit.md"},
    ]
    
    goal = "Research search and planning"
    
    print(f"\nGoal: {goal}")
    print(f"Plan: {len(plan)} tasks")
    
    # Test auto-approve
    result = reviewer.review(goal, plan, auto_approve=True)
    print(f"\nAuto-approve: {result.approved}")
    assert result.approved, "Auto-approve should approve"
    
    # Test non-interactive (should not approve without user input)
    result = reviewer.review(goal, plan, auto_approve=False)
    print(f"Non-interactive: {result.approved}")
    assert not result.approved, "Non-interactive should not auto-approve"
    
    print("\n✅ Phase 2: PASS\n")


def test_integration():
    """Test full integration flow."""
    print("\n" + "=" * 70)
    print("TEST 3: Full Integration")
    print("=" * 70)
    
    # Test with a vague goal
    vague_goal = "improve performance"
    print(f"\nVague Goal: {vague_goal}")
    
    clarifier = GoalClarifier()
    clarified = clarifier.clarify(vague_goal, interactive=False)
    
    if clarified.is_ambiguous:
        print(f"✓ Detected as ambiguous")
        print(f"  Questions generated: {len(clarified.clarifying_questions)}")
        
        # Simulate answers
        print(f"\nSimulating user answers...")
        answers = {
            "output": "scaffold/agent/worker.py",
            "acceptance": "Tests pass, benchmark shows 20% faster",
            "scope": "Only touch worker.py, not orchestrator.py"
        }
        
        clarified = clarifier._build_clarified_goal(vague_goal, answers)
        print(f"  Clarified goal: {clarified.clarified_goal}")
        print(f"  Output: {clarified.output_specification}")
        print(f"  Acceptance: {clarified.acceptance_criteria}")
        print(f"  Scope: {clarified.scope_boundaries}")
        
        # Now generate a mock plan
        plan = [
            {"task_id": 1, "action": "Profile worker.py", "file": "scaffold/agent/worker.py"},
            {"task_id": 2, "action": "Optimize hot paths", "file": "scaffold/agent/worker.py"},
        ]
        
        # Review plan
        reviewer = PlanReviewer(interactive=False)
        result = reviewer.review(
            clarified.clarified_goal,
            plan,
            files_to_modify=["scaffold/agent/worker.py"],
            acceptance_criteria=clarified.acceptance_criteria,
            auto_approve=True  # Simulate approval
        )
        
        if result.approved:
            print(f"\n✓ Plan approved")
            print(f"  Would execute: {len(plan)} tasks")
        
        print("\n✅ Integration: PASS\n")


if __name__ == "__main__":
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " " * 15 + "GOAL CLARIFICATION TEST SUITE" + " " * 23 + "║")
    print("╚" + "═" * 68 + "╝")
    
    try:
        test_ambiguity_detection()
        test_plan_review()
        test_integration()
        
        print("\n" + "=" * 70)
        print("ALL TESTS PASSED ✅")
        print("=" * 70)
        print("\nReady for dogfooding!")
        print("Run: awos worker start 'Research AWOS search+planning. Output: docs/research/audit.md'")
        print()
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

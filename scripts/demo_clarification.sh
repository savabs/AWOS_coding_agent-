#!/bin/bash
# Demo script: Shows goal clarification system in action

cat << 'EOF'

╔═══════════════════════════════════════════════════════════════════════╗
║           GOAL CLARIFICATION DEMO                                     ║
╚═══════════════════════════════════════════════════════════════════════╝

This demo shows the 3-phase goal clarification system:

Phase 1: Clarification Questions
Phase 2: Plan Review Checkpoint
Phase 3: Cheap Ambiguity Detection (DeepSeek)

═══════════════════════════════════════════════════════════════════════

SCENARIO 1: VAGUE GOAL (triggers clarification)
═══════════════════════════════════════════════════════════════════════

User runs:
  awos worker start "research search and planning"

AWOS detects ambiguity and asks:

  ╔══════════════════════════════════════════════════════════════╗
  ║              GOAL CLARIFICATION NEEDED                       ║
  ╚══════════════════════════════════════════════════════════════╝
  
  Your goal: research search and planning
  
  Before I start, I need to clarify 3 thing(s):
  
  1. What file should I create or modify? 
     (e.g., docs/research/audit.md)
     Answer: docs/research/search_planning_audit.md
  
  2. How will we know the task is complete? 
     (e.g., 'Tests pass', 'Code runs')
     Answer: File exists and documents current capabilities
  
  3. What files can I touch? What files should I NOT touch? 
     (e.g., 'Only planner.py, not worker.py')
     Answer: Only create new doc, don't edit code files
  
  ══════════════════════════════════════════════════════════════
  
  Thank you! Starting work with clarified goal...

Then AWOS generates a plan and shows it:

  ╔══════════════════════════════════════════════════════════════╗
  ║              PLAN REVIEW CHECKPOINT                          ║
  ╚══════════════════════════════════════════════════════════════╝
  
  Goal: research search and planning. Output: docs/research/search_planning_audit.md
  
  Proposed Plan (3 tasks):
    1. Read scaffold/agent/planner.py
       File: scaffold/agent/planner.py
    2. Read memory/vector_memory.py
       File: memory/vector_memory.py
    3. Create docs/research/search_planning_audit.md with analysis
       File: docs/research/search_planning_audit.md
  
  Files to modify: docs/research/search_planning_audit.md
  
  Acceptance criteria: File exists and documents current capabilities
  
  ══════════════════════════════════════════════════════════════
  
  Approve plan? [y]es / [n]o / [r]efine / [q]uestion: y
  
  ✓ Plan approved. Starting execution...

Then AWOS executes the approved plan in worktree.

═══════════════════════════════════════════════════════════════════════

SCENARIO 2: CLEAR GOAL (skips clarification)
═══════════════════════════════════════════════════════════════════════

User runs:
  awos worker start "Create docs/research/search_planning_audit.md analyzing scaffold/agent/planner.py and memory/vector_memory.py. File exists when done. Only create new doc."

AWOS detects this is CLEAR (has output file + acceptance + scope) and:
  [CLARIFICATION] Goal is clear, proceeding
  [PLANNING] Breaking down goal...
  
Then jumps directly to plan review:

  ╔══════════════════════════════════════════════════════════════╗
  ║              PLAN REVIEW CHECKPOINT                          ║
  ╚══════════════════════════════════════════════════════════════╝
  
  Goal: Create docs/research/search_planning_audit.md...
  
  Proposed Plan (3 tasks):
    1. Read scaffold/agent/planner.py
    2. Read memory/vector_memory.py
    3. Create docs/research/search_planning_audit.md
  
  Approve plan? [y]es / [n]o / [r]efine / [q]uestion: y

═══════════════════════════════════════════════════════════════════════

COST COMPARISON:
═══════════════════════════════════════════════════════════════════════

Without clarification (old):
  Attempt 1: $0.003 (guesses wrong → .aider files)
  Attempt 2: $0.003 (still wrong)
  Attempt 3: $0.003 (finally right)
  Total: $0.009, 30 minutes wasted

With clarification (new):
  Clarification: $0.0001 (DeepSeek, 1 second)
  Execution: $0.003 (right first time)
  Total: $0.0031, 5 minutes

Savings: 70% cost, 83% time

═══════════════════════════════════════════════════════════════════════

READY TO TRY:
═══════════════════════════════════════════════════════════════════════

awos worker start "Research AWOS search and planning infrastructure: read scaffold/agent/planner.py and memory/vector_memory.py. Document current capabilities. Output: docs/research/search_planning_audit.md"

Or test with a deliberately vague goal:

awos worker start "improve performance"

This will trigger clarification questions!

═══════════════════════════════════════════════════════════════════════

EOF

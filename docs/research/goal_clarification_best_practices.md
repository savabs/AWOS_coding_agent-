# Goal Clarification — Best Practices from Top Agents (2026)

> **Problem:** First dogfood job failed because vague goal → AWOS guessed wrong  
> **Solution:** Research how Devin, Cursor, o3, and agent patterns handle ambiguous requests

---

## Key Finding: All top agents have a **clarification layer BEFORE execution**

---

## 1. Devin AI (30-50% success on clear goals, 15-30% on vague)

### What they do:

**Structured prompt format (required):**
```
Goal: What "done" looks like (1-2 sentences)
Context: Where code lives, what conventions apply
Acceptance criteria: Checkable conditions (tests pass, files change, CI green)
Scope: What's in, what's out, what NOT to touch
Environment: Install/test/build commands
Stop conditions: When to ask for help
```

**Plan review checkpoint (highest-leverage moment):**
- Devin proposes plan BEFORE executing
- Human reviews: Does plan match goal? Are assumptions correct? Out-of-scope work?
- If wrong, refine prompt and START OVER (don't try to course-correct mid-execution)

**"Improved Prompt" feature:**
- After session, Devin shows what was missing from original prompt
- Highlights: added context, clarified ambiguity, frontloaded important info

### Key lessons:

✅ **Explicit acceptance criteria prevent misinterpretation**  
✅ **Plan review catches drift in 1 minute vs 1 hour of wrong work**  
✅ **Bad prompts → start over, don't iterate**

---

## 2. Cursor Composer (real-time RL on user feedback)

### What they do:

**Trained to ask clarifying questions when uncertain:**
- Model learned: risky edits → ask first
- Problem: over-optimized → asked TOO MANY questions (had to fix reward function)
- Balance: clarify when truly ambiguous, execute when confident

**Tools + instructions co-optimization:**
- Instructions define WHEN to use each tool
- Example: "Use python tool for computation, not internal reasoning"

**Multi-turn context:** 
- Can ask questions mid-task
- User answers → incorporated immediately
- Agent continues while waiting for response

### Key lessons:

✅ **Asking clarifying questions is LEARNED behavior (needs RL)**  
⚠️ **Over-clarification is real risk (analysis paralysis)**  
✅ **Tool boundaries must be explicit in system prompt**

---

## 3. OpenAI o3 / Reasoning Models

### What they do:

**System instruction to prevent guessing:**
```json
{"role": "system", "content": "Don't make assumptions about what values to use with functions. Ask for clarification if a user request is ambiguous."}
```

**Fast model as clarification router:**
- Use GPT-4o-mini ($0.15/M tokens) to detect ambiguity
- If ambiguous → ask clarifying questions
- If clear → route to o3 ($15/M tokens) for execution
- Saves 100x cost on clarification loops

**Function calling boundaries:**
- Define in tool description: "Call this tool when X, NOT when Y"
- Reduce tool ambiguity at definition time, not runtime

### Key lessons:

✅ **Explicit "ask for clarification" instruction in system prompt**  
✅ **Use cheap model to detect ambiguity before expensive execution**  
✅ **Define tool boundaries at TOOL level, not just global instructions**

---

## 4. Agent Patterns (2026 Research)

### Goal Decomposition Pattern:

**Hierarchical tree:**
```
Root Goal (user's vague request)
  ├─ Sub-goal 1 (mid-level)
  │   ├─ Atomic action 1.1
  │   └─ Atomic action 1.2
  ├─ Sub-goal 2
  │   ├─ Atomic action 2.1
  │   └─ Atomic action 2.2
  └─ Sub-goal 3
```

**Two strategies:**

1. **Plan-then-Execute** (Devin style)
   - Decompose entire goal upfront
   - Generate full plan
   - **User approves plan**
   - Then execute
   - Pro: predictable, reviewable
   - Con: brittle to environment changes

2. **ReAct / Dynamic** (Cursor style)
   - Decompose only immediate next steps
   - Execute → Observe → Replan
   - Pro: adapts to changes
   - Con: harder to review full scope

**Hybrid (best for production):**
- High-level plan upfront (user reviews)
- Dynamic decomposition per sub-goal (agent adapts)

### Failure Modes to Avoid:

1. **Goal drift:** Sub-task doesn't actually serve parent goal
   - Example: "Improve UX" → "Add dark mode" (might not be what user wanted)
   
2. **Pre-commitment:** Agent commits to specifics before gathering info
   - Example: "Find best flight" → "Compare these 5 flights" (before checking what exists)

3. **Over-decomposition:** Too granular, coordination overhead
   - Rule: If task = 1-2 actions, don't decompose

### Key lessons:

✅ **Plan review checkpoint = highest leverage intervention**  
✅ **Dynamic replanning beats static plans in real world**  
✅ **Decompose only if task requires 3+ steps**

---

## Synthesis: What AWOS Needs

### Current AWOS flow:
```
User goal → Planner → Worker → Execute → Done
```

**Problem:** No clarification, no plan review

### Proposed AWOS flow:
```
User goal 
  ↓
CLARIFICATION LAYER (NEW)
  • Detect ambiguity (cheap model)
  • Ask targeted questions
  • Fill in missing: output file, acceptance criteria, scope
  ↓
PLANNING LAYER (enhanced)
  • Generate hierarchical plan
  • Show plan to user
  • USER APPROVES or refines
  ↓
EXECUTION LAYER (existing worker)
  • Execute approved plan
  • Dynamic replanning per sub-goal
  ↓
VERIFICATION (existing)
  • Check acceptance criteria
  • Surface to user
```

---

## Concrete Implementation for AWOS

### Phase 1: Clarification Prompt (immediate)

Add to orchestrator system prompt:

```
If the user's goal is missing any of these, ASK before planning:

1. Output specification
   - "Create file X" or "Modify file Y"?
   - What should the output contain?

2. Acceptance criteria
   - How will we know it's done?
   - "Tests pass"? "Code runs"? "Doc is readable"?

3. Scope boundaries
   - What files CAN be touched?
   - What files MUST NOT be touched?
   - What's out of scope?

If ANY of these are unclear, respond with:
"Before I start, I need to clarify:
1. [specific question about output]
2. [specific question about acceptance]
3. [specific question about scope]

Please provide these details so I can create an accurate plan."

DO NOT GUESS. DO NOT START PLANNING UNTIL THESE ARE CLEAR.
```

### Phase 2: Plan Review Checkpoint (week 2)

After planner generates tasks, before worker executes:

```python
def orchestrator_with_approval(goal: str) -> None:
    # 1. Clarify (Phase 1)
    clarified_goal = clarify_if_needed(goal)
    
    # 2. Plan
    plan = planner.generate(clarified_goal)
    
    # 3. SHOW PLAN TO USER (NEW)
    print("=" * 60)
    print("PLAN REVIEW")
    print("=" * 60)
    print(f"Goal: {clarified_goal}")
    print(f"\nProposed plan:")
    for i, task in enumerate(plan, 1):
        print(f"  {i}. {task.description}")
    print(f"\nFiles to modify: {plan.files}")
    print(f"Acceptance: {plan.acceptance_criteria}")
    print("=" * 60)
    
    # 4. GET APPROVAL
    response = input("Approve plan? (y/n/refine): ")
    if response == 'n':
        print("Plan rejected. Please refine goal.")
        return
    elif response == 'refine':
        feedback = input("What should change? ")
        # Replan with feedback
        plan = planner.generate(clarified_goal, feedback)
    
    # 5. Execute approved plan
    worker.execute(plan)
```

### Phase 3: Cheap Model Router (optimization)

Use DeepSeek ($0.14/M) to detect ambiguity before using Claude ($3/M):

```python
def detect_ambiguity(goal: str) -> bool:
    """Use cheap model to check if goal is clear."""
    prompt = f"""
    Is this goal clear enough to execute? Check:
    1. Specific output file mentioned?
    2. Acceptance criteria defined?
    3. Scope boundaries stated?
    
    Goal: {goal}
    
    Answer: CLEAR or AMBIGUOUS
    """
    response = deepseek_call(prompt)  # $0.0001 vs $0.003
    return "AMBIGUOUS" in response
```

---

## Success Metrics

| Metric | Before | After | Target |
|--------|---------|-------|--------|
| Misinterpretation rate | 100% (first dogfood job) | ? | <20% |
| User intervention time | 0 min (task fails silently) | ? | 2 min (plan review) |
| Wasted work (wrong path) | 10+ min | ? | <2 min |
| Cost per clarification | $0.003 (Claude) | ? | $0.0001 (DeepSeek) |

---

## Next Steps

**Immediate (today):**
1. Add clarification prompt to orchestrator system message
2. Retry dogfood Job 1 with explicit output file in goal

**Week 2:**
3. Implement plan review checkpoint (user approval)
4. Test on Jobs 2-5

**Week 3:**
5. Add cheap model ambiguity detector
6. Measure misinterpretation rate drop

---

## References

- Devin Prompting Guide (2026): https://sureprompts.com/blog/devin-ai-prompting-guide
- Cursor Composer RL: https://cursor.com/blog/real-time-rl-for-composer
- OpenAI o3 Function Calling: https://developers.openai.com/cookbook/examples/o-series/o3o4-mini_prompting_guide
- Agent Goal Decomposition (2026): https://zylos.ai/research/2026-03-19-ai-agent-goal-decomposition-hierarchical-planning

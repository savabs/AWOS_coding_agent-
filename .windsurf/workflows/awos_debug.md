---
auto_execution_mode: 0
description: AWOS debug protocol — mandatory after 2 failed fix attempts
---
**Hard rule: after 2 failed fix attempts on the same bug, mandatory switch to this protocol.**
Do not attempt a 3rd patch without completing steps 1–4 below.

## Step 0: Research First (No Guessing Rule)
Before any fix attempt, search external technical sources.
1. Copy the exact error message (strip project-specific paths).
2. Search GitHub Issues, official docs, Stack Overflow.
3. Read sources — do not skim. Look for root cause, version triggers, accepted workarounds.
4. Write findings to a research file `docs/debug_<short_name>.md` before touching any code.
5. Only then write a fix — grounded in sources, not invented.

## Step 1: Reproduce (Minimal Case)
Create the smallest possible case that triggers the bug.
You cannot reliably fix what you cannot reliably trigger.
Do not proceed until you have a minimal reproduction that fails consistently.

## Step 2: Instrument
Add targeted logging/assertions to observe actual state at the failure point.
Key things to instrument:
- Input values at entry point (type, shape, content)
- Intermediate values at each transformation step
- Actual vs expected value at point of failure
- Any external state read (config, cache, database)

## Step 3: Form a Hypothesis
Write it down before changing any code:
```
I think the bug is at [exact location: file, function, line range]
because [specific reasoning based on instrumentation output]
This check would CONFIRM: [specific test or assertion]
This check would DISCONFIRM: [specific test or assertion]
```
A hypothesis without a disconfirmation criterion is not falsifiable.

## Step 4: Verify the Hypothesis
Run the confirming/disconfirming check without making any production code changes.
If confirmed → proceed to Step 5.
If disconfirmed → go back to Step 3 with new evidence.

## Step 5: Fix — Once, With Confidence
- Make the smallest targeted change that addresses the root cause
- Do not make speculative improvements while in here
- Do not refactor adjacent code "while you're in there"
- One change. One commit. One thing proved.

## Step 6: Regress
Add a test that would have caught this bug when it was introduced. This is not optional.
Mark the test with a comment: `# regression: <brief description>`

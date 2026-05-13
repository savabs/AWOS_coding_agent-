# Debug Protocol

> **Hard rule: after 2 failed fix attempts on the same bug, mandatory switch to this protocol.**
> Do not attempt a 3rd patch without completing steps 1–4 below.
> Infinite retry loops waste context and produce noise without signal.

---

## When This Protocol Activates

- Two fix attempts for the same bug have failed
- A test that should be passing keeps failing for unclear reasons
- The error message is changing but the root cause is not obvious
- You are adding more and more logic to fix something that "should be simple"

**If you have a hunch and want to try "just one more fix" — stop. Do the protocol first.**

---

## Step 0: Research First (No Guessing Rule)

**Before any fix attempt, search external technical sources.**

The rule: never guess a fix for an error you have not researched. Hallucinated fixes waste time and can introduce new bugs that mask the real root cause.

### Procedure

1. **Copy the exact error message** (traceback, error code, crash line). Strip project-specific paths/names so it is searchable.
2. **Search technical sources** — in priority order:
   - GitHub Issues for the library that produced the error (search: `repo:<owner>/<lib> "<error string>"`)
   - Official library docs / changelog (check for known bugs in your version)
   - Stack Overflow, PyTorch forums, HuggingFace forums for ML/training errors
   - arXiv / blog posts for algorithmic errors (NaN gradients, reward collapse, etc.)
3. **Read the sources** — do not skim. Look for: root cause explanation, confirmed version-specific triggers, accepted workarounds.
4. **Write findings to a research file** — `docs/debug_<short_name>.md` — before touching any code:

```markdown
## Error: <error string>

**Searched:** <date>

### Sources
| Source | URL | Key finding |
|---|---|---|
| GitHub Issue #NNNN | https://github.com/... | Root cause: X. Fixed in version Y. |
| PyTorch forum thread | https://... | Workaround: disable Z. |

### Root Cause (confirmed)
<one paragraph — what actually causes this>

### Fix
<exact change to make, based on sources>

### Prevention
<what to do so this doesn't recur>
```

5. **Only then write a fix** — grounded in the sources, not invented.

### Why This Rule Exists

- ML training errors (NaN gradients, CUDA kernel mismatches, dtype crashes) have specific, documented root causes. Guessing produces the wrong fix.
- A wrong fix can mask the real error, making it harder to find later.
- The research file becomes the `debug_log.md` entry — no extra work required.

**If search returns nothing useful after 3 keyword variants: mark the claim as `UNVERIFIED` in the research file and escalate. Do not fill the gap with a plausible-sounding guess.**

---

## Step 1: Reproduce (Minimal Case)

Create the smallest possible case that triggers the bug.

```python
# Minimal reproduction
# Remove all unrelated code until only the failing case remains
def test_minimal_repro():
    # exact conditions that trigger the bug
    result = the_broken_function(minimal_input)
    assert result == expected  # this fails
```

**You cannot reliably fix what you cannot reliably trigger.**
If you cannot create a minimal reproduction, the bug is not understood yet.

Do not proceed to Step 2 until you have a minimal reproduction that fails consistently.

---

## Step 2: Instrument

Add targeted logging/assertions to observe actual state at the failure point.

```python
# Example instrumentation
def the_broken_function(input):
    print(f"DEBUG: input type={type(input)}, value={input!r}")

    intermediate = compute_something(input)
    print(f"DEBUG: intermediate={intermediate!r}")

    result = transform(intermediate)
    print(f"DEBUG: result={result!r}")
    return result
```

**Never debug by reading code alone. Look at actual runtime values.**

Key things to instrument:
- Input values at the entry point (type, shape, content)
- Intermediate values at each transformation step
- The actual vs. expected value at the point of failure
- Any external state that the function reads (config, cache, database)

Run the minimal reproduction with instrumentation. Read the output carefully.

---

## Step 3: Form a Hypothesis

Write it down before changing any code:

```
I think the bug is at [exact location: file, function, line range]
because [specific reasoning based on instrumentation output]
This check would CONFIRM the hypothesis: [specific test or assertion]
This check would DISCONFIRM the hypothesis: [specific test or assertion]
```

**A hypothesis without a disconfirmation criterion is not falsifiable.** Keep forming hypotheses until you have one that is specific and testable.

Common hypothesis patterns:
- "The input is None when it shouldn't be — because X is not setting it before calling this function"
- "The type is str when int is expected — because Y is not converting the config value"
- "The cache key is wrong — because Z is using the raw URL instead of the normalized URL"
- "The order of operations is wrong — because A runs before B, but B needs A's output"

---

## Step 4: Verify the Hypothesis

Run the confirming/disconfirming check **without making any production code changes**.

```python
# Verifying the hypothesis — NOT a fix, just a check
def test_hypothesis():
    # Does the expected precondition actually hold?
    assert precondition_holds(...)  # does this pass or fail?

    # Is the actual behavior what the hypothesis predicts?
    actual = the_broken_function(triggering_input)
    # Does actual match what the hypothesis predicted? Yes/No
```

If the hypothesis is confirmed → proceed to Step 5.
If the hypothesis is disconfirmed → go back to Step 3 with the new evidence.

---

## Step 5: Fix — Once, With Confidence

Now that the root cause is known:
- Make the smallest targeted change that addresses the root cause
- Do not make speculative improvements while in here
- Do not refactor adjacent code "while you're in there"
- One change. One commit. One thing proved.

```python
# Before: the bug
def the_broken_function(input):
    return process(input.value)  # bug: input can be None

# After: the fix
def the_broken_function(input):
    if input is None:
        raise ValueError("input must not be None")  # explicit, at the boundary
    return process(input.value)
```

---

## Step 6: Regress

Add a test that would have caught this bug when it was introduced.
This is not optional. This prevents the same bug from returning.

```python
def test_regression_none_input_raises():
    # regression: input=None was silently producing wrong output; should raise
    with pytest.raises(ValueError, match="must not be None"):
        the_broken_function(None)
```

Mark the test with a comment: `# regression: <brief description of original bug>`

---

## Common Bug Patterns (Reference)

### Type mismatch
- Check what type is actually arriving vs. what the function expects
- Common source: config values loaded as strings, JSON fields with unexpected types

### Off-by-one / boundary conditions
- Check the edge values: 0, 1, empty collection, single element, max value
- Check < vs <= and > vs >=

### Mutation of shared state
- Is the data being modified in-place when a copy is expected?
- Is a list/dict being shared across test cases?

### Ordering dependency
- Does function A assume function B has already run?
- Is test setup happening in the right order?
- Is the database/cache populated before the function reads it?

### External state
- Is the test depending on a file, environment variable, or network call that isn't mocked?
- Is the test leaving state behind that affects the next test?

### Async / timing
- Is there a race condition between writes and reads?
- Is a timeout too short for the operation?

---

## When to Escalate

If Step 3–4 reveal that the bug is outside the immediate codebase (library bug, OS behavior,
network behavior, hardware), escalate:
1. Confirm the external cause with a minimal reproduction that demonstrates it
2. Add a comment explaining the root cause and its external nature
3. Write a workaround that is clearly labeled as a workaround: `# WORKAROUND: <explanation>`
4. File an issue or note in the task file that the underlying bug is external

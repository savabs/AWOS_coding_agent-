---
description: "Pre-execution planning ritual — build a joint execution plan before touching any code. Run before every implementation step."
mode: agent
tools:
  - read_file
  - grep_search
  - file_search
  - semantic_search
  - fetch_webpage
  - tool_search
---

# Pre-Implementation Planning Ritual

Run this before writing any code. The goal is to compress all context into a structured plan that makes the implementation step trivial.

## Step 1: Locate the Governing Artifacts

Find and read:
- `tasks/active/<name>.md` — which step is active? What are the acceptance criteria?
- `docs/specs/<name>_spec.md` — what does this step require exactly?
- `docs/research/<name>.md` — what are the verified references for this step?

If any artifact is missing, **stop and create it** before proceeding (research → spec → task order).

## Step 2: Search for Relevant Code

Use grep_search and semantic_search to find:
- The file(s) to be modified
- The exact function, class, or section being changed
- Any existing patterns or conventions to follow
- Any tests that cover this area

Read the relevant sections — understand before editing.

## Step 3: Search External References (if needed)

If this step involves a library, API, algorithm, or pattern not already verified in this session:
1. `fetch_webpage` on the official docs URL (if known)
2. Otherwise `tavily_search` basic with 2–3 keyword variants
3. Record the verified source in the research doc before proceeding

## Step 4: Build the Execution Plan

State explicitly:
- **File(s) to change:** `<paths>`
- **What changes:** `<one sentence per file>`
- **What it must NOT break:** `<existing behavior or interfaces>`
- **Insertion point:** `<function name, line range, or section>`

## Step 5: Name Edge Cases

List the top 3 things that could go wrong with this specific change:
1. `<edge case 1>`
2. `<edge case 2>`
3. `<edge case 3>`

## Step 6: Define Tests

Specify before writing code:
- **Happy path:** `<exact scenario that proves it works>`
- **Failure 1:** `<most likely error path>`
- **Failure 2:** `<second most likely error path>`

## Step 7: Compress and Confirm

Output this plan block:

```
STEP:    <step number and description from task file>
FILES:   <paths to change>
CHANGE:  <one-line description of the change>
RISK:    <primary failure mode>
TESTS:
  happy  — <description>
  fail1  — <description>
  fail2  — <description>
REFS:    <verified source URL or "no external deps">

PROCEED? [yes / adjust <what> / abort]
```

Wait for user confirmation before writing any code.

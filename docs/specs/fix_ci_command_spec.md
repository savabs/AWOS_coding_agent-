---
title: "Spec: awos fix-ci command"
tags:
  - doc/spec
  - phase/4
  - topic/ci-rescue
  - topic/cli
  - status/active
---

# Spec: `awos fix-ci` Command

> **Goal:** Build `awos fix-ci` — one-command CI rescue for any Python repo.
> **Research:** [[fix_ci_command]]
> **Task:** [[pivot_web_app_product]] step 1

---

## Goal (one sentence)

Add a new CLI command `awos fix-ci` that discovers failing tests in any Python repo, generates a CI rescue plan, executes it via the orchestrator, and shows transparent routing + cost comparison.

---

## Files Affected

| File | Action |
|---|---|
| `scaffold/agent/ci_discovery.py` | **NEW** — test failure discovery + parsing |
| `scaffold/agent/ci_planner.py` | **NEW** — CI goal + plan generation |
| `scaffold/agent/ci_display.py` | **NEW** — terminal output formatting |
| `awos.py` | **MODIFY** — add `cmd_fix_ci()` + argparse |
| `tests/test_ci_discovery.py` | **NEW** — unit tests for discovery |
| `tests/test_ci_planner.py` | **NEW** — unit tests for planner |

---

## Implementation Steps

### Step 1: Create `ci_discovery.py` — test failure discovery

**What:** New module that runs pytest, parses output, extracts failures.

**Functions:**
```python
def discover_ci_failures(repo_root: str) -> list[dict]:
    """
    Run pytest on repo_root, parse output, return failure list.
    
    Returns: [
        {
            "module": "auth.py",
            "test_name": "test_login",
            "test_file": "tests/test_auth.py",
            "error_msg": "AssertionError: expected True, got False",
            "line_number": 45,
        },
        ...
    ]
    """

def group_failures_by_module(failures: list[dict]) -> dict[str, list[dict]]:
    """Group failures by source module."""
```

**Test:** `tests/test_ci_discovery.py`
- Test on wedge fixture (28 reds → 28 failures discovered)
- Test on empty repo (0 failures)
- Test on repo with no pytest (graceful error)

**Exit condition:** `pytest tests/test_ci_discovery.py` passes.

---

### Step 2: Create `ci_planner.py` — CI goal + plan generation

**What:** New module that generates CI-specific goal and task plan from failures.

**Functions:**
```python
def generate_ci_goal(failures: list[dict]) -> str:
    """
    Generate CI rescue goal from failure list.
    
    Example output:
    "Fix 12 failing tests in auth.py, billing.py, inventory.py.
     Do not edit tests/. Keep 7 regression tests green."
    """

def generate_ci_plan(failures: list[dict]) -> list[dict]:
    """
    Generate task plan from failure list (deterministic, no LLM).
    
    Returns: [
        {
            "task_id": 1,
            "file": "auth.py",
            "action": "Fix test_login: AssertionError at line 45",
            "complexity": "low",
        },
        ...
    ]
    """
```

**Test:** `tests/test_ci_planner.py`
- Test goal generation (includes module names, test count)
- Test plan generation (one task per failure, correct file paths)
- Test empty failure list (returns empty plan)

**Exit condition:** `pytest tests/test_ci_planner.py` passes.

---

### Step 3: Create `ci_display.py` — terminal output formatting

**What:** New module that formats CI rescue results for terminal display.

**Functions:**
```python
def print_ci_header(failures: list[dict]):
    """Print header: 'Found X failing tests in Y modules'"""

def print_ci_results(failures: list[dict], result: dict):
    """
    Print final results:
    - Time elapsed
    - Cost
    - Per-task: model used, cost, pass/fail
    - Summary: X/Y fixed
    - Cost comparison vs Cursor
    """
```

**Test:** Manual verification (terminal output is hard to unit test).

**Exit condition:** Run `awos fix-ci` on wedge fixture, observe formatted output.

---

### Step 4: Add `cmd_fix_ci()` to `awos.py`

**What:** New CLI command that orchestrates the full flow.

**Function:**
```python
def cmd_fix_ci(args):
    """One-command CI rescue for any Python repo."""
    repo_root = getattr(args, "root", ".") or "."
    
    # 1. Discover failures
    print_ci_header(discover_ci_failures(repo_root))
    
    # 2. Generate goal + plan
    goal = generate_ci_goal(failures)
    plan = generate_ci_plan(failures)
    
    # 3. Call orchestrator
    wrapped_args = argparse.Namespace(
        goal=goal,
        root=repo_root,
        pre_planned_tasks=plan,
        auto_approve=True,
        resume=False,
        session_id=None,
    )
    cmd_run(wrapped_args)
    
    # 4. Show results
    print_ci_results(failures, result)
```

**Argparse:** Add to `build_parser()`:
```python
fix_ci = sub.add_parser("fix-ci", help="One-command CI rescue")
fix_ci.add_argument("--root", default=".", help="Target repo path")
```

**Test:** Manual verification.

**Exit condition:** `python3 awos.py fix-ci --root tests/fixtures/wedge_v1/ci_rescue_sprint` runs successfully.

---

### Step 5: Live proof — run on wedge fixture

**What:** Run `awos fix-ci` on the CI Rescue Sprint fixture and verify:
- 28 failures discovered
- 18 tasks generated (one per failure group)
- Orchestrator executes successfully
- Tests pass after fixes
- Cost < $0.01
- Transparent routing shown (DeepSeek used)
- Cost comparison displayed

**Command:**
```bash
python3 awos.py fix-ci --root tests/fixtures/wedge_v1/ci_rescue_sprint
```

**Success markers:**
- `[CI] Found 28 failing tests in 5 modules`
- `[CI] Starting CI rescue...`
- `[CI] CI Rescue Complete`
- `Cost: ₹0.35` (or similar low cost)
- `If on Cursor: ~₹1.50`

**Exit condition:** All markers observed in terminal output.

---

## Edge Cases

| Case | Handling |
|---|---|
| No failing tests | Print "CI is green!" and exit |
| No pytest installed | Print "pytest not found. Install with: pip install pytest" |
| Non-Python repo | Print "v1 supports Python repos only" |
| >50 failing tests | Print warning: "Large repo detected. v1 supports <50 failures." |
| Test timeout | TestRunner already handles timeouts (60s default) |

---

## Live Proof

**Scenario:** Run `awos fix-ci` on CI Rescue Sprint fixture.

**Command:**
```bash
python3 awos.py fix-ci --root tests/fixtures/wedge_v1/ci_rescue_sprint
```

**Expected output:**
```
═══════════════════════════════════════════════════════════════════
  AWOS Coding Agent — CI Repair
═══════════════════════════════════════════════════════════════════

[CI] Discovering test failures...
[CI] Found 28 failing tests in 5 modules:
  • auth.py: 4 failures
  • billing.py: 5 failures
  • inventory.py: 5 failures
  • shipping.py: 3 failures
  • promo.py: 11 failures

[CI] Generating CI rescue plan...
[CI] Plan: 18 tasks (one per failure group)

[CI] Starting CI rescue...
[PLANNER] Using pre-built plan: 18 tasks
[TASK 1] Fix verify_password (auth.py) — DeepSeek — $0.001
[TASK 2] Fix token_fingerprint (auth.py) — DeepSeek — $0.001
...
[TASK 18] Fix bundle_discount (promo.py) — DeepSeek — $0.002

═══════════════════════════════════════════════════════════════════
  AWOS Coding Agent — CI Repair Complete
═══════════════════════════════════════════════════════════════════

  Repo:   ci_rescue_sprint
  Time:   49.7s (unattended)
  Cost:   $0.0056

  Results:
    ✅ auth.py         — 4/4 fixed (DeepSeek)
    ✅ billing.py      — 5/5 fixed (DeepSeek)
    ✅ inventory.py    — 5/5 fixed (DeepSeek)
    ✅ shipping.py     — 3/3 fixed (DeepSeek)
    ✅ promo.py        — 11/11 fixed (DeepSeek)

  Summary: 28/28 fixed · 35/35 tests passing

  Your savings:
    This fix:    $0.0056
    If on Cursor: ~$0.02 (manual prompting)
    Savings: 72%
═══════════════════════════════════════════════════════════════════
```

---

## Related

- [[fix_ci_command]] — research doc
- [[CI_RESCUE_PRODUCT]] — CI repair capability spec
- [[TRANSPARENT_ROUTING]] — routing transparency UX spec
- [[pivot_web_app_product]] — active task file
- `scaffold/agent/orchestrator.py` — main execution engine
- `scaffold/agent/test_runner.py` — test framework detection
- `docs/missions/ci_rescue_sprint.json` — existing CI rescue mission

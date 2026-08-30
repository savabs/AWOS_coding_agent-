---
title: "Research: awos fix-ci command"
tags:
  - doc/research
  - phase/4
  - topic/ci-rescue
  - topic/cli
  - status/active
---

# Research: `awos fix-ci` Command

> **Goal:** Understand what exists, what's missing, and how to build `awos fix-ci` — the one-command CI rescue experience.

---

## 1. What Exists

### 1.1 Orchestrator (`scaffold/agent/orchestrator.py`)

The main execution engine. Key method: `execute_feature(goal, codebase_root, ...)`.

**Flow:**
```
execute_feature()
  ├── VectorMemory.index_codebase()
  ├── ProjectPlanner.load_or_create_goal()
  ├── RuntimeSession (optional)
  ├── Worktree sandbox (optional)
  ├── GitManager.setup()
  ├── SymbolIndex.build()
  ├── Goal Clarification (optional)
  ├── LiveRenderer.session_start()
  ├── Exploration phase (optional)
  ├── Planner.plan() OR use pre_planned_tasks
  ├── For each task:
  │   ├── EscalationEngine → pick model
  │   ├── Worker.execute_task() → generate patch
  │   ├── Verifier.verify_and_apply() → validate
  │   ├── compute_reward() → score outcome
  │   └── Learn → .awos/ (error patterns, skills, prompts)
  └── Return result dict
```

**Key parameters:**
- `goal`: string — the user's objective
- `codebase_root`: string — path to the repo
- `pre_planned_tasks`: optional list — skip planner, use pre-built plan
- `auto_approve_plan`: bool — skip interactive plan review
- `resume`: bool — resume from paused session
- `session_id`: optional — resume specific session

**Return value:**
```python
{
    "success": bool,
    "goal": str,
    "tasks_completed": int,
    "tasks_failed": int,
    "total_cost": float,
    "execution_log": list,
    "errors": list,
    "time_elapsed": float,
}
```

### 1.2 Worker (`scaffold/agent/worker.py`)

Executes atomic micro-tasks using LLMs. Key method: `execute_task(task, file_content, ...)`.

**Model selection:**
- Primary: DeepSeek V4 Flash (`deepseek-chat`) — $0.14/MTok
- Fallback chain: OpenRouter → OpenAI GPT-4o-mini → Anthropic Haiku
- Circuit breaker: skips dead providers
- Cheap-only mode: blocks premium tiers (Haiku, Sonnet)

**Edit format:** SEARCH/REPLACE blocks. Falls back to JSON-structured edits.

### 1.3 Verifier (`scaffold/agent/verifier.py`)

Applies SEARCH/REPLACE changes and validates. Key method: `verify_and_apply(search_replace, file_path, check_type)`.

**Checks:**
- Syntax validation (Python AST parse)
- Contract compliance (function signatures, constraints)
- Returns: `{success, applied, errors, needs_retry, error_context}`

### 1.4 Planner (`scaffold/agent/planner.py`)

Converts goals into atomic task blueprints. Uses Claude Sonnet.

**Output format:**
```json
{
  "plan": [
    {
      "task_id": 1,
      "file": "src/auth.py",
      "action": "Add login function signature",
      "complexity": "low",
      "function_signature": "...",
      "constraints": [...],
      "must_not": [...]
    }
  ],
  "reasoning": "...",
  "total_tasks": 3
}
```

### 1.5 TestRunner (`scaffold/agent/test_runner.py`)

Language-agnostic test executor. Key methods: `detect()`, `run(changed_files)`.

**Auto-detection:** pytest, jest, vitest, go test, cargo test, make test.

**Safety:** Requires `AWOS_SAFE_TO_RUN_TESTS=1` env var.

**Return:** `TestResult(passed, failed, errors, pass_rate, raw_output, timed_out)`.

### 1.6 Existing CI Rescue Mission

**Config:** `docs/missions/ci_rescue_sprint.json`
**Plan:** `docs/missions/ci_rescue_sprint.plan.json` (18 pre-planned tasks)
**Fixture:** `tests/fixtures/wedge_v1/ci_rescue_sprint/` (28 reds, 5 modules, 35 tests)

**How it works:**
```bash
python3 awos.py mission start --ci-rescue
```

1. Loads mission config (goal, env vars, plan file)
2. Sets env: `AWOS_USE_WORKTREE=true`, `AWOS_CHEAP_ONLY=true`, etc.
3. Loads pre-planned tasks from `ci_rescue_sprint.plan.json`
4. Calls `cmd_run()` which calls `orch.execute_feature(pre_planned_tasks=plan)`
5. Post-run: runs `wedge_v1_assert.py` to verify semantic pass

**Proven result:** 28 reds → green, $0.0056, 49 seconds, semantic_pass.

### 1.7 CLI Entry Point (`awos.py`)

**Existing commands:**
- `awos run "<goal>"` — generic orchestrator run
- `awos worker start "<goal>"` — simple worker interface
- `awos mission start --ci-rescue` — pre-configured CI rescue mission
- `awos stats --savings` — compounding cost report
- `awos report` — PEI scorecard

**`cmd_run()` function:**
```python
def cmd_run(args):
    tracker = TokenTracker(monthly_budget=...)
    orch = Orchestrator(tracker=tracker)
    result = orch.execute_feature(
        goal=args.goal,
        codebase_root=args.root,
        pre_planned_tasks=args.pre_planned_tasks,
        auto_approve_plan=args.auto_approve,
        resume=args.resume,
        session_id=args.session_id,
    )
    _print_run_result(result)
```

---

## 2. What's Missing for `awos fix-ci`

### 2.1 CI Failure Discovery

**Current state:** The existing CI rescue mission uses a **pre-planned task list** (hardcoded in `ci_rescue_sprint.plan.json`). It doesn't discover failures dynamically.

**What's needed:** A module that:
1. Runs `pytest` (or other test framework) on the target repo
2. Parses the output to extract failing test names and error messages
3. Groups failures by source module (e.g., `auth.py`, `billing.py`)
4. Returns a structured list: `[{module, test_name, error_msg, line_number}]`

### 2.2 CI-Specific Goal Generation

**Current state:** The mission config has a hardcoded goal string:
> "CI Rescue Sprint: fix auth.py, billing.py, inventory.py, shipping.py, and promo.py so all 35 pytest tests pass..."

**What's needed:** A function that generates a CI-specific goal from discovered failures:
```python
def generate_ci_goal(failures: list[dict]) -> str:
    """
    Input: [{module: "auth.py", test: "test_login", error: "AssertionError..."}]
    Output: "Fix 12 failing tests in auth.py, billing.py, inventory.py.
             Do not edit tests/. Keep 7 regression tests green."
    """
```

### 2.3 CI-Specific Plan Generation

**Current state:** The mission uses a pre-planned task list. For a generic repo, we need to generate the plan dynamically.

**Options:**
- **Option A:** Use the Planner (Sonnet) to generate tasks from the CI goal + failure list
- **Option B:** Generate tasks directly from failure list (cheaper, faster, no LLM call)
- **Option C:** Hybrid — generate initial plan from failures, let Planner refine

**Recommendation:** Option B for v1 (fast, cheap, deterministic). Option A for v2 (smarter planning).

### 2.4 Transparent Routing Display

**Current state:** The orchestrator uses `EscalationEngine` + `LinUCB` to pick models, but doesn't show the user why.

**What's needed:** A display layer that shows:
- Which model was chosen for each task
- Why (cost, capability, past success rate)
- What the alternatives are
- How to override

### 2.5 Cost Comparison Display

**Current state:** The orchestrator tracks cost internally (`total_cost` in result), but doesn't compare to alternatives.

**What's needed:** A display that shows:
- "This fix cost ₹0.42"
- "On Cursor: ~₹1.80 (manual prompting)"
- "This month saved: ₹8,400 vs manual debug time"

### 2.6 `awos fix-ci` CLI Command

**Current state:** No such command exists.

**What's needed:**
```python
def cmd_fix_ci(args):
    """One-command CI rescue for any Python repo."""
    # 1. Detect test framework
    # 2. Run tests, capture failures
    # 3. Group failures by module
    # 4. Generate CI goal
    # 5. Generate plan (or use Planner)
    # 6. Call orchestrator
    # 7. Show results + cost comparison
```

---

## 3. Design Decisions

### 3.1 Test Framework Support

**v1:** Python + pytest only (proven, most common for agencies)
**v2:** Add jest, vitest, go test, cargo test (TestRunner already supports detection)

### 3.2 Plan Generation Strategy

**v1:** Generate tasks directly from failure list (Option B)
- Fast: no LLM call for planning
- Cheap: saves ~$0.03-0.05 per run
- Deterministic: same failures → same plan
- Limitation: may miss cross-module dependencies

**v2:** Use Planner (Option A) for complex repos
- Smarter: understands dependencies
- More expensive: ~$0.05 per run
- Slower: ~5-10 seconds for planning

### 3.3 Routing Transparency

**v1:** Show model name + cost per task in terminal output
**v2:** Add web dashboard with per-task routing display + override UI

### 3.4 Cost Comparison

**v1:** Show "This fix cost ₹X. On Cursor: ~₹Y" in terminal
**v2:** Add web dashboard with monthly savings tracker

### 3.5 Integration with Existing Mission System

**Decision:** `awos fix-ci` is a **new command**, not a wrapper around `awos mission start --ci-rescue`.

**Why:**
- The mission system is for **pre-configured, hardcoded missions** (CI Rescue Sprint fixture)
- `awos fix-ci` is for **any repo, dynamic discovery**
- Different use cases, different flows

**Relationship:**
- `awos mission start --ci-rescue` → for testing/demos on the wedge fixture
- `awos fix-ci` → for real customer repos

---

## 4. Implementation Approach

### 4.1 New Module: `scaffold/agent/ci_discovery.py`

**Responsibilities:**
- Detect test framework (reuse TestRunner.detect())
- Run tests and parse output
- Extract failing tests + error messages
- Group by source module
- Return structured failure list

**Key functions:**
```python
def discover_ci_failures(repo_root: str) -> list[dict]:
    """
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
    """
    Returns: {
        "auth.py": [{test_name, error_msg, ...}, ...],
        "billing.py": [...],
    }
    """
```

### 4.2 New Module: `scaffold/agent/ci_planner.py`

**Responsibilities:**
- Generate CI-specific goal from failures
- Generate task list from failures (v1: deterministic, v2: LLM-based)

**Key functions:**
```python
def generate_ci_goal(failures: list[dict]) -> str:
    """
    Input: failure list
    Output: "Fix 12 failing tests in auth.py, billing.py, inventory.py.
             Do not edit tests/. Keep 7 regression tests green."
    """

def generate_ci_plan(failures: list[dict]) -> list[dict]:
    """
    Input: failure list
    Output: [
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

### 4.3 New CLI Command: `awos fix-ci`

**Location:** `awos.py` — add `cmd_fix_ci(args)` function

**Flow:**
```python
def cmd_fix_ci(args):
    repo_root = args.root or "."
    
    # 1. Discover failures
    print("[CI] Discovering test failures...")
    failures = discover_ci_failures(repo_root)
    if not failures:
        print("[CI] No failing tests found. CI is green!")
        return
    
    # 2. Group by module
    grouped = group_failures_by_module(failures)
    print(f"[CI] Found {len(failures)} failing tests in {len(grouped)} modules")
    
    # 3. Generate goal + plan
    goal = generate_ci_goal(failures)
    plan = generate_ci_plan(failures)
    
    # 4. Call orchestrator
    print(f"[CI] Starting CI rescue...")
    wrapped_args = argparse.Namespace(
        goal=goal,
        root=repo_root,
        pre_planned_tasks=plan,
        auto_approve=True,
        resume=False,
        session_id=None,
    )
    cmd_run(wrapped_args)
    
    # 5. Show results + cost comparison
    print_ci_results(failures, result)
```

### 4.4 Display Functions

**Location:** `awos.py` or new module `scaffold/agent/ci_display.py`

**Functions:**
```python
def print_ci_results(failures: list[dict], result: dict):
    """
    Output:
    ════════════════════════════════════════════════════════
      AWOS Coding Agent — CI Repair Complete
    ════════════════════════════════════════════════════════
    
      Repo:   my-broken-repo
      Time:   4m 32s (unattended)
      Cost:   ₹0.35 (included in your ₹2,000/month plan)
    
      Results:
        ✅ auth/test_login.py         — fixed (DeepSeek, $0.001)
        ✅ billing/test_invoice.py    — fixed (DeepSeek, $0.001)
        ⚠️ shipping/test_rates.py    — 3 retries, needs review
    
      Summary: 3/4 fixed · 12 tests green
    
      Your savings this month:
        This fix:    ₹0.35 (included in plan)
        If on Cursor: ~₹1.50 (manual prompting)
        This month total saved: ₹8,400 vs manual debug time
    ════════════════════════════════════════════════════════
    """
```

---

## 5. Risks and Edge Cases

### 5.1 Test Framework Detection

**Risk:** TestRunner.detect() may fail on repos with non-standard test setups.
**Mitigation:** Fall back to `pytest` as default for Python repos.

### 5.2 Failure Parsing

**Risk:** pytest output format may vary (different verbosity levels, plugins).
**Mitigation:** Use `pytest --tb=short -q` for consistent output. Parse with regex.

### 5.3 Cross-Module Dependencies

**Risk:** Fixing one module may break another (e.g., auth.py depends on billing.py).
**Mitigation:** v1: accept this limitation. v2: use Planner to understand dependencies.

### 5.4 Large Repos

**Risk:** Repos with 100+ failing tests may be too complex for v1.
**Mitigation:** Limit v1 to repos with <50 failing tests. Show warning for larger repos.

### 5.5 Non-Python Repos

**Risk:** v1 only supports Python + pytest.
**Mitigation:** Show clear error message: "v1 supports Python repos only. JavaScript/Go/Rust support coming in v2."

---

## 6. Success Criteria

### 6.1 Functional

- [ ] `awos fix-ci` runs on any Python repo with pytest
- [ ] Discovers failing tests automatically
- [ ] Fixes tests without editing test files
- [ ] Shows transparent routing (model + cost per task)
- [ ] Shows cost comparison vs Cursor

### 6.2 Performance

- [ ] Discovery phase: <5 seconds
- [ ] Planning phase: <2 seconds (deterministic plan generation)
- [ ] Total time: <10 minutes for repos with <20 failing tests

### 6.3 Cost

- [ ] Cost per fix: <₹1 (using DeepSeek routing)
- [ ] Cost comparison: shows 50%+ savings vs Cursor

### 6.4 UX

- [ ] One command: `awos fix-ci`
- [ ] No configuration needed (auto-detect test framework)
- [ ] Clear output: what was fixed, what failed, cost, savings

---

## 7. Related

- [[VISION]] — product identity (learnable OS + self-improving coding agent)
- [[CI_RESCUE_PRODUCT]] — CI repair capability spec
- [[TRANSPARENT_ROUTING]] — routing transparency UX spec
- [[memories/repo/project_structure]] — canonical facts
- [[pivot_web_app_product]] — active task file
- `scaffold/agent/orchestrator.py` — main execution engine
- `scaffold/agent/test_runner.py` — test framework detection
- `docs/missions/ci_rescue_sprint.json` — existing CI rescue mission

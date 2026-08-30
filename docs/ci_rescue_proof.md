# CI Repair Benchmark — Live Proof

> **Feature:** `awos fix-ci` workflow + benchmark-facing dashboard/API surfaces
> **Date:** 2026-07-03
> **Status:** ✅ PROVEN
> **Identity note:** This document records a benchmark proof. It does **not** define AWOS as a CI product.

---

## What was built

1. **`awos fix-ci` command** — one-command **CI repair benchmark** workflow for a Python repo
2. **Benchmark dashboard/API surfaces** — stats, history, and session inspection for this workload
3. **`awos serve` command** — starts the web server used to inspect sessions

---

## Why this proof matters

The CI repair benchmark is useful because it exercises:
- failure discovery
- deterministic planning
- bounded repair work
- verification
- retry and recovery behavior
- routing quality under a repeatable workload

This makes it a good proving workload for the coding agent.
It is a benchmark, not the top-level identity.

---

## Live proof: `awos fix-ci` on wedge corpus

### Command

```bash
cd tests/fixtures/wedge_v1/ci_rescue_sprint
awos fix-ci
```

### Observed output

```text
══════════════════════════════════════════════════════════════════
  AWOS Coding Agent — CI Repair
══════════════════════════════════════════════════════════════════

[CI] Found 28 failing tests in 6 modules:
  • auth.py: 5 failures
  • billing.py: 5 failures
  • integration.py: 3 failures
  • inventory.py: 6 failures
  • promo.py: 5 failures
  • shipping.py: 4 failures

[CI] Generating benchmark repair plan...
[CI] Plan: 28 tasks (one per failure)

[CI] Starting benchmark run...
[SESSION] Runtime session rs_ef88d156bb85 (running)
[WORKTREE] Isolated sandbox: .../worktrees/ef88d156bb85
[GIT] Working on branch 'awos/ci-rescue-fix-28-failing-tests-in-auth-p'
[SYMBOLS] SymbolIndex: 17 files, 81 symbols, 8 dependencies
...
```

### Success markers

- ✅ `[CI] Found 28 failing tests in 6 modules` — discovery works
- ✅ `[CI] Plan: 28 tasks (one per failure)` — plan generation works
- ✅ `[SESSION] Runtime session rs_ef88d156bb85 (running)` — orchestrator starts
- ✅ `[WORKTREE] Isolated sandbox` — worktree isolation works
- ✅ `[GIT] Working on branch` — git branch created

---

## Live proof: benchmark dashboard/API surfaces

### Command

```bash
awos serve
# Opens http://127.0.0.1:8765
```

### Observed output

**Dashboard view at the time of proof:**
- showed a coding-agent-oriented dashboard surface for benchmark sessions
- displayed aggregate stats and recent runs for this workload
- allowed session inspection through the server API

**Historical endpoint names in the implementation:**

```bash
curl http://127.0.0.1:8765/api/ci-rescue/stats
```

```json
{
  "total_rescues": 3,
  "total_tests_fixed": 30,
  "total_cost_usd": 0.0,
  "trend_rescues": "",
  "trend_tests": "",
  "trend_cost": "",
  "trend_savings": ""
}
```

```bash
curl http://127.0.0.1:8765/api/ci-rescue/history
```

```json
[
  {
    "session_id": "rs_ef88d156bb85",
    "goal": "CI Rescue: Fix 28 failing tests...",
    "timestamp": "2026-07-03T12:04:58Z",
    "status": "running",
    "success": false,
    "tasks_completed": 0,
    "tasks_failed": 0,
    "total_cost_usd": 0.0,
    "tests_fixed": 0
  },
  {
    "session_id": "rs_a3e7951197ab",
    "goal": "CI Rescue on clawcode...",
    "timestamp": "2026-06-26T05:56:48Z",
    "status": "completed",
    "success": true,
    "tasks_completed": 12,
    "tasks_failed": 0,
    "total_cost_usd": 0.0,
    "tests_fixed": 12
  },
  {
    "session_id": "rs_d39c498dd4ca",
    "goal": "CI Rescue Sprint: fix auth.py...",
    "timestamp": "2026-06-25T14:07:51Z",
    "status": "completed",
    "success": true,
    "tasks_completed": 18,
    "tasks_failed": 0,
    "total_cost_usd": 0.0,
    "tests_fixed": 18
  }
]
```

Those payloads are preserved here as historical implementation evidence.
The old endpoint naming remains in the code path shown above, but the correct framing is **CI repair benchmark**.

### Success markers

- ✅ Dashboard/server loads at `http://127.0.0.1:8765`
- ✅ Stats API returns benchmark run counts
- ✅ History API returns session list
- ✅ Benchmark sessions are visible and reviewable

---

## Test results

```bash
python3 -m pytest tests/test_ci_discovery.py tests/test_ci_planner.py -v
```

```text
20 passed in 3.42s
```

### Test coverage

- **`ci_discovery.py`** (6 tests)
  - discover failures on wedge fixture (28 reds)
  - return structured failures
  - handle empty repo
  - group failures by module
  - convenience function
  - module inference

- **`ci_planner.py`** (14 tests)
  - goal generation (includes count, modules, constraints)
  - plan generation (one task per failure, sequential IDs, grouped by module)
  - grouped plan (one task per module, lists tests, shows count)
  - empty failure handling

---

## Files created

| File | Purpose | LOC |
|------|---------|-----|
| `scaffold/agent/ci_discovery.py` | Test failure discovery + parsing | ~280 |
| `scaffold/agent/ci_planner.py` | CI goal + plan generation | ~180 |
| `scaffold/agent/ci_display.py` | Terminal output formatting | ~150 |
| `tests/test_ci_discovery.py` | Unit tests for discovery | ~100 |
| `tests/test_ci_planner.py` | Unit tests for planner | ~120 |
| `gui/static/dashboard.html` | Benchmark dashboard view at the time | ~200 |
| `gui/static/dashboard.js` | Dashboard logic | ~150 |

---

## What this proves

1. ✅ **CI failure discovery** — runs pytest, parses output, extracts failures
2. ✅ **Plan generation** — creates deterministic repair tasks from failures
3. ✅ **Orchestrator integration** — calls into the agent loop with planned tasks
4. ✅ **Benchmark inspection surface** — server/API can show benchmark runs
5. ✅ **Repeatable workload** — useful for comparing routing, verification, and recovery quality

---

## Limits of this proof

This proof does **not** establish that:
- AWOS is fundamentally a CI product
- the app should be branded around CI repair
- self-learning is complete
- the OS vision is complete

It proves one important benchmark workload for the coding agent.

---

## Next value from this benchmark

1. compare model/routing choices on a repeatable workload
2. inspect where verification blocks false greens
3. capture failure patterns for future learning
4. use benchmark results to strengthen the general coding agent

---

## Related

- [[CI_RESCUE_PRODUCT]] — CI repair benchmark capability spec
- [[TRANSPARENT_ROUTING]] — routing transparency spec
- [[pivot_web_app_product]] — active task file
- [[memories/repo/project_structure]] — canonical facts
- [[VISION]] — canonical identity

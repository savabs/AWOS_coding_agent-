---
title: "Checkpoint 2026-05-15 — Sprint 2 Complete (Layer 2–3 Dispatcher)"
tags:
  - doc/checkpoint
  - phase/2-3
  - topic/awos-v2
  - status/done
date: 2026-05-15
---

## Sprint 2 Complete: Layer 2–3 Dispatcher (HITL + Routing)

**Completed in one session.** All 8 steps built and tested.

### What Was Built

**Core Components (5 modules):**

1. **ComplexityScorer** (`scaffold/agent/complexity_scorer.py`)
   - Heuristic scoring: lines of code, cyclomatic complexity, import depth, nesting
   - Output: 1–10 scale (trivial to expert-required)
   - Tested on simple and complex code

2. **ModelRouter** (`scaffold/agent/model_router.py`)
   - Maps complexity → model tier with pricing
   - 1–3: DeepSeek (budget, $0.14/MTok)
   - 4–7: Claude-Haiku (balanced, $0.80/MTok)
   - 8–10: Claude-Opus (expert, $15.0/MTok)

3. **PreflightManifest** (`scaffold/agent/preflight_manifest.py`)
   - Generates XML manifest of what agent will do
   - Includes: task, complexity, model tier, cost estimate, files involved
   - Well-formed XML, timestamped, stored in `.awos/preflight_*.xml`

4. **HITLConsent** (`scaffold/agent/hitl_consent.py`)
   - Display manifest to user
   - Request approval (default: DENY — explicit user consent required)
   - Log decision to COST_LOG.xml (approve/deny with metadata)

5. **Dispatcher** (`scaffold/agent/dispatcher.py`)
   - Orchestrates full pipeline: complexity → routing → manifest → consent
   - Returns DispatchDecision with approval status, model config, cost estimate

**Integration (1 module):**

6. **CLI Enhancement** (`scaffold/agent/cli.py`)
   - New subcommand: `awos dispatch <task> <repo> [--files ...] [--no-confirm]`
   - Shows manifest, asks for approval, logs decision
   - Exit code: 0 if approved, 1 if denied

**Output Files:**

- `.awos/preflight_*.xml` — One file per dispatch call, human-reviewable
- `.awos/COST_LOG.xml` — Append-only log of all decisions (3 entries verified)

### Tested Flows

**Flow 1: Interactive Approval**
```bash
echo "y" | python3 scaffold/agent/cli.py dispatch "Add tests to module" .
# Output: ✓ APPROVED, logged to COST_LOG.xml
```

**Flow 2: Interactive Denial**
```bash
echo "n" | python3 scaffold/agent/cli.py dispatch "Refactor database layer" .
# Output: ✗ DENIED, logged to COST_LOG.xml
```

**Flow 3: Auto-Deny (HITL Default)**
```bash
python3 scaffold/agent/cli.py dispatch "..." . --no-confirm
# Output: ✗ DENIED (always, unless --approve flag added later)
```

### Key Design Decisions

1. **Fail-Closed HITL:** Default deny. User must explicitly type "y" or "yes".
2. **No Test Overhead:** Focused on workable product, not test coverage.
3. **Simple Heuristics:** Complexity scoring uses basic metrics (not ML), avoids dependencies.
4. **Atomic Components:** Each module independently testable and reusable.
5. **XML for Persistence:** Manifests and COST_LOG are human-readable, Git-friendly.

### Current State

| Artifact | Location | Status |
|---|---|---|
| ComplexityScorer | `scaffold/agent/complexity_scorer.py` | ✅ Tested |
| ModelRouter | `scaffold/agent/model_router.py` | ✅ Tested |
| PreflightManifest | `scaffold/agent/preflight_manifest.py` | ✅ Tested |
| HITLConsent | `scaffold/agent/hitl_consent.py` | ✅ Tested |
| Dispatcher | `scaffold/agent/dispatcher.py` | ✅ Tested end-to-end |
| CLI integration | `scaffold/agent/cli.py` | ✅ Tested both flows |
| COST_LOG.xml | `.awos/COST_LOG.xml` | ✅ 3 decisions logged |
| Task tracker | `tasks/active/sprint2_dispatcher_task.md` | ✅ All 8 steps marked done |

### What's Workable Now

User can run:
```bash
cd /home/becmachlean/2024/projects/AWOS_coding_agent
echo "y" | python3 scaffold/agent/cli.py dispatch "Your task here" .
```

And the system will:
1. Analyze code complexity
2. Select appropriate model tier
3. Generate preflight manifest (human-readable)
4. Show manifest and ask for approval
5. Log approval/denial to COST_LOG.xml
6. Return success/failure to shell

### Next Steps (Sprint 3)

Sprint 3 = **Layer 4–5 Hydration + Soul** (symbol hydration, prompt caching, SOUL.xml)

This will add:
- Symbol extraction from STRUCT.xml
- Context hydration (load relevant code into prompt)
- Prompt caching (90% token reduction on repeated contexts)
- SOUL.xml persistence (rules, patterns, decisions, cost history)

### Definition of Done ✅

- [x] All 8 steps complete
- [x] User can run `awos dispatch <task> <repo>` end-to-end
- [x] Manifest displays, consent prompt works, approval/denial logged
- [x] No crashes on valid input
- [x] COST_LOG.xml tracks all decisions
- [x] Workable product (no tests, just working code)

---

## Related
- Spec: [[awos_v2_blueprint_spec]]
- Task: [[sprint2_dispatcher_task]]
- Previous: [[checkpoint_2026-05-15_awos_v2_preflight]]
- Sprint 1: [[sprint1_layer1_task]] (TreeSitter, STRUCT.xml, FileWatcher)

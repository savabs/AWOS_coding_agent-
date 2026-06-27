---
title: "Checkpoint: 2026-06-12"
tags:
  - doc/checkpoint
  - phase/v1_validation
  - feature/cheap-only
  - feature/token-metrics
date: 2026-06-12
---

# Session Checkpoint — 2026-06-12

> **Canonical session memory for work through 2026-06-12.**
> Next agent: read this after `VISION.md` and `memories/repo/project_structure.md`.

---

## One-line status

**AWOS v1 self-validation is underway on our own repo in cheap-only mode.** Token-first PEI, visual diff after verify, and verifier fidelity fixes are in place. Cost target met (~$0.001/task). Quality ~50–62%. Need 5–10 more validation runs before agency outreach.

---

## What we built (this arc)

### Identity & GTM docs
| Artifact | Purpose |
|---|---|
| `VISION.md` v1.2 | Canonical manifesto — compression thesis, v1 done criteria, determining filter |
| `docs/V1_VALIDATION.md` | Self-proof benchmark log + pass criteria |
| `docs/NICHE_GTM.md` | Agency-first go-to-market |
| `docs/AGENCY_ONE_PAGER.md` | Sales one-pager |
| `README.md` | Repositioned for learnable OS (not Cursor competitor) |

**Last pushed commit:** `b644839` — token-first PEI, cheap-only routing, GTM docs

### Token-first metrics (not flat tier estimates)
- `scaffold/agent/usage_record.py` — record real API tokens, derive cost
- `worker.py`, `orchestrator.py` — wire tokens to spans, reward_store, budget
- `pei_report.py` — PEI = Quality × Speed ÷ **Tokens** (tokens section first)
- `awos.py cmd_budget` — tokens primary, cost secondary

### Cheap-only mode (`AWOS_CHEAP_ONLY=true`)
- `escalation_engine.py` — `is_cheap_only()`, cap at GPT-4o-mini, rotate DeepSeek↔OpenAI on retry
- `worker.py` — block Anthropic; `_try_cheap_fallback()` for alternate cheap providers
- `orchestrator.py` — CheapPlanner first; no Haiku in `_cheap_call`
- `critic_engine.py` — no Haiku when cheap-only
- `.env`: `AWOS_CHEAP_ONLY=true`, `AWOS_PREMIUM_BUDGET=0`
- `tests/test_cheap_only.py`

### Validation hardening (uncommitted as of checkpoint)
| Module | Change |
|---|---|
| `integration_reviewer.py` | Cheap-only uses DeepSeek or skips; never Sonnet; filters noise |
| `verifier.py` | `_check_edit_fidelity()` — rejects inner docstring when goal says "comment above `symbol`" |
| `cheap_planner.py` | `_normalize_plan_paths()` — bare filenames → `scaffold/agent/` |
| `run_diagnostics.py` | Per-task stage tracing + **`print_visual_diff()`** after verify |
| `orchestrator.py` | RUN DIAGNOSTICS blocks + visual diff box after successful verify |
| `tests/test_verifier_fidelity.py` | Fidelity gate tests |
| `tests/test_integration_reviewer.py` | Integration reviewer tests |

### Visual verification UX (user request)
After `[TASK N] VERIFIED`, terminal shows:
```
┌─ APPLIED CHANGE (visual) ───────────────────────────────
│  File: scaffold/agent/worker.py
│  -  removed lines
│  +  added lines
└────────────────────────────────────────────────────────
  → Review: git diff scaffold/agent/worker.py
  → Open:   scaffold/agent/worker.py
```

---

## v1 validation runs (on own repo)

| # | Date | Goal | Result | Cost | Notes |
|---|---|---|---|---|---|
| 1 | 2026-06-12 | Docstring on `usage_record.py` | ✓ | ~$0.0006 | DeepSeek, 19s |
| 2 | 2026-06-12 | Docstring on `is_cheap_only()` | ✗ | ~$0.0002 | Planner wrong path `./escalation_engine.py` |
| 3 | 2026-06-12 | Comment on `budget_ledger.get_status` | ✓ | ~$0.0009 | Full path in goal fixed routing |
| 4 | 2026-06-12 | `#` comment above `_try_cheap_fallback` | ✓* | ~$0.0013 | *Wrong placement (inner docstring only) — pre-fidelity fix |
| 5 | 2026-06-12 | Same goal (post-fidelity fix) | ✓ | ~$0.0010 | Correct `#` above `def _try_cheap_fallback` |
| 6 | 2026-06-12 | Same goal (visual diff demo) | ✓ | ~$0.0013 | Visual diff box confirmed in output |

**Pass criteria status:**

| Criterion | Target | Status |
|---|---|---|
| Avg cost/task | < $0.002 | **PASS** (~$0.001/task) |
| Task success rate | ≥ 45% | **PASS** (~50–62% on recent spans) |
| Premium models | 0 | **PASS** (cheap-only enforced) |
| Volume | 10–15 runs | **IN PROGRESS** (~6 done) |
| Agency outreach | after volume pass | **PENDING** |

**Session spend (cheap-only era):** ~$0.0096 total · 64,529 tokens · 14 API calls (`awos budget`)

---

## Key findings

1. **Historical spans skew metrics** — 94% of estimated spend from 43% premium tasks (Haiku/Sonnet). Trust `[OBS]` line and `awos budget` for new runs, not rolling METRICS panel alone.
2. **Cheap-only economics** — ~$0.50/mo vs ~$23 at same volume with premium.
3. **Self-learning not compounding yet** — PromptEvolver 0 guidelines; 636 error patterns mostly UNKNOWN.
4. **Integration reviewer false positives** — hallucinates issues from truncated diffs (e.g. `integration_reviewer.py` imports).
5. **Goals must use full paths** — `scaffold/agent/<file>.py` and name the symbol.

---

## Verified example change (run 5/6)

```python
    # Tries alternate cheap providers.
    def _try_cheap_fallback(
```

`git diff scaffold/agent/worker.py` confirms `#` comment directly above `def`, not only in docstring.

---

## Environment

```bash
AWOS_CHEAP_ONLY=true
AWOS_PREMIUM_BUDGET=0
AWOS_PROMPT_EVOLUTION=true
AWOS_LIVE_TOOLS=true
```

## CLI quick reference

```bash
python3 awos.py run "In scaffold/agent/<file>.py <goal>"   # goal is positional
python3 awos.py budget
python3 awos.py report --html reports/pei_report.html
git diff scaffold/agent/<file>.py                          # visual verify
```

---

## Still broken / unwired (not fixed this arc)

- PRM import path
- MCTS traces
- PER unused in production loop
- Dual VectorMemory instances
- `session_dreaming.py` — placeholder only (SOUL not auto-updated)
- Integration reviewer noise on partial diffs

---

## Validation task queue (NEW)

15 project tasks in `docs/v1_validation_tasks.json`. Progress via:

```bash
python3 awos.py validate           # dashboard: 3/15 done, 12 pending
python3 awos.py validate run       # run next task
python3 awos.py validate run --all
```

State: `.awos/v1_validation_progress.json`

## Next steps (priority order)

1. **Commit uncommitted validation fixes** — verifier fidelity, run_diagnostics, visual diff, cheap_planner paths, integration_reviewer, tests, validation queue
2. **Run validation queue** — `awos validate run` until 15/15 (or `run --all` ~$0.01 budget)
3. **Tune integration reviewer** — suppress false positives on truncated diffs
4. **Optional:** `awos run --show-diff` flag; tighten verifier to reject docstring-only when goal says "comment above"
5. **Agency outreach** — only after 10+ stable validation runs

---

## Uncommitted files (as of checkpoint)

```
M  scaffold/agent/budget_ledger.py
M  scaffold/agent/cheap_planner.py
M  scaffold/agent/integration_reviewer.py
M  scaffold/agent/orchestrator.py
M  scaffold/agent/verifier.py
M  scaffold/agent/worker.py
?? scaffold/agent/run_diagnostics.py
?? docs/V1_VALIDATION.md
?? tests/test_verifier_fidelity.py
?? tests/test_integration_reviewer.py
```

---

## Cold-start read order

```
1. VISION.md
2. memories/repo/project_structure.md
3. docs/memory/checkpoint_2026-06-12.md   ← this file
4. docs/V1_VALIDATION.md
5. tasks/active/*.md
```

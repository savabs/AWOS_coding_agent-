---
title: "Checkpoint: GUI hardening + M1 PromptEvolver — 2026-06-16"
tags:
  - doc/checkpoint
  - feature/gui
  - feature/self-learning
  - phase/m1-prompt-evolver
date: 2026-06-16
immutable: true
---

# Session Checkpoint — 2026-06-16

> **Immutable record of this session.**
> **Prior GUI checkpoint:** `docs/memory/checkpoint_gui_layer_2026-06-12.md`
> **Self-learning spec:** `tasks/active/true_self_learning_task.md`
> **Open doc in editor:** `HOW_TO_CODE_SELF_LEARNING_AGENT.md`

---

## One-line status

**GUI chat is operable (delete, stop, full-screen, partial replies). Agent kernel validated 15/15. M1 PromptEvolver hardening is coded and tested but not yet run live — learning loop still unproven in production `.awos/`. Agent Stop does not kill the orchestrator (biggest trust gap).**

---

## Session arc (what happened)

### Phase A — GUI chat management
User wanted Hermes/Claude Code-style chat UX: delete chats, clear all, full-viewport layout, working stop with partial output kept.

### Phase B — Validation + progress assessment
Ran v1 validation (`python3 awos.py validate run --all`) → **15/15 passed**. Fixed broken import in `validation_runner.py` (`from scaffold.agent.orchestrator import Orchestrator`).

### Phase C — Learning loop deep dive → M1 implementation
User approved implementing **M1 PromptEvolver hardening** (“research long and code well”). Durable session counter, versioned candidates, KPI accept/reject gate, orchestrator wiring, observability updates, tests.

### Phase D — Background task clarification (end of session)
User asked what the “background task” was doing. Clarified:
- **Background process on machine:** `python3 gui/server.py --port 8765` (idle HTTP server from a prior agent session).
- **“Agent may still be running in background” UI label:** honest — Stop only cancels SSE/polling, not `Orchestrator.execute_feature()` in the daemon thread.

---

## What was built this session

### GUI — chat shell

| Area | Files | Behavior |
|------|-------|----------|
| Delete / clear | `scaffold/agent/gui_chat.py`, `gui/server.py` | `DELETE` + `POST` clear/delete endpoints; per-chat × and Clear all |
| Stop | `gui_chat.py`, `gui/server.py`, `gui/static/chat.js` | Send→Stop, `AbortController`, `POST /api/chat/stop`, SSE drain on disconnect |
| Partial on stop | `gui_chat.py`, `chat.js` | Keeps partial reply; no `_Stopped by user._` suffix on text; `finalizeAfterStop()` polls server |
| Full-screen layout | `gui/static/style.css`, `index.html`, `shared.js` | Viewport-fitted flex shell, not fixed min-heights |
| Tests | `tests/test_gui_chat_store.py` | delete, stop_run, partial reply helpers |

**Known GUI limitation (not fixed):** In **Agent** mode, `_stream_agent()` spawns a `daemon=True` thread calling `orch.execute_feature()`. `cancel.set()` only breaks the event tail loop — orchestrator keeps running until it finishes naturally.

### M1 — PromptEvolver hardening (Feature 1A)

| Component | File | Purpose |
|-----------|------|---------|
| Durable state | `scaffold/agent/learning_state.py` **(new)** | `.awos/learning_state.json` — session counter, KPI window, candidate probation |
| Versioned prompts | `scaffold/agent/prompt_evolver.py` | `.awos/prompt_versions/vNNNN.json`, candidate → accept/rollback |
| Orchestrator wire-up | `scaffold/agent/orchestrator.py` | `record_session()` at end of `execute_feature()`; `start_candidate()` not immediate `persist()`; evolution LLM uses 512 max tokens |
| Observability | `scaffold/agent/self_learning_metrics.py` | Shows sessions completed, version, status, candidate probation |
| Tests | `tests/test_learning_state.py` **(new)**, `tests/test_prompt_evolver.py` | 55 tests pass across learning + evolver + e2e pipeline |

### Validation fix

| File | Fix |
|------|-----|
| `scaffold/agent/validation_runner.py` | Import `Orchestrator` from `scaffold.agent.orchestrator` (was broken) |

---

## Learning loop flow (M1 — as implemented)

```
Session completes
  → learning_state.record_session(success, cost, tasks_failed, tasks_total, used_candidate)
  → if candidate in probation: record_candidate_session → maybe_finalize_candidate
       → accepted: prompt_evolver.commit_candidate()
       → rejected: prompt_evolver.rollback_candidate()
  → if should_evolve(sessions) and no active candidate:
       evolve() → start_candidate() (probation, not instant active)
```

**Env vars:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `AWOS_PROMPT_EVOLUTION` | off | Must be `true` |
| `AWOS_PROMPT_EVOLVE_EVERY` | `10` | Evolve every N sessions |
| `AWOS_PROMPT_ACCEPT_WINDOW` | `5` | Sessions to evaluate candidate |
| `AWOS_PROMPT_MIN_SUCCESS_DROP` | `0.05` | Max success-rate regression allowed |
| `AWOS_PROMPT_MAX_COST_INCREASE` | `0.25` | Max cost increase allowed |

---

## State at session end

### Three layers

```
┌─────────────────────────────────────────────────────────┐
│  GUI (Chat + Runs)     — MVP operable, Stop is UI-only  │
├─────────────────────────────────────────────────────────┤
│  Agent kernel          — PROVED (15/15 validation)      │
├─────────────────────────────────────────────────────────┤
│  Self-learning loop    — CODED (M1), NOT PROVEN LIVE  │
└─────────────────────────────────────────────────────────┘
```

### Metrics / artifacts (`.awos/` at session end)

| Artifact | Status |
|----------|--------|
| `learning_state.json` | **Not created yet** — no post-M1 validation run with evolution enabled |
| `evolved_prompt.json` | **Not present** — prompt never evolved in live runs |
| `error_patterns.jsonl` | ~**637** patterns, ~**600 UNKNOWN** (poor signal for evolution) |
| `v1_validation_progress.json` | **15/15** tasks passed |
| PEI / task success | ~51% overall, ~$0.0008/task (from prior assessment) |
| Skills indexed | 0 |
| Tools synthesized | 0 |

### Tests

- `tests/test_learning_state.py` + `tests/test_prompt_evolver.py` + `tests/test_self_learning_e2e.py` → **55 passed**
- Full suite not re-run this session

### Git

- **Many changes uncommitted** (GUI, M1, validation_runner, docs, etc.)
- Last commit on branch: `b644839` — token-first PEI reporting, cheap-only routing
- User did not request a commit this session

### Processes

- GUI server may still be running: `python3 gui/server.py --port 8765` on `127.0.0.1:8765`
- Kill: `kill $(pgrep -f 'gui/server.py')` or restart after code changes

---

## Bugs fixed this session

| Issue | Cause | Fix |
|-------|-------|-----|
| `501` on clear chats | Old server without `do_DELETE` | `POST /api/chat/sessions/clear` + `/delete` |
| Stop wiped reply | Cancel before save; client abort | Process events before break; SSE drain; `finalizeAfterStop()` |
| `validate run` crashed | Wrong orchestrator import | `scaffold.agent.orchestrator` |
| Evolution JSON truncated | `_cheap_call` max_tokens=120 | Prompt evolution uses `max_tokens=512` |

---

## What's blocked / not done

| Gap | Impact | Notes |
|-----|--------|-------|
| **Agent Stop ≠ orchestrator cancel** | User trust, wasted tokens/files | `_stream_agent` daemon thread has no cancel hook into `execute_feature()` |
| **M1 not proven live** | Learning loop theoretical | Need runs with `AWOS_PROMPT_EVOLUTION=true` |
| **Error patterns mostly UNKNOWN** | Evolution gets garbage signal | Fix failure typing in worker/verifier/post_mortem |
| **GUI server restart** | Stale backend after edits | `python3 gui/server.py` + hard refresh browser |
| **E2E browser tests** | `tests/test_gui_browser.py` exists, not run this session | — |
| **Scaffold self-mutation (1C)** | Deferred | Too risky before stop + prompt loop proven |

---

## Decisions made

| Decision | Rationale |
|----------|-----------|
| M1 before more GUI polish | Kernel proved; learning loop was the strategic gap |
| Candidate probation + KPI gate | Don’t activate evolved prompts without measured accept/reject |
| Durable `learning_state.json` | In-memory `_session_count` never survived new `Orchestrator()` |
| Keep `evolved_prompt.json` as worker hot-load pointer | Backward compat with `Worker` and existing tests |
| Honest `agent_may_continue` label | Stop only cancels stream, not orchestrator — don’t lie in UI |

---

## Probable next step (start here next session)

### **Priority 1: Real Agent cancel** ← recommended

Wire cancel from GUI → orchestrator so Stop actually stops work.

**Scope:**
1. Pass `threading.Event` (or shared cancel token) from `gui_chat._stream_agent` → `Orchestrator.execute_feature`
2. Check cancel between tasks, worker retries, and cheap LLM calls
3. On cancel: clean exit, rollback in-flight file edits, save partial run to `.awos/gui/orch_*`
4. Remove or replace `agent_may_continue` — Stop means stop

**Acceptance:** Hit Stop mid-agent-run → orchestrator thread exits within seconds; no new writes after stop.

### **Priority 2: Prove M1 learning loop live**

```bash
export AWOS_PROMPT_EVOLUTION=true
export AWOS_PROMPT_EVOLVE_EVERY=3
export AWOS_PROMPT_ACCEPT_WINDOW=2
python3 awos.py validate run --count 9
python3 -c "from scaffold.agent.self_learning_metrics import SelfLearningMetrics; SelfLearningMetrics().print_report()"
```

Confirm: `learning_state.json`, `prompt_versions/v0001.json`, accept/reject firing.

### **Priority 3: Fix error pattern typing**

Investigate why ~94% of `error_patterns.jsonl` is `UNKNOWN`. Without real failure types, PromptEvolver evolves noise.

### Sequence

```
Real cancel  →  prove M1 evolution  →  fix error typing  →  measure success-rate delta
```

---

## Next session starting point

Read in order (~5 min):

1. This checkpoint
2. `tasks/active/true_self_learning_task.md` — Feature 1A–1C spec
3. `scaffold/agent/learning_state.py` + `prompt_evolver.py` — M1 implementation
4. `scaffold/agent/gui_chat.py` lines ~578–675 — Agent stop gap

**Quick commands:**

```bash
# GUI
python3 gui/server.py --port 8765
# → http://127.0.0.1:8765 (hard refresh after static changes)

# Validation
python3 awos.py validate run --count 3

# Learning tests
python3 -m pytest tests/test_learning_state.py tests/test_prompt_evolver.py tests/test_self_learning_e2e.py -q

# Self-learning report
python3 -c "from scaffold.agent.self_learning_metrics import SelfLearningMetrics; SelfLearningMetrics().print_report()"
```

---

## Files changed / added this session (high signal)

```
scaffold/agent/learning_state.py              [new — M1 durable state]
scaffold/agent/prompt_evolver.py              [modified — versioning, candidate gate]
scaffold/agent/orchestrator.py                [modified — learning_state wire-up, max_tokens]
scaffold/agent/self_learning_metrics.py       [modified — version/status in report]
scaffold/agent/gui_chat.py                    [modified — delete, stop, partial reply]
scaffold/agent/validation_runner.py           [modified — import fix]
gui/server.py                                 [modified — clear/delete/stop APIs, SSE drain]
gui/static/chat.js, style.css, shared.js      [modified — stop UX, layout]
gui/static/index.html                         [modified — layout]
tests/test_learning_state.py                  [new]
tests/test_gui_chat_store.py                  [new]
tests/test_prompt_evolver.py                  [modified — versioning tests]
HOW_TO_CODE_SELF_LEARNING_AGENT.md            [new — user-facing doc, not from this session's code work]
```

---

## Related

- `docs/memory/checkpoint_gui_layer_2026-06-12.md` — GUI v0 origin
- `docs/specs/gui_layer_spec.md` — canonical GUI spec
- `docs/v1_validation_tasks.json` — 15 validation tasks
- `HOW_TO_CODE_SELF_LEARNING_AGENT.md` — self-learning concepts (open in IDE)

---

*Session ended 2026-06-16. Next agent: start with real Agent cancel unless user directs otherwise.*

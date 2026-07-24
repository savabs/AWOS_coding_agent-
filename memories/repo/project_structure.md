# AWOS — Project Structure

> **Canonical owner for all project metrics and identity facts.**
> All other files must [[link]] here; they must not copy these values.
> Product identity: see [[VISION]].

---

## Identity

**AWOS** is an adaptive engineering runtime — a greedy meta-AI that optimizes **quality × speed ÷ cost** on every project, shows every routing decision transparently, and gets more reliable at your codebase every week.

**Customer-facing tagline:** "The AI engineering system that learns your codebase."

**Mission:** Build the execution and learning layer that autonomous agents rely on. Start with CI Rescue for engineering agencies. Expand to any domain where repeated work benefits from continuous adaptation.

**Strategic edge:** Transparent routing (show model + reasoning + past success rate + override), self-learning kernel (PromptEvolver, LiveToolSynthesizer, LinUCB, SkillLibrary) with `.awos/` state that compounds reliability. Not a Cursor competitor — a different category: unattended verified batches + compounding intelligence.

**Pivot (2026-07-03):** From "learnable OS for autonomous agents" to "adaptive engineering runtime." From black-box routing to glass-box transparency. From broad API play to CI Rescue wedge for engineering agencies.

**Moat:** Not secrecy. Transparency + learning loop + compounding `.awos/` state. Anyone can show the model name. Nobody can show your repo's success rate going from 82% to 94% over 6 months because the system learned your codebase, your error patterns, your coding style.

**First product:** CI Rescue wedge. `awos fix-ci` → red tests → green. 28 tests fixed, $0.0056 total, 49 seconds. Proven on wedge corpus (semantic_pass). See [[CI_RESCUE_PRODUCT]].

**Coding agent:** App #1 on the kernel. Kernel is portable to research, support, ops, and custom domain agents.

---

## Build Sequence

- Phase 0: Workflow backbone — DONE (2026-04)
- Phase 1: Vector memory + ReAct traces — DONE (2026-05)
- Phase 2: ML meta-controller (LinUCB) + escalation ladder — DONE (2026-05)
- Phase 3: Self-learning loop (PromptEvolver, ToolSynth, ScaffoldEvolver) — DONE (2026-05)
- Phase 4: Product-Market Fit — CI Rescue wedge, web app, agency pilots — **ACTIVE** (pivot 2026-07-03)
- Phase 5: Compounding moat — three-layer memory, cross-repo learning, margin optimization — PLANNED
- Phase 6: Platform — multi-app kernel, API layer, marketplace — DEFERRED

**Current phase:** 4 — CI Rescue wedge + `awos.io` web app + 5 agency pilots

---

## Architecture

```
Kernel (domain-agnostic)          App #1: Coding (domain-specific)
─────────────────────────         ────────────────────────────────
orchestrator.py                   planner.py, worker.py, verifier.py
reward_store.py (objective)       git_manager.py, cartographer.py
ml_router.py (LinUCB)             test_runner.py
escalation_engine.py              symbol_index.py
budget_ledger.py
prompt_evolver.py
live_tool_synth.py
scaffold_evolver.py (OFF by default)
vector_memory.py, skill_library.py
agent_state_manager.py
dag_executor.py
gui/ (server + web UI)
```

**Runtime state:** `.awos/` — the OS filesystem (error patterns, skills, evolved prompts, tools, rewards, goals, memory).

---

## Key Modules

| Module | Path | Purpose |
|---|---|---|
| CLI entry | `awos.py` | User-facing commands (`fix-ci`, `serve`, `stats`) |
| Kernel loop | `scaffold/agent/orchestrator.py` | Plan → execute → verify → learn |
| Objective function | `scaffold/agent/reward_store.py` | `compute_reward()` — quality × speed ÷ cost |
| Bandit router | `scaffold/agent/ml_router.py` | LinUCB model selection |
| Model scheduler | `scaffold/agent/escalation_engine.py` | 5-tier ladder, start cheap escalate on evidence |
| Prompt compiler | `scaffold/agent/prompt_evolver.py` | Experience → evolved prompts |
| Tool compiler | `scaffold/agent/live_tool_synth.py` | Failures → synthesized helpers |
| Self-patcher | `scaffold/agent/scaffold_evolver.py` | Scaffold mutations (OFF by default — research feature) |
| Coding execute | `scaffold/agent/worker.py` | LLM edit application |
| Coding verify | `scaffold/agent/verifier.py` | Syntax + apply gate |
| CI discovery | `scaffold/agent/ci_discovery.py` | Test failure discovery + parsing |
| CI planner | `scaffold/agent/ci_planner.py` | CI goal + plan generation |
| CI display | `scaffold/agent/ci_display.py` | Terminal output formatting |
| Web UI | `gui/server.py` | Web server + JSON API + SSE |
| Web dashboard | `gui/static/dashboard.html` | CI Rescue dashboard view |
| Agent Console | `gui/static/agent.html` | Apple-inspired frameless UI: reasoning sheet, event cards, frosted sidebar, live stream |
| Electron app | `awos-app/main.js` | Native desktop: frameless window, dock icon, native menus, Python server spawn |
| Electron preload | `awos-app/preload.js` | Secure bridge: dockBounce, setProgress, platform info |
| In-app agent run | `gui/server.py` POST `/api/agent/run` | Spawns `awos run` as subprocess, streams events via SSE, no terminal needed |
| Electron config | `awos-app/package.json` | npm project with electron + electron-builder for macOS/Linux/Windows |
| Identity doc | `VISION.md` | Canonical product vision |

---

## Execution Flow

```
Goal
  ↓
Orchestrator (kernel)
  ├── EscalationEngine / LinUCB → pick model (shown transparently to user)
  ├── Planner → task DAG
  ├── Worker → execute (Coding App)
  ├── Verifier → quality gate (Coding App)
  ├── compute_reward() → score outcome
  └── Learn → .awos/ (error patterns, skills, prompts, tools, weights)
```

**User sees:** model choice + reasoning + past success rate + override option. Not a black box.

---

## Current Metrics

| Metric | Value | Last updated |
|---|---|---|
| Model tiers | 9 (8 working, 1 down) via OpenCode Go | 2026-07-21 |
| Working models | 8/15 validated | 2026-07-21 |
| Chat latency | 5-8s via DeepSeek V4 Flash (primary) | 2026-07-21 |
| Provider | OpenCode Go ($10/mo) only working provider | 2026-07-21 |
| Budget spent | $2.09 of $20/mo | 2026-07-21 |
| Total requests | 2,153 | 2026-07-21 |
| Avg cost/req | $0.00097 | 2026-07-21 |
| App packaging | macOS .app in dist/mac/ (~608MB) | 2026-07-21 |
| Diagnostics | Timing sensors (HTTP + LLM call) | 2026-07-21 |
| Kernel LOC | ~40K across 140 modules | 2026-07-21 |
| Active task files | 1 (sprint1_working_app — DONE 2026-07-24) | 2026-07-24 |
| Current phase | 4 — Product-Market Fit (Sprint 1 complete; Sprint 2 next) | 2026-07-24 |
| Pilot customers | 0 | 2026-07-21 |
| E2E tests | 1 (test_orchestrator_e2e — added 2026-07-24) | 2026-07-24 |
| Dead models filtered | 5 (gemini-2.0-flash, gemini-2.5-flash, gpt-4o-mini, claude-haiku-4-5, claude-sonnet-4-6) | 2026-07-24 |

---

## North Star Metric

**Project Efficiency Index (PEI)** = (Quality × Speed) / Cost

- Quality: test pass rate, success rate, rework cycles
- Speed: time from goal to done
- Cost: total LLM spend per project

Tracked via: `performance.json`, `spans.jsonl`, `reward_store.jsonl`, `awos stats`.

**Customer-facing metric:** "Your AI improved X% this month" — shown on web dashboard. Backed by PEI.

---

## Active Work

| Task | Focus | Status |
|---|---|---|
| **Pivot to web app product** | CI Rescue wedge + transparent routing + agency pilot | **ACTIVE** — see [[pivot_web_app_product]] |
| **Routing learning repair** | Fix 3 bugs preventing LinUCB self-learning | **DONE** — see [[routing_learning_repair]] |
| **Wedge v1 test rescue** | Define minimum CI Rescue sprint worth ₹2k/mo | **ACTIVE** — see [[wedge_v1_test_rescue]] |
| **CI Rescue sprint corpus** | 28 reds, 5 modules, 18 tasks | **ACTIVE** — see [[ci_rescue_sprint_corpus]] |
| **OpenRouter integration** | 5th LLM provider | **NEAR DONE** — 12/13 steps |
| **Server stability** | App crashes after 2-3 queries | **DONE (2026-07-21)** — see [[checkpoint_2026-07-21_server_stability]] |
| **Speed** | Target 3-5s per query | **DONE (2026-07-21)** — 30-47% faster, see [[checkpoint_2026-07-21_chat_speed]] |
| **OpenCode Go 429 quota** | All 9 models failing with `GoUsageLimitError` | **DONE (2026-07-23)** — API key rotated, see [[checkpoint_2026-07-23_playwright_mcp]] |
| **Playwright MCP** | No live eyes on the running app | **DONE (2026-07-23)** — `@playwright/mcp` installed; agent can now `browser_snapshot()`, `browser_take_screenshot()`, etc. |
| **Model name badge bug (B1)** | Shows `ocg:deepseek-v4-flash` instead of `DeepSeek V4 Flash` | **DONE (2026-07-23)** — `gui_chat.py:1069` uses `name` from tuple |
| **Phantom model names during streaming (B2)** | `DeepSeek V4 Pro` then `MiniMax M3` shown before real model | **DONE (2026-07-23)** — `agent.html:420, 436` start with empty `''` |
| **M3 cost badge (B3)** | Couldn't reproduce — M3 not in dropdown | **DONE (2026-07-23)** — M3 added to dropdown; cost shows $0.0003 for 787+83 tokens |
| **Server stability (B4)** | Alleged crash after Playwright e2e | **DONE (2026-07-23)** — 20/20 stress test passed, server alive |
| **Error message truncation (B5)** | Long error messages cut off mid-string | **DONE (2026-07-23)** — backend yields `error` event, frontend renders styled block w/ full text + copy button |
| **T1: Orchestrator progress invisible** | Events emitted but only titlebar dot updates | **DONE (2026-07-23)** — `connectSSE` routes 8 event types to active msg bubble with color coding |
| **T2: Thinking mixed into bubble** | Non-reasoning models put preamble in content | **DONE (2026-07-23)** — `splitThinkingPreamble()` detects `<think>` blocks + cue phrases |
| **T3: Thinking panel hidden** | Collapsed by default | **DONE (2026-07-23)** — now starts expanded with `▼` arrow |
| **UI/UX: Agent Power Layer** | Glass-box UI — see [[ui_ux_priority]] for full breakdown | **PRIORITY #1** — 11 incremental steps |
| | ├─ ✅ Syntax highlighting + copy buttons | Done |
| | ├─ ✅ Model badge (model, TTFB, tokens, cost) | Done |
| | ├─ ✅ Visual verification pipeline (Playwright) | Done |
| | ├─ 1️⃣ Fix badge spacing — text runs together | 5min fix |
| | ├─ 2️⃣ Message actions — copy full response, regenerate, delete | small |
| | ├─ 3️⃣ Smooth streaming — debounced renders | medium |
| | ├─ 4️⃣ Thinking panel polish — model name in header, animation | small |
| | ├─ 5️⃣ Empty state — suggestions, quick actions | small |
| | ├─ 6️⃣ Model selector dropdown — user picks model | medium |
| | ├─ 7️⃣ Multi-chat sidebar — create/switch/delete chats | medium |
| | ├─ 8️⃣ Conversation history — persist chats across reload | medium |
| | ├─ 9️⃣ File references — show edited files in message | medium |
| | ├─ 10️⃣ Stop button polish — immediate feedback | small |
| | └─ 11️⃣ Page routing — settings, dashboard, sessions | large |
| **File editor** | Monaco Editor + file IPC + native save | **PRIORITY #2** |
| **AI + editor** | Agent reads open file, shows inline diffs | **PRIORITY #3** |
| **Workspace mgmt** | Multiple repos, per-repo .awos/ | **PRIORITY #4** |
| **Orchestrator E2E** | awos run executes coding via OpenCode Go | **PRIORITY #5** |
| **Test suite** | After features stable. Validates everything. | **PRIORITY #6** |
| **File editor** | Monaco Editor + file IPC + native save | **PRIORITY #2** |
| **AI + editor** | Agent reads open file, shows inline diffs | **PRIORITY #3** |
| **Workspace mgmt** | Multiple repos, per-repo .awos/ | **PRIORITY #4** |
| **Orchestrator E2E** | awos run executes coding via OpenCode Go | **PRIORITY #5** |
| **Test suite** | After features stable. Validates everything. | **PRIORITY #6** |
| **Workspace mgmt** | Multiple repos, per-repo .awos/ | **PRIORITY #3** |
| **Orchestrator E2E** | awos run executes coding via OpenCode Go | **PRIORITY #4** |
| **Test suite** | After features stable. Validates everything. | **PRIORITY #5** |

---

## Key Decisions

| Decision | Rationale | Date |
|---|---|---|
| AWOS = adaptive engineering runtime (customer-facing) | Customers buy outcomes, not architecture | 2026-07-03 |
| Glass-box routing: show models + reasoning + overrides | Trust through transparency. Moat is the learning loop, not secrecy. | 2026-07-03 |
| Customer pays subscription, AWOS manages all API keys | Reliability compounds via learning. Cost optimization follows quality. | 2026-07-03 |
| CI Rescue is the wedge product | Universal pain. Measurable outcome. Proven (28 → green). Unattended. | 2026-07-03 |
| Target: engineering agencies (not individual devs, not enterprise) | Fastest path to revenue. Multiple client repos = compounding visible. | 2026-07-03 |
| Web app is primary product surface | Zero-install experience. CLI is for power users. | 2026-07-03 |
| ScaffoldEvolver OFF by default | Self-modifying code scares customers. Research feature only. | 2026-07-03 |
| "90% cost reduction" claim replaced with PEI trend | Honest, defensible, provable. | 2026-07-03 |
| Greedy objective: quality × speed ÷ cost | Encoded in `compute_reward()` | Permanent |
| `.awos/` local state ownership | Compounding switching cost | Permanent |
| User is architect/director, AWOS is senior engineer | UX principle: engineer feels powerful, not passive | 2026-07-03 |

---

## Obsolete / Superseded

| Idea | Replaced by | Why |
|---|---|---|
| "Hide model selection from customers" | Transparent routing with overrides | Trust > secrecy |
| "AWOS API productization (Phase 5)" | CI Rescue wedge first | API layer needs proven demand |
| "Multi-app kernel (Phase 6)" | Deferred until coding proves value | Premature expansion |
| "Agent-making firm" as immediate goal | CI Rescue product + agency pilots | Narrow first, expand later |
| "90% cost reduction" claims | PEI trend ("Your AI improved X% this month") | Defensible metric |
| 90% savings projection ($30 at month 12) | Removed. PEI trend shown, not promised. | Honest positioning |
| Model ladder refactor (top-60 tiers) | Superseded by pivot focus | CI Rescue wedge takes priority |

---

## Cold-Start Protocol

```bash
# 1. Identity
cat VISION.md

# 2. Project facts (this file)
cat memories/repo/project_structure.md

# 3. App/UI state (what the user sees, what the code does)
cat memories/repo/ui_state.md

# 4. Last session
ls -t docs/memory/ | head -1 | xargs cat

# 5. Active tasks
cat tasks/active/*.md

# 6. Operational protocols (when implementing)
cat AWOS.md
```

**Why this works:** `ui_state.md` is a hand-curated snapshot of the running app
— layout, components, events, model tiers, bugs. Reading it is 100× faster than
parsing `agent.html` (582 lines) + `gui_chat.py` (1372 lines) every session.
Update `ui_state.md` whenever the UI or routing logic changes.

Do not re-read the entire codebase. Follow the links.

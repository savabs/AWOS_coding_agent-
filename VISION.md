# AWOS — Vision & Identity

> **Canonical owner for what AWOS is, why it exists, and what we optimize for.**
> Read this before anything else. All other docs serve this document.
> Last updated: 2026-06-08 — v1.0

---

## One sentence

**AWOS is a learnable operating system for autonomous work — a greedy AI that finishes projects well, fast, and cheap, and gets better every time.**

The coding agent is **App #1**. The kernel works for any domain.

---

## What AWOS is

AWOS is not a coding assistant. It is not a Cursor alternative. It is not an IDE plugin.

AWOS is **infrastructure for autonomous agents**:

- An **operating system** that schedules work, manages resources, and enforces limits
- A **compiler** that turns raw experience into executable policy
- A **database** that tracks habits, mistakes, and patterns per deployment
- A **meta-AI** whose LLMs are compute resources — like CPU cycles in Linux

The LLM is not the product. The greedy optimizer that decides *when*, *how*, and *which* LLM to use — and learns from every outcome — is the product.

---

## The objective function

Everything in AWOS serves one greedy objective:

```
Maximize:  Quality × Speed ÷ Cost

Quality  →  did the work succeed? (tests pass, requirements met, no rework)
Speed    →  how fast from goal to done?
Cost     →  total spend (LLM calls + retries + wasted tiers)
```

This is encoded in `compute_reward()` in `scaffold/agent/reward_store.py`:

- **Success on cheap tier** → high reward
- **Success on expensive tier** → lower reward (opportunity cost)
- **Failure on cheap tier** → small penalty (worth trying)
- **Failure on expensive tier** → large penalty (wasted money)

LinUCB, EscalationEngine, BudgetLedger, PromptEvolver, LiveToolSynthesizer, ScaffoldEvolver — all exist to maximize this function.

**Our metric is project delivery efficiency.** Not tokens. Not model names. Not "suggestions accepted."

---

## Three metaphors

### OS — schedules resources, manages state

| OS concept | AWOS equivalent |
|---|---|
| Kernel | `orchestrator.py` — main execution loop |
| Scheduler | EscalationEngine + LinUCB bandit |
| Memory manager | VectorMemory, context hydration |
| File system | `.awos/` directory tree |
| Process table | `goals/` + `state/` |
| Syscalls | Worker tools + synthesized helpers |
| Resource limits | BudgetLedger hard caps |
| Crash recovery | Checkpoints + git rollback |
| Self-update | ScaffoldEvolver (test-gated) |
| Observability | `awos stats`, spans.jsonl |

Linux runs programs. **AWOS runs agents** — and cares whether they succeed.

### Compiler — experience → policy

| Input (raw experience) | Output (compiled policy) |
|---|---|
| `error_patterns.jsonl` | `evolved_prompt.json` (PromptEvolver) |
| Repeated failure patterns | `.awos/tools/custom_*.py` (LiveToolSynthesizer) |
| Task outcomes × model × cost | `strategy_weights.json` (LinUCB) |
| High failure rate sessions | `scaffold_mutations.jsonl` (ScaffoldEvolver) |

Configured rules (Cursor Team Rules) tell the AI who you are.
**AWOS compiles who you are** from what actually happened.

### Database — persistent knowledge per deployment

| File | What it stores |
|---|---|
| `error_patterns.jsonl` | Every mistake, categorized |
| `skills/index.json` | What worked, win rates |
| `reward_store.jsonl` | Outcomes × model × cost |
| `memory/` | Semantic history (ChromaDB) |
| `spans.jsonl` | Execution traces |
| `performance.json` | Model × task type matrix |
| `budget.json` | Spend ledger |

`.awos/` is the customer's institutional memory. It compounds. Switching cost grows with every session.

---

## Kernel vs App

AWOS splits into two layers:

```
┌─────────────────────────────────────────────────────────┐
│  KERNEL (domain-agnostic)                               │
│  Greedy for: quality × speed ÷ cost                     │
│                                                         │
│  RewardStore + compute_reward()                         │
│  LinUCB bandit (ml_router.py)                           │
│  EscalationEngine (model ladder)                        │
│  BudgetLedger                                           │
│  ErrorPatternStore                                      │
│  PromptEvolver                                          │
│  LiveToolSynthesizer                                    │
│  ScaffoldEvolver + StabilityGate                        │
│  VectorMemory + SkillLibrary                            │
│  AgentStateManager + DAGExecutor                        │
│  ObservabilityStore                                     │
└─────────────────────────────────────────────────────────┘
                          │
              plugs into any domain via
                          │
┌─────────────────────────────────────────────────────────┐
│  APP LAYER (domain-specific)                            │
│                                                         │
│  Coding App (v1):  Planner → Worker → Verifier        │
│  Research App:     Planner → Searcher → FactChecker     │
│  Support App:      Planner → Responder → CSATCheck      │
│  Ops App:          Planner → Runbook → HealthVerify     │
│  Custom apps:      built by agent-making firm           │
└─────────────────────────────────────────────────────────┘
```

Every app follows the same loop:

```
Goal → Plan → Execute → Verify → Reward → Learn → .awos/ grows
```

Only **Execute** and **Verify** change per domain. The kernel is universal.

---

## The self-learning loop

Real learning means AWOS's own code, prompts, and tools **change from experience** — not RAG, not manual rules.

| Level | Component | What changes |
|---|---|---|
| 1A | PromptEvolver | Worker instructions from failure/success patterns |
| 1B | LiveToolSynthesizer | New Python tools synthesized on repeated failures |
| 1C | ScaffoldEvolver | Agent scaffold source code self-patches (test-gated) |
| 2 | LinUCB bandit | Model routing policy from task outcomes |
| 3 | SkillLibrary | Success patterns indexed and injected |

This is what Cursor Enterprise **cannot** productize at scale:

- Team Rules are **configured** (admin writes them)
- AWOS learning is **compiled** (system discovers and applies)

After 6 months on a repo, `.awos/` contains hundreds of error patterns, evolved prompts, custom tools, and trained routing weights. That is irreplaceable local intelligence.

---

## Business model

We are an **agent-making firm**, not a mass-market consumer product.

### What we sell

- **AWOS API** — customers send goals, we return outcomes
- Customers never pick models — we route internally
- Margin = (API price) − (actual LLM cost)
- Routing gets smarter → margin improves over time

### Who we sell to (niche, ~1,000 users)

| Segment | Why they pay |
|---|---|
| Dev agencies | Agent learns agency patterns; client 5 cheaper than client 1 |
| Platform builders | Embed learning agents via API |
| Long-running eng teams | Multi-session goals, audit trail, budget caps |
| Custom agent builds | $5k–50k project + maintenance on AWOS kernel |

We do **not** compete with Cursor for individual developers or IDE UX.

### Pricing ladder

| Tier | Price | What |
|---|---|---|
| AWOS API | $149/mo | Coding app on kernel, `.awos/` state |
| AWOS Studio | $499/mo | Multi-app, team shared state |
| Custom agent | $5k–50k + $299/mo | Domain-specific app on kernel |
| Platform license | $2k/mo | Embed kernel in their product |

### North star metric (customer-facing)

**Project Efficiency Index (PEI)** = (Quality × Speed) / Cost

Every project gets a scorecard. Week over week, PEI should improve as `.awos/` compiles experience.

---

## What we are not

| Misconception | Reality |
|---|---|
| "Better Cursor" | Different category — learnable OS vs IDE plugin |
| "AI coding assistant" | Meta-AI that uses LLMs as compute |
| "Agent framework" | Operating system with greedy objective |
| "Mass-market SaaS" | Niche agent-making firm, ~1,000 paying users |
| "Model picker product" | Models hidden; routing is our moat |
| "Workflow template" | Workflow protocols serve kernel apps; they are not the identity |

---

## Competitive positioning

| | Cursor / Copilot | AWOS |
|---|---|---|
| Personalization | Configured rules (admin writes) | Compiled learning (system discovers) |
| Memory | Retrieved (RAG, MCP plugins) | Compiled (prompts, tools, routing evolve) |
| Model selection | User or admin picks | Greedy optimizer picks internally |
| Metric | Seats × usage | Quality × speed ÷ cost per project |
| State ownership | Split (cloud + git rules) | `.awos/` local, customer owns |
| Gets cheaper over time | No | Yes — LinUCB + cache + learning |
| Domain | IDE-bound | Kernel portable to any autonomous work |

---

## Evolution roadmap

```
Phase 1 (now):     Coding App on AWOS kernel — prove PEI improves over time
Phase 2:           Agency + audit GTM — first revenue (see docs/NICHE_GTM.md)
Phase 3:           AWOS API — productize after 3 paying clients
Phase 4:           Multi-app OS — research, support, ops apps on same kernel
Phase 5:           Agent-making firm — custom domain apps for niche clients
Phase 6:           Full AI-native environment — files, git, CI, agents, memory as one learnable system
```

Coding is the proof point. **Agency deployment is the fast earnable track.** See `docs/NICHE_GTM.md`.

---

## Operational protocols (still apply)

The Research → Spec → Task workflow in `AWOS.md` is **how kernel apps execute work with discipline**. It is not the product identity — it is the operational protocol that prevents incoherent implementation inside any AWOS app.

Agents working on AWOS itself or apps built on it still follow:

1. Research before code
2. Spec with atomic steps
3. Task file as source of truth
4. Checkpoint at session end
5. Single-Owner Rule for facts

These protocols serve the greedy objective: **unplanned work wastes cost and destroys quality.**

---

## Agent cold-start (read order)

```
1. VISION.md                              ← this file (identity)
2. memories/repo/project_structure.md     ← current facts and metrics
3. docs/memory/checkpoint_*.md            ← last session state
4. tasks/active/*.md                      ← current work
5. AWOS.md                                ← operational protocols (when implementing)
```

---

## Related

- [[AWOS]] — operational protocols and workflow doctrine
- [[memories/repo/project_structure]] — canonical project facts
- [[AGENT_INDEX]] — full file map for agents
- `scaffold/agent/` — kernel and coding app implementation

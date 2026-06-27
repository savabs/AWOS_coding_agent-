# AWOS — Learnable Operating System for Autonomous Work

> **An AI that is greedy to finish your projects — well, fast, and cheap — and gets better every time.**

AWOS is not a coding assistant or a Cursor alternative. It is a **learnable operating system** for autonomous agents: a meta-AI that schedules work, compiles experience into policy, and optimizes for **quality × speed ÷ cost**.

The coding agent is **App #1**. The kernel is domain-agnostic and portable to research, support, ops, and custom agent builds.

**Read [`VISION.md`](VISION.md) first.** It is the canonical identity document for this project.

---

## Quick start

```bash
# Start work on a goal
awos worker start "fix the auth bug in login.py"

# Check status (Ctrl+C anytime to pause)
awos worker status

# Resume after pause
awos worker resume

# Review changes before merge
awos worker diff

# Cancel if needed
awos worker cancel
```

**First time?** See [Setup](#setup) below.

---

## What AWOS is

| Metaphor | Role |
|---|---|
| **OS** | Schedules agents, manages budget, enforces limits, self-updates |
| **Compiler** | Turns failures and successes into prompts, tools, routing weights |
| **Database** | `.awos/` tracks habits, mistakes, and patterns per deployment |

The LLM is compute. AWOS is the intelligence that decides how to spend it.

---

## The objective

```
Maximize:  Quality × Speed ÷ Cost
```

Every component — LinUCB routing, EscalationEngine, PromptEvolver, LiveToolSynthesizer, BudgetLedger — serves this function. Our metric is **project delivery efficiency**, not tokens or model names.

---

## Repository layout

```
AWOS_coding_agent/
├── VISION.md                  ← Identity and strategy (read first)
├── AWOS.md                    ← Operational protocols for kernel apps
├── AGENT_INDEX.md             ← Agent cold-start file map
├── awos.py                    ← CLI entry point
│
├── scaffold/agent/            ← Kernel + Coding App implementation
│   ├── orchestrator.py        ← Kernel loop
│   ├── reward_store.py        ← Objective function (compute_reward)
│   ├── ml_router.py             ← LinUCB bandit
│   ├── escalation_engine.py   ← Model ladder scheduler
│   ├── prompt_evolver.py      ← Compiles experience → prompts
│   ├── live_tool_synth.py     ← Compiles failures → tools
│   ├── scaffold_evolver.py    ← Self-modifying scaffold (test-gated)
│   ├── worker.py / verifier.py  ← Coding App execute/verify layer
│   └── core/                  ← Performance, observability, queue model
│
├── .awos/                     ← Runtime state (the OS filesystem)
│   ├── error_patterns.jsonl   ← Mistake database
│   ├── skills/                ← Learned success patterns
│   ├── evolved_prompt.json    ← Compiled worker policy
│   ├── tools/                 ← Synthesized helpers
│   ├── reward_store.jsonl     ← Outcomes for bandit learning
│   ├── goals/ + state/          ← Multi-session process table
│   └── memory/                ← Vector long-term memory
│
├── docs/                      ← Research, specs, checkpoints
├── tasks/                     ← Active implementation tracking
├── protocols/                 ← Step-by-step operating guides
└── tests/                     ← 776+ tests
```

---

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp .env.example .env   # edit with your keys

# Run the coding app
python3 awos.py run --goal "add rate limiting to the API"

# Check self-learning state
python3 awos.py stats

# Check budget
python3 awos.py budget
```

---

## CLI

| Command | Purpose |
|---|---|
| `awos run <goal>` | Execute a feature goal (Coding App) |
| `awos chat` | Interactive session |
| `awos stats` | Self-learning observability report |
| `awos budget` | Month-to-date spend |
| `awos performance` | Model × task type success matrix |
| `awos memory search <q>` | Search vector memory |
| `awos goals` | List multi-session goals |

---

## Kernel vs App

**Kernel (domain-agnostic):** RewardStore, LinUCB, EscalationEngine, BudgetLedger, PromptEvolver, LiveToolSynthesizer, ScaffoldEvolver, VectorMemory, SkillLibrary, DAGExecutor, ObservabilityStore.

**Coding App (domain-specific):** Planner, Worker, Verifier, GitManager, Cartographer, TestRunner.

Future apps (research, support, ops) swap the execute/verify layer. The kernel stays the same.

---

## Business

We are an **agent-making firm** targeting ~1,000 niche paying users — agencies, platform builders, eng teams — not a mass-market IDE product.

- Customers use our API; they never pick models
- We optimize routing internally; margin improves as LinUCB learns
- `.awos/` state compounds → switching cost grows over time

See [`VISION.md`](VISION.md) for full strategy, pricing ladder, and competitive positioning.

---

## For agents

Cold-start sequence:

1. `VISION.md`
2. `memories/repo/project_structure.md`
3. Latest `docs/memory/checkpoint_*.md`
4. `tasks/active/*.md`
5. `AWOS.md` (when implementing)

See `AGENT_INDEX.md` for the full file map.

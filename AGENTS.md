# AGENTS.md — AWOS Agent Instructions

> **Read this at the start of every agent session.**
> Cursor enforces identity via `.cursor/rules/awos-vision.mdc` (`alwaysApply: true`).

---

## 1. Identity (non-negotiable)

**`VISION.md` v2 is the sole, permanent core philosophy.** Nothing else may define what AWOS is.

| AWOS is | AWOS is not |
|---------|-------------|
| Capability amplifier + bare-metal compute (local) + API routing (cloud) | API-only or local-only product |
| Hybrid: laptop / on-prem / rented GPU / unified API key | Bypassing verify, reward, or learning loop |
| Best runtime for autonomous agents | Cursor, Copilot, or IDE assistant |
| Kernel = product; coding = App #1 | A chatbot or benchmark-chasing product |

**Fundamental principle:** Models are replaceable. Intelligence compounds.

**2-year mission:** Given any model + budget → maximum reliable work. Coding = benchmark only.

**North star:** Turn commodity intelligence into reliable autonomous work.

**Current execution:** Stage 1 — one excellent worker. See `VISION.md` stages + deferrals.

**Objective:** Useful Work Output ÷ (Dollar · Second · Watt)

---

## 2. Cold-start sequence (every session)

```
1. VISION.md v2.8                         → sole philosophy
2. docs/MASTER_PLAN.md                    → execution plan
3. memories/repo/project_structure.md     → phase, metrics
4. docs/memory/checkpoint_2026-06-16_master_plan.md → latest handoff
5. AWOS.md                                → operational protocols (when implementing)
```

Do **not** re-read the full codebase. Use `AGENT_INDEX.md` for the file map.

---

## 3. Implementation discipline

For non-trivial work, follow `AWOS.md`:

```
Research → Spec → Task → Implement (one atomic step) → Live proof → Checkpoint
```

- No implementation until research + spec + task file exist
- One atomic step at a time; if a step has "and", split it
- Facts have one owner (`memories/repo/project_structure.md`)
- **Live proof required** for orchestrator, sessions, CLI, safety features — runnable demo + observable markers, not unit tests alone (`AWOS.md` §2.6 · `protocols/LIVE_PROOF_PROTOCOL.md`)

---

## 4. Forbidden actions

- **Contradict `VISION.md`** in code comments, docs, prompts, or user-facing copy
- **Duplicate identity** elsewhere — link to `VISION.md` instead
- **Reintroduce removed positioning**: agency GTM, "learnable OS", "Cursor competitor", client-facing PEI sales framing
- Frame AWOS as a **self-improving coding harness** or claim we make models "100× smarter"
- Propose **deferred** work (RL, AGI, swarms, custom LLM, robotics, multi-agent) before Stage 1–7 foundations exist
- **Change `VISION.md`** without explicit user request (philosophy is stamped permanent)
- Skip preflight for non-trivial changes (see `AWOS.md`)
- Mark kernel/CLI/safety features done with **only** unit tests — live proof required (see `protocols/LIVE_PROOF_PROTOCOL.md`)

---

## 5. Architecture alignment

Build toward VISION layers. Current code lives in `scaffold/agent/` (transitional); target layout:

```
kernel/     scheduler, reward, memory, routing, reflection, …
cognition/  llm, rl, symbolic, hybrid
runtime/    terminal, browser, git, apis, …
apps/       coding (App #1), research, devops, …
```

When proposing new work, map it to a **VISION stage (1–7)**. Reject work that belongs in **Explicitly Deferred** until foundations are done.

---

## 8. Execution discipline

- **Vision** = decades. **Execution** = next stage only.
- Do not propose: RL training, custom LLMs, multi-agent swarms, world simulation, robotics, AGI — see `VISION.md` → Explicitly Deferred.
- Coding proves the kernel; it is not the product mission.
- Prioritize: memory, planning, execution, verification, reflection, routing, capability reuse (including capability cache).

---

## 6. Key paths

| Path | Purpose |
|------|---------|
| `VISION.md` | Sole identity |
| `AWOS.md` | How to implement |
| `AGENT_INDEX.md` | Full file map |
| `scaffold/agent/orchestrator.py` | Main execution loop |
| `.awos/` | Runtime state (memory, rewards, skills) |
| `awos.py` | CLI |

---

## 7. Quick identity check

Before writing docs or architecture proposals, ask:

1. Does this align with `VISION.md` v2?
2. Am I restating identity instead of linking to `VISION.md`?
3. Which **stage (1–7)** does this work advance? Is deferred work justified?
4. Does it improve useful work per dollar/second/watt?

If any answer is wrong, stop and re-read `VISION.md`.

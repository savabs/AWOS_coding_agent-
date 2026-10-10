# AGENTS.md — AWOS orientation

> Orientation for agent sessions. There are no project rules: no forbidden
> topics, no deferred work, no mandatory preflight or ritual. Explore,
> propose, and build whatever moves the project forward, including RL,
> training/fine-tuning, multi-agent systems, and new hardware ideas.
> (Rules removed by the owner on 2026-10-10.)

---

## What AWOS is building

The agent's own computer: a local-first runtime that turns small or local
models (plus cloud models when needed) into reliable autonomous work. The
end goal is local computer use. Coding is the first testbed. The objective
is useful work per dollar · second · watt. `VISION.md` has the longer
background; treat it as context, not constraint.

## Useful starting points

| Path | Purpose |
|------|---------|
| `docs/MASTER_PLAN.md` | Execution plan |
| `docs/research/` | Research notes (local-first architecture, trick book) |
| `docs/expedition/` | Broad exploration charts + `ATLAS.md` |
| `docs/specs/` | Specs and pre-registered experiments |
| `AGENT_INDEX.md` | File map |
| `scaffold/agent/orchestrator.py` | Main execution loop |
| `scripts/job_series.py` | Benchmark runner |
| `.awos/` | Runtime state (memory, rewards, skills) |
| `awos.py` | CLI |

## Working preference: parallel work

The owner prefers work split across parallel agents whenever the pieces
don't collide (different files, no shared mutable state such as `.awos/`
ledgers or one git index, no ordering dependency). Give each agent its own
paths, then merge and verify the results together.

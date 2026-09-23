---
title: "Product Direction: The Agent's Own Computer"
tags:
  - doc/product
  - topic/strategy
  - status/active
---

# Product Direction: The Agent's Own Computer

> Identity lives in [`VISION.md`](../../VISION.md) — "best runtime for
> autonomous agents; bare-metal compute + API routing". This doc is the
> product shape of that identity. It does not replace it.

Chosen 2026-09-23 by the project owner: **the agent's own computer**, shipped
first as **the OS for AI boxes**.

## In one paragraph

Today's agents borrow the human's computer: they fight for the mouse, stop when
the laptop lid closes, and push data and logins to the cloud. AWOS gives the
agent **its own machine** — its own workspace, shell, browser and storage,
always on, with the human handing it jobs and getting back verified results.
First as **software** for AI hardware people already own (Mac mini/Studio,
Nvidia DGX Spark, a Linux box); later as a polished box of our own.

The comma.ai lesson: be the brain for hardware that already exists, publish a
short list of supported setups, and make each one excellent.

## Why this is Stage 1, not a jump ahead

`docs/MASTER_PLAN.md` Stage 1 = *one excellent worker — persistent,
hours-long, learns*. Its exit criteria — multi-hour goals, isolated execution,
pause/resume, verify-fail → replan, reward logging — are exactly what an agent
with its own computer needs. This direction gives Stage 1 a product shape.

**One agent first.** Several agents per box is multi-agent work, deferred in
`VISION.md` until Stage 1 is done.

## First customers (beachhead)

**Solo developers and small software teams.** Coding is the only workload
proven today (12/12 benchmark cases through the orchestrator, ~$0.001 each,
verified by tests). The promise: *leave it issues at night, get tested pull
requests in the morning — on your own box, your code never leaves.*

Browser and office work come after coding is dependable.

## MVP — what "done" means

| # | Capability | Exists today? |
|---|---|---|
| 1 | **Always-on host** — `awos serve`: a job queue that survives restarts | No |
| 2 | **The agent's workspace** — per-job sandbox: repo checkout, shell, tests | Partly (git worktree) |
| 3 | **Hours-long jobs** — checkpoint, pause, resume | Code exists, never proven live |
| 4 | **Reliable goal → result** — planner → tasks → agent loop | 9/12 from a goal; 12/12 from a task |
| 5 | **Hand-off** — result as a branch/PR + short report + notification | No |
| 6 | **Hybrid brain** — local model for cheap steps, OpenRouter for hard ones | Routing exists; local path unproven |
| 7 | **Budget + audit** — spend cap and a log of every action | BudgetLedger yes; audit log partial |

## Milestones (each ends with a live proof)

- **M0 — Goal → result is dependable.** Fix the four planner-path defects
  (no-edit-needed counted as failure; failing-test-first tasks punished; the
  decomposer splitting on words; over-splitting). *Proof:* 12/12 via `--plan`.
- **M1 — Always-on host.** `awos serve` + job queue + restart safety.
  *Proof:* submit 3 jobs, kill the process mid-job, it resumes and finishes.
- **M2 — Hours-long job.** Checkpoints and resume on a real repo, in an
  isolated workspace. *Proof:* a multi-hour job survives a pause and restart.
- **M3 — Hand-off.** Results as a branch/PR with a report; phone notification.
  *Proof:* an issue filed at night becomes a tested PR by morning.
- **M4 — Runs on the box.** One supported setup end to end (e.g. Mac mini with
  a local model plus OpenRouter). *Proof:* the M3 proof on that box, with the
  cost split local vs cloud.

## Not now

- Multiple agents per box (deferred — multi-agent).
- Our own hardware (after the software is loved).
- Pixel-level desktop control (after coding and browser work are dependable).
- Consumer/home use.

## Open questions for the owner

1. Which hardware do we develop and prove on first? (Mac mini/Studio, DGX
   Spark, a Linux box.)
2. Is the beachhead — solo developers and small teams — right?
3. Name for the product, distinct from the AWOS kernel?

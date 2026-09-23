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

## General computer, coding first

It is only "the agent's own computer" if the agent can do on it what a person
does on a computer — not just write code. Coding is **App #1** (per
`VISION.md`: kernel = product, coding = App #1), chosen because it is the one
ability proven today. Nothing in the host may assume a job is coding.

Every job has the same shape:

```
job = goal + ability (app) + verifier
```

| Layer | General (kernel) | Coding (App #1) | Later apps |
|---|---|---|---|
| Workspace | a full sandboxed machine: files, shell, network, browser, accounts | a repo inside it | documents, downloads, profiles |
| Tools | shell, files, browser, HTTP/APIs, email/calendar | edit_file, run_tests, git | browser actions, forms, app UIs |
| Verifier | pluggable: "how do we know it's done?" | the project's tests | page reached, file produced, email sent, value matches |
| Hand-off | report + artifacts + notification | branch / PR | document, spreadsheet, booked slot |

Design rules, binding from M1 on:

1. **The workspace is a whole machine, not a git checkout.** Git worktrees are
   one feature inside it.
2. **Verification is an interface, not pytest.** Each app brings its own
   verifier; the host only asks "done and verified?"
3. **Jobs, reports and budgets never mention code.** Coding details live in the
   coding app.

Abilities, in order (each added only when the previous one is dependable):

1. **Coding** — proven (12/12).
2. **Browser + research** — find, read, compare, fill forms; verifier = the
   page state or the extracted facts.
3. **Files and documents** — produce and edit docs, sheets, reports.
4. **Ops on the machine itself** — install, configure, monitor, fix.
5. **Communication** — email and calendar on the user's behalf, with approval.
6. **Full computer use** — operate any desktop app through its screen.

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

They are the first *customers*, not the limit of the product: the same box
gains browser, documents and ops abilities next (see *General computer, coding
first*).

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
- Pixel-level desktop control yet — it is ability 6, after coding, browser,
  documents, ops and communication are dependable.
- Consumer/home use.

## Open questions for the owner

1. Which hardware do we develop and prove on first? (Mac mini/Studio, DGX
   Spark, a Linux box.)
2. Is the beachhead — solo developers and small teams — right?
3. Name for the product, distinct from the AWOS kernel?

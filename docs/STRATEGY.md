# AWOS strategy (decided 2026-10-16, refined 2026-10-17)

## The space (2026-10-17)

**A private agent on your own computer that does your work unattended, checks its own results, and learns your
recurring tasks, getting better each time, with nothing leaving your machine.**

- **Self-serve.** Not a service, not an API/data product.
- **Uncontested by design.** Big players fight on cloud, interactive and rented intelligence. AWOS takes local,
  unattended, verified, and learns your tasks on-device. Cloud vendors can't easily follow, because local and free
  cuts token revenue and per-user on-device training doesn't fit a cloud API.
- **Sequence:**
  1. **Overnight coding agent:** queue issues, get verified branches by morning, offline.
  2. **Self-improvement:** learn from the user's own successful tasks.
  3. **Link to general computer use** (repetitive desktop work).

**AWOS is a local-first, private autonomous agent that runs on your own computer with a free local model.**
- **Now:** coding. It takes an issue or task and returns a verified branch, offline and at $0 per task.
- **Goal:** general computer use on your own machine (operating apps privately).

## Why this, and not a cloud coding agent

- Pre-registered benchmarks (`docs/memory/runs/`) show that on a strong cloud model the harness adds little: the
  model does the work. Competing head-on with Claude Code, Codex and Copilot on cloud models is a losing position.
- A **weak local model is where a harness can add real, measurable lift**: reply-format reliability, caching, stuck
  recovery, verification, cost routing.
- **Local, private and $0 per task** is a position cloud vendors structurally won't prioritise; they sell tokens.

## What has to be true (proof, in order)

1. **AWOS makes a local model better:** pre-registered local runs (H2 and ablations), AWOS vs the minimal loop on
   the same local model. Report solve rate, valid-reply and finish rates, and time and energy per solve.
2. **Local is good enough to be useful** on real tasks people bring.
3. **Local computer use works safely** (perception and safety items from the REA research).

## Customer: not yet known

To be found by cheap discovery alongside the proof work, not guessed. Candidate segments:
- privacy-bound dev teams (code can't leave the machine)
- cost-sensitive individual developers
- builders on open or local models

## What we deprioritise

- Optimising cloud-only paths.
- Cloud-model benchmarks beyond regression checks.
- Selling on "smarter than Claude Code" (our data doesn't support it).

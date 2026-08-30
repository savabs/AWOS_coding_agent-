# Wedge v1 — Internal Benchmark Bar

> **Path note:** this file historically framed a pricing wedge. It now defines the minimum benchmark bar that should count as meaningful coding-agent work.

---

## Benchmark reality

Tiny tasks do not tell us enough about the coding agent.

The benchmark bar should be high enough to reveal:
- planning weaknesses
- verification weaknesses
- routing mistakes
- tool gaps
- recovery failures

---

## What counts as meaningful benchmark work

| Job | Why it matters |
|-----|----------------|
| CI repair sprint (10–40 failures) | Medium-scale, verifiable, stresses recovery |
| Package hardening across several files | Multi-step, reviewable, bounded |
| Repeated bugfix batch | Reveals whether the harness helps weaker models |
| Refactor + test preservation | Stresses safe editing and verification |

---

## What does not count

- one trivial bug
- one docstring edit
- one 30-second demo
- a task that can’t produce honest verification

---

## Minimum benchmark bar

| Must prove | Threshold |
|------------|-----------|
| **Scale** | 10+ verified steps, or 10+ test failures, or 5+ files |
| **Verification** | `semantic_pass` or equivalent honest success gate |
| **Reviewability** | output diff is mergeable and understandable |
| **Failure signal** | benchmark exposes useful agent weaknesses |
| **Recovery** | agent can retry or escalate intelligently |

---

## Why this matters to the larger vision

A proper coding agent must clear real benchmark bars before self-learning claims matter.

Benchmark first.
Learning second.
OS expansion third.

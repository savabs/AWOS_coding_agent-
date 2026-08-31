---
title: "Checkpoint 2026-08-31 Bar Test and Pivot"
tags:
  - doc/checkpoint
  - phase/1
  - topic/decision
---

# Checkpoint: bar test, and the decision that followed

**Date:** 2026-08-31 · **Branch:** `chore/track-untracked-source` · nothing pushed

> Immutable record. Corrections go to the canonical owner file and the next
> checkpoint.

## Read these first if starting cold

1. `docs/venture_disqualifying_criteria.md` — the nine rules, calibrated
2. `docs/venture_scan_protocol.md` — where scan evidence comes from; **the scan is not yet run**
3. `docs/experiments/bar_test_2026-08-31.md` — why AWOS stopped being the plan

## What happened

**1. Repository was largely uncommitted.** 140 untracked `.py` files and 3,332
uncommitted insertions, including whole subsystems `project_structure.md` lists
as key modules: the CI subsystem, the evidence debugger (`core/debug_*`),
`evaluation_v1`, `execution_graph`. `_test_result_failed` did not exist at HEAD —
the entire test-outcome gate was uncommitted. A fresh clone was missing the
system the documentation described. Now committed across 20 additive commits.

**2. Four bugs of one class fixed.** AWOS invoked pytest four ways and three were
wrong. `test_runner` used a bare `pytest` (absent from PATH in a non-activated
venv) and laundered the resulting `FileNotFoundError` into `pass_rate=0.0`, so
the outcome gate rolled back **correct** edits and reported "Tests failed".
`ci_discovery` used the system `python3` and reported **every repository as
clean**. `wedge_v1_assert` likewise. Plus a `[DEBUGGER]` banner on stdout making
`awos sessions list --json` emit invalid JSON. Suite went 41 → 34 failures.

**3. The bar test ran.** Pre-registered, then executed. Result: **STOP**, two
conditions independently. Numbers in the memory entry and the experiment doc.

**4. Strategy changed.** AWOS is no longer the revenue plan. It stays a tool and
a portfolio piece. The bar-test write-up is publishable as-is.

## Owner constraints (load-bearing)

- **Will talk about problems and support. Will not sell or persuade.** The
  product does the selling; they do the problem-solving. This is not
  no-human-contact and must not be read as such.
- Four absolute no-gos, chosen knowingly and reaffirmed after an assistant
  attempt to soften them was withdrawn: ToS-violating scraping, regulated
  advice, single-platform dependency, handling personal data.
- These converge on an archetype: **sell software or data; do not run a hosted
  service that holds people's stuff.** Licence-key, self-hosted, opaque-key API,
  and data products survive. Account-based SaaS and marketplace-locked
  businesses do not.
- 8 weeks to a payable v1, with a **mandatory week-3 falsification gate**: if the
  riskiest assumption has not been tested against reality by week 3, the
  candidate dies regardless of how well the build is going.

## Verification

- `.venv/bin/python -m pytest tests/ -q --ignore=tests/test_autonomous_learning_e2e.py` — 34 failed, 1447 passed; unchanged by the provider work
- Bar test graded independently — pytest run directly, never AWOS's own report; `test/` untouched in both arms
- `AWOS_PROVIDER` env-gates the provider; default OpenCode path byte-identical

## Blocked

**The venture scan has not run.** Web-search budget exhausted (200/200) and
search is what generates candidates. Fetching still works, so verification is
available but discovery is not. Running it in that state would mean recalling
candidates from memory and confirming them afterwards — the exact ordering these
documents exist to prevent.

To resume: raise `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`, or use a fresh
session (budget is per-session), then follow `docs/venture_scan_protocol.md`.

## Also outstanding

- Provider account dead: both OpenCode Go ladder models RegionError, balance empty. Anthropic is the only live path.
- 34 test failures triaged; 18 of them are stale tests from the single-provider pivot, not defects
- `EscalationLevel` declares 7 members, `LEVEL_MAP` holds 2 — `KeyError` risk for 5
- `rich` missing from `requirements.txt`; a fresh clone cannot run `awos worker start`
- `.awos/` — 146k untracked files, 1,287 already tracked; one `.gitignore` line would stop the growth
- 36 root PNGs and 29 `awos-react.*.json` deliberately left untracked

## Related
- [[venture_disqualifying_criteria]] · [[venture_scan_protocol]] · [[bar_test_2026-08-31]]

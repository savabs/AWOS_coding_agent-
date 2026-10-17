# Long-horizon task: finance_tracker_from_scratch (scorer note)

Task dir: `tests/long_tasks/finance_tracker_from_scratch/`. The agent starts from a nearly empty
repo whose `README.md` is a full spec for `fintrack`, a stdlib-only finance CLI. It has to build about 10
modules. Budget: `max_turns` 150, `max_cost_usd` 2.0, `timeout_min` 90. We expect a strong model to
need about 15-40 turns.

Goal string (task.json):

> Build the finance tracker described in README.md from scratch in the fintrack/ package: importing both bank CSV formats, duplicate detection, rule-based categorization, SQLite storage, monthly reports, budgets, export and the python -m fintrack command line. README.md is the full spec - follow its module names, function signatures, output formats and exit codes exactly, use only the standard library, and add tests as you go.

## Features and hidden-test counts

| # | Feature | Hidden file | Functions | Cases |
|---|---------|-------------|-----------|-------|
| F1 | Import two CSV formats (signed vs Debit/Credit, ISO vs DD.MM.YYYY, `.` vs `,` decimals, BOM, malformed rows with line numbers) | `test_f1_import.py` | 7 | 7 |
| F2 | Duplicate detection across imports (occurrence-counting fingerprint) | `test_f2_duplicates.py` | 4 | 4 |
| F3 | Rule-based categorization (contains/regex, priority, file-order ties, direction, default, invalid rules) | `test_f3_rules.py` | 6 | 14 |
| F4 | SQLite persistence (reopen, exact money, filters/order, budget storage) | `test_f4_storage.py` | 4 | 4 |
| F5 | Monthly report (totals/counts, order, exact text, JSON, empty month, no `-0.00`) | `test_f5_reports.py` | 5 | 5 |
| F6 | Budgets (ok/near/over, exact-budget boundary, threshold, refunds) | `test_f6_budgets.py` | 5 | 5 |
| F7 | Export CSV/JSON with date-range and category filters | `test_f7_export.py` | 4 | 4 |
| F8 | CLI `python -m fintrack` (all subcommands, exit codes 0/1/2/3, `error:` messages, all-or-nothing import, subprocess run) | `test_f8_cli.py` | 9 | 15 |
| | **Total** | | **44** | **58** |

`scripts/long_tasks.py validate --task finance_tracker_from_scratch` checks the task. On `project/`
all 58 cases fail, each one separately: modules are imported lazily inside tests, so the failures are
per-test `ModuleNotFoundError`s and never a whole-file collection error. On `project/` plus
`reference/`, all 58 pass in about 1.3 s.

## Partial credit

This task is rarely solved outright, so binary SOLVED says little. Score it like this:

1. Copy `hidden_tests/` into the finished project and run
   `python -m pytest -q -rA -p no:cacheprovider hidden_tests` with the project root as cwd.
2. **Overall score** = passed cases / 58. Count errors as failures.
3. **Per-feature score** = passed / cases for each `test_fN_*.py`. Group the `PASSED`/`FAILED`/`ERROR`
   lines from `-rA` by file name.
4. **Macro score** = mean of the 8 per-feature scores. Use it as the headline number. F3 and F8 have
   parametrized cases, so the overall score over-weights them; the macro score weights all 8 features
   equally.
5. Also record whether the agent's own `tests/` still pass. A run that breaks them is flagged even if
   the hidden score is high.

## What to record per run

- model and runtime config (executor, sandbox, routing)
- **turns used** (out of 150), **cost USD** (out of 2.0), **wall minutes** (out of 90), and whether it timed out
- overall, macro and per-feature scores (F1..F8)
- **where it stopped**: the last feature it worked on, plus the reason. One of: claimed done,
  turn cap, cost cap, timeout, repeat-loop detector, crash/infra
- files created, and whether `python -m fintrack --help` runs at all
- for the first failing hidden test of each feature, a one-line failure cause (spec misread, wrong
  format, missing module, ...). This shows which part of the spec the agent loses track of over a
  long run.

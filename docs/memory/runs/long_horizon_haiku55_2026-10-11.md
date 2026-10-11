# Long-horizon tasks on Claude Haiku 5.5 (2026-10-11)

These are AWOS's realistic multi-step tasks (`tests/long_tasks/`, run by `scripts/long_tasks.py`). Each run hands the orchestrator only the user's goal, then judges the result with hidden tests the agent never saw. The model is direct Anthropic `claude-haiku-5-5` with prompt caching, on `claude/bench-run`.

## Results: 6 of 6 solved, $0.46 total

| Task | Kind | Hidden tests | Cost | Time |
|---|---|---|---|---|
| config_env_migration | migration (ini → env vars across the app) | 22/22 | $0.049 | 2.5 min |
| invoice_data_report | messy data → 2 reports | 9/9 | $0.036 | 1.5 min |
| monthly_summary_perf | performance (≥5× faster, identical numbers) | 20/20 | $0.166 | 24.2 min |
| orders_export_filters | feature + CLI + error handling | 14/14 | $0.019 | 1.5 min |
| timezone_report_bug | debugging (timezone/DST) | 10/10 | $0.030 | 2.2 min |
| **finance_tracker_from_scratch** | **greenfield: 8-feature app from an empty package** | **58/58** | **$0.161** | **8.8 min** |

- Results files: `.awos/long_tasks_20261011T163421.json` (the 5 existing tasks) and `.awos/long_tasks_20261011T164334.json` (finance).
- About the finance task (built 2026-10-11, commit 981ff3b):
  - **What it asks:** README spec only. Two bank CSV formats (one in European number notation), duplicate detection, a rules engine, SQLite, text and JSON reports, budgets, filtered export, and a CLI with exit codes 0–3.
  - **Hidden tests:** 58 cases across 8 feature files, with deliberate traps:
    - identical rows within one file are kept, but re-imports must not duplicate;
    - exactly-at-budget counts as "near";
    - no `-0.00`;
    - physical line numbers in errors;
    - imports are all-or-nothing.
  - **Run:** 24 turns, 24 tool calls, 0 failed. The later turns were the agent checking its own build against the spec: budget thresholds, the duplicate rule, exit codes, message wording.

## What this does and does not show

- **Shows:** AWOS on Haiku 5.5 reliably completes multi-file, multi-feature, well-specified work end to end. That includes a full app from scratch, at a fraction of a dollar, with self-checking against the spec.
- **Does not show:**
  - **Under-specified, open-ended goals.** Every task here has a precise spec or README.
  - **Multi-hour or multi-session horizons.** The longest run was 24 minutes.
  - **Work that needs judgement calls the tests cannot see**, such as design quality.
  - **Robustness across repeats.** Each task ran once.
- **Contamination risk is low** for finance_tracker, which was written today. The 5 older tasks were written in-repo months ago.
- **Ceiling problem:** like the 73-issue set, these tasks no longer discriminate on Haiku 5.5. The next long-horizon tests need to be harder:
  - a vague goal that needs clarification or assumptions;
  - a much larger app (hundreds of tests, several sessions);
  - a real existing codebase with no spec;
  - or a multi-day project driven through the always-on host, with checkpoints and resume.

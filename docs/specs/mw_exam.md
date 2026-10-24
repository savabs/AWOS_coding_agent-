# Bigger exam (factory `ood300`) + exam report

Plan item 0.1 in `docs/research/rl_cua_2026-10-24/NEXT_WEEK_PLAN.md`. The goal is that a single result is accurate to about ±5 points instead of ±9, and that every base vs trained comparison reports CIs and a paired test.

## What it does

**`ood300` task selector** (`scaffold/rl/factory/catalog.py`)
- `FactorySource().tasks("ood300")` returns 297 tasks:
  - the 99 frozen `ood` tasks, unchanged and first. Their ids and goals are pinned by sha256 in the test.
  - 198 new instances of the same 3 ood families (calendar, files_tree, wizard) and the same templates.
- The new tasks are built this way:
  - Ids are `factory/<family>/<template>/<k>` with `k >= template.count`, so each has a new seed.
  - Each template fills up to `3 x count` tasks.
  - Every candidate goes through the same oracle simulation and `verify.score` admission as the existing tasks.
  - Candidates are deduplicated on content (goal, data, params, oracle steps). This removed 0 tasks. 1 candidate was rejected because the oracle needed more than 12 steps, and the next k replaced it.
- Family code, templates, `tmpl.count`, `tasks()` and `tasks("ood")` are not changed. The extension is built lazily, on the first `tasks("ood300")` call or the first lookup of an extension id.
  - `FactorySource` lookups now go through `Catalog.entry(id)`, so harness worker processes resolve extension ids without asking for the split first.
- New tasks keep `split="ood"` and carry `meta.exam="ood300_ext"`. Every training guard that refuses eval splits therefore refuses them too (`scaffold/rl/train/dataset.check_split`, used by `pairs.py` and `verifier_data.py`; the `EVAL_SPLITS` row check in `kaggle_driver.py`). The name `ood300` is not a train split either, so the fail-closed check also catches it.
- `python -m scaffold.rl.factory list|check --split ood300` works.

**Exam report** (`scripts/rl/exam_report.py`, stdlib only, no API)
- Inputs are harness `trajectories.jsonl` files or folders for each arm. Shards merge. A filter on path or `trajectory.model` handles artifact folders that hold several models.
- Slices:
  - `all`
  - `factory_ood300`
  - `factory_ood99`: the old exam, comparable with earlier numbers
  - `factory_ood300_ext`
  - `miniwob_holdout`
  - any other `<source>_<split>`
- Each slice reports:
  - success per arm with a 95% Wilson CI over tasks
  - the paired difference with a 95% percentile bootstrap CI (10k draws, fixed seed)
  - exact two-sided McNemar on discordant tasks
  - unsafe episodes per arm and by code
- A per-family breakdown is included.
- **Gate** (plan rule 2), on `factory_ood300`. It passes only if all three hold:
  - the difference is at least 2 pts
  - the bootstrap lower bound is above 0
  - unsafe episodes do not increase
- Output is markdown on stdout, plus `<out>.md` and `<out>.json` when `--out` is given.
- How tasks are scored:
  - Only tasks present in both arms, without infra errors, are compared.
  - With K samples per task, the task score is the pass fraction.
  - For McNemar, a task counts as a pass only if its score is above 0.5.

## How to run

```
# list / replay-check the exam (pure-Python driver, ~1 s)
PYTHONPATH=. python3 -m scaffold.rl.factory check --split ood300
# Chromium admission (the real gate)
PYTHONPATH=. ~/.awos/rl-venv/bin/python -m scaffold.rl.harness admit --source factory --split ood300
# evaluate a model on it (CI: workflow_dispatch; factory only, miniwob keeps split=eval/holdout)
gh workflow run rl-eval.yml --ref <branch> -f sources=factory -f split=ood300 -f models=qwen3.5-4b
# compare
python3 scripts/rl/exam_report.py --base artifacts/base/ --trained artifacts/r3/ \
    --base-filter qwen3.5-4b --trained-filter r3 --trained-label r3 --out runs/exam_r3_vs_base
# tests
PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/rl/test_exam.py -q
```

No `ci/` env file was added. `rl-eval.yml` always reads `ci/rl_eval.env`, and `scripts/rl/ci_matrix.py` already passes `--split ood300` through unchanged, which a test checks. A miniwob source with `split=ood300` would fail, so dispatch factory and miniwob separately.

## Measured (2026-10-24, laptop, no model calls)

- Catalog: `tasks("ood300")` returns 297 tasks:

  | Family | Tasks |
  |---|---|
  | files_tree | 105 |
  | wizard | 99 |
  | calendar | 93 |

  - The extension needed 199 candidates, with 1 rejected and 0 duplicates.
  - Building it adds about 0.7 s to the catalog's 1.9 s.
- Admission:
  - `factory check --split ood300`: 297/297.
  - Chromium `harness admit --split ood300`: 297/297.
  - Oracle harness run (`run --model oracle --split ood300 --workers 4`): pass@1 1.0 on 297 tasks with 0 unsafe, in about 80 s.
- Report on that oracle run against itself: 1.5 s. The Wilson CI at 100% is [98.7, 100] for n=297 and [96.3, 100] for n=99.
- Precision: at p ≈ 0.61 (Qwen 4B base), the Wilson half-width is about ±5.5 pts at n=297, versus about ±9.5 at n=99.
- Tests: `tests/rl/test_exam.py` 11 passed (16 s at load average ~20). `tests/rl`: 190 passed, 7 skipped, 9 deselected.
  - `test_exam.py` sets its own `pytest.mark.timeout(120)`: building ood300 takes several seconds and the 10 s default in `pytest.ini` timed out under load.
  - The HTTP replay test replays a sample of 40 of the 198 new tasks: the first and last of every (family, template), so every template and the highest seeds are covered. This takes about 4 s instead of 9-24 s. The full 297-task replay is `factory check --split ood300` (above), and `score()` admits every task when the catalog is built.

## Limits (honest)

- **Only 3 unseen families.** The 198 new tasks are new instances of the same templates: new data, themes, layouts, ARIA levels and popups, but no new app structure.
  - The CI therefore measures "this model on these 3 families" more precisely. It does not measure "unseen apps in general".
  - Tasks of one template are correlated, so the task-level CIs are somewhat optimistic for claims about new families.
  - The per-family table shows when one family drives a gain.
- Template mix: the extension keeps each template's share (3 x count). Calendar `create_event` is 48 of 297 tasks.
- The report has not been run on real model trajectories, because there are no local rl-eval artifacts on this machine. It was run on the oracle and on synthetic records in tests.
- Multi-sample McNemar uses score > 0.5 as the pass threshold. `scripts/eval_report.py` instead uses the exact sign test on per-task deltas; for 1 sample per task the two are the same.
- CI cost: factory runs are about 3x longer on `ood300`. The shard counts in `ci/rl_eval.env` were not changed (the workflow file and env are not in this item's scope).

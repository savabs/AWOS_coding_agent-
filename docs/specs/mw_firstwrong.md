# First-wrong-step finder + correction data (plan items 0.2 + 0.3)

Module: `scaffold/rl/train/first_wrong.py`. Tests: `tests/rl/test_first_wrong.py`.

## What it does

For each FAILED policy episode (a runner.py `trajectories.jsonl` row), it replays the agent's own
replies in the same seeded task through the real harness: same TaskSource, MicroAgent and headless
Chromium, and the same guards. At each state s_k it asks whether the scripted oracle can still finish
the task. A state is recoverable if some oracle continuation played from s_k passes the verifier.
Continuations are every suffix of the oracle, plus "close the popup, then a later suffix" on popup
tasks. Passing uses the same rules as `dataset.episode_drop_reason`: success, no unsafe code, and
pause tasks must end in needs_user. The first wrong step is the first k where s_{k-1} is recoverable
and s_k is not. The correction is the oracle's reply at step k from s_{k-1}. The replay checks that
the prompt there equals the recorded one, so the reply's control numbers refer to the observation the
agent actually saw. No model, no judge, no paid API.

Speed-up: if the agent's action at step k equals the oracle's action in a successful check, and the
next prompts match, that check already proves s_k recoverable, so no new episode is run.

Categories, first rule that applies:

| category | rule |
|---|---|
| unsafe | a check from s_k carries a new unsafe code |
| invalid_action | the reply did not parse or MicroAgent rejected it |
| gave_up_early | `needs_user` on an act/answer task, or `done` while the oracle still had work |
| misread_goal | `done` where the oracle also finishes now (wrong answer), or the oracle's control with a different value |
| loop | same action on the same observation earlier, or s_k is recoverable once the step cap is lifted |
| wrong_control | anything else |

Each trajectory gets one verdict: `labelled`, `diverged` (the replay prompt differs from the
recording, so no label), `no_oracle`, `oracle_fails_at_start`, `no_wrong_step`,
`no_valid_correction` or `check_budget`.

Outputs in `--out`:

- `train.jsonl` / `valid.jsonl`: rows with exactly dataset.py's schema (`messages`, `prompt`,
  `completion`, `meta`). `meta` holds dataset.py's keys plus `correction`, `category`, `rule`,
  `wrong_action`, `wrong_valid` and `oracle_j`. The split is by task id with
  `dataset.task_is_val`, using the same `--val-frac` / `--seed` as the SFT build.
- `pairs.jsonl`: the same steps in pairs.py's DPO row format (`rule="first_wrong"`).
- `verdicts.jsonl`: one line per failed episode.
- `summary.json` / `summary.md`: counts by verdict, by category and by family × category.

## Exam integrity

Every record goes through `dataset.check_split`, then through a live lookup:

- **Factory:** the task id must be `train` in a freshly built `Catalog()`.
- **MiniWoB:** the task type must not be in `catalog.frozen_holdout()`, which includes `click-tab-2`.
- **Anything else:** an unknown source or unknown id is refused.

Before anything is written, `assert_train_only` checks every output row again. Any eval task is a
hard error (CLI exit 2) and nothing is written. `test_real_exam_never_admitted_by_default_lookup`
asserts that all factory ood tasks (99 or more) and all 15 MiniWoB holdout types are refused.

## How to run

```
~/.awos/rl-venv/bin/python -m scaffold.rl.train.first_wrong --policy q4b=<unpacked artifacts dir> \
    --out runs/rl/first_wrong/ [--source factory|miniwob] [--prompt-check strict|observation] \
    [--max-checks 60] [--max-episodes 0] [--val-frac 0.1] [--seed 0] [--dry-run]
```

`--dry-run` only counts the work and starts no browser. To use the rows in SFT, point the dataset
`--extra-rows` flag (another agent's item) at `train.jsonl`. Tests:

```
PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/rl/test_first_wrong.py -q
```

The last test needs Playwright and Chromium and is skipped without them.

## Measured (2026-10-24, laptop, mac headless Chromium)

Input: 3 real Qwen3.5-4B train-split artifacts downloaded from GitHub (no CI launched):
`rl-qwen3.5-4b-factory-train-s14of20`, `...-s18of20` and `rl-qwen3.5-4b-miniwob-train-s19of20`.
That is 152 rows; 101 are failed episodes with steps (63 factory, 38 MiniWoB).

- **Factory:** all 63 failed episodes labelled, with 0 diverged. This holds even though they were
  recorded on Linux runners. 556 replay checks in total (5.5 per episode, about 0.5 s each), 287 s for
  the whole run. Output: 52 train rows, 8 valid rows and 60 pairs (3 rows dropped as duplicates).
- **MiniWoB:** 36 of 38 have no oracle, because only 4 of the types have scripted oracles. The other 2
  (click-checkboxes) first came out `diverged`, because the start URL carries a random `awos_ep` id.
  After normalising that id, both are labelled `loop`: the agent toggled the same checkbox until the
  step cap.

First-wrong-step categories on the 63 labelled factory episodes:

| category | count | share |
|---|---|---|
| gave_up_early | 33 | 52% |
| wrong_control | 21 | 33% |
| loop | 4 | 6% |
| invalid_action | 3 | 5% |
| unsafe | 2 | 3% |
| misread_goal | 0 | 0% |

The corrections are 51 `click`, 5 `fill`, 5 `select` and 2 `done`. Shard s14 is mostly a degenerate
model (needs_user("") at step 1, garbage fills), which inflates `gave_up_early`.

## Limits / not done

- **"Recoverable" means recoverable by the scripted oracle.** A detour that Back would undo (wrong
  page, a dialog opened before the popup was closed) counts as the wrong step. The correction is
  still a correct move from the state before the detour.
- **Loops that keep the state recoverable are labelled late.** For example, toggling a checkbox back
  and forth is only flagged at the step where the cap makes the goal unreachable, not at the first
  repeat.
- **MiniWoB coverage is limited by its oracles.** Only 4 types have one (`miniwob/oracles.py`).
  Writing more oracles is the lever here.
- **Corrections carry no `# reason` line.** That is plan item 0.5.
- **Runs in one process only.** There are no workers. About 5 minutes per 100 failed episodes on a
  laptop.
- **The `misread_goal` rules never fired on this data.** They are covered only by the fake-env tests.
- **Determinism is assumed and checked, not guaranteed.** A diverging replay is reported, never
  labelled. `--prompt-check observation` tolerates history-text drift.

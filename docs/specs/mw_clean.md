# mw_clean: clean training data + reason lines (plan items 0.4 and 0.5)

Branch `claude/mw-clean`. Code: `scaffold/rl/train/clean.py`, `scaffold/rl/train/reasons.py`,
`scaffold/rl/train/dataset.py` (new flags), `scaffold/rl/harness/models.py` (reply normalisation).

## What it does

**Cleaner (`dataset --clean`).** Filters run in this order. `stats.json["clean"]` records the
before and after count for each one, and the CLI prints the same table.

| Filter | Unit | Rule |
|---|---|---|
| verified_success | episodes | Keeps an episode only if the verifier scored it a success, with no unsafe code and no infra error. This filter was already in the builder; the table now reports its counts. |
| no_loops | episodes | Drops the whole episode when one (observation, action) pair occurs more than `--max-repeats` times (default 1). The `/t/iN/` instance id is normalised first. This catches a tab loop such as A, B, A, B. |
| near_dedupe | rows | Keeps one row per (system, user text, action line). The user text is normalised by removing "Step N of M." and the instance id and collapsing whitespace. The reason line is ignored, so the same move with different wording counts as a duplicate. |
| scripted_cap | train rows | Limits oracle/scripted rows to `--scripted-frac` of train (default 0.30). Kept oracle rows come first from the families with the fewest model rows. If the set has no model rows at all, the cap is skipped and the skip is reported (applying it would delete everything). |

**Exam guard (always on, not only with `--clean`).** `check_split` already refused any split
other than `train`. It now also refuses tasks that are labelled `train` but belong to the exam:
- a factory task whose family is an `ood=True` family (calendar, files_tree, wizard; read from
  the registry);
- a MiniWoB task whose type or family is in the frozen holdout (`data/holdout.json`, 15 types,
  including click-tab-2).

The task id alone is enough to trigger the refusal. Tests check all 99 factory ood tasks and all
15 holdout types, with the original label and relabelled as train.

**`--extra-rows [LABEL=]PATH[:WEIGHT]`** (repeatable) adds JSONL rows in the builder's output
schema. Every row is validated:
- roles are system, user, assistant;
- `meta.task_id` is present;
- there is exactly one parseable action line;
- there is at most one leading `# ` reason line of 160 chars or fewer;
- `prompt` and `completion`, if present, equal the chat.py rendering.

A bad row or an eval task makes the build exit 2. Extra rows share the exact and near dedupe, the
task-id validation split, `--clean` and `--reasons` with the trajectory rows.

**Reason lines (`reasons.py`).** Claude Haiku (`claude-haiku-5-5`, thinking disabled, effort low,
no temperature, max_tokens 60) receives:
- the agent's exact user prompt;
- the known right action;
- the control line that action targets.

It writes only why the action is right. The style rules and examples are in the reason-writer
system prompt.

Each reply is sanitised to one line of 120 chars or fewer. A reply is rejected if it contains:
- action syntax;
- a control number;
- a `{{secret:` handle.

Rejected replies are still cached (reason `""`), so they are never paid for twice.

Results are cached in a JSONL file keyed by sha256(user prompt + action line). Only keys that are
not in the cache are sent.

Spend controls:
- Batch API by default (50% price).
- The batch id is saved next to the cache before polling, so an interrupted run collects the same
  batch and does not submit a second one.
- Before any call, the worst case must fit under `--max-usd` (default 1.50), or the run exits 3.
  The worst case assumes input at chars/2.5 tokens and every reply at max_tokens.
- Sync mode also stops when actual spend reaches the cap.

`dataset --reasons CACHE` sets the reply to `# <reason>\n<action>` and replaces any earlier
reason line.

**Grammar and parser check (item c).** The grammar already accepted an optional reason line:
`root ::= reason? action`, `reason ::= "# " [^\n]{1,160} "\n"`. The parser already ignores `#`
lines, and the system prompt invites "one short '# reason' line". So the format stays `# ...`
and `micro_agent.py` is unchanged; adding a second `reason:` form would make the targets
inconsistent. `normalize_text_reply`, used for cloud replies, now:
- maps a `reason: ...` line onto `# ...`;
- drops an empty `#` line, which the grammar rejects (it needs 1 to 160 chars).

`tests/test_micro_reason.py` checks with the GBNF recogniser that old replies (no reason) and new
ones both match and parse to the same action. It also checks that the reason length is capped
and that two reasons, or a reason after the action, are rejected.

## How to run

```bash
# 1. build clean data
PYTHONPATH=. python3 -m scaffold.rl.train.dataset --input oracle=runs/rl/demos_factory_train \
    --input q4b=artifacts/collect/:1 --extra-rows corrections=runs/rl/corrections.jsonl \
    --out ~/.cache/awos-models/rl/data/v3 --clean
# 2. reason lines: dry run first (no call), then the owner runs it without --dry-run
PYTHONPATH=. python3 -m scaffold.rl.train.reasons --rows ~/.cache/awos-models/rl/data/v3/train.jsonl \
    --rows ~/.cache/awos-models/rl/data/v3/valid.jsonl --cache ~/.cache/awos-models/rl/reasons.jsonl --dry-run
# 3. rebuild with the reasons merged (same flags + --reasons)
PYTHONPATH=. python3 -m scaffold.rl.train.dataset ... --clean --reasons ~/.cache/awos-models/rl/reasons.jsonl
# tests
PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/rl tests/test_micro_reason.py -q
```

## Measured

- The only local trajectories are the oracle demos in `runs/rl/demos_factory_train`
  (460 episodes):

  | Filter | Before | After |
  |---|---|---|
  | verified_success (episodes) | 460 | 429 (31 have no steps) |
  | no_loops (episodes) | 429 | 429 |
  | near_dedupe (rows) | 1525 | 1525 |
  | scripted_cap | — | skipped: there are no model rows in this set |

  Model rollouts, which are where loops and duplicates happen, are on GitHub artifacts and were
  not run here.
- Reasons dry run on those 1525 unique rows: about 2.4M input tokens (estimated, worst case) and
  at most 91.5k output tokens. Worst case cost is **$0.143 batch** or $0.285 sync. At the planned
  ~7.5k rows the estimate is about $0.7 batch, under the $1.50 cap.
- Tests: `tests/rl` + `tests/test_micro_reason.py` give 237 passed, 7 skipped (the 9 deselected
  are the suite's own markers). The new test files have 24 tests (clean), 20 (reasons) and
  14 (micro reason). Once, under machine load, `test_factory.py::test_export_cli` hit its 10 s
  timeout; it passes alone and in the rerun.

## Limits

- **No real Haiku call was made.** The batch and sync paths are tested only against fake clients
  that mirror the SDK shapes in the claude-api skill. The first real run should use `--limit 20`
  and the output should be read before the full run.
- The token estimate is chars/2.5, which is deliberately high. No `count_tokens` call was made.
- Loop detection uses exact observation text. A loop whose page changes every visit (a counter,
  a timestamp) is not caught.
- Near-dedupe is exact after normalisation, not fuzzy. The oracle set has 0 near-duplicates, so
  the effect on model data is unmeasured.
- Rows without a cached reason keep their reply unchanged. `stats.json["reasons"]` reports how
  many are missing; aim for 0 before training.

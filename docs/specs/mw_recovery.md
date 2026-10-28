# Recovery / anti-loop training rows (`scaffold/rl/train/recovery.py`)

Plan change 4 (NEXT_WEEK_PLAN.md). The checkpoint-150 exam lost 12 of 14 MiniWoB tasks to tab-switching
loops. The model had never seen its own history show a mistake or a loop, and had never seen the move
that gets out of one. This generator builds such rows on TRAIN tasks only.

## What it does

For each train task:

1. It runs the oracle cleanly through the real harness (MicroAgent + headless Chromium, product guards).
   A task is used only if that clean run passes the verifier.
2. For each perturbation type, it picks an injection point k (oracle actions already taken), injects
   the mistake, and then tries recovery candidates shortest first. Each candidate is a full episode.
3. A candidate is kept only if the whole episode passes the verifier with no unsafe codes, every
   injected action was emitted, and every row step was accepted by MicroAgent. So each recorded
   "correct move" provably reaches success from the real, perturbed state.

| Perturbation | Injected mistake |
|---|---|
| `wrong_click` | click an unrelated non-destructive link/tab (or checkbox) |
| `wrong_tab` | click a sibling tab/link in the same region as the oracle's tab/link |
| `repeat` | repeat the previous oracle action 2-3x (fill/select 1-2x: MicroAgent shows SKIPPED) |
| `pingpong` | after the oracle's tab X: Y, X (or Y, X, Y, X). The loop ends on the right tab, so the row is the move that breaks it |
| `wrong_field` | fill the oracle's value into another textbox (never a secret) |
| `wrong_option` | select another option in the oracle's combobox/radiogroup |
| `scroll_away` | `scroll(down)`; MicroAgent rejects it when every control is listed |

Recovery candidates:
- `resume`: the next oracle action. The resolver scrolls up or down if needed.
- `undo`: restore the wrong field's old value, or re-click the wrong checkbox.
- `nav_back`: the link named after the page we were on.
- `replay1..3`: replay the last 1-3 oracle actions, optionally after `nav_back`.

Rows:
- Rows are the recovery steps plus the first resumed oracle step. The clean prefix and the injected
  mistake are never rows, so the wrong action gets zero loss.
- Loop rule: a row's first action may not equal the last injected action, except for an explicit
  `undo`.
- Answer tasks: a `done(answer)` row is kept only if the answer is visible in that row's own
  observation. Without this, rows would teach answering from memory.
- Prompts are MicroAgent's own prompts. The "Your previous actions and results" block (last 6
  actions with ok / SKIPPED / REJECTED) shows the mistake exactly as the model sees its own at run
  time.

**Output format.** JSONL in `dataset.Example` schema (`messages`, `prompt`, `completion`, `meta`), for
dataset.py's `--extra-rows`. The meta fields are:
- `kind="recovery"`
- `task_kind`
- `perturbation`
- `recovery`
- `stop_loop` (true for repeat and pingpong)
- `perturb_actions`
- `row_role` (`recover` or `resume`)
- `reason_line`
- the usual `task_id`, `family`, `split` and `step`

Stats are written to `<out>.stats.json`.

**MiniWoB oracles.** `miniwob/oracles.py` has oracles for 4 types. recovery.py adds local oracles for
these train types:
- `click-collapsible-2` and `-nodelay`: a "seek" oracle that opens the first untried section until the
  link shows. Accordion sections appear as `role=tab`; this is the closest TRAIN analog of the holdout
  click-tab family.
- `click-dialog-2`
- `click-test-2`
- `enter-text-2`

`click-collapsible` is not covered: its Submit control is a `tab` that does not register a click
through the product's action path.

**Exam integrity.**
- `check_train_task` refuses:
  - the factory `ood` split and every ood family;
  - every MiniWoB holdout type and family (all of `click-tab*` included);
  - MiniWoB seeds below 1000 (the eval seeds);
  - unknown sources.
- `assert_train_rows` re-checks every written row.
- Tests cover all of these.

## How to run

```
# needs Playwright + Chromium: the 3.12 venv
PYTHONPATH=. ~/.awos/rl-venv/bin/python -m scaffold.rl.train.recovery \
    --out runs/rl/recovery/recovery_rows.jsonl --workers 4      # factory + miniwob, all train tasks
#   --sources factory --families docs_hub --limit 20            # smoke run (round-robin over templates)
#   --reason template                                           # add a fixed '# reason' line to the first recovery row
#   --dry-run                                                   # list tasks, no browser
PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/rl/test_train_recovery.py -q
```

No paid API is called (the oracle only). It costs nothing to run: local Chromium, or a free GitHub runner.

## Measured (2026-10-24, laptop, 4 workers, seed 0, `--per-type 1`)

The full run took 1025 s: 550 train tasks (460 factory + 90 MiniWoB = 9 types x seeds 1000-1009),
4807 episodes. 545 tasks were used (5 clean-oracle failures). Output: **2121 rows**, 409 of them
stop-loop rows, 0 duplicates.

| Perturbation | Plans | Kept | Rows | Main drops |
|---|---|---|---|---|
| wrong_click | 631 | 446 | 655 | unresolved 148, not_success 32 |
| wrong_tab | 253 | 163 | 263 | unresolved 87 |
| repeat | 394 | 335 | 335 | not_success 51 |
| pingpong | 74 | 74 | 74 | — |
| wrong_field | 118 | 116 | 232 | — |
| wrong_option | 51 | 48 | 48 | infra 3 |
| scroll_away | 514 | 514 | 514 | — |

| Family | wrong_click | wrong_tab | repeat | pingpong | wrong_field | wrong_option | scroll_away | Total |
|---|---|---|---|---|---|---|---|---|
| factory/docs_hub | 124 | 37 | 55 | 37 | 16 | 0 | 82 | 351 |
| factory/mail | 91 | 106 | 27 | 0 | 36 | 5 | 56 | 321 |
| factory/assign | 71 | 78 | 39 | 0 | 30 | 12 | 47 | 277 |
| factory/contacts | 69 | 0 | 43 | 3 | 86 | 0 | 51 | 252 |
| factory/records | 103 | 16 | 18 | 6 | 0 | 12 | 67 | 222 |
| factory/prefs | 67 | 0 | 43 | 0 | 14 | 19 | 61 | 204 |
| factory/checkpoints | 34 | 0 | 22 | 0 | 44 | 0 | 27 | 127 |
| factory/shop | 58 | 0 | 3 | 0 | 6 | 0 | 38 | 105 |
| miniwob/click-collapsible | 18 | 26 | 18 | 18 | 0 | 0 | 18 | 98 |
| miniwob/click-link | 10 | 0 | 10 | 10 | 0 | 0 | 10 | 40 |
| miniwob/enter-text | 0 | 0 | 20 | 0 | 0 | 0 | 20 | 40 |
| miniwob/click-checkboxes | 10 | 0 | 10 | 0 | 0 | 0 | 10 | 30 |
| miniwob/click-test | 0 | 0 | 10 | 0 | 0 | 0 | 10 | 20 |
| miniwob/click-dialog | 0 | 0 | 9 | 0 | 0 | 0 | 9 | 18 |
| miniwob/click-button | 0 | 0 | 8 | 0 | 0 | 0 | 8 | 16 |

**Breakdowns:**
- Recovery used: resume 1309, undo 244, nav_back 392, nav_back+replay1 69, replay1-2 107.
- Row roles: resume 1696, recover 425.
- Task kinds: act 1647, answer 421, pause 53.
- Rows that are `done(...)`: 566.
- Prompt length: p50 3.7k characters, max 6.2k.

## Limits / not done

- **Not trained or evaluated.** There is no evidence yet that these rows reduce loops. The gain must be
  measured on the exam, as the plan's rules require.
- **Mixing:**
  - scroll_away is 24% of the rows and mostly teaches "a rejected scroll, then the planned action".
    Downweight it with `meta.perturbation`.
  - 27% of the rows are `done(...)`. Watch for early stopping (EvoCUA) when mixing. Research suggests
    about 25% recovery rows in the SFT mix.
- **MiniWoB episodes end on the first scoring click.** Some MiniWoB rows (repeat / pingpong on
  click-link, click-button, click-test-2) show clicks made after the episode had already ended,
  followed by `done`. Filter by `task_source` if they look unhelpful.
- **Unlabeled MiniWoB fields.** `enter-text` oracles name the field `""`, but the current page reader
  labels it `... (unlabeled)`. recovery.py falls back to the page's only textbox. The upstream
  `OracleModel` / `oracles.py` were not changed (not my paths) and still miss it.
- **Factory pause tasks** whose wall is caught by a product guard (e.g. `checkpoints/login`) never ask
  the model, so they yield no rows.
- **Reason lines.** Rows carry no reason line by default. `--reason template` adds one fixed line per
  type. Haiku-written reason lines (plan item 0.5) can use `meta.perturbation`.
- **Scope:**
  - One injection point per (task, perturbation) at seed 0. Raise `--per-type` or vary `--seed` for
    more rows.
  - Only 9 MiniWoB train types have oracles.

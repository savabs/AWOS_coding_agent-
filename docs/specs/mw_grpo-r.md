# mw_grpo-r: review fix for the GRPO Kaggle resume

Branch `claude/mw-grpo-r`, based on `claude/mw-grpo`. Main spec: `docs/specs/mw_grpo.md`.

## What was wrong

`kaggle/rl-grpo/run.py` `copy_resume` looked for checkpoints with
`Path('/kaggle/input').glob(f'{slug}*/**/ck/iter_*')`. That only matches a mount at
`/kaggle/input/<slug>*`. Kaggle mounts kernel sources at `/kaggle/input/notebooks/<owner>/<slug>/`,
and a `resume_from` of the form `owner/slug` (which `stage.py` accepts) never matched at all. The
lookup also ran after pip install, the Chromium install and the speed probe, so a failed resume
wasted that GPU time.

## Fix

- `find_resume(spec, inputs)` takes an absolute path or a slug and strips any `owner/` prefix. It
  searches for `<slug>*` directories at depths 1-4 under `/kaggle/input`, the same approach as the
  SFT kernel's `find_resume_checkpoint`. It keeps only `ck/iter_N/` directories that contain
  `state.json`, picks the one with the highest number, and raises SystemExit if there are none.
- `main()` runs this lookup before it unpacks the repo, installs packages or runs the probe. It skips
  the lookup when `speed_only` is set. `copy_resume(src, out)` then copies the checkpoint into
  `out/ck`, and `grpo.train` resumes from it as before.

## Tests (2026-10-24, this Mac)

- New tests in `tests/rl/test_grpo.py`:
  - `test_find_resume_kaggle_mount_layouts` checks the deep `notebooks/<owner>/<slug>` mount, the
    `owner/` prefix, a shallow mount, an absolute path, that an incomplete checkpoint is skipped,
    that the dataset dir is ignored, and that an unknown slug raises SystemExit.
  - `test_run_missing_resume_fails_before_install_and_probe` checks that no subprocess runs before
    the SystemExit.
- `PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/rl -q`: 191 passed, 9 skipped.
- `~/.awos/train-cpu-venv/bin/python -m pytest tests/rl/test_grpo.py -q`: 13 passed, 1 skipped
  (the real-harness test needs playwright).

## Limits

- Not run on Kaggle. The `notebooks/<owner>/<slug>` layout comes from the SFT kernel fix in
  1676797, not from a new observation.
- The reviewer's non-blocking notes (a)-(d) are not addressed here: default config has no SFT warm
  start, `max_hours` does not count install and probe time, GradScaler state is not checkpointed, and
  `validate` checks only the split.

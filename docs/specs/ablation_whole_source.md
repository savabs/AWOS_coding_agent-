# Ablation 4: whole top source in the one-shot context

Pre-registered 2026-10-06, before any run. Stage 1 (one excellent worker).

## Why

In reliability run 2, AWOS failed the parse issues because it never edited.
The cause, from a log and code diagnosis:

- `one_shot.build_context` sent `parse/__init__.py` (about 9k tokens, over the
  6k per-file cap) as sections around noisy grep hits, so the fix region was
  missing:
  - parse_137: 1 of 5 fix lines in context;
  - parse_159: 0 of 4.
- The model then wrote 16k tokens asking to see the missing code, and the reply
  was cut off.
- The agent loop read for 26 turns without editing; that is a separate problem.

Aider, which sees whole files, solves these issues in about 2 turns.

## Change (one change)

Branch `worktree-agent-a682029614cf012fe`, commit `d4839bf`.

- The top relevant non-test source goes into the context **whole and first**
  when it is over the per-file cap but costs no more than
  `AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION` (default 0.5) of the 24k budget.
- Otherwise ranking and sectioning stay as they were.
- `=0` turns the rule off, and that is the control arm.
- The offline rebuild shows parse context gets every fix line, and
  boltons_428 and jsonpointer_64 get byte-identical context.
- Known risk: boltons_458's test file no longer fits.

The grep single-file fix (`075a0a6`) lands **before** both arms as a plain bug
fix, so it is not part of this comparison.

## Design

- **Issues:** the real-issue series where the rule changes the context.
  - parse_137, parse_159, parse_249;
  - boltons_458, boltons_474;
  - tomlkit_591, toolz_635.
- **Controls:** boltons_428 and jsonpointer_64, where the context is unchanged.
- **Arms:** `off` with the fraction at 0 (control) versus `off` with the
  fraction at 0.5. Both arms use the same code and differ only in the env var.
  Each arm gets `--repeat 2`.
- **Primary metric:** solve rate on the 7 affected issues, scored per issue and
  paired, with a sign-flip permutation test and a bootstrap CI.
- **Guardrails:**
  - billed $ per issue may rise by at most 50%;
  - no affected issue may go from 2/2 to 0/2 without a log explanation;
  - the controls must not change.

## Decision rule (written before running)

- **Adopt (default 0.5)** if the solve rate on affected issues is up and the
  guardrails hold. A significant result is not required at this n; the
  direction must be consistent with at least 2 issues improved and at most 1
  worse.
- **Reject (default 0)** if the solve rate is flat or down, or a guardrail
  fails.
- If boltons_458 drops but parse rises, report it and consider a lower
  fraction. Do not pick a fraction after seeing the data: a re-tune would be
  its own run.

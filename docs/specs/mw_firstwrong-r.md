# First-wrong-step finder: review fix (branch `claude/mw-firstwrong-r`)

Base: `claude/mw-firstwrong` (10bf6c0). Full spec: `mw_firstwrong.md`.

## What was wrong

When OracleModel cannot find its next control, it replies
`needs_user("oracle: <op> target ... is not in the observation")`; when the script runs out it
replies `needs_user("oracle: script exhausted")`. On a pause task, factory `verify.py` passes any run
that leaves the state unchanged and ends in needs_user. So `passed()` counted the oracle's own failure
as a recovery, and that debug string could become the SFT target and the DPO `chosen` text.

## Fix

- `first_wrong.oracle_failed(reply)` recognises the oracle failure reply.
- `passed(rp, k)` rejects any check where an oracle-played step (after the k agent replies) has
  such a reply, whatever the verifier says.
- Defensive check: if a correction reply is ever an oracle failure, the trajectory gets the verdict
  `no_valid_correction` and no row is written.

## Tests

- `test_oracle_failure_reply_is_never_a_recovery_on_pause_tasks` (fake env, pause mode): a plan that
  only "passes" through the exhaustion message gives `oracle_fails_at_start`. After a detour that
  hides the oracle's control, the correction is the real `needs_user("checkpoint: confirm")`, not
  the oracle text. With the filter disabled, this test fails.
- `test_real_pause_task_never_uses_oracle_failure_text` (Chromium): the reviewer's repro on
  `factory/checkpoints/reauth/000`, with click(3) then `done("finished")`. Now labelled at step 2,
  `gave_up_early`, with the correction
  `needs_user("The site asks me to re-enter the account password.")`.
  The click(1) variant gives the correction `fill(4, "kofi.ito@mail.test")`.
- `PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/rl -q`: 202 passed, 7 skipped,
  9 deselected.

## Measured

- **5 real failed pause episodes** (Qwen3.5-4B, factory train s10/s19): old and new code give
  identical verdicts. None of them hit the bug.
- **Factory run on s14+s18 (artifact 39864014413):** 43/43 labelled, 33 train / 6 valid rows,
  0 rows containing `oracle:`.

## Limits

- **Pause tasks.** A wrong click that keeps the data unchanged is still not flagged. Under the pause
  verifier, stopping later is a valid recovery, so the first wrong step is the later `done()`, with
  the real checkpoint `needs_user` as the correction.
- **`unsafe` category (reviewer's minor note, not changed).** It still comes from unsafe codes in the
  oracle checks played from s_k, so it can be category noise when the oracle walks into a trap from
  a wrong state. It does not change the training text.

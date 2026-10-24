# Learning phase: where AWOS lags reality (2026-09-30)

This learning phase ran three read-only investigations over every ordertool
run so far, the salesdesk run, and the code. It made no code changes.

- **A: why jobs fail.** 54 failed job-runs, 184 failing hidden tests.
- **B: where money and time go.** 48 AWOS jobs from runs 20260929T131340 and
  20260930T125415.
- **C: which model calls can fail silently.** Every LLM call site on the
  default path.

## A. Why jobs fail: agent quality, not guessing

| Class | Tests | Share |
|---|---|---|
| Unstated, but inferable from code | 8 | 4% |
| Stated in the goal, missed | 32 | 17% |
| Attempted, wrong | 96 | 52% |
| Rolled back or timed out, nothing delivered | 30 | 16% |
| Harness (Aider sent no files) | 18 | 10% |

No hidden test asks for something the agent could not know.

The benchmark therefore measures whether the agent follows stated
requirements and the conventions visible in the code. It does not measure
whether the agent guesses well.

Recurring causes:

- **The agent stops when the visible tests pass.** The 25 visible tests never
  cover the new requirement.
- **The worker gets a paraphrase of the goal.** It sees the planner's
  rewording, not the goal itself. In j12 the paraphrase lost the literal
  layout.
- **Existing helpers are reinvented.** The agent writes its own instead of
  reusing `round_money`, the error classes or the header check. Rounding
  alone failed j04, j06 and j12.
- **A max_turns or repeat stop rolls back all the work.** Good partial work
  is lost with it.
- **The Aider arm is invalid on j08, j11 and j12.** It ran with no files
  added, so Aider asked "please add the files" and made no edits. Aider's
  5/12 score understates it.

## B. Where the money goes

Mean billed cost is $0.029 per job; mean time is 390 s.

| Component | Cost per job | Share |
|---|---|---|
| Agent turns (25.6 turns, ~9.6k input tokens each) | ~$0.0215 | ~74% |
| Integration reviewer (claude-sonnet-4-6, unpinned, output unused) | ~$0.0045 | ~15% |
| Planner, mostly failures (fixed in 13332e2) | ~$0.0035 | ~12% |
| Notebook | $0.0004 | ~1.5% |

- **Most input is re-sent context.** Over 85% of agent input is context sent
  again every turn. Prompt caching is effectively absent: there are no cached
  tokens, and the discount is only about 15%.
- **Fixed overhead is about $0.006 per job.** It comes from the planner and
  the reviewer. On easy jobs AWOS costs 4–8× what Aider does, and about 75%
  of that floor is overhead.
- **Time is model latency.** A turn takes about 9 s. Tools take less than
  1 s.
- **The ledger is not 2× low.** On long jobs billed cost is below the ledger,
  because input is billed about 15% under list price.

## C. Silent failures

| Call site | Severity | Problem |
|---|---|---|
| Notebook rewrite (`project_notebook.py`) | HIGH | 2000 tokens, no reasoning cap, no `finish_reason` check. A reply with one heading passes validation. A cut-off rewrite overwrote the notebook (2732 → 364 chars) and cannot recover. |
| Failure critique (`orchestrator._cheap_call`) | HIGH | Cut-off critiques are saved to `error_patterns.jsonl`. It always uses qwen3.7-plus, its spend is unmetered, and a failure can take up to about 18 min. |
| Integration reviewer | MED | Uses unpinned Sonnet, unmetered, outside the goal budget. Its output is unused and it reports false positives. |
| Goal check | MED | Fails open: a crash becomes "complete, UNVERIFIED". |

Recommended fix: one reasoning-model policy in `providers.py`
(`_RewriteModel.call`), which nearly every OpenRouter call already goes
through. It would set:

- reasoning effort per role;
- a floor on max_tokens;
- on an empty or cut-off reply, one retry with reasoning off, then a typed
  error;
- spend recording for every call.

The agent loop's client should also be built through it.

## What to change, in order

1. **Measurement integrity.**
   - Fix the Aider harness so it adds files, and re-validate its j08, j11
     and j12.
   - Mark the salesdesk network failures invalid.
   - Measure run-to-run noise (item D).
2. **Stop silent corruption.**
   - Add the shared reasoning policy.
   - Validate notebook rewrites: keep the old notebook, keep a `.bak`.
   - Drop the Sonnet reviewer or pin it to the run's model.
3. **Correctness.**
   - Give the worker the verbatim goal.
   - Before finishing, the agent writes its own test for each stated clause.
   - Reuse existing helpers.
   - On a stop, keep the partial work instead of rolling everything back.
4. **Cost.**
   - Get prompt caching to hit: pin a caching provider and keep a stable
     prefix.
   - Trim the base prompt.
   - Cut the fixed overhead.

After these, run repeated runs so a result can be told apart from noise.

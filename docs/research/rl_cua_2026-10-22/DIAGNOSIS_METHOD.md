# Failure diagnosis method (Claude Sonnet 5.5, Message Batches)

Date: 2026-10-23. Code: `scripts/rl/diagnose_failures.py`. Workflow: `.github/workflows/rl-diagnose.yml`.
Tests: `tests/rl/test_diagnose.py` (offline).

## Why

The baseline (run 39817774382) and the qwen3.5-4b collection runs (39864014413, 39852748367) hold
about 2,240 failed episodes. Pass rates do not tell us what to fix. Each failure has to be
assigned to whoever can fix it: model training (SFT/RL data), the product agent (guards, prompt,
grammar), the product observation (AX tree rendered as numbered controls), the factory task or
verifier, the MiniWoB shim, or the harness. Reading 2,000 trajectories by hand is not realistic.
One batched LLM verdict per episode costs about half a cent.

## Pipeline

1. **Download.** `gh run download <id> --pattern 'rl-*'` into `artifacts/<run_id>/`. The run id
   is taken from the path.
2. **select** (no API calls):
   - keep failed episodes: `score.success == false` or `score.unsafe` non-empty;
   - drop infra errors: `trajectory.error`, `stop_by` of `model_error` or `infra`, or a reason
     starting with "model call failed" or "cannot open the start page";
   - for resumed runs, keep only the latest row per (run, model, task, sample);
   - dedupe near-identical episodes. The key is task id + action sequence + outcome, across samples,
     runs and models. Each group remembers every episode it stands for (`covers`), and the report
     shows counts both with and without duplicates. The group is represented by the qwen3.5-4b
     episode when there is one;
   - order the episodes: qwen3.5-4b eval splits first, then qwen3.5-4b train, then the other models.
     Within each tier, take one episode from each (model, source, family) in turn;
   - cap each cluster (model, source, family, normalised harness reason) at `--max-per-cluster`
     (default 40), so ~475 "control 1 has no accessible name" episodes do not use up the budget;
   - stop adding episodes once the estimated cost reaches `--budget-usd`. Refuse (exit 2) if the
     estimate is above `--max-usd`.
3. **submit**: one `messages.batches.create` call. It writes `batch.json` and does not run again
   unless `--force` is given, so a single job cannot spend twice.
4. **wait**: polls every 60 s for up to 5 h. On timeout it cancels the batch so that finished
   results can still be collected.
5. **collect**: streams the results and keys them by `custom_id`. Possible statuses: succeeded,
   errored, expired, canceled, refusal, parse_error, missing. Verdicts are checked against the
   enums, and the length limits are applied here (see Request shape).
6. **report**: Markdown containing:
   - coverage and real token usage and cost;
   - root_cause overall, × model, × source/split and × family, plus a qwen3.5-4b-only table;
   - counts by fix_owner;
   - the top 15 suggested fixes, grouped greedily by keyword Jaccard similarity (threshold 0.3);
   - 5 examples for each of the top 6 causes, spread over families, qwen3.5-4b first.

## Request shape

- Model `claude-sonnet-5-5`. No `temperature`, `top_p` or `top_k`: the API has rejected these
  since 2026-10-21.
- `--mode fast` (default) sends `thinking: {"type": "between_tools"}`, which is how thinking is
  turned off on Sonnet 5.5 (`disabled` returns a 400). `max_tokens` is 1500.
- `--mode think` leaves out `thinking` (adaptive) and sets `output_config.effort: "low"`.
  `max_tokens` is 6000. It costs about twice as much per episode.
- Structured output: `output_config.format = {"type": "json_schema", "schema": ...}`. Schema:
  `root_cause` (13-value enum), `first_bad_step` (int), `explanation`, `suggested_fix`,
  `fix_owner` (6-value enum). `additionalProperties: false`. Structured outputs does not support
  `maxLength`, so the 300 and 200 character limits are written in the prompt and enforced in collect.
- System prompt: a description of the AWOS agent, its verbs, the product guards (grounding /
  ambiguity refusal, unnamed-control refusal, destructive refusal, walls, duplicate and invalid
  limits, the done() acknowledgement rule, the 12-step cap, injection flagging), the verifiers and
  the cause definitions. It also includes the agent's own system prompt word for word (the most
  common one in the data). It is identical across requests and marked `cache_control: ephemeral`.
  Caching in a batch is best-effort.
- User message: goal, source/split/family/kind/tags, a few task meta keys, then each step:
  - the page purpose line, URL and title, Status, Dialog and Regions (300 chars each), page text
    (800 chars), and the control list (up to 60 lines);
  - if the page and the controls did not change since the previous step, a short note instead.
    If the control list kept its length, only the changed lines (new values, checked state);
  - the raw model reply, the parsed action, whether it was valid, and the harness notes for that
    step (control, write, duplicate_of, REJECTED, destructive_blocked, effect).

  Then outcome, reason, stop_by and the verifier notes. The history block and the instruction
  boilerplate in the agent prompt are dropped, because the step list already contains them.
- Page text is described as untrusted test data. The factory deliberately puts injection strings in it.

## Cost estimate

Tokens are estimated as characters / 3.5. Batch prices (half of $2 / $10 per MTok): input $1,
output $5, cache write $1.25, cache read $0.10. Output is estimated at 350 tokens per episode in
fast mode and 1,400 in think mode. The estimate assumes nothing is cached and adds a ×1.15 margin,
so it is an upper bound. The real cost in the report is computed from `usage` (input, output,
cache creation, cache read).

Measured on the actual artifacts of the three runs (3,826 records), with the defaults:

| | value |
|---|---|
| failed | 2,239 |
| infra excluded | 4 |
| unique after dedupe | 1,807 (428 duplicates merged) |
| skipped by cluster cap 40 | 41 |
| selected | 1,766 (qwen3.5-4b: 1,356) |
| estimated input / output tokens | 5.84 M / 0.62 M |
| estimated cost (upper bound) | $10.27 |

About 60% of the input is the shared system prompt (~2.1k tokens per request). If the cache hits
in the batch, the real cost falls towards ~$5–6. With `MODE=think` and a $11 budget, about 940
episodes fit.

## Re-attaching

If the job dies after submitting, set `BATCH_ID` in the workflow env and push again. Select is
deterministic for the same inputs and settings, so the `custom_id`s match the batch. The early
`rl-diagnosis-batch` artifact keeps `batch.json` and `selection.json`.

## Limits

- One verdict per episode, with one cause. Episodes that mix causes are forced into one of them.
- The dedupe key is exact. Episodes that differ by one action are diagnosed separately.
- Grouping fixes by keywords is a rough first pass. Read the examples before acting on a cluster.
- Model verdicts are a triage aid, not ground truth. Spot-check about 20 against the trajectories
  before changing the product or the training mix.

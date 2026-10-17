# Spec: loop breaker (Trick T2)

*Stage 1 (one excellent worker): verification and execution reliability.
Source: `docs/research/trick_book_2026-10.md` §1 #3 and §2.2 "Loop breaker".
Flag: `AWOS_LOOP_BREAKER` (default `0` until measured; `1` = on).*

## 1. Problem

DeepSeek V4 Flash and small local models sometimes fall into a verbatim loop
and keep writing until the output cap (16k tokens). The reply has no usable
edit, and we pay for every token up to the cap. Aider shows the same failure
on the same model. In our own Aider-arm logs (corpus in §5), 8 of 38 unsolved
job runs contain a tandem repeat of at least 256 characters. None of the 70
solved runs do.

## 2. Research (brief)

- **Repetition and degeneration.** Greedy and low-temperature decoding tends to
  fall into self-reinforcing loops. The more often a phrase has already
  repeated, the more likely the model is to repeat it again (Holtzman et al.
  2019, *The Curious Case of Neural Text Degeneration*, arXiv:1904.09751; Xu et
  al. 2022, *Learning to Break the Loop*, arXiv:2206.02369). The usual cheap
  signals are a long suffix self-match (the tail of the text already occurred
  earlier) and runs of identical lines.
- **DRY sampler** ("Don't Repeat Yourself", p-e-w; in text-generation-webui
  and llama.cpp). DRY is a logit penalty that grows as
  `multiplier · base^(match_len − allowed_len)` whenever the next token would
  extend a verbatim repeat of earlier context. That is exactly what a SEARCH
  block must do: copy file text that already appears in the prompt and often
  earlier in the reply. The same holds for repetition and frequency penalties,
  which are known to break code and JSON generation. Decision: **detect, don't
  penalise**, and never look inside SEARCH/REPLACE blocks. Sampling stays
  untouched, so verbatim copying is unaffected. See also the 2026 paper *Don't
  Repeat Yourself: Stopping Verbatim Loops at Sampling Time*
  (arXiv:2608.22761).
- **Measured effect in agents.** SWE-smith (arXiv:2504.21798) reports that more
  than 25% of SWE-agent-LM-32B trajectories contain a repeated action sequence
  of length ≥ 10, against under 4% for Claude 3.7 Sonnet, and that such a
  repeat means about an 89% chance of failure. Weaker models loop more, and a
  loop is almost always fatal. BudgetMLAgent (arXiv:2411.07464) is the cost
  framing: cheap models plus early stopping and escalation instead of paying
  for runaway generations. It reports no loop-specific number.
- **Streaming and early abort.**
  - The OpenAI SDK takes `stream=True` and
    `stream_options={"include_usage": True}`. Through OpenRouter, every chat
    stream ends with a usage chunk.
  - OpenRouter's stream cancellation "immediately stops model processing and
    billing" for providers that support it. For providers that don't, and for
    non-streamed calls, the full response is billed
    (openrouter.ai/docs/api-reference/streaming).
  - llama-server streams OpenAI-style SSE.
  - So we can abort early instead of waiting for the cap. Any backend that
    rejects the stream arguments gets post-hoc detection instead (§4).

## 3. Detector (`scaffold/agent/loop_guard.py`)

The detector is incremental and fed with streamed text. It tracks the same
SEARCH/REPLACE markers as `one_shot.parse_blocks`. Outside edit blocks, it
fires on any of these:

| kind | rule |
|---|---|
| `repeated_lines` | ≥ 4 consecutive identical non-blank lines (rstrip-compared, ≥ 3 visible chars; blank lines are skipped, not breaks) |
| `self_match` | the last 256 chars (~64 tokens) occurred earlier **and** the repeat is a tandem repeat: the repeated region directly follows its earlier copy (`R R…`) |
| `repeated_block` | ≥ 3 consecutive identical complete edit blocks |
| `repeated_lines_in_block` | inside a block: ≥ 50 consecutive identical lines (a real file copy does not produce this) |

**Why tandem.** The literal rule ("the tail occurred anywhere earlier")
tripped on **34 of 70 solved runs (49%)**. Flash quotes a function in prose
and then copies it again into SEARCH, and that is not a loop. With the tandem
requirement the rate drops to 0 of 70 (§5). The history of outside-block text
also restarts after every completed block. Otherwise the identical
`path / ```python` preambles of many blocks would add up to a false match.

Truncation keeps text up to the start of the second copy: one copy of the
repeated line, sentence or block survives.

## 4. Guarded call

`loop_guard.guarded_create(create, kwargs)` is used by:

- one-shot: `one_shot._UsageTap.create`, which covers the first call, the
  repair call and best-of-N candidates;
- the agent loop: `agent_loop.OpenAIToolClient.complete`, with tool-call
  deltas reassembled from the stream.

It streams the call, watching content and reasoning deltas with separate
detectors. On a trip it closes the stream, trims the text and resamples
**once** with the same prompt, at `AWOS_LOOP_BREAKER_RESAMPLE_TEMP` (default
0.3) when the first call ran at temperature 0. A temperature-0 retry tends to
loop the same way.

- Retry clean → that reply is returned, with `loop_guard="resampled"`.
- Retry loops too → the longer trimmed text is kept with
  `finish_reason="length"` and `loop_guard="tripped"`. The callers' existing
  cut-off path then takes over: one-shot applies the complete blocks and falls
  back as for a capped reply.
- A backend that raises `TypeError` on the stream arguments gets a normal call,
  post-hoc detection and one resample.

The returned object is OpenAI-shaped, so cost accounting, `_UsageTap` and
`llm_call_log` work unchanged. Usage is the sum of both attempts. An aborted
stream has no usage chunk, so its tokens are estimated at chars/4.
`llm_call_log` writes `loop_guard: resampled|tripped` on the line. stderr
prints `[LOOP-GUARD] tripped: <kind> (<stream>) after N chars; resampled`.

Off (the default): no code path changes. `_UsageTap.create` and
`OpenAIToolClient.complete` call the client exactly as before (tested).

`AWOS_LOOP_BREAKER` is recorded in `scripts/job_series.py` `ROUTING_ENV`.

## 5. False-positive check

`scripts/loop_guard_corpus.py` is read-only. It checks the detector against
the Aider-arm `run.log`s of past job series. These are the only full Flash
replies we keep, because AWOS stores 600 chars of a one-shot reply. Each job
section is one sample, holding all of that job's replies, so the numbers are
conservative. Rich wraps prose at 80 columns; code is unchanged.

| | runs | trips |
|---|---|---|
| solved (pre-registered target ≤ 2%) | 70 | **0 (0%)** |
| unsolved | 38 | 8 (21%), all `self_match` on genuine loops ("Now we need to ensure…" ×N) |

The literal non-tandem rule: 34/70 solved and 22/38 unsolved.
`tests/test_loop_guard.py::test_false_positive_rate_on_solved_runs` re-runs
this check when the corpus exists.

## 6. Risks and limits

- Loops inside a SEARCH/REPLACE body are caught only at 50 identical lines.
  A long-period loop of distinct lines inside REPLACE is not caught.
- The token estimate for an aborted stream ignores reasoning tokens not shown
  in the deltas. Spend on a trip may be under-counted slightly.
- Providers that don't support cancellation keep generating and billing after
  the abort (OpenRouter docs). We still save the wall time.
- Detection lags by up to one period on a long-period loop: a tandem repeat
  needs two full copies.
- The reasoning stream is monitored too. A reasoning loop trips the guard and
  resamples, even though the visible content may be empty.
- Keep the default off until an A/B (T1 decision rule) shows no regression in
  solved/$.

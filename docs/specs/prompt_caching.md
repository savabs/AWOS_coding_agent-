# Spec — T7 prompt caching: cache-aware cost + cache telemetry (T7a), prefix plan (T7b)

Status: T7a implemented 2026-10-09 · T7b implemented 2026-10-09 behind AWOS_STABLE_PREFIX (default off; §7).
Source: `docs/research/trick_book_2026-10.md` §1 #7, §2.7. VISION stage 1 (one excellent worker): this lowers $ per unit of useful work without touching how the work is verified.

## 1. Research (2026-10-09)

| Question | Finding | Source |
|---|---|---|
| OpenRouter usage fields | `usage.prompt_tokens_details.cached_tokens` (tokens read from cache), `.cache_write_tokens` (written), `usage.cost` (charged, already cache-priced), `usage.cost_details.upstream_inference_cost`. Usage is now **always** included; `usage: {include: true}` is deprecated. | [OpenRouter usage accounting](https://openrouter.ai/docs/use-cases/usage-accounting) |
| OpenRouter caching | DeepSeek caching is automatic (no `cache_control`). OpenRouter uses sticky provider routing to raise the hit rate: it hashes the opening messages or uses `session_id` (body or `x-session-id`), with a 10-minute idle expiry. An explicit `provider.order` overrides stickiness. | [OpenRouter prompt caching](https://openrouter.ai/docs/features/prompt-caching) |
| V4 Flash on our pinned providers ($/MTok, in / out / cache read) | DeepInfra 0.09 / 0.18 / 0.018; GMICloud 0.091 / 0.182 / 0.0182; Novita 0.14 / 0.28 / 0.028; SiliconFlow 0.13 / 0.28 / 0.028. All four bill cache reads at about **0.2× input** (not 0.1×). None are flagged `supports_implicit_caching`, yet all of them return `cached_tokens` > 0 (measured below). | `GET https://openrouter.ai/api/v1/models/deepseek/deepseek-v4-flash/endpoints` |
| DeepSeek direct (`deepseek-flash`) | Off-peak: hit $0.003 · miss $0.15 · out $0.60. Peak (01–04 and 06–10 UTC, Mon–Fri) doubles all three. The hit costs 1/50 of a miss. DeepSeek is **not** an OpenRouter endpoint for V4 Flash, so these prices need a direct key. That is a routing change and out of scope here. Direct usage reports `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`. | [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing) |
| llama-server | `cache_prompt` defaults to true (it reuses the common prefix with the slot's previous request). The response carries `timings.cache_n` (prompt tokens reused) and `usage.prompt_tokens_details.cached_tokens`. Prometheus `/metrics` is available behind `--metrics`, and `--cache-reuse N` enables KV-shift reuse. The cost is $0, so caching only buys latency and watts. | [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) |

The trick-book figure of "3.9% hit rate" is **stale**. The pinned-provider runs from 2026-10-08 already cache 70–80% of input tokens (§4).

## 2. What T7a implements

**Logging (`scaffold/agent/llm_call_log.py`, new fields only):**

- `cached_tokens` was already logged. It now falls back to DeepSeek's `prompt_cache_hit_tokens` and llama-server's `timings.cache_n`.
- `cache_write_tokens`, from OpenRouter `prompt_tokens_details.cache_write_tokens` or Anthropic `cache_creation_input_tokens`.
- `cache_hit_ratio`: cached ÷ the whole prompt. Anthropic's `input_tokens` excludes cache reads and writes, so those are added back. It is also computed in `record_call` when a caller passes `cached_tokens` directly.
- `cost_source`:
  - `provider` when the cost is OpenRouter's `usage.cost`;
  - `estimate` when it is the caller's estimate.
- `cost_est_usd`: AWOS's cache-aware estimate for the same call, logged next to the provider cost so the price table can be checked against the bill.
- `request_type` on utility calls. This separates the arbiter (`acceptance_arbitration`), which is logged under component `acceptance`.

**Pricing (`scaffold/agent/providers.py`):**

- `usage_tokens(usage, timings)` normalises the OpenAI/OpenRouter, DeepSeek-direct, Anthropic and llama-server usage shapes into `{input (whole prompt), cached, cache_write, output}`.
- `price_table(model)` gives the input, output, cache_read and cache_write rates:
  - base rates come from `agent_loop.PRICES`;
  - cache rates come from the `CACHE_PRICE_FACTORS` table (V4 Flash/Pro read = 0.2×; Claude read 0.1× and write 1.25×; and so on);
  - a model that is priced but not listed there gets **no** discount, so the estimate errs high, which is the safe direction for a cost cap;
  - `AWOS_PRICE_TABLE=<json>` overrides any rate per model.
- `estimate_cost_cached(model, in, out, cached, write)`. With no cache counts it equals `agent_loop.estimate_cost`.
- `response_cost(model, response)` returns `usage.cost` when the provider reports it, otherwise the cache-aware estimate.
- `_record_utility_spend` (one_shot, acceptance, arbiter, planner and other utility calls) is now cache-aware. The ledger gets the blended input price, so its USD matches the returned cost.

**Report (`scripts/cache_report.py <file-or-dir>... [--json]`):**

- Shows calls, input, cached, hit %, logged $, estimated $, no-cache $ and saved $.
- Breaks these down by bucket (one_shot / agent / acceptance / arbiter / other) and by serving provider.
- Old logs are tolerated: missing counts read as 0, the estimate is recomputed when `cost_est_usd` is absent, and the arbiter folds into acceptance when `request_type` is absent.

**Not changed (owned elsewhere):** `agent_loop.estimate_cost` still prices every input token at the full rate. The agent loop's in-run `outcome.cost_usd`, which drives budget caps, therefore **over-counts** cached turns by about 2× on V4 Flash (compare est$ with nocache$ in §4). Follow-up (one line, for the owner of agent_loop.py): use `providers.response_cost(model, raw_response)`, or pass the cache counts to `estimate_cost_cached`. The logged `cost_usd` is already correct, because it is OpenRouter's billed cost.

## 3. Live measurement (2026-10-09, total spend ≈ $0.0015)

- Setup: 3 calls per provider pin through `providers.utility_chat`.
- Each call sends an identical ~5.1k-token system prefix. It starts with a fresh nonce, so call 1 is a cold miss.
- The calls set `AWOS_SESSION_ID` and pin `deepseek/deepseek-v4-flash`.

| pin | call | provider | input | cached | hit | billed $ | est $ |
|---|---|---|---|---|---|---|---|
| deepinfra,gmicloud,novita,siliconflow | 1 | DeepInfra | 5111 | 0 | 0% | 0.000460 | 0.000455 |
| | 2 | DeepInfra | 5111 | 4864 | 95.2% | 0.000110 | 0.000109 |
| | 3 | DeepInfra | 5111 | 4864 | 95.2% | 0.000110 | 0.000109 |
| gmicloud | 1 | GMICloud | 5191 | 0 | 0% | 0.000480 | 0.000470 |
| | 2 | GMICloud | 5191 | 4152 | 80.0% | 0.000177 | 0.000173 |
| | 3 | GMICloud | 5191 | 4464 | 86.0% | 0.000157 | 0.000153 |

- A warm call costs **0.24×** (DeepInfra) to **0.33×** (GMICloud) of the cold call.
- The estimate is within about 1–2% of the bill. The gap is the PRICES input rate of 0.089 against the provider's 0.09–0.091.
- Caching works in blocks: DeepInfra in 64-token-ish chunks, GMICloud with a partial first hit.

`scripts/cache_report.py` on existing runs (read-only):

| run | calls | hit % (token-weighted) | one_shot | agent | acceptance | other | billed $ | no-cache $ |
|---|---|---|---|---|---|---|---|---|
| 20261008T220115 (r1+r2) | 374 | **70.6%** | 66.5% | 71.7% | 63.6% | 16.5% | 0.2585 | 0.4635 |
| 20261008T211703 | 202 | **80.4%** | 79.1% | 81.3% | 69.5% | 10.0% | 0.1317 | 0.2859 |
| 20261008T233641 (r1+r2) | 92 | 74.2% | 42.5% | 76.5% | 27.7% | – | 0.0489 | 0.1018 |

In 20261008T220115, the provider split is GMICloud 76.5% against DeepInfra 17.4%. The cause of DeepInfra's low rate has not been investigated. It may be calls landing on a cold cache after a provider change; in the live probe DeepInfra cached 95%. Caching already roughly halves the bill. The remaining waste is in one_shot, acceptance and "other" (planner, review, critique), which share only the system prompt across tasks, and in agent-loop history condensing (§5).

## 4. Prefix audit (read-only; message order as sent today)

No call sets `cache_control`. Every call is a static system prompt followed by **one user message that starts with task text**.

### one_shot (`one_shot.build_messages` L829–837, via `utility_chat`, temperature 0)

1. system `SYSTEM_PROMPT` (static).
2. user:
   - `# Task\n\n{task}`. This is `worker_task_text`: the goal, the planner action, "step of a {total}-task plan", then "Files to change", then `prev_task_context[:2000]`.
   - Optional experience block.
   - `# Repository` (`## Repository map` + `## Files`, from `build_context`).
   - A fixed closing instruction.

### Acceptance generator (`acceptance.build_messages` L151–157)

1. system `SYSTEM_PROMPT` (static).
2. user:
   - `# Issue\n\n{goal}`.
   - `# Repository (current, unfixed)`, built by `build_context(budget=6000)`. That is a **different budget from one_shot**, so the repository block differs even within one task.
   - A fixed instruction.

### Arbiter (`build_arbitration_messages` L500–512, logged as component `acceptance`, request_type `acceptance_arbitration`)

1. system `ARBITRATION_PROMPT` (static).
2. user:
   - `# Issue\n\n{goal}`.
   - For each failing test: its source and the failure tail. The tail contains pytest timings and tmp paths.
   - `# Current diff`.
   - "Answer with the JSON object for these tests: …".

### Agent loop (`AgentLoop.run` L677+)

1. system `SYSTEM_PROMPT` (static, no per-task text).
2. tools, in registration order: read_file, list_dir, find_files, grep, edit_file, run_tests, then [shell], then [run_command if a sandbox exists]. These are stable unless sandbox availability changes.
3. messages[0] user: `_agent_loop_prompt` (orchestrator L3020–3046). Order:
   - task text and plan step;
   - files to change;
   - prev_task_context;
   - exploration summary;
   - notebook;
   - experience retrieval;
   - a closing line;
   - then the one-shot fallback note, resume reason, test status, acceptance feedback, and repair feedback, all appended.
4. Turns are appended (assistant turn, then tool results clipped to 8000 chars).
5. **`_condense_history` (L1099–1142)** replaces tool results older than the last `AWOS_AGENT_KEEP_TURNS` (8) assistant turns, **in place**, with an elision stub. From turn ~9 on, the cutoff moves forward every turn. Each call therefore changes a message just past the previous cutoff, and everything after it is re-billed as fresh input.
6. Each resume starts a new `AgentLoop` with a new messages[0].

### Prefix breakers found

No timestamps, uuids, token budgets or unsorted directory listings appear in prompt text: `_walk` sorts, and `rank_files` breaks ties by path. The acceptance hidden-dir uuid never reaches the prompt. The breakers are structural:

1. **Task text comes first** in every user message:
   - goal;
   - `{total}-task plan`;
   - `prev_task_context`.

   As a result the cross-task prefix ends at the system prompt (plus tools).
2. **Retrieval that varies per goal** sits between the task and the repository:
   - experience;
   - notebook;
   - exploration summary.
3. **The repository block is task-dependent**: file ranking by grep hits and goal words, sectioning around hit lines, and `(shown below)` marks in the map. one_shot and acceptance also use **different context budgets**.
4. **The agent loop rewrites history** each turn past turn 8 (`_condense_history`), and synthetic notes carry counters ("{n} times", "at turn {k}").
5. **Resumes and repairs** rebuild messages[0] with appended dynamic feedback.
6. **Arbiter**: test output (timings, tmp paths) and the diff are inherently dynamic. They are fine as a suffix.

## 5. T7b plan — minimal reorder (implemented, see §7)

Target order for every call: **system → tools → repo card → files → goal → feedback**. Stable content comes first, and the most volatile content comes last.

1. **one_shot + acceptance:** move `# Task` / `# Issue` *after* `# Repository`.
   - Build the repository block once per task with one shared budget. Use `max(one_shot, 6000)`, or pass one_shot's `RepoContext` to acceptance.
   - Then the acceptance call (made right after one_shot on the same task) reads the whole repository prefix from cache.
   - Within a goal, across plan steps, the repo map is identical while the files may differ. Put the map before the files.
2. **Static repo card:** emit `## Repository map` without the `(shown below)` marks, as a separate leading block. The marks make the map depend on the file choice. Then sibling tasks in one repo share system + map.
3. **Agent loop messages[0]:** split it into two user messages:
   - (a) a stable block: exploration summary, notebook, files;
   - (b) a volatile block: task, plan step, prev_task_context, experience, feedback.

   Resume and repair feedback becomes a *new trailing* message instead of a rewritten messages[0].
4. **Condensing:** make `_condense_history` cache-friendly. Stub in **chunks** (e.g. advance the cutoff only every 8 turns), so the prefix stays byte-stable between condense events. Alternatively, condense only when the context nears its limit. Drop or freeze turn counters in synthetic notes.
5. **Arbiter:** keep as is. The goal moves after the static prompt only if a stable shared part (the diff) exists. Its volume is small.
6. **Measure:**
   - Compare `scripts/cache_report.py` hit % per bucket before and after, plus billed $/solve, under the T1 paired decision rule (`66c4c9c`).
   - Expected wins: one_shot and acceptance go from ~45–79% to more than 85%; agent turns past turn 8 stop re-billing condensed history.

Risk: reordering a prompt can change model behaviour, which affects the solve rate. So T7b ships behind a flag (`AWOS_STABLE_PREFIX=1`) and is A/B'd, not flipped silently.

## 6. Risks and limits of T7a

- The `CACHE_PRICE_FACTORS` defaults are a snapshot from 2026-10-09; provider prices drift. `cost_est_usd` beside the billed `cost_usd` makes drift visible, and `AWOS_PRICE_TABLE` fixes it without a code change.
- PRICES base rates are OpenRouter list rates (V4 Flash 0.089). The cheapest pinned provider is 0.09, so the estimate runs about 1–2% low on the billed cost.
- DeepSeek direct off-peak pricing is documented here but not modelled. It is not reachable through OpenRouter.
- Agent-loop budget caps still use the cache-blind `agent_loop.estimate_cost` (§2, follow-up).

## 7. T7b implemented (2026-10-09) — `AWOS_STABLE_PREFIX=1` (default off)

Code: `scaffold/agent/stable_prefix.py`. Tests: `tests/test_stable_prefix.py`. With the flag off, every prompt is byte-identical to before; the tests check this for one_shot, acceptance and the agent loop.

- **one_shot + acceptance generator:**
  - Both use `stable_prefix.repository_context`. It ranks on the verbatim goal and uses one_shot's budget, so acceptance moves from 6000 to 24000 tokens. The repo map carries no `(shown below)` marks.
  - Both send `SHARED_SYSTEM`, then a user message ordered `# Repository` → `# Instructions` (the role's old system prompt, verbatim) → task/issue.
  - The role prompt moves after the repository. Two different system prompts would otherwise end the shared prefix at byte 0; §5's plan missed this.
- **one_shot repair:** the repair continues the conversation as first request + reply + a new user message, instead of a fresh REPAIR_PROMPT call.
- **Agent loop:**
  - `_agent_loop_prompt_stable` orders the first message as exploration notes → notebook → task (goal first) → files → previous task → experience.
  - Feedback becomes new trailing user messages. This covers the one-shot note, the resume reason and the acceptance repair; the prompt is a list, built with `append_feedback`.
  - `condense_blocks` masks results older than `keep_turns` in blocks of `AWOS_STABLE_PREFIX_BLOCK` (4) turns.
  - Two results stay pinned: the last read of a file that is later edited, and the last failing `run_tests`.

**Prefix stability.** Measured offline on recorded tasks (more-itertools_1304 and parse_249; `common_prefix_fraction` on serialized requests):

| | off | on |
|---|---|---|
| acceptance → one_shot shared prefix | 0.1% | 93–94% |
| one_shot → repair shared prefix | 1.4% | 90–91% |
| agent loop, 24 turns: calls that break the previous prefix | 16 | 4 (turns 13/17/21/25) |
| agent loop: byte-weighted prefix reuse | 38–40% | 77% |

**Live run (Flash, pinned providers, 1 run each).** Cache hit rates compared with the same series' earlier runs:

- **more-itertools_1304:** solved in one shot. one_shot hit 80%, acceptance 90%, 85% in total, $0.0013. Earlier runs ranged from 87% (r1) to 69.5% (r2).
- **parse_249:** solved through the agent loop (28 turns, $0.045).
  - one_shot hit 82%; the cold earlier runs scored 0%.
  - acceptance hit 0%. It ran cold at 25k tokens, $0.0023 against $0.0005 before.
  - The agent turns hit 77.5%; earlier runs ranged from 40.6% to 80.1%.
  - Total hit rate was 73.5%.

**Risks:**
- Acceptance now pays for a 4× larger repository section on a miss. Per task, the shared-section effect on cost is roughly neutral, unless one_shot or best-of-N reuse it.
- Moving the role instructions into the user message, and ranking on the goal rather than the planner's text, may change solve rate. A/B this before flipping the default.

## 8. C11 — keep the Anthropic cache warm through long gates (2026-10-16)

Source: `docs/research/rea_synthesis_2026-10-16.md` item 11. Code: `scaffold/agent/agent_loop.py`
(`with_cache_breakpoints`, `AnthropicToolClient.keep_warm` / `keep_alive_ping`, `CacheKeepWarm`,
`keepalive_ping_budget`), `scaffold/agent/providers.py` (`cache_write_1h`, `usage_cache_write_1h`),
two lines in `scaffold/agent/loop_executor.py` (`_submit`, `_account`). Tests: `tests/test_anthropic_cache.py`.

**Problem.** An Anthropic cache entry lives 5 minutes from the *start* of the last request that wrote
or read it. After a submit, the loop's success gate runs the project's full suite; past 5 minutes the
entry is gone, and a red gate's feedback goes into the same conversation, so the next request re-writes
the whole prefix P at 1.25× input instead of reading it.

**Facts used** (claude-api skill, prompt caching, 2026-10): `cache_control: {"type": "ephemeral"}` =
5-minute TTL, `{"type": "ephemeral", "ttl": "1h"}` = 1-hour TTL. Writes cost 1.25× input (5 m) or 2×
(1 h); reads ~0.1× (0.05× on Opus 5.5 and in our table for Sonnet 5.5, 0.025× on Fable 5.1). A read
refreshes the timer for free. A `max_tokens: 0` request runs only the prefill: it bills the cache read
(and any uncached part as a write), no output, and restarts the TTL. Longer-TTL breakpoints must come
before shorter ones. `usage.cache_creation.ephemeral_1h_input_tokens` splits the writes by TTL.

**Options and break-even** (x = input price/token, r = read factor, w5 = 1.25, w1 = 2, P = cached
prefix, W = all tokens written over the run, W ≥ P; p = probability the long gate ends red with a
feedback round, i.e. that a request on the prefix follows; g = seconds from the submit request's start
to the end of the gate):

| Option | Extra cost per run | Notes |
|---|---|---|
| A. 5 m, nothing (before C11) | p · P(w5 − r)x per gate with g > 300 s | Haiku 5.5: 1.15·P·x |
| B. 1 h on every breakpoint | (w1 − w5)·W·x = 0.75·W·x **always**, gap or not | wins over A only if p·1.15·P > 0.75·W, i.e. p > 0.65 even when W = P; a run with no long gap pays +60 % on every write |
| C. keep-alive pings during the gate | k · r · P · x, k = ⌈g / 270⌉ − 1 pings | wins over A while k·r < p(w5 − r); costs 0 when g < 270 s |
| D. one 1 h "upgrade" ping at gate start | ≈ w1·P·x (re-write at 2×) | beats C only when k·r > 2, i.e. k > 20 (gate > 90 min on Haiku) — not implemented |

"1 h only on the request before the gate" (an auto hint) is **not feasible**: the request that precedes
the gate is the one whose reply decides to submit, so its TTL is fixed before a gate is known. Getting
1 h onto that request means marking every request 1 h, which is option B.

C dominates B for gates under ~36 minutes on Haiku 5.5 (k < 0.75/0.1) and costs nothing on runs without
long gaps, so it is the default. Worked example, Haiku 5.5 ($0.10 in, read $0.01, 5 m write $0.125,
1 h write $0.20 per MTok), P = 50k tokens, a 12-minute red gate (k = 2):

- A: re-write 50k × $0.125/M = $0.00625 instead of a $0.0005 read → **+$0.00575**
- B: ≥ 50k × $0.075/M = **+$0.00375** on every run, plus nothing saved on runs whose gate is green
- C: 2 × $0.0005 = **+$0.001**, and $0 on any run whose gates finish within 270 s

**What ships — `AWOS_ANTHROPIC_CACHE_TTL`:**

- `auto` (default): breakpoints stay `{"type": "ephemeral"}` (request bodies byte-identical to before).
  While the loop's gate runs (`LoopExecutor._submit` holds `client.keep_warm(...)` open), a daemon
  thread re-sends the last request with `max_tokens: 0` once the last request or ping started
  `AWOS_ANTHROPIC_KEEPALIVE_S` (270, clamped ≤ 290) seconds ago. No ping when: the gate cannot send
  feedback any more (last feedback round spent — nothing would read the cache), caching is off
  (`AWOS_ANTHROPIC_CACHE=0`), the last request forced a `tool_choice` (rejected with `max_tokens: 0`,
  and changing it misses the messages cache), or the TTL mode is not `auto`. A failed ping stops the
  pings and is logged; it never fails the gate.
- Ping budget per gate = ⌊p(w5 − r)/r⌋ from `providers.price_table` (`keepalive_ping_budget`), with
  p = `AWOS_ANTHROPIC_KEEPALIVE_RETRY_P` (default 0.5): Haiku 5.5 → 5 pings (gates up to 27 min),
  Sonnet 5.5 → 12; a model without a cache discount → 0. `AWOS_ANTHROPIC_KEEPALIVE_MAX` overrides.
  Worst case (gate green after a long run) the pings cost ≤ p·(w5 − r)·P·x, about half a miss.
- `5m`: plain 5-minute breakpoints, no keep-alive (the pre-C11 behaviour). Unknown values read as `5m`.
- `1h`: every breakpoint carries `"ttl": "1h"`; no keep-alive. For workloads where most runs pause
  5–60 minutes and then continue (human review), not for the coding loop.

**Accounting.** `price_table` gains `cache_write_1h` (2× input for `claude-*`, = `cache_write`
elsewhere, overridable in `AWOS_PRICE_TABLE`). `estimate_cost_cached` / `agent_loop.estimate_cost` take
`cache_write_1h_tokens` (a subset of the writes) and price it at that rate; `response_cost` and utility
spend read it from `usage.cache_creation`. `ModelReply.cache_write_1h_tokens` carries it to the loop's
`_account`. Each ping is accounted like a turn (logged under component `loop`, its cache read in
`run.cost_usd`, `cached_tokens` and `input_tokens`), so cost caps see it.

**Measure (not yet run live):** cache-read ratio of the first request after a gate longer than 5
minutes, auto vs `5m`, on a repo whose suite takes > 5 minutes (the synthesis's primary metric).

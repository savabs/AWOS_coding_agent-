# Spec — T7 prompt caching: cache-aware cost + cache telemetry (T7a), prefix plan (T7b)

Status: T7a implemented 2026-10-09 · T7b planned (no prompt changes yet).
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

## 5. T7b plan — minimal reorder (next step; not implemented)

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

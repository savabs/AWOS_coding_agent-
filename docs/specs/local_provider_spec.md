# Spec: local model endpoint (Trick T0)

*VISION Stage 1 (one excellent worker): routing + bare-metal compute. Enabler for
every local-first trick in `docs/research/local_first_architecture_2026-10.md`
§2.1 ("Model endpoints", marked **must fix**) and §2.3.*

## Problem

`providers.chat_client` routes to OpenRouter whenever `OPENROUTER_API_KEY` is
set, so a run cannot be pointed at a local model while the cloud key stays in
`.env` as the fallback. `build_client_from_env` already had an `AWOS_BASE_URL`
branch for the agent loop, but the one-shot call, utility calls (acceptance
generator / arbiter, notebook, review) and cheap calls all went through
`chat_client` and therefore to OpenRouter.

## Research (2026-10-09)

**Model.** Qwen3.5-9B, `unsloth/Qwen3.5-9B-GGUF`, file `Qwen3.5-9B-Q4_K_M.gguf`
(5.68 GB; 1.19M downloads; also `bartowski/Qwen_Qwen3.5-9B-GGUF`). Reasons:
- §2.3 of the architecture doc names Qwen3.5-9B as the floor for 16–24 GB
  machines (the 35B-A3B / 27B L0 models do not fit 16 GB).
- Hybrid Gated-DeltaNet + attention: most layers keep no growing KV cache, so a
  24k window costs far less memory than a dense 8B (Qwen3-8B f16 KV ≈ 144 KB/token).
- Official card reports tool use (BFCL-V4, TAU2-Bench) and coding (LiveCodeBench v6);
  thinking is on by default and can be switched off (`enable_thinking: false`).
- Alternatives checked and available: `Qwen/Qwen3-8B-GGUF` Q4_K_M (5.03 GB),
  `Qwen/Qwen2.5-Coder-7B-Instruct-GGUF` q4_k_m (4.68 GB). Older; kept as
  `AWOS_LOCAL_GGUF_REPO/FILE` overrides.

**Quantization rules** (`docs/research/trick_book_2026-10.md` §2.2): 4-bit floor
(Q4_K_M), no 3-bit, **no KV quantization by default** (4-bit KV cost −58.9% at
7B), text edit format, local context 16–24k → 24576.

**llama-server flags** (llama.cpp build 11146, `llama-server --help`):

```
llama-server -m ~/.cache/awos-models/Qwen3.5-9B-Q4_K_M.gguf --alias qwen3.5-9b \
  --host 127.0.0.1 --port 8080 -ngl 99 -c 24576 -np 1 -fa on --jinja \
  --reasoning off --cache-prompt --cache-reuse 256 \
  --temp 0.7 --top-p 0.8 --top-k 20 --min-p 0 --metrics
```

- `-ngl 99`: all layers on Metal. `-fa on`: flash attention (flag takes on|off|auto).
- `-c 24576 -np 1`: one slot owns the whole window (with N slots the window is
  split); AWOS calls are sequential. Raise `-np` only for local pass@k.
- `--cache-prompt` (default on) + `--cache-reuse 256`: the agent loop re-sends a
  growing prefix; the slot reuses it instead of re-prefilling.
- `--reasoning off`: thinking-mode control at the server (the template's
  `enable_thinking=false`); per-request `chat_template_kwargs` still works.
  Thinking off is the default per §2.3; A/B it later.
- Sampling: Qwen3.5 card, non-thinking mode. A request's own `temperature`
  (utility calls send 0) overrides it.
- `--host 127.0.0.1`: never bound outside the machine.

## Design

`AWOS_PROVIDER=local` (or `AWOS_AGENT_MODEL=local/<name>`) → `providers.local_mode()`.

| Env | Default | Meaning |
|---|---|---|
| `AWOS_LOCAL_BASE_URL` | `AWOS_BASE_URL`, else `http://127.0.0.1:8080/v1` | OpenAI-compatible base URL |
| `AWOS_LOCAL_MODEL` | `qwen3.5-9b` | served alias |
| `AWOS_LOCAL_API_KEY` | `not-needed` | only if the server was started with `--api-key` |
| `AWOS_LOCAL_CONTEXT` | 24576 | caps the one-shot context budget at half the window |

- `chat_client` / `messages_client` return a `_LocalRouted` client on the local
  server whatever keys are set. It rewrites every `model=` to the served alias
  (the router/ladder may name `deepseek/deepseek-v4-flash`; one model is loaded)
  and is not a `_Routed`, so no OpenRouter-only fields (`reasoning`, provider pin)
  are sent.
- `openrouter_key()` returns None, so no caller renames models for OpenRouter.
- `build_client_from_env` → `OpenAIToolClient(local client, "local/<alias>")`.
  The `local/` id prices at $0 (`PRICES["local"]`); `_price_for` returns $0 for
  any id in local mode, so the ledger, budget and call log show $0 with token
  counts kept.
- `llm_call_log` writes `provider: "local"`.
- `scripts/check_backend.py` (and so `job_series` backend checks) works
  unchanged; it prints `provider  local` and a start hint when the server is down.
- Unset `AWOS_PROVIDER`: behaviour byte-for-byte as before.

## Out of scope

Local→cloud escalation (C1), pass@k, constrained decoding, context compaction for
the agent loop (a long loop can exceed 24k and stop with `model_error`).

## Proof

`tests/test_local_provider.py` (selection with/without the OpenRouter key, $0,
base URL override, check_backend against a fake local server) and a live
`job_series` run fully local (see the commit message).

---
title: "Spec: OpenRouter as the Single Provider Key"
tags:
  - doc/spec
  - topic/routing
  - status/active
---

# Spec: OpenRouter as the Single Provider Key

## Problem

Eleven modules construct their own provider clients from six different keys
(`DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
`OPENCODE_GO_API_KEY`, `OPENROUTER_API_KEY`). Each key is a separate balance to
fund and a separate way to fail: the 2026-09-22 benchmark paid a 402 (DeepSeek
unfunded) and a 401 (wrong key for the endpoint) on every single-shot task
before reaching a model that worked.

Decision (user, 2026-09-23): **one OpenRouter key for all model calls.**

## Design

`scaffold/agent/providers.py` owns provider selection.

- `openrouter_key()` — the key, or `None`.
- `openrouter_model_id(model_id)` — maps the bare ids used across the code
  (`deepseek-v4-flash`, `gpt-4o-mini`, `claude-haiku-4-5`, `gemini-2.5-flash`)
  to OpenRouter's vendor-namespaced ids (`deepseek/deepseek-v4-flash`,
  `openai/gpt-4o-mini`, `anthropic/claude-haiku-4.5`, `google/gemini-2.5-flash`).
  Every mapped id was checked against `GET /api/v1/models` on 2026-09-23.
- `chat_client(direct_key, direct_base_url)` — OpenAI-shaped client. With an
  OpenRouter key it points at OpenRouter and rewrites `model` on each call;
  otherwise it falls back to the direct provider, or `None`.
- `messages_client(direct_key)` — Anthropic-shaped client. With an OpenRouter
  key it uses OpenRouter's Anthropic-compatible endpoint
  (`https://openrouter.ai/api/v1/messages`), so call sites keep `system=`,
  content blocks and usage fields unchanged.

When `OPENROUTER_API_KEY` is set it **wins**: direct keys are ignored. Direct
paths are kept, not deleted, so a machine without the key still runs.

Call sites change one line each: `OpenAI(api_key=k, base_url=u)` becomes
`chat_client(k, u)`, `Anthropic(api_key=k)` becomes `messages_client(k)`.
`google.genai` has no OpenRouter route, so with an OpenRouter key the Gemini
branches (`create_file_executor`, `react_worker`) are skipped and the next
branch — DeepSeek, served through OpenRouter — handles the call.

## Out of scope

- `agent_loop.build_client_from_env` already prefers OpenRouter; unchanged.
- Local runtimes (`AWOS_PROVIDER=local`) are unaffected.
- Model choice. Ids stay as they are; only the route changes.

## Proof

- Unit: id mapping, precedence (OpenRouter set → direct keys ignored), model
  rewriting on both client shapes.
- Live: with the key in `.env`, `scripts/check_backend.py` plus a single-shot
  benchmark case must show no DeepSeek/OpenAI/Anthropic-direct traffic.

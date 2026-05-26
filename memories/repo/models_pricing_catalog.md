---
title: "Canonical model IDs and API pricing"
tags:
  - doc/memory
  - topic/model-tiering
  - topic/pricing
---

# Models and pricing catalog

**Single source of truth** for model **names**, **Aider/LiteLLM-style IDs** (where this repo uses them), and **USD per 1M tokens** (`$/MTok`) copied from vendor docs — not from memory.

**Rules**

- All prices are **USD per 1 million tokens** unless noted.
- **Do not duplicate** these tables into specs or research without linking here; update **this file** when vendors change rates.
- **Gemini** prices often depend on **prompt length** (e.g. ≤200k vs >200k); the row notes which band applies.
- **`scripts/token_report.py`** should stay aligned with the subset marked **“In token_report”**; if they diverge, fix `PRICING` after re-checking the source URL.

**Last full reconciliation:** 2026-05-14 (Anthropic docs, Google pricing HTML, DeepSeek pricing page, OpenAI API pricing page).

---

## Legend

| Column | Meaning |
|--------|--------|
| **Input** | Non-cached input tokens (base rate). |
| **Output** | Generated tokens (including “thinking” where the vendor bills them as output). |
| **5m cache write** | Anthropic: write price for **5-minute** prompt cache TTL. |
| **1h cache write** | Anthropic: write price for **1-hour** prompt cache TTL. |
| **Cache read / hit** | Tokens read from prompt cache (Anthropic) or context cache **token** price (Gemini table); **not** the same accounting as Anthropic. |
| **Aider `--model` example** | Typical LiteLLM routing string for Aider ([other LLMs](https://aider.chat/docs/llms/other.html)). Anthropic-first-party models are often configured **without** the `anthropic/` prefix in `.aider.conf.yml`. |

---

## Anthropic (Claude API)

**Source:** [Model pricing](https://docs.anthropic.com/en/docs/about-claude/pricing) (retrieved 2026-05-14).  
**Prompt caching:** [Prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) — cache read = **0.1×** base input; 5m write = **1.25×** base input; 1h write = **2×** base input (same page, “Prompt caching” section).

API model strings commonly used in tooling: `claude-sonnet-4-5`, `claude-haiku-4-5`, `claude-opus-4-5`, etc. (see Anthropic model docs for exact deprecation status of older IDs.)

| Model (API name) | Input $/MTok | 5m cache write $/MTok | 1h cache write $/MTok | Cache read $/MTok | Output $/MTok | In token_report |
|------------------|-------------:|----------------------:|----------------------:|------------------:|--------------:|:----------------|
| Claude Opus 4.7 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 | no |
| Claude Opus 4.6 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 | no |
| Claude Opus 4.5 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 | **yes** (`claude-opus-4-5`) |
| Claude Opus 4.1 | 15.00 | 18.75 | 30.00 | 1.50 | 75.00 | no |
| Claude Sonnet 4.6 | 3.00 | 3.75 | 6.00 | 0.30 | 15.00 | no |
| Claude Sonnet 4.5 | 3.00 | 3.75 | 6.00 | 0.30 | 15.00 | **yes** (`claude-sonnet-4-5`) |
| Claude Haiku 4.5 | 1.00 | 1.25 | 2.00 | 0.10 | 5.00 | **yes** (`claude-haiku-4-5`) |
| Claude Haiku 3.5 | 0.80 | 1.00 | 1.60 | 0.08 | 4.00 | **yes** (`claude-haiku-3-5`; retired except Bedrock/Vertex per Anthropic) |

**Note:** Haiku 3.5 / Sonnet 4 / Opus 4 deprecations: see Anthropic [model deprecations](https://docs.anthropic.com/docs/en/about-claude/model-deprecations).

---

## Google (Gemini Developer API)

**Source:** [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) (HTML retrieved 2026-05-14).  
**Aider examples:** `gemini/gemini-2.5-flash`, `gemini/gemini-2.5-pro`, `gemini/gemini-2.5-flash-lite`.

Google publishes **Free** vs **Paid** columns and **context caching** (cached token price + **storage** price per MTok per hour). Below is the **paid** text/image/video line where applicable; audio rates differ (see source).

### Gemini 2.5 Pro (`gemini-2.5-pro`)

| Band | Input $/MTok | Output $/MTok | Context cache: cached tokens $/MTok | Storage (per 1M cached tokens / hour) |
|------|---------------:|--------------:|------------------------------------:|---------------------------------------:|
| prompts ≤200k | 1.25 | 10.00 | 0.125 | 4.50 |
| prompts >200k | 2.50 | 15.00 | 0.25 | 4.50 |

**In token_report:** `gemini-2.5-pro` uses the **≤200k** input/output/cache token columns only (storage billed separately on Google’s side).

### Gemini 2.5 Flash (`gemini-2.5-flash`)

| | Input $/MTok | Output $/MTok | Context cache: cached tokens $/MTok | Storage $/MTok/hour |
|---|-------------:|--------------:|------------------------------------:|--------------------:|
| Paid (text/image/video) | 0.30 | 2.50 | 0.03 | 1.00 |

Free tier limits (e.g. RPD) are on the same page and change over time — **do not** copy free-tier numbers here without re-reading Google.

**In token_report:** `gemini-2.5-flash` matches **paid** text/image/video row for input/output/cache token columns; storage is separate.

### Gemini 2.5 Flash-Lite (`gemini-2.5-flash-lite`)

| | Input $/MTok | Output $/MTok | Context cache: cached tokens $/MTok | Storage $/MTok/hour |
|---|-------------:|--------------:|------------------------------------:|--------------------:|
| Paid (text/image/video) | 0.10 | 0.40 | 0.01 | 1.00 |

**In token_report:** `gemini-2.5-flash-lite` matches the above paid row for the three rate columns used in `PRICING`.

---

## DeepSeek (OpenAI-compatible API)

**Source:** [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing) (retrieved 2026-05-14).

| Model id (API) | Input cache miss $/MTok | Input cache hit $/MTok | Output $/MTok | Notes |
|----------------|------------------------:|-----------------------:|--------------:|-------|
| `deepseek-chat` | 0.14 | 0.0028 | 0.28 | Alias for **non-thinking** `deepseek-v4-flash`; DeepSeek states aliases will be deprecated in favor of explicit v4-flash naming. |
| `deepseek-reasoner` | 0.14 | 0.0028 | 0.28 | Alias for **thinking** `deepseek-v4-flash`. |
| `deepseek-v4-flash` | 0.14 | 0.0028 | 0.28 | Same table row as above on pricing page. |
| `deepseek-v4-pro` | 1.74 (0.435 with time-limited discount on page) | 0.0145 (0.003625 discounted) | 3.48 (0.87 discounted) | **Discounted** rates on DeepSeek’s page were **time-limited** (check current page for active discount). |

**Aider examples:** `deepseek/deepseek-chat`, `deepseek/deepseek-reasoner`.

**In token_report:** `deepseek-chat` and `deepseek-reasoner` use miss/hit split consistent with DeepSeek’s table; `cache_write` in `PRICING` is set equal to miss input for reporting simplicity (see DeepSeek billing rules on their page).

---

## OpenAI (API)

**Source:** [OpenAI API pricing](https://openai.com/api/pricing/) (retrieved 2026-05-14). Below: **standard** processing, context under **270k** where the page states a caveat.

| Model | Input $/MTok | Cached input $/MTok | Output $/MTok |
|-------|-------------:|--------------------:|--------------:|
| GPT-5.5 | 5.00 | 0.50 | 30.00 |
| GPT-5.4 | 2.50 | 0.25 | 15.00 |
| GPT-5.4 mini | 0.75 | 0.075 | 4.50 |

**Aider examples:** typically `openai/gpt-5.5`, `openai/gpt-5.4`, etc. — confirm with `aider --list-models gpt` locally.

**In token_report:** not yet — add rows when you standardize on OpenAI models for cost reports.

---

## OpenRouter

**Source:** Per-model cards on [openrouter.ai/models](https://openrouter.ai/models) (prices vary by upstream and change frequently).

**Env:** `OPENROUTER_API_KEY` ([LiteLLM env list](https://aider.chat/docs/llms/other.html)).

**Policy for this repo:** do **not** paste OpenRouter-derived $/MTok numbers into this catalog unless each row cites the **exact model card URL** and **date** copied. Use OpenRouter for discovery and failover; keep **vendor tables** above as the audit trail for flagship SKUs.

---

## Groq, Mistral, Cohere, Together AI, Fireworks, Replicate

**Policy:** same as OpenRouter — pricing is **model-specific** and changes often. Use:

- Groq: [console.groq.com](https://console.groq.com/) pricing / model list  
- Mistral: [mistral.ai/pricing](https://mistral.ai/pricing)  
- Cohere: [cohere.com/pricing](https://cohere.com/pricing)  
- Together: [together.ai/pricing](https://www.together.ai/pricing)  
- Fireworks: [fireworks.ai/pricing](https://fireworks.ai/pricing)  
- Replicate: per-model page on [replicate.com](https://replicate.com/)

Add a **dated** subsection here only when a specific SKU is locked in for production routing.

---

## Related

- [[project_structure]] — project metrics; link this file for pricing truth
- `scripts/token_report.py` — cost aggregation for models present in `PRICING`
- `CONVENTIONS.md` — MODEL ROUTING (operational defaults, not a pricing sheet)

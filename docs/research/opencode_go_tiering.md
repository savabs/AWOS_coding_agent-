---
title: "Research: OpenCode Go + Zen — Model Offerings &amp; AWOS Tier Integration"
tags:
  - doc/research
  - phase/4
  - topic/model-tiering
  - topic/opencode-go
  - status/active
---

> **Content:** [opencode_go_tiering.html](opencode_go_tiering.html) — open in browser.

## Summary

OpenCode Go ($10/mo flat) gives us access to 13 open-source coding models including MiMo-V2.5, Qwen3.7 Plus, Kimi K2.7 Code, and GLM-5.2 — models we don't have direct API keys for. Primary value is **backup routing** (when DeepSeek/Gemini APIs are down) and **access to unique models** not available via our existing providers.

Zen PAYG is not cost-effective vs direct APIs for proprietary models, but its **free models** could be useful as T0 alternatives for non-sensitive code.

## Key Recommendation

Add OpenCode Go as a rotation provider in the AWOS escalation ladder — like OpenRouter. Integrate 3-4 Go models at different tier levels:
- **T2:** MiMo-V2.5 ($0.14/MTok) — backup to DeepSeek
- **T3:** Qwen3.7 Plus ($0.40/MTok) — mid-tier open model
- **T4:** Kimi K2.7 Code ($0.95/MTok) — code-specialized

See the HTML doc for full pricing tables and integration plan.

## Related
- [[model_tiering]] — existing AWOS model tier research
- [[openrouter_integration]] — how OpenRouter was integrated
- [[models_pricing_catalog]] — canonical pricing truth
- [[project_structure]] — active phase and metrics

---
title: "Research: Multi-Provider Model Tiering System"
tags:
  - doc/research
  - topic/model-tiering
  - phase/2
  - status/active
---
> **Content:** [model_tiering.html](model_tiering.html) — open in browser.

## Summary
Research into multi-provider model routing for cost-efficiency. Key finding: DeepSeek-Chat (V4-Flash) scores 70.2% on Aider polyglot benchmark at $0.14/MTok — 21× cheaper than Claude Sonnet-4 for higher benchmark quality.

## 5-Tier System
| Tier | Model | Input $/MTok | Polyglot % |
|---|---|---|---|
| T1 FREE | Gemini 2.5 Flash (free tier) | $0 | 44% |
| T2 CHEAP | deepseek/deepseek-chat | $0.14 | 70.2% |
| T3 MID | deepseek/deepseek-reasoner | $0.14 | 74.2% |
| T4 MAIN | gemini/gemini-2.5-pro | $1.25 | 79-83% |
| T4 MAIN | claude-sonnet-4-5 | $3.00 | 56.4% |
| T5 HEAVY | claude-opus-4-5 | $5.00 in (see catalog) | 70.7% |

## Related
- [[token_efficiency]] — base research (Aider + Claude setup)
- [[model_tiering_spec]] — implementation spec
- [[model_tiering_task]] — active task checklist

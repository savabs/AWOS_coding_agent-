---
title: "Checkpoint 2026-05-13 — Model Tiering System Complete"
tags:
  - doc/checkpoint
  - phase/2
  - topic/model-tiering
  - status/done
date: 2026-05-13
---

## Session Summary

Completed full AWOS preflight + implementation of multi-provider model tiering system.

## What Was Done

### AWOS Artifacts Created
- `docs/research/model_tiering.html` — 6-tab research: benchmark data (polyglot YAML), verified pricing (Gemini, DeepSeek, Claude), 5-tier definitions, routing decision tree, Aider setup guide
- `docs/research/model_tiering.md` — Obsidian stub
- `docs/specs/model_tiering_spec.html` — 5 atomic steps spec
- `docs/specs/model_tiering_spec.md` — Obsidian stub
- `tasks/active/model_tiering_task.html` — Interactive task checklist (8 steps: 5 impl + 3 verification)
- `tasks/active/model_tiering_task.md` — Obsidian stub

### Implementation (all 5 spec steps done)
- **1.1** `.env` — added `DEEPSEEK_API_KEY=` and `GEMINI_API_KEY=` placeholder entries with URLs
- **1.2** `scripts/token_report.py` — PRICING dict expanded: now covers 9 models (Claude ×4, Gemini ×3, DeepSeek ×2)
- **1.3** `Makefile` — 8 targets: `help`, `dev`, `dev-cheap`, `dev-think`, `dev-mid`, `dev-pro`, `prewarm`, `report`
- **1.4** `CONVENTIONS.md` — MODEL ROUTING section appended with 5-tier table + escalation rules
- **1.5** `memories/repo/project_structure.md` — updated with multi-provider cost table, corrected metrics, dual active tasks

## Key Facts (verified from primary sources)

| Fact | Value | Source |
|---|---|---|
| DeepSeek-Chat polyglot score | 70.2% (pass_rate_2) | Aider polyglot leaderboard YAML, 2025-10-03 |
| DeepSeek-Chat input price | $0.14/MTok (cache miss), $0.0028 (hit) | api-docs.deepseek.com, 2026-05-13 |
| Gemini 2.5 Pro input price | $1.25/MTok (≤200k) | ai.google.dev/gemini-api/docs/pricing, 2026-05-13 |
| Gemini 2.5 Flash free tier | Yes, up to 500 RPD | ai.google.dev/gemini-api/docs/pricing, 2026-05-13 |
| Claude Sonnet-4 polyglot | 56.4% (no think) | Aider polyglot YAML, 2025-05-24 |
| DeepSeek vs Sonnet cost ratio | 21× cheaper input for higher benchmark | Calculated |
| deepseek-chat API name | Now = deepseek-v4-flash (non-thinking) | api-docs.deepseek.com, 2026-05-13 |
| deepseek-reasoner API name | Now = deepseek-v4-flash (thinking mode) | api-docs.deepseek.com, 2026-05-13 |

## Decision: Default Model = DeepSeek-Chat

DeepSeek-Chat (deepseek/deepseek-chat) is now the recommended default for all coding tasks:
- 70.2% polyglot score vs 56.4% for Claude Sonnet-4
- $0.14/MTok input vs $3.00/MTok for Sonnet — 21× cheaper
- `make dev-cheap` launches it; `make dev` remains as Sonnet fallback

Tradeoffs accepted: slower latency (104s/case), China-based API (no PII/IP), no Anthropic SDK caching.

## Immediate Next Steps

1. **Get DeepSeek API key** → platform.deepseek.com → fill `DEEPSEEK_API_KEY=` in `.env`
2. **Get Gemini API key** → aistudio.google.com/apikey → fill `GEMINI_API_KEY=` in `.env`
3. **Run verification V1**: `aider --model deepseek/deepseek-chat --message "say hi"`
4. **Run verification V2**: `aider --model gemini/gemini-2.5-flash --message "say hi"`
5. **Run `make dev-cheap`** to confirm end-to-end tier 2 workflow
6. **Run baseline session** with `--no-stream`, then `make report` for cache metrics (pending step 2.4)

## Blocked On

- DeepSeek API key not yet obtained (placeholder only)
- Gemini API key not yet obtained (placeholder only)
- No real Aider session run yet (cache hit rate still unknown)

## Related
- [[model_tiering]] — research document
- [[model_tiering_spec]] — spec
- [[model_tiering_task]] — task (steps 1.1-1.5 complete; V1-V3 pending)
- [[token_efficiency]] — previous research (Aider + Claude setup)
- [[phase1_aider_setup]] — previous task

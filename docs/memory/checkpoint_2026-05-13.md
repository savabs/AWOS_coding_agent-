---
title: "Checkpoint 2026-05-13 — Project defined, Phase 1 task ready"
tags:
  - doc/checkpoint
  - phase/1
  - topic/token-efficiency
  - topic/aider
---
> **Content:** This file. Session checkpoint — immutable after session ends.

## Session Summary
Defined the entire project from scratch. Goal: maximum token efficiency as Copilot
moves to token-based pricing June 1 2026. Migration path: Copilot Pro → Aider + Claude API.

## What Was Completed This Session
- Ran cold-start, defined project identity and mission
- Researched (verified): Claude prompt caching pricing, Aider options, all token efficiency strategies
- Created full preflight triad:
  - `docs/research/token_efficiency.html` — 8-tab deep research note
  - `docs/specs/token_efficiency_spec.html` — 10-step atomic spec across 3 phases
  - `tasks/active/phase1_aider_setup.html` — interactive checklist
- Filled in `memories/repo/project_structure.md` with project identity, cost table, phase status

## Current State
- Phase 1 has NOT started (research/spec/task only, no implementation files yet)
- No Aider installed yet
- No `.env`, `.aider.conf.yml`, `.aiderignore`, `CONVENTIONS.md` created yet

## Immediate Next Steps
1. Get ANTHROPIC_API_KEY from Anthropic console
2. Run Step 1.1: `pip install aider-chat anthropic python-dotenv`
3. Follow task checklist in `tasks/active/phase1_aider_setup.html` step by step

## Key Facts

| Fact | Value |
|---|---|
| Copilot token pricing starts | June 1, 2026 |
| Recommended model | claude-sonnet-4-5 ($3/$15 input/output per MTok) |
| Cache read cost | $0.30/MTok (10% of base input) |
| Cache min threshold | 1,024 tokens (sonnet-4-5) |
| Aider cache flag | `--cache-prompts` |
| Aider map tokens | `--map-tokens 512` (reduced from default) |
| Weak model | claude-haiku-3-5 ($0.80/$4 per MTok) |

## Related
- [[token_efficiency]] — research
- [[token_efficiency_spec]] — spec
- [[phase1_aider_setup]] — task
- [[project_structure]] — canonical metrics

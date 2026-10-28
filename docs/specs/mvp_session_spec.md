---
title: "Spec: MVP Session Launcher (tiered Aider)"
tags:
  - doc/spec
  - phase/2
  - topic/mvp
  - topic/model-tiering
---

# Spec: MVP Session Launcher

## Goal

Ship a **single, repeatable way** to open an Aider session at the right **model tier** so work can move fast without building a custom editor or a self-learning router.

**Exit condition:** From a clean shell, `make copilot` or `make mvp` (or `python3 scripts/mvp_aider.py`) starts Aider at the default cheap tier; switching tiers is one env var (`COPILOT_TIER` / `TIER`); user-facing loop documented in `WORKFLOW.md`.

## In scope (MVP)

- One launcher script: `scripts/mvp_aider.py`
- Make targets: `make copilot` (primary UX name) and `make mvp` (alias; tier `TIER=…` or `COPILOT_TIER=…`)
- Tier table aligned with `CONVENTIONS.md` **MODEL ROUTING** (subset: 2, 3r, 3g, 4g, 4c, 5)
- Claude tiers **4c** and **5** run `scripts/prewarm_cache.py` first; other tiers skip prewarm

## Out of scope (not MVP)

- Custom coding editor UI
- Automatic escalation / failure detection loops (cascade router)
- Self-learning or cognitive memory system
- OpenRouter / dynamic model registry (keys may exist in `.env`; launcher stays on fixed tiers)

## Tier reference

| ID | Behavior |
|----|----------|
| `2` | `deepseek/deepseek-chat` — default worker |
| `3r` | `deepseek/deepseek-reasoner` |
| `3g` | `gemini/gemini-2.5-flash` |
| `4g` | `gemini/gemini-2.5-pro` |
| `4c` | Aider default from `.aider.conf.yml` (Claude Sonnet + Haiku weak) + prewarm |
| `5` | `claude-opus-4-5` + prewarm |

## Related

- [[WORKFLOW]] — daily Copilot-style loop and token levers
- [[model_tiering_spec]] — full multi-provider plan
- [[CONVENTIONS]] — escalation rules when not using the launcher

---
title: "Spec: Model Tiering — Multi-Provider Routing"
tags:
  - doc/spec
  - topic/model-tiering
  - phase/2
  - status/active
---
> **Content:** [model_tiering_spec.html](model_tiering_spec.html) — open in browser.

## Steps (5 atomic)
- 1.1 Add DEEPSEEK_API_KEY + GEMINI_API_KEY to .env
- 1.2 Expand PRICING dict in token_report.py
- 1.3 Add Makefile tier targets (dev-cheap, dev-think, dev-mid, dev-pro)
- 1.4 Add MODEL ROUTING section to CONVENTIONS.md
- 1.5 Update memories/repo/project_structure.md

## Related
- [[model_tiering]] — research
- [[model_tiering_task]] — task checklist
- [[token_efficiency_spec]] — previous spec (Aider setup)

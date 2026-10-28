---
title: "Task: Phase 1 — Aider + Claude API Token-Efficient Setup"
tags:
  - doc/task
  - phase/1
  - topic/token-efficiency
  - topic/aider
  - status/active
---
> **Content:** [phase1_aider_setup.html](phase1_aider_setup.html) — open in browser for interactive checklist.

## Steps (10 total)
### Phase 1 — Install & Configure
- [ ] 1.1 Install Aider and Anthropic SDK
- [ ] 1.2 Create `.env` with ANTHROPIC_API_KEY
- [ ] 1.3 Create `.aider.conf.yml` (token-optimal defaults)
- [ ] 1.4 Create `.aiderignore` (lean repo map)
- [ ] 1.5 Create `CONVENTIONS.md` + add to conf as read file

### Phase 2 — Token Measurement
- [ ] 2.1 Create `scripts/token_report.py`
- [ ] 2.2 Create `scripts/prewarm_cache.py`
- [ ] 2.3 Create `requirements.txt`
- [ ] 2.4 Run baseline session and record metrics

### Phase 3 — Advanced Optimizations
- [ ] 3.1 Add Makefile with `make dev` target
- [ ] 3.2 Evaluate architect mode on a real task

## Related
- [[token_efficiency]]
- [[token_efficiency_spec]]
- [[project_structure]]

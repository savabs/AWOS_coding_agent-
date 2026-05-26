---
title: "Research: Token Efficiency with Aider + Claude API"
tags:
  - doc/research
  - phase/1
  - topic/token-efficiency
  - topic/aider
  - topic/caching
  - status/active
---
> **Content:** [token_efficiency.html](token_efficiency.html) — open in browser.

## Summary
Research into token cost minimization strategies as Copilot moves to token-based
pricing June 1 2026. Migration path: Copilot Pro → Aider + Claude API.

Key findings:
- Cache reads cost 10% of base input price (10× savings)
- Output tokens cost 5× more than input — minimize verbosity
- Aider `--cache-prompts` + `--cache-keepalive-pings 3` handles most caching automatically
- Repo map is a major token sink — use `--map-tokens 512` + `.aiderignore`
- Model tiering: Haiku for cheap tasks, Sonnet for complex, Architect mode for large refactors
- Pre-warm cache with `max_tokens=0` API call at session start

## Related
- [[token_efficiency_spec]]
- [[phase1_aider_setup]]
- [[project_structure]]

---
title: ReflexionMemory — Phase 5B Research
date: 2026-05-19
phase: 5B
status: complete
tags: [reflexion, verbal-rl, error-pattern-store, critique-injection, self-learning]
related:
  - docs/specs/reflexion_memory_spec.html
  - docs/research/test_runner_reward.html
  - scaffold/agent/self_correction.py
  - scaffold/agent/error_pattern_store.py (new)
---

## Goal
Upgrade AWOS from rule-based static error hints to **adaptive verbal critique memory**
that persists failure narratives across sessions and injects them into retry prompts.

## Hypothesis (Internet-verified)
Reflexion (Princeton NeurIPS 2023): storing verbal self-critiques in an episodic memory
buffer and prepending them to retry prompts improves HumanEval pass@1 from 80% → 91%
**without any weight updates**. Self-Refine (NeurIPS 2023): iterative feedback loops with
actionable, localized critiques further improve code quality on successive attempts.

## Key Decisions
1. **Storage**: JSONL append-only at `.awos/error_patterns.jsonl` — same pattern as RewardStore.
2. **Critique generation**: cheap LLM call (Gemini Flash / DeepSeek) on failure only, not on every attempt.
3. **Retrieval**: fuzzy match on (file_path + error_type) — no vector DB needed, keeps it free.
4. **Injection point**: `Worker.execute_task()` prepends top-3 relevant critiques to prompt.
5. **Cap**: max 5 critiques per (file, error_type) pair to prevent prompt bloat.

## What AWOS Already Has
- SelfCorrectionEngine: rule-based, 4 error classes, static hints — no persistence, no LLM.
- SkillLibrary: persists successful approaches — but not failure critiques.
- VectorMemory: persists interactions — too broad, not failure-specific.

## What Is Missing (Phase 5B)
- ErrorPatternStore: persist (task_id, file, error_type, error_msg, critique, timestamp).
- CritiqueGenerator: LLM-generated verbal critique on failure (cheap model, 1 call).
- CritiqueInjector: load top-N relevant critiques and prepend to worker prompt on retry.

## Risk Summary
- **Cost**: ~1 cheap LLM call per failure. Estimated <$0.0001 per critique. Negligible.
- **Hallucinated critiques**: mitigated — critique is informational only, not executable code.
- **Prompt bloat**: capped at 3 injected critiques × ~100 tokens = ~300 tokens overhead.
- **False critique loop**: if critique is wrong, next attempt still subject to TestRunner pass_rate.

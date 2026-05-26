# SOTA Coding Agent Improvements — Research

**Full doc**: [docs/research/sota_improvements.html](sota_improvements.html)  
**Date**: 2026-05-22

## Summary
Research into SOTA coding agents (Claude Code src/, Aider, OpenHands, SWE-Agent, SWE-bench) identifying gaps in AWOS and an improvement roadmap P0–P3.

## Key Findings
- Claude Code uses JSON structured tool calls (not SEARCH/REPLACE text blocks)
- Claude Code enforces read-before-write — no editing without full file read
- Test execution (pytest) is the objective verifier for all SOTA agents (not LLM self-eval)
- Aider architect mode: plain text instructions → editor model → JSON edits
- Prompt caching: 90% cost reduction on stable system prompts (Anthropic)
- LSP diagnostics: type errors/undefined refs without running code (Claude Code LSP registry)
- SWE-bench SOTA: 79.2% (2025) via parallel sampling + test execution

## Roadmap
- P0 (7h): JSON edit format, read-before-write, multi-match detection → [spec](../specs/p0_structured_edit_spec.html)
- P1 (9h): Test execution, architect+editor split, context compaction → [spec](../specs/p1_verifier_architect_spec.html)
- P2 (11h): Prompt cache, parallel sampling, worktree isolation → [spec](../specs/p2_performance_spec.html)
- P3 (12h): LSP/ruff, auto skills, enhanced repo map → [spec](../specs/p3_advanced_spec.html)

---
title: "Research: Build Your Own Coding Agent (From Scratch)"
tags:
  - doc/research
  - topic/coding-agent
  - topic/agent-architecture
---

> **Content:** Inline summary below. Video source: https://www.youtube.com/watch?v=kU8Tg1_PYJE (39:06)

## Video Summary

**"Build Your Own Coding Agent (From Scratch)"** — a tutorial on implementing an autonomous coding agent that can:

1. **Understand the codebase** — read files, build context about project structure
2. **Formulate plans** — break tasks into steps
3. **Execute edits autonomously** — modify code based on plan
4. **Test and repair** — run tests, parse failures, retry intelligently
5. **Persist memory** — remember past context and decisions across sessions

This is the foundational architecture needed for a daily engineering loop.

## Relevance to AWOS

The AWOS MVP spec (§ "Architectural philosophy") mirrors this exactly:
- **FileAgent** = understand codebase (§2: Tree-Sitter skeletons)
- **Context Builder** = formulate plans with compressed context
- **Edit–test–repair loop** = `awos debug` command (§3: Phase 2)
- **Persistent memory** = `.awos/` directory lifecycle

**Key differentiator:** AWOS adds:
- **Token-aware context compression** (skeleton/hydrate) to keep API costs low
- **Model routing** (DeepSeek by default; Sonnet for hard problems) to minimize spend
- **Structured failure extraction** (TerminalAgent) instead of raw log parsing

## Gap Analysis

| Concept | Standard Agent | AWOS Implementation |
|---------|---|---|
| Context building | Full file dumps | Tree-Sitter skeletons + selective hydration |
| Model choice | Single tier (usually frontier) | Routed by complexity + cost tier |
| Input token cost | 100% ✓ | ~20% via skeleton reuse + prompt cache |
| Failure handling | String parsing | Language-aware extraction (LSP-like) |
| Session state | In-memory or DB | Lightweight `.awos/` files |

## Implementation Path

**Video ≈ "learn the patterns"**
**AWOS Phase 0–2 ≈ "build the system with cost awareness"**

The video's agent architecture is the **conceptual foundation**; AWOS adds the **cost optimization layer** and **production hardening**.

## Related

- [[awos_mvp]] — specification
- [[model_tiering]] — routing cost table
- [[token_efficiency]] — input/output optimization strategies

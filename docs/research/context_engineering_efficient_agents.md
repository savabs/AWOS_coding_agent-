---
title: "Context Engineering for Efficient Agents — Research Note"
tags:
  - doc/research
  - topic/context-engineering
  - topic/token-efficiency
  - topic/agent-memory
  - phase/4
---

> **Content:** [context_engineering_efficient_agents.html](context_engineering_efficient_agents.html) — open in browser.

## Key Findings

1. **Byte-capped command output** reduces token usage ~50% (agents-md pattern)
2. **Raw chunking often matches LLM-based memory** at 8.4× lower cost (Yuan et al., 2026)
3. **Deterministic CPU-first memory** can be 5×–242× cheaper than LLM-based approaches (DMF, 2026)
4. **Retrieval quality dominates write sophistication** for memory utility (3× study, MemAgents ICLR 2026)
5. **Context compression** (LCLMs) achieves 4:1–16:1 ratios with < 0.6B encoder

## Sources

| Source | Type | Key Insight |
|--------|------|-------------|
| arXiv:2606.09659 | LCLM paper | Encoder-decoder context compression for agents |
| arXiv:2603.22048 | DAIRA paper | Dynamic analysis → 25% fewer input tokens |
| arXiv:2603.03018 | REGAL paper | Registry-driven deterministic grounding |
| GitHub: Austin1serb/agents-md | Repo | Byte-capped output, context discipline (50% reduction) |
| arXiv:2603.02473 | Yuan et al. | Raw chunking matches LLM memory (MemAgents ICLR 2026) |
| arXiv:2606.03463 | DMF paper | Deterministic CPU-first memory (zero LLM tokens) |
| arXiv:2606.21144 | AdaMem | Learn what to remember, +9% accuracy, -9% volume |
| arXiv:2605.30711 | SAGE | Novelty gate, 3.4× cheaper add-phase |
| arXiv:2605.23904 | SkillOpt | Text-space optimizer, zero deployment overhead |
| arXiv:2604.04853 | MemMachine | Ground-truth preserving, ~80% fewer tokens vs Mem0 |
| arXiv:2504.19413 | Mem0 (2026) | Reference memory architecture |

## Related

- [[VISION]] — objective function (Quality × Speed ÷ Cost)
- [[AWOS]] — implementation protocols
- [[context_manager]] — existing ContextManager in scaffold/agent/
- [[session_state]] — existing session compact/dehydrate
- [[vector_memory]] — existing ChromaDB-based memory

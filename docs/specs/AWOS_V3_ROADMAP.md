# AWOS v3 Roadmap — Closing All Gaps

**Full plan:** [AWOS_V3_ROADMAP.html](AWOS_V3_ROADMAP.html)

## 8 Gaps Identified

| # | Gap | Priority | Effort | Solution |
|---|-----|----------|--------|----------|
| 1 | No persistent memory | **P0** | 4h | ChromaDB vector memory |
| 2 | Keyword-based context | **P0** | 6h | Semantic codebase index |
| 3 | Opaque reasoning | **P1** | 3h | ReAct traces |
| 4 | No self-verification | **P1** | 2h | Multi-layer verification |
| 8 | Budget resets per session | **P1** | 1h | Persistent budget ledger |
| 5 | No tool learning | **P2** | 4h | SQLite performance tracker |
| 7 | No failure recovery | **P2** | 2h | Retry with simplification |
| 6 | Sequential execution | **P3** | 8h | DAG parallel executor (deferred) |

## Timeline

- **Week 1 (14h):** Vector Memory + Semantic Index + ReAct Traces + Budget Ledger
- **Week 2 (6h):** Self-Verification + Tool Performance Tracker
- **Week 3 (2h):** Retry with Simplification + measurement
- **Week 4 (8h):** Parallel executor — **deferred** until profiling justifies

## Impact Projection

| Metric | Current | Target | Fix |
|--------|---------|--------|-----|
| Cross-session recall | 0% | 80% | Vector Memory |
| Context relevance | 60% | 90% | Semantic Index |
| Reasoning visibility | 0% | 100% | ReAct Traces |
| Syntax error rate | 15% | 5% | Self-Verification |
| Tool selection | 50% | 85% | Tool Tracker |
| First-attempt success | 70% | 80% | Retry/Simplify |

## Cost Safety

- All improvements: ~$0.0002/req extra (local embeddings, cheap Gemini checks)
- Conservative routing: **$0.81/month**
- Balanced routing: **$15.18/month**
- Quality-focused: **$43.82/month** (still 95.6% cheaper than Copilot)

## Go/No-Go Gates

1. **Week 1:** Vector Memory recalls ≥ 70% accurately
2. **Week 2:** Syntax error rate ≤ 5% on 20 tasks
3. **Week 3:** First-attempt success ≥ 80%
4. **Ongoing:** Month-to-date ≤ $20

---

**22 hours total. Say "implement week 1" to start.**

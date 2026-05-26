---
title: "Current vs Proposed Agentic Architecture"
tags:
  - doc/research
  - topic/architecture
created: 2026-05-16
---

# Agentic Architecture: Current vs Proposed

## Current Architecture (AWOS v2)

```
┌─────────────────────────────────────────────────────────────┐
│                        USER REQUEST                          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: CARTOGRAPHER (Tree-Sitter → STRUCT.xml)          │
│  • Parses codebase into AST                                 │
│  • No semantic understanding                                │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: DISPATCHER (Complexity → Model Router)            │
│  • Heuristic scoring (1-10)                                 │
│  • Routes to DeepSeek/Haiku/Sonnet                          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: HITL (Human-in-the-Loop Consent)                  │
│  • User approval gate                                       │
│  • Cost estimate                                            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: HYDRATION (Symbol Extract → Prompt Build)         │
│  • Keyword-based context retrieval ⚠️                       │
│  • No semantic search                                       │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 5: SOUL (Prompt Cache + Cost Log)                    │
│  • 15-min TTL cache ⚠️                                       │
│  • No persistent memory                                     │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR (Research → Plan → Execute → Synthesize)      │
│  • Sequential execution ⚠️                                   │
│  • No reasoning traces                                      │
│  • No self-verification                                     │
│  • No retry logic                                           │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  18 TOOLS (web, GitHub, filesystem, shell, Python, HF)      │
│  • No tool learning ⚠️                                       │
│  • No composition macros                                    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
                    RESPONSE
```

**Gaps:**
- ⚠️ Keyword-based context (misses relevant code)
- ⚠️ No persistent memory (forgets after 15min)
- ⚠️ Sequential execution (slow)
- ⚠️ No reasoning visibility
- ⚠️ No self-correction
- ⚠️ No tool learning

---

## Proposed Architecture (AWOS v3)

```
┌─────────────────────────────────────────────────────────────┐
│                        USER REQUEST                          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  VECTOR MEMORY (ChromaDB) ✨ NEW                            │
│  • Semantic search across all past interactions             │
│  • "How did I solve X before?" retrieval                    │
│  • Cross-session learning                                   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: CARTOGRAPHER + SEMANTIC INDEX ✨ ENHANCED         │
│  • Tree-Sitter AST (unchanged)                              │
│  • + Embeddings of all functions/classes                    │
│  • Incremental re-indexing on file changes                  │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: DISPATCHER (unchanged)                            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: HITL (unchanged)                                  │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: SEMANTIC HYDRATION ✨ ENHANCED                    │
│  • Vector search for relevant code (not keywords)           │
│  • Retrieve similar past solutions from memory              │
│  • Context quality: 60% → 90%                               │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 5: SOUL + PERSISTENT MEMORY ✨ ENHANCED              │
│  • Prompt cache (unchanged)                                 │
│  • + Long-term vector memory (never expires)                │
│  • + Reasoning trace archive                                │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  REACT ORCHESTRATOR ✨ NEW                                  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  THOUGHT: "I need to find API docs for X"            │  │
│  │     ↓                                                 │  │
│  │  ACTION: web_search(query="X API docs")              │  │
│  │     ↓                                                 │  │
│  │  OBSERVATION: [search results]                       │  │
│  │     ↓                                                 │  │
│  │  THOUGHT: "Result #2 looks most relevant"            │  │
│  │     ↓                                                 │  │
│  │  ACTION: fetch_url(url=result[2].url)                │  │
│  │     ↓                                                 │  │
│  │  OBSERVATION: [page content]                         │  │
│  │     ↓                                                 │  │
│  │  VERIFICATION: Does this answer the question? ✓      │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
│  Features:                                                   │
│  • Visible reasoning chain                                  │
│  • Self-verification loop                                   │
│  • Retry on failure                                         │
│  • Parallel tool execution (when independent)               │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  TOOL LEARNER ✨ NEW                                        │
│  • Tracks: which tools succeed for which task types         │
│  • Recommends tools based on past performance               │
│  • Learns compositions: "search → fetch → summarize"        │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  18 TOOLS + MACROS ✨ ENHANCED                              │
│  • Original 18 tools (unchanged)                            │
│  • + Learned macros (reusable compositions)                 │
│  • + Retry logic (exponential backoff)                      │
│  • + Fallback alternatives                                  │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  SELF-VERIFIER ✨ NEW                                       │
│  • Syntax check (code outputs)                              │
│  • Spec compliance (LLM-based)                              │
│  • Test execution (if available)                            │
│  • Auto-retry on failure                                    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
                  VERIFIED RESPONSE
```

---

## Key Improvements Summary

| Component | Before | After | Impact |
|-----------|--------|-------|--------|
| **Context Retrieval** | Keyword matching | Semantic embeddings | 60% → 90% relevance |
| **Memory** | 15min cache | Persistent vector DB | Cross-session learning |
| **Reasoning** | Opaque | ReAct traces | Debuggable + improvable |
| **Execution** | Sequential | Parallel (DAG) | 30-50% faster |
| **Tool Selection** | Random/manual | Learned from history | 50% → 85% accuracy |
| **Error Handling** | Fail-fast | Retry + fallback | 40% → 70% success rate |
| **Quality** | No verification | Self-check loop | Fewer user corrections |
| **Tool Reuse** | None | Learned macros | 3-5 calls → 1 macro |

---

## Data Flow Example: "Find best Python logging library"

### Current (AWOS v2)
```
1. User: "Find best Python logging library"
2. Dispatcher: complexity=3 → DeepSeek
3. Hydration: keyword search "logging" in codebase → finds 2 files
4. Orchestrator: Plan = [web_search]
5. Execute: web_search("best python logging library")
6. Synthesize: Return top 3 results
```

**Issues:**
- Didn't check if we already researched this
- Didn't verify if results are current (2025 vs 2020)
- No reasoning trace
- Forgot this answer after 15min

### Proposed (AWOS v3)
```
1. User: "Find best Python logging library"

2. Vector Memory: Search past interactions
   → Found: "We researched this 2 weeks ago, chose structlog"
   → Return cached answer + reasoning

   [If not found, continue:]

3. Dispatcher: complexity=3 → DeepSeek

4. Semantic Hydration:
   - Embed query
   - Find similar code (logging configs in codebase)
   - Retrieve past tool usage for "library research" tasks

5. ReAct Orchestrator:
   THOUGHT: "Need recent comparison of Python logging libs"
   ACTION: web_search(query="python logging library comparison 2025")
   OBSERVATION: [5 results]
   
   THOUGHT: "Top result is from Real Python, fetch full article"
   ACTION: fetch_url(url="realpython.com/...")
   OBSERVATION: [article content]
   
   THOUGHT: "Article recommends structlog, loguru, and stdlib"
   ACTION: hf_search_models(query="python logging")  [parallel]
   ACTION: github_search_code(query="structlog config")  [parallel]
   OBSERVATION: [model results + code examples]
   
   VERIFICATION: "Do results answer the question?" → YES

6. Self-Verifier: Check if answer is actionable → YES

7. Tool Learner: Record success
   - web_search → fetch_url → github_search_code = SUCCESS
   - Save as "research_library" macro

8. Vector Memory: Store interaction + answer
   - Embedding: "python logging library comparison"
   - Answer: "structlog (structured), loguru (simple), stdlib (builtin)"
   - Reasoning trace: [full ReAct chain]

9. Return: Verified answer + reasoning trace
```

**Improvements:**
- ✅ Checked memory first (instant answer if cached)
- ✅ Parallel tool execution (faster)
- ✅ Verified answer quality
- ✅ Learned tool composition for future
- ✅ Persistent memory (never forgets)
- ✅ Transparent reasoning

---

## Cost Analysis

### Current System
- **Per request:** $0.001 - $0.05 (model only)
- **Memory:** $0 (ephemeral cache)
- **Total:** ~$0.01/request average

### Proposed System
- **Per request:** $0.001 - $0.05 (model, unchanged)
- **Embeddings:** $0.0001/request (Gemini or local=free)
- **Vector DB:** $0 (ChromaDB is local)
- **Total:** ~$0.01/request (same!)

**ROI:** 10x capability improvement for ~0% cost increase

---

## Migration Path

### Phase 1: Drop-in Enhancements (Week 1-2)
- Add ChromaDB (no breaking changes)
- Embed codebase on startup
- Replace keyword search with semantic search
- **Impact:** Better context, no other changes

### Phase 2: Reasoning Traces (Week 3-4)
- Add ReAct loop to orchestrator
- Persist traces to `.awos/traces/`
- Add CLI viewer: `awos traces show <session_id>`
- **Impact:** Transparent reasoning, debuggable

### Phase 3: Learning (Week 5-6)
- Add tool performance tracking
- Implement macro learning
- Add self-verification
- **Impact:** Agent learns from experience

### Phase 4: Optimization (Week 7-8)
- Parallel execution
- Retry logic
- Fallback strategies
- **Impact:** Faster, more reliable

---

## Open Design Questions

1. **Embedding model choice:**
   - Local: `all-MiniLM-L6-v2` (384 dim, free, fast)
   - API: Gemini embeddings (768 dim, $0.0001/1k tokens)
   - **Recommendation:** Start local, upgrade to API if quality insufficient

2. **Vector DB:**
   - ChromaDB (persistent, easy)
   - FAISS (faster, manual persistence)
   - **Recommendation:** ChromaDB for v3.0, FAISS for v3.1 if speed needed

3. **Reasoning model:**
   - Same model for thoughts + actions (coherent)
   - Separate cheap model for thoughts (Gemini Flash)
   - **Recommendation:** Separate (Gemini Flash = $0.0001/1k vs Sonnet = $0.003/1k)

4. **Trace storage:**
   - JSON files (simple, grep-able)
   - SQLite (queryable)
   - **Recommendation:** JSON for v3.0, SQLite for v3.1 if analytics needed

---

**Next:** Review `AGENTIC_AMPLIFICATION_SPEC.md` for implementation details

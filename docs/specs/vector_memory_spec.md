# Spec: Vector Memory (Phase 6)

> Canonical HTML: docs/specs/vector_memory_spec.html
> This file is a navigation stub for Obsidian only.

## Goal
Add ChromaDB + all-MiniLM-L6-v2 semantic retrieval to AWOS for:
1. Codebase chunk injection into Worker prompts
2. Cross-session outcome memory
3. Goal deduplication (cosine ≥ 0.82 replaces Jaccard in ProjectPlanner)

## New Files
- scaffold/agent/vector_memory.py — VectorMemory, CodeChunk, OutcomeRecord
- tests/test_vector_memory.py — 25+ tests

## Modified Files
- scaffold/agent/orchestrator.py — init + wire index_codebase, query_code, store_outcome
- scaffold/agent/worker.py — vector_chunks param + [RELEVANT CODE] prompt injection
- scaffold/agent/project_planner.py — find_similar_goal() before Jaccard fallback
- requirements.txt — chromadb>=0.6.0, sentence-transformers>=3.0.0

## Key Constants
- GOAL_SIM_THRESHOLD = 0.82
- TOP_K_CODE = 5 (chunks in prompt)
- MAX_CHUNK_TOKENS = 256 (MiniLM limit)
- MAX_SESSION_DOCS = 10,000

## Steps (13 atomic)
- [ ] 1. Create vector_memory.py — error class + dataclasses + __init__
- [ ] 2. Implement index_codebase() — AST chunking + mtime cache + upsert
- [ ] 3. Implement query_code() — embed + cosine search + return CodeChunks
- [ ] 4. Implement store_outcome() + query_outcomes()
- [ ] 5. Implement find_similar_goal() + reset_codebase_index()
- [ ] 6. Add deps to requirements.txt
- [ ] 7. Wire VectorMemory into Orchestrator.__init__ + execute_feature()
- [ ] 8. Wire query_code() into _execute_single_task() → pass to worker
- [ ] 9. Add vector_chunks param to Worker.execute_task() + inject [RELEVANT CODE]
- [ ] 10. Wire store_outcome() post-task in _execute_single_task()
- [ ] 11. Wire find_similar_goal() into ProjectPlanner.load_or_create_goal()
- [ ] 12. Write tests/test_vector_memory.py (25+ tests)
- [ ] 13. pytest tests/ → 240+ passed, 0 failed

## Links
- Research: [[vector_memory_research]]
- Related: [[project_planner_spec]]

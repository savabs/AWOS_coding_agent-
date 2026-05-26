# Task: Vector Memory (Phase 6)

> Canonical HTML: tasks/active/vector_memory_task.html
> This file is a navigation stub for Obsidian only.

## Goal
Add ChromaDB + all-MiniLM-L6-v2 semantic retrieval to AWOS.

## Steps (13 atomic)
- [ ] 1. Create vector_memory.py — VectorMemoryUnavailableError, CodeChunk, OutcomeRecord, __init__
- [ ] 2. Implement index_codebase() — AST chunking + mtime cache + ChromaDB upsert
- [ ] 3. Implement query_code() — embed + cosine search + return CodeChunks
- [ ] 4. Implement store_outcome() + query_outcomes()
- [ ] 5. Implement find_similar_goal() + reset_codebase_index()
- [ ] 6. Add chromadb>=0.6.0, sentence-transformers>=3.0.0 to requirements.txt
- [ ] 7. Wire VectorMemory into Orchestrator.__init__ + execute_feature()
- [ ] 8. Wire query_code() into _execute_single_task() → pass to worker
- [ ] 9. Add vector_chunks param to Worker.execute_task() + [RELEVANT CODE] injection
- [ ] 10. Wire store_outcome() post-task in _execute_single_task()
- [ ] 11. Wire find_similar_goal() into ProjectPlanner.load_or_create_goal()
- [ ] 12. Write tests/test_vector_memory.py (25+ tests)
- [ ] 13. pytest tests/ → 240+ passed, 0 failed

## Links
- Spec: [[vector_memory_spec]]
- Research: [[vector_memory_research]]

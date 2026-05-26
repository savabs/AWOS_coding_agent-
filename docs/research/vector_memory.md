# Research: Vector Memory — AWOS Phase 6

> Canonical HTML: docs/research/vector_memory.html
> This file is a navigation stub for Obsidian only.

## Summary
Dense vector retrieval (ChromaDB + all-MiniLM-L6-v2) to replace keyword matching for:
1. Codebase semantic search — inject relevant code chunks into Worker prompt
2. Session memory — cross-session outcome recall
3. Goal matching — cosine similarity replaces Jaccard in ProjectPlanner

## Key Findings (VERIFIED)
- Dense embedding retrieval outperforms sparse/BM25 for repo-level code (arXiv:2510.04905)
- all-MiniLM-L6-v2: 384-dim, 256 token max, ChromaDB default, local/free
- ChromaDB PersistentClient stores to disk, supports SentenceTransformerEmbeddingFunction
- Cursor uses file_path + start/end line metadata as standard chunk schema

## Links
- Spec: [[vector_memory_spec]]
- Related: [[project_planner_research]] (Jaccard matching to be upgraded)

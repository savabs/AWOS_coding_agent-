---
title: "Multi-Resolution Context Pipeline — Research"
tags:
  - doc/research
  - topic/context-engineering
  - topic/multi-resolution
  - topic/semantic-retrieval
  - phase/4
---

> **Content:** [multi_resolution_context_research.html](multi_resolution_context_research.html) — open in browser.

# Multi-Resolution Context Pipeline for AWOS — Research

## 1. Problem Statement

**Current state:** When the AWOS worker executes a task, it receives:
- Full file content (truncated to `MAX_CONTEXT_TOKENS_BLOCK_B`)
- RAG context from `codebase_index.query_code()`
- Cross-file context from `symbol_index`
- Skill context from `SkillLibrary`
- Error context from past failures
- Vector chunks from `VectorMemory.query_code()`

**The problem:** This assembly is **flat and non-adaptive**. Every task gets roughly the same amount of context regardless of:
- Task complexity (a typo fix doesn't need 5 code chunks)
- Task type (test-writing needs different context than refactoring)
- Whether the same file was recently worked on

**Cost model:** The worker sends ALL of this to the LLM every time. A simple task costs the same in context tokens as a complex architectural change. There is no "try cheaper first" path.

## 2. Existing Infrastructure (What We Can Build On)

| Component | File | What it does | Reuse? |
|-----------|------|-------------|--------|
| `VectorMemory` | `vector_memory.py` | ChromaDB + fastembed. `query_code()` returns top-k code chunks via cosine similarity. `index_codebase()` pre-computes embeddings for all `.py` files. `EMBEDDING_MODEL = all-MiniLM-L6-v2` (384-dim, local, zero API cost). | ✅ Core foundation |
| `SymbolExtractor` | `symbol_extractor.py` | Extracts function/class/module symbols from `STRUCT.xml`. Keyword-based matching via `_extract_keywords()` and `_score_relevance()`. Returns `Symbol` objects with name, type, file, line, snippet. | ✅ For Level 2 signatures |
| `PromptHydrator` | `prompt_hydrator.py` | Assembles full prompt from task + symbols. Token-budget aware (`max_tokens // 2` for context). Produces `HydratedPrompt` with token estimate. | ✅ Pattern to extend |
| `ContextManager` | `context_manager.py` | Budget-tracking context blocks with priority-based dehydration. `emergency_dehydrate()` at 80% threshold. | ✅ For enforcement |
| `SessionState` | `session_state.py` | Compacts completed tasks into summary at `MAX_CONTEXT_TOKENS=800` threshold. | ✅ For Level 4 outcomes |
| `ErrorPatternStore` | `error_pattern_store.py` | Append-only JSONL of failure patterns. Retrievable by (file, error_type). | ✅ For Level 4 errors |
| `SkillLibrary` | `skill_library.py` | Success patterns with win rates. `get_skill_context(task)` for relevant patterns. | ✅ For Level 4 skills |

## 3. Design: Multi-Resolution Context Pipeline

### 3.1 Architecture

```
Task: "fix JWT expiry validation in auth middleware"
      │
      ▼
┌─────────────────────────────────────────────────────┐
│  MultiResolutionContextPipeline.build(task, level)  │
│                                                     │
│  Level 0 ─ task description only                    │
│  │          ~40 tokens                              │
│  ├─ Level 1 ─ + module overview (pre-computed)      │
│  │          ~100 tokens                             │
│  ├─ Level 2 ─ + function signatures (SymbolExtract) │
│  │          ~300 tokens                             │
│  ├─ Level 3 ─ + semantic code chunks (VectorMemory) │
│  │          ~1,200 tokens                           │
│  └─ Level 4 ─ + outcomes + errors + skills          │
│             ~1,800 tokens                           │
└─────────────────────────────────────────────────────┘
      │
      ▼
  To Worker → Model Router (cheaper tier for lower levels)
```

### 3.2 Each Level in Detail

**Level 0 — Task Only**
```python
{
    "task": "fix JWT expiry validation in auth middleware",
    "file": "auth/middleware.py",
    "context_tokens": 40
}
```
- No file read, no codebase search
- Use when: simple changes with clear instructions, retry of same file

**Level 1 — Module Overview**
```python
# Pre-computed during index_codebase()
{
    "module": "auth/middleware.py",
    "summary": "JWT authentication middleware with token validation, role checking, and session renewal",
    "imports": ["jwt", "datetime", "functools"],
    "exports": ["authenticate()", "require_role()", "refresh_token()"],
    "dependencies": ["auth/utils.py", "config/settings.py"],
    "context_tokens": 100
}
```
- Built once when indexing, cached, never re-read
- Use when: the agent needs orientation but not full code

**Level 2 — Function Signatures**
```python
# From SymbolExtractor (already available via STRUCT.xml)
{
    "functions": [
        {"name": "authenticate", "sig": "def authenticate(token: str) -> dict:", "line": 42},
        {"name": "require_role", "sig": "def require_role(role: str):", "line": 78},
    ],
    "classes": [
        {"name": "AuthMiddleware", "methods": ["process_request", "process_response"]},
    ],
    "context_tokens": 300
}
```
- Use when: task mentions specific functions/classes
- Built from STRUCT.xml or AST parsing

**Level 3 — Semantic Code Chunks**
```python
# From VectorMemory.query_code(task_action)
{
    "chunks": [
        {"text": "def authenticate(token):\n    payload = jwt.decode(...)", "score": 0.92},
        {"text": "if payload['exp'] < datetime.utcnow():\n    raise ExpiredError", "score": 0.87},
        {"text": "class ExpiredError(AuthError):\n    ...", "score": 0.81},
    ],
    "context_tokens": 1200
}
```
- Semantic search over pre-computed embeddings
- Use when: meaningful code reading is needed
- This already exists in `VectorMemory.query_code()` — we're making it a level in the pipeline

**Level 4 — Historical Context**
```python
{
    "past_outcomes": [...],    # from VectorMemory.query_outcomes()
    "error_patterns": [...],   # from ErrorPatternStore.retrieve_any()
    "related_skills": [...],   # from SkillLibrary.get_skill_context()
    "context_tokens": 1800
}
```
- Use when: task is complex, has history of failures, or needs cross-session recall

### 3.3 SemanticRetriever (New Module)

A thin wrapper over VectorMemory that provides a unified interface:

```python
class SemanticRetriever:
    """Unified semantic search over codebase, with caching and fallback."""
    
    def __init__(self, vector_memory, symbol_extractor, cache_dir):
        self.vm = vector_memory
        self.se = symbol_extractor
        self.cache = {}  # {task_hash: [CodeChunk]}
    
    def retrieve(self, task: str, top_k: int = 3) -> list[CodeChunk]:
        """Returns ranked code chunks for a task."""
        key = hashlib.sha256(task.encode()).hexdigest()[:16]
        if key in self.cache:
            return self.cache[key]
        
        # Try semantic search first
        if self.vm:
            chunks = self.vm.query_code(task, top_k=top_k)
            if chunks:
                self.cache[key] = chunks
                return chunks
        
        # Fallback: symbol keyword matching
        symbols = self.se.extract_symbols(task, limit=top_k * 2)
        if not symbols:
            return []
        
        chunks = []
        for sym in symbols[:top_k]:
            try:
                code = self.se.get_symbol_code(sym)
                chunks.append(CodeChunk(
                    chunk_id=f"sym/{sym.name}",
                    file_path=sym.file,
                    start_line=sym.line,
                    end_line=sym.line + sym.size,
                    text=code or sym.snippet,
                    symbol_name=sym.name,
                ))
            except Exception:
                pass
        self.cache[key] = chunks
        return chunks
    
    def preload(self, repo_root: str) -> int:
        """Build the semantic index for an entire repo. Returns chunk count."""
        return self.vm.index_codebase(repo_root) if self.vm else 0
```

### 3.4 ContextStrategyBandit (Next Sprint)

Extension of existing `ml_router.py` LinUCB:
```python
# Arms = context levels
context_bandit = LinUCB([
    {"name": "level_0", "features": ["simple", "typofix"]},
    {"name": "level_1", "features": ["module_orientation"]},
    {"name": "level_2", "features": ["signature_needed"]},
    {"name": "level_3", "features": ["code_body_needed"]},
    {"name": "level_4", "features": ["historical_context"]},
])
# Reward = success * quality / tokens_used
```

## 4. What We're Building Now (MVP)

### Phase 1: SemanticRetriever (this sprint)
- Wraps VectorMemory with caching + fallback
- `retrieve(task, top_k)` → `list[CodeChunk]`
- `preload(repo_root)` → builds entire index
- Unit tests with mock VectorMemory

### Phase 2: MultiResolutionContextPipeline (this sprint)
- `build(task, level=3)` → `ContextAssembly`
- Levels 0–3 with clear token budgets
- Integration point in `worker.py` → `execute_task()`
- Unit tests per level

### Phase 3: Integration + Live Proof (this sprint)
- Wire into orchestrator's task execution loop
- Token counting + comparison (with vs without pipeline)
- Live proof: run a coding task, show token savings

### Deferred to next sprint:
- ContextStrategyBandit (LinUCB for level selection)
- Level 4 (historical outcomes/errors/skills)
- Cross-task context reuse within session

## 5. Benchmarks (How We'll Measure Success)

**Primary metric:** `context_tokens / task_success` — lower is better

**Test scenarios:**
1. **Simple typo fix:** Level 0 should work (0 codebase tokens)
2. **Function modification:** Level 2 should work (signatures only)
3. **New feature:** Level 3 needed (semantic chunks)
4. **Complex refactor:** Level 4 needed (historical context)

**Success criteria:**
- Level 0 resolves 20%+ of tasks (typos, simple renames)
- Level 2 resolves 50%+ (clear function-level changes)
- Level 3 resolves 80%+ (most coding tasks)
- Average tokens per task drops by 40%+ vs current full-context approach

## 6. Related

- [[context_engineering_efficient_agents]] — broader SOTA research
- [[multi_resolution_context_spec]] — implementation spec
- [[VISION]] — Quality × Speed ÷ Cost
- [[vector_memory]] — existing ChromaDB-based semantic search
- [[prompt_hydrator]] — existing context assembly
- [[context_manager]] — existing budget management

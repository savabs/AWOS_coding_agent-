---
title: "Multi-Resolution Context Pipeline — Spec"
tags:
  - doc/spec
  - topic/multi-resolution
  - topic/semantic-retrieval
  - phase/4
---

> **Content:** [multi_resolution_context_spec.html](multi_resolution_context_spec.html) — open in browser.

# Multi-Resolution Context Pipeline — Spec

## Goal

Build a **tiered context assembly system** that gives the AWOS worker only the context it actually needs for each task, defaulting to cheaper levels and escalating only when they fail — reducing average context tokens per task by 40%+ without degrading task success rate.

## Files Affected

### New Files
| File | Purpose |
|------|---------|
| `scaffold/agent/semantic_retriever.py` | Thin wrapper over VectorMemory + SymbolExtractor fallback |
| `scaffold/agent/multi_resolution_context.py` | 4-level context assembly pipeline |
| `tests/test_semantic_retriever.py` | Unit tests for SemanticRetriever |
| `tests/test_multi_resolution_context.py` | Unit tests for pipeline |
| `scripts/demo_multi_resolution_context.sh` | Live proof demo |
| `docs/multi_resolution_context_proof.md` | Proof guide |

### Modified Files
| File | Change |
|------|--------|
| `scaffold/agent/worker.py` | Accept `context_level` param in `execute_task()` |
| `scaffold/agent/orchestrator.py` | Call pipeline before worker, set context level |

### Not Modified (Read Only)
| File | Why |
|------|-----|
| `vector_memory.py` | Used via existing API — no changes needed |
| `symbol_extractor.py` | Used via existing API — no changes needed |
| `prompt_hydrator.py` | Pattern reference only |

---

## Implementation Steps

### Step 1: SemanticRetriever — skeleton + retrieve()

**1.1** Create `scaffold/agent/semantic_retriever.py` with:
- `SemanticRetriever` class
- `__init__(self, vector_memory=None, symbol_extractor=None)`
- `retrieve(self, task_description: str, top_k: int = 3) -> list[dict]` — returns list of `{file_path, start_line, end_line, text, symbol_name, score}` dicts
- In-memory cache: `{hash(task_desc): list[dict]}` — 100 entry LRU
- Token counting helper: `_estimate_tokens(text: str) -> int` — uses `len(text) // 4`

**Validation check:** `python -c "from scaffold.agent.semantic_retriever import SemanticRetriever; sr = SemanticRetriever(); print(type(sr))"`

**Edge cases:**
- VectorMemory unavailable → fallback to SymbolExtractor
- VectorMemory available but returns empty → fallback to SymbolExtractor
- Both unavailable → return empty list
- Task is empty string → return empty list

---

### Step 1.2: SemanticRetriever — fallback path

When VectorMemory is None or returns empty:
- Use `symbol_extractor.extract_symbols(task_description, limit=top_k)` if available
- Convert each `Symbol` to a dict: `{file_path, line: start_line, line+snippet_lines: end_line, text: snippet, symbol_name: name, score: 1.0}`
- If SymbolExtractor also unavailable → return empty list

---

### Step 1.3: SemanticRetriever — preload()

- `preload(self, repo_root: str) -> int` → delegate to `vector_memory.index_codebase(repo_root)`
- Return chunk count, or 0 if VectorMemory unavailable
- Log: `logger.info("[SemanticRetriever] preloaded %d chunks from %s", count, repo_root)`

---

### Step 1.4: Unit tests — test_semantic_retriever.py

```python
# Test cases:
# 1. retrieve() with mock VectorMemory returns results
# 2. retrieve() with None VectorMemory falls back to SymbolExtractor
# 3. retrieve() with both None returns empty list
# 4. retrieve() with empty task returns empty list
# 5. Cache: same task returns cached result (no second query)
# 6. preload() delegates to VectorMemory.index_codebase()
# 7. _estimate_tokens() returns correct estimates
```

**Run:** `pytest tests/test_semantic_retriever.py -q --timeout=10`

---

### Step 2: MultiResolutionContextPipeline — dataclasses + skeleton

**2.1** Create `scaffold/agent/multi_resolution_context.py` with:

```python
@dataclass
class ContextAssembly:
    """Result of context assembly at a given level."""
    level: int
    task_section: str           # always: the task description
    module_section: str         # Level 1+: module overview
    signature_section: str      # Level 2+: function signatures
    chunk_section: str          # Level 3+: semantic code chunks
    history_section: str        # Level 4+: outcomes + errors
    full_context: str           # assembled prompt section
    estimated_tokens: int       # rough token count
    chunks_used: list[dict]     # for observability

@dataclass 
class ModuleOverview:
    """Pre-computed overview of a source file."""
    file_path: str
    summary: str           # single sentence description
    imports: list[str]
    top_level_symbols: list[str]  # function/class names
    dependencies: list[str]       # other files this imports
```

**Validation check:** `python -c "from scaffold.agent.multi_resolution_context import ContextAssembly, MultiResolutionContextPipeline; print('OK')"`

---

### Step 2.2: MultiResolutionContextPipeline — build()

```python
class MultiResolutionContextPipeline:
    def __init__(self, semantic_retriever=None, symbol_extractor=None, 
                 error_pattern_store=None, project_root=None):
        ...

    def build(self, task: dict, level: int = 3, file_content: str = "") -> ContextAssembly:
        """Build context for a task at the specified level."""
```

**Edge cases:**
- level < 0 → clamp to 0
- level > 4 → clamp to 4
- task dict missing "action" key → use empty string
- file_content is empty at Level 3+ → chunks only (no file content)

---

### Step 2.3: MultiResolutionContextPipeline — Level 0 (Task Only)

```python
def _build_level_0(self, task: dict) -> ContextAssembly:
    task_section = f"TASK: {task.get('action', '')}"
    return ContextAssembly(
        level=0,
        task_section=task_section,
        full_context=task_section,
        estimated_tokens=len(task_section) // 4,
    )
```

No file reads. No codebase search. Just the task.

---

### Step 2.4: MultiResolutionContextPipeline — Level 1 (+ Module Overview)

```python
def _build_level_1(self, task: dict, assembly: ContextAssembly) -> ContextAssembly:
    # Get file info from task
    file_path = task.get("file", "")
    overview = self._get_module_overview(file_path)
    if overview:
        assembly.module_section = f"MODULE: {overview.summary}\nEXPORTS: {', '.join(overview.top_level_symbols)}"
    # Rebuild full_context
    assembly.full_context = f"{assembly.task_section}\n\n{assembly.module_section}"
    assembly.estimated_tokens = len(assembly.full_context) // 4
    assembly.level = 1
    return assembly
```

`_get_module_overview()` → reads the first 20 lines of the file + import statements + top-level `def`/`class` names via regex. Builds a `ModuleOverview`. Results are cached per file_path.

**This is deterministic — no LLM, no embeddings, just regex.**

---

### Step 2.5: MultiResolutionContextPipeline — Level 2 (+ Function Signatures)

```python
def _build_level_2(self, task: dict, assembly: ContextAssembly) -> ContextAssembly:
    if self.symbol_extractor:
        symbols = self.symbol_extractor.extract_symbols(task.get("action", ""), limit=5)
        if symbols:
            lines = ["RELEVANT SYMBOLS:"]
            for s in symbols:
                lines.append(f"  {s.type} {s.name}(...) — {s.file}:{s.line}")
            assembly.signature_section = "\n".join(lines)
    # Rebuild
    assembly.full_context = "\n\n".join(filter(None, [
        assembly.task_section, assembly.module_section, assembly.signature_section
    ]))
    assembly.estimated_tokens = len(assembly.full_context) // 4
    assembly.level = 2
    return assembly
```

---

### Step 2.6: MultiResolutionContextPipeline — Level 3 (+ Semantic Chunks)

```python
def _build_level_3(self, task: dict, assembly: ContextAssembly, file_content: str = "") -> ContextAssembly:
    if self.semantic_retriever:
        chunks = self.semantic_retriever.retrieve(task.get("action", ""), top_k=3)
        if chunks:
            lines = ["RELEVANT CODE:"]
            for c in chunks:
                lines.append(f"-- {c['file_path']}:{c['start_line']} ({c.get('symbol_name','')})")
                # Truncate each chunk to max 400 chars
                text = c['text'][:400]
                lines.append(text)
                lines.append("")
            assembly.chunk_section = "\n".join(lines)
            assembly.chunks_used = chunks

    # Add file content if provided
    file_section = ""
    if file_content:
        truncated = file_content[:2000]
        file_section = f"FILE: {task.get('file', 'unknown')}\n{truncated}"

    assembly.full_context = "\n\n".join(filter(None, [
        assembly.task_section, assembly.module_section, 
        assembly.signature_section, assembly.chunk_section, file_section
    ]))
    assembly.estimated_tokens = len(assembly.full_context) // 4
    assembly.level = 3
    return assembly
```

---

### Step 2.7: Unit tests — test_multi_resolution_context.py

```python
# Test cases:
# 1. build() with level=0 returns task-only context
# 2. build() with level=1 includes module overview
# 3. build() with level=2 includes signatures
# 4. build() with level=3 includes chunks + file content
# 5. level=4 includes historical context (when stores added)
# 6. level < 0 clamps to 0
# 7. level > 4 clamps to 4  
# 8. missing "action" key → empty task section
# 9. estimated_tokens increases with level
# 10. ContextAssembly fields match the level

# Use mock SemanticRetriever, mock SymbolExtractor
```

**Run:** `pytest tests/test_multi_resolution_context.py -q --timeout=10`

---

### Step 3: Integration — Worker

**3.1** Add `context_level` parameter to `worker.execute_task()`:
```python
def execute_task(
    self, task, file_content, codebase_context,
    context_level: int = 3,      # NEW: 0-4
    context_assembly=None,        # NEW: pre-built ContextAssembly
    ...
    vector_chunks=None,           # KEPT for backward compat
    ...
) -> dict:
```

**3.2** When `context_assembly` is provided, use it instead of building context from scratch:
```python
if context_assembly is not None:
    # Use pre-built multi-resolution context
    context_snippet = context_assembly.full_context
else:
    # Fallback: existing context building (backward compatible)
    context_snippet = self._build_file_and_rag_context(file_content, task, codebase_index)
```

**Validation:** Existing worker tests still pass. `pytest tests/test_worker.py -q --timeout=10`

**Edge cases:**
- `context_assembly` is None → backward-compatible fallback
- `context_assembly.full_context` is empty → still works (just the task prompt)

---

### Step 3.3: Integration — Orchestrator

In `orchestrator._execute_single_task()`, before calling the worker:

```python
# Build multi-resolution context
if self.context_pipeline is not None:
    assembly = self.context_pipeline.build(
        task=task, 
        level=self._suggest_context_level(task),
        file_content=file_content,
    )
    _context_assembly = assembly
    logger.info("[MultiResContext] Level %d — %d tokens", assembly.level, assembly.estimated_tokens)
else:
    _context_assembly = None
```

Initialize `self.context_pipeline` during orchestrator `__init__`:
```python
if self.vector_memory is not None:
    from .semantic_retriever import SemanticRetriever
    from .multi_resolution_context import MultiResolutionContextPipeline
    self.semantic_retriever = SemanticRetriever(
        vector_memory=self.vector_memory,
        symbol_extractor=self.symbol_extractor,
    )
    self.context_pipeline = MultiResolutionContextPipeline(
        semantic_retriever=self.semantic_retriever,
        symbol_extractor=self.symbol_extractor,
        error_pattern_store=self.error_pattern_store,
    )
else:
    self.context_pipeline = None  # graceful degradation
```

`suggest_context_level(task)` — simple heuristic:
```python
def _suggest_context_level(self, task: dict) -> int:
    """Suggest a context level based on task complexity hints."""
    complexity = task.get("complexity", "medium")
    action = task.get("action", "").lower()
    
    # Pattern matching
    if any(kw in action for kw in ["typo", "rename", "variable", "import"]):
        return 0
    if complexity == "low":
        return 1
    if complexity == "medium":
        return 2
    return 3  # default: semantic chunks
```

---

### Step 4: Live Proof

**4.1** Create `scripts/demo_multi_resolution_context.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== Multi-Resolution Context Pipeline — Live Proof ==="
echo ""

# Step 1: Index the codebase
echo "[1/4] Indexing codebase with VectorMemory..."
python -c "
from scaffold.agent.vector_memory import VectorMemory
vm = VectorMemory()
n = vm.index_codebase('scaffold/agent')
print(f'  Indexed {n} chunks')
"

# Step 2: Build context at each level
echo ""
echo "[2/4] Building context at each level..."
python -c "
from scaffold.agent.vector_memory import VectorMemory
from scaffold.agent.semantic_retriever import SemanticRetriever
from scaffold.agent.multi_resolution_context import MultiResolutionContextPipeline

vm = VectorMemory()
sr = SemanticRetriever(vector_memory=vm)
pipeline = MultiResolutionContextPipeline(semantic_retriever=sr)

task = {'file': 'worker.py', 'action': 'add error handling to execute_task method', 'complexity': 'medium'}

for level in range(4):
    assembly = pipeline.build(task, level=level)
    print(f'Level {level}: {assembly.estimated_tokens} tokens')
"

# Step 3: Show token savings
echo ""
echo "[3/4] Token comparison vs full context..."
python -c "
# Simulate full context: reading worker.py (1369 lines ~ 40KB)
full_file_tokens = 40000 // 4  # ~10,000 tokens
from scaffold.agent.vector_memory import VectorMemory
from scaffold.agent.semantic_retriever import SemanticRetriever
from scaffold.agent.multi_resolution_context import MultiResolutionContextPipeline

vm = VectorMemory()
sr = SemanticRetriever(vector_memory=vm)
pipeline = MultiResolutionContextPipeline(semantic_retriever=sr)

task = {'file': 'worker.py', 'action': 'add error handling to execute_task method', 'complexity': 'medium'}
assembly = pipeline.build(task, level=3)
saved = full_file_tokens - assembly.estimated_tokens
pct = int(saved / full_file_tokens * 100)
print(f'Full file context:   ~{full_file_tokens} tokens')
print(f'Level 3 context:     ~{assembly.estimated_tokens} tokens')
print(f'Saved:               ~{saved} tokens ({pct}%)')
"

echo ""
echo "[4/4] === [OK] Multi-Resolution Context Pipeline — Live Proof Complete ==="
```

**Observable markers:**
- Line: "Indexed N chunks" — N > 0
- Line: "Level 0: X tokens" — X < 50
- Line: "Level 1: X tokens" — X > Level 0
- Line: "Level 2: X tokens" — X > Level 1
- Line: "Level 3: X tokens" — X > Level 2
- Line: "Saved: ~N tokens (P%)" — P > 60

**4.2** Create `docs/multi_resolution_context_proof.md` — proof guide

---

## Testing Plan

### Unit Tests
| Test file | Count | Run |
|-----------|-------|-----|
| `test_semantic_retriever.py` | 7 tests | `pytest tests/test_semantic_retriever.py -q --timeout=10` |
| `test_multi_resolution_context.py` | 10 tests | `pytest tests/test_multi_resolution_context.py -q --timeout=10` |
| `test_worker.py` (existing) | Regression | `pytest tests/test_worker.py -q --timeout=10` |

### Integration Test
- Run orchestrator with multi-resolution context enabled
- Verify context_level is passed to worker
- Verify graceful degradation when pipeline is None

### Live Proof
- `bash scripts/demo_multi_resolution_context.sh`
- 5 observable markers

---

## Deferred (Next Sprint)

- `ContextStrategyBandit` — LinUCB for level selection (replaces `_suggest_context_level`)
- Level 4: historical outcomes + error patterns + skills
- Cross-task context reuse within session
- Token tracking per level for PEI reporting

---

## Related

- [[multi_resolution_context_research]] — research phase
- [[multi_resolution_context_task]] — task file
- [[multi_resolution_context_proof]] — live proof guide
- [[VISION]] — Quality × Speed ÷ Cost
- [[vector_memory]] — existing ChromaDB infrastructure

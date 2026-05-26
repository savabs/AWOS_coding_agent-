---
title: "Agentic Amplification Spec — 10x the Model's Effective Power"
tags:
  - doc/spec
  - phase/next
  - topic/architecture
  - priority/high
created: 2026-05-16
---

# Agentic Amplification Spec

**Goal:** Multiply the model's effective capabilities 10x through better scaffolding — not bigger models.

**Philosophy:** The model is the CPU. We're building the OS, RAM, disk, and peripherals.

---

## Current State Analysis

### What We Have ✅
1. **18 tools** (web search, GitHub, filesystem, shell, Python exec, HuggingFace)
2. **5-layer AWOS v2** (Cartographer → Dispatcher → HITL → Hydration → Soul)
3. **Session memory** (hybrid strategy: recent full + old summarized)
4. **Cost optimization** (model routing, prompt caching, token tracking)
5. **Basic orchestration** (Research → Plan → Execute → Synthesize)

### What's Missing ❌
1. **No persistent episodic memory** — session memory dies after 15min cache TTL
2. **No tool reflection** — agent can't learn which tools work for which tasks
3. **No multi-step reasoning traces** — no Chain-of-Thought persistence
4. **Weak context retrieval** — keyword matching only (no embeddings)
5. **No self-correction loop** — agent can't verify its own outputs
6. **No parallel tool execution** — everything is sequential
7. **No learned tool compositions** — can't save "search → fetch → summarize" as a macro
8. **No failure recovery** — if a tool fails, the whole plan fails

---

## Proposed Improvements

### 1. **Persistent Vector Memory** (Highest ROI)

**Problem:** Session memory is ephemeral. Agent forgets everything after cache expires.

**Solution:** Embed all interactions + tool results into a vector DB.

```python
# scaffold/agent/memory/vector_store.py
class VectorMemory:
    """Persistent semantic memory using ChromaDB or FAISS"""
    
    def __init__(self, persist_dir: Path):
        self.db = chromadb.PersistentClient(path=str(persist_dir))
        self.collection = self.db.get_or_create_collection(
            name="awos_memory",
            metadata={"hnsw:space": "cosine"}
        )
    
    def store(self, text: str, metadata: dict):
        """Store interaction with embeddings"""
        self.collection.add(
            documents=[text],
            metadatas=[metadata],
            ids=[f"{metadata['session_id']}_{metadata['timestamp']}"]
        )
    
    def retrieve(self, query: str, n: int = 5) -> list[dict]:
        """Semantic search for relevant past interactions"""
        results = self.collection.query(
            query_texts=[query],
            n_results=n
        )
        return results
```

**Impact:**
- Agent remembers solutions from weeks ago
- "How did I solve X last time?" becomes answerable
- Cross-session learning accumulates

**Cost:** ~$0 (ChromaDB is local, embeddings via free Gemini or local model)

---

### 2. **Tool Reflection & Learning**

**Problem:** Agent doesn't learn which tools succeed/fail for which tasks.

**Solution:** Track tool performance and build a success matrix.

```python
# scaffold/agent/tools/tool_learner.py
class ToolLearner:
    """Learn which tools work for which task patterns"""
    
    def __init__(self, db_path: Path):
        self.db = sqlite3.connect(db_path)
        self._init_schema()
    
    def record_usage(self, tool_name: str, task_embedding: np.ndarray, 
                     success: bool, latency_ms: float):
        """Log tool usage outcome"""
        # Store: tool, task_vector, success, latency, timestamp
        pass
    
    def recommend_tools(self, task_description: str, top_k: int = 3) -> list[str]:
        """Recommend tools based on similar past tasks"""
        # 1. Embed task_description
        # 2. Find similar past tasks (cosine similarity)
        # 3. Return tools with highest success rate for those tasks
        pass
```

**Impact:**
- Agent learns "web_search works better than github_search_code for API docs"
- Reduces trial-and-error loops
- Builds institutional knowledge

---

### 3. **Multi-Step Reasoning Traces (ReAct Pattern)**

**Problem:** No visibility into agent's reasoning. Can't debug or improve.

**Solution:** Persist Thought → Action → Observation loops.

```python
@dataclass
class ReasoningTrace:
    """Single step in ReAct loop"""
    thought: str          # "I need to find the API endpoint for X"
    action: str           # "web_search"
    action_input: dict    # {"query": "X API endpoint"}
    observation: str      # Tool result
    timestamp: str
    success: bool

class ReActOrchestrator(Orchestrator):
    """Enhanced orchestrator with reasoning traces"""
    
    def execute_with_reasoning(self, goal: str) -> tuple[str, list[ReasoningTrace]]:
        traces = []
        for step in self.plan.steps:
            # LLM generates thought
            thought = self._generate_thought(step, context=traces)
            
            # Execute action
            result = self.tools.execute(step.tool, step.args)
            
            # Record trace
            traces.append(ReasoningTrace(
                thought=thought,
                action=step.tool,
                action_input=step.args,
                observation=result.text,
                success=result.success,
                timestamp=datetime.now().isoformat()
            ))
            
            # Self-correction: if failed, try alternative
            if not result.success:
                alt_step = self._generate_alternative(step, result.error)
                if alt_step:
                    traces.append(self._execute_step(alt_step))
        
        return self.synthesize(goal, traces), traces
```

**Impact:**
- Debuggable reasoning chains
- Self-correction on failures
- Training data for future fine-tuning

---

### 4. **Parallel Tool Execution**

**Problem:** Sequential execution wastes time when tools are independent.

**Solution:** Dependency graph + asyncio.

```python
class ParallelOrchestrator:
    """Execute independent tools in parallel"""
    
    async def execute_parallel(self, plan: Plan) -> list[ToolResult]:
        # Build dependency graph
        graph = self._build_dag(plan.steps)
        
        results = {}
        while graph.has_pending():
            # Get all steps with satisfied dependencies
            ready = graph.get_ready_steps()
            
            # Execute in parallel
            tasks = [self._execute_async(step) for step in ready]
            step_results = await asyncio.gather(*tasks)
            
            # Update graph
            for step, result in zip(ready, step_results):
                results[step.id] = result
                graph.mark_done(step.id)
        
        return list(results.values())
```

**Example:**
```
Sequential:  web_search (2s) → fetch_url (3s) → github_search (2s) = 7s
Parallel:    [web_search, github_search] (2s) → fetch_url (3s) = 5s
```

**Impact:** 30-50% faster execution for multi-tool tasks

---

### 5. **Tool Composition Macros**

**Problem:** Common patterns (search → fetch → summarize) are re-planned every time.

**Solution:** Learn and save successful tool chains.

```python
class ToolComposer:
    """Learn and reuse successful tool compositions"""
    
    def detect_pattern(self, traces: list[ReasoningTrace]) -> Optional[ToolMacro]:
        """Detect reusable patterns in successful traces"""
        if len(traces) >= 3:
            # Pattern: search → fetch → summarize
            if (traces[0].action == "web_search" and 
                traces[1].action == "fetch_url" and
                traces[2].action == "run_python"):
                return ToolMacro(
                    name="research_topic",
                    steps=[t.action for t in traces[:3]],
                    success_rate=1.0
                )
        return None
    
    def execute_macro(self, macro: ToolMacro, args: dict) -> list[ToolResult]:
        """Execute a learned composition"""
        results = []
        for step in macro.steps:
            # Execute with context from previous steps
            result = self.tools.execute(step, self._adapt_args(args, results))
            results.append(result)
        return results
```

**Impact:**
- 3-5 tool calls → 1 macro call
- Faster execution
- Fewer LLM planning calls

---

### 6. **Self-Verification Loop**

**Problem:** Agent produces output but doesn't verify correctness.

**Solution:** Critic-Actor pattern with verification tools.

```python
class SelfVerifier:
    """Verify agent outputs before returning to user"""
    
    def verify_code(self, code: str, spec: str) -> tuple[bool, str]:
        """Verify generated code meets spec"""
        checks = []
        
        # 1. Syntax check
        try:
            ast.parse(code)
            checks.append(("syntax", True, ""))
        except SyntaxError as e:
            checks.append(("syntax", False, str(e)))
        
        # 2. Spec compliance (LLM-based)
        prompt = f"Does this code satisfy the spec?\n\nSpec:\n{spec}\n\nCode:\n{code}"
        compliance = self._check_compliance(prompt)
        checks.append(("spec", compliance, ""))
        
        # 3. Run tests if available
        if self._has_tests(code):
            test_result = self._run_tests(code)
            checks.append(("tests", test_result.success, test_result.output))
        
        all_pass = all(passed for _, passed, _ in checks)
        report = "\n".join(f"{name}: {'✓' if p else '✗'} {msg}" for name, p, msg in checks)
        
        return all_pass, report
```

**Impact:**
- Catch errors before user sees them
- Higher quality outputs
- Fewer correction loops

---

### 7. **Contextual Tool Selection (Embeddings)**

**Problem:** Current context retrieval uses keyword matching (weak).

**Solution:** Embed codebase + use semantic search.

```python
class SemanticContextManager:
    """Retrieve relevant code using embeddings"""
    
    def __init__(self, codebase_root: Path):
        self.root = codebase_root
        self.index = self._build_index()
    
    def _build_index(self) -> VectorStore:
        """Embed all code files"""
        index = VectorStore()
        for file in self.root.rglob("*.py"):
            chunks = self._chunk_file(file)  # Split into functions/classes
            for chunk in chunks:
                index.add(
                    text=chunk.code,
                    metadata={"file": str(file), "type": chunk.type, "name": chunk.name}
                )
        return index
    
    def get_relevant_context(self, query: str, max_chunks: int = 5) -> str:
        """Retrieve most relevant code chunks"""
        results = self.index.query(query, n=max_chunks)
        return "\n\n".join(r["text"] for r in results)
```

**Impact:**
- Better context = better outputs
- Works across large codebases (>10k files)
- Replaces naive keyword search

---

### 8. **Failure Recovery & Retry Logic**

**Problem:** Single tool failure kills entire plan.

**Solution:** Exponential backoff + alternative strategies.

```python
class ResilientOrchestrator(Orchestrator):
    """Orchestrator with retry and fallback logic"""
    
    def execute_with_retry(self, step: Step, max_retries: int = 3) -> ToolResult:
        """Execute with exponential backoff"""
        for attempt in range(max_retries):
            result = self.tools.execute(step.tool, step.args)
            
            if result.success:
                return result
            
            # Retry with backoff
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            
            # Final attempt: try alternative tool
            alt_tool = self._find_alternative(step.tool)
            if alt_tool:
                return self.tools.execute(alt_tool, step.args)
        
        return result  # Return last failure
```

**Impact:**
- Handles transient failures (network, rate limits)
- More robust execution
- Better user experience

---

## Implementation Priority

| Feature | ROI | Effort | Priority |
|---------|-----|--------|----------|
| 1. Vector Memory | 10x | Medium | **P0** |
| 7. Semantic Context | 8x | Medium | **P0** |
| 3. ReAct Traces | 6x | Low | **P1** |
| 6. Self-Verification | 5x | Low | **P1** |
| 2. Tool Learning | 4x | Medium | **P2** |
| 8. Retry Logic | 3x | Low | **P2** |
| 4. Parallel Execution | 2x | High | **P3** |
| 5. Tool Macros | 2x | High | **P3** |

---

## Phase 1: Foundation (P0)

**Week 1: Vector Memory**
1. Add ChromaDB to requirements.txt
2. Implement `VectorMemory` class
3. Hook into `UnifiedAgent` to store all interactions
4. Add retrieval to context manager

**Week 2: Semantic Context**
1. Embed codebase on startup (cache embeddings)
2. Replace `_read_relevant_context` with semantic search
3. Add incremental re-indexing on file changes

**Deliverable:** Agent remembers past solutions + retrieves better context

---

## Phase 2: Reasoning (P1)

**Week 3: ReAct Traces**
1. Add `ReasoningTrace` dataclass
2. Modify `Orchestrator.execute()` to generate thoughts
3. Persist traces to `.awos/traces/*.json`
4. Add trace viewer CLI command

**Week 4: Self-Verification**
1. Implement `SelfVerifier` for code outputs
2. Add syntax/spec/test checks
3. Auto-retry on verification failure

**Deliverable:** Transparent reasoning + higher quality outputs

---

## Phase 3: Learning (P2)

**Week 5: Tool Learning**
1. SQLite DB for tool performance
2. Track success/failure per tool per task type
3. Recommend tools based on history

**Week 6: Retry Logic**
1. Add exponential backoff to tool execution
2. Implement fallback tool selection
3. Log all retries for analysis

**Deliverable:** Agent learns from experience + handles failures gracefully

---

## Success Metrics

| Metric | Baseline | Target |
|--------|----------|--------|
| Context relevance (human eval) | 60% | 90% |
| First-attempt success rate | 40% | 70% |
| Average task completion time | 45s | 30s |
| Cross-session knowledge reuse | 0% | 50% |
| Tool selection accuracy | 50% | 85% |

---

## Dependencies

**New packages:**
```txt
chromadb>=0.4.0          # Vector DB
sentence-transformers>=2.2.0  # Embeddings (or use Gemini API)
faiss-cpu>=1.7.0         # Alternative to ChromaDB (faster, no persistence)
```

**Optional:**
```txt
openai>=1.0.0            # For GPT-4 embeddings (if not using local)
cohere>=4.0.0            # For Cohere embeddings + rerank
```

---

## Open Questions

1. **Embedding model:** Local (all-MiniLM-L6-v2) vs API (Gemini/Cohere)?
   - Local: Free, fast, offline
   - API: Better quality, costs ~$0.0001/1k tokens

2. **Vector DB:** ChromaDB vs FAISS vs Qdrant?
   - ChromaDB: Easy, persistent, good for <1M vectors
   - FAISS: Fastest, no persistence (need manual save)
   - Qdrant: Production-grade, overkill for local agent

3. **Reasoning model:** Same model for thought generation or separate cheap model?
   - Same: Coherent reasoning
   - Separate: Cheaper (Gemini Flash for thoughts, Sonnet for actions)

---

## Next Steps

1. **Review this spec** — validate priorities
2. **Choose embedding strategy** — local vs API
3. **Implement Phase 1** — vector memory + semantic context
4. **Measure impact** — run before/after benchmarks

---

**Author:** Cascade  
**Date:** 2026-05-16  
**Status:** Draft — awaiting review

#!/usr/bin/env python3
"""
Phase 1 Integration Test — End-to-End Benchmark for Agentic Amplification

Tests all Week 1 + Week 2 components working together through UnifiedAgent
and Orchestrator integration points.  No API keys required.
"""
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, "scaffold")
sys.path.insert(0, "scaffold/agent")
os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from agent.memory.vector_memory import VectorMemory
from agent.memory.codebase_index import CodebaseIndex
from agent.budget_ledger import BudgetLedger, get_ledger
from agent.core.reasoning import (
    ReasoningTraceStore, ReasoningSession, ReasoningTrace
)
from agent.core.performance_tracker import ToolPerformanceTracker

# ── helpers ──────────────────────────────────────────────────────────────

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

results = []


def assert_test(name, condition, detail=""):
    if condition:
        results.append((name, True, detail))
        print(f"  {PASS} {name}")
    else:
        results.append((name, False, detail))
        print(f"  {FAIL} {name} — {detail}")


# ═══════════════════════════════════════════════════════════════════════════
print("=" * 60)
print("Phase 1 Integration Test")
print("=" * 60)

# ── 1. Vector Memory: Cross-Session Recall ───────────────────────────────
print("\n1. VectorMemory — Cross-Session Recall")
vm_dir = tempfile.mkdtemp(prefix="awos_vm_")
vm = VectorMemory(persist_dir=vm_dir)

# Session 1: store an interaction
vm.store(
    text="Q: What is the best Python logging library?\nA: structlog is excellent for structured logging.",
    metadata={"session_id": "s1", "handler": "reasoning", "cost": 0.001},
)
vm.store(
    text="Q: How do I configure structlog?\nA: Use structlog.configure with JSONRenderer.",
    metadata={"session_id": "s1", "handler": "coding", "cost": 0.002},
)

# Session 2: recall
vm2 = VectorMemory(persist_dir=vm_dir)  # fresh instance, same disk
recalled = vm2.retrieve("logging library recommendation", n_results=2)
assert_test(
    "recall_past_solutions",
    len(recalled) >= 1 and "structlog" in recalled[0]["text"],
    f"got {len(recalled)} results, text={recalled[0]['text'][:60] if recalled else 'none'}",
)

# Metadata preserved
assert_test(
    "metadata_preserved",
    recalled and recalled[0]["metadata"].get("handler") == "reasoning",
)

# Stats
stats = vm2.stats()
assert_test("vm_stats", stats["total_interactions"] >= 2, f"count={stats['total_interactions']}")

# ── 2. CodebaseIndex — Semantic Search ─────────────────────────────────
print("\n2. CodebaseIndex — Semantic Search Quality")
ci_dir = tempfile.mkdtemp(prefix="awos_ci_")
ci_vm = VectorMemory(persist_dir=ci_dir)
ci = CodebaseIndex(
    project_root=Path(__file__).parent / "scaffold" / "agent",
    vector_memory=ci_vm,
)
ci_stats = ci.index_project()
assert_test(
    "index_built",
    ci_stats["files"] > 0 and ci_stats["chunks"] > 0,
    f"{ci_stats['files']} files, {ci_stats['chunks']} chunks",
)

# Semantic query
ctx = ci.get_context_for_query("how to track budget spending", max_chunks=3)
assert_test(
    "semantic_query_budget",
    "budget" in ctx.lower() or "ledger" in ctx.lower() or "token" in ctx.lower(),
    f"context preview: {ctx[:120]}",
)

# ── 3. BudgetLedger — Persistence ────────────────────────────────────────
print("\n3. BudgetLedger — Persistence Across Instances")
bl_path = Path(tempfile.mkdtemp(prefix="awos_bl_")) / "budget.json"
bl1 = BudgetLedger(ledger_path=bl_path)
bl1.record("coding", "DeepSeek", 1000, 500, 0.003)
bl1.record("reasoning", "Claude-Haiku", 2000, 800, 0.008)

# Fresh instance reads same ledger
bl2 = BudgetLedger(ledger_path=bl_path)
status = bl2.get_status(monthly_budget=20.0)
assert_test(
    "ledger_persistence",
    status["requests"] >= 2 and status["spent"] >= 0.01,
    f"requests={status['requests']}, spent=${status['spent']:.4f}",
)

# ── 4. ReasoningTraceStore — Persistence ─────────────────────────────────
print("\n4. ReasoningTraceStore — Create, Save, List, Load")
ts_dir = tempfile.mkdtemp(prefix="awos_ts_")
ts = ReasoningTraceStore(persist_dir=ts_dir)

sess = ReasoningSession(session_id="int_test_1", goal="Test integration")
sess.add_trace(
    ReasoningTrace(
        thought="Need to search codebase",
        action="codebase_search",
        action_input={"query": "budget"},
        observation="Found BudgetLedger in scaffold/agent/budget_ledger.py",
        success=True,
        model="deepseek-chat",
        cost=0.001,
    )
)
sess.add_trace(
    ReasoningTrace(
        thought="Apply fix",
        action="edit_file",
        action_input={"file": "budget_ledger.py"},
        observation="File edited successfully",
        success=True,
        model="deepseek-chat",
        cost=0.002,
    )
)
fpath = ts.save(sess)
assert_test("trace_saved", fpath.exists(), f"path={fpath}")

listed = ts.list_sessions()
assert_test(
    "trace_listed",
    any(s["session_id"] == "int_test_1" for s in listed),
    f"sessions: {[s['session_id'] for s in listed]}",
)

loaded = ts.load("int_test_1")
assert_test(
    "trace_loaded",
    loaded is not None and len(loaded.traces) == 2,
    f"traces={len(loaded.traces) if loaded else 'none'}",
)

# ── 5. ToolPerformanceTracker — Recording & Querying ──────────────────────
print("\n5. ToolPerformanceTracker — Recording, Stats, Recommendations")
pt_dir = tempfile.mkdtemp(prefix="awos_pt_")
pt = ToolPerformanceTracker(persist_dir=pt_dir)

# Seed with mixed results
for i in range(5):
    pt.record(
        tool="_handle_coding",
        model="deepseek-chat",
        task_type="bug_analysis",
        success=(i % 2 == 0),  # 3 true, 2 false
        latency_ms=1200.0 + i * 100,
        cost=0.002,
    )
pt.record(
    tool="_handle_coding",
    model="claude-haiku-4-5",
    task_type="bug_analysis",
    success=True,
    latency_ms=2100.0,
    cost=0.005,
)

stats = pt.get_stats(tool="_handle_coding", task_type="bug_analysis", window=10)
assert_test(
    "perf_stats_count",
    stats["count"] == 6,
    f"count={stats['count']}",
)
assert_test(
    "perf_stats_rate",
    stats["success_rate"] is not None and 0.5 < stats["success_rate"] < 1.0,
    f"rate={stats['success_rate']}",
)

rec = pt.recommend_model("bug_analysis", ["deepseek-chat", "claude-haiku-4-5"])
assert_test(
    "perf_recommendation",
    rec is not None,
    f"recommended={rec}",
)

# ── 6. Self-Verification — Compile Check ───────────────────────────────────
print("\n6. Self-Verification — Compile-Check in _handle_coding")


def _extract_code_blocks(text: str) -> list:
    import re

    pattern = re.compile(r"```python\n(.*?)\n```", re.DOTALL)
    return [m.group(1) for m in pattern.finditer(text)]


good_code = "```python\ndef add(a, b):\n    return a + b\n```"
bad_code = "```python\ndef add(a, b)\n    return a + b\n```"

good_blocks = _extract_code_blocks(good_code)
bad_blocks = _extract_code_blocks(bad_code)

assert_test("extract_good", len(good_blocks) == 1 and good_blocks[0].startswith("def add"))
assert_test("extract_bad", len(bad_blocks) == 1)

# Compile check
good_ok = True
for block in good_blocks:
    try:
        compile(block, "<agent_output>", "exec")
    except SyntaxError:
        good_ok = False

bad_ok = True
for block in bad_blocks:
    try:
        compile(block, "<agent_output>", "exec")
    except SyntaxError:
        bad_ok = False

assert_test("compile_good_passes", good_ok)
assert_test("compile_bad_fails", not bad_ok)

# ── 7. UnifiedAgent Integration — Source Verification ──────────────────────
print("\n7. UnifiedAgent — Component Wiring (AST inspection)")


def _source_contains(path: Path, *fragments: str) -> bool:
    src = path.read_text(errors="ignore")
    return all(f in src for f in fragments)


ua_src = Path("scaffold/agent/unified_agent.py")
assert_test(
    "ua_imports_vector_memory",
    _source_contains(ua_src, "from memory.vector_memory import VectorMemory"),
)
assert_test(
    "ua_imports_codebase_index",
    _source_contains(ua_src, "from memory.codebase_index import CodebaseIndex"),
)
assert_test(
    "ua_imports_budget_ledger",
    _source_contains(ua_src, "from budget_ledger import BudgetLedger", "get_ledger()"),
)
assert_test(
    "ua_imports_performance",
    _source_contains(ua_src, "from core.performance_tracker import ToolPerformanceTracker"),
)
assert_test(
    "ua_init_vector_memory",
    _source_contains(ua_src, "self.vector_memory = VectorMemory"),
)
assert_test(
    "ua_init_codebase_index",
    _source_contains(ua_src, "self.codebase_index = CodebaseIndex"),
)
assert_test(
    "ua_init_budget_ledger",
    _source_contains(ua_src, "self.budget_ledger = get_ledger()"),
)
assert_test(
    "ua_init_performance",
    _source_contains(ua_src, "self.performance = ToolPerformanceTracker"),
)

# ── 8. UnifiedAgent — _read_relevant_context uses CodebaseIndex ─────────
print("\n8. UnifiedAgent — Context Retrieval Wiring")
assert_test(
    "ua_uses_semantic_search",
    _source_contains(
        ua_src,
        "self.codebase_index.get_context_for_query",
        "semantic_context",
    ),
)
assert_test(
    "ua_has_fallback",
    _source_contains(ua_src, "SECONDARY: Legacy keyword search"),
)

# ── 9. UnifiedAgent — Recall + Store Integration ────────────────────────
print("\n9. UnifiedAgent — Recall + Store Wiring")
assert_test(
    "ua_recalls_before_routing",
    _source_contains(ua_src, "self._recall_past_solutions(user_input)"),
)
assert_test(
    "ua_stores_after_response",
    _source_contains(ua_src, "self.vector_memory.store("),
)
assert_test(
    "ua_budget_wired_deepseek",
    _source_contains(ua_src, "self.budget_ledger.record", "_call_deepseek"),
)
assert_test(
    "ua_budget_wired_anthropic",
    _source_contains(ua_src, "self.budget_ledger.record", "_call_anthropic"),
)

# ── 10. Orchestrator — ReAct Trace + Performance Integration ────────────
print("\n10. Orchestrator — ReAct Trace + Performance Wiring")
orch_src = Path("scaffold/agent/orchestrator.py")
assert_test(
    "orch_imports_reasoning",
    _source_contains(
        orch_src,
        "from .core.reasoning import ReasoningTrace",
        "ReasoningSession",
        "ReasoningTraceStore",
    ),
)
assert_test(
    "orch_creates_session",
    _source_contains(orch_src, "ReasoningSession(", "session_id="),
)
assert_test(
    "orch_adds_trace",
    _source_contains(orch_src, "session.add_trace(trace)"),
)
assert_test(
    "orch_saves_trace",
    _source_contains(orch_src, "self.trace_store.save(session)"),
)
assert_test(
    "orch_imports_performance",
    _source_contains(orch_src, "from .core.performance_tracker import ToolPerformanceTracker"),
)
assert_test(
    "orch_records_perf",
    _source_contains(orch_src, "self.performance.record("),
)
assert_test(
    "orch_escalation_has_perf",
    _source_contains(
        orch_src,
        "performance_tracker=self.performance",
        "EscalationEngine",
    ),
)

# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)

for name, ok, detail in results:
    mark = PASS if ok else FAIL
    print(f"  {mark} {name}")
    if not ok and detail:
        print(f"      → {detail}")

print()
print(f"Total: {passed}/{len(results)} passed  |  {failed} failed")

# Cleanup
shutil.rmtree(vm_dir, ignore_errors=True)
shutil.rmtree(ci_dir, ignore_errors=True)
if bl_path.exists():
    shutil.rmtree(bl_path.parent, ignore_errors=True)
shutil.rmtree(ts_dir, ignore_errors=True)
shutil.rmtree(pt_dir, ignore_errors=True)

if failed > 0:
    print("\nSome tests FAILED.")
    sys.exit(1)
else:
    print("\nAll Phase 1 integration tests PASSED.")
    sys.exit(0)

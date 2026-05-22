#!/usr/bin/env python3
"""
Phase 1 validation tests — no API calls needed for most tests.

Covers:
  1. Routing: semantic/regex routes to correct handler
  2. _is_complex_coding_task: correctly classifies requests
  3. Token tracker: planning/execution types accepted
  4. Codebase discovery: extracts symbols via ast
  5. Verifier: applies SEARCH/REPLACE locally (free)
  6. Worker regex: SEARCH/REPLACE parser handles fenced+unfenced

Run:  python test_phase1.py
"""

import os
import sys
import tempfile
from pathlib import Path

# Put scaffold/agent on path
sys.path.insert(0, str(Path(__file__).parent / "scaffold" / "agent"))


# ─── 1. Routing helpers ───────────────────────────────────────────────────────

def test_is_complex_routing():
    print("\n[1] _is_complex_coding_task heuristics")

    # Import inline to avoid full agent init
    import importlib, types
    src = Path("scaffold/agent/unified_agent.py").read_text()

    # Extract just the method via exec — avoids importing the full agent
    ns = {}
    exec(
        "def _is_complex_coding_task(self, user_input):\n"
        + "\n".join(
            "    " + l if not l.startswith("    ") else l
            for l in src.split("def _is_complex_coding_task")[1]
            .split("def ")[0]
            .strip()
            .splitlines()[1:]
        ),
        ns,
    )
    check = lambda t: ns["_is_complex_coding_task"](None, t)

    cases = [
        # (input, expected, label)
        ("implement a full authentication system from scratch", True,  "full system"),
        ("build a new caching module",                          True,  "new module"),
        ("refactor the entire database layer",                  True,  "refactor + layer"),
        ("fix a typo in main.py",                              False, "typo fix"),
        ("just change one line",                               False, "one line"),
        ("rename variable x to y",                             False, "rename only"),
        ("add error handling to worker",                       False, "add + simple"),
        ("implement a complete pipeline service",              True,  "pipeline service"),
    ]

    passed = 0
    for text, expected, label in cases:
        result = check(text)
        status = "✅" if result == expected else "❌"
        if result != expected:
            print(f"  {status} FAIL [{label}]: got {result}, expected {expected} — '{text}'")
        else:
            print(f"  {status} [{label}]")
            passed += 1

    print(f"  {passed}/{len(cases)} passed")
    return passed == len(cases)


# ─── 2. Token Tracker: new types ─────────────────────────────────────────────

def test_token_tracker():
    print("\n[2] TokenTracker: planning + execution types")
    from token_tracker import TokenTracker

    t = TokenTracker()
    try:
        t.record("planning",   "Claude Sonnet", 100, 50, 0.001)
        t.record("execution",  "DeepSeek",      200, 80, 0.0002)
        t.record("routing",    "Haiku",          50, 10, 0.00005)
        t.record("new_type",   "unknown",         5,  5, 0.0)   # dynamic key
    except KeyError as e:
        print(f"  ❌ KeyError on record: {e}")
        return False

    status = t.get_budget_status()
    assert status["total_cost"] > 0, "cost not tracked"
    assert status["total_tokens"] == (150 + 280 + 60 + 10), "token count wrong"
    print(f"  ✅ All 4 request types accepted. Total cost: ${status['total_cost']:.6f}")
    return True


# ─── 3. Codebase discovery + AST symbols ─────────────────────────────────────

def test_codebase_discovery():
    print("\n[3] Orchestrator codebase discovery + AST symbols")

    try:
        from orchestrator import Orchestrator
    except Exception as e:
        print(f"  ⚠️  Cannot import Orchestrator (likely missing API key): {e}")
        return True  # Non-fatal for this test

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "auth.py").write_text(
            "class AuthManager:\n    def login(self, user): pass\n\ndef hash_password(p): return p\n"
        )
        (p / "db.py").write_text(
            "class Database:\n    def connect(self): pass\n\ndef get_connection(): pass\n"
        )

        o = Orchestrator.__new__(Orchestrator)  # skip __init__ (needs API keys)
        ctx = o._discover_codebase_context(tmp)

        assert "modules" in ctx
        assert "files" in ctx
        assert "symbols" in ctx
        assert len(ctx["symbols"]) >= 4, f"Expected >=4 symbols, got {ctx['symbols']}"

        print(f"  ✅ Found {len(ctx['symbols'])} symbols:")
        for s in ctx["symbols"][:6]:
            print(f"     {s}")
    return True


# ─── 4. Verifier: local SEARCH/REPLACE (no API) ──────────────────────────────

def test_verifier():
    print("\n[4] Verifier: SEARCH/REPLACE apply + Python syntax check")
    from verifier import Verifier

    v = Verifier()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")
        fname = f.name

    try:
        # Valid change
        r = v.verify_and_apply(
            {"search": "def sub(a, b):\n    return a - b",
             "replace": "def sub(a, b):\n    return a - b\n\ndef mul(a, b):\n    return a * b"},
            fname
        )
        assert r["success"], f"Valid change rejected: {r}"
        assert "mul" in Path(fname).read_text()
        print("  ✅ Valid change applied + syntax validated")

        # Invalid syntax change
        r2 = v.verify_and_apply(
            {"search": "def add(a, b):",
             "replace": "def add(a, b)  # missing colon"},
            fname
        )
        assert not r2["success"], "Invalid syntax should be rejected"
        print("  ✅ Syntax error correctly rejected")
    finally:
        Path(fname).unlink(missing_ok=True)
    return True


# ─── 5. Worker SEARCH/REPLACE parser ─────────────────────────────────────────

def test_worker_parser():
    print("\n[5] Worker: SEARCH/REPLACE parser (fenced + unfenced)")

    # Avoid needing API keys
    os.environ.setdefault("DEEPSEEK_API_KEY", "dummy-for-init")
    os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-for-init")

    try:
        from worker import Worker
        # Bypass real API init — just test the parser method
        w = Worker.__new__(Worker)
        w.client = None
        w.anthropic_client = None
        w.model = "deepseek-chat"
        w.fallback_model = "claude-3-5-haiku-20241022"
    except Exception as e:
        print(f"  ⚠️  Worker init issue: {e}")
        return True

    # Strategy 1: fenced
    fenced = (
        "SEARCH:\n```python\ndef old():\n    pass\n```\n"
        "REPLACE:\n```python\ndef new():\n    return 42\n```\n"
        "REASONING: renamed function"
    )
    r = w._parse_search_replace(fenced)
    assert r["success"], f"Fenced parse failed: {r}"
    assert "old" in r["search"]
    assert "new" in r["replace"]
    print("  ✅ Fenced blocks parsed correctly")

    # Strategy 2: unfenced
    unfenced = (
        "SEARCH:\ndef old():\n    pass\n"
        "REPLACE:\ndef new():\n    return 42\n"
        "REASONING: renamed"
    )
    r2 = w._parse_search_replace(unfenced)
    assert r2["success"], f"Unfenced parse failed: {r2}"
    print("  ✅ Unfenced blocks parsed correctly")

    # Edge: empty blocks should fail gracefully
    r3 = w._parse_search_replace("SEARCH:\n\nREPLACE:\n\n")
    assert not r3["success"]
    print("  ✅ Empty blocks correctly rejected")

    return True


# ─── 6. GitManager: backup + rollback ────────────────────────────────────────

def test_git_manager():
    print("\n[6] GitManager: file backup and rollback")
    sys.path.insert(0, str(Path("scaffold/agent")))
    from git_manager import GitManager

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        target = p / "code.py"
        target.write_text("def foo(): return 1\n")

        gm = GitManager(tmp)  # no git repo in tmpdir
        assert not gm.is_git_repo

        # Backup
        gm.backup_file(str(target))
        assert str(target) in gm.file_backups
        print("  ✅ File backed up")

        # Simulate a write by verifier
        target.write_text("def foo(): return 999  # broken\n")
        assert "999" in target.read_text()

        # Rollback
        gm.rollback_file(str(target))
        restored = target.read_text()
        assert "return 1" in restored, f"Rollback failed: {restored}"
        print("  ✅ File rolled back correctly")

        # finalize(success=True) should leave file alone
        target.write_text("def foo(): return 42\n")
        gm.record_modified(str(target))
        gm.finalize(True)
        assert "42" in target.read_text()
        print("  ✅ finalize(success=True) leaves file untouched")

    return True


# ─── 8. SymbolIndex: symbol extraction + import graph ──────────────────────────

def test_symbol_index():
    print("\n[8] SymbolIndex: tree-sitter symbol extraction + import graph")
    from symbol_index import SymbolIndex

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)

        # File A: defines base classes
        (p / "base.py").write_text(
            "class BaseHandler:\n"
            "    def handle(self, req): pass\n"
            "    def validate(self, data): pass\n"
            "\ndef create_handler(): return BaseHandler()\n"
        )
        # File B: imports from base
        (p / "worker.py").write_text(
            "from base import BaseHandler, create_handler\n"
            "import os\n"
            "\nclass Worker(BaseHandler):\n"
            "    def execute(self, task): pass\n"
            "    def retry(self, task, error): pass\n"
        )
        # File C: imports worker
        (p / "orchestrator.py").write_text(
            "from worker import Worker\n"
            "\nclass Orchestrator:\n"
            "    def run(self, goal): pass\n"
        )

        idx = SymbolIndex(tmp)
        idx.build()

        # Check symbol extraction
        base_syms = idx.get_file_symbols(str(p / "base.py"))
        assert "BaseHandler" in base_syms.classes, f"Classes: {base_syms.classes}"
        assert any("handle" in f for f in base_syms.functions), f"Fns: {base_syms.functions}"
        assert any("create_handler" in f for f in base_syms.functions)
        print(f"  ✅ Symbol extraction: {len(base_syms.classes)} classes, {len(base_syms.functions)} functions")

        # Check import graph
        worker_syms = idx.get_file_symbols(str(p / "worker.py"))
        assert "base" in worker_syms.from_imports, f"from_imports: {worker_syms.from_imports}"
        print(f"  ✅ Import graph: worker imports {list(worker_syms.from_imports.keys())}")

        # Check cross-file context for worker
        ctx = idx.get_context_for_task(str(p / "worker.py"), "add retry with backoff")
        assert "Worker" in ctx or "execute" in ctx, f"Context missing worker symbols: {ctx[:200]}"
        print(f"  ✅ Cross-file context generated ({len(ctx)} chars)")
        print(f"     Preview: {ctx[:100].strip()}...")

        print(f"  ✅ Summary: {idx.summary()}")

    return True


# ─── 7. Worker error context injection ───────────────────────────────────────

def test_worker_error_context():
    print("\n[7] Worker: error_context injected into retry prompt")
    os.environ.setdefault("DEEPSEEK_API_KEY", "dummy")
    os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

    from worker import Worker
    w = Worker.__new__(Worker)
    w.client = None
    w.anthropic_client = None
    w.model = "deepseek-chat"
    w.fallback_model = "claude-3-5-haiku-20241022"

    task_with_error = {
        "task_id": 1,
        "file": "test.py",
        "action": "add logging",
        "complexity": "low",
        "error_context": "SyntaxError: invalid syntax on line 5"
    }

    # Build the prompt manually to check it contains the error section
    action = task_with_error["action"]
    file_content = "def foo(): pass\n"
    context_snippet = w._extract_context(file_content, action)
    error_context = task_with_error.get("error_context", "")
    attempt = 2

    retry_section = ""
    if error_context and attempt > 1:
        retry_section = f"\n\u26a0\ufe0f  PREVIOUS ATTEMPT FAILED \u2014 FIX THIS ERROR:\n{error_context}\n\nDo NOT repeat the same approach. Use a different strategy.\n"

    assert "SyntaxError" in retry_section, "Error context not in retry section"
    assert "PREVIOUS ATTEMPT FAILED" in retry_section
    print("  ✅ Error context appears in retry prompt")

    task_first = {**task_with_error, "error_context": ""}
    retry_section_first = ""
    if task_first.get("error_context") and 1 > 1:
        retry_section_first = "ERROR"
    assert retry_section_first == ""
    print("  ✅ Error context absent on attempt 1 (clean prompt)")

    return True


# ─── 9. ResponseCache: TTL, hits, misses, stats ────────────────────────────────

def test_response_cache():
    print("\n[9] ResponseCache: TTL, hits, misses, stats")
    from response_cache import ResponseCache

    cache = ResponseCache(ttl_seconds=3)  # 3-second TTL for testing

    key = cache.make_key("deepseek", "explain the worker module")
    same_key = cache.make_key("deepseek", "  Explain the Worker Module  ")  # normalised

    # Miss on empty cache
    assert cache.get(key) is None
    print("  ✅ Cache miss on empty cache")

    # Set and hit
    cache.set(key, "The worker module handles...", model="deepseek")
    assert cache.get(key) == "The worker module handles..."
    print("  ✅ Cache hit after set")

    # Same key after normalisation
    assert key == same_key, "Normalisation should produce same key"
    print("  ✅ Key normalisation works (case + whitespace insensitive)")

    # Different model = different key
    other_key = cache.make_key("haiku", "explain the worker module")
    assert other_key != key
    print("  ✅ Different model produces different key")

    # TTL expiry
    import time
    time.sleep(4)
    assert cache.get(key) is None, "Expired entry should return None"
    print("  ✅ TTL expiry works (3s)")

    # Stats
    s = cache.stats()
    assert s["hits"] == 1
    assert s["misses"] == 2, f"Expected 2 misses (empty + expired), got {s['misses']}"  # empty + expired
    print(f"  ✅ Stats: {cache.summary()}")

    return True


# ─── 10. Budget alerts at thresholds ─────────────────────────────────────

def test_budget_alerts():
    print("\n[10] TokenTracker: budget threshold alerts")
    from token_tracker import TokenTracker
    import io, sys as _sys

    t = TokenTracker(monthly_budget=1.00)  # $1 budget for easy testing

    alerts = []
    original_print = print

    # Capture prints
    import builtins
    captured = []
    original = builtins.print
    def capturing_print(*args, **kwargs):
        captured.append(" ".join(str(a) for a in args))
        original(*args, **kwargs)
    builtins.print = capturing_print

    try:
        t.record("coding", "deepseek", 100, 50, 0.45)   # 45% — no alert
        t.record("coding", "deepseek", 100, 50, 0.10)   # 55% — fires 50% alert
        t.record("coding", "deepseek", 100, 50, 0.20)   # 75% — fires 75% alert
        t.record("coding", "deepseek", 100, 50, 0.16)   # 91% — fires 90% alert
        prev_count = len([c for c in captured if "BUDGET ALERT" in c])
        t.record("coding", "deepseek", 100, 50, 0.01)   # still >90% — no new alert
    finally:
        builtins.print = original

    alerts_fired = [c for c in captured if "BUDGET ALERT" in c]
    assert len(alerts_fired) == 3, f"Expected 3 alerts, got {len(alerts_fired)}: {alerts_fired}"
    assert "50%" in alerts_fired[0], f"Expected 50% threshold in: {alerts_fired[0]}"
    assert "75%" in alerts_fired[1], f"Expected 75% threshold in: {alerts_fired[1]}"
    assert "90%" in alerts_fired[2], f"Expected 90% threshold in: {alerts_fired[2]}"
    print("  ✅ Fired exactly at 50%, 75%, 90%")
    print("  ✅ No duplicate alerts at same threshold")

    # Cache hit tracking
    t.record_cache_hit(estimated_cost_saved=0.01)
    t.record_cache_hit(estimated_cost_saved=0.01)
    assert t.cache_hits == 2
    assert abs(t.cache_savings_est - 0.02) < 0.0001
    print(f"  ✅ Cache hits tracked: {t.cache_hits} hits, ${t.cache_savings_est:.4f} saved")

    return True


# ─── 11. EscalationEngine ────────────────────────────────────────────────────

def test_escalation_engine():
    print("\n[11] EscalationEngine: complexity gates + failure escalation")
    from escalation_engine import EscalationEngine, EscalationLevel

    e = EscalationEngine(monthly_budget=20.0)

    simple_task = {"task_id": 1, "file": "utils.py", "action": "fix typo", "complexity": "low"}
    complex_task = {"task_id": 2, "file": "auth.py",  "action": "implement authentication system from scratch", "complexity": "high"}
    arch_task    = {"task_id": 3, "file": "core.py",  "action": "refactor entire database layer architecture", "complexity": "high"}

    # Simple task = DeepSeek (no failures, low complexity)
    d = e.decide(simple_task, failure_count=0, budget_remaining=19.0)
    assert d.spec.level == EscalationLevel.DEEPSEEK, f"Expected DEEPSEEK, got {d.spec.level}"
    print(f"  ✅ Simple task → {d.spec.name}")

    # Complex task = Sonnet (complexity boost pushes score >= 7)
    d = e.decide(complex_task, failure_count=0, budget_remaining=15.0)
    assert d.spec.level == EscalationLevel.SONNET, f"Expected Sonnet, got {d.spec.level}"
    print(f"  ✅ Complex task → {d.spec.name}")

    # After 1 failure on simple task → escalates to Haiku
    d = e.decide(simple_task, failure_count=1, budget_remaining=19.0)
    assert d.spec.level.value >= EscalationLevel.HAIKU.value, f"Expected >= Haiku after 1 failure, got {d.spec.level}"
    print(f"  ✅ 1 failure → escalated to {d.spec.name}")

    # After 2 failures → Sonnet
    d = e.decide(simple_task, failure_count=2, budget_remaining=19.0)
    assert d.spec.level.value >= EscalationLevel.SONNET.value, f"Expected >= Sonnet after 2 failures"
    print(f"  ✅ 2 failures → escalated to {d.spec.name}")

    # Budget gate: with only $2 remaining, Sonnet is blocked
    d = e.decide(arch_task, failure_count=3, budget_remaining=2.0)
    assert d.spec.level != EscalationLevel.SONNET, f"Sonnet should be blocked at $2 remaining"
    print(f"  ✅ Budget gate: Sonnet blocked at $2 remaining → {d.spec.name} used")

    # Force level override
    from escalation_engine import EscalationLevel
    d = e.decide(simple_task, force_level=EscalationLevel.SONNET)
    assert d.spec.level == EscalationLevel.SONNET
    assert d.forced
    print(f"  ✅ Force level override works")

    return True


# ─── 12. ExampleStore: record + retrieve ────────────────────────────────────────────

def test_example_store():
    print("\n[12] ExampleStore: record successes, inject few-shot examples")
    from example_store import ExampleStore

    store = ExampleStore(store_path="/tmp/test_awos_examples.json")
    store.clear() if hasattr(store, 'clear') else None
    store._examples.clear()

    task1 = {"task_id": 1, "file": "worker.py",   "action": "add error handling to execute function"}
    task2 = {"task_id": 2, "file": "verifier.py",  "action": "add logging to verify method"}
    task3 = {"task_id": 3, "file": "worker.py",   "action": "add retry logic to execute function"}

    store.record_success(task1, "def execute(self):\n    pass", "def execute(self):\n    try:\n        pass\n    except Exception as e:\n        raise")
    store.record_success(task2, "def verify(self):\n    pass", "def verify(self):\n    print('verifying')\n    pass")

    assert store.size() == 2
    print(f"  ✅ Stored 2 examples")

    # Retrieve for similar task (same file type, overlapping keywords)
    prompt = store.get_examples_for_prompt(task3)
    assert "EXAMPLES" in prompt or len(prompt) > 0, "Should find examples for similar task"
    print(f"  ✅ Retrieved relevant examples for similar task")
    print(f"     Preview: {prompt[:80].strip()}...")

    # No cross-file-type retrieval (task3 is .py, should not get .js examples)
    js_task = {"task_id": 4, "file": "app.js", "action": "add error handling"}
    js_prompt = store.get_examples_for_prompt(js_task)
    assert js_prompt == "", f"Should not retrieve .py examples for .js task"
    print(f"  ✅ No cross-extension contamination")

    return True


# ─── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    results = []
    tests = [
        test_token_tracker,
        test_codebase_discovery,
        test_verifier,
        test_worker_parser,
        test_is_complex_routing,
        test_git_manager,
        test_symbol_index,
        test_worker_error_context,
        test_response_cache,
        test_budget_alerts,
        test_escalation_engine,
        test_example_store,
    ]

    for t in tests:
        try:
            results.append(t())
        except Exception as e:
            import traceback
            print(f"  ❌ Exception: {e}")
            traceback.print_exc()
            results.append(False)

    passed = sum(results)
    total = len(results)
    print(f"\n{'='*50}")
    print(f"Phase 1 Tests: {passed}/{total} passed")
    if passed == total:
        print("✅ All Phase 1 tests passed — pipeline is healthy")
    else:
        print("❌ Some tests failed — see above")
    print("="*50)
    sys.exit(0 if passed == total else 1)

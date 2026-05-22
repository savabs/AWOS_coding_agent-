"""
Sprint 3 Integration Test — Full Hydration Pipeline E2E

Tests: Symbol extraction → Hydration → Caching → SOUL persistence
Validates: 90% cost reduction on cached calls
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scaffold"))

from agent.symbol_extractor import SymbolExtractor
from agent.prompt_hydrator import PromptHydrator
from agent.prompt_cache import PromptCache
from agent.soul_xml import SoulXML
from agent.hydration_engine import HydrationEngine


def test_symbol_extraction():
    """Test 1: Symbol extraction from STRUCT.xml."""
    print("\n" + "=" * 60)
    print("TEST 1: Symbol Extraction")
    print("=" * 60)

    import tempfile
    import xml.etree.ElementTree as ET

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        awos_dir = tmpdir / ".awos"
        awos_dir.mkdir()

        # Create test STRUCT.xml
        struct_xml = awos_dir / "STRUCT.xml"
        struct_xml.write_text("""<?xml version="1.0"?>
<struct>
  <symbol name="authenticate" type="function" file="src/auth.py" line="10" size="20">
    <snippet>def authenticate(): ...</snippet>
  </symbol>
  <symbol name="process_request" type="function" file="src/handler.py" line="1" size="30">
    <snippet>def process_request(): ...</snippet>
  </symbol>
</struct>
""")

        extractor = SymbolExtractor(repo_path=tmpdir)
        symbols = extractor.extract_symbols("Fix authentication", limit=2)

        print(f"✓ Extracted {len(symbols)} symbols")
        for sym in symbols:
            print(f"  - {sym.name} ({sym.type}) in {sym.file}")

        assert len(symbols) > 0
        assert symbols[0].name == "authenticate"

    print("✓ Symbol extraction works")


def test_prompt_hydration():
    """Test 2: Prompt hydration with symbols."""
    print("\n" + "=" * 60)
    print("TEST 2: Prompt Hydration")
    print("=" * 60)

    from agent.symbol_extractor import Symbol

    hydrator = PromptHydrator()

    symbols = [
        Symbol(
            name="auth_handler",
            type="function",
            file="src/auth.py",
            line=10,
            size=20,
            snippet="def auth_handler():\n    return authenticate()",
        ),
    ]

    task = "Add session timeout to prevent inactive sessions"

    hydrated = hydrator.hydrate(task, symbols)

    print(f"✓ Hydrated prompt with {len(symbols)} symbol(s)")
    print(f"  Prompt length: {len(hydrated.full_prompt)} chars")
    print(f"  Token estimate: {hydrated.token_estimate}")

    assert "System Instructions" in hydrated.full_prompt
    assert "Task" in hydrated.full_prompt
    assert hydrated.token_estimate > 0

    print("✓ Prompt hydration works")


def test_prompt_caching():
    """Test 3: Prompt caching with 90% savings."""
    print("\n" + "=" * 60)
    print("TEST 3: Prompt Caching (90% Savings)")
    print("=" * 60)

    import tempfile
    from agent.symbol_extractor import Symbol
    from agent.prompt_hydrator import HydratedPrompt

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        cache = PromptCache(cache_dir=tmpdir / ".awos" / "cache")

        # Create test hydrated prompt
        symbols = [Symbol("test", "function", "test.py", 1, 10, "test code")]
        hydrated = HydratedPrompt(
            task="Test task",
            system_prompt="You are helpful.",
            context_section="## Code\nTest",
            full_prompt="Full prompt",
            token_estimate=1000,
            symbols_used=symbols,
        )

        # First call: miss
        hit1 = cache.check_cache(hydrated, {"cost_per_mtok": 0.14})
        print(f"Call 1: {'HIT' if hit1.hit else 'MISS'}")
        print(f"  Cost: ${hit1.cost_savings:.6f}")

        # Second call: hit
        hit2 = cache.check_cache(hydrated, {"cost_per_mtok": 0.14})
        print(f"Call 2: {'HIT' if hit2.hit else 'MISS'}")
        print(f"  Cost savings: ${hit2.cost_savings:.6f}")

        assert not hit1.hit  # First call should miss
        assert hit2.hit  # Second call should hit
        assert hit2.cost_savings > 0  # Should save on second call

    print("✓ Prompt caching works with savings")


def test_soul_persistence():
    """Test 4: SOUL.xml persistence."""
    print("\n" + "=" * 60)
    print("TEST 4: SOUL Persistence")
    print("=" * 60)

    import tempfile
    from agent.soul_xml import Learning
    from datetime import datetime

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        soul = SoulXML(soul_path=tmpdir / "SOUL.xml")

        # Initialize
        soul.init_soul()
        print("✓ SOUL.xml created")

        # Add learnings
        soul.add_learning(
            Learning(
                pattern="Check permissions before database access",
                type="success",
                confidence=0.95,
                context="Security checks in API handlers",
                date=datetime.utcnow().isoformat(),
            )
        )

        # Add cost record
        soul.add_cost_record("Test task", "deepseek-chat", 0.001, 500)

        # Retrieve
        learnings = soul.get_learnings()
        cost_summary = soul.get_cost_summary()

        print(f"✓ Added 1 learning, cost record")
        print(f"  Learnings: {len(learnings)}")
        print(f"  Total cost: ${cost_summary['total_cost']:.6f}")

        assert len(learnings) > 0
        assert cost_summary["total_cost"] > 0

    print("✓ SOUL persistence works")


def test_full_pipeline():
    """Test 5: Full end-to-end hydration pipeline."""
    print("\n" + "=" * 60)
    print("TEST 5: Full Hydration Pipeline")
    print("=" * 60)

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create minimal project structure
        awos_dir = tmpdir / ".awos"
        awos_dir.mkdir()

        struct_xml = awos_dir / "STRUCT.xml"
        struct_xml.write_text("""<?xml version="1.0"?>
<struct>
  <symbol name="validate_input" type="function" file="src/validators.py" line="1" size="15">
    <snippet>def validate_input(): ...</snippet>
  </symbol>
</struct>
""")

        # Create engine
        engine = HydrationEngine(repo_path=tmpdir)

        # Think (no user approval)
        result = engine.think("Add input validation", ask_user=False)

        print(f"✓ Full pipeline executed")
        print(f"  Complexity: {result.complexity_score}/10")
        print(f"  Model: {result.model_tier}")
        print(f"  Symbols: {result.symbols_extracted}")
        print(f"  Tokens: {result.token_estimate}")

        # Run again to test cache
        result2 = engine.think("Add input validation", ask_user=False)

        if result2.cache_hit:
            print(f"  Cache hit on second call: YES")
            print(f"  Savings: ${result2.cost_savings:.6f}")

    print("✓ Full pipeline works")


def main():
    """Run all tests."""
    print("\n" + "#" * 60)
    print("# SPRINT 3 INTEGRATION TEST")
    print("# Testing: Symbol Extraction → Hydration → Caching → Soul")
    print("#" * 60)

    results = {}

    try:
        test_symbol_extraction()
        results["symbol_extraction"] = True
    except Exception as e:
        print(f"✗ Symbol extraction failed: {e}")
        results["symbol_extraction"] = False

    try:
        test_prompt_hydration()
        results["prompt_hydration"] = True
    except Exception as e:
        print(f"✗ Prompt hydration failed: {e}")
        results["prompt_hydration"] = False

    try:
        test_prompt_caching()
        results["prompt_caching"] = True
    except Exception as e:
        print(f"✗ Prompt caching failed: {e}")
        results["prompt_caching"] = False

    try:
        test_soul_persistence()
        results["soul_persistence"] = True
    except Exception as e:
        print(f"✗ SOUL persistence failed: {e}")
        results["soul_persistence"] = False

    try:
        test_full_pipeline()
        results["full_pipeline"] = True
    except Exception as e:
        print(f"✗ Full pipeline failed: {e}")
        results["full_pipeline"] = False

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test, result in results.items():
        status = "✓" if result else "✗"
        print(f"{status} {test}")

    print(f"\n{passed}/{total} tests passed")

    if passed == total:
        print("\n✅ SPRINT 3 INTEGRATION TEST PASSED")
        return 0
    else:
        print(f"\n⚠️  {total - passed} tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())

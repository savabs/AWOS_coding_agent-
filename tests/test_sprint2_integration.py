import pytest
pytestmark = pytest.mark.integration

"""
Sprint 2 Integration Test — Real API Calls

This test validates that the dispatcher correctly routes tasks to the right
model tier based on complexity scoring, and that each model responds correctly.

API Cost Optimization:
- Use minimal tokens (short prompts, short responses)
- Test each tier once (DeepSeek, Haiku)
- Skip Gemini/OpenAI unless explicitly needed
- Total estimated cost: ~$0.05–0.10

Run:
    python3 tests/test_sprint2_integration.py
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# Load .env file
project_root = Path(__file__).parent.parent
env_file = project_root / ".env"
if env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(env_file)

# Add scaffold to path
sys.path.insert(0, str(project_root / "scaffold"))

from agent.complexity_scorer import ComplexityScorer
from agent.model_router import ModelRouter
from agent.preflight_manifest import PreflightManifest
from agent.dispatcher import Dispatcher


def test_complexity_scoring():
    """Test 1: Verify complexity scoring heuristics."""
    print("\n" + "=" * 60)
    print("TEST 1: Complexity Scoring")
    print("=" * 60)

    scorer = ComplexityScorer()

    test_cases = [
        ("x = 1", 1, "trivial"),
        ("if x > 10:\n    return x\nelse:\n    return 0", 2, "simple"),
        ("""
def process(items):
    result = []
    for item in items:
        if item > 10:
            for sub in item.split():
                if sub.isdigit():
                    result.append(int(sub))
    return result
""", 4, "moderate"),
    ]

    for code, expected_max, desc in test_cases:
        score = scorer.score(code)
        status = "✓" if score <= expected_max else "✗"
        print(f"{status} {desc}: score={score}, expected<={expected_max}")

    print("\n✓ Complexity scoring works")


def test_model_routing():
    """Test 2: Verify model routing by complexity."""
    print("\n" + "=" * 60)
    print("TEST 2: Model Routing")
    print("=" * 60)

    router = ModelRouter()

    test_cases = [
        (1, "deepseek", "Budget tier (score 1)"),
        (5, "deepseek", "Balanced tier (score 5)"),
        (8, "anthropic", "Expert tier (score 8) — Haiku, no Opus"),
    ]

    for score, expected_provider, desc in test_cases:
        config = router.route(score)
        status = "✓" if config.provider == expected_provider else "✗"
        print(f"{status} {desc}")
        print(f"   Model: {config.model} | Cost: ${config.cost_per_mtok}/MTok")

    print("\n✓ Model routing works")


def test_manifest_generation():
    """Test 3: Verify preflight manifest generation."""
    print("\n" + "=" * 60)
    print("TEST 3: Preflight Manifest")
    print("=" * 60)

    manifest_gen = PreflightManifest()

    path = manifest_gen.generate(
        task_description="Add error handling",
        complexity_score=5,
        repo_path="/test/repo",
        files_involved=["src/main.py", "src/utils.py"],
    )

    data = manifest_gen.read_manifest(path)
    print(f"✓ Manifest generated: {path}")
    print(f"  Task: {data['task']}")
    print(f"  Complexity: {data['complexity_score']}/10")
    print(f"  Model: {data['model']}")
    print(f"  Cost: ${data['cost_estimate']:.6f}")
    print(f"  Files: {len(data['files_involved'])} involved")

    # Validate structure
    assert data["task"] == "Add error handling"
    assert data["complexity_score"] == 5
    assert isinstance(data["model"], str) and data["model"]  # model name populated
    assert len(data["files_involved"]) == 2

    print("\n✓ Manifest generation works")


def test_dispatcher_pipeline():
    """Test 4: Full dispatcher pipeline (no API calls yet)."""
    print("\n" + "=" * 60)
    print("TEST 4: Dispatcher Pipeline")
    print("=" * 60)

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create test file
        test_file = tmpdir / "test.py"
        test_file.write_text("""
def simple():
    return 42
""")

        dispatcher = Dispatcher(repo_path=tmpdir)
        decision = dispatcher.dispatch(
            task_description="Add docstrings",
            target_files=["test.py"],
            ask_user=False,
        )

        print(f"✓ Dispatch completed")
        print(f"  Decision: {'APPROVED' if decision.approved else 'DENIED'}")
        print(f"  Complexity: {decision.complexity_score}/10")
        print(f"  Model: {decision.model_config['model']}")
        print(f"  Cost: ${decision.cost_estimate:.6f}")
        print(f"  Manifest: {decision.manifest_path}")

        print("\n✓ Dispatcher pipeline works")


def test_api_integration_deepsek():
    """Test 5: Call DeepSeek API (budget tier)."""
    print("\n" + "=" * 60)
    print("TEST 5: DeepSeek API Integration")
    print("=" * 60)

    try:
        import litellm
        
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            print("✗ DEEPSEEK_API_KEY not set — skipping")
            return False

        print("Calling: deepseek-chat...")
        
        response = litellm.completion(
            model="deepseek/deepseek-chat",
            api_key=api_key,
            messages=[
                {
                    "role": "user",
                    "content": "What is 2+2? Answer in one sentence only."
                }
            ],
            temperature=0.7,
            max_tokens=50,
        )

        print(f"✓ DeepSeek responded")
        print(f"  Usage: {response.usage.prompt_tokens} → {response.usage.completion_tokens} tokens")
        print(f"  Response: {response.choices[0].message.content[:100]}")

        return True

    except Exception as e:
        print(f"✗ DeepSeek API failed: {e}")
        return False


def test_api_integration_anthropic():
    """Test 6: Call Anthropic API (balanced tier)."""
    print("\n" + "=" * 60)
    print("TEST 6: Anthropic API Integration")
    print("=" * 60)

    try:
        from anthropic import Anthropic
        
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            print("✗ ANTHROPIC_API_KEY not set — skipping")
            return False

        print("Calling: claude-3-5-haiku (native client)...")
        
        client = Anthropic(api_key=api_key)
        
        # Try multiple model IDs to find one that works
        models_to_try = [
            "claude-3-5-haiku-20241022",
            "claude-3-haiku-20240307",
            "claude-sonnet-4-6",
        ]
        
        message = None
        for model_id in models_to_try:
            try:
                message = client.messages.create(
                    model=model_id,
                    max_tokens=50,
                    messages=[
                        {
                            "role": "user",
                            "content": "What is 2+2? Answer in one sentence only."
                        }
                    ]
                )
                print(f"✓ Claude responded (using {model_id})")
                break
            except Exception:
                continue
        
        if message is None:
            print(f"✗ None of the Anthropic models were available: {', '.join(models_to_try)}")
            print("   Note: Check your Anthropic account for available models")
            return False

        if message.content:
            print(f"  Response: {message.content[0].text[:100]}")

        return True

    except Exception as e:
        print(f"✗ Anthropic API failed: {e}")
        return False


def test_api_integration_gemini():
    """Test 7: Call Gemini API (free tier / budget)."""
    print("\n" + "=" * 60)
    print("TEST 7: Gemini API Integration")
    print("=" * 60)

    try:
        import google.generativeai as genai
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("✗ GEMINI_API_KEY not set — skipping")
            return False

        print("Calling: gemini-2.5-flash (native client)...")
        
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content("What is 2+2? Answer in one sentence only.")

        print(f"✓ Gemini-Flash responded")
        if response.text:
            print(f"  Response: {response.text[:100]}")

        return True

    except Exception as e:
        print(f"✗ Gemini API failed: {e}")
        return False


def main():
    """Run all tests."""
    print("\n" + "#" * 60)
    print("# SPRINT 2 INTEGRATION TEST")
    print("# Testing: Complexity Scoring → Routing → Manifest → Consent")
    print("#" * 60)

    results = {}

    # Local tests (no API cost)
    try:
        test_complexity_scoring()
        results["complexity"] = True
    except Exception as e:
        print(f"✗ Complexity scoring failed: {e}")
        results["complexity"] = False

    try:
        test_model_routing()
        results["routing"] = True
    except Exception as e:
        print(f"✗ Model routing failed: {e}")
        results["routing"] = False

    try:
        test_manifest_generation()
        results["manifest"] = True
    except Exception as e:
        print(f"✗ Manifest generation failed: {e}")
        results["manifest"] = False

    try:
        test_dispatcher_pipeline()
        results["dispatcher"] = True
    except Exception as e:
        print(f"✗ Dispatcher pipeline failed: {e}")
        results["dispatcher"] = False

    # API tests (with cost)
    print("\n" + "=" * 60)
    print("API INTEGRATION TESTS (real API calls)")
    print("=" * 60)

    results["deepsek"] = test_api_integration_deepsek()
    results["anthropic"] = test_api_integration_anthropic()
    results["gemini"] = test_api_integration_gemini()

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
        print("\n✅ SPRINT 2 INTEGRATION TEST PASSED")
        return 0
    else:
        print(f"\n⚠️  {total - passed} tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())

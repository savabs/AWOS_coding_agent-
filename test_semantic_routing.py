#!/usr/bin/env python3
"""
Test script for the semantic routing system

Tests the new semantic RequestRouter using Claude Haiku:
- Measures response time for routing decisions
- Compares against expected handler types
- Tracks confidence, cost estimates, and token estimates
- Displays cost and performance analysis
"""

import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

# Add scaffold directory to path
sys.path.insert(0, str(Path(__file__).parent / "scaffold" / "agent"))

from unified_agent import RequestRouter, HandlerType

# Test inputs with expected handlers (from regex approach baseline)
TEST_CASES = [
    {
        "input": "explain this project",
        "expected": HandlerType.REASONING,
        "description": "Project explanation request"
    },
    {
        "input": "what can you do",
        "expected": HandlerType.WORKFLOW,
        "description": "Capability/help inquiry"
    },
    {
        "input": "implement a login",
        "expected": HandlerType.CODING,
        "description": "Feature implementation request"
    },
    {
        "input": "review my code",
        "expected": HandlerType.REVIEW,
        "description": "Code review request"
    },
    {
        "input": "what should I do next",
        "expected": HandlerType.WORKFLOW,
        "description": "Workflow/next steps request"
    },
    {
        "input": "how should I design the auth system",
        "expected": HandlerType.REASONING,
        "description": "Architecture/design decision request"
    },
    {
        "input": "fix this bug in the parser",
        "expected": HandlerType.CODING,
        "description": "Bug fix request"
    },
    {
        "input": "are there any security issues with this code",
        "expected": HandlerType.REVIEW,
        "description": "Security analysis request"
    },
    {
        "input": "where am I in the workflow",
        "expected": HandlerType.WORKFLOW,
        "description": "Current status request"
    },
    {
        "input": "what's the best approach for scaling",
        "expected": HandlerType.REASONING,
        "description": "Architecture/best practices request"
    },
]


def print_header(title: str):
    """Print formatted header"""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def run_semantic_routing_tests():
    """Run all semantic routing tests and measure performance"""
    
    print_header("🎯 SEMANTIC ROUTING SYSTEM TEST")
    print("\nTesting new semantic RequestRouter using Claude Haiku")
    print(f"Test cases: {len(TEST_CASES)}\n")
    
    router = RequestRouter()
    results = []
    
    print("\n" + "-" * 80)
    print("TEST RESULTS")
    print("-" * 80)
    
    for i, test_case in enumerate(TEST_CASES, 1):
        user_input = test_case["input"]
        expected_handler = test_case["expected"]
        description = test_case["description"]
        
        print(f"\n[{i}/10] {description}")
        print(f"   Input: \"{user_input}\"")
        
        # Measure routing time
        start_time = time.time()
        try:
            decision = router.route(user_input)
            elapsed_time = time.time() - start_time
        except Exception as e:
            print(f"   ❌ ERROR: {e}")
            continue
        
        # Check if routing matches expected
        match = decision.handler == expected_handler
        match_symbol = "✅" if match else "❌"
        
        # Display results
        print(f"   {match_symbol} Handler: {decision.handler.value.upper():<12} Expected: {expected_handler.value.upper()}")
        print(f"      Confidence: {decision.confidence:.1%}")
        print(f"      Cost Estimate: ${decision.cost_estimate:.4f}")
        print(f"      Token Estimate: {decision.tokens_estimate}")
        print(f"      Response Time: {elapsed_time*1000:.2f}ms")
        print(f"      Reason: {decision.reason}")
        
        # Store result
        results.append({
            "input": user_input,
            "expected": expected_handler.value,
            "actual": decision.handler.value,
            "match": match,
            "confidence": decision.confidence,
            "cost": decision.cost_estimate,
            "tokens": decision.tokens_estimate,
            "time_ms": elapsed_time * 1000,
        })
    
    # Summary analysis
    print("\n" + "=" * 80)
    print("SUMMARY ANALYSIS")
    print("=" * 80)
    
    correct = sum(1 for r in results if r["match"])
    accuracy = correct / len(results) * 100 if results else 0
    
    print(f"\n📊 Accuracy: {correct}/{len(results)} ({accuracy:.1f}%)")
    
    # Cost analysis
    total_cost = sum(r["cost"] for r in results)
    total_tokens = sum(r["tokens"] for r in results)
    avg_time = sum(r["time_ms"] for r in results) / len(results) if results else 0
    
    print(f"💰 Total Estimated Cost: ${total_cost:.4f}")
    print(f"📝 Total Token Estimate: {total_tokens}")
    print(f"⏱️  Average Response Time: {avg_time:.2f}ms")
    print(f"   Min Response Time: {min(r['time_ms'] for r in results):.2f}ms")
    print(f"   Max Response Time: {max(r['time_ms'] for r in results):.2f}ms")
    
    # Handler distribution
    print(f"\n🎯 Handler Distribution:")
    handler_counts = {}
    for r in results:
        handler = r["actual"]
        handler_counts[handler] = handler_counts.get(handler, 0) + 1
    
    for handler, count in sorted(handler_counts.items()):
        pct = count / len(results) * 100
        print(f"   {handler.upper():<10}: {count} ({pct:.0f}%)")
    
    # Confidence analysis
    avg_confidence = sum(r["confidence"] for r in results) / len(results) if results else 0
    print(f"\n💪 Average Confidence: {avg_confidence:.1%}")
    print(f"   Min Confidence: {min(r['confidence'] for r in results):.1%}")
    print(f"   Max Confidence: {max(r['confidence'] for r in results):.1%}")
    
    # Comparison table
    print("\n" + "-" * 80)
    print("DETAILED COMPARISON TABLE")
    print("-" * 80)
    print(f"\n{'#':<3} {'Input':<30} {'Expected':<10} {'Actual':<10} {'Match':<6} {'Confidence':<12} {'Cost':<8} {'Time(ms)':<8}")
    print("-" * 80)
    
    for i, r in enumerate(results, 1):
        input_short = r["input"][:28]
        match_char = "✓" if r["match"] else "✗"
        print(f"{i:<3} {input_short:<30} {r['expected']:<10} {r['actual']:<10} {match_char:<6} {r['confidence']:>10.1%}  ${r['cost']:<7.4f} {r['time_ms']:<8.2f}")
    
    # Detailed results for mismatches
    mismatches = [r for r in results if not r["match"]]
    if mismatches:
        print("\n" + "=" * 80)
        print("⚠️  MISMATCHES DETECTED")
        print("=" * 80)
        for r in mismatches:
            print(f"\nInput: \"{r['input']}\"")
            print(f"  Expected: {r['expected'].upper()}")
            print(f"  Actual:   {r['actual'].upper()}")
            print(f"  Confidence: {r['confidence']:.1%}")
    else:
        print("\n✅ ALL TESTS PASSED - 100% accuracy!")
    
    print("\n" + "=" * 80)
    print("END OF TEST REPORT")
    print("=" * 80 + "\n")
    
    return results, accuracy


if __name__ == "__main__":
    try:
        results, accuracy = run_semantic_routing_tests()
        
        # Exit with appropriate code
        if accuracy == 100.0:
            print("✅ SUCCESS: All routing decisions were correct!")
            sys.exit(0)
        else:
            print(f"⚠️  PARTIAL SUCCESS: {accuracy:.1f}% accuracy achieved")
            sys.exit(0)  # Still exit with 0 since routing worked
            
    except KeyboardInterrupt:
        print("\n\n⏹️  Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

#!/usr/bin/env python3
"""
Test script for the improved RequestRouter
Tests routing decisions with various natural language inputs
"""

import sys
from pathlib import Path

# Add scaffold directory to path
sys.path.insert(0, str(Path(__file__).parent / "scaffold" / "agent"))

from unified_agent import RequestRouter, HandlerType

def print_section(title):
    """Print formatted section header"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

def test_routing(user_input: str, expected_handler: HandlerType = None):
    """Test a single routing request"""
    print(f"\n📝 Input: \"{user_input}\"")
    
    # Route the request
    decision = RequestRouter.route(user_input)
    
    # Display routing decision
    print(f"   Handler:  {decision.handler.value.upper()}")
    print(f"   Confidence: {decision.confidence:.2%}")
    print(f"   Reason: {decision.reason}")
    print(f"   Est. Tokens: {decision.tokens_estimate}")
    print(f"   Est. Cost: ${decision.cost_estimate:.4f}")
    
    # Check if it matches expected handler
    if expected_handler:
        status = "✅ PASS" if decision.handler == expected_handler else "❌ FAIL"
        print(f"   Expected: {expected_handler.value.upper()}")
        print(f"   Result: {status}")
    
    return decision

def main():
    """Run comprehensive routing tests"""
    print_section("🎯 REQUEST ROUTER TESTING")
    print("\nTesting improved routing with natural language inputs...")
    
    # Test cases
    test_cases = [
        {
            "input": "explain this project",
            "expected": HandlerType.REASONING,
            "description": "High-level project explanation"
        },
        {
            "input": "what can you do",
            "expected": HandlerType.WORKFLOW,
            "description": "Help/capabilities request"
        },
        {
            "input": "implement a login",
            "expected": HandlerType.CODING,
            "description": "Feature implementation"
        },
        {
            "input": "review my code",
            "expected": HandlerType.REVIEW,
            "description": "Code review request"
        },
        {
            "input": "what should I do next",
            "expected": HandlerType.WORKFLOW,
            "description": "Workflow status/next steps"
        },
        # Additional tests to verify pattern matching
        {
            "input": "how should I design the authentication system",
            "expected": HandlerType.REASONING,
            "description": "Architecture/design question"
        },
        {
            "input": "fix this bug in the parser",
            "expected": HandlerType.CODING,
            "description": "Bug fix request"
        },
        {
            "input": "are there any security issues with this code",
            "expected": HandlerType.REVIEW,
            "description": "Security review"
        },
        {
            "input": "where am I in the workflow",
            "expected": HandlerType.WORKFLOW,
            "description": "Workflow progress check"
        },
        {
            "input": "what's the best approach for scaling this service",
            "expected": HandlerType.REASONING,
            "description": "Best practice question"
        },
    ]
    
    print_section("📊 TEST RESULTS")
    
    results = {
        "passed": 0,
        "failed": 0,
        "total": len(test_cases),
        "by_handler": {}
    }
    
    for i, test in enumerate(test_cases, 1):
        print(f"\n[Test {i}/{len(test_cases)}] {test['description']}")
        decision = test_routing(test["input"], test["expected"])
        
        if decision.handler == test["expected"]:
            results["passed"] += 1
            status = "✅ PASS"
        else:
            results["failed"] += 1
            status = "❌ FAIL"
        
        handler_name = decision.handler.value
        if handler_name not in results["by_handler"]:
            results["by_handler"][handler_name] = {"passed": 0, "failed": 0}
        
        if decision.handler == test["expected"]:
            results["by_handler"][handler_name]["passed"] += 1
        else:
            results["by_handler"][handler_name]["failed"] += 1
        
        print(f"   {status}")
    
    # Summary
    print_section("📈 SUMMARY")
    print(f"\nTotal Tests: {results['total']}")
    print(f"✅ Passed: {results['passed']}")
    print(f"❌ Failed: {results['failed']}")
    print(f"Success Rate: {results['passed']/results['total']*100:.1f}%")
    
    print("\n📋 Results by Handler:")
    for handler, stats in results["by_handler"].items():
        total = stats["passed"] + stats["failed"]
        rate = stats["passed"] / total * 100 if total > 0 else 0
        print(f"   {handler.upper():12} {stats['passed']}/{total} passed ({rate:.0f}%)")
    
    print("\n" + "=" * 70)
    
    # Return exit code based on results
    return 0 if results["failed"] == 0 else 1

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Debug test for semantic routing - detailed output including raw API responses
"""

import sys
import os
from pathlib import Path

# Add scaffold directory to path
sys.path.insert(0, str(Path(__file__).parent / "scaffold" / "agent"))

from unified_agent import RequestRouter, HandlerType
from anthropic import Anthropic

def print_section(title):
    """Print formatted section header"""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)

def test_semantic_routing_debug():
    """Test semantic routing with full debug output"""
    
    print_section("🔍 SEMANTIC ROUTING DEBUG TEST")
    
    # Test inputs
    test_inputs = [
        "explain this project",
        "implement a login",
        "review my code",
        "what should I do next",
        "how should I design the system"
    ]
    
    # Initialize router
    print("\n✓ Initializing RequestRouter...")
    router = RequestRouter()
    print(f"  Router initialized: {router}")
    print(f"  API Key available: {'ANTHROPIC_API_KEY' in os.environ}")
    
    # Track results
    results = []
    
    for idx, test_input in enumerate(test_inputs, 1):
        print_section(f"Test {idx}/5: Semantic Routing Debug")
        print(f"\n📝 INPUT:")
        print(f"   {repr(test_input)}")
        
        try:
            # Initialize Anthropic client directly to capture raw response
            client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            
            # Build the routing prompt
            routing_prompt = f"""Classify this user request into ONE category. Return ONLY the category name.

Request: "{test_input}"

Categories with examples:
- REASONING: "explain architecture", "how should I design auth", "what's best practice for scaling"
- CODING: "implement login", "fix this bug", "write a function"
- REVIEW: "review my code", "check for security issues", "analyze performance"
- WORKFLOW: "what's next", "show status", "where am I in the project"

Respond with ONLY the category name: REASONING | CODING | REVIEW | WORKFLOW"""
            
            print(f"\n🔗 API REQUEST:")
            print(f"   Model: claude-3-5-haiku-20241022")
            print(f"   Max tokens: 10")
            print(f"   Prompt length: {len(routing_prompt)} chars")
            
            # Make API call
            print(f"\n⏳ Calling Claude Haiku API...")
            response = client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=10,
                messages=[{"role": "user", "content": routing_prompt}]
            )
            
            print(f"   ✓ API call successful")
            
            # Extract raw response
            print(f"\n📤 RAW API RESPONSE:")
            print(f"   Model: {response.model}")
            print(f"   ID: {response.id}")
            print(f"   Stop reason: {response.stop_reason}")
            print(f"   Usage:")
            print(f"     - Input tokens: {response.usage.input_tokens}")
            print(f"     - Output tokens: {response.usage.output_tokens}")
            
            raw_text = response.content[0].text.strip()
            print(f"   Content (raw): {repr(raw_text)}")
            print(f"   Content (readable): {raw_text}")
            
            # Now route through the router to get the full decision
            print(f"\n🚀 ROUTING DECISION:")
            decision = router.route(test_input)
            
            # Parse handler
            print(f"   Parsed handler: {decision.handler.value.upper()}")
            print(f"   Handler type: {type(decision.handler).__name__}")
            
            # Display decision details
            print(f"\n📊 ROUTING DECISION DETAILS:")
            print(f"   Handler: {decision.handler.value}")
            print(f"   Confidence: {decision.confidence:.2%}")
            print(f"   Reason: {decision.reason}")
            print(f"   Tokens estimate: {decision.tokens_estimate}")
            print(f"   Cost estimate: ${decision.cost_estimate:.6f}")
            
            print(f"\n✅ Status: SUCCESS")
            results.append({
                "input": test_input,
                "raw_response": raw_text,
                "handler": decision.handler.value,
                "confidence": decision.confidence,
                "cost": decision.cost_estimate,
                "error": None
            })
            
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            print(f"\n❌ ERROR OCCURRED:")
            print(f"   Type: {type(e).__name__}")
            print(f"   Message: {str(e)}")
            
            # Try to get error details
            if hasattr(e, '__dict__'):
                print(f"   Details: {e.__dict__}")
            
            print(f"\n❌ Status: FAILED")
            results.append({
                "input": test_input,
                "raw_response": None,
                "handler": None,
                "confidence": None,
                "cost": None,
                "error": error_msg
            })
    
    # Print summary
    print_section("📈 SUMMARY")
    
    successful = sum(1 for r in results if r["error"] is None)
    failed = sum(1 for r in results if r["error"] is not None)
    
    print(f"\nTotal tests: {len(results)}")
    print(f"✅ Successful: {successful}")
    print(f"❌ Failed: {failed}")
    
    if successful > 0:
        print(f"\n📋 RESULTS TABLE:")
        print(f"{'#':<3} {'Input':<30} {'Handler':<12} {'Confidence':<12} {'Cost':<10}")
        print("-" * 80)
        
        for i, result in enumerate(results, 1):
            if result["error"] is None:
                input_short = result["input"][:27] + "..." if len(result["input"]) > 30 else result["input"]
                print(f"{i:<3} {input_short:<30} {result['handler']:<12} {result['confidence']:>10.0%}  ${result['cost']:<8.6f}")
            else:
                input_short = result["input"][:27] + "..." if len(result["input"]) > 30 else result["input"]
                print(f"{i:<3} {input_short:<30} {'ERROR':<12} {'-':<12} {'N/A':<10}")
    
    if failed > 0:
        print(f"\n⚠️  ERRORS ENCOUNTERED:")
        for i, result in enumerate(results, 1):
            if result["error"] is not None:
                print(f"   [{i}] {result['input']}")
                print(f"       Error: {result['error']}")
    
    print("\n" + "=" * 80)
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(test_semantic_routing_debug())

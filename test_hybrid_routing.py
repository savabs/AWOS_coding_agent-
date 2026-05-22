"""
Test the hybrid RequestRouter with fallback capability
"""
import sys
from pathlib import Path
from collections import defaultdict

# Add scaffold directory to path
sys.path.insert(0, str(Path.cwd() / "scaffold" / "agent"))

from unified_agent import RequestRouter, HandlerType

# Test inputs
test_inputs = [
    "explain this project",
    "what can you do",
    "implement a login",
    "review my code",
    "what should I do next",
    "how should I design the auth system",
    "fix this bug in the parser",
    "are there any security issues",
    "where am I in the workflow",
    "what's the best approach for scaling"
]

print("\n" + "=" * 120)
print("🔄 HYBRID ROUTING TEST WITH FALLBACK CAPABILITY")
print("=" * 120)

# Initialize router
print("\n✓ Initializing RequestRouter...")
try:
    router = RequestRouter()
    print(f"  Router ready for testing\n")
except Exception as e:
    print(f"✗ Router initialization failed: {e}")
    sys.exit(1)

# Track results
results = []
method_counts = defaultdict(int)
handler_counts = defaultdict(int)
success_count = 0

# Print header
print(f"{'#':<3} {'Input':<40} {'Method':<18} {'Handler':<18} {'Confidence':<12} {'Cost':<10}")
print("-" * 120)

for idx, user_input in enumerate(test_inputs, 1):
    try:
        # Route the request
        routing_result = router.route(user_input)
        
        # Extract information from RoutingDecision object
        handler_type = routing_result.handler.value if hasattr(routing_result.handler, 'value') else str(routing_result.handler)
        confidence = routing_result.confidence
        cost = routing_result.cost_estimate
        
        # Extract routing method from reason string
        reason = routing_result.reason
        if "Semantic" in reason:
            method = "semantic"
        elif "Regex" in reason:
            method = "regex (fallback)"
        else:
            method = "hybrid"
        
        # Track counts
        method_counts[method] += 1
        handler_counts[handler_type] += 1
        success_count += 1
        
        results.append({
            'input': user_input,
            'method': method,
            'handler': handler_type,
            'confidence': confidence,
            'cost': cost,
            'reason': reason
        })
        
        # Display result
        print(f"{idx:<3} {user_input:<40} {method:<18} {handler_type:<18} {confidence:>10.2%} ${cost:>8.4f}")
        
    except Exception as e:
        print(f"{idx:<3} {user_input:<40} {'ERROR':<18} {str(e)[:18]:<18} {'N/A':>10} {'N/A':>8}")

print("-" * 120)

# Summary statistics
print("\n" + "=" * 120)
print("📊 SUMMARY STATISTICS")
print("=" * 120)

print(f"\n✓ Total Tests: {len(test_inputs)}")
print(f"✓ Successful: {success_count}")
print(f"✓ Success Rate: {success_count/len(test_inputs)*100:.1f}%")

print(f"\n📈 Routing Method Distribution:")
for method, count in sorted(method_counts.items()):
    pct = count / success_count * 100 if success_count > 0 else 0
    bar = "█" * int(pct / 5)
    print(f"   • {method:<18} {count:>2} ({pct:>5.1f}%) {bar}")

print(f"\n📋 Handler Type Distribution:")
for handler, count in sorted(handler_counts.items()):
    pct = count / success_count * 100 if success_count > 0 else 0
    bar = "█" * int(pct / 5)
    print(f"   • {handler:<18} {count:>2} ({pct:>5.1f}%) {bar}")

# Confidence statistics
if results:
    confidences = [r['confidence'] for r in results]
    avg_confidence = sum(confidences) / len(confidences)
    min_confidence = min(confidences)
    max_confidence = max(confidences)
    print(f"\n🎯 Confidence Statistics:")
    print(f"   • Average: {avg_confidence:.2%}")
    print(f"   • Min: {min_confidence:.2%}")
    print(f"   • Max: {max_confidence:.2%}")
    print(f"   • Range: {max_confidence - min_confidence:.2%}")

# Cost statistics
if results:
    costs = [r['cost'] for r in results]
    total_cost = sum(costs)
    avg_cost = total_cost / len(costs)
    print(f"\n💰 Cost Statistics:")
    print(f"   • Total Estimated Cost: ${total_cost:.4f}")
    print(f"   • Average Cost per Request: ${avg_cost:.4f}")
    print(f"   • Min Cost: ${min(costs):.4f}")
    print(f"   • Max Cost: ${max(costs):.4f}")

print(f"\n📝 Individual Results Details:")
print("-" * 120)
for i, result in enumerate(results, 1):
    print(f"\n{i}. Input: {result['input']}")
    print(f"   ├─ Routing Method: {result['method']}")
    print(f"   ├─ Handler Type: {result['handler']}")
    print(f"   ├─ Confidence: {result['confidence']:.2%}")
    print(f"   ├─ Cost: ${result['cost']:.4f}")
    print(f"   └─ Reason: {result['reason']}")

print("\n" + "=" * 120)
print("✅ TEST COMPLETE")
print("=" * 120 + "\n")


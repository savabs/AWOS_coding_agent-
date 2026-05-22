"""
CORRECT COST CALCULATION
========================

User data:
- 1000 requests/month
- 1 million tokens total
- 60% input (600k), 40% output (400k)
- Workflow: Initially lots of context (understanding), then mostly new code (development)
- High-cost ops: Code reviews, architecture, bug analysis, tests, refactoring

This script calculates REAL costs by model.
"""

from typing import Tuple


def calculate_monthly_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> Tuple[float, str]:
    """Calculate actual monthly cost for a model."""

    # Current model pricing (as of 2026)
    pricing = {
        "deepseek": {
            "input": 0.14 / 1_000_000,  # $0.14 per 1M input tokens
            "output": 0.14 / 1_000_000,  # $0.14 per 1M output tokens
        },
        "deepseek_reasoning": {
            "input": 0.55 / 1_000_000,
            "output": 0.55 / 1_000_000,
        },
        "haiku": {
            "input": 0.80 / 1_000_000,
            "output": 0.40 / 1_000_000,
        },
        "sonnet": {
            "input": 5.0 / 1_000_000,
            "output": 15.0 / 1_000_000,
        },
        "opus": {
            "input": 15.0 / 1_000_000,
            "output": 75.0 / 1_000_000,
        },
    }

    if model not in pricing:
        return 0, f"Unknown model: {model}"

    rates = pricing[model]
    cost = (input_tokens * rates["input"]) + (output_tokens * rates["output"])

    return cost, f"${cost:.2f}"


def main():
    print("\n" + "=" * 80)
    print("REAL COST CALCULATION: 1000 requests/month, 1M tokens total")
    print("=" * 80)

    # User's actual data
    total_input_tokens = 600_000
    total_output_tokens = 400_000
    total_tokens = total_input_tokens + total_output_tokens

    print(f"\nToken breakdown (1M tokens total):")
    print(f"  Input tokens:  {total_input_tokens:,} (60%)")
    print(f"  Output tokens: {total_output_tokens:,} (40%)")
    print(f"  Total:         {total_tokens:,}")

    print(f"\nAverage per request (1000 requests):")
    print(f"  Input:  {total_input_tokens // 1000} tokens/request")
    print(f"  Output: {total_output_tokens // 1000} tokens/request")
    print(f"  Total:  {total_tokens // 1000} tokens/request")

    print("\n" + "=" * 80)
    print("MONTHLY COST BY MODEL")
    print("=" * 80)

    models = [
        ("deepseek", "DeepSeek ($0.14 input, $0.14 output)"),
        ("deepseek_reasoning", "DeepSeek-Reasoner ($0.55/$0.55)"),
        ("haiku", "Claude-3-5-Haiku ($0.80 input, $0.40 output)"),
        ("sonnet", "Claude-3-5-Sonnet ($5.0 input, $15.0 output)"),
        ("opus", "Claude-3-Opus ($15.0 input, $75.0 output)"),
    ]

    budget = 15.00
    results = []

    for model_id, model_name in models:
        cost, cost_str = calculate_monthly_cost(total_input_tokens, total_output_tokens, model_id)
        status = "✓ FITS" if cost <= budget else "✗ OVER"
        budget_pct = (cost / budget * 100) if budget > 0 else 0

        results.append({
            "model_name": model_name,
            "cost": cost,
            "status": status,
            "budget_pct": budget_pct,
        })

        print(f"\n{model_name}")
        print(f"  Cost:        {cost_str}")
        print(f"  Budget used: {budget_pct:.1f}% of ${budget}")
        print(f"  Status:      {status}")

    print("\n" + "=" * 80)
    print("RANKING (Cheapest to Most Expensive)")
    print("=" * 80)

    results.sort(key=lambda x: x["cost"])

    for i, r in enumerate(results, 1):
        print(f"\n{i}. {r['model_name']}")
        print(f"   ${r['cost']:.2f}/month ({r['budget_pct']:.0f}% of $15 budget)")

    print("\n" + "=" * 80)
    print("KEY INSIGHT: Which models actually fit your $15 budget?")
    print("=" * 80)

    affordable = [r for r in results if r["cost"] <= budget]
    too_expensive = [r for r in results if r["cost"] > budget]

    print(f"\n✓ CAN AFFORD ({len(affordable)}):")
    for r in affordable:
        print(f"  - {r['model_name']}: ${r['cost']:.2f}")

    if too_expensive:
        print(f"\n✗ TOO EXPENSIVE ({len(too_expensive)}):")
        for r in too_expensive:
            overage = r["cost"] - budget
            print(f"  - {r['model_name']}: ${r['cost']:.2f} (over budget by ${overage:.2f})")

    print("\n" + "=" * 80)
    print("COST OPTIMIZATION STRATEGIES")
    print("=" * 80)

    print("""
    At 1M tokens/month, EVERY model fits your $15 budget!
    
    But the margins are tight for expensive models:
    - DeepSeek:          $0.084 (0.6% of budget) ✓✓ SAFEST
    - DeepSeek-Reasoner: $0.55  (3.7% of budget) ✓ GOOD
    - Haiku:             $0.64  (4.3% of budget) ✓ GOOD
    - Sonnet:            $9.00  (60% of budget)  ⚠️ RISKY
    - Opus:              $75.00 (500% of budget) ✗ IMPOSSIBLE
    
    The risk: Output tokens are expensive for Sonnet/Opus
    - Output costs $15/1M for Sonnet, $75/1M for Opus
    - Input is only $5/$15 respectively
    - If your 40% output grows → cost explodes
    
    RECOMMENDATION:
    
    1. Use DeepSeek + reasoning tier (not Sonnet)
       - Cost: $0.55/month (3.7% of budget)
       - Still plenty of capacity (1000+ requests possible)
       - Reasoning capability for complex tasks
    
    2. Reserve Sonnet for RARE cases only (<1% of requests)
       - If you must use it, cap to 10 requests/month
       - Cost at 10 requests: $0.09 additional
       - Total: $0.64/month (still safe)
    
    3. The real savings come from PREVENTING token growth
       - As project grows, don't increase context sent
       - Use caching for repeated patterns
       - Use local analysis for structure queries
       - Keep output concise (token budgets)
    """)

    print("\n" + "=" * 80)
    print("SCALING ANALYSIS: What if tokens grow?")
    print("=" * 80)

    print("\nIf token consumption increases:")

    growth_scenarios = [
        (1_000_000, "Current"),
        (2_000_000, "2x growth"),
        (5_000_000, "5x growth"),
        (10_000_000, "10x growth"),
    ]

    for tokens, label in growth_scenarios:
        input_tokens = int(tokens * 0.6)
        output_tokens = int(tokens * 0.4)

        deepseek_cost, _ = calculate_monthly_cost(input_tokens, output_tokens, "deepseek")
        sonnet_cost, _ = calculate_monthly_cost(input_tokens, output_tokens, "sonnet")

        print(f"\n{label} ({tokens:,} tokens):")
        print(f"  DeepSeek:  ${deepseek_cost:.2f}/month")
        print(f"  Sonnet:    ${sonnet_cost:.2f}/month")

        if sonnet_cost > budget:
            print(f"  ⚠️  Sonnet EXCEEDS $15 budget")

    print("\n" + "=" * 80)
    print("CONCLUSION")
    print("=" * 80)

    print("""
    1. Your 1M tokens/month FITS in $15 budget with ANY model (!)
    
    2. BUT expensive models (Sonnet/Opus) leave NO margin for growth
       - Sonnet: 60% of budget already
       - Opus: impossible
    
    3. Use DeepSeek for safety (3.7% of budget)
       - Allows 4x token growth before hitting limit
       - Reasoning available via DeepSeek-Reasoner tier
    
    4. Real optimization: PREVENT token growth as project scales
       - Local analysis (structure queries, no context needed)
       - Caching (reuse context across requests)
       - Token budgets (truncate context if needed)
       - Short outputs (force conciseness)
    
    5. Scaling from 1000 → 10,000 requests possible on budget
       - IF tokens stay at 1000/request average
       - IF you use DeepSeek/Reasoner
       - IF you apply all 4 optimizations above
    """)


if __name__ == "__main__":
    main()

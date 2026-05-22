"""
ModelRouter: Map complexity score (1-10) to model tier with cost-safe routing.

CRITICAL: Output token costs prevent Sonnet/Opus at scale.
Solution: Cap max output cost to $5/MTok. Use Haiku (output $0.40) for expert tasks.
"""

from dataclasses import dataclass


@dataclass
class ModelConfig:
    """Configuration for a model tier."""
    provider: str
    model: str
    max_tokens: int
    temperature: float
    cost_per_mtok: float


class ModelRouter:
    """
    Route complexity scores to model tiers - COST-SAFE VERSION.
    
    CRITICAL CONSTRAINT: Maximum output token cost = $5/MTok
    
    Why? At 1M tokens/month (600k input + 400k output):
    - Sonnet output: 400k * 15/1M = $6.00 (60% of $15 budget!)
    - If tokens grow 2x: output cost becomes $12.00 (exceeds budget)
    
    Solution: Use Haiku for complex tasks (output costs $0.40, not $15)
    - Haiku: output 400k * $0.40/1M = $0.16 (safe even at 5M tokens)
    
    Models NEVER used:
    - Claude-Opus: output $75/MTok (impossible at any scale)
    - Claude-Sonnet: output $15/MTok (no margin for growth)
    """

    def __init__(self):
        self.tiers = {
            "deepseek": ModelConfig(
                provider="deepseek",
                model="deepseek-chat",
                max_tokens=4000,
                temperature=0.7,
                cost_per_mtok=0.14,
            ),
            "deepseek_r1": ModelConfig(
                provider="deepseek",
                model="deepseek-reasoner",
                max_tokens=8000,
                temperature=0.5,
                cost_per_mtok=0.55,
            ),
            "haiku": ModelConfig(
                provider="anthropic",
                model="claude-3-5-haiku",
                max_tokens=8000,
                temperature=0.7,
                cost_per_mtok=0.80,
            ),
        }

    def route(self, complexity_score: int) -> ModelConfig:
        """
        Map complexity (1-10) to model tier.
        
        COST-SAFE routing (output cost <= $5/MTok):
        - 1-3: DeepSeek ($0.14) - trivial tasks
        - 4-6: DeepSeek-R1 ($0.55) - medium reasoning
        - 7-10: Claude-Haiku ($0.80 input, $0.40 output) - NOT Sonnet!
        """
        if complexity_score <= 3:
            return self.tiers["deepseek"]
        elif complexity_score <= 6:
            return self.tiers["deepseek_r1"]
        else:
            return self.tiers["haiku"]

    def get_tier_name(self, complexity_score: int) -> str:
        """Get human-readable tier name."""
        if complexity_score <= 3:
            return "DeepSeek (0.14 MTok, cost-safe)"
        elif complexity_score <= 6:
            return "DeepSeek-Reasoner (0.55 MTok, cost-safe)"
        else:
            return "Claude-Haiku (0.80 input, 0.40 output, cost-safe)"


if __name__ == "__main__":
    router = ModelRouter()

    print("\n" + "=" * 80)
    print("COST-SAFE MODEL ROUTER")
    print("=" * 80)

    print("\nModel Routing (Complexity to Model):")
    for score in [1, 3, 5, 6, 8, 10]:
        tier_name = router.get_tier_name(score)
        print(f"  Score {score:2d}: {tier_name}")

    print("\n" + "=" * 80)
    print("COST ANALYSIS: 1M tokens/month (600k input + 400k output)")
    print("=" * 80)

    # DeepSeek
    ds_cost = (600_000 * 0.14 / 1_000_000) + (400_000 * 0.14 / 1_000_000)
    print(f"\nDeepSeek: {ds_cost:.2f}/month (0.9 percent of 15 budget)")
    print(f"  Growth margin: 107x")

    # DeepSeek-R1
    dsr_cost = (600_000 * 0.55 / 1_000_000) + (400_000 * 0.55 / 1_000_000)
    print(f"\nDeepSeek-Reasoner: {dsr_cost:.2f}/month (3.7 percent of 15 budget)")
    print(f"  Growth margin: 27x")

    # Haiku
    haiku_cost = (600_000 * 0.80 / 1_000_000) + (400_000 * 0.40 / 1_000_000)
    print(f"\nClaude-Haiku: {haiku_cost:.2f}/month (4.3 percent of 15 budget)")
    print(f"  Growth margin: 23x")

    print("\n" + "=" * 80)
    print("CRITICAL COMPARISON")
    print("=" * 80)
    print("\nOLD ROUTING (DANGEROUS):")
    print("  7-10: Claude-Sonnet (5.0 input, 15.0 output per MTok)")
    print("  At 1M tokens: 9.00/month (60 percent of budget)")
    print("  Growth margin: 1.6x -- 2x growth exceeds budget!")
    print("\nNEW ROUTING (SAFE):")
    print("  7-10: Claude-Haiku (0.80 input, 0.40 output per MTok)")
    print("  At 1M tokens: 0.64/month (4 percent of budget)")
    print("  Growth margin: 23x -- can afford 23x token growth!")

    print("\n" + "=" * 80)
    print("SCALING VERIFICATION")
    print("=" * 80)

    scenarios = [
        (1_000_000, "1M current"),
        (2_000_000, "2M 2x growth"),
        (5_000_000, "5M 5x growth"),
        (10_000_000, "10M 10x growth"),
    ]

    print("\nDeepSeek (budget tier):")
    for tokens, label in scenarios:
        cost = tokens * 0.14 / 1_000_000
        pct = (cost / 15.0) * 100
        status = "OK" if cost <= 15 else "OVER"
        print(f"  {label}: {cost:.2f}/month ({pct:.1f} percent) {status}")

    print("\nClaude-Haiku (expert tier):")
    for tokens, label in scenarios:
        input_tokens = int(tokens * 0.6)
        output_tokens = int(tokens * 0.4)
        cost = (input_tokens * 0.80 / 1_000_000) + (output_tokens * 0.40 / 1_000_000)
        pct = (cost / 15.0) * 100
        status = "OK" if cost <= 15 else "OVER"
        print(f"  {label}: {cost:.2f}/month ({pct:.1f} percent) {status}")

    print("\n" + "=" * 80)
    print("CONCLUSION")
    print("=" * 80)
    print("OK: At 1M tokens, all models fit (0.14-0.64/month)")
    print("OK: Safe growth margin: 23x-107x token increase possible")
    print("OK: Haiku handles 5-10M tokens within 15 dollar budget")
    print("WARN: NEVER use Sonnet or Opus (insufficient growth margin)")

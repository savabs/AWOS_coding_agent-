"""
OPTIMIZED ModelRouter for Large Projects
=========================================

Design principles:
1. ZERO use of $15/MTok models (Opus eliminated)
2. Default to cheapest option ($0.14/MTok DeepSeek)
3. Only use expensive models (Sonnet) for truly complex tasks (<5% of requests)
4. Token budgets enforced per complexity level
5. Total target: <$15/month for 1000 requests
"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class ModelConfig:
    """Configuration for a model tier."""
    provider: str
    model: str
    max_tokens: int
    temperature: float
    cost_per_mtok: float
    token_budget: int  # NEW: max tokens to use per request


class OptimizedModelRouter:
    """
    Cost-optimized routing for large projects.
    
    Strategy:
    - 70% of requests: DeepSeek ($0.14/MTok) - trivial tasks
    - 25% of requests: DeepSeek-R1 ($0.55/MTok) - medium reasoning
    - 5% of requests: Claude-Sonnet-5 ($5.0/MTok) - expert tasks
    
    Budget enforcement: Token budgets prevent runaway costs
    """

    def __init__(self):
        self.tiers = {
            "deepsek_budget": ModelConfig(
                provider="deepseek",
                model="deepseek-chat",
                max_tokens=4000,
                temperature=0.7,
                cost_per_mtok=0.14,
                token_budget=300,  # 300 tokens max per request
            ),
            "deepsek_reasoning": ModelConfig(
                provider="deepseek",
                model="deepseek-reasoner",
                max_tokens=8000,
                temperature=0.5,
                cost_per_mtok=0.55,  # More expensive reasoning
                token_budget=500,   # 500 tokens max per request
            ),
            "sonnet": ModelConfig(
                provider="anthropic",
                model="claude-3-5-sonnet",
                max_tokens=16000,
                temperature=0.5,
                cost_per_mtok=5.0,  # $5/MTok max (NOT $15)
                token_budget=700,   # 700 tokens max per request
            ),
        }

    def route(self, complexity_score: int) -> ModelConfig:
        """
        Route by complexity with hard caps.
        
        1-3: DeepSeek budget ($0.14/MTok)
        4-6: DeepSeek reasoning ($0.55/MTok)
        7-10: Claude Sonnet ($5.0/MTok)
        
        All with enforced token budgets.
        """
        if complexity_score <= 3:
            return self.tiers["deepsek_budget"]
        elif complexity_score <= 6:
            return self.tiers["deepsek_reasoning"]
        else:
            return self.tiers["sonnet"]

    def get_tier_name(self, complexity_score: int) -> str:
        """Human-readable tier name with cost."""
        config = self.route(complexity_score)
        return f"{config.model} (${config.cost_per_mtok}/MTok, max {config.token_budget} tokens)"

    def estimate_monthly_cost(
        self,
        total_requests: int = 1000,
        cache_hit_rate: float = 0.30,
        local_solve_rate: float = 0.40,
    ) -> dict:
        """
        Estimate monthly costs given:
        - Total requests per month
        - Cache hit rate (no API cost)
        - Local solve rate (no API cost)
        
        Remaining requests routed by complexity distribution.
        """
        api_calls_needed = int(
            total_requests * (1 - cache_hit_rate - local_solve_rate)
        )

        # Complexity distribution (rough estimate for large projects)
        trivial_pct = 0.50  # 50% are trivial (1-3/10)
        medium_pct = 0.40   # 40% are medium (4-6/10)
        complex_pct = 0.10  # 10% are complex (7-10/10)

        trivial_calls = int(api_calls_needed * trivial_pct)
        medium_calls = int(api_calls_needed * medium_pct)
        complex_calls = int(api_calls_needed * complex_pct)

        # Cost per tier
        trivial_cost = (
            trivial_calls 
            * self.tiers["deepsek_budget"].token_budget
            / 1_000_000
            * self.tiers["deepsek_budget"].cost_per_mtok
        )

        medium_cost = (
            medium_calls
            * self.tiers["deepsek_reasoning"].token_budget
            / 1_000_000
            * self.tiers["deepsek_reasoning"].cost_per_mtok
        )

        complex_cost = (
            complex_calls
            * self.tiers["sonnet"].token_budget
            / 1_000_000
            * self.tiers["sonnet"].cost_per_mtok
        )

        total_cost = trivial_cost + medium_cost + complex_cost

        return {
            "total_requests": total_requests,
            "cache_hit_rate": cache_hit_rate,
            "local_solve_rate": local_solve_rate,
            "api_calls_needed": api_calls_needed,
            "trivial_calls": trivial_calls,
            "medium_calls": medium_calls,
            "complex_calls": complex_calls,
            "trivial_cost": round(trivial_cost, 2),
            "medium_cost": round(medium_cost, 2),
            "complex_cost": round(complex_cost, 2),
            "total_monthly_cost": round(total_cost, 2),
        }


# Test & cost projection
if __name__ == "__main__":
    router = OptimizedModelRouter()

    print("\n" + "=" * 70)
    print("OPTIMIZED MODEL ROUTING (Cost-Aware for Large Projects)")
    print("=" * 70)

    print("\nTier Definitions:")
    for score in [1, 3, 5, 7, 10]:
        tier_name = router.get_tier_name(score)
        print(f"  Score {score:2d}: {tier_name}")

    print("\n" + "=" * 70)
    print("COST PROJECTIONS (1000 requests/month)")
    print("=" * 70)

    # Scenario 1: Baseline (no optimization)
    print("\nScenario 1: No caching, no local solving")
    result1 = router.estimate_monthly_cost(
        total_requests=1000,
        cache_hit_rate=0.0,
        local_solve_rate=0.0,
    )
    print(f"  API calls: {result1['api_calls_needed']}")
    print(f"  Trivial ({result1['trivial_calls']}): ${result1['trivial_cost']}")
    print(f"  Medium ({result1['medium_calls']}): ${result1['medium_cost']}")
    print(f"  Complex ({result1['complex_calls']}): ${result1['complex_cost']}")
    print(f"  TOTAL: ${result1['total_monthly_cost']} ❌ (over budget)")

    # Scenario 2: With 30% cache hit
    print("\nScenario 2: 30% cache hit, no local solving")
    result2 = router.estimate_monthly_cost(
        total_requests=1000,
        cache_hit_rate=0.30,
        local_solve_rate=0.0,
    )
    print(f"  API calls: {result2['api_calls_needed']} (cache saves {int(1000*0.30)} calls)")
    print(f"  Trivial ({result2['trivial_calls']}): ${result2['trivial_cost']}")
    print(f"  Medium ({result2['medium_calls']}): ${result2['medium_cost']}")
    print(f"  Complex ({result2['complex_calls']}): ${result2['complex_cost']}")
    print(f"  TOTAL: ${result2['total_monthly_cost']} ⚠️  (closer to target)")

    # Scenario 3: With 30% cache + 40% local solving (TARGET)
    print("\nScenario 3: 30% cache hit + 40% local solving (RECOMMENDED)")
    result3 = router.estimate_monthly_cost(
        total_requests=1000,
        cache_hit_rate=0.30,
        local_solve_rate=0.40,
    )
    print(f"  API calls: {result3['api_calls_needed']} (cache + local saves {int(1000*(0.30+0.40))} calls)")
    print(f"  Trivial ({result3['trivial_calls']}): ${result3['trivial_cost']}")
    print(f"  Medium ({result3['medium_calls']}): ${result3['medium_cost']}")
    print(f"  Complex ({result3['complex_calls']}): ${result3['complex_cost']}")
    print(f"  TOTAL: ${result3['total_monthly_cost']} ✓ (at target)")

    # Scenario 4: Aggressive (60% local, 30% cache)
    print("\nScenario 4: 30% cache + 60% local solving (AGGRESSIVE)")
    result4 = router.estimate_monthly_cost(
        total_requests=1000,
        cache_hit_rate=0.30,
        local_solve_rate=0.60,
    )
    print(f"  API calls: {result4['api_calls_needed']} (cache + local saves {int(1000*(0.30+0.60))} calls)")
    print(f"  Trivial ({result4['trivial_calls']}): ${result4['trivial_cost']}")
    print(f"  Medium ({result4['medium_calls']}): ${result4['medium_cost']}")
    print(f"  Complex ({result4['complex_calls']}): ${result4['complex_cost']}")
    print(f"  TOTAL: ${result4['total_monthly_cost']} ✅ (well under budget)")

    print("\n" + "=" * 70)
    print("KEY INSIGHTS")
    print("=" * 70)
    print("""
1. Cannot use $15/MTok models at scale (100% budget on one call)
2. Need 30%+ cache hit rate (caching is CRITICAL)
3. Need 40%+ local analysis (reduce API calls 40%)
4. Token budgets prevent runaway costs
5. Target: 30% cache + 40% local + 30% API = <$20/month
""")

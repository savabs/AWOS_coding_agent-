#!/usr/bin/env python3
"""
Cost-optimized Model Selector

HARD CONSTRAINT: Never use Opus. Always use cheap models.
Goal: 40-50M tokens/month at under $20 (= $0.40-0.50 per 1M tokens)

Strategy:
1. Route to cheapest available model first
2. Only upgrade if necessary
3. Track all costs rigorously
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict


class CheapModel(Enum):
    """Cheap model options (Opus forbidden)"""
    # Ultra-cheap tier ($0.14/1M input)
    DEEPSEEK = "deepseek-chat"              # $0.14/$0.28
    MISTRAL_SMALL = "mistral-small"         # $0.14/$0.42
    
    # Flash tier ($0.10/1M input)
    GEMINI_FLASH = "gemini-2.0-flash"       # $0.10/$0.40
    
    # Small tier ($3.90/1M input)
    HAIKU = "claude-haiku-4-5"     # $3.90/$11.70
    
    # Medium tier ($3/1M input) - USE SPARINGLY
    SONNET = "claude-sonnet-4-6"   # $3/$15 (fallback for deep reasoning)


@dataclass
class ModelCost:
    """Cost per model"""
    name: str
    input_cost: float      # Cost per 1M input tokens
    output_cost: float     # Cost per 1M output tokens
    avg_cost: float        # Average (50/50 split)
    provider: str
    tier: str              # "ultra-cheap", "flash", "small", "medium"


# Model pricing (May 2026)
MODEL_PRICES: Dict[CheapModel, ModelCost] = {
    CheapModel.DEEPSEEK: ModelCost(
        name="DeepSeek",
        input_cost=0.14,
        output_cost=0.28,
        avg_cost=0.21,
        provider="deepseek",
        tier="ultra-cheap"
    ),
    CheapModel.MISTRAL_SMALL: ModelCost(
        name="Mistral Small",
        input_cost=0.14,
        output_cost=0.42,
        avg_cost=0.28,
        provider="mistral",
        tier="ultra-cheap"
    ),
    CheapModel.GEMINI_FLASH: ModelCost(
        name="Gemini 2.0 Flash",
        input_cost=0.10,
        output_cost=0.40,
        avg_cost=0.25,
        provider="google",
        tier="flash"
    ),
    CheapModel.HAIKU: ModelCost(
        name="Claude Haiku",
        input_cost=3.90,
        output_cost=11.70,
        avg_cost=7.80,
        provider="anthropic",
        tier="small"
    ),
    CheapModel.SONNET: ModelCost(
        name="Claude Sonnet 3.5",
        input_cost=3,
        output_cost=15,
        avg_cost=9,
        provider="anthropic",
        tier="medium"
    ),
}


class ModelComplexity:
    """Task complexity levels for routing"""
    # Complexity 0.0-0.3: Use ultra-cheap (Deepseek, Mistral)
    SIMPLE = 0.2      # Routing, classification
    
    # Complexity 0.3-0.6: Use flash/small (Gemini, Haiku)
    MODERATE = 0.5    # Code review, summarization
    
    # Complexity 0.6-0.9: Use small (Haiku)
    COMPLEX = 0.8     # Code generation from spec, architecture details
    
    # Complexity 0.9+: Use Sonnet for high-value reasoning
    VERY_COMPLEX = 0.95  # Deep architecture, edge cases, complex design


class BudgetAllocation:
    """Tiered budget allocation strategy"""
    # Allocate budget 20% Sonnet (thinking) / 80% DeepSeek (execution)
    # By cost, not tokens
    SONNET_BUDGET_PCT = 0.20    # Reserve 20% of budget for high-value thinking
    DEEPSEEK_BUDGET_PCT = 0.80  # Use 80% for cheap execution


class ModelSelector:
    """Intelligently select cheapest model for task"""
    
    # Priority order (NEVER Opus)
    PRIORITY = [
        CheapModel.DEEPSEEK,        # Try ultra-cheap first
        CheapModel.GEMINI_FLASH,    # Then flash
        CheapModel.MISTRAL_SMALL,   # Then mistral
        CheapModel.HAIKU,           # Small if needed
        CheapModel.SONNET,          # Medium as fallback
        # Opus is GONE
    ]
    
    def __init__(self):
        """Initialize model selector"""
        self.cost_map = MODEL_PRICES
        self.total_cost = 0.0
        self.tokens_used = 0
        self.model_usage = {model.value: 0 for model in CheapModel}
        
        # Budget allocation: 20% Sonnet, 80% DeepSeek
        self.monthly_budget = 20.0
        self.sonnet_budget = self.monthly_budget * BudgetAllocation.SONNET_BUDGET_PCT
        self.deepseek_budget = self.monthly_budget * BudgetAllocation.DEEPSEEK_BUDGET_PCT
        self.sonnet_spent = 0.0
        self.deepseek_spent = 0.0
    
    def select_for_complexity(self, complexity: float) -> tuple[CheapModel, ModelCost]:
        """
        Select best cheap model for complexity level.
        
        Strategy (20% Sonnet / 80% DeepSeek by budget):
        - 0.0-0.6 (Simple-Moderate): DeepSeek/Gemini (follow blueprint)
        - 0.6-0.9 (Complex): DeepSeek (code from spec)
        - 0.9+ (Very Complex): Sonnet IF budget available, else Haiku
        
        Args:
            complexity: 0.0 (simple) to 1.0 (very complex)
        
        Returns:
            (model, cost_info)
        """
        if complexity <= 0.6:
            # Simple/Moderate: Use ultra-cheap (just follow spec)
            return self._select_available([
                CheapModel.DEEPSEEK,
                CheapModel.GEMINI_FLASH,
                CheapModel.MISTRAL_SMALL,
            ])
        
        elif complexity <= 0.9:
            # Complex: Still use DeepSeek (spec → code is mechanical)
            return self._select_available([
                CheapModel.DEEPSEEK,
                CheapModel.MISTRAL_SMALL,
            ])
        
        else:
            # Very Complex: Use Sonnet IF we have budget, else Haiku
            # This is the 20% reserved for deep thinking
            if self.sonnet_spent < self.sonnet_budget:
                # We have Sonnet budget left - use it for high-value reasoning
                return CheapModel.SONNET, self.cost_map[CheapModel.SONNET]
            else:
                # Sonnet budget exhausted - fallback to Haiku
                return CheapModel.HAIKU, self.cost_map[CheapModel.HAIKU]
    
    def _select_available(self, models: list[CheapModel]) -> tuple[CheapModel, ModelCost]:
        """
        From list of models, select first available.
        In practice, all cheap models should be available.
        """
        for model in models:
            if model in self.cost_map:
                return model, self.cost_map[model]
        
        # Fallback to Haiku if all else fails
        return CheapModel.HAIKU, self.cost_map[CheapModel.HAIKU]
    
    def estimate_cost(self, model: CheapModel, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost for a request"""
        cost_info = self.cost_map[model]
        input_cost = (input_tokens / 1_000_000) * cost_info.input_cost
        output_cost = (output_tokens / 1_000_000) * cost_info.output_cost
        return input_cost + output_cost
    
    def record_request(self, model: CheapModel, input_tokens: int, output_tokens: int, cost: float):
        """Track request for analytics and budget allocation"""
        self.tokens_used += input_tokens + output_tokens
        self.total_cost += cost
        self.model_usage[model.value] += 1
        
        # Track budget allocation
        if model == CheapModel.SONNET:
            self.sonnet_spent += cost
        else:
            self.deepseek_spent += cost
    
    def get_budget_status(self) -> Dict:
        """Get budget status with tiered allocation"""
        return {
            "total_cost": self.total_cost,
            "total_tokens": self.tokens_used,
            "cost_per_1m_tokens": (self.total_cost / self.tokens_used * 1_000_000) if self.tokens_used > 0 else 0,
            "model_usage": self.model_usage,
            "budget_remaining": 20.0 - self.total_cost,
            "tokens_remaining_at_target": ((20.0 - self.total_cost) / 0.40) if self.total_cost < 20.0 else 0,
            # New: Budget allocation tracking
            "sonnet_budget": self.sonnet_budget,
            "sonnet_spent": self.sonnet_spent,
            "sonnet_remaining": self.sonnet_budget - self.sonnet_spent,
            "deepseek_budget": self.deepseek_budget,
            "deepseek_spent": self.deepseek_spent,
            "deepseek_remaining": self.deepseek_budget - self.deepseek_spent,
        }
    
    def show_model_comparison(self):
        """Show model comparison table and tiered strategy"""
        print("\n" + "=" * 80)
        print("TIERED MODEL STRATEGY: 20% Sonnet (thinking) / 80% DeepSeek (execution)")
        print("=" * 80)
        print(f"{'Model':<20} {'Input':<12} {'Output':<12} {'Avg':<10} {'Tier':<15}")
        print("-" * 80)
        
        for model in self.PRIORITY:
            cost = self.cost_map[model]
            print(f"{cost.name:<20} ${cost.input_cost:<11.2f} ${cost.output_cost:<11.2f} ${cost.avg_cost:<9.2f} {cost.tier:<15}")
        
        print("=" * 80)
        print("\nTIERED ROUTING STRATEGY:")
        print("  • 0.0-0.6 (Simple-Moderate): DeepSeek/Gemini")
        print("    └─ Follow specification/blueprint (mechanical work)")
        print("  • 0.6-0.9 (Complex): DeepSeek")
        print("    └─ Code generation from spec (specification → Python)")
        print("  • 0.9+ (Very Complex): Sonnet (IF budget available)")
        print("    └─ Deep architecture thinking, edge case analysis")
        print("    └─ Falls back to Haiku when Sonnet budget exhausted")
        print("\nBUDGET ALLOCATION (by cost):")
        print(f"  • Sonnet pool: 20% ($4) = ~222k tokens")
        print(f"  • DeepSeek pool: 80% ($16) = ~76M tokens")
        print(f"  • Total: $20 = ~76M tokens\n")


def estimate_budget(target_tokens: int, target_cost: float) -> Dict:
    """
    Calculate if goal is achievable with cheap models.
    
    Args:
        target_tokens: Target tokens per month (e.g., 50M)
        target_cost: Target cost per month (e.g., $20)
    
    Returns:
        Budget analysis
    """
    target_rate = (target_cost / target_tokens) * 1_000_000  # Cost per 1M tokens
    
    selector = ModelSelector()
    
    print("\n" + "=" * 80)
    print(f"BUDGET ANALYSIS: {target_tokens:,} tokens / ${target_cost:.2f} = ${target_rate:.2f} per 1M")
    print("=" * 80)
    
    analysis = {
        "target_tokens": target_tokens,
        "target_cost": target_cost,
        "required_rate_per_1m": target_rate,
        "achievable": {},
    }
    
    for model in selector.PRIORITY:
        cost = selector.cost_map[model]
        tokens_possible = int((target_cost / cost.avg_cost) * 1_000_000)
        achievable = tokens_possible >= target_tokens
        
        analysis["achievable"][cost.name] = {
            "tokens_possible": tokens_possible,
            "actual_cost": (tokens_possible / 1_000_000) * cost.avg_cost,
            "achievable": achievable,
            "margin": tokens_possible - target_tokens,
        }
        
        status = "✅ ACHIEVABLE" if achievable else "❌ NOT ACHIEVABLE"
        print(f"{cost.name:<20} {tokens_possible:>10,} tokens (${(tokens_possible/1_000_000)*cost.avg_cost:>6.2f}) {status}")
    
    print("=" * 80 + "\n")
    return analysis


if __name__ == "__main__":
    # Demo
    print("🎯 CHEAP MODELS ONLY - NO OPUS\n")
    
    selector = ModelSelector()
    selector.show_model_comparison()
    
    # Budget analysis
    print("\n" + "=" * 80)
    print("GOAL: 50M tokens / month at under $20")
    print("=" * 80)
    estimate_budget(target_tokens=50_000_000, target_cost=20.0)
    
    # Example selections
    print("\nEXAMPLE MODEL SELECTIONS:")
    print("-" * 80)
    
    complexities = [
        (0.1, "Routing"),
        (0.3, "Simple classification"),
        (0.5, "Code review"),
        (0.7, "Code generation"),
        (0.9, "Complex architecture"),
    ]
    
    for complexity, description in complexities:
        model, cost = selector.select_for_complexity(complexity)
        print(f"Complexity {complexity:.1f} ({description:<25}): {cost.name:<20} (${cost.avg_cost:.2f}/1M)")

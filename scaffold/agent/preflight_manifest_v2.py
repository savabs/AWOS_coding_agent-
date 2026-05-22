"""
Pre-Flight Cost Manifest — ASCII Terminal UI (No Rich Library)

Problem: You don't know the cost until after you pay.
Solution: Show the user a "pre-flight" cost breakdown BEFORE running.

Uses ASCII art (no external dependencies).
"""

from typing import Dict, Optional
from dataclasses import dataclass
import sys


@dataclass
class CostEstimate:
    """Cost estimate for a request."""
    system_prompt_tokens: int
    context_tokens: int
    task_tokens: int
    total_input_tokens: int
    estimated_output_tokens: int = 0
    input_cost_mtok: float = 5.0  # Claude Sonnet
    output_cost_mtok: float = 15.0
    
    @property
    def input_cost(self) -> float:
        return (self.total_input_tokens / 1_000_000) * self.input_cost_mtok
    
    @property
    def output_cost(self) -> float:
        return (self.estimated_output_tokens / 1_000_000) * self.output_cost_mtok
    
    @property
    def total_cost(self) -> float:
        return self.input_cost + self.output_cost


class PreFlightManifest:
    """Display cost BEFORE running."""
    
    def __init__(self, estimate: CostEstimate, budget: float = 15.0, monthly_spent: float = 0.0):
        self.estimate = estimate
        self.budget = budget
        self.monthly_spent = monthly_spent
    
    def show(self):
        """Display manifest in ASCII."""
        est = self.estimate
        remaining = self.budget - self.monthly_spent
        after = remaining - est.total_cost
        
        print("\n" + "=" * 75)
        print("PRE-FLIGHT COST MANIFEST")
        print("=" * 75)
        
        print("\n📊 TOKEN BREAKDOWN:")
        print(f"    System Prompt  │ {est.system_prompt_tokens:8,} tokens")
        print(f"    Context Blocks │ {est.context_tokens:8,} tokens")
        print(f"    Task Prompt    │ {est.task_tokens:8,} tokens")
        print(f"    ───────────────┼──────────────")
        print(f"    Total Input    │ {est.total_input_tokens:8,} tokens")
        print(f"    Est. Output    │ {est.estimated_output_tokens:8,} tokens")
        
        print("\n💰 COST BREAKDOWN:")
        print(f"    Input   @ ${est.input_cost_mtok:5.2f}/MTok │ ${est.input_cost:10.6f}")
        print(f"    Output  @ ${est.output_cost_mtok:5.2f}/MTok │ ${est.output_cost:10.6f}")
        print(f"    ─────────────────────────────┼──────────────")
        print(f"    Total Cost                   │ ${est.total_cost:10.6f}")
        
        print("\n💵 BUDGET STATUS:")
        print(f"    Monthly Budget     │ ${self.budget:8.2f}")
        print(f"    Spent This Month   │ ${self.monthly_spent:8.2f}")
        print(f"    Remaining          │ ${remaining:8.2f}")
        print(f"    After This Request │ ${after:8.2f}")
        
        if after < 0:
            overage = abs(after)
            print(f"\n    ❌ ALERT: Would exceed budget by ${overage:.2f}")
            safety = "UNSAFE"
        else:
            pct = ((self.monthly_spent + est.total_cost) / self.budget) * 100
            print(f"\n    ✓ Safe to proceed ({pct:.0f}% of budget used)")
            safety = "SAFE"
        
        print("\n" + "=" * 75)
        print(f"STATUS: {safety} | Cost: ${est.total_cost:.6f} | Budget: ${remaining:.2f} remaining")
        print("=" * 75 + "\n")
        
        return safety == "SAFE"
    
    def ask_approval(self) -> bool:
        """Show manifest and ask for user approval."""
        self.show()
        
        while True:
            response = input("Approve this request? (y/n): ").lower().strip()
            if response in ['y', 'yes']:
                print("✓ Approved. Proceeding...")
                return True
            elif response in ['n', 'no']:
                print("✗ Cancelled.")
                return False
            else:
                print("Please enter 'y' or 'n'")


class CostEstimator:
    """Estimate cost of a request."""
    
    @staticmethod
    def estimate(
        task_prompt: str,
        context: str = "",
        system_prompt: str = "",
        output_tokens_estimate: int = 500,
    ) -> CostEstimate:
        """Estimate cost of a request."""
        
        # Rough token calculation: 1 token ≈ 4 characters
        system_tokens = max(1, len(system_prompt) // 4)
        context_tokens = max(1, len(context) // 4)
        task_tokens = max(1, len(task_prompt) // 4)
        total_input = system_tokens + context_tokens + task_tokens
        
        return CostEstimate(
            system_prompt_tokens=system_tokens,
            context_tokens=context_tokens,
            task_tokens=task_tokens,
            total_input_tokens=total_input,
            estimated_output_tokens=output_tokens_estimate,
        )
    
    @staticmethod
    def estimate_with_cache(
        task_prompt: str,
        context: str = "",
        system_prompt: str = "",
        output_tokens_estimate: int = 500,
        cache_hit: bool = False,
    ) -> CostEstimate:
        """
        Estimate cost with prompt caching.
        
        If cache_hit: system_prompt + context cost 10% of normal (90% savings).
        """
        
        system_tokens = max(1, len(system_prompt) // 4)
        context_tokens = max(1, len(context) // 4)
        task_tokens = max(1, len(task_prompt) // 4)
        
        if cache_hit:
            # Cached input costs 10% of normal
            total_input = int(system_tokens * 0.1) + int(context_tokens * 0.1) + task_tokens
            system_display = int(system_tokens * 0.1)
            context_display = int(context_tokens * 0.1)
        else:
            total_input = system_tokens + context_tokens + task_tokens
            system_display = system_tokens
            context_display = context_tokens
        
        return CostEstimate(
            system_prompt_tokens=system_display,
            context_tokens=context_display,
            task_tokens=task_tokens,
            total_input_tokens=total_input,
            estimated_output_tokens=output_tokens_estimate,
        )


# Quick test
if __name__ == "__main__":
    print("TEST 1: Normal request (no cache)")
    estimate = CostEstimator.estimate(
        task_prompt="Fix the bug in dispatcher.py (500 chars)",
        context="Full project codebase context (50KB)",
        system_prompt="You are a Python coding expert. Follow these rules: (2KB)",
        output_tokens_estimate=600,
    )
    
    manifest = PreFlightManifest(
        estimate=estimate,
        budget=15.0,
        monthly_spent=3.50,
    )
    manifest.show()
    
    print("\n" + "=" * 75)
    print("TEST 2: Cached request (90% savings on system + context)")
    print("=" * 75)
    
    estimate_cached = CostEstimator.estimate_with_cache(
        task_prompt="Fix another bug (500 chars)",
        context="Same codebase context (50KB) - CACHED",
        system_prompt="You are a Python coding expert... (2KB) - CACHED",
        output_tokens_estimate=600,
        cache_hit=True,
    )
    
    manifest_cached = PreFlightManifest(
        estimate=estimate_cached,
        budget=15.0,
        monthly_spent=3.50 + estimate.total_cost,
    )
    manifest_cached.show()
    
    print("\nCOST COMPARISON:")
    print(f"  First request:  ${estimate.total_cost:.6f}")
    print(f"  Second request (cached): ${estimate_cached.total_cost:.6f}")
    print(f"  Savings: ${estimate.total_cost - estimate_cached.total_cost:.6f} (90%+ on context)")

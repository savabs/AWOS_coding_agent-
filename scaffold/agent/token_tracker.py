#!/usr/bin/env python3
"""
Token-optimized routing using cost selector.
Tracks every request for budget visibility.
"""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class TokenUsageRecord:
    """Track tokens and costs per request"""
    request_type: str      # "routing", "coding", "review", "reasoning"
    model_used: str
    input_tokens: int
    output_tokens: int
    cost: float
    total_tokens: int


class TokenTracker:
    """Tracks token usage and costs for budget monitoring"""
    
    def __init__(self, monthly_budget: float = 20.0, monthly_token_target: float = 50_000_000):
        """Initialize tracker"""
        self.monthly_budget = monthly_budget
        self.monthly_token_target = monthly_token_target
        self.target_rate_per_1m = (monthly_budget / monthly_token_target) * 1_000_000
        
        self.total_cost = 0.0
        self.total_tokens = 0
        self.request_count = 0
        self.cache_hits = 0             # Responses served from ResponseCache
        self.cache_savings_est = 0.0    # Estimated $ saved by cache hits
        self._alerted_thresholds: set = set()  # Tracks which alerts have fired
        self.ALERT_THRESHOLDS = [0.50, 0.75, 0.90]
        self.requests: Dict[str, list] = {
            "routing": [],
            "coding": [],
            "review": [],
            "reasoning": [],
            "planning": [],    # planner.py Sonnet calls
            "execution": [],   # worker.py DeepSeek/Haiku calls
        }
    
    def record(self, request_type: str, model: str, input_tokens: int, output_tokens: int, cost: float):
        """Record a request"""
        total = input_tokens + output_tokens
        
        record = {
            "model": model,
            "input": input_tokens,
            "output": output_tokens,
            "total": total,
            "cost": cost,
        }
        
        if request_type not in self.requests:
            self.requests[request_type] = []
        self.requests[request_type].append(record)
        self.total_tokens += total
        self.total_cost += cost
        self.request_count += 1
        
        # Fire budget threshold alerts once per threshold
        if self.monthly_budget > 0:
            usage_pct = self.total_cost / self.monthly_budget
            for threshold in self.ALERT_THRESHOLDS:
                if usage_pct >= threshold and threshold not in self._alerted_thresholds:
                    self._alerted_thresholds.add(threshold)
                    print(
                        f"\n⚠️  BUDGET ALERT [{threshold:.0%} threshold]: {usage_pct:.0%} used "
                        f"(${self.total_cost:.2f} of ${self.monthly_budget:.2f}). "
                        f"{self.request_count} requests so far."
                    )
    
    def get_budget_status(self) -> Dict:
        """Get current budget status"""
        remaining_budget = self.monthly_budget - self.total_cost
        remaining_tokens = int((remaining_budget / self.target_rate_per_1m) * 1_000_000) if remaining_budget > 0 else 0
        
        return {
            "total_cost": self.total_cost,
            "remaining_budget": remaining_budget,
            "total_tokens": self.total_tokens,
            "remaining_tokens": remaining_tokens,
            "target_cost": self.monthly_budget,
            "target_tokens": self.monthly_token_target,
            "budget_used_pct": (self.total_cost / self.monthly_budget * 100) if self.monthly_budget > 0 else 0,
            "token_rate_per_1m": (self.total_cost / self.total_tokens * 1_000_000) if self.total_tokens > 0 else 0,
            "on_track": self.total_cost <= (self.request_count / 12 * self.monthly_budget),  # Rough check
        }
    
    def record_cache_hit(self, estimated_cost_saved: float = 0.002):
        """Record a response served from ResponseCache."""
        self.cache_hits += 1
        self.cache_savings_est += estimated_cost_saved

    def check_guard(self, estimated_cost: float = 0.0) -> tuple:
        """
        Pre-call guardrail check.
        Returns (allowed: bool, message: str).
        Hard-blocks at 100% budget; warns at 90%.
        """
        remaining = self.monthly_budget - self.total_cost
        if self.total_cost >= self.monthly_budget:
            return False, (
                f"🛑 HARD STOP: monthly budget of ${self.monthly_budget:.2f} exhausted "
                f"(${self.total_cost:.4f} spent). Set AWOS_MONTHLY_BUDGET to a higher limit."
            )
        if estimated_cost > 0 and estimated_cost > remaining:
            return False, (
                f"🛑 This call (est. ${estimated_cost:.4f}) would exceed remaining budget "
                f"(${remaining:.4f}). Skipping."
            )
        if (self.total_cost / self.monthly_budget) >= 0.90:
            return True, (
                f"⚠️  Budget at {self.total_cost/self.monthly_budget:.0%} — "
                f"${remaining:.4f} remaining."
            )
        return True, ""

    def show_status(self):
        """Display a rich budget dashboard."""
        s = self.get_budget_status()
        pct = s["budget_used_pct"] / 100.0
        bar_len = 40
        filled = int(bar_len * pct)
        bar_color = "🟥" if pct >= 0.90 else ("🟨" if pct >= 0.75 else "🟩")
        bar = bar_color * filled + "⬜" * (bar_len - filled)

        print("\n" + "═" * 68)
        print("  💰  AWOS BUDGET MONITOR")
        print("═" * 68)
        print(f"  [{bar}]  {pct:.1%}")
        print(f"  Spent:     ${s['total_cost']:.5f}  of  ${s['target_cost']:.2f} monthly cap")
        print(f"  Remaining: ${s['remaining_budget']:.5f}")
        print(f"  Requests:  {self.request_count}  |  Tokens: {s['total_tokens']:,}")

        if self.cache_hits > 0:
            total_reqs = self.request_count + self.cache_hits
            hit_rate = self.cache_hits / total_reqs * 100 if total_reqs else 0
            print(f"  Cache:     {self.cache_hits} hits ({hit_rate:.0f}% rate)  "
                  f"est. saved ${self.cache_savings_est:.5f}")

        # Per-model breakdown
        model_totals: Dict[str, dict] = {}
        for records in self.requests.values():
            for r in records:
                m = r["model"]
                if m not in model_totals:
                    model_totals[m] = {"reqs": 0, "tokens": 0, "cost": 0.0}
                model_totals[m]["reqs"]   += 1
                model_totals[m]["tokens"] += r["total"]
                model_totals[m]["cost"]   += r["cost"]

        if model_totals:
            print(f"\n  {'Model':<28} {'Reqs':>5}  {'Tokens':>10}  {'Cost':>10}")
            print(f"  {'─'*28}  {'─'*5}  {'─'*10}  {'─'*10}")
            for model, d in sorted(model_totals.items(), key=lambda x: -x[1]["cost"]):
                print(f"  {model:<28}  {d['reqs']:>5}  {d['tokens']:>10,}  ${d['cost']:>9.5f}")

        # Monthly projection
        if self.request_count > 0:
            avg_cost = s["total_cost"] / self.request_count
            projected = avg_cost * 1516          # Your historical request volume
            status_icon = "✅" if projected <= self.monthly_budget else "❌"
            print(f"\n  Monthly projection (1,516 reqs):  ${projected:.2f}  {status_icon}")
            avg_per_req = s["total_cost"] / self.request_count
            print(f"  Avg cost per request:             ${avg_per_req:.6f}")

        print("═" * 68 + "\n")


if __name__ == "__main__":
    # Demo
    tracker = TokenTracker(monthly_budget=20.0, monthly_token_target=50_000_000)
    
    # Simulate some requests
    tracker.record("routing", "deepseek", 150, 50, 0.00003)
    tracker.record("coding", "gemini-flash", 500, 1500, 0.00051)
    tracker.record("review", "deepseek", 800, 600, 0.00022)
    tracker.record("reasoning", "gemini-flash", 1000, 2000, 0.00091)
    
    tracker.show_status()
    
    # Project to month
    print("\nProjection if pattern continues:")
    if tracker.request_count > 0:
        avg_cost_per_request = tracker.total_cost / tracker.request_count
        avg_tokens_per_request = tracker.total_tokens / tracker.request_count
        monthly_requests_estimate = 500 * 4  # 500 chats, 4 msgs each
        
        projected_cost = avg_cost_per_request * monthly_requests_estimate
        projected_tokens = int(avg_tokens_per_request * monthly_requests_estimate)
        
        print(f"  Avg per request: {avg_tokens_per_request:.0f} tokens, ${avg_cost_per_request:.6f}")
        print(f"  Monthly estimate: {projected_tokens:,} tokens, ${projected_cost:.2f}")
        print(f"  Status: {'✅ UNDER BUDGET' if projected_cost <= 20 else '❌ OVER BUDGET'}\n")

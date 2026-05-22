"""
Inference Engine Monitor - Integration Examples

Shows how to integrate monitoring into the Dispatcher, ModelRouter,
and other components of the AWOS system.

Each example demonstrates best practices for capturing metrics
without impacting performance.
"""

import time
from typing import Any
from datetime import date, timedelta
from scaffold.agent.inference_engine_monitor import InferenceEngineMonitor


# ============================================================================
# EXAMPLE 1: Dispatcher Integration
# ============================================================================

class MonitoredDispatcher:
    """
    Dispatcher with integrated monitoring.
    
    Pattern: Wrap the API call to capture metrics before and after.
    """

    def __init__(self):
        self.monitor = InferenceEngineMonitor("dispatcher_stats.db", monthly_budget=15.0)

    def dispatch(self, task: str, model: str, complexity: int, client: Any) -> Any:
        """
        Dispatch with monitoring.
        
        Captures:
        - Task type (inferred from task description)
        - Model used
        - Complexity score
        - Input/output tokens
        - Response time
        - Success/error status
        """

        # Infer task type
        task_type = self._infer_task_type(task)

        # Time the API call
        start_time = time.time()
        error = None
        success = True

        try:
            response = client.messages.create(
                model=model,
                max_tokens=2000,
                messages=[{"role": "user", "content": task}],
            )
        except Exception as e:
            error = str(e)
            success = False
            response = None

        # Calculate metrics
        response_time_ms = int((time.time() - start_time) * 1000)

        # Extract tokens if successful
        if success and response:
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
        else:
            input_tokens = 0
            output_tokens = 0

        # Record the request
        self.monitor.record_request(
            task=task,
            model=model,
            task_type=task_type,
            complexity=complexity,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            response_time_ms=response_time_ms,
            success=success,
            error=error,
        )

        # Return response (or raise error)
        if not success:
            raise Exception(error)
        return response

    def _infer_task_type(self, task: str) -> str:
        """Infer task type from task description."""
        task_lower = task.lower()

        if any(w in task_lower for w in ["review", "lint", "quality", "check"]):
            return "code_review"
        elif any(w in task_lower for w in ["bug", "error", "leak", "fix", "crash", "issue"]):
            return "bug_analysis"
        elif any(w in task_lower for w in ["test", "unit test", "test case"]):
            return "test_generation"
        elif any(w in task_lower for w in ["design", "architecture", "structure", "api"]):
            return "architecture"
        else:
            return "analysis"

    def show_stats(self):
        """Display current statistics."""
        self.monitor.print_dashboard()

    def export_daily_stats(self):
        """Export today's stats to CSV."""
        today = date.today()
        self.monitor.export_csv(today, today, f"stats_{today}.csv")


# ============================================================================
# EXAMPLE 2: ModelRouter Integration
# ============================================================================

class MonitoredModelRouter:
    """
    Model router with integrated monitoring of routing decisions.
    
    Pattern: Log the routing decision BEFORE the API call is made.
    This helps understand if the router is making good choices.
    """

    def __init__(self):
        self.monitor = InferenceEngineMonitor("router_stats.db", monthly_budget=15.0)
        self.routing_decisions = []

    def route(self, complexity: int) -> str:
        """
        Route based on complexity, with decision logging.
        
        Decision made BEFORE the call, so we can later correlate
        with actual token usage to evaluate routing effectiveness.
        """

        if complexity >= 7:
            model = "claude-sonnet-4-6"
            reason = "High complexity requires quality model"
        elif complexity >= 5:
            model = "deepseek-reasoner"
            reason = "Medium complexity, reasoning needed"
        else:
            model = "deepseek-chat"
            reason = "Low complexity, cost-effective model"

        # Log the decision
        self.routing_decisions.append({
            "timestamp": time.time(),
            "complexity": complexity,
            "selected_model": model,
            "reason": reason,
        })

        return model

    def evaluate_routing_effectiveness(self):
        """
        Analyze if routing decisions led to good outcomes.
        
        Returns metrics showing:
        - Did high-complexity tasks use high-quality models?
        - Did low-complexity tasks use cheap models?
        - What was the cost efficiency?
        """

        models = self.monitor.model_comparison()
        monthly = self.monitor.monthly_report()

        print("\n" + "=" * 80)
        print("ROUTING EFFECTIVENESS ANALYSIS")
        print("=" * 80)

        # Cost efficiency by model
        print("\nCost Efficiency (tokens per dollar):")
        for model, metrics in models.items():
            tokens_per_dollar = metrics.get("tokens_per_dollar", 0)
            print(f"  {model}: {tokens_per_dollar:,.0f} tokens/$")

        # Task type analysis
        print("\nCost by Task Type (higher cost = more complex):")
        for task_type, stats in monthly.by_task_type.items():
            avg_cost = stats["cost"] / stats["count"] if stats["count"] > 0 else 0
            print(f"  {task_type}: ${avg_cost:.6f} per request")

        print("\n" + "=" * 80 + "\n")


# ============================================================================
# EXAMPLE 3: OutputTokenOptimizer Integration
# ============================================================================

class MonitoredOptimizer:
    """
    Monitor the effectiveness of output token optimization.
    
    Pattern: Log BEFORE optimization, compare with AFTER optimization results.
    """

    def __init__(self):
        self.monitor = InferenceEngineMonitor("optimizer_stats.db")
        self.optimization_baseline = None

    def start_optimization_baseline(self):
        """Start collecting baseline (unoptimized) metrics."""
        print("📊 Starting baseline collection (unoptimized requests)...")
        self.optimization_baseline = {}

    def record_optimized_request(
        self,
        task_type: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        response_time_ms: int,
        optimized: bool = True,
    ):
        """Record a request with optimization status."""

        self.monitor.record_request(
            task=f"Optimized {task_type}" if optimized else f"Unoptimized {task_type}",
            model=model,
            task_type=task_type,
            complexity=5,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            response_time_ms=response_time_ms,
            success=True,
        )

    def analyze_optimization_impact(self):
        """
        Compare optimized vs unoptimized requests.
        
        Shows:
        - Percentage reduction in output tokens
        - Cost savings
        - Response time impact
        """

        models = self.monitor.model_comparison()
        print("\n" + "=" * 80)
        print("OPTIMIZATION IMPACT ANALYSIS")
        print("=" * 80)

        total_stats = self.monitor.get_stats()

        print(f"\nOverall Statistics:")
        print(f"  Total Requests: {total_stats['total_requests']}")
        print(f"  Total Cost: ${total_stats['total_cost']:.4f}")
        print(f"  Avg Response Time: {total_stats['avg_response_time_ms']:.1f}ms")

        # If we have enough data, analyze by task type
        daily = self.monitor.daily_report()
        if daily.by_task_type:
            print(f"\nCost by Task Type:")
            for task, stats in daily.by_task_type.items():
                avg_cost = stats["cost"] / stats["count"] if stats["count"] > 0 else 0
                print(f"  {task}: ${avg_cost:.6f}/request")

        print("\n" + "=" * 80 + "\n")


# ============================================================================
# EXAMPLE 4: Budget Monitoring
# ============================================================================

class BudgetMonitor:
    """
    Monitor budget status and alert when approaching limits.
    """

    def __init__(self, monthly_budget: float = 15.0, alert_threshold: float = 0.8):
        self.monitor = InferenceEngineMonitor("budget_stats.db", monthly_budget)
        self.alert_threshold = alert_threshold  # Alert at 80% of budget

    def check_budget_status(self):
        """Check current budget status."""
        monthly = self.monitor.monthly_report()

        print("\n" + "=" * 80)
        print(f"BUDGET STATUS - {monthly.month}")
        print("=" * 80)

        print(f"\nBudget: ${monthly.budget:.2f}")
        print(f"Spent: ${monthly.total_cost:.4f} ({monthly.budget_used_percent:.1f}%)")
        print(f"Remaining: ${monthly.budget - monthly.total_cost:.4f}")

        if monthly.projected_cost > monthly.budget:
            print(f"\n⚠️  WARNING: Projected to exceed budget!")
            print(f"   Projected: ${monthly.projected_cost:.4f}")
            print(f"   Overage: ${monthly.projected_cost - monthly.budget:.4f}")
        elif monthly.budget_used_percent >= (self.alert_threshold * 100):
            print(f"\n⚠️  CAUTION: At {monthly.budget_used_percent:.1f}% of budget")
            print(f"   Remaining for {calendar_days_remaining()} days: ${(monthly.budget - monthly.total_cost):.4f}")
        else:
            print(f"\n✅ Budget OK: {100 - monthly.budget_used_percent:.1f}% remaining")

        # Cost trend
        days_into_month = (date.today() - date(int(monthly.month.split("-")[0]), int(monthly.month.split("-")[1]), 1)).days + 1
        daily_avg = monthly.total_cost / days_into_month if days_into_month > 0 else 0

        print(f"\nDaily Average Cost: ${daily_avg:.4f}/day")
        print(f"At this rate, monthly total: ${daily_avg * 30:.2f}")

        print("\n" + "=" * 80 + "\n")

    def recommend_actions(self):
        """Recommend actions based on budget status."""
        monthly = self.monitor.monthly_report()
        models = self.monitor.model_comparison()

        print("\n📋 RECOMMENDATIONS:")

        if not models:
            print("\n📊 No data yet to make recommendations. Continue monitoring...")
            return

        if monthly.budget_used_percent > 80:
            # Find most expensive model
            most_expensive = max(models.items(), key=lambda x: x[1]["total_cost"])
            print(f"\n⚠️  Consider switching from {most_expensive[0]} to a cheaper alternative")
            print(f"   Current: ${most_expensive[1]['total_cost']:.4f} ({most_expensive[1]['total_requests']} requests)")
            print(f"   Potential savings: Use cheaper model for simple tasks")

        # Find most cost-effective model
        most_efficient = max(models.items(), key=lambda x: x[1].get("tokens_per_dollar", 0))
        print(f"\n✅ Most cost-effective model: {most_efficient[0]}")
        print(f"   {most_efficient[1]['tokens_per_dollar']:,.0f} tokens per dollar")


def calendar_days_remaining() -> int:
    """Days remaining in current month."""
    today = date.today()
    if today.month == 12:
        last_day = date(today.year + 1, 1, 1)
    else:
        last_day = date(today.year, today.month + 1, 1)
    return (last_day - today).days


# ============================================================================
# EXAMPLE 5: Real-World Workflow
# ============================================================================

def example_complete_workflow():
    """
    Complete workflow showing monitoring in action.
    """

    print("\n" + "=" * 80)
    print("INFERENCE ENGINE MONITORING - COMPLETE WORKFLOW EXAMPLE")
    print("=" * 80)

    # Initialize monitoring components (use same database)
    shared_monitor = InferenceEngineMonitor("workflow_stats.db", monthly_budget=15.0)
    
    dispatcher = MonitoredDispatcher()
    dispatcher.monitor = shared_monitor  # Use shared monitor
    
    router = MonitoredModelRouter()
    router.monitor = shared_monitor  # Use shared monitor
    
    budget_monitor = BudgetMonitor(monthly_budget=15.0)
    budget_monitor.monitor = shared_monitor  # Use shared monitor

    # Simulate a workflow
    print("\n1️⃣  Simulating dispatcher calls...")

    # Simulate some requests (without actual API calls)
    test_tasks = [
        ("Review this function for bugs", "code_review", 5),
        ("Why is memory leaking?", "bug_analysis", 7),
        ("Generate unit tests", "test_generation", 6),
    ]

    for task, task_type, complexity in test_tasks * 3:
        model = router.route(complexity)

        # Simulate tokens (don't actually call API)
        input_toks = 2000 + (complexity * 200)
        output_toks = 800 + (complexity * 100)

        shared_monitor.record_request(
            task=task,
            model=model,
            task_type=task_type,
            complexity=complexity,
            input_tokens=input_toks,
            output_tokens=output_toks,
            response_time_ms=1250,
            success=True,
        )

    print("✅ Recorded 9 simulated requests")

    # Show dispatcher stats
    print("\n2️⃣  Dispatcher Statistics:")
    shared_monitor.print_dashboard()

    # Analyze routing effectiveness
    print("\n3️⃣  Routing Effectiveness:")
    router.monitor = shared_monitor
    router.evaluate_routing_effectiveness()

    # Check budget status
    print("\n4️⃣  Budget Monitoring:")
    budget_monitor.check_budget_status()

    # Recommendations
    print("\n5️⃣  Actionable Recommendations:")
    budget_monitor.recommend_actions()

    print("\n" + "=" * 80)
    print("✅ WORKFLOW COMPLETE")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    example_complete_workflow()

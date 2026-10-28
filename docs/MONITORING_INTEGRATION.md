---
title: "Inference Engine Monitoring System - Integration & Usage"
tags:
  - doc/spec
  - topic/monitoring
---

# Inference Engine Monitoring System - Integration Guide

**Status:** Production-ready, tested ✅

**Purpose:** Track costs, tokens, performance, and quality metrics for data-driven decisions

**Components:**
1. `InferenceEngineMonitor` — Core monitoring class
2. Dispatcher integration — Log every API call
3. Budget monitoring — Alert on spending  
4. Reports — Daily, monthly, model analysis
5. CLI dashboard — Real-time visibility

---

## Quick Start (5 Minutes)

### 1. Initialize Monitor

```python
from scaffold.agent.inference_engine_monitor import InferenceEngineMonitor

monitor = InferenceEngineMonitor(
    db_path="stats.db",           # SQLite database file
    monthly_budget=15.0            # Your monthly budget
)
```

### 2. Log Requests in Dispatcher

```python
def dispatch(task, model, complexity, client):
    import time
    
    start_time = time.time()
    response = client.messages.create(...)
    response_time_ms = int((time.time() - start_time) * 1000)
    
    # Log the request
    monitor.record_request(
        task=task,
        model=model,
        task_type="code_review",  # or bug_analysis, test_generation, etc.
        complexity=complexity,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        response_time_ms=response_time_ms,
        success=True
    )
    
    return response
```

### 3. View Dashboard

```python
# Print dashboard
monitor.print_dashboard()

# Or generate reports
daily_report = monitor.daily_report()
monthly_report = monitor.monthly_report()
```

That's it! Monitor will:
- ✅ Track every request
- ✅ Calculate costs automatically
- ✅ Aggregate by hour, day, month
- ✅ Provide insights and recommendations

---

## Integration Points (3 Files to Modify)

### Point 1: Dispatcher (`scaffold/agent/dispatcher.py`)

```python
# At the top
from scaffold.agent.inference_engine_monitor import InferenceEngineMonitor

class Dispatcher:
    def __init__(self):
        self.monitor = InferenceEngineMonitor("stats.db", monthly_budget=15.0)
    
    def dispatch(self, task: str, model: str, complexity: int):
        import time
        
        # Infer task type
        task_type = self._classify_task(task)
        
        # Time the call
        start_time = time.time()
        error = None
        success = True
        
        try:
            response = client.messages.create(
                model=model,
                max_tokens=2000,
                messages=[{"role": "user", "content": task}]
            )
        except Exception as e:
            error = str(e)
            success = False
            response = None
        
        response_time_ms = int((time.time() - start_time) * 1000)
        
        # Extract metrics
        if success and response:
            input_toks = response.usage.input_tokens
            output_toks = response.usage.output_tokens
        else:
            input_toks = output_toks = 0
        
        # Log to monitor
        self.monitor.record_request(
            task=task,
            model=model,
            task_type=task_type,
            complexity=complexity,
            input_tokens=input_toks,
            output_tokens=output_toks,
            response_time_ms=response_time_ms,
            success=success,
            error=error
        )
        
        if not success:
            raise Exception(error)
        return response
    
    def _classify_task(self, task: str) -> str:
        """Classify task type from description."""
        task_lower = task.lower()
        if any(w in task_lower for w in ["review", "lint", "quality"]):
            return "code_review"
        elif any(w in task_lower for w in ["bug", "error", "leak", "fix"]):
            return "bug_analysis"
        elif any(w in task_lower for w in ["test", "unit test"]):
            return "test_generation"
        else:
            return "analysis"
```

### Point 2: Model Router (`scaffold/agent/model_router.py`)

Add routing decision logging:

```python
class ModelRouter:
    def __init__(self):
        self.monitor = InferenceEngineMonitor("stats.db")
    
    def route(self, complexity: int) -> str:
        """Route based on complexity."""
        if complexity >= 7:
            model = "claude-3-5-sonnet"
        elif complexity >= 5:
            model = "deepseek-reasoner"
        else:
            model = "deepseek-chat"
        
        # Log the decision (helps evaluate routing effectiveness)
        self.routing_decisions.append({
            "complexity": complexity,
            "selected_model": model,
            "timestamp": datetime.now()
        })
        
        return model
```

### Point 3: Error Handler (or wherever errors occur)

```python
def handle_error(error, context):
    """Log errors to monitoring system."""
    
    monitor.record_request(
        task=context.get("task", "unknown"),
        model=context.get("model", "unknown"),
        task_type=context.get("task_type", "analysis"),
        complexity=context.get("complexity", 5),
        input_tokens=0,
        output_tokens=0,
        response_time_ms=context.get("response_time_ms", 0),
        success=False,
        error=str(error)
    )
```

---

## Reports & Analytics

### Daily Report

```python
daily = monitor.daily_report()  # Today

# or specific date
daily = monitor.daily_report(date(2026, 5, 15))

# Access data
print(f"Requests: {daily.total_requests}")
print(f"Cost: ${daily.total_cost:.4f}")
print(f"Error rate: {daily.error_rate:.2f}%")
print(f"By model: {daily.by_model}")
print(f"By task type: {daily.by_task_type}")
```

### Monthly Report

```python
monthly = monitor.monthly_report()  # Current month

# or specific month
monthly = monitor.monthly_report("2026-05")

# Access data
print(f"Total cost: ${monthly.total_cost:.4f}")
print(f"Budget used: {monthly.budget_used_percent:.1f}%")
print(f"Projected: ${monthly.projected_cost:.4f}")
print(f"Daily average: {monthly.daily_avg_requests:.1f} requests/day")
```

### Model Comparison

```python
models = monitor.model_comparison()

for model_name, metrics in models.items():
    print(f"{model_name}:")
    print(f"  Requests: {metrics['total_requests']}")
    print(f"  Cost: ${metrics['total_cost']:.4f}")
    print(f"  Error rate: {metrics['error_rate']:.2f}%")
    print(f"  Tokens/$: {metrics['tokens_per_dollar']:.0f}")
```

### Cost Breakdown

```python
daily = monitor.daily_report()

# By model
for model, stats in daily.by_model.items():
    print(f"{model}: ${stats['cost']:.4f}")

# By task type
for task_type, stats in daily.by_task_type.items():
    print(f"{task_type}: ${stats['cost']:.4f}")
```

---

## CSV Export

Export data for external analysis:

```python
from datetime import date, timedelta

# Export last 7 days
start = date.today() - timedelta(days=7)
end = date.today()

monitor.export_csv(start, end, "last_week.csv")

# Analyze in Excel, pandas, etc.
```

CSV includes:
- Timestamp
- Model
- Task type
- Complexity
- Input/output tokens
- Cost breakdown
- Response time
- Success/failure

---

## Budget Monitoring

Create budget alerts:

```python
def check_budget_status(monitor, alert_threshold=0.8):
    """Alert if approaching budget limit."""
    
    monthly = monitor.monthly_report()
    
    if monthly.budget_used_percent >= (alert_threshold * 100):
        print(f"⚠️  Budget alert: {monthly.budget_used_percent:.1f}% used")
        print(f"   Spent: ${monthly.total_cost:.4f} / ${monthly.budget:.2f}")
        print(f"   Days remaining in month: {calendar_days_remaining()}")
        
        # Recommend actions
        if monthly.projected_cost > monthly.budget:
            print(f"   ⚠️  WARNING: Will exceed budget!")
            print(f"   Projected: ${monthly.projected_cost:.4f}")
        
        return False  # Budget alert triggered
    
    return True  # Budget OK
```

Call this periodically:

```python
# In your main loop or hourly check
if not check_budget_status(monitor, alert_threshold=0.75):
    # Trigger cost-saving measures
    # - Switch to cheaper models
    # - Batch requests
    # - Skip non-critical tasks
    pass
```

---

## Metrics Tracked

### Per Request
- `timestamp` — When request occurred
- `model` — Model used
- `task_type` — Type of task (code_review, bug_analysis, etc.)
- `complexity` — Complexity score (1-10)
- `input_tokens` — Input tokens used
- `output_tokens` — Output tokens used
- `total_tokens` — Sum of input + output
- `cost_input` — Cost of input tokens
- `cost_output` — Cost of output tokens
- `total_cost` — Total cost of request
- `response_time_ms` — How long request took
- `success` — Did the request succeed?
- `error_message` — If failed, what was the error?

### Aggregated Stats
- Request count
- Total tokens (by model, task type, date)
- Total cost (by model, task type, date)
- Average response time
- Error rate
- Error count

### Summary Metrics
- Monthly cost vs budget
- Daily average requests
- Growth rate (requests per day)
- Cost per model
- Cost per task type
- Token efficiency (tokens per dollar)

---

## Dashboard Commands

```python
# Show real-time dashboard
monitor.print_dashboard()

# This displays:
# - Overall statistics (all-time)
# - Daily report (today)
# - By model breakdown
# - By task type breakdown
# - Monthly budget status
# - Model comparison (cost efficiency)
```

---

## Integration Checklist

- [ ] Add `InferenceEngineMonitor` import to dispatcher
- [ ] Initialize monitor in `__init__`
- [ ] Wrap API call with timer
- [ ] Call `monitor.record_request()` after each call
- [ ] Add error handling to record failures
- [ ] Set monthly_budget = $15.0
- [ ] Test with 10-20 requests
- [ ] View dashboard with `monitor.print_dashboard()`
- [ ] Set up daily report generation
- [ ] Set up monthly report generation
- [ ] Create budget alert function
- [ ] Test with real requests
- [ ] Export CSV for analysis

---

## Example: Complete Dispatcher Integration

```python
import time
from datetime import date
from scaffold.agent.inference_engine_monitor import InferenceEngineMonitor

class MonitoredDispatcher:
    def __init__(self):
        self.monitor = InferenceEngineMonitor(
            db_path="stats.db",
            monthly_budget=15.0
        )
        self.client = None  # Initialize with your Anthropic client
    
    def dispatch(self, task: str, model: str, complexity: int):
        """Dispatch with full monitoring."""
        
        # Classify task
        task_type = self._classify_task(task)
        
        # Time the request
        start_time = time.time()
        success = True
        error = None
        response = None
        
        try:
            # Make API call
            response = self.client.messages.create(
                model=model,
                max_tokens=2000,
                messages=[{"role": "user", "content": task}]
            )
        except Exception as e:
            success = False
            error = str(e)
        
        # Calculate response time
        response_time_ms = int((time.time() - start_time) * 1000)
        
        # Extract token usage
        if success:
            input_toks = response.usage.input_tokens
            output_toks = response.usage.output_tokens
        else:
            input_toks = output_toks = 0
        
        # Record to monitor
        self.monitor.record_request(
            task=task,
            model=model,
            task_type=task_type,
            complexity=complexity,
            input_tokens=input_toks,
            output_tokens=output_toks,
            response_time_ms=response_time_ms,
            success=success,
            error=error
        )
        
        # Check budget
        self._check_budget()
        
        # Return or raise
        if not success:
            raise Exception(f"API error: {error}")
        return response
    
    def _classify_task(self, task: str) -> str:
        task_lower = task.lower()
        if any(w in task_lower for w in ["review", "lint"]):
            return "code_review"
        elif any(w in task_lower for w in ["bug", "error", "fix"]):
            return "bug_analysis"
        elif any(w in task_lower for w in ["test"]):
            return "test_generation"
        else:
            return "analysis"
    
    def _check_budget(self):
        """Check if approaching budget limit."""
        monthly = self.monitor.monthly_report()
        if monthly.budget_used_percent >= 80:
            print(f"⚠️  Warning: {monthly.budget_used_percent:.1f}% of budget used")
    
    def show_stats(self):
        """Display monitoring dashboard."""
        self.monitor.print_dashboard()
    
    def export_month_stats(self):
        """Export monthly data to CSV."""
        today = date.today()
        month_start = date(today.year, today.month, 1)
        self.monitor.export_csv(month_start, today, f"stats_{today.year}_{today.month:02d}.csv")


# Usage:
# dispatcher = MonitoredDispatcher()
# response = dispatcher.dispatch("Review this code", "claude-3-5-sonnet", 5)
# dispatcher.show_stats()
```

---

## Troubleshooting

### No data showing in reports?
- Make sure you're using the same database file
- Check that `monitor.record_request()` is being called
- Verify requests are being recorded successfully

### Budget showing as $0.00?
- Ensure `monthly_budget` is set correctly when initializing
- Check that tokens are being logged (not defaulting to 0)

### Model comparison showing empty?
- Need at least one request recorded for each model
- Make sure model names match exactly

### CSV export not working?
- Check write permissions on output directory
- Verify date range is valid
- Ensure there's data for the date range

---

## Advanced: Custom Metrics

Add custom metrics to tracking:

```python
# Define your own metrics
request_id = "req_12345"
custom_data = {
    "user_id": "user_123",
    "session_id": "sess_456",
    "cache_hit": True,
    "quality_score": 9.5
}

# Store as JSON in database (optional enhancement)
# For now, use task parameter to include metadata:
monitor.record_request(
    task=f"CodeReview[user=user_123, session=sess_456]",
    ...
)
```

---

## Status

✅ Implementation complete
✅ Testing complete
✅ Integration examples provided
✅ Documentation complete

**Ready to integrate into your dispatcher now.**

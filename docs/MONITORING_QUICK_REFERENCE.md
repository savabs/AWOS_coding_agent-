---
title: "Inference Engine Monitoring — Quick Reference"
tags:
  - doc/reference
  - topic/monitoring
---

# Inference Engine Monitoring — Quick Reference

**Status:** ✅ Production-ready

---

## 1. Get Started (5 Minutes)

### Python Code
```python
from scaffold.agent.inference_engine_monitor import InferenceEngineMonitor

# Initialize
monitor = InferenceEngineMonitor("stats.db", monthly_budget=15.0)

# Log a request
monitor.record_request(
    task="Code review",
    model="claude-3-5-sonnet",
    task_type="code_review",
    complexity=5,
    input_tokens=2000,
    output_tokens=800,
    response_time_ms=1250,
    success=True
)

# View dashboard
monitor.print_dashboard()
```

### Command Line
```bash
# Show dashboard
python scaffold/agent/monitor_cli.py

# Show today's stats
python scaffold/agent/monitor_cli.py --today

# Show month's stats
python scaffold/agent/monitor_cli.py --month

# Compare models
python scaffold/agent/monitor_cli.py --models

# Export to CSV
python scaffold/agent/monitor_cli.py --export output.csv
```

---

## 2. Files & Resources

| File | Purpose |
|---|---|
| `scaffold/agent/inference_engine_monitor.py` | Core monitoring engine |
| `scaffold/agent/monitor_integration_examples.py` | 5 integration examples |
| `scaffold/agent/monitor_cli.py` | Command-line dashboard |
| `docs/MONITORING_INTEGRATION.md` | Step-by-step integration guide |
| `docs/specs/inference_engine_monitoring_spec.md` | Complete specification |
| `docs/MONITORING_COMPLETE.md` | Delivery summary |

---

## 3. Integration (1 Hour)

### Step 1: Import (2 minutes)
```python
from scaffold.agent.inference_engine_monitor import InferenceEngineMonitor

class Dispatcher:
    def __init__(self):
        self.monitor = InferenceEngineMonitor("stats.db", monthly_budget=15.0)
```

### Step 2: Log Requests (10 minutes)
```python
import time

start_time = time.time()
response = client.messages.create(...)
response_time_ms = int((time.time() - start_time) * 1000)

self.monitor.record_request(
    task=task,
    model=model,
    task_type=self._classify_task(task),
    complexity=complexity,
    input_tokens=response.usage.input_tokens,
    output_tokens=response.usage.output_tokens,
    response_time_ms=response_time_ms,
    success=True
)
```

### Step 3: Generate Reports (10 minutes)
```python
# Daily
daily = self.monitor.daily_report()
print(f"Today: ${daily.total_cost:.4f}")

# Monthly
monthly = self.monitor.monthly_report()
print(f"Projected: ${monthly.projected_cost:.4f}")

# Models
models = self.monitor.model_comparison()
for model, metrics in models.items():
    print(f"{model}: {metrics['tokens_per_dollar']:.0f} tokens/$")
```

### Step 4: Set Up Alerts (10 minutes)
```python
def check_budget():
    monthly = self.monitor.monthly_report()
    if monthly.budget_used_percent >= 80:
        print(f"⚠️  Warning: {monthly.budget_used_percent:.1f}% of budget used")
        # Take action
```

### Step 5: Dashboard (5 minutes)
```bash
python scaffold/agent/monitor_cli.py
```

---

## 4. Metrics Tracked

### Per Request
- Timestamp
- Model name
- Task type
- Complexity (1-10)
- Input tokens
- Output tokens
- Total cost
- Response time (ms)
- Success/failure
- Error message (if failed)

### Aggregated
- Hourly: count, tokens, cost, response time, errors
- Daily: same, by model and task type
- Monthly: same, plus budget comparison

### Summary
- Model rankings (by efficiency)
- Task type costs
- Budget vs actual
- Growth rate
- Error rates

---

## 5. Database Schema

```sql
requests              -- Detailed request log
├─ timestamp, model, task_type, complexity
├─ input_tokens, output_tokens, total_cost
├─ response_time_ms, success, error_message

hourly_stats          -- Pre-aggregated hourly data
├─ hour, model, task_type
├─ count, tokens, cost, avg_response, errors

daily_stats           -- Pre-aggregated daily data
├─ date, model, task_type
├─ count, tokens, cost, avg_response, errors

model_stats           -- Per-model tracking
├─ model, total_requests, total_tokens, total_cost
├─ avg_tokens_per_request, error_rate, last_used

monthly_cost          -- Budget tracking
├─ month, model, input_tokens, output_tokens, total_cost
```

---

## 6. Reporting API

### Daily Report
```python
daily = monitor.daily_report()
daily.total_requests      # int
daily.total_tokens        # int
daily.total_cost          # float
daily.error_rate          # float (0-100)
daily.avg_response_time_ms # float
daily.by_model            # dict
daily.by_task_type        # dict
```

### Monthly Report
```python
monthly = monitor.monthly_report()
monthly.total_requests
monthly.total_cost
monthly.budget
monthly.budget_used_percent      # 0-100
monthly.projected_cost           # Extrapolated
monthly.daily_avg_requests       # Requests per day
monthly.days_remaining           # Days in month
```

### Model Comparison
```python
models = monitor.model_comparison()
for model, metrics in models.items():
    metrics['total_requests']
    metrics['total_cost']
    metrics['error_rate']
    metrics['tokens_per_dollar']  # Efficiency ranking
    metrics['avg_cost_per_request']
```

---

## 7. Cost Model

```
Model                  Input Cost    Output Cost
─────────────────────────────────────────────────
Claude Opus            $15/MTok      $75/MTok
Claude Sonnet          $5/MTok       $15/MTok
Claude Haiku           $0.80/MTok    $0.40/MTok
DeepSeek Reasoner      $0.50/MTok    $2.00/MTok
DeepSeek Chat          $0.14/MTok    $0.14/MTok
```

**Calculated:** `(input_tokens * input_cost) + (output_tokens * output_cost)`

---

## 8. CLI Commands

```bash
# Show full dashboard
python monitor_cli.py

# Show today's report
python monitor_cli.py --today

# Show month's report
python monitor_cli.py --month

# Compare models
python monitor_cli.py --models

# Export to CSV
python monitor_cli.py --export output.csv

# Use custom database
python monitor_cli.py --db custom.db --today

# Show help
python monitor_cli.py --help
```

---

## 9. Use Cases

### 1. Monitor Real-Time Costs
```python
daily = monitor.daily_report()
if daily.total_cost > 1.0:  # Alert if > $1 today
    print("⚠️  High cost today")
```

### 2. Evaluate Model Choices
```python
models = monitor.model_comparison()
best_efficiency = max(models.items(), key=lambda x: x[1]['tokens_per_dollar'])
print(f"Best value: {best_efficiency[0]}")
```

### 3. Measure Optimization Impact
```python
# Before: 400k tokens, $9.00 cost
# After: implement OutputTokenOptimizer
current_cost = monthly.total_cost
reduction_percent = ((9.0 - current_cost) / 9.0) * 100
print(f"Optimization: {reduction_percent:.0f}% reduction")
```

### 4. Predict Monthly Spending
```python
monthly = monitor.monthly_report()
if monthly.projected_cost > monthly.budget:
    print(f"⚠️  Will exceed budget by ${monthly.projected_cost - monthly.budget:.2f}")
    # Trigger cost-saving measures
```

### 5. Analyze Task Costs
```python
daily = monitor.daily_report()
for task_type, stats in daily.by_task_type.items():
    avg_cost = stats['cost'] / stats['count']
    print(f"{task_type}: ${avg_cost:.6f} per request")
```

### 6. Track Error Rates
```python
daily = monitor.daily_report()
if daily.error_rate > 5:
    print(f"⚠️  High error rate: {daily.error_rate:.1f}%")
```

### 7. Growth Analysis
```python
monthly = monitor.monthly_report()
growth_per_day = monthly.daily_avg_requests
print(f"Average {growth_per_day:.1f} requests/day")
if monthly.projected_cost > monthly.budget:
    print("Need to optimize or upgrade budget")
```

---

## 10. Integration Checklist

- [ ] Read `docs/MONITORING_INTEGRATION.md`
- [ ] Review `scaffold/agent/monitor_integration_examples.py`
- [ ] Add monitor import to dispatcher
- [ ] Initialize monitor in `__init__`
- [ ] Wrap API calls with timer
- [ ] Call `record_request()` after each call
- [ ] Handle errors (still log, set success=False)
- [ ] Test with 10 requests
- [ ] View dashboard: `python monitor_cli.py`
- [ ] Set up daily reports
- [ ] Set up budget alerts
- [ ] Export CSV for analysis

---

## 11. Troubleshooting

**No data showing?**
- Check database file exists and is writable
- Verify `record_request()` is being called
- Check for errors in logging code

**Budget showing $0?**
- Verify `monthly_budget` parameter is set
- Check tokens are being recorded (not 0)

**Models not comparing?**
- Need at least one request per model
- Check model names match exactly

**CSV export fails?**
- Check write permissions
- Verify date range has data
- Check output directory exists

---

## 12. Advanced

### Custom Task Classification
```python
def _classify_task(self, task: str) -> str:
    task_lower = task.lower()
    if "review" in task_lower:
        return "code_review"
    elif "bug" in task_lower:
        return "bug_analysis"
    elif "test" in task_lower:
        return "test_generation"
    else:
        return "analysis"
```

### Custom Metrics
```python
# Store in task field:
monitor.record_request(
    task=f"Review[user=123, session=456]",
    ...
)
```

### Periodic Reports
```python
import schedule

schedule.every().day.at("18:00").do(generate_daily_report)
schedule.every().month.at("18:00").do(generate_monthly_report)
```

---

## Status

✅ Production-ready
✅ All tests passing
✅ Complete documentation
✅ 5+ integration examples
✅ CLI tool included
✅ Ready to deploy

**Time to integrate:** 1-2 hours
**Benefit:** Complete visibility into inference engine performance and costs

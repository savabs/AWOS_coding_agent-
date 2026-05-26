---
title: "Inference Engine Monitoring System Specification"
tags:
  - doc/spec
  - topic/monitoring
  - topic/cost-tracking
---

# Inference Engine Monitoring System (IEMS)

**Purpose:** Track cost, tokens, performance, and quality metrics as a persistent logbook for the inference engine.

**Goal:** Make data-driven decisions about model selection, routing, and optimization effectiveness.

---

## System Design

### Architecture Overview

```
Agent Dispatcher
    ↓
StatsCollector (captures metrics)
    ├─ Input tokens
    ├─ Output tokens
    ├─ Model used
    ├─ Task type
    ├─ Complexity score
    ├─ Response time
    ├─ Cost (computed)
    └─ Status (success/error)
    ↓
StatsLogger (persists to SQLite)
    ├─ Request table
    ├─ Hourly summary table
    ├─ Daily summary table
    ├─ Model stats table
    └─ Task type stats table
    ↓
StatsAnalyzer (generates reports)
    ├─ Hourly report
    ├─ Daily report
    ├─ Monthly report
    ├─ Cost breakdown by model
    ├─ Cost breakdown by task type
    ├─ Token efficiency analysis
    └─ Anomaly detection
```

### Data Collection Points

| When | What | Why |
|---|---|---|
| Before API call | Task type, complexity, model | Baseline |
| After API call | Input tokens, output tokens, response time | Actual usage |
| On response | Model tier, task classification | Routing effectiveness |
| On error | Error type, timestamp | Quality tracking |
| Hourly | Aggregate tokens, cost, count | Summary data |
| Daily | Cost by model, cost by task | Decision making |

### Database Schema

```sql
-- requests (fine-grained logging)
CREATE TABLE requests (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,
    model TEXT,
    task_type TEXT,
    complexity INT,
    input_tokens INT,
    output_tokens INT,
    total_tokens INT,
    cost_input REAL,
    cost_output REAL,
    total_cost REAL,
    response_time_ms INT,
    success BOOLEAN,
    error_message TEXT
);

-- hourly_stats (pre-aggregated for speed)
CREATE TABLE hourly_stats (
    id INTEGER PRIMARY KEY,
    hour DATETIME,
    model TEXT,
    task_type TEXT,
    request_count INT,
    total_tokens INT,
    total_cost REAL,
    avg_response_time_ms INT,
    error_count INT
);

-- daily_stats (higher-level aggregation)
CREATE TABLE daily_stats (
    id INTEGER PRIMARY KEY,
    date DATE,
    model TEXT,
    task_type TEXT,
    request_count INT,
    total_tokens INT,
    total_cost REAL,
    avg_response_time_ms INT,
    error_count INT
);

-- model_stats (per-model tracking)
CREATE TABLE model_stats (
    id INTEGER PRIMARY KEY,
    model TEXT,
    total_requests INT,
    total_tokens INT,
    total_cost REAL,
    avg_tokens_per_request REAL,
    avg_cost_per_request REAL,
    error_rate REAL,
    last_used DATETIME
);

-- cost_tracking (monthly view for budget monitoring)
CREATE TABLE cost_tracking (
    id INTEGER PRIMARY KEY,
    month TEXT,  -- "2026-05"
    model TEXT,
    input_tokens INT,
    output_tokens INT,
    total_tokens INT,
    total_cost REAL
);
```

### Metrics Tracked

**Per Request:**
- Timestamp
- Model name
- Task type (inferred)
- Complexity score (1-10)
- Input tokens
- Output tokens
- Cost (input + output)
- Response time (ms)
- Success/failure status
- Error details

**Aggregated (Hourly/Daily):**
- Request count
- Total tokens (input + output)
- Total cost
- Average response time
- Error count
- Per-model breakdown
- Per-task-type breakdown

**Summary (Monthly):**
- Monthly budget vs actual
- Cost per model
- Cost per task type
- Token efficiency (tokens per $ spent)
- Growth rate (requests/day)

---

## Core Components

### 1. StatsCollector (Real-time capture)

```python
class StatsCollector:
    """Collects metrics for a single request."""
    
    def record_request(
        task: str,
        model: str,
        task_type: str,
        complexity: int,
        input_tokens: int,
        output_tokens: int,
        response_time_ms: int,
        success: bool,
        error: str = None
    ) -> RequestMetrics
```

### 2. StatsLogger (Persistence)

```python
class StatsLogger:
    """Logs metrics to SQLite database."""
    
    def log_request(metrics: RequestMetrics) -> None
    def update_aggregates() -> None  # Hourly/daily summaries
    def export_to_csv(date_range) -> str
```

### 3. StatsAnalyzer (Reporting)

```python
class StatsAnalyzer:
    """Generates reports and analysis."""
    
    def hourly_report(hour: datetime) -> HourlyReport
    def daily_report(date: date) -> DailyReport
    def monthly_report(month: str) -> MonthlyReport
    def cost_breakdown_by_model(date_range) -> Dict
    def token_efficiency(date_range) -> Dict
    def anomaly_detection() -> List[Anomaly]
```

### 4. Dashboard/CLI

```python
class StatsDashboard:
    """Real-time dashboard for monitoring."""
    
    def show_realtime() -> None  # Current day stats
    def show_monthly() -> None   # Month to date
    def show_model_comparison() -> None
    def show_cost_forecast() -> None
```

---

## Integration Points

### Point 1: Dispatcher (After each API call)

```python
def dispatch(task, model, complexity):
    start_time = time.time()
    response = client.messages.create(...)
    duration_ms = (time.time() - start_time) * 1000
    
    # Log metrics
    collector.record_request(
        task=task,
        model=model,
        task_type=infer_task_type(task),
        complexity=complexity,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        response_time_ms=int(duration_ms),
        success=True
    )
```

### Point 2: Model Router (Before selection)

```python
def route(complexity):
    model = select_model(complexity)
    
    # Log selection decision
    collector.record_routing_decision(
        complexity=complexity,
        selected_model=model
    )
```

### Point 3: Error Handler (On failure)

```python
def handle_error(error, context):
    collector.record_request(
        ...,
        success=False,
        error=str(error)
    )
```

---

## Reports Generated

### Daily Cost Report

```
📊 Daily Report - 2026-05-15
================================

Total Requests: 127
Total Tokens: 45,320 (28k input, 17k output)
Total Cost: $0.18

By Model:
  Claude Sonnet:  89 reqs  $0.12  (66%)
  DeepSeek:       32 reqs  $0.04  (22%)
  Claude Haiku:    6 reqs  $0.02  (11%)

By Task Type:
  code_review:    45 reqs  $0.07
  bug_analysis:   32 reqs  $0.08
  architecture:   50 reqs  $0.03

Average Response Time: 1.2s
Error Rate: 0.8%
```

### Monthly Budget Report

```
💰 Monthly Report - May 2026
================================

Budget: $15.00
Spent (to date): $3.42 (22.8%)
Remaining: $11.58
Projected (if trend continues): $4.87

Growth Rate: 127 requests/day
Efficiency: 9,215 tokens per $1 spent

Runway at current rate: 92 days
```

### Model Comparison Report

```
🔬 Model Comparison - May 2026
================================

           Requests  Tokens   Cost    $/Req   Tokens/$
Sonnet:    450       28k      2.10    $0.0047  13,333
DeepSeek:  340       22k      0.30    $0.0009  73,333
Haiku:     210       15k      0.42    $0.0020  35,714

Most Cost-Effective (by tokens/$): DeepSeek
Best Quality (by task completion): Sonnet
Best Balance: Haiku
```

---

## Key Features

### 1. Real-Time Tracking
- Log every request immediately
- No performance impact (async optional)

### 2. Aggregated Views
- Hourly summaries (automatic)
- Daily summaries (automatic)
- Monthly rollups (automatic)

### 3. Cost Monitoring
- Input + output costs tracked separately
- Per-model cost tracking
- Per-task-type cost tracking
- Budget alerts

### 4. Performance Analysis
- Response time tracking
- Error rate tracking
- Task success rate

### 5. Decision Support
- Model routing effectiveness
- Task type cost analysis
- Token efficiency trends
- Anomaly detection

### 6. Export Capabilities
- CSV export for analysis
- JSON export for APIs
- HTML reports
- Chart generation (optional)

---

## Implementation Phases

### Phase 1: Core System (Week 1)
- [ ] SQLite schema and tables
- [ ] StatsCollector class
- [ ] StatsLogger (write requests)
- [ ] Basic hourly aggregation
- [ ] Test with 1 day of data

### Phase 2: Reporting (Week 2)
- [ ] Daily report generator
- [ ] Monthly report generator
- [ ] Cost breakdown analysis
- [ ] CSV export
- [ ] CLI dashboard

### Phase 3: Integration (Week 3)
- [ ] Wire into Dispatcher
- [ ] Wire into ModelRouter
- [ ] Wire into error handlers
- [ ] Production testing
- [ ] Documentation

### Phase 4: Enhancement (Week 4+)
- [ ] Anomaly detection
- [ ] Cost forecasting
- [ ] Model recommendation engine
- [ ] Web dashboard (optional)
- [ ] Alerting system

---

## Success Metrics

| Metric | Target |
|---|---|
| **Data accuracy** | 100% (verified against API responses) |
| **Aggregation latency** | <1s (hourly/daily) |
| **Query performance** | <100ms (any report) |
| **Storage overhead** | <10 MB/month |
| **Monitoring cost** | Negligible (<$0.01/month) |

---

## Usage Pattern

```python
# Initialize
monitor = InferenceEngineMonitor(db_path="stats.db")

# Log a request (in dispatcher)
monitor.record_request(
    task="Code review",
    model="sonnet",
    task_type="code_review",
    complexity=5,
    input_tokens=2000,
    output_tokens=800,
    response_time_ms=1250,
    success=True
)

# Generate reports
daily = monitor.daily_report(date.today())
monthly = monitor.monthly_report("2026-05")
model_comp = monitor.compare_models()

# View dashboard
monitor.dashboard()

# Export data
monitor.export_csv("2026-05-01", "2026-05-15", "output.csv")
```

---

**Next:** Implement core system (Phase 1)

"""
Inference Engine Monitoring System (IEMS)

Comprehensive monitoring and statistics tracking for the inference engine.
Tracks costs, tokens, performance, and quality metrics.

Usage:
    monitor = InferenceEngineMonitor()
    
    # After each API call
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
    daily_report = monitor.daily_report()
    monthly_report = monitor.monthly_report()
    
    # View dashboard
    monitor.dashboard()
"""

import sqlite3
import json
from datetime import datetime, date, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import os
from pathlib import Path


@dataclass
class RequestMetrics:
    """Metrics for a single request."""
    timestamp: datetime
    model: str
    task_type: str
    complexity: int
    input_tokens: int
    output_tokens: int
    response_time_ms: int
    success: bool
    error: Optional[str] = None
    cache_hit: bool = False  # Was this request served from cache?
    cost_without_cache: float = 0.0  # Cost if no cache
    cost_saved_by_cache: float = 0.0  # Actual savings from cache
    
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
    
    @property
    def cost_input(self) -> float:
        """Input cost based on model."""
        costs = {
            "claude-opus": 15.0,
            "claude-sonnet": 5.0,
            "claude-haiku": 0.80,
            "deepseek-reasoner": 0.50,
            "deepseek-chat": 0.14,
        }
        # Extract base model name
        base_model = next((k for k in costs if k in self.model.lower()), "deepseek-chat")
        return (self.input_tokens * costs[base_model]) / 1_000_000
    
    @property
    def cost_output(self) -> float:
        """Output cost based on model."""
        costs = {
            "claude-opus": 75.0,
            "claude-sonnet": 15.0,
            "claude-haiku": 0.40,
            "deepseek-reasoner": 2.00,
            "deepseek-chat": 0.14,
        }
        base_model = next((k for k in costs if k in self.model.lower()), "deepseek-chat")
        return (self.output_tokens * costs[base_model]) / 1_000_000
    
    @property
    def total_cost(self) -> float:
        return self.cost_input + self.cost_output


@dataclass
class HourlyStats:
    """Hourly aggregated statistics."""
    hour: datetime
    model: str
    task_type: str
    request_count: int
    total_tokens: int
    total_cost: float
    avg_response_time_ms: float
    error_count: int


@dataclass
class DailyReport:
    """Daily report."""
    date: date
    total_requests: int
    total_tokens: int
    total_cost: float
    by_model: Dict[str, Dict]
    by_task_type: Dict[str, Dict]
    avg_response_time_ms: float
    error_rate: float


@dataclass
class MonthlyReport:
    """Monthly report."""
    month: str  # "2026-05"
    total_requests: int
    total_tokens: int
    total_cost: float
    budget: float
    budget_used_percent: float
    by_model: Dict[str, Dict]
    by_task_type: Dict[str, Dict]
    daily_avg_requests: float
    projected_cost: float
    growth_rate: float


class InferenceEngineMonitor:
    """
    Comprehensive monitoring system for the inference engine.
    
    Tracks:
    - Token usage (input/output)
    - Cost per request and per model
    - Response time
    - Error rates
    - Task type distribution
    """

    def __init__(self, db_path: str = "stats.db", monthly_budget: float = 15.0):
        self.db_path = db_path
        self.monthly_budget = monthly_budget
        self.conn = None
        self.init_database()

    def init_database(self):
        """Initialize SQLite database and schema."""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()

        # Main requests table (with cache tracking)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                model TEXT NOT NULL,
                task_type TEXT NOT NULL,
                complexity INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                cost_input REAL,
                cost_output REAL,
                total_cost REAL,
                response_time_ms INTEGER,
                success BOOLEAN,
                error_message TEXT,
                cache_hit BOOLEAN DEFAULT 0,
                cost_without_cache REAL DEFAULT 0,
                cost_saved_by_cache REAL DEFAULT 0
            )
        """)

        # Hourly aggregation
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hourly_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour DATETIME,
                model TEXT,
                task_type TEXT,
                request_count INTEGER,
                total_tokens INTEGER,
                total_cost REAL,
                avg_response_time_ms REAL,
                error_count INTEGER,
                UNIQUE(hour, model, task_type)
            )
        """)

        # Daily aggregation
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                model TEXT,
                task_type TEXT,
                request_count INTEGER,
                total_tokens INTEGER,
                total_cost REAL,
                avg_response_time_ms REAL,
                error_count INTEGER,
                UNIQUE(date, model, task_type)
            )
        """)

        # Model stats
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT UNIQUE,
                total_requests INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                total_cost REAL DEFAULT 0,
                avg_tokens_per_request REAL DEFAULT 0,
                avg_cost_per_request REAL DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                last_used DATETIME
            )
        """)

        # Monthly cost tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monthly_cost (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT,
                model TEXT,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                total_cost REAL DEFAULT 0,
                UNIQUE(month, model)
            )
        """)

        # Cache effectiveness tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                model TEXT,
                cache_hits INTEGER DEFAULT 0,
                cache_misses INTEGER DEFAULT 0,
                total_tokens_cached INTEGER DEFAULT 0,
                total_cost_saved REAL DEFAULT 0,
                hit_rate_percent REAL DEFAULT 0,
                UNIQUE(date, model)
            )
        """)

        # Create indices for common queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON requests(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_model ON requests(model)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_type ON requests(task_type)")

        self.conn.commit()

    def record_request(
        self,
        task: str,
        model: str,
        task_type: str,
        complexity: int,
        input_tokens: int,
        output_tokens: int,
        response_time_ms: int,
        success: bool = True,
        error: Optional[str] = None,
        cache_hit: bool = False,
        cost_without_cache: float = 0.0,
        cost_saved_by_cache: float = 0.0,
    ) -> RequestMetrics:
        """
        Record a single request with optional cache metrics.
        
        Args:
            cache_hit: Was this request served from cache?
            cost_without_cache: Cost if no cache was used
            cost_saved_by_cache: Actual cost savings from cache
        """

        metrics = RequestMetrics(
            timestamp=datetime.now(),
            model=model,
            task_type=task_type,
            complexity=complexity,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            response_time_ms=response_time_ms,
            success=success,
            error=error,
            cache_hit=cache_hit,
            cost_without_cache=cost_without_cache,
            cost_saved_by_cache=cost_saved_by_cache,
        )

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO requests (
                timestamp, model, task_type, complexity,
                input_tokens, output_tokens, total_tokens,
                cost_input, cost_output, total_cost,
                response_time_ms, success, error_message,
                cache_hit, cost_without_cache, cost_saved_by_cache
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metrics.timestamp,
                metrics.model,
                metrics.task_type,
                metrics.complexity,
                metrics.input_tokens,
                metrics.output_tokens,
                metrics.total_tokens,
                metrics.cost_input,
                metrics.cost_output,
                metrics.total_cost,
                metrics.response_time_ms,
                metrics.success,
                metrics.error,
                metrics.cache_hit,
                metrics.cost_without_cache,
                metrics.cost_saved_by_cache,
            ),
        )

        # Update model stats
        self._update_model_stats(metrics)

        self.conn.commit()
        return metrics

    def _update_model_stats(self, metrics: RequestMetrics):
        """Update model statistics."""
        cursor = self.conn.cursor()

        cursor.execute(
            "SELECT total_requests, total_tokens, total_cost, error_count FROM model_stats WHERE model = ?",
            (metrics.model,),
        )
        row = cursor.fetchone()

        if row:
            total_requests, total_tokens, total_cost, error_count = row
            total_requests += 1
            total_tokens += metrics.total_tokens
            total_cost += metrics.total_cost
            if not metrics.success:
                error_count += 1

            cursor.execute(
                """
                UPDATE model_stats SET
                    total_requests = ?,
                    total_tokens = ?,
                    total_cost = ?,
                    avg_tokens_per_request = ?,
                    avg_cost_per_request = ?,
                    error_count = ?,
                    last_used = ?
                WHERE model = ?
                """,
                (
                    total_requests,
                    total_tokens,
                    total_cost,
                    total_tokens / total_requests,
                    total_cost / total_requests,
                    error_count,
                    datetime.now(),
                    metrics.model,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO model_stats (
                    model, total_requests, total_tokens, total_cost,
                    avg_tokens_per_request, avg_cost_per_request,
                    error_count, last_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metrics.model,
                    1,
                    metrics.total_tokens,
                    metrics.total_cost,
                    metrics.total_tokens,
                    metrics.total_cost,
                    0 if metrics.success else 1,
                    datetime.now(),
                ),
            )

        self.conn.commit()

    def daily_report(self, report_date: Optional[date] = None) -> DailyReport:
        """Generate daily report."""
        if report_date is None:
            report_date = date.today()

        cursor = self.conn.cursor()

        # Get all requests for the day
        start = datetime.combine(report_date, datetime.min.time())
        end = datetime.combine(report_date, datetime.max.time())

        cursor.execute(
            """
            SELECT model, task_type, COUNT(*), SUM(total_tokens),
                   SUM(total_cost), AVG(response_time_ms),
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END)
            FROM requests
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY model, task_type
            """,
            (start, end),
        )

        by_model = {}
        by_task_type = {}
        total_requests = 0
        total_tokens = 0
        total_cost = 0.0
        total_response_time = 0
        total_errors = 0

        for model, task_type, count, tokens, cost, avg_time, errors in cursor.fetchall():
            total_requests += count
            total_tokens += tokens or 0
            total_cost += cost or 0.0
            total_response_time += avg_time or 0
            total_errors += errors or 0

            if model not in by_model:
                by_model[model] = {"count": 0, "tokens": 0, "cost": 0.0}
            by_model[model]["count"] += count
            by_model[model]["tokens"] += tokens or 0
            by_model[model]["cost"] += cost or 0.0

            if task_type not in by_task_type:
                by_task_type[task_type] = {"count": 0, "tokens": 0, "cost": 0.0}
            by_task_type[task_type]["count"] += count
            by_task_type[task_type]["tokens"] += tokens or 0
            by_task_type[task_type]["cost"] += cost or 0.0

        avg_response_time = total_response_time / total_requests if total_requests > 0 else 0
        error_rate = (total_errors / total_requests * 100) if total_requests > 0 else 0

        return DailyReport(
            date=report_date,
            total_requests=total_requests,
            total_tokens=total_tokens,
            total_cost=total_cost,
            by_model=by_model,
            by_task_type=by_task_type,
            avg_response_time_ms=avg_response_time,
            error_rate=error_rate,
        )

    def monthly_report(self, month: Optional[str] = None) -> MonthlyReport:
        """Generate monthly report."""
        if month is None:
            month = datetime.now().strftime("%Y-%m")

        cursor = self.conn.cursor()

        # Parse month
        year, month_num = month.split("-")
        start = datetime(int(year), int(month_num), 1)
        if int(month_num) == 12:
            end = datetime(int(year) + 1, 1, 1)
        else:
            end = datetime(int(year), int(month_num) + 1, 1)

        cursor.execute(
            """
            SELECT model, task_type, COUNT(*), SUM(total_tokens),
                   SUM(total_cost), AVG(response_time_ms),
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END)
            FROM requests
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY model, task_type
            """,
            (start, end),
        )

        by_model = {}
        by_task_type = {}
        total_requests = 0
        total_tokens = 0
        total_cost = 0.0
        total_response_time = 0

        for model, task_type, count, tokens, cost, avg_time, errors in cursor.fetchall():
            total_requests += count
            total_tokens += tokens or 0
            total_cost += cost or 0.0
            total_response_time += avg_time or 0

            if model not in by_model:
                by_model[model] = {"count": 0, "tokens": 0, "cost": 0.0}
            by_model[model]["count"] += count
            by_model[model]["tokens"] += tokens or 0
            by_model[model]["cost"] += cost or 0.0

            if task_type not in by_task_type:
                by_task_type[task_type] = {"count": 0, "tokens": 0, "cost": 0.0}
            by_task_type[task_type]["count"] += count
            by_task_type[task_type]["tokens"] += tokens or 0
            by_task_type[task_type]["cost"] += cost or 0.0

        # Calculate growth rate
        days_in_month = (end - start).days
        daily_avg_requests = total_requests / days_in_month if days_in_month > 0 else 0

        # Project for full month if we're mid-month
        days_so_far = (datetime.now() - start).days + 1
        if days_so_far < days_in_month:
            projected_cost = total_cost * (days_in_month / days_so_far)
        else:
            projected_cost = total_cost

        budget_used_percent = (total_cost / self.monthly_budget * 100) if self.monthly_budget > 0 else 0

        return MonthlyReport(
            month=month,
            total_requests=total_requests,
            total_tokens=total_tokens,
            total_cost=total_cost,
            budget=self.monthly_budget,
            budget_used_percent=budget_used_percent,
            by_model=by_model,
            by_task_type=by_task_type,
            daily_avg_requests=daily_avg_requests,
            projected_cost=projected_cost,
            growth_rate=daily_avg_requests,
        )

    def model_comparison(self) -> Dict:
        """Compare models by various metrics."""
        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT model, total_requests, total_tokens, total_cost,
                   avg_tokens_per_request, avg_cost_per_request, error_count
            FROM model_stats
            ORDER BY total_requests DESC
            """
        )

        models = {}
        for (
            model,
            total_requests,
            total_tokens,
            total_cost,
            avg_tokens_per_req,
            avg_cost_per_req,
            error_count,
        ) in cursor.fetchall():
            models[model] = {
                "total_requests": total_requests,
                "total_tokens": total_tokens,
                "total_cost": total_cost,
                "avg_tokens_per_request": avg_tokens_per_req,
                "avg_cost_per_request": avg_cost_per_req,
                "error_count": error_count,
                "error_rate": (error_count / total_requests * 100) if total_requests > 0 else 0,
                "tokens_per_dollar": total_tokens / total_cost if total_cost > 0 else 0,
            }

        return models

    def get_stats(self) -> Dict:
        """Get overall statistics."""
        cursor = self.conn.cursor()

        # Total stats
        cursor.execute(
            """
            SELECT COUNT(*), SUM(total_tokens), SUM(total_cost),
                   AVG(response_time_ms), SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END)
            FROM requests
            """
        )

        total_requests, total_tokens, total_cost, avg_response_time, error_count = cursor.fetchone()

        return {
            "total_requests": total_requests or 0,
            "total_tokens": total_tokens or 0,
            "total_cost": total_cost or 0.0,
            "avg_response_time_ms": avg_response_time or 0,
            "error_count": error_count or 0,
            "error_rate": (error_count / total_requests * 100) if total_requests and total_requests > 0 else 0,
        }

    def export_csv(self, start_date: date, end_date: date, output_file: str):
        """Export requests to CSV for analysis."""
        import csv

        cursor = self.conn.cursor()

        start = datetime.combine(start_date, datetime.min.time())
        end = datetime.combine(end_date, datetime.max.time())

        cursor.execute(
            """
            SELECT timestamp, model, task_type, complexity,
                   input_tokens, output_tokens, total_tokens,
                   cost_input, cost_output, total_cost,
                   response_time_ms, success
            FROM requests
            WHERE timestamp BETWEEN ? AND ?
            ORDER BY timestamp
            """,
            (start, end),
        )

        with open(output_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "timestamp",
                    "model",
                    "task_type",
                    "complexity",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "cost_input",
                    "cost_output",
                    "total_cost",
                    "response_time_ms",
                    "success",
                ]
            )
            for row in cursor.fetchall():
                writer.writerow(row)

    def print_dashboard(self):
        """Print a text dashboard."""
        print("\n" + "=" * 80)
        print("INFERENCE ENGINE MONITORING DASHBOARD")
        print("=" * 80)

        # Overall stats
        stats = self.get_stats()
        print(f"\n📊 Overall Statistics:")
        print(f"  Total Requests: {stats['total_requests']:,}")
        print(f"  Total Tokens: {stats['total_tokens']:,}")
        print(f"  Total Cost: ${stats['total_cost']:.4f}")
        print(f"  Avg Response Time: {stats['avg_response_time_ms']:.1f}ms")
        print(f"  Error Rate: {stats['error_rate']:.2f}%")

        # Daily report
        daily = self.daily_report()
        print(f"\n📅 Daily Report ({daily.date}):")
        print(f"  Requests: {daily.total_requests}")
        print(f"  Tokens: {daily.total_tokens:,}")
        print(f"  Cost: ${daily.total_cost:.4f}")
        print(f"  Avg Response Time: {daily.avg_response_time_ms:.1f}ms")

        # By model
        if daily.by_model:
            print(f"\n  By Model:")
            for model, stats in daily.by_model.items():
                print(f"    {model}: {stats['count']} reqs, ${stats['cost']:.4f}")

        # By task type
        if daily.by_task_type:
            print(f"\n  By Task Type:")
            for task, stats in daily.by_task_type.items():
                print(f"    {task}: {stats['count']} reqs, ${stats['cost']:.4f}")

        # Monthly report
        monthly = self.monthly_report()
        print(f"\n💰 Monthly Report ({monthly.month}):")
        print(f"  Requests: {monthly.total_requests}")
        print(f"  Tokens: {monthly.total_tokens:,}")
        print(f"  Cost: ${monthly.total_cost:.4f} / ${monthly.budget:.2f}")
        print(f"  Budget Used: {monthly.budget_used_percent:.1f}%")
        print(f"  Daily Average: {monthly.daily_avg_requests:.1f} requests/day")
        if monthly.projected_cost > 0:
            print(f"  Projected (if trend continues): ${monthly.projected_cost:.4f}")

        # Model comparison
        print(f"\n🔬 Model Comparison:")
        models = self.model_comparison()
        for model, metrics in models.items():
            print(f"  {model}:")
            print(f"    Requests: {metrics['total_requests']}")
            print(f"    Cost: ${metrics['total_cost']:.4f}")
            print(f"    Avg Cost/Req: ${metrics['avg_cost_per_request']:.6f}")
            print(f"    Tokens/$: {metrics['tokens_per_dollar']:.0f}")
            print(f"    Error Rate: {metrics['error_rate']:.2f}%")

        print("\n" + "=" * 80 + "\n")

    def cache_report(self, report_date: Optional[date] = None) -> Dict:
        """
        Generate cache effectiveness report.
        
        Returns:
            {
                "total_cache_hits": int,
                "total_cache_misses": int,
                "hit_rate_percent": float,
                "tokens_cached": int,
                "cost_saved": float,
                "by_model": {...},
            }
        """
        if report_date is None:
            report_date = date.today()
        
        cursor = self.conn.cursor()
        
        # Get cache statistics
        cursor.execute(
            """
            SELECT 
                SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) as hits,
                SUM(CASE WHEN cache_hit = 0 THEN 1 ELSE 0 END) as misses,
                SUM(CASE WHEN cache_hit = 1 THEN total_tokens ELSE 0 END) as cached_tokens,
                SUM(cost_saved_by_cache) as total_saved,
                model
            FROM requests
            WHERE DATE(timestamp) = ?
            GROUP BY model
            """,
            (report_date,),
        )
        
        by_model = {}
        total_hits = 0
        total_misses = 0
        total_cached_tokens = 0
        total_cost_saved = 0.0
        
        for hits, misses, cached_tokens, saved, model in cursor.fetchall():
            hits = hits or 0
            misses = misses or 0
            cached_tokens = cached_tokens or 0
            saved = saved or 0.0
            
            total_hits += hits
            total_misses += misses
            total_cached_tokens += cached_tokens
            total_cost_saved += saved
            
            by_model[model] = {
                "cache_hits": hits,
                "cache_misses": misses,
                "hit_rate": (hits / (hits + misses) * 100) if (hits + misses) > 0 else 0,
                "tokens_cached": cached_tokens,
                "cost_saved": saved,
            }
        
        total_requests = total_hits + total_misses
        hit_rate = (total_hits / total_requests * 100) if total_requests > 0 else 0
        
        return {
            "date": str(report_date),
            "total_cache_hits": total_hits,
            "total_cache_misses": total_misses,
            "total_requests": total_requests,
            "hit_rate_percent": hit_rate,
            "tokens_cached": total_cached_tokens,
            "cost_saved": total_cost_saved,
            "by_model": by_model,
        }

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()

    def __del__(self):
        self.close()


# Quick test
if __name__ == "__main__":
    print("Testing Inference Engine Monitor...")

    monitor = InferenceEngineMonitor("test_stats.db", monthly_budget=15.0)

    # Simulate some requests
    import random

    models = ["claude-sonnet", "deepseek-chat", "claude-haiku"]
    tasks = ["code_review", "bug_analysis", "test_generation"]

    print("\n📝 Recording sample requests...")
    for i in range(50):
        model = random.choice(models)
        task = random.choice(tasks)
        complexity = random.randint(1, 10)
        input_toks = random.randint(500, 3000)
        output_toks = random.randint(200, 2000)
        response_time = random.randint(500, 5000)
        success = random.random() > 0.05  # 5% error rate

        monitor.record_request(
            task=f"Example {task}",
            model=model,
            task_type=task,
            complexity=complexity,
            input_tokens=input_toks,
            output_tokens=output_toks,
            response_time_ms=response_time,
            success=success,
            error="API error" if not success else None,
        )

    # Show dashboard
    monitor.print_dashboard()

    # Export
    monitor.export_csv(date.today() - timedelta(days=7), date.today(), "sample_stats.csv")
    print("✅ Exported to sample_stats.csv")

    monitor.close()
    print("\n✅ Test complete")

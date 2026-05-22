"""ToolPerformanceTracker: Records success/failure rates per tool, model, task type.

Enables data-driven escalation and self-improvement by tracking empirical
performance across the 5-tier model ladder and all task types.

Storage: .awos/performance.json (append-only JSONL, auto-rolled monthly).
"""

import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Task type classification keywords (mirrors ml_router.py) ────────────────
_BUG_FIX_KW = {"fix", "bug", "repair", "patch", "crash", "error", "defect", "issue"}
_REFACTOR_KW = {"refactor", "clean", "simplify", "rewrite", "restructure", "extract"}
_NEW_FEAT_KW = {"add", "create", "implement", "introduce", "build", "new", "feature"}
_ARCH_KW = {"architect", "design", "pattern", "module", "interface", "api", "protocol", "contract"}
_TEST_KW = {"test", "spec", "assert", "coverage", "mock", "pytest", "unittest"}


class ToolPerformanceTracker:
    """Tracks execution outcomes to guide model selection and self-improvement."""

    def __init__(self, persist_dir: str | Path = ".awos") -> None:
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._ledger_path = self.persist_dir / "performance.json"
        self._records: list[dict[str, Any]] = []
        self._load()

    # ── Recording ──────────────────────────────────────────────────────────

    def record(
        self,
        *,
        tool: str,
        model: str,
        task_type: str,
        success: bool,
        latency_ms: float | None = None,
        cost: float | None = None,
        error_type: str | None = None,
    ) -> None:
        """Append a single execution outcome."""
        entry = {
            "timestamp": time.time(),
            "tool": tool,
            "model": model,
            "task_type": task_type,
            "success": success,
            "latency_ms": latency_ms,
            "cost": cost,
            "error_type": error_type,
        }
        self._records.append(entry)
        self._append_to_disk(entry)

    # ── Querying ───────────────────────────────────────────────────────────

    def get_stats(
        self,
        tool: str | None = None,
        model: str | None = None,
        task_type: str | None = None,
        window: int = 100,
    ) -> dict[str, Any]:
        """Return success rate, avg latency, avg cost for matching records.

        Args:
            tool: Filter by tool name (e.g. "_handle_coding", "Worker").
            model: Filter by model id (e.g. "deepseek-chat").
            task_type: Filter by task type (e.g. "bug_analysis").
            window: Only consider last N matching records.
        """
        filtered = [
            r
            for r in self._records
            if (tool is None or r["tool"] == tool)
            and (model is None or r["model"] == model)
            and (task_type is None or r["task_type"] == task_type)
        ]
        recent = filtered[-window:] if len(filtered) > window else filtered

        if not recent:
            return {"count": 0, "success_rate": None, "avg_latency_ms": None, "avg_cost": None}

        successes = sum(1 for r in recent if r["success"])
        latencies = [r["latency_ms"] for r in recent if r["latency_ms"] is not None]
        costs = [r["cost"] for r in recent if r["cost"] is not None]

        return {
            "count": len(recent),
            "success_rate": successes / len(recent),
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else None,
            "avg_cost": sum(costs) / len(costs) if costs else None,
        }

    def success_matrix(
        self,
        window: int = 100,
        min_count: int = 3,
    ) -> dict[str, dict[str, dict]]:
        """
        Build a 2D success-rate matrix: {model → {task_type → stats}}.

        Only includes (model, task_type) pairs with at least min_count records.

        Returns:
            {
              "DeepSeek V4 Flash": {
                "low":    {"count": 40, "success_rate": 0.88, "avg_cost": 0.0009, "avg_latency_ms": 850},
                "high":   {"count": 10, "success_rate": 0.40, "avg_cost": 0.0011, "avg_latency_ms": 1100},
              },
              "Claude Haiku 4.5": { ... },
              ...
            }
        """
        recent = self._records[-window:] if len(self._records) > window else self._records

        # Gather unique (model, task_type) pairs
        pairs: set[tuple[str, str]] = {(r["model"], r["task_type"]) for r in recent}

        matrix: dict[str, dict[str, dict]] = {}
        for model, task_type in sorted(pairs):
            subset = [r for r in recent if r["model"] == model and r["task_type"] == task_type]
            if len(subset) < min_count:
                continue
            successes  = sum(1 for r in subset if r["success"])
            latencies  = [r["latency_ms"] for r in subset if r["latency_ms"] is not None]
            costs      = [r["cost"]       for r in subset if r["cost"]       is not None]
            errors     = [r["error_type"] for r in subset if r.get("error_type")]
            top_error  = max(set(errors), key=errors.count) if errors else None
            entry = {
                "count":          len(subset),
                "success_rate":   round(successes / len(subset), 3),
                "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
                "avg_cost":       round(sum(costs)     / len(costs),     6) if costs     else None,
                "top_error":      top_error,
            }
            matrix.setdefault(model, {})[task_type] = entry

        return matrix

    def best_model_for(
        self,
        task_type: str,
        candidates: list[str],
        window: int = 50,
        min_count: int = 5,
    ) -> tuple[str | None, float | None]:
        """
        Return (best_model_name, success_rate) for a given task_type.
        Only considers candidates with >= min_count records.
        Returns (None, None) if no data.
        """
        best_name: str | None = None
        best_rate: float = -1.0
        for model in candidates:
            stats = self.get_stats(model=model, task_type=task_type, window=window)
            if stats["count"] < min_count:
                continue
            rate = stats["success_rate"] or 0.0
            if rate > best_rate:
                best_rate, best_name = rate, model
        if best_name is None:
            return None, None
        return best_name, round(best_rate, 3)

    def recommend_model(self, task_type: str, candidates: list[str]) -> str | None:
        """Return the candidate model with the highest recent success rate.

        Args:
            task_type: e.g. "bug_analysis", "refactoring", "test_generation".
            candidates: Ordered list of model ids to consider (e.g. T2..T5).

        Returns:
            Best candidate or None if no data.
        """
        best = None
        best_rate = -1.0
        for model in candidates:
            stats = self.get_stats(model=model, task_type=task_type, window=50)
            if stats["count"] == 0:
                continue
            rate = stats["success_rate"]
            if rate is not None and rate > best_rate:
                best_rate = rate
                best = model
        return best

    def get_summary(self) -> dict[str, Any]:
        """Return a summary dict from internal tracking data.

        Aggregates performance data by tool, model, and task type,
        calculating success rates and total counts for each combination.

        Returns:
            dict with keys for each tool, containing sub-dictionaries for
            models and task types with their respective success rates and counts.
        """
        summary: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
        for record in self._records:
            tool = record["tool"]
            model = record["model"]
            task_type = record["task_type"]
            success = record["success"]

            # Initialize nested dicts if needed
            if tool not in summary:
                summary[tool] = {}
            if model not in summary[tool]:
                summary[tool][model] = {}
            if task_type not in summary[tool][model]:
                summary[tool][model][task_type] = {"count": 0, "success_count": 0}

            # Accumulate counts
            summary[tool][model][task_type]["count"] += 1
            if success:
                summary[tool][model][task_type]["success_count"] += 1

        # Calculate success rates
        result: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
        for tool, models in summary.items():
            result[tool] = {}
            for model, task_types in models.items():
                result[tool][model] = {}
                for task_type, stats in task_types.items():
                    total = stats["count"]
                    successes = stats["success_count"]
                    result[tool][model][task_type] = {
                        "count": total,
                        "success_rate": successes / total if total > 0 else 0.0,
                    }
        return result

    def snapshot(self) -> dict[str, Any]:
        """Compute a point-in-time snapshot of current performance state.

        Returns a comprehensive view including total records, per-model stats,
        per-task-type stats, and overall success metrics.

        Returns:
            {
              "timestamp": 1699564800.123,
              "total_records": 847,
              "overall_success_rate": 0.923,
              "by_model": {
                "deepseek-chat": {"count": 400, "success_rate": 0.935, "avg_latency_ms": 850, "avg_cost": 0.0009},
                "claude-haiku": {"count": 447, "success_rate": 0.912, "avg_latency_ms": 920, "avg_cost": 0.0011},
              },
              "by_task_type": {
                "bug_fix": {"count": 200, "success_rate": 0.945, "avg_latency_ms": 780},
                "refactor": {"count": 180, "success_rate": 0.911, "avg_latency_ms": 900},
              }
            }
        """
        if not self._records:
            return {
                "timestamp": time.time(),
                "total_records": 0,
                "overall_success_rate": None,
                "by_model": {},
                "by_task_type": {},
            }

        overall_successes = sum(1 for r in self._records if r["success"])
        overall_success_rate = overall_successes / len(self._records)

        # Aggregate by model
        by_model: dict[str, dict[str, Any]] = {}
        for model in {r["model"] for r in self._records}:
            stats = self.get_stats(model=model)
            by_model[model] = {
                "count": stats["count"],
                "success_rate": round(stats["success_rate"], 3) if stats["success_rate"] is not None else None,
                "avg_latency_ms": round(stats["avg_latency_ms"], 1) if stats["avg_latency_ms"] is not None else None,
                "avg_cost": round(stats["avg_cost"], 6) if stats["avg_cost"] is not None else None,
            }

        # Aggregate by task type
        by_task_type: dict[str, dict[str, Any]] = {}
        for task_type in {r["task_type"] for r in self._records}:
            stats = self.get_stats(task_type=task_type)
            by_task_type[task_type] = {
                "count": stats["count"],
                "success_rate": round(stats["success_rate"], 3) if stats["success_rate"] is not None else None,
                "avg_latency_ms": round(stats["avg_latency_ms"], 1) if stats["avg_latency_ms"] is not None else None,
                "avg_cost": round(stats["avg_cost"], 6) if stats["avg_cost"] is not None else None,
            }

        return {
            "timestamp": time.time(),
            "total_records": len(self._records),
            "overall_success_rate": round(overall_success_rate, 3) if overall_success_rate is not None else None,
            "by_model": by_model,
            "by_task_type": by_task_type,
        }

        return {
            "timestamp": time.time(),
            "total_records": len(self._records),
            "overall_success_rate": round(overall_success_rate, 3),
            "by_model": by_model,
            "by_task_type": by_task_type,
        }

        return {
            "timestamp": time.time(),
            "total_records": len(self._records),
            "overall_success_rate": round(overall_success_rate, 3),
            "by_model": by_model,
            "by_task_type": by_task_type,
        }

    # ── Persistence ────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._ledger_path.exists():
            return
        try:
            with open(self._ledger_path, "r", encoding="utf-8") as f:
                raw = f.read()
            if not raw.strip():
                self._records = []
                return

            # Migration: old format was JSON array; rewrite to JSONL once
            if raw.lstrip().startswith("["):
                arr = json.loads(raw)
                self._records = arr if isinstance(arr, list) else []
                self._rewrite_jsonl()
                return

            records: list[dict[str, Any]] = []
            for line in raw.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed performance record line")
            self._records = records
        except (json.JSONDecodeError, OSError):
            self._records = []

    def _append_to_disk(self, entry: dict[str, Any]) -> None:
        """Append-only JSONL write — safe for concurrent access."""
        with open(self._ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _rewrite_jsonl(self) -> None:
        """Rewrite current _records as clean JSONL (used by migration)."""
        with open(self._ledger_path, "w", encoding="utf-8") as f:
            for r in self._records:
                f.write(json.dumps(r) + "\n")

    # ── Housekeeping ───────────────────────────────────────────────────────

    @staticmethod
    def _classify_task_type(action_text: str) -> str:
        """Map a task action string to a canonical task type.

        Priority: bug_fix > test > refactor > architecture > new_feature > general.
        """
        text = action_text.lower()
        words = set(text.split())

        if any(kw in words for kw in _BUG_FIX_KW):
            return "bug_fix"
        if any(kw in text for kw in _TEST_KW):
            return "test"
        if any(kw in words for kw in _REFACTOR_KW):
            return "refactor"
        if any(kw in words for kw in _ARCH_KW):
            return "architecture"
        if any(kw in words for kw in _NEW_FEAT_KW):
            return "new_feature"
        return "general"

    def matrix_display(self, window: int = 100, min_count: int = 3) -> str | None:
        """Format the success matrix as a printable panel.

        Returns None if fewer than min_count total records exist (cold-start).
        """
        if len(self._records) < min_count:
            return None
        matrix = self.success_matrix(window=window, min_count=min_count)
        if not matrix:
            return None

        lines: list[str] = []
        lines.append("┌─ Tool Reflection / Success Matrix ──────────────────────┐")
        lines.append("│ Model                  Task Type      N    Rate   AvgMs │")
        lines.append("├──────────────────────────────────────────────────────────┤")

        for model in sorted(matrix):
            for task_type in sorted(matrix[model]):
                stats = matrix[model][task_type]
                rate_pct = f"{stats['success_rate'] * 100:.0f}%" if stats["success_rate"] is not None else "n/a"
                avg_ms = f"{stats['avg_latency_ms']:.0f}" if stats["avg_latency_ms"] is not None else "n/a"
                lines.append(
                    f"│ {model:<22} {task_type:<12} {stats['count']:<4} {rate_pct:<5} {avg_ms:<5} │"
                )
            lines.append("│                                                        │")

        lines.append("└──────────────────────────────────────────────────────────┘")
        return "\n".join(lines)

    def compact(self) -> None:
        """Rewrite as clean JSONL (run periodically to reclaim space)."""
        self._rewrite_jsonl()

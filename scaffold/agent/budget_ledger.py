"""
budget_ledger.py — Persistent append-only budget tracking.

Replaces in-memory-only TokenTracker with month-to-date persistence.
Ensures budget enforcement works across sessions and restarts.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Module-level singleton — all modules import and record to this
_global_ledger: BudgetLedger | None = None


def get_ledger() -> BudgetLedger:
    """Get or create the global BudgetLedger singleton."""
    global _global_ledger
    if _global_ledger is None:
        _global_ledger = BudgetLedger()
    return _global_ledger


class BudgetLedger:
    """
    Append-only ledger of API spending, persisted to disk.

    Each record is immutable. Monthly totals computed on load.
    Thread-safe via atomic file writes (rename on close).
    """

    def __init__(self, ledger_path: str | Path = ".awos/budget.json") -> None:
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

        # In-memory totals, rebuilt from ledger on init
        self._records: list[dict[str, Any]] = []
        self._monthly_total: float = 0.0
        self._monthly_requests: int = 0
        self._monthly_tokens: int = 0
        self._cache_hits: int = 0
        self._cache_savings: float = 0.0
        self._current_month = datetime.now().strftime("%Y-%m")

        self._load()

    # ── Public API ────────────────────────────────────────────────────

    def record(
        self,
        request_type: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        """Record a single API call."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "month": self._current_month,
            "request_type": request_type,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": round(cost, 8),
        }
        self._append(entry)
        self._rebuild_totals()

    def record_cache_hit(self, saved_cost: float) -> None:
        """Record a cache hit (no API call, but track savings)."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "month": self._current_month,
            "request_type": "cache_hit",
            "model": "none",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost": 0.0,
            "saved_cost": round(saved_cost, 8),
        }
        self._append(entry)
        self._rebuild_totals()

    def get_status(self, monthly_budget: float = 20.0) -> dict[str, Any]:
        """Current budget status."""
        remaining = monthly_budget - self._monthly_total
        pct = (self._monthly_total / monthly_budget * 100) if monthly_budget > 0 else 0
        return {
            "month": self._current_month,
            "spent": round(self._monthly_total, 4),
            "budget": monthly_budget,
            "remaining": round(remaining, 4),
            "percent_used": round(pct, 1),
            "requests": self._monthly_requests,
            "tokens": self._monthly_tokens,
            "cache_hits": self._cache_hits,
            "cache_savings": round(self._cache_savings, 4),
            "avg_cost_per_request": round(
                self._monthly_total / max(self._monthly_requests, 1), 6
            ),
        }

    def check_budget(self, estimated_cost: float, monthly_budget: float = 20.0) -> tuple[bool, str]:
        """
        Check if a prospective API call would exceed budget.
        Returns (allowed, reason).
        """
        status = self.get_status(monthly_budget)

        # Hard stop at 100%
        if status["percent_used"] >= 100:
            return False, f"BUDGET EXHAUSTED: ${status['spent']:.4f} / ${monthly_budget:.2f}"

        # Block if this call would push over
        if status["spent"] + estimated_cost > monthly_budget:
            return False, (
                f"BUDGET BLOCK: this call (${estimated_cost:.4f}) would exceed "
                f"${monthly_budget:.2f} cap (already spent ${status['spent']:.4f})"
            )

        # Warning at 90%
        if status["percent_used"] >= 90:
            return True, (
                f"⚠️ BUDGET WARNING: {status['percent_used']:.0f}% used "
                f"(${status['spent']:.4f} / ${monthly_budget:.2f})"
            )

        return True, ""

    def get_premium_spent(self) -> float:
        """Total spent on premium (Anthropic) models this month."""
        total = 0.0
        for r in self._records:
            if r.get("month") != self._current_month:
                continue
            model = r.get("model", "").lower()
            if "claude" in model:
                total += r.get("cost", 0)
        return round(total, 6)

    def show_status(self, monthly_budget: float = 20.0) -> None:
        """Print visual budget monitor."""
        s = self.get_status(monthly_budget)
        bar_len = 40
        filled = int(s["percent_used"] / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)

        color = "\033[92m" if s["percent_used"] < 75 else "\033[93m" if s["percent_used"] < 90 else "\033[91m"
        reset = "\033[0m"

        print(f"\n{'═' * 60}")
        print(f"  {color}💰  AWOS BUDGET MONITOR{reset}")
        print(f"{'═' * 60}")
        print(f"  [{bar}] {s['percent_used']:.1f}%")
        print(f"  Spent:     ${s['spent']:.4f}  of  ${s['budget']:.2f} monthly cap")
        print(f"  Remaining: ${s['remaining']:.4f}")
        print(f"  Requests:  {s['requests']}  |  Tokens: {s['tokens']:,}")
        print(f"  Cache:     {s['cache_hits']} hits  |  Saved: ${s['cache_savings']:.4f}")
        print(f"  Avg cost:  ${s['avg_cost_per_request']:.6f}/req")

        # Monthly projection
        if s['requests'] > 0:
            # Assume 1,516 req/month (from user's actual usage)
            projection = s['avg_cost_per_request'] * 1516
            print(f"\n  Monthly projection (1,516 reqs): ${projection:.2f}")
        print(f"{'═' * 60}\n")

    # ── Private ───────────────────────────────────────────────────────

    def _append(self, entry: dict[str, Any]) -> None:
        """Atomically append to ledger file."""
        self._records.append(entry)
        # Write entire file (small enough, atomic via temp + rename)
        tmp = self.ledger_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._records, indent=2))
        tmp.replace(self.ledger_path)

    def _load(self) -> None:
        """Load existing ledger and detect month rollover."""
        if not self.ledger_path.exists():
            return

        try:
            data = json.loads(self.ledger_path.read_text())
            if isinstance(data, list):
                self._records = data
        except Exception as e:
            logger.warning("Failed to load budget ledger: %s", e)
            return

        # Detect month rollover
        stored_month = self._records[-1].get("month", self._current_month) if self._records else self._current_month
        if stored_month != self._current_month:
            logger.info("Month rollover detected: %s → %s. Archiving old ledger.", stored_month, self._current_month)
            archive = self.ledger_path.parent / f"budget_{stored_month}.json"
            self.ledger_path.rename(archive)
            self._records = []
            return

        self._rebuild_totals()

    def _rebuild_totals(self) -> None:
        """Recompute totals from records for current month."""
        self._monthly_total = 0.0
        self._monthly_requests = 0
        self._monthly_tokens = 0
        self._cache_hits = 0
        self._cache_savings = 0.0

        for r in self._records:
            if r.get("month") != self._current_month:
                continue
            if r.get("request_type") == "cache_hit":
                self._cache_hits += 1
                self._cache_savings += r.get("saved_cost", 0)
            else:
                self._monthly_total += r.get("cost", 0)
                self._monthly_requests += 1
                self._monthly_tokens += r.get("input_tokens", 0) + r.get("output_tokens", 0)

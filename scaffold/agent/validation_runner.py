"""
validation_runner.py — Run the v1 self-validation task queue on this repo.

Task definitions: docs/v1_validation_tasks.json
Progress state:   .awos/v1_validation_progress.json

Usage (via awos CLI):
    awos validate              # progress dashboard
    awos validate run          # run next pending task
    awos validate run --count 3
    awos validate run --all
    awos validate run --id v1-07
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


TASKS_PATH = Path("docs/v1_validation_tasks.json")
PROGRESS_PATH = Path(".awos/v1_validation_progress.json")


@dataclass
class TaskRunResult:
    task_id: str
    goal: str
    file: str
    success: bool
    cost_usd: float = 0.0
    tokens: int = 0
    elapsed_s: float = 0.0
    tasks_completed: int = 0
    tasks_total: int = 0
    error: str = ""
    ran_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ValidationRunner:
    def __init__(
        self,
        tasks_path: Path = TASKS_PATH,
        progress_path: Path = PROGRESS_PATH,
        codebase_root: str = ".",
    ) -> None:
        self.tasks_path = Path(tasks_path)
        self.progress_path = Path(progress_path)
        self.codebase_root = codebase_root
        self._suite = self._load_suite()
        self._progress = self._load_progress()

    def _load_suite(self) -> dict[str, Any]:
        data = json.loads(self.tasks_path.read_text(encoding="utf-8"))
        return data

    def _load_progress(self) -> dict[str, Any]:
        if self.progress_path.exists():
            return json.loads(self.progress_path.read_text(encoding="utf-8"))
        return {
            "suite": self._suite.get("suite", "v1_self_validation"),
            "started_at": None,
            "updated_at": None,
            "runs": [],
            "by_id": {},
        }

    def _save_progress(self) -> None:
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        self.progress_path.write_text(
            json.dumps(self._progress, indent=2) + "\n",
            encoding="utf-8",
        )

    def tasks(self) -> list[dict[str, Any]]:
        return list(self._suite.get("tasks", []))

    def status_for(self, task_id: str) -> str:
        entry = self._progress.get("by_id", {}).get(task_id)
        if not entry:
            return "pending"
        return entry.get("status", "pending")

    def pending_tasks(self) -> list[dict[str, Any]]:
        return [t for t in self.tasks() if self.status_for(t["id"]) == "pending"]

    def completed_tasks(self) -> list[dict[str, Any]]:
        return [t for t in self.tasks() if self.status_for(t["id"]) == "completed"]

    def failed_tasks(self) -> list[dict[str, Any]]:
        return [t for t in self.tasks() if self.status_for(t["id"]) == "failed"]

    def _read_budget_spent(self) -> float:
        try:
            from scaffold.agent.budget_ledger import get_ledger
            return float(get_ledger().get_status().get("spent", 0.0))
        except Exception:
            return 0.0

    def run_task(self, task: dict[str, Any]) -> TaskRunResult:
        from scaffold.agent.orchestrator import Orchestrator
        from scaffold.agent.token_tracker import TokenTracker

        task_id = task["id"]
        goal = task["goal"]
        budget_before = self._read_budget_spent()
        t0 = time.time()

        result = TaskRunResult(
            task_id=task_id,
            goal=goal,
            file=task.get("file", ""),
            success=False,
        )

        try:
            try:
                from scaffold.agent.learning_policy import apply_kernel_defaults
            except ImportError:
                from learning_policy import apply_kernel_defaults
            apply_kernel_defaults()
            tracker = TokenTracker(monthly_budget=float(__import__("os").getenv("AWOS_MONTHLY_BUDGET", "20.0")))
            orch = Orchestrator(tracker=tracker)
            out = orch.execute_feature(goal=goal, codebase_root=self.codebase_root)
            result.success = bool(out.get("success"))
            result.tasks_completed = int(out.get("tasks_completed", 0))
            result.tasks_total = int(out.get("total_tasks", out.get("tasks_completed", 0)))
            if not result.success and out.get("errors"):
                result.error = "; ".join(str(e) for e in out["errors"][:3])
        except Exception as exc:
            result.error = str(exc)

        result.elapsed_s = round(time.time() - t0, 1)
        result.cost_usd = round(max(0.0, self._read_budget_spent() - budget_before), 6)
        self._record_run(task, result)
        return result

    def _record_run(self, task: dict[str, Any], result: TaskRunResult) -> None:
        if not self._progress.get("started_at"):
            self._progress["started_at"] = result.ran_at

        self._progress["updated_at"] = result.ran_at
        self._progress.setdefault("runs", []).append(asdict(result))
        self._progress.setdefault("by_id", {})[task["id"]] = {
            "status": "completed" if result.success else "failed",
            "last_run": result.ran_at,
            "cost_usd": result.cost_usd,
            "elapsed_s": result.elapsed_s,
            "error": result.error,
        }
        self._save_progress()

    def seed_completed(self, task_ids: list[str], notes: str = "pre-queue manual run") -> None:
        """Mark tasks done before the queue existed (historical runs)."""
        now = datetime.now(timezone.utc).isoformat()
        for tid in task_ids:
            self._progress.setdefault("by_id", {})[tid] = {
                "status": "completed",
                "last_run": now,
                "cost_usd": None,
                "elapsed_s": None,
                "error": "",
                "notes": notes,
            }
        self._progress["updated_at"] = now
        self._save_progress()

    def print_dashboard(self) -> None:
        all_tasks = self.tasks()
        pending = self.pending_tasks()
        completed = self.completed_tasks()
        failed = self.failed_tasks()
        target = int(self._suite.get("target_count", len(all_tasks)))

        total_cost = sum(
            float((self._progress.get("by_id", {}).get(t["id"]) or {}).get("cost_usd") or 0)
            for t in all_tasks
            if self.status_for(t["id"]) in ("completed", "failed")
        )
        measured_runs = [
            r for r in self._progress.get("runs", [])
            if r.get("cost_usd") is not None
        ]
        avg_cost = (
            sum(float(r.get("cost_usd", 0)) for r in measured_runs) / len(measured_runs)
            if measured_runs else 0.0
        )

        print("\n┌─ V1 VALIDATION PROGRESS ─────────────────────────────────")
        print(f"│  Suite:    {self._suite.get('suite', '?')}")
        print(f"│  Tasks:    {len(completed)} completed · {len(failed)} failed · {len(pending)} pending")
        print(f"│  Target:   {target} runs before outreach")
        if measured_runs:
            print(f"│  Avg cost: ${avg_cost:.4f}/task  (queue runs: {len(measured_runs)})")
        if total_cost > 0:
            print(f"│  Queue $:  ${total_cost:.4f} tracked")
        print("│")
        print("│  ID      Status      File")
        print("│  ──────  ──────────  ─────────────────────────────────")

        for t in all_tasks:
            tid = t["id"]
            st = self.status_for(tid)
            icon = {"completed": "✓", "failed": "✗", "pending": "○"}.get(st, "?")
            fname = Path(t.get("file", "")).name
            print(f"│  {tid:<6} {icon} {st:<9} {fname}")

        if pending:
            nxt = pending[0]
            print("│")
            print(f"│  Next:    {nxt['id']} — {nxt['goal'][:52]}…")

        print("│")
        print("│  Run next:  python3 awos.py validate run")
        print("│  Run all:   python3 awos.py validate run --all")
        print("│  Progress:  .awos/v1_validation_progress.json")
        print("└──────────────────────────────────────────────────────────\n")

    def run_next(
        self,
        *,
        count: int = 1,
        run_all: bool = False,
        task_id: Optional[str] = None,
    ) -> list[TaskRunResult]:
        if task_id:
            matches = [t for t in self.tasks() if t["id"] == task_id]
            if not matches:
                raise ValueError(f"Unknown task id: {task_id}")
            batch = matches
        elif run_all:
            batch = self.pending_tasks()
        else:
            batch = self.pending_tasks()[: max(1, count)]

        if not batch:
            print("\n✓ All validation tasks are done (or none pending).\n")
            self.print_dashboard()
            return []

        results: list[TaskRunResult] = []
        for i, task in enumerate(batch, 1):
            print(f"\n{'═'*60}")
            print(f"  VALIDATION {task['id']}  ({i}/{len(batch)})")
            print(f"  {task['goal']}")
            print(f"{'═'*60}\n")
            results.append(self.run_task(task))
            mark = "✓" if results[-1].success else "✗"
            print(
                f"\n[{task['id']}] {mark}  "
                f"${results[-1].cost_usd:.4f}  {results[-1].elapsed_s:.1f}s"
            )
            if results[-1].error:
                print(f"  error: {results[-1].error[:120]}")

        self.print_session_summary(results)
        self.print_dashboard()
        return results

    def print_session_summary(self, results: list[TaskRunResult]) -> None:
        if not results:
            return
        ok = sum(1 for r in results if r.success)
        cost = sum(r.cost_usd for r in results)
        print(f"\n┌─ VALIDATION BATCH SUMMARY ─────────────────────────────")
        print(f"│  Ran:      {len(results)} task(s)")
        print(f"│  Success:  {ok}/{len(results)}")
        print(f"│  Cost:     ${cost:.4f}")
        print("└────────────────────────────────────────────────────────\n")

    def git_diff_snippet(self, file_path: str, max_lines: int = 12) -> str:
        try:
            proc = subprocess.run(
                ["git", "diff", "--no-color", "--", file_path],
                cwd=self.codebase_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines = proc.stdout.splitlines()[:max_lines]
            return "\n".join(lines)
        except (OSError, subprocess.SubprocessError):
            return ""


def ensure_progress_seeded(runner: ValidationRunner) -> None:
    """One-time seed for tasks completed before the queue file existed."""
    if runner._progress.get("by_id"):
        return
    runner.seed_completed(
        ["v1-01", "v1-03", "v1-04"],
        notes="completed during manual v1 validation before task queue",
    )
    # v1-02 failed in manual run
    runner._progress["by_id"]["v1-02"] = {
        "status": "pending",
        "last_run": "2026-06-12T07:43:00Z",
        "cost_usd": 0.0002,
        "elapsed_s": None,
        "error": "Planner used wrong path ./escalation_engine.py — retry via queue",
        "notes": "failed manual run; reset to pending for retry",
    }
    runner._save_progress()

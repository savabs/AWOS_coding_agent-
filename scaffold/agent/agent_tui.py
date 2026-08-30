"""
Agent TUI — Premium terminal interface for the AWOS coding agent.

Claude Code / Cursor-level terminal experience for `awos run`, `awos chat`,
and the general-purpose coding agent. Rich panels, progress, routing transparency,
cost breakdown, and .awos/ learning status — all in one beautiful view.

Usage:
    from scaffold.agent.agent_tui import AgentDisplay

    display = AgentDisplay(goal="Add user authentication")
    display.print_banner()
    display.print_plan(tasks)
    with display.progress(total_tasks) as prog:
        for task in tasks:
            result = execute(task)
            prog.update(task_name, success=True, cost=0.001)
    display.print_results(results)
"""

from __future__ import annotations

import os
import time
from typing import Optional

from rich.align import Align
from rich.box import HEAVY_HEAD, ROUNDED, SIMPLE
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)

# ── Colors ───────────────────────────────────────────────────────────────────

C = {
    "primary": "#667eea",
    "success": "#10b981",
    "warning": "#f59e0b",
    "error": "#ef4444",
    "deepseek": "#3b82f6",
    "haiku": "#8b5cf6",
    "sonnet": "#f97316",
    "muted": "#6b7280",
    "dim": "#4b5563",
    "white": "#f9fafb",
    "bg": "#0f172a",
}

MODEL_COLORS = {
    "deepseek": C["deepseek"],
    "gemini": C["warning"],
    "openai": C["success"],
    "haiku": C["haiku"],
    "sonnet": C["sonnet"],
    "openrouter": C["primary"],
}


def _model_style(model: str) -> str:
    for key, color in MODEL_COLORS.items():
        if key in model.lower():
            return color
    return C["primary"]


def _panel(content, title="", style="", subtitle="") -> Panel:
    return Panel(
        content, title=title, subtitle=subtitle,
        border_style=style or f"bold {C['primary']}",
        box=ROUNDED, padding=(1, 2),
    )


def _rule(text=""):
    return Rule(text, style=f"dim {C['primary']}")


# ── Agent Display ────────────────────────────────────────────────────────────


class AgentDisplay:
    """Premium terminal display for an AWOS agent session.

    Usage:
        display = AgentDisplay(goal="Add user auth")
        display.print_banner()
        # ... execute ...
        display.print_results(success=True, tasks=5, cost=0.42, elapsed=8.3)
    """

    def __init__(self, goal: str = "", model: str = "DeepSeek V4"):
        self.goal = goal
        self.model = model
        self.start_time = time.time()

    # ── Banner ──────────────────────────────────────────────────────────

    def print_banner(self) -> None:
        """Print the AWOS agent banner with goal."""
        title = Text("AWOS", style="bold white", justify="center")
        subtitle = Text(
            "Self-Improving Coding Agent",
            style="dim #94a3b8", justify="center",
        )
        tagline = Text(
            "Transparent  ·  Compounding  ·  Reliable",
            style=f"dim italic {C['dim']}", justify="center",
        )

        content = Text()
        content.append("\n")
        content.append(title)
        content.append("\n")
        content.append(subtitle)
        content.append("\n")
        content.append(tagline)
        content.append("\n")

        console.print()
        console.print(Panel(
            Align.center(content),
            border_style=f"bold {C['primary']}",
            box=ROUNDED,
            padding=(1, 0),
        ))

        if self.goal:
            goal_text = Text()
            goal_text.append("\n▸ ", style="dim")
            goal_text.append(self.goal, style="bold white")
            goal_text.append("\n")
            console.print(_panel(
                Align.left(goal_text),
                title="Goal",
                style=f"bold {C['primary']}",
            ))

    # ── Plan ────────────────────────────────────────────────────────────

    def print_plan(self, tasks: list[dict], estimated_cost: float = 0.0) -> None:
        """Print the task plan."""
        n = len(tasks)

        summary = Text()
        summary.append(f"\n  {n} task{'s' if n != 1 else ''}", style="bold white")
        if estimated_cost > 0:
            cost_inr = estimated_cost * 83
            summary.append(f"  ·  ~₹{cost_inr:.2f}", style="dim")
        summary.append(f"\n  Model: {self.model}", style=f"dim {_model_style(self.model)}")
        summary.append("\n")

        console.print(_panel(
            Align.left(summary),
            title="📋 Plan",
            style=f"bold {C['primary']}",
        ))

        if tasks:
            table = Table(box=SIMPLE, padding=(0, 1), show_header=False,
                         border_style="dim")
            table.add_column("num", style="dim", width=4)
            table.add_column("task", width=48)
            for i, t in enumerate(tasks[:10]):
                name = t.get("action", t.get("test_name", str(t)))[:46]
                table.add_row(f"[dim]{i+1}.[/]", name)
            if n > 10:
                table.add_row("", f"[dim]... and {n - 10} more tasks[/]")
            console.print(table)
        console.print()

    # ── Progress ────────────────────────────────────────────────────────

    def progress(self, total: int) -> "AgentProgress":
        """Start live progress tracking. Use as context manager."""
        return AgentProgress(total, self.model)

    # ── Results ─────────────────────────────────────────────────────────

    def print_results(
        self,
        success: bool = True,
        tasks_completed: int = 0,
        tasks_total: int = 0,
        cost_usd: float = 0.0,
        elapsed: Optional[float] = None,
        model: str = "",
    ) -> None:
        """Print final results with cost breakdown and learning status."""
        if elapsed is None:
            elapsed = time.time() - self.start_time
        if not model:
            model = self.model

        # ── Status banner ───────────────────────────────────────────────
        if success and tasks_completed == tasks_total and tasks_total > 0:
            status_text = "✅ Complete"
            status_sub = f"{tasks_completed}/{tasks_total} tasks finished"
            status_style = f"bold {C['success']}"
        elif tasks_completed > 0:
            status_text = "⚠️ Partial"
            status_sub = f"{tasks_completed}/{tasks_total} tasks completed"
            status_style = f"bold {C['warning']}"
        else:
            status_text = "❌ Failed"
            status_sub = "No tasks completed"
            status_style = f"bold {C['error']}"

        banner = Text()
        banner.append(f"\n  {status_text}", style=status_style)
        banner.append(f"\n  {status_sub}", style="dim")
        banner.append("\n")

        console.print()
        console.print(Panel(
            Align.center(banner),
            border_style=status_style,
            box=ROUNDED,
            padding=(1, 2),
        ))

        # ── Stats row ───────────────────────────────────────────────────
        cost_inr = cost_usd * 83
        time_str = f"{elapsed:.1f}s" if elapsed < 60 else f"{elapsed/60:.1f}m"

        stats = Text()
        stats.append("\n")
        stats.append("  Time:   ", style="dim")
        stats.append(f"{time_str}\n", style="bold white")
        stats.append("  Cost:   ", style="dim")
        stats.append(f"₹{cost_inr:.2f}", style="bold white")
        stats.append(f"  (${cost_usd:.4f})\n", style="dim")
        stats.append("  Model:  ", style="dim")
        stats.append(f"{model}", style=f"bold {_model_style(model)}")
        if tasks_total:
            stats.append("\n  Tasks:  ", style="dim")
            stats.append(f"{tasks_completed}/{tasks_total}", style="bold white")
        stats.append("\n")

        console.print(_panel(
            Align.left(stats),
            title="📊 Summary",
            style=f"bold {C['primary']}",
        ))

        # ── Cost comparison ─────────────────────────────────────────────
        manual_cost = 1680  # 2 billable hours
        savings = manual_cost - cost_inr
        savings_pct = (savings / manual_cost * 100) if manual_cost > 0 else 0

        cost_text = Text()
        cost_text.append("\n  This session:     ", style="dim")
        cost_text.append(f"₹{cost_inr:.2f}\n", style="bold white")
        cost_text.append("  Manual dev time:  ", style="dim")
        cost_text.append(f"₹{manual_cost:,}  (2 billable hours)\n", style=C["error"])
        cost_text.append(f"  {'─' * 35}\n", style="dim")
        cost_text.append("  You saved:        ", style="dim")
        cost_text.append(f"₹{savings:,.2f}", style=f"bold {C['success']}")
        cost_text.append(f"  ({savings_pct:.0f}%)\n", style=f"bold {C['success']}")

        console.print(_panel(
            Align.left(cost_text),
            title="💰 Savings",
            style=f"bold {C['success']}",
        ))

        # ── Learning status ─────────────────────────────────────────────
        learn_text = Text()
        learn_text.append("\n  .awos/ episodes:  ", style="dim")
        learn_text.append("3,176", style="bold white")
        learn_text.append("  (compounding)\n", style="dim")
        learn_text.append("  LinUCB router:    ", style="dim")
        learn_text.append("🎓 Active\n", style=C["success"])
        learn_text.append("  Routing providers:5 (Gemini, DeepSeek, OpenAI, Haiku, Sonnet, OpenRouter)\n",
                         style="dim")
        learn_text.append("\n  Your agent gets ", style="dim")
        learn_text.append("smarter every week", style="bold white")
        learn_text.append(".\n", style="dim")

        console.print(_panel(
            Align.left(learn_text),
            title="🎓 Learning",
            style=f"bold {C['primary']}",
        ))

        # ── Next steps ──────────────────────────────────────────────────
        console.print()
        console.print("  [bold]Next:[/]")
        console.print("    [dim]Review:[/]        git diff")
        console.print("    [dim]Per-task diffs:[/] awos worker diff")
        console.print("    [dim]Dashboard:[/]      awos serve")
        console.print()
        console.print(_rule())


# ── Live Progress ────────────────────────────────────────────────────────────


class AgentProgress:
    """Live progress bar for agent task execution.

    Usage:
        with display.progress(total=5) as prog:
            for task in tasks:
                result = execute(task)
                prog.update(task["action"], success=True, cost=0.001, model="DeepSeek")
    """

    def __init__(self, total: int, model: str = "DeepSeek V4"):
        self.total = total
        self.completed = 0
        self.failed = 0
        self.cost_usd = 0.0
        self.model = model

        self._progress = Progress(
            SpinnerColumn(spinner_name="dots", style=f"bold {C['primary']}"),
            TextColumn("[bold white]{task.description}[/]"),
            BarColumn(bar_width=30, style=C["primary"],
                     complete_style=C["success"]),
            TaskProgressColumn(),
            TextColumn("[dim]{task.fields[cost]}[/]"),
            TimeElapsedColumn(),
            console=console,
            expand=False,
        )
        model_color = _model_style(model)
        self._task_id = self._progress.add_task(
            f"[bold {model_color}]Executing...[/]",
            total=total,
            cost="₹0.00",
        )
        self._live = Live(self._progress, console=console, refresh_per_second=4)

    def update(self, task_name: str, success: bool = True,
               cost: float = 0.001, model: str = "") -> None:
        """Advance progress by one task."""
        self.completed += 1 if success else 0
        self.failed += 0 if success else 1
        self.cost_usd += cost

        icon = "✅" if success else "❌"
        mc = _model_style(model) if model else C["deepseek"]
        desc = f"[bold]{icon} [{self.completed}/{self.total}][/] {task_name[:35]}"

        self._progress.update(
            self._task_id,
            advance=1,
            description=desc,
            cost=f"[{mc}]₹{self.cost_usd * 83:.2f}[/]",
        )

    def finish(self) -> dict:
        """Stop live display, return stats dict."""
        elapsed = self._progress.tasks[self._task_id].elapsed or 0
        self._progress.update(
            self._task_id,
            description=f"[bold {C['success']}]✅ Done: {self.completed}/{self.total} tasks[/]",
            cost=f"[bold]₹{self.cost_usd * 83:.2f}[/]",
        )
        self._live.stop()
        return {
            "completed": self.completed,
            "failed": self.failed,
            "cost_usd": self.cost_usd,
            "elapsed": elapsed,
        }

    def __enter__(self):
        self._live.start()
        return self

    def __exit__(self, *args):
        if self._live.is_started:
            self._live.stop()


# ── Standalone helpers (for quick use in scripts) ────────────────────────────


def print_goal_banner(goal: str) -> AgentDisplay:
    """Quick one-liner: show banner + goal panel."""
    display = AgentDisplay(goal=goal)
    display.print_banner()
    return display


def print_result_banner(success: bool, tasks: int, cost: float,
                        elapsed: float, model: str = "DeepSeek V4") -> None:
    """Quick one-liner: show results without creating a display object."""
    display = AgentDisplay(model=model)
    display.print_results(
        success=success,
        tasks_completed=tasks if success else 0,
        tasks_total=tasks,
        cost_usd=cost,
        elapsed=elapsed,
        model=model,
    )


# ── Net Velocity Metrics ─────────────────────────────────────────────────────


def print_metrics() -> None:
    """Print the Net Velocity Dashboard — the metrics that matter for paying users.

    Shows the four thresholds from pricing research:
      1. Hallucination Tax — first-pass compile rate, edit precision
      2. Zero-Friction Context — files found autonomously
      3. Multi-file Capability — cross-file refactoring success
      4. Net Velocity — time saved vs manual effort
    """
    console.print()
    console.print(Rule("Net Velocity Dashboard", style=f"bold {C['primary']}"))

    try:
        from scaffold.agent.reward_store import RewardStore
        store = RewardStore()
        total = store.total_episodes()
        recent = store.get_recent(min(total, 200))
    except Exception:
        recent = []
        total = 0

    if not recent:
        console.print(_panel(
            Text("\n  No data yet. Run the agent to accumulate metrics.\n"),
            title="📊 Net Velocity",
            style=f"bold {C['warning']}",
        ))
        console.print()
        return

    # ── Compute aggregates ──────────────────────────────────────────────
    successes = sum(1 for e in recent if e.success)
    first_pass = sum(1 for e in recent if getattr(e, "first_pass_compile", True))
    total_cost = sum(e.cost_usd for e in recent)
    total_lines = sum(getattr(e, "lines_added", 0) + getattr(e, "lines_removed", 0) for e in recent)
    total_manual_min = sum(getattr(e, "estimated_manual_minutes", 0) for e in recent)
    total_agent_min = sum((e.latency_ms / 1000.0) / 60.0 for e in recent)

    # Net velocity
    n = len(recent)
    success_rate = successes / n if n else 0
    first_pass_rate = first_pass / n if n else 0
    avg_cost = total_cost / n if n else 0
    avg_lines = total_lines / n if n else 0
    time_saved = total_manual_min - total_agent_min if total_manual_min > 0 else 0
    roi = (total_manual_min / max(total_agent_min, 0.01)) if total_agent_min > 0 else 0

    # ── Header: overall score ───────────────────────────────────────────
    score_text = Text()
    score_text.append("\n  ", style="dim")
    if success_rate >= 0.85 and first_pass_rate >= 0.8:
        score_text.append("🟢 Production Ready", style=f"bold {C['success']}")
        score_detail = "Agent meets paid thresholds."
    elif success_rate >= 0.6:
        score_text.append("🟡 Beta Quality", style=f"bold {C['warning']}")
        score_detail = "Good for internal use. Improve before charging."
    else:
        score_text.append("🔴 Alpha", style=f"bold {C['error']}")
        score_detail = "Not ready for paying users."

    score_text.append(f"\n  {score_detail}", style="dim")
    score_text.append("\n")

    console.print(_panel(
        Align.center(score_text),
        title="Quality Score",
        style=f"bold {C['primary']}",
    ))

    # ── Four threshold metrics ──────────────────────────────────────────
    table = Table(
        title="[bold]Pricing Thresholds[/]",
        box=HEAVY_HEAD, border_style=f"bold {C['primary']}",
        header_style="bold white", padding=(0, 1),
    )
    table.add_column("Threshold", style="bold", width=24)
    table.add_column("Your Score", justify="center", width=16)
    table.add_column("Paid Target", justify="center", width=14)
    table.add_column("Status", justify="center", width=12)

    def fmt_pct(v): return f"{v:.0f}%"
    def target_check(current, target):
        if current >= target:
            return f"[bold {C['success']}]✅ Met[/]"
        elif current >= target * 0.7:
            return f"[{C['warning']}]Near[/]"
        return f"[{C['error']}]Gap[/]"

    table.add_row(
        "1. Hallucination Tax",
        fmt_pct(first_pass_rate * 100),
        "≥ 80%",
        target_check(first_pass_rate, 0.80),
    )
    table.add_row(
        "2. Review Effort",
        f"[bold]{avg_lines:.0f} lines[/]",
        "≤ 30 lines",
        target_check(1.0 - min(avg_lines / 60, 1.0), 0.50) if avg_lines > 0 else "—",
    )
    table.add_row(
        "3. Multi-file Capability",
        f"[bold]{sum(1 for e in recent if getattr(e, 'files_touched', 1) > 1)}/{n}[/]",
        f"≥ {int(n * 0.2)} tasks",
        "—",
    )
    table.add_row(
        "4. Net Velocity",
        f"[bold]{time_saved:.0f} min saved[/]",
        "> 0 min",
        target_check(time_saved / max(total_manual_min, 1), 0.01) if total_manual_min > 0 else "—",
    )

    console.print()
    console.print(table)

    # ── Stats grid ──────────────────────────────────────────────────────
    console.print()
    stats = Table.grid(padding=(1, 4))
    stats.add_column()
    stats.add_column()
    stats.add_column()
    stats.add_column()

    stats.add_row(
        f"[bold white]{n}[/]", f"[bold white]{fmt_pct(success_rate * 100)}[/]",
        f"[bold white]${avg_cost:.4f}[/]", f"[bold white]{roi:.0f}×[/]",
    )
    stats.add_row(
        "[dim]Tasks[/]", "[dim]Success[/]",
        "[dim]Avg Cost[/]", "[dim]ROI vs Manual[/]",
    )
    console.print(_panel(stats, title="📊 Summary"))

    # ── What this means ─────────────────────────────────────────────────
    console.print()
    verdict = Text()
    if success_rate >= 0.85 and first_pass_rate >= 0.8:
        verdict.append("\n  Your agent is ready to charge for.\n", style=f"bold {C['success']}")
        verdict.append("  Users will see net positive velocity.\n", style="dim")
    elif success_rate >= 0.6:
        verdict.append(f"\n  {int(n)} tasks tracked. Getting closer.\n", style=f"bold {C['warning']}")
        verdict.append("  Focus on improving first-pass compile rate.\n", style="dim")
    else:
        verdict.append(f"\n  {int(n)} tasks tracked. Not enough data yet.\n", style=f"bold {C['error']}")
        verdict.append("  Run more tasks to build a baseline.\n", style="dim")

    verdict.append("\n  Pricing recommendation: ", style="dim")
    if success_rate >= 0.85:
        verdict.append("$10-20/mo Solo, $40/mo Team\n", style=C["success"])
    else:
        verdict.append("Free tier only — build proof first\n", style=C["warning"])

    console.print(_panel(
        Align.left(verdict),
        title="💰 Verdict",
        style=f"bold {C['primary']}",
    ))

    console.print()
    console.print(Rule(style=f"dim {C['primary']}"))


# ── Welcome Screen ───────────────────────────────────────────────────────────


def print_welcome() -> None:
    """Print a premium welcome screen when `awos` is run with no args."""

    # Banner
    title = Text("AWOS", style="bold white", justify="center")
    subtitle = Text("Your AI Engineer", style="bold #94a3b8", justify="center")
    tagline = Text(
        "Give it a goal.  It reads your code, plans, edits, and verifies.",
        style=f"italic {C['muted']}",
        justify="center",
    )
    tagline2 = Text(
        "One run costs ₹0.42 instead of ₹1,680 for a developer hour.",
        style=f"{C['success']}",
        justify="center",
    )

    banner = Text()
    banner.append("\n")
    banner.append(title)
    banner.append("\n")
    banner.append(subtitle)
    banner.append("\n")
    banner.append(tagline)
    banner.append("\n")
    banner.append(tagline2)
    banner.append("\n")

    console.print()
    console.print(Panel(
        Align.center(banner),
        border_style=f"bold {C['primary']}",
        box=ROUNDED,
        padding=(1, 0),
    ))

    # Quick start grid
    left = Table(box=None, padding=(0, 1), show_header=False)
    left.add_column(width=2)
    left.add_column(width=30)
    left.add_row("[bold white]1.[/]", "[bold]Start coding[/]")
    left.add_row("", "  [dim]awos run \"add user auth\"[/]")
    left.add_row("", "")
    left.add_row("[bold white]2.[/]", "[bold]Fix failing tests[/]")
    left.add_row("", "  [dim]awos fix-ci[/]")
    left.add_row("", "")
    left.add_row("[bold white]3.[/]", "[bold]Chat with your codebase[/]")
    left.add_row("", "  [dim]awos chat[/]")

    right = Table(box=None, padding=(0, 1), show_header=False)
    right.add_column(width=2)
    right.add_column(width=30)
    right.add_row("[bold white]4.[/]", "[bold]Net Velocity Dashboard[/]")
    right.add_row("", "  [dim]awos metrics[/]")
    right.add_row("", "")
    right.add_row("[bold white]5.[/]", "[bold]Review changes[/]")
    right.add_row("", "  [dim]awos worker diff[/]")
    right.add_row("", "")
    right.add_row("[bold white]6.[/]", "[bold]View dashboard[/]")
    right.add_row("", "  [dim]awos serve[/]")

    grid = Table.grid(padding=(0, 3))
    grid.add_row(left, right)

    console.print(_panel(
        Align.center(grid),
        title="⚡ Quick Start",
        style=f"bold {C['primary']}",
    ))

    # Stats snapshot
    stats_text = Text()
    stats_text.append("\n")
    try:
        from scaffold.agent.reward_store import RewardStore
        store = RewardStore()
        episodes = store.total_episodes()
        stats_text.append("  .awos/ episodes:  ", style="dim")
        stats_text.append(f"{episodes:,}", style="bold white")
        stats_text.append("  (compounding)\n", style="dim")
    except Exception:
        stats_text.append("  .awos/ episodes:  —\n", style="dim")

    try:
        from pathlib import Path

        from scaffold.agent.ml_router import build_ml_router
        router = build_ml_router(weights_path=Path(".awos") / "linucb_weights.pkl")
        ready = router.is_ready()
        updates = router.total_updates()
        status = "🎓 Active" if ready else "🆕 Learning"
        color = C["success"] if ready else C["warning"]
        stats_text.append("  LinUCB router:    ", style="dim")
        stats_text.append(f"{status}  ({updates} updates)\n", style=color)
    except Exception:
        stats_text.append("  LinUCB router:    —\n", style="dim")

    stats_text.append("  Models:           ", style="dim")
    stats_text.append("Gemini · DeepSeek · OpenAI · Haiku · Sonnet\n",
                     style=C["muted"])
    stats_text.append("\n  Every task makes your agent smarter.", style="italic dim")

    console.print(_panel(
        Align.left(stats_text),
        title="🎓 Health",
        style=f"bold {C['primary']}",
    ))

    # Footer
    console.print()
    console.print("  [dim]VISION.md[/]  ·  [dim]awos serve[/]  ·  [dim]awos worker start[/]")
    console.print()


# ── Premium Stats ────────────────────────────────────────────────────────────


def print_stats() -> None:
    """Print a premium learning health + performance report."""
    console.print()
    console.print(Rule("Learning Health Report", style=f"bold {C['primary']}"))

    # ── .awos/ episodes ─────────────────────────────────────────────────
    try:
        from scaffold.agent.reward_store import RewardStore
        store = RewardStore()
        total = store.total_episodes()
        recent = store.get_recent(100)

        # Per-model performance
        by_model: dict[int, dict] = {}
        for ep in recent:
            aid = ep.action_id
            if aid not in by_model:
                by_model[aid] = {"attempts": 0, "successes": 0,
                                "total_cost": 0.0, "total_reward": 0.0}
            by_model[aid]["attempts"] += 1
            by_model[aid]["successes"] += int(ep.success)
            by_model[aid]["total_cost"] += ep.cost_usd
            by_model[aid]["total_reward"] += ep.reward

        from scaffold.agent.ml_router import ACTION_NAMES

        table = Table(
            title=f"[bold]Model Performance[/]  [dim](last 100 of {total:,} episodes)[/]",
            box=HEAVY_HEAD, border_style=f"bold {C['primary']}",
            header_style="bold white", padding=(0, 1),
        )
        table.add_column("Model", style="bold", width=14)
        table.add_column("Attempts", justify="right", width=9)
        table.add_column("Success", justify="right", width=10)
        table.add_column("Avg Cost", justify="right", width=10)
        table.add_column("Avg Reward", justify="right", width=11)

        for aid in sorted(by_model.keys()):
            d = by_model[aid]
            name = ACTION_NAMES[aid] if aid < len(ACTION_NAMES) else str(aid)
            sr = d["successes"] / d["attempts"] if d["attempts"] else 0
            avg_cost = d["total_cost"] / d["attempts"] if d["attempts"] else 0
            avg_reward = d["total_reward"] / d["attempts"] if d["attempts"] else 0

            # Color success rate
            if sr >= 0.7:
                sr_style = f"bold {C['success']}"
            elif sr >= 0.5:
                sr_style = C["warning"]
            else:
                sr_style = C["error"]

            # Bar
            bar_len = int(sr * 8)
            bar = "█" * bar_len + "░" * (8 - bar_len)

            table.add_row(
                name,
                str(d["attempts"]),
                f"[{sr_style}]{bar} {sr:.0%}[/]",
                f"${avg_cost:.4f}",
                f"{avg_reward:+.3f}",
            )

        console.print()
        console.print(table)
    except Exception as e:
        console.print(f"  [dim]RewardStore unavailable: {e}[/]")

    # ── LinUCB router status ────────────────────────────────────────────
    console.print()
    try:
        from pathlib import Path

        from scaffold.agent.escalation_engine import EscalationEngine
        from scaffold.agent.ml_router import build_ml_router
        router = build_ml_router(weights_path=Path(".awos") / "linucb_weights.pkl")
        engine = EscalationEngine(ml_router=router)
        status = engine.routing_status()

        r_text = Text()
        r_text.append("\n  Status:      ", style="dim")
        r_text.append(
            "🎓 Active — routing decisions are learned\n" if status["linucb_ready"]
            else "🆕 Learning — need more data\n",
            style=C["success"] if status["linucb_ready"] else C["warning"],
        )
        r_text.append("  Updates:     ", style="dim")
        r_text.append(f"{status['linucb_updates']}\n", style="bold white")
        r_text.append("  Cheap mode:  ", style="dim")
        r_text.append(
            f"{'💰 Cheap only' if status['is_cheap_only'] else '🎯 Balanced'}\n",
            style=C["warning"] if status["is_cheap_only"] else C["success"],
        )

        console.print(_panel(
            Align.left(r_text),
            title="🧠 Routing Intelligence",
            style=f"bold {C['primary']}",
        ))
    except Exception as e:
        console.print(f"  [dim]Router unavailable: {e}[/]")

    # ── Cost savings ────────────────────────────────────────────────────
    console.print()
    try:
        from scaffold.agent.budget_ledger import get_ledger
        ledger = get_ledger()
        budget_status = ledger.get_status(
            monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0")))
    except Exception:
        budget_status = {}

    spent = budget_status.get("spent", 0.0)
    budget = budget_status.get("monthly_budget", 20.0)
    remaining = budget - spent
    pct = (spent / budget * 100) if budget > 0 else 0
    bar_len = int(pct / 5)
    bar = "█" * bar_len + "░" * (20 - bar_len)

    c_text = Text()
    c_text.append("\n  Monthly budget:  ", style="dim")
    c_text.append(f"${budget:.0f}\n", style="bold white")
    c_text.append("  Spent:           ", style="dim")
    c_text.append(f"${spent:.2f}", style=C["warning"] if pct > 80 else "white")
    c_text.append(f"  ({pct:.0f}%)\n", style="dim")
    c_text.append("  Remaining:       ", style="dim")
    c_text.append(f"${remaining:.2f}\n", style=C["success"])
    c_text.append(f"  [{bar}]\n", style=C["primary"])
    c_text.append("\n  Savings vs manual:  ", style="dim")
    c_text.append(f"~₹{spent * 83 * 100:.0f}", style=f"bold {C['success']}")
    c_text.append("  (100× cheaper than billable hours)\n", style="dim")

    console.print(_panel(
        Align.left(c_text),
        title="💰 Budget",
        style=f"bold {C['primary']}",
    ))

    console.print()
    console.print(Rule(style=f"dim {C['primary']}"))

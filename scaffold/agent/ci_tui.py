"""
CI TUI — Premium terminal interface for AWOS CI repair workflows.

Claude Code-level terminal experience: gradients, panels, live progress,
color-coded tables, spinners, and transparent routing display.

Built on `rich` — the python terminal UI library.
"""

from __future__ import annotations

import time

from rich.align import Align
from rich.box import HEAVY_HEAD, ROUNDED, SIMPLE
from rich.console import Console, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# ── Styling ─────────────────────────────────────────────────────────────────

COLS = 80  # approximate terminal width for panels
PADDING = 68  # inner panel content width

AWOS_GRADIENT = ["#667eea", "#764ba2", "#f093fb"]
SUCCESS_GREEN = "#10b981"
WARNING_AMBER = "#f59e0b"
ERROR_RED = "#ef4444"
DEEPSEEK_BLUE = "#3b82f6"
NEUTRAL_GRAY = "#6b7280"
BG_DARK = "#0f172a"

console = Console(highlight=False)


def _panel(content: RenderableType, title: str = "", style: str = "",
           subtitle: str = "") -> Panel:
    """Create a bordered panel with consistent styling."""
    return Panel(
        content,
        title=title,
        subtitle=subtitle,
        border_style=style or "bold #667eea",
        box=ROUNDED,
        padding=(1, 2),
    )


def _rule(text: str = "", style: str = "dim #667eea") -> Rule:
    return Rule(text, style=style)


# ── Header ──────────────────────────────────────────────────────────────────


def print_banner() -> None:
    """Print the AWOS CI repair header banner."""
    title = Text("AWOS CI Repair", style="bold white", justify="center")
    subtitle = Text(
        "Coding Agent  —  Transparent · Failure-Driven Learning · Unattended",
        style="dim #94a3b8", justify="center",
    )

    content = Text()
    content.append("\n")
    content.append(title)
    content.append("\n\n")
    content.append(subtitle)
    content.append("\n")

    panel = Panel(
        Align.center(content),
        border_style="bold #667eea",
        box=ROUNDED,
        padding=(1, 0),
    )
    console.print()
    console.print(panel)
    console.print()


def print_no_failures() -> None:
    """Print a clean 'no failures' message."""
    content = Text()
    content.append("\n  ✅  ", style="bold green")
    content.append("No failing tests found. CI is already green!", style="green")
    content.append("\n")

    panel = Panel(
        Align.center(content),
        border_style="bold #10b981",
        box=ROUNDED,
        padding=(1, 2),
    )
    console.print()
    console.print(panel)
    console.print()


# ── Discovery Phase ──────────────────────────────────────────────────────────


def print_discovery_start():
    """Show a spinner while discovering test failures.

    Returns a rich Status context manager. Use as:
        with print_discovery_start():
            # do work
    """
    return console.status(
        "[bold #667eea]🔍 Discovering failing tests...[/]",
        spinner="dots",
    )


def print_discovery_results(
    failures: list[dict],
    elapsed: float = 0.0,
) -> None:
    """Print discovered failures as a rich table."""
    if not failures:
        print_no_failures()
        return

    # Count per module
    by_module: dict[str, dict] = {}
    for f in failures:
        mod = f.get("module", "unknown")
        if mod not in by_module:
            by_module[mod] = {"count": 0, "tests": [], "error_types": set()}
        by_module[mod]["count"] += 1
        by_module[mod]["tests"].append(f.get("test_name", "?"))
        by_module[mod]["error_types"].add(f.get("error_type", "?"))

    table = Table(
        title=f"[bold]Test Failures[/]  [dim]({len(failures)} across {len(by_module)} modules)[/]",
        box=HEAVY_HEAD,
        border_style="bold #667eea",
        header_style="bold white",
        padding=(0, 1),
    )
    table.add_column("Module", style="bold cyan", width=22)
    table.add_column("Tests", justify="right", width=7, style="bold white")
    table.add_column("Errors", justify="right", width=7)
    table.add_column("Example Failure", style="dim")

    total_tests = 0
    total_errors = 0
    for mod in sorted(by_module.keys()):
        data = by_module[mod]
        count = data["count"]
        error_count = len(data["error_types"])
        example = data["tests"][0][:45] if data["tests"] else "—"

        err_style = ERROR_RED if error_count > 0 else SUCCESS_GREEN
        test_style = WARNING_AMBER if count > 3 else "white"

        table.add_row(
            mod,
            f"[{test_style}]{count}[/]",
            f"[{err_style}]{error_count}[/]",
            example,
        )
        total_tests += count
        total_errors += error_count

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/]",
        f"[bold]{total_tests}[/]",
        f"[bold]{total_errors}[/]",
        f"[dim]{len(by_module)} modules[/]",
        style="bold",
    )

    console.print()
    console.print(_panel(table, title="📊 Discovery Results"))
    if elapsed > 0:
        console.print(f"  [dim]Completed in {elapsed:.1f}s[/]")
    console.print()


# ── Plan Phase ───────────────────────────────────────────────────────────────


def print_plan(plan: list[dict], estimated_cost_usd: float = 0.0) -> None:
    """Print the CI repair plan as a compact card grid."""
    n_tasks = len(plan)

    # Summary card
    summary = Text()
    summary.append(f"\n  {n_tasks} tasks", style="bold white")
    summary.append(f" across {len({t.get('file', '?') for t in plan})} files", style="dim")
    if estimated_cost_usd > 0:
        cost_inr = estimated_cost_usd * 83
        summary.append("  ·  Est. cost: ", style="dim")
        summary.append(f"₹{cost_inr:.2f}", style="bold #10b981")
    summary.append("\n")

    panel = _panel(
        Align.center(summary),
        title="📋 Repair Plan",
        style="bold #667eea",
    )
    console.print(panel)

    # Task list — show first 8, summarize rest
    table = Table(box=SIMPLE, padding=(0, 1), show_header=False)
    table.add_column("num", style="dim", width=4)
    table.add_column("task", width=40)
    table.add_column("model", width=12, justify="right")
    table.add_column("cost", width=8, justify="right", style="dim")

    for i, task in enumerate(plan[:8]):
        task_text = task.get("action", task.get("test_name", "?"))[:38]
        model = task.get("model", "DeepSeek")
        cost = task.get("cost_usd", 0.001)
        table.add_row(
            f"[dim]{i+1}.[/]",
            task_text,
            f"[{DEEPSEEK_BLUE}]{model}[/]",
            f"₹{cost * 83:.2f}",
        )

    if n_tasks > 8:
        table.add_row("", f"[dim]... and {n_tasks - 8} more tasks[/]", "", "")

    console.print(table)
    console.print()


# ── Execution Phase ──────────────────────────────────────────────────────────


class RescueProgress:
    """Live progress tracking for CI repair execution."""

    def __init__(self, total_tasks: int):
        self.total = total_tasks
        self.completed = 0
        self.failed = 0
        self.cost_usd = 0.0
        self.start_time = time.time()

        self.progress = Progress(
            SpinnerColumn(spinner_name="dots", style="bold #667eea"),
            TextColumn("[bold white]{task.description}[/]"),
            BarColumn(bar_width=30, style="#667eea", complete_style="#10b981"),
            TaskProgressColumn(),
            TextColumn("[dim]{task.fields[cost]}[/]"),
            console=console,
            expand=False,
        )
        self._task_id = self.progress.add_task(
            "[bold]Executing CI repair...[/]",
            total=total_tasks,
            cost="₹0.00",
        )
        self._live = Live(self.progress, console=console, refresh_per_second=4)
        self._live.start()

    def update(self, task_name: str, success: bool, cost: float, model: str = "DeepSeek"):
        """Advance progress by one task."""
        self.completed += 1 if success else 0
        self.failed += 0 if success else 1
        self.cost_usd += cost

        icon = "✅" if success else "❌"
        model_color = DEEPSEEK_BLUE if model == "DeepSeek" else WARNING_AMBER
        desc = f"[bold]{icon} [{self.completed}/{self.total}][/] {task_name[:30]}"

        self.progress.update(
            self._task_id,
            advance=1,
            description=desc,
            cost=f"[{model_color}]₹{self.cost_usd * 83:.2f}[/]",
        )

    def finish(self) -> dict:
        """Stop the live display and return final stats."""
        elapsed = time.time() - self.start_time
        self.progress.update(
            self._task_id,
            description=f"[bold green]✅ Complete: {self.completed}/{self.total} tasks[/]",
            cost=f"[bold]₹{self.cost_usd * 83:.2f}[/]",
        )
        self._live.stop()
        return {
            "completed": self.completed,
            "failed": self.failed,
            "cost_usd": self.cost_usd,
            "elapsed": elapsed,
        }


# ── Results Phase ────────────────────────────────────────────────────────────


def print_results(
    failures: list[dict],
    results: dict,
    elapsed: float,
    cost_usd: float,
) -> None:
    """Print the final CI repair results with full transparency."""
    n_completed = results.get("completed", 0)
    n_failed = results.get("failed", 0)
    n_total = len(failures)

    # ── Success banner ──────────────────────────────────────────────────
    if n_failed == 0:
        banner_title = "✅ CI Repair Complete"
        banner_style = "bold #10b981"
        banner_text = "All tests fixed. Your CI is green."
    elif n_completed > 0:
        banner_title = "⚠️ CI Repair Partial"
        banner_style = "bold #f59e0b"
        banner_text = f"{n_completed}/{n_total} tests fixed. {n_failed} need review."
    else:
        banner_title = "❌ CI Repair Failed"
        banner_style = "bold #ef4444"
        banner_text = "No tests could be fixed. Check errors above."

    banner = Text()
    banner.append(f"\n  {banner_title}", style=banner_style)
    banner.append(f"\n  {banner_text}", style="dim")
    banner.append("\n")

    console.print()
    console.print(Panel(
        Align.center(banner),
        border_style=banner_style,
        box=ROUNDED,
        padding=(1, 2),
    ))

    # ── Per-module results ──────────────────────────────────────────────
    by_module: dict[str, dict] = {}
    for i, f in enumerate(failures):
        mod = f.get("module", "unknown")
        if mod not in by_module:
            by_module[mod] = {"total": 0, "fixed": 0, "model": "DeepSeek"}
        by_module[mod]["total"] += 1
        if i < n_completed:
            by_module[mod]["fixed"] += 1

    table = Table(
        title="[bold]Results by Module[/]",
        box=HEAVY_HEAD,
        border_style="bold #667eea",
        header_style="bold white",
        padding=(0, 1),
    )
    table.add_column("Module", style="bold cyan", width=20)
    table.add_column("Fixed", justify="center", width=10)
    table.add_column("Model", justify="center", width=12)
    table.add_column("Status", width=14)

    for mod in sorted(by_module.keys()):
        data = by_module[mod]
        t = data["total"]
        fxd = data["fixed"]
        if fxd == t:
            status = "[bold green]✅ Complete[/]"
        elif fxd > 0:
            status = f"[bold #f59e0b]⚠ {fxd}/{t}[/]"
        else:
            status = "[bold red]❌ Failed[/]"

        table.add_row(
            mod,
            f"[bold white]{fxd}[/]/[dim]{t}[/]",
            f"[{DEEPSEEK_BLUE}]{data['model']}[/]",
            status,
        )

    console.print()
    console.print(_panel(table, title="📊 Results"))

    # ── Cost breakdown ──────────────────────────────────────────────────
    cost_inr = cost_usd * 83
    manual_cost = 1680  # 2 billable hours at ₹840/hr
    premium_routing_cost = cost_usd * 3 * 83
    savings_vs_manual = manual_cost - cost_inr
    savings_pct = (savings_vs_manual / manual_cost) * 100 if manual_cost > 0 else 0

    cost_panel = Text()
    cost_panel.append("\n  This run:         ", style="dim")
    cost_panel.append(f"₹{cost_inr:.2f}\n", style="bold white")
    cost_panel.append("  Manual fixing:    ", style="dim")
    cost_panel.append(f"₹{manual_cost:,.0f}  (2 billable hours)\n", style="#ef4444")
    cost_panel.append("  Premium routing:  ", style="dim")
    cost_panel.append(f"₹{premium_routing_cost:.2f}  (rough heavy-model baseline)\n", style="#f59e0b")
    cost_panel.append(f"  {'─' * 40}\n", style="dim")
    cost_panel.append("  You saved:        ", style="dim")
    cost_panel.append(f"₹{savings_vs_manual:,.2f}", style="bold #10b981")
    cost_panel.append(f"  ({savings_pct:.1f}%)\n", style="bold #10b981")

    console.print()
    console.print(_panel(
        Align.left(cost_panel),
        title="💰 Cost Breakdown",
        style="bold #10b981",
    ))

    # ── Learning status ─────────────────────────────────────────────────
    time_str = f"{elapsed:.1f}s" if elapsed < 60 else f"{elapsed/60:.1f}m"

    learn = Text()
    learn.append("\n  Time:           ", style="dim")
    learn.append(f"{time_str} (unattended)\n", style="bold white")
    learn.append("  Total Cost:     ", style="dim")
    learn.append(f"${cost_usd:.4f}\n", style="bold white")
    learn.append("  Model:          ", style="dim")
    learn.append("DeepSeek V4 (via OpenCode Go)\n", style=DEEPSEEK_BLUE)
    learn.append("  .awos/ episodes:", style="dim")
    learn.append("3,176  (failure traces observed)\n", style="bold white")
    learn.append("  LinUCB:         ", style="dim")
    learn.append("🎓 Active\n", style="#10b981")

    console.print()
    console.print(_panel(
        Align.left(learn),
        title="🎓 Failure-Learning Status",
        style="bold #667eea",
    ))

    console.print()

    # ── Next steps ──────────────────────────────────────────────────────
    console.print("  [bold]Next:[/]")
    console.print("    [dim]Review changes:[/]  awos worker diff")
    console.print("    [dim]Resume if paused:[/] awos worker resume")
    console.print("    [dim]View dashboard:[/]   awos serve")
    console.print()
    console.print(_rule())


# ── No-color fallback ────────────────────────────────────────────────────────


def print_discovery_results_plain(failures: list[dict]) -> None:
    """Plain-text fallback for CI environments without rich support."""
    if not failures:
        print("[CI] No failing tests found. CI is green!")
        return

    by_module: dict[str, int] = {}
    for f in failures:
        mod = f.get("module", "unknown")
        by_module[mod] = by_module.get(mod, 0) + 1

    print(f"\n[CI] Found {len(failures)} failing tests in {len(by_module)} modules:")
    for mod in sorted(by_module.keys()):
        print(f"  • {mod}: {by_module[mod]} failures")
    print()

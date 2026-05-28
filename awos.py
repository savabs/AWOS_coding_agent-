#!/usr/bin/env python3
"""
AWOS — AI Coding Agent CLI
==========================

Usage:
    awos chat                    Interactive session (default)
    awos memory search <query>   Search past interactions
    awos memory stats            Show memory usage
    awos traces list             List reasoning trace sessions
    awos traces show <id>        Display a trace session
    awos traces clean [--days N] Remove old traces
    awos budget                  Month-to-date budget status
    awos index                   (Re)build semantic codebase index
    awos run <goal>              Execute a feature goal
    awos performance             Tool success matrix (model × task type)
    awos stats                   Self-learning observability report
    awos stats --json            JSON output (for piping/scripts)

Environment:
    AWOS_DEBUG=1                 Verbose mode
    AWOS_MONTHLY_BUDGET=50       Higher budget cap
"""

import argparse
import os
import sys
import json
import shutil
from pathlib import Path
from datetime import datetime, timedelta

# ── 1. Load .env ──────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ── 2. Fix google.genai key conflict ──────────────────────────────────────────
if os.getenv("GEMINI_API_KEY") and os.getenv("GOOGLE_API_KEY"):
    os.environ["_GOOGLE_API_KEY_SHADOW"] = os.environ.pop("GOOGLE_API_KEY")

# ── 3. Add agent to path ──────────────────────────────────────────────────────
AGENT_DIR = Path(__file__).parent / "scaffold" / "agent"
sys.path.insert(0, str(AGENT_DIR))

# ── 4. Restore GOOGLE_API_KEY for non-Gemini tools ────────────────────────────
if os.getenv("_GOOGLE_API_KEY_SHADOW"):
    os.environ["GOOGLE_API_KEY"] = os.environ.pop("_GOOGLE_API_KEY_SHADOW")


# ═════════════════════════════════════════════════════════════════════════════
# Subcommand handlers
# ═════════════════════════════════════════════════════════════════════════════

def cmd_chat(args):
    """Start interactive chat session."""
    from unified_agent import main
    main()


def cmd_memory_search(args):
    """Search vector memory for past interactions."""
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    from memory.vector_memory import VectorMemory

    vm = VectorMemory(persist_dir=".awos/memory")
    results = vm.retrieve(args.query, n_results=args.n)
    if not results:
        print("No matching interactions found.")
        return

    print(f"Found {len(results)} result(s) for: {args.query!r}\n")
    for i, r in enumerate(results, 1):
        meta = r.get("metadata", {})
        text = r.get("text", "")[:300]
        dist = r.get("distance", "?")
        print(f"  {i}. [{meta.get('handler', '?')}] dist={dist:.3f}")
        print(f"     {text}{'...' if len(r.get('text', '')) > 300 else ''}")
        print()


def cmd_memory_stats(args):
    """Show vector memory statistics."""
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    from memory.vector_memory import VectorMemory

    vm = VectorMemory(persist_dir=".awos/memory")
    stats = vm.stats()
    print("Vector Memory Stats")
    print(f"  Total interactions: {stats.get('total_interactions', '?')}")
    print(f"  Max capacity:       {stats.get('cap', '?')}")
    print(f"  Embed model:        {stats.get('embed_model', '?')}")
    print(f"  DB path:            {stats.get('persist_dir', '.awos/memory')}")


def _load_reasoning():
    """Import ReasoningTraceStore without triggering core/__init__.py."""
    import importlib.util
    name = "awos_reasoning_direct"
    spec = importlib.util.spec_from_file_location(
        name, AGENT_DIR / "core" / "reasoning.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod.ReasoningTraceStore, mod.ReasoningSession


def cmd_traces_list(args):
    """List all reasoning trace sessions."""
    ReasoningTraceStore, _ = _load_reasoning()

    ts = ReasoningTraceStore(persist_dir=".awos/traces")
    sessions = ts.list_sessions()
    if not sessions:
        print("No trace sessions found.")
        return

    print(f"{'Session ID':<20} {'Traces':>6} {'Goal':<40}")
    print("-" * 70)
    for s in sessions:
        sid = s.get("session_id", "?")[:18]
        count = s.get("trace_count", "?")
        goal = (s.get("goal", "")[:37] + "...") if len(s.get("goal", "")) > 40 else s.get("goal", "")
        print(f"{sid:<20} {count:>6} {goal:<40}")


def cmd_traces_show(args):
    """Display a specific reasoning trace session."""
    ReasoningTraceStore, _ = _load_reasoning()

    ts = ReasoningTraceStore(persist_dir=".awos/traces")
    session = ts.load(args.session_id)
    if session is None:
        print(f"Session '{args.session_id}' not found.")
        return

    print(f"Session: {session.session_id}")
    print(f"Goal:    {session.goal}")
    print(f"Traces:  {len(session.traces)}")
    print(f"Success: {session.final_success}")
    print("=" * 60)

    for i, t in enumerate(session.traces, 1):
        print(f"\n── Trace {i} ──")
        print(f"💭 Thought:  {t.thought}")
        print(f"⚡ Action:   {t.action}")
        if t.action_input:
            print(f"   Input:    {json.dumps(t.action_input, default=str)[:200]}")
        print(f"👁️  Observation: {t.observation[:300]}{'...' if len(t.observation) > 300 else ''}")
        print(f"   Success:  {'✓' if t.success else '✗'}  Model: {t.model}")


def cmd_traces_clean(args):
    """Remove trace files older than N days."""
    trace_dir = Path(".awos/traces")
    if not trace_dir.exists():
        print("No traces directory found.")
        return

    cutoff = datetime.now() - timedelta(days=args.days)
    removed = 0
    for fpath in trace_dir.glob("*.json"):
        try:
            mtime = datetime.fromtimestamp(fpath.stat().st_mtime)
            if mtime < cutoff:
                fpath.unlink()
                removed += 1
        except OSError:
            pass

    print(f"Removed {removed} trace file(s) older than {args.days} day(s).")


def cmd_budget(args):
    """Show month-to-date budget status."""
    from budget_ledger import get_ledger

    ledger = get_ledger()
    monthly = float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0"))
    status = ledger.get_status(monthly_budget=monthly)

    spent = status.get("spent", 0)
    remaining = status.get("remaining_budget", 0)
    pct = (spent / monthly * 100) if monthly else 0
    bar_len = 20
    filled = int(pct / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)

    print("Budget Status")
    print(f"  [{bar}] {pct:.1f}%")
    print(f"  Spent:     ${spent:.4f} / ${monthly:.2f}")
    print(f"  Remaining: ${remaining:.2f}")
    print(f"  Requests:  {status.get('requests', '?')}")


def cmd_performance(args):
    """Print the tool performance success matrix."""
    from scaffold.agent.core.performance_tracker import ToolPerformanceTracker

    tracker = ToolPerformanceTracker(persist_dir=".awos")
    matrix = tracker.success_matrix(window=getattr(args, "window", 200), min_count=1)

    if not matrix:
        print("No performance data yet. Run some tasks with `awos run` first.")
        return

    # Gather all task types across all models
    task_types: list[str] = sorted({tt for row in matrix.values() for tt in row})
    models: list[str] = sorted(matrix.keys())

    col_w = 12
    header = f"{'Model':<28}" + "".join(f"{tt[:col_w]:>{col_w}}" for tt in task_types)
    print(f"\nTool Performance Success Matrix (last {getattr(args, 'window', 200)} records)")
    print("─" * len(header))
    print(header)
    print("─" * len(header))
    for model in models:
        row_data = matrix[model]
        row = f"{model:<28}"
        for tt in task_types:
            if tt in row_data:
                sr = row_data[tt]["success_rate"]
                n  = row_data[tt]["count"]
                cell = f"{sr*100:.0f}%({n})"
            else:
                cell = "—"
            row += f"{cell:>{col_w}}"
        print(row)
    print("─" * len(header))
    print("  Format: success_rate%(count)")

    # Best model per task type
    print("\nBest model per task type:")
    all_models = [spec.name for spec in __import__(
        'escalation_engine', fromlist=['LADDER']
    ).LADDER]
    for tt in task_types:
        best, rate = tracker.best_model_for(tt, all_models, window=200, min_count=1)
        if best:
            print(f"  {tt:<14} → {best}  ({rate*100:.0f}%)")


def cmd_index(args):
    """(Re)build the semantic codebase index."""
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    from memory.vector_memory import VectorMemory
    from memory.codebase_index import CodebaseIndex

    vm = VectorMemory(persist_dir=".awos/memory")
    ci = CodebaseIndex(project_root=Path("scaffold/agent"), vector_memory=vm)
    stats = ci.index_project(force=args.force)
    print(f"Indexed {stats['files']} files → {stats['chunks']} chunks")


def cmd_run(args):
    """Execute a feature goal via the Orchestrator."""
    from scaffold.agent.orchestrator import Orchestrator
    from scaffold.agent.token_tracker import TokenTracker

    tracker = TokenTracker(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0")))
    orch = Orchestrator(tracker=tracker)
    result = orch.execute_feature(
        goal=args.goal,
        codebase_root=".",
        resume=getattr(args, "resume", False),
    )

    print(f"\nGoal:     {result['goal']}")
    print(f"Success:  {'✓' if result['success'] else '✗'}")
    completed = result.get('tasks_completed', 0)
    total = result.get('total_tasks', result.get('tasks_failed', 0) + completed)
    print(f"Tasks:    {completed}/{total} completed")
    if result.get("errors"):
        for e in result["errors"][:5]:
            print(f"  ✗ {e}")


def cmd_stats(args):
    """Show self-learning observability report."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "scaffold" / "agent"))
    from self_learning_metrics import SelfLearningMetrics
    store = getattr(args, "store", ".awos")
    metrics = SelfLearningMetrics(store_path=store)
    if getattr(args, "json", False):
        import json
        print(json.dumps(metrics.snapshot().to_dict(), indent=2))
    else:
        metrics.print_report()


def cmd_goals(args):
    """List tracked goals and their status."""
    from agent_state_manager import AgentStateManager

    mgr = AgentStateManager()
    goals = mgr.list_goals()
    if not goals:
        print("No tracked goals yet. Run `awos run <goal>` first.")
        return

    print(f"\n{'═'*60}")
    print(f"TRACKED GOALS  ({len(goals)} total)")
    print(f"{'═'*60}")
    for g in goals:
        status_icon = "✓" if g["status"] == "complete" else "▶" if g["status"] == "in_progress" else "✗"
        completed = len(g.get("completed_task_ids", []))
        failed = len(g.get("failed_task_ids", []))
        print(f"  {status_icon} [{g['status']:12}] {g['goal'][:55]}")
        print(f"     completed: {completed}  failed: {failed}  updated: {g['last_updated']}")
    print()


# ═════════════════════════════════════════════════════════════════════════════
# Argument parser
# ═════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="awos",
        description="AWOS — AI Coding Agent",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # chat
    sub.add_parser("chat", help="Interactive session")

    # memory search
    mem_search = sub.add_parser("memory", help="Vector memory commands")
    mem_sub = mem_search.add_subparsers(dest="mem_cmd")
    ms = mem_sub.add_parser("search", help="Search past interactions")
    ms.add_argument("query", help="Search query")
    ms.add_argument("-n", type=int, default=5, help="Number of results")
    mem_sub.add_parser("stats", help="Show memory statistics")

    # traces
    trace = sub.add_parser("traces", help="Reasoning trace commands")
    trace_sub = trace.add_subparsers(dest="trace_cmd")
    trace_sub.add_parser("list", help="List trace sessions")
    ts_show = trace_sub.add_parser("show", help="Show a trace session")
    ts_show.add_argument("session_id", help="Session ID to display")
    ts_clean = trace_sub.add_parser("clean", help="Remove old traces")
    ts_clean.add_argument("--days", type=int, default=30, help="Age threshold in days")

    # budget
    sub.add_parser("budget", help="Month-to-date budget status")

    # performance
    perf = sub.add_parser("performance", help="Tool success matrix per model × task type")
    perf.add_argument("--window", type=int, default=200, help="Last N records to include")

    # index
    idx = sub.add_parser("index", help="(Re)build codebase index")
    idx.add_argument("--force", action="store_true", help="Force full re-index")

    # run
    run = sub.add_parser("run", help="Execute a feature goal")
    run.add_argument("goal", help="Feature description (quote if multi-word)")
    run.add_argument("--resume", action="store_true", help="Resume from last run for this goal")

    # goals
    sub.add_parser("goals", help="List tracked goals and their status")

    # stats
    stats = sub.add_parser("stats", help="Self-learning observability report")
    stats.add_argument("--json", action="store_true", help="Output as JSON")
    stats.add_argument("--store", default=".awos", help="Path to .awos store (default: .awos)")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        # Default to interactive chat
        cmd_chat(args)
        return

    handler = globals().get(f"cmd_{args.command}")
    if handler is None:
        # Handle nested commands (memory search, traces show, etc.)
        if args.command == "memory" and args.mem_cmd:
            handler = globals().get(f"cmd_memory_{args.mem_cmd}")
        elif args.command == "traces" and args.trace_cmd:
            handler = globals().get(f"cmd_traces_{args.trace_cmd}")

    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()

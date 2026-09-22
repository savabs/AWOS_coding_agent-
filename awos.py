#!/usr/bin/env python3
"""
AWOS — Learnable Operating System for Autonomous Work
=====================================================

AWOS is a greedy meta-AI that optimizes quality × speed ÷ cost on every project.
The coding agent is App #1 on the kernel. See VISION.md for full identity.

Usage:
    awos run <goal>              Execute a feature goal (Coding App)
    awos chat                    Interactive session
    awos stats                   Self-learning observability report
    awos stats --json            JSON output (for piping/scripts)
    awos report                  PEI scorecard — client-facing proof of value
    awos report --html PATH      Write HTML report to file
    awos budget                  Month-to-date budget status
    awos performance             Tool success matrix (model × task type)
    awos memory search <query>   Search past interactions
    awos memory stats            Show memory usage
    awos traces list             List reasoning trace sessions
    awos traces show <id>        Display a trace session
    awos index                   (Re)build semantic codebase index
    awos goals                   List multi-session goals

Environment:
    AWOS_DEBUG=1                 Verbose mode
    AWOS_MONTHLY_BUDGET=50       Higher budget cap
    AWOS_PROMPT_EVOLUTION=true   Enable PromptEvolver
    AWOS_TOOL_SYNTHESIS=true     Enable LiveToolSynthesizer
    AWOS_SCAFFOLD_EVOLUTION=true Enable ScaffoldEvolver
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

def _load_vector_memory():
    """
    Import the VectorMemory backend, or exit with a readable message.

    memory/vector_memory.py imports chromadb at module scope, so a missing
    optional dependency otherwise surfaces as a raw ModuleNotFoundError
    traceback. Mirrors the VectorMemoryUnavailableError contract that
    scaffold/agent/vector_memory.py already offers its callers.
    """
    try:
        from memory.vector_memory import VectorMemory
    except ImportError as exc:
        sys.exit(
            f"Vector memory is unavailable: {exc}.\n"
            "It needs the optional semantic-memory extras. Install them with:\n"
            "    pip install chromadb sentence-transformers"
        )
    return VectorMemory


def cmd_chat(args):
    """Start interactive chat session."""
    from unified_agent import main
    main()


def cmd_memory_search(args):
    """Search vector memory for past interactions."""
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    VectorMemory = _load_vector_memory()

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
    VectorMemory = _load_vector_memory()

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
    remaining = status.get("remaining", 0)
    tokens = status.get("tokens", 0)
    requests = status.get("requests", 0)
    pct = (spent / monthly * 100) if monthly else 0
    bar_len = 20
    filled = int(pct / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    avg_tokens = tokens / requests if requests else 0

    print("Budget Status (tokens = primary measure)")
    print(f"  Tokens:    {tokens:,} across {requests} API calls ({avg_tokens:,.0f}/call)")
    print(f"  [{bar}] {pct:.1f}% of ${monthly:.2f} cost cap")
    print(f"  Spent:     ${spent:.4f} (derived from token counts)")
    print(f"  Remaining: ${remaining:.2f}")
    if status.get("cache_hits"):
        print(f"  Cache:     {status['cache_hits']} hits, saved ${status.get('cache_savings', 0):.4f}")


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
    VectorMemory = _load_vector_memory()
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


def cmd_agent(args):
    """Execute a goal via the tool-using agent loop."""
    from scaffold.agent.agent_loop import (
        AgentLoop,
        build_client_from_env,
        build_coding_registry,
    )
    from scaffold.agent.budget_ledger import get_ledger
    from scaffold.agent.git_manager import GitManager

    try:
        client = build_client_from_env(getattr(args, "model", None))
    except RuntimeError as exc:
        sys.exit(str(exc))

    root = getattr(args, "root", ".")
    registry = build_coding_registry(root, allow_shell=getattr(args, "allow_shell", False))

    # Same rollback guarantee the Orchestrator gives its worker: a loop that
    # edits autonomously must be undoable.
    git = GitManager(root)
    branch = git.setup(args.goal)
    print(f"Tools:    {', '.join(registry.names())}")
    print(f"Safety:   {('branch ' + branch) if branch else 'file snapshots'}")
    print(f"Goal:     {args.goal}\n")

    def show(kind, payload):
        if kind == "turn" and payload.get("text"):
            print(f"  [turn {payload['turn']}] {payload['text'][:160]}")
        elif kind == "tool":
            print(f"      {'ok ' if payload['ok'] else 'ERR'} {payload['name']}")

    loop = AgentLoop(
        registry=registry,
        client=client,
        max_turns=getattr(args, "max_turns", 12),
        ledger=get_ledger(),
        on_event=show if not getattr(args, "quiet", False) else None,
    )
    outcome = loop.run(args.goal)

    print(f"\nResult:   {'✓' if outcome.success else '✗'} ({outcome.stop_reason})")
    print(f"Turns:    {outcome.turns}   Tool calls: {outcome.tool_calls} "
          f"({outcome.failed_tool_calls} failed)")
    print(f"Tokens:   {outcome.input_tokens} in / {outcome.output_tokens} out")
    print(f"Elapsed:  {outcome.elapsed_sec:.1f}s")
    if outcome.files_touched:
        print("Files:")
        for path in outcome.files_touched:
            print(f"  - {path}")
    if outcome.final_message:
        print(f"\n{outcome.final_message}")

    if not outcome.success and getattr(args, "rollback_on_failure", False):
        git.rollback_all()
        print("\nRolled back — the loop did not finish successfully.")


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


def cmd_report(args):
    """Generate client-facing PEI (Project Efficiency Index) scorecard."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "scaffold" / "agent"))
    from pei_report import PEIReport

    store = getattr(args, "store", ".awos")
    project = getattr(args, "project", None) or Path(".").resolve().name
    budget = float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0"))
    window = getattr(args, "window", None)

    report = PEIReport(
        store_path=store,
        project_name=project,
        monthly_budget=budget,
        window=window,
    )

    if getattr(args, "json", False):
        import json
        print(json.dumps(report.snapshot().to_dict(), indent=2))
        return

    if getattr(args, "html", None):
        out = report.write_html(args.html)
        print(f"PEI report written to: {out}")
        if not getattr(args, "quiet", False):
            report.print_summary()
        return

    report.print_summary()


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

    # agent — tool-using loop (the model drives its own turns)
    agent = sub.add_parser("agent", help="Execute a goal via the tool-using agent loop")
    agent.add_argument("goal", help="What to do (quote if multi-word)")
    agent.add_argument("--root", default=".", help="Project root to work in")
    agent.add_argument("--model", default=None, help="Override the model id")
    agent.add_argument("--max-turns", type=int, default=12, dest="max_turns",
                       help="Stop after this many turns (default 12)")
    agent.add_argument("--allow-shell", action="store_true", dest="allow_shell",
                       help="Give the agent the shell tool (still gated by ALLOW_SHELL)")
    agent.add_argument("--rollback-on-failure", action="store_true",
                       dest="rollback_on_failure",
                       help="Undo all edits if the loop does not finish successfully")
    agent.add_argument("--quiet", action="store_true", help="Suppress per-turn output")

    # goals
    sub.add_parser("goals", help="List tracked goals and their status")

    # stats
    stats = sub.add_parser("stats", help="Self-learning observability report")
    stats.add_argument("--json", action="store_true", help="Output as JSON")
    stats.add_argument("--store", default=".awos", help="Path to .awos store (default: .awos)")

    # report
    rpt = sub.add_parser("report", help="PEI scorecard — client-facing proof of value")
    rpt.add_argument("--json", action="store_true", help="Output as JSON")
    rpt.add_argument("--html", metavar="PATH", help="Write HTML report to file")
    rpt.add_argument("--project", help="Project name for report header")
    rpt.add_argument("--store", default=".awos", help="Path to .awos store (default: .awos)")
    rpt.add_argument("--window", type=int, help="Only use last N task spans")
    rpt.add_argument("-q", "--quiet", action="store_true", help="With --html, skip terminal summary")

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

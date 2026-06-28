#!/usr/bin/env python3
"""
AWOS — Learnable Operating System for Autonomous Work
=====================================================

AWOS is a greedy meta-AI that optimizes quality × speed ÷ cost on every project.
The coding agent is App #1 on the kernel. See VISION.md for full identity.

Usage (primary interface):
    awos worker start "<goal>"       Start work on a coding goal
    awos worker status                List all work sessions
    awos worker resume [session_id]   Continue paused work
    awos worker diff [session_id]     Show sandbox changes
    awos worker cancel [session_id]   Stop active work

Advanced commands:
    awos run <goal>              Execute via orchestrator directly (power users)
    awos stats                   Self-learning observability report
    awos stats --savings         Compounding cost savings report (show AWOS value)
    awos stats --json            JSON output (for piping/scripts)
    awos report                  PEI scorecard — client-facing proof of value
    awos report --html PATH      Write HTML report to file
    awos budget                  Month-to-date budget status
    awos sessions list           Runtime sessions (pause/resume internals)
    awos sessions resume <rs_id> Resume a paused session by ID
    awos mission guide           Real workload mission playbook (lab tool)
    awos mission start           Start Stage 1 worktree mission
    awos mission start --ci-rescue  CI Rescue Sprint (18 tasks, 28 reds)
    awos gauntlet list           Stage 1 risk gauntlet scenarios (lab tool)
    awos gauntlet run <ID>       Run gauntlet G1–G6
    awos memory search <query>   Search past interactions
    awos traces list             List reasoning trace sessions
    awos index                   (Re)build semantic codebase index

Environment:
    AWOS_DEBUG=1                 Verbose mode
    AWOS_MONTHLY_BUDGET=50       Higher budget cap (default: $20)
    AWOS_PROMPT_EVOLUTION=true   Enable PromptEvolver
    AWOS_TOOL_SYNTHESIS=true     Enable LiveToolSynthesizer
    AWOS_SCAFFOLD_EVOLUTION=true Enable ScaffoldEvolver
"""

import argparse
import os
import sys
import json
import shutil
import signal
import subprocess
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
    from memory.vector_memory import VectorMemory
    from memory.codebase_index import CodebaseIndex

    vm = VectorMemory(persist_dir=".awos/memory")
    ci = CodebaseIndex(project_root=Path("scaffold/agent"), vector_memory=vm)
    stats = ci.index_project(force=args.force)
    print(f"Indexed {stats['files']} files → {stats['chunks']} chunks")


def _sessions_store():
    from scaffold.agent.runtime_session import RuntimeSessionStore
    return RuntimeSessionStore()


def cmd_sessions_list(args):
    """List durable runtime sessions."""
    from scaffold.agent.runtime_session import SessionStatus

    store = _sessions_store()
    status = getattr(args, "status", None)
    status_enum = SessionStatus(status) if status else None
    sessions = store.list_sessions(status=status_enum)
    if not sessions:
        print("No runtime sessions found.")
        return

    print(f"\n{'─'*72}")
    print(f"  RUNTIME SESSIONS  ({len(sessions)} total)")
    print(f"{'─'*72}")
    for s in sessions[: getattr(args, "limit", 20)]:
        done = len(s.progress.completed_task_ids)
        total = s.progress.total_tasks or "?"
        sandbox = ""
        if s.sandbox.enabled and s.sandbox.worktree_path:
            sandbox = f"  worktree={s.sandbox.worktree_path}"
        print(
            f"  {s.session_id}  [{s.status.value:9}]  "
            f"{done}/{total} tasks  {s.goal[:40]}{'…' if len(s.goal) > 40 else ''}{sandbox}"
        )
    print()


def cmd_sessions_show(args):
    """Show one runtime session."""
    from scaffold.agent.runtime_session import SessionStatus

    store = _sessions_store()
    try:
        s = store.load(args.session_id)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Session not found: {exc}")
        raise SystemExit(1)

    print(f"\nSession:   {s.session_id}")
    status_display = s.status.value
    if s.pause_reason:
        status_display += f" ({s.pause_reason})"
    print(f"Status:    {status_display}")
    print(f"Goal:      {s.goal}")
    print(f"Root:      {s.codebase_root}")
    print(f"Progress:  {s.progress.completed_task_ids} done / {s.progress.total_tasks} total")
    if s.sandbox.enabled:
        print(f"Sandbox:   worktree={s.sandbox.worktree_path or 'n/a'}  branch={s.sandbox.branch or 'n/a'}")
    if s.paused_at:
        print(f"Paused at: {s.paused_at}")
    print()


def cmd_sessions_resume(args):
    """Resume a paused runtime session."""
    from scaffold.agent.runtime_session import SessionStatus

    store = _sessions_store()
    try:
        rs = store.load(args.session_id)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Session not found: {exc}")
        raise SystemExit(1)
    if rs.status != SessionStatus.PAUSED:
        print(f"Session {args.session_id} is {rs.status.value} — only paused sessions can resume.")
        raise SystemExit(1)

    from scaffold.agent.learning_policy import apply_kernel_defaults
    from scaffold.agent.orchestrator import Orchestrator
    from scaffold.agent.token_tracker import TokenTracker

    apply_kernel_defaults()
    os.environ.setdefault("AWOS_LEARNING_DISABLE", "true")
    tracker = TokenTracker(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0")))
    orch = Orchestrator(tracker=tracker)
    _prev = signal.getsignal(signal.SIGINT)

    def _on_sigint(*_):
        orch._pause_requested = True
        print("\n[SESSION] SIGINT — pausing after current task…", flush=True)

    signal.signal(signal.SIGINT, _on_sigint)
    try:
        result = orch.execute_feature(
            goal=rs.goal,
            codebase_root=rs.codebase_root or ".",
            session_id=rs.session_id,
            resume=True,
        )
    except KeyboardInterrupt:
        print("\n[SESSION] Interrupted — partial progress saved.")
        raise SystemExit(130)
    finally:
        signal.signal(signal.SIGINT, _prev)

    _print_run_result(result)


def _print_run_result(result: dict) -> None:
    print(f"\nGoal:     {result.get('goal', '')}")
    print(f"Success:  {'✓' if result.get('success') else '✗'}")
    completed = result.get("tasks_completed", 0)
    total = result.get("total_tasks", result.get("tasks_failed", 0) + completed)
    print(f"Tasks:    {completed}/{total} completed")
    if result.get("runtime_session_id"):
        print(f"Session:  {result['runtime_session_id']}")
    if result.get("errors"):
        for e in result["errors"][:5]:
            print(f"  ✗ {e}")


def cmd_run(args):
    """Execute a feature goal via the Orchestrator."""
    from scaffold.agent.learning_policy import apply_kernel_defaults
    from scaffold.agent.orchestrator import Orchestrator
    from scaffold.agent.token_tracker import TokenTracker

    apply_kernel_defaults()
    tracker = TokenTracker(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0")))
    orch = Orchestrator(tracker=tracker)
    session_id = getattr(args, "session_id", None)
    goal = getattr(args, "goal", None)
    if session_id and not goal:
        from scaffold.agent.runtime_session import RuntimeSessionStore
        try:
            goal = RuntimeSessionStore().load(session_id).goal
        except FileNotFoundError:
            print(f"Session not found: {session_id}")
            raise SystemExit(1)
    if not goal and not session_id:
        print("Provide a goal or --session <rs_id>.")
        raise SystemExit(1)

    _prev = signal.getsignal(signal.SIGINT)

    def _on_sigint(*_):
        orch._pause_requested = True
        print("\n[SESSION] SIGINT — pausing after current task…", flush=True)

    if os.getenv("AWOS_RUNTIME_SESSION", "").lower() in ("1", "true", "yes"):
        signal.signal(signal.SIGINT, _on_sigint)
    run_kwargs = dict(
        goal=goal,
        codebase_root=getattr(args, "root", ".") or ".",
        resume=getattr(args, "resume", False) and not session_id,
        session_id=session_id,
        auto_approve_plan=getattr(args, "auto_approve", False),
    )
    pre_planned = getattr(args, "pre_planned_tasks", None)
    if pre_planned is not None:
        run_kwargs["pre_planned_tasks"] = pre_planned
    try:
        result = orch.execute_feature(**run_kwargs)
    except KeyboardInterrupt:
        print("\n[SESSION] Interrupted — partial progress saved.")
        raise SystemExit(130)
    finally:
        signal.signal(signal.SIGINT, _prev)

    _print_run_result(result)


def _awos_repo_root() -> Path:
    return Path(__file__).resolve().parent


def _missions_dir() -> Path:
    return _awos_repo_root() / "docs" / "missions"


def _mission_kwargs(args) -> dict:
    return dict(
        long=getattr(args, "long", False),
        clawcode=getattr(args, "clawcode", False),
        ci_rescue=getattr(args, "ci_rescue", False),
        clawcode_ci_rescue=getattr(args, "clawcode_ci_rescue", False),
        config=getattr(args, "config", None),
    )


def _load_mission_config(
    *,
    long: bool = False,
    clawcode: bool = False,
    ci_rescue: bool = False,
    clawcode_ci_rescue: bool = False,
    config: str | None = None,
) -> dict:
    if config:
        path = Path(config).expanduser()
        if not path.is_absolute():
            path = _awos_repo_root() / path
        if not path.is_file():
            print(f"Error: mission config not found: {path}", file=sys.stderr)
            raise SystemExit(1)
        return json.loads(path.read_text(encoding="utf-8"))
    flags = sum([bool(long), bool(clawcode), bool(ci_rescue), bool(clawcode_ci_rescue)])
    if flags > 1:
        print("Error: use only one mission flag (--long, --clawcode, --ci-rescue, --clawcode-ci-rescue)", file=sys.stderr)
        raise SystemExit(1)
    if clawcode_ci_rescue:
        name = "clawcode_ci_rescue.json"
    elif clawcode:
        name = "stage1_phase_d_clawcode.json"
    elif ci_rescue:
        name = "ci_rescue_sprint.json"
    elif long:
        name = "stage1_long_workload.json"
    else:
        name = "stage1_real_workload.json"
    return json.loads((_missions_dir() / name).read_text(encoding="utf-8"))


def _mission_codebase_root(cfg: dict) -> str:
    """Resolve target repo path from mission config (relative to AWOS repo root)."""
    root = cfg.get("codebase_root", ".")
    path = Path(root).expanduser()
    if not path.is_absolute():
        path = _awos_repo_root() / path
    path = path.resolve()
    if not path.is_dir():
        print(f"Error: codebase_root not found: {path}", file=sys.stderr)
        raise SystemExit(1)
    return str(path)


def _ensure_git_repo(repo_path: str) -> None:
    """Initialize git in fixture repos that are not yet versioned."""
    p = Path(repo_path)
    if (p / ".git").exists():
        return
    print(f"[MISSION] Initializing git repo at {repo_path}")
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "mission buggy baseline"],
        cwd=repo_path,
        check=True,
    )


def get_latest_mission_session(
    *,
    long: bool = False,
    clawcode: bool = False,
    ci_rescue: bool = False,
    config: str | None = None,
):
    """Return most recent runtime session for a mission goal, or None."""
    from scaffold.agent.runtime_session import RuntimeSessionStore, goal_hash

    cfg = _load_mission_config(long=long, clawcode=clawcode, ci_rescue=ci_rescue, config=config)
    gh = goal_hash(cfg["goal"])
    matches = [s for s in RuntimeSessionStore().list_sessions() if s.goal_hash == gh]
    if not matches:
        return None
    matches.sort(key=lambda s: s.updated_at or "", reverse=True)
    return matches[0]


def load_mission_plan(cfg: dict, missions_dir: Path | None = None) -> list | None:
    """Load pre-planned tasks from mission config (inline plan or plan_file)."""
    base = missions_dir or _missions_dir()
    raw: list | None = None
    if isinstance(cfg.get("plan"), list):
        raw = cfg["plan"]
    elif plan_file := cfg.get("plan_file"):
        data = json.loads((base / plan_file).read_text(encoding="utf-8"))
        raw = data if isinstance(data, list) else data.get("plan", [])
    if not raw:
        return None
    from scaffold.agent.plan_actions import normalize_plan
    return normalize_plan(raw, ".")


def cmd_mission_guide(args):
    """Real workload mission playbook."""
    cfg = _load_mission_config(**_mission_kwargs(args))
    print(
        f"""
╔══════════════════════════════════════════════════════════════════╗
║  STAGE 1 REAL WORKLOAD MISSION                                   ║
╚══════════════════════════════════════════════════════════════════╝

WHAT WE ARE PROVING
  A real multi-step coding goal runs in an isolated git worktree,
  can pause (Ctrl+C), resume hours later, and leaves main repo clean.

YOUR MISSION GOAL ({cfg.get('id', 'mission')}, cheap-only, worktree sandbox):
  {cfg['goal'][:200]}…

STEP 1 — START
  awos mission start              # 5-task quick mission
  awos mission start --long       # 8-task fixed plan (~10–30 min)
  awos mission start --ci-rescue  # 18-task CI Rescue Sprint (28 reds)

STEP 2 — PAUSE (when task 2 finishes, or whenever)
  Press Ctrl+C once. Wait for "[SESSION] Pause requested".

STEP 3 — CHECK
  awos sessions list --status paused
  awos sessions show rs_<id>    # must show Sandbox worktree path

STEP 4 — RESUME (same day or hours later)
  awos sessions resume rs_<id>

STEP 5 — VERIFY SUCCESS
  awos sessions show rs_<id>    # status: completed
  git status                    # main repo clean
  ls .awos/worktrees/           # your sandbox diff lives here

SUCCESS CRITERIA
"""
    )
    for c in cfg.get("success_criteria", []):
        print(f"  • {c}")
    print(f"\nFull spec: docs/specs/stage1_real_workload_mission.md\n")


def cmd_mission_start(args):
    """Launch the configured real workload mission."""
    cfg = _load_mission_config(**_mission_kwargs(args))
    for k, v in cfg.get("env", {}).items():
        os.environ[k] = str(v)
    from scaffold.agent.learning_policy import apply_kernel_defaults
    apply_kernel_defaults()
    codebase_root = _mission_codebase_root(cfg)
    if cfg.get("ensure_git"):
        _ensure_git_repo(codebase_root)
    print(f"Mission: {cfg.get('title', cfg.get('id'))}")
    print(f"Target:  {codebase_root}")
    print(f"Env: AWOS_USE_WORKTREE={os.getenv('AWOS_USE_WORKTREE')}  AWOS_CHEAP_ONLY={os.getenv('AWOS_CHEAP_ONLY')}")
    print(f"Pause hint: {cfg.get('pause_hint', 'Ctrl+C after a task completes')}\n")
    plan = load_mission_plan(cfg)
    if plan:
        print(f"[MISSION] Pre-planned task list: {len(plan)} tasks (planner skipped)\n")
    ns = argparse.Namespace(
        goal=cfg["goal"],
        resume=False,
        session_id=None,
        root=codebase_root,
        pre_planned_tasks=plan,
        mission_config=cfg,
    )
    cmd_run(ns)
    _maybe_run_mission_assertions(cfg, ns)


def _maybe_run_mission_assertions(cfg: dict, args) -> None:
    """Post-run wedge assertions when mission config includes goal_assertions."""
    if not cfg.get("goal_assertions"):
        return
    from scaffold.agent.runtime_session import RuntimeSessionStore, goal_hash

    gh = goal_hash(cfg["goal"])
    matches = [s for s in RuntimeSessionStore().list_sessions() if s.goal_hash == gh]
    if not matches:
        print("[MISSION] No session for post-run assertions — skip wedge assert")
        return
    matches.sort(key=lambda s: s.updated_at or "", reverse=True)
    session = matches[0]
    if not session.sandbox.worktree_path:
        print("[MISSION] No worktree for post-run assertions — skip wedge assert")
        return
    goal_file = cfg.get("goal_assertions_file")
    if goal_file:
        assert_path = Path(goal_file)
        if not assert_path.is_absolute():
            assert_path = _awos_repo_root() / assert_path
    else:
        root = _mission_codebase_root(cfg)
        assert_path = Path(root) / "wedge_goal.json"
    if not assert_path.is_file():
        print(f"[MISSION] Assertions file not found: {assert_path}")
        return
    print(f"\n[MISSION] Post-run wedge assertions on {session.sandbox.worktree_path}")
    script = _awos_repo_root() / "scripts" / "wedge_v1_assert.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            session.sandbox.worktree_path,
            "--assertions",
            str(assert_path),
        ],
        cwd=_awos_repo_root(),
    )
    if result.returncode != 0:
        print("[MISSION] Wedge assertions FAILED — see output above")
        raise SystemExit(result.returncode)


def cmd_mission_status(args):
    """Show mission-related runtime sessions."""
    cfg = _load_mission_config(**_mission_kwargs(args))
    from scaffold.agent.runtime_session import RuntimeSessionStore, goal_hash

    store = RuntimeSessionStore()
    gh = goal_hash(cfg["goal"])
    matches = [s for s in store.list_sessions() if s.goal_hash == gh]
    if not matches:
        print("No sessions for current mission goal yet. Run: awos mission start")
        return
    print(f"\nMission sessions ({len(matches)}):\n")
    for s in matches:
        sandbox = ""
        if s.sandbox.enabled and s.sandbox.worktree_path:
            sandbox = f"  sandbox={s.sandbox.worktree_path}"
        print(
            f"  {s.session_id}  [{s.status.value}]  "
            f"{len(s.progress.completed_task_ids)}/{s.progress.total_tasks} tasks{sandbox}"
        )
        if s.status.value == "paused":
            print(f"    → resume: awos sessions resume {s.session_id}")
    print()


def cmd_guide(args):
    """Plain-English intro: what AWOS is and how to run it."""
    root = Path(__file__).resolve().parent
    print(
        f"""
╔══════════════════════════════════════════════════════════════════╗
║  AWOS — Autonomous Work OS (coding agent = App #1)               ║
╚══════════════════════════════════════════════════════════════════╝

WHAT IT IS
  AWOS takes a goal in plain English, plans tasks, edits code, verifies,
  and learns from outcomes. State lives in .awos/ (sessions, errors, rewards).

HOW TO INVOKE (pick one)
  1) From this repo (always works):
       cd {root}
       python3 awos.py guide
       python3 awos.py gauntlet list
       python3 awos.py run "your goal here"

  2) Short command `awos` on your PATH (one-time setup):
       export PATH="{root / 'scripts'}:$PATH"
       awos gauntlet list

  Spelling: gauntlet  (not guantlet)

COMMON COMMANDS
  awos guide              This help
  awos run "<goal>"       Run the coding agent on a goal
  awos gauntlet list      Stress-test scenarios (G1–G6)
  awos gauntlet run G6    Run one scenario (SIGINT pause proof)
  awos sessions list      Paused / completed runtime sessions
  awos sessions resume rs_<id>   Continue a paused run
  awos stats              Learning + performance snapshot

GAUNTLET = extreme stress tests (not normal unit tests)
  G2  pause/resume mid-plan        ~15s, automated
  G3  worker fail → MCTS trace     ~20s, automated
  G6  Ctrl+C pause → resume        ~1–2 min, live APIs
  G1  worktree isolation           live run, cheap mode

SETUP CHECKLIST
  [ ] cd to repo root
  [ ] cp .env.example .env  and add ANTHROPIC_API_KEY / GOOGLE_API_KEY
  [ ] python3 awos.py guide   (you are here)
  [ ] python3 awos.py gauntlet run G2   (quick smoke)

Docs: VISION.md · docs/stage1_risk_gauntlet_booklet.md
"""
    )


def cmd_gauntlet_list(args):
    """List Stage 1 risk gauntlet scenarios."""
    import subprocess
    script = Path(__file__).resolve().parent / "scripts" / "stage1_gauntlet_runner.py"
    subprocess.run([sys.executable, str(script), "list"], check=False)


def cmd_gauntlet_run(args):
    """Run one risk gauntlet scenario."""
    import subprocess
    script = Path(__file__).resolve().parent / "scripts" / "stage1_gauntlet_runner.py"
    rc = subprocess.run(
        [sys.executable, str(script), "run", args.scenario_id.upper()]
    ).returncode
    raise SystemExit(rc)


def cmd_stats(args):
    """Show self-learning observability report."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "scaffold" / "agent"))
    
    # If --savings flag, show cost savings analysis
    if getattr(args, "savings", False):
        from budget_ledger import get_ledger
        ledger = get_ledger()
        status = ledger.get_status(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0")))
        
        print("\n╔═════════════════════════════════════════════════════════════════════╗")
        print("║        AWOS COMPOUNDING ADVANTAGE — Cost Savings Report            ║")
        print("╚═════════════════════════════════════════════════════════════════════╝\n")
        
        # Current month stats
        print(f"Month:          {status['month']}")
        print(f"Total spent:    ${status['spent']:.2f}")
        print(f"Cache savings:  ${status.get('cache_savings', 0):.2f}")
        print(f"Requests:       {status.get('requests', 0)}")
        print()
        
        # Show cost per request trend (if enough data)
        records = ledger._records
        if len(records) >= 10:
            # Group by batches of 10 requests
            batches = []
            for i in range(0, len(records), 10):
                batch = records[i:i+10]
                batch_cost = sum(r.get("cost", 0) for r in batch)
                batches.append(batch_cost / len(batch))
            
            print("Cost per request trend (batches of 10):")
            print()
            for idx, avg_cost in enumerate(batches[:10], 1):  # Show first 10 batches
                bar_len = int(avg_cost * 1000)  # Scale for visualization
                bar = "█" * min(bar_len, 50)
                print(f"  Batch {idx:2d}: ${avg_cost:.4f} {bar}")
            
            if len(batches) > 1:
                first_batch = batches[0]
                last_batch = batches[-1]
                if first_batch > 0:
                    savings_pct = ((first_batch - last_batch) / first_batch) * 100
                    print()
                    print(f"Learning trend: {savings_pct:+.1f}% cost change from first to last batch")
            print()
        else:
            print("(Not enough data yet — need 10+ requests to show trend)")
            print()
        
        # Value prop
        print("═══════════════════════════════════════════════════════════════════════")
        print("AWOS VALUE PROPOSITION:")
        print()
        print("  Cursor: Same cost every job ($3 → $3 → $3)")
        print("  AWOS:   Gets cheaper with learning ($3 → $1 → $0.30)")
        print()
        print("  How?")
        print("    • DeepSeek routing (instant 50% savings)")
        print("    • PromptEvolver learns YOUR patterns")
        print("    • SkillLibrary caches YOUR solutions")
        print("    • Fewer retries = lower cost")
        print()
        print("  The more you use it, the cheaper it gets.")
        print("═══════════════════════════════════════════════════════════════════════\n")
        return
    
    # Default: full self-learning metrics
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
# Worker commands (Phase C — simple product surface)
# ═════════════════════════════════════════════════════════════════════════════

def cmd_worker_start(args):
    """Start work on a goal (simple interface)."""
    if not args.goal or not args.goal.strip():
        print("Error: Goal required", file=sys.stderr)
        print("Usage: awos worker start \"your goal here\"", file=sys.stderr)
        sys.exit(1)
    
    # Map to orchestrator run with runtime session
    root = getattr(args, "root", ".") or "."
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        print(f"Error: --root not found: {root_path}", file=sys.stderr)
        sys.exit(1)

    wrapped_args = argparse.Namespace(
        goal=args.goal,
        resume=False,
        session_id=None,
        root=str(root_path),
        pre_planned_tasks=None,
        auto_approve=getattr(args, "auto_approve", False),
    )
    
    print(f"Starting work: {args.goal}")
    print(f"Target repo: {root_path}")
    if getattr(args, "auto_approve", False):
        print(f"[AUTO-APPROVE] Plans will be executed without review")
    print(f"Ctrl+C to pause anytime\n")
    
    cmd_run(wrapped_args)


def cmd_worker_status(args):
    """List all work sessions."""
    from runtime_session import RuntimeSessionStore
    
    store = _sessions_store()
    sessions = store.list_sessions()
    
    if not sessions:
        print("No active work.")
        print("\nStart work with: awos worker start \"your goal\"")
        return
    
    print(f"\n{len(sessions)} session(s):\n")
    for s in sessions:
        prog = s.progress
        cost = s.budget.get("spent_usd", 0)
        goal_short = (s.goal[:40] + "...") if len(s.goal) > 40 else s.goal
        
        status_display = s.status.value
        if s.pause_reason:
            status_display += f" ({s.pause_reason})"
        
        print(f"{s.session_id}  {status_display:15s}  {len(prog.completed_task_ids)}/{prog.total_tasks} tasks  ${cost:.3f}  \"{goal_short}\"")
        
        if s.sandbox.enabled and s.sandbox.worktree_path:
            print(f"  └─ sandbox: {s.sandbox.worktree_path}")
    
    print()


def cmd_worker_resume(args):
    """Resume paused work."""
    from runtime_session import RuntimeSessionStore, SessionStatus
    
    store = _sessions_store()
    session_id = args.session_id
    
    if not session_id:
        # Find latest paused
        sessions = [s for s in store.list_sessions() if s.status == SessionStatus.PAUSED]
        if not sessions:
            print("No paused work found.")
            print("\nStart new work with: awos worker start \"your goal\"")
            sys.exit(1)
        if len(sessions) > 1:
            print(f"Multiple paused sessions found. Specify one:\n")
            for s in sessions:
                print(f"  awos worker resume {s.session_id}")
            sys.exit(1)
        session_id = sessions[0].session_id
    
    # Load session
    try:
        session = store.load(session_id)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Session not found: {exc}", file=sys.stderr)
        sys.exit(1)
    
    if session.status != SessionStatus.PAUSED:
        if session.status == SessionStatus.COMPLETED:
            print(f"Session {session_id} already completed.")
            print(f"\nReview changes with: awos worker diff {session_id}")
            sys.exit(1)
        elif session.status == SessionStatus.CANCELLED:
            print(f"Session {session_id} was cancelled.")
            sys.exit(1)
        else:
            print(f"Session {session_id} is {session.status.value} (not paused).")
            sys.exit(1)
    
    print(f"Resuming: {session.goal}")
    print(f"Session: {session_id}")
    if session.sandbox.worktree_path:
        print(f"Sandbox: {session.sandbox.worktree_path}")
    print()
    
    # Resume
    wrapped_args = argparse.Namespace(
        goal=session.goal,
        resume=True,
        session_id=session_id,
        root=".",
        pre_planned_tasks=None,
    )
    cmd_run(wrapped_args)


def cmd_worker_diff(args):
    """Show sandbox changes."""
    from runtime_session import RuntimeSessionStore
    import subprocess
    
    store = _sessions_store()
    session_id = args.session_id
    
    if not session_id:
        # Latest session
        sessions = store.list_sessions()
        if not sessions:
            print("No work history.")
            sys.exit(1)
        session = max(sessions, key=lambda s: s.updated_at)
        session_id = session.session_id
    else:
        try:
            session = store.load(session_id)
        except (FileNotFoundError, ValueError) as exc:
            print(f"Session not found: {exc}", file=sys.stderr)
            sys.exit(1)
    
    if not session.sandbox.enabled or not session.sandbox.worktree_path:
        print(f"Session {session_id} has no sandbox.")
        sys.exit(1)
    
    worktree = Path(session.sandbox.worktree_path)
    if not worktree.exists():
        print(f"Sandbox missing: {worktree}")
        sys.exit(1)
    
    print(f"Session: {session_id}")
    print(f"Sandbox: {worktree}")
    print(f"\nChanges vs main:\n")
    
    # git diff main
    try:
        result = subprocess.run(
            ["git", "diff", "main"],
            cwd=worktree,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            print(result.stdout)
        else:
            print("(no changes)")
    except Exception as exc:
        print(f"Error running git diff: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_worker_cancel(args):
    """Cancel active/paused work."""
    from runtime_session import RuntimeSessionStore, SessionStatus
    
    store = _sessions_store()
    session_id = args.session_id
    
    if not session_id:
        # Find latest active or paused
        sessions = [s for s in store.list_sessions() if s.status in (SessionStatus.RUNNING, SessionStatus.PAUSED)]
        if not sessions:
            print("No active work to cancel.")
            sys.exit(1)
        if len(sessions) > 1:
            print(f"Multiple active sessions. Specify one:\n")
            for s in sessions:
                print(f"  awos worker cancel {s.session_id}")
            sys.exit(1)
        session_id = sessions[0].session_id
    
    try:
        session = store.load(session_id)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Session not found: {exc}", file=sys.stderr)
        sys.exit(1)
    
    if session.status == SessionStatus.CANCELLED:
        print(f"Session {session_id} already cancelled.")
        sys.exit(0)
    
    if session.status == SessionStatus.COMPLETED:
        print(f"Session {session_id} already completed (cannot cancel).")
        sys.exit(1)
    
    # Finalize as cancelled
    store.finalize(session, SessionStatus.CANCELLED)
    
    print(f"Session {session_id} cancelled")
    if session.sandbox.worktree_path:
        print(f"Sandbox kept at: {session.sandbox.worktree_path}")
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

    # worker (Phase C — simple product surface)
    worker = sub.add_parser("worker", help="Simple coding worker interface")
    worker_sub = worker.add_subparsers(dest="worker_cmd", required=True)
    
    w_start = worker_sub.add_parser("start", help="Start work on a goal")
    w_start.add_argument("goal", help="What to build/fix")
    w_start.add_argument(
        "--root",
        default=".",
        help="Target repo path (default: current directory)",
    )
    w_start.add_argument(
        "--auto-approve",
        action="store_true",
        help="Auto-approve plans without interactive review (useful for scripts)",
    )
    
    worker_sub.add_parser("status", help="List all work sessions")
    
    w_resume = worker_sub.add_parser("resume", help="Resume paused work")
    w_resume.add_argument("session_id", nargs="?", help="Session ID (optional, defaults to latest paused)")
    
    w_diff = worker_sub.add_parser("diff", help="Show sandbox changes")
    w_diff.add_argument("session_id", nargs="?", help="Session ID (optional, defaults to latest)")
    
    w_cancel = worker_sub.add_parser("cancel", help="Cancel active/paused work")
    w_cancel.add_argument("session_id", nargs="?", help="Session ID (optional, defaults to latest active)")

    # mission
    mis = sub.add_parser("mission", help="Stage 1 real workload mission (worktree + pause/resume)")
    mis_sub = mis.add_subparsers(dest="mission_cmd", required=True)
    p_guide = mis_sub.add_parser("guide", help="Mission playbook")
    p_guide.add_argument("--long", action="store_true", help="Long mission playbook")
    p_guide.add_argument("--clawcode", action="store_true", help="Phase D clawcode mission playbook")
    p_guide.add_argument("--ci-rescue", action="store_true", help="CI Rescue Sprint playbook")
    p_guide.add_argument("--config", metavar="PATH", help="Mission JSON config path")
    p_start = mis_sub.add_parser("start", help="Start mission (worktree, cheap-only)")
    p_start.add_argument("--long", action="store_true", help="8-task long workload mission (AWOS repo)")
    p_start.add_argument("--clawcode", action="store_true", help="Phase D: 14-task mission on clawcode repo")
    p_start.add_argument("--ci-rescue", action="store_true", dest="ci_rescue", help="CI Rescue Sprint: 18-task, 28 reds")
    p_start.add_argument("--clawcode-ci-rescue", action="store_true", dest="clawcode_ci_rescue", help="clawcode real repo: 12 failing tests")
    p_start.add_argument("--config", metavar="PATH", help="Mission JSON config (e.g. docs/missions/ci_rescue_sprint.json)")
    p_status = mis_sub.add_parser("status", help="Mission session progress")
    p_status.add_argument("--long", action="store_true", help="Show long-mission sessions")
    p_status.add_argument("--clawcode", action="store_true", help="Show clawcode mission sessions")
    p_status.add_argument("--ci-rescue", action="store_true", dest="ci_rescue", help="Show CI Rescue Sprint sessions")
    p_status.add_argument("--clawcode-ci-rescue", action="store_true", dest="clawcode_ci_rescue", help="Show clawcode CI rescue sessions")
    p_status.add_argument("--config", metavar="PATH", help="Mission JSON config path")

    # guide
    sub.add_parser("guide", help="What AWOS is and how to run commands")

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
    run.add_argument("goal", nargs="?", help="Feature description (quote if multi-word)")
    run.add_argument("--resume", action="store_true", help="Resume from last run for this goal")
    run.add_argument("--session", dest="session_id", metavar="RS_ID", help="Resume this runtime session")
    run.add_argument("--root", default=".", help="Target repo path (default: current directory)")

    # sessions — durable pause/resume (RuntimeSession)
    sess = sub.add_parser("sessions", help="Runtime session commands (pause/resume)")
    sess_sub = sess.add_subparsers(dest="sessions_cmd", required=True)
    sess_list = sess_sub.add_parser("list", help="List runtime sessions")
    sess_list.add_argument("--status", choices=["pending", "running", "paused", "completed", "failed", "cancelled"])
    sess_list.add_argument("--limit", type=int, default=20)
    sess_show = sess_sub.add_parser("show", help="Show session details")
    sess_show.add_argument("session_id", metavar="RS_ID")
    sess_resume = sess_sub.add_parser("resume", help="Resume a paused session")
    sess_resume.add_argument("session_id", metavar="RS_ID")

    # gauntlet
    gnt = sub.add_parser("gauntlet", help="Stage 1 risk gauntlet stress tests")
    gnt_sub = gnt.add_subparsers(dest="gauntlet_cmd", required=True)
    gnt_sub.add_parser("list", help="List scenarios")
    gnt_run = gnt_sub.add_parser("run", help="Run scenario by ID")
    gnt_run.add_argument("scenario_id", metavar="ID", help="e.g. G2, G6")

    # goals
    sub.add_parser("goals", help="List tracked goals and their status")

    # stats
    stats = sub.add_parser("stats", help="Self-learning observability report")
    stats.add_argument("--json", action="store_true", help="Output as JSON")
    stats.add_argument("--store", default=".awos", help="Path to .awos store (default: .awos)")
    stats.add_argument("--savings", action="store_true", help="Show compounding cost savings report")

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
    # Common typo: guantlet → gauntlet
    if len(sys.argv) > 1 and sys.argv[1].lower() == "guantlet":
        print("Note: use 'gauntlet' (not 'guantlet').", file=sys.stderr)
        sys.argv[1] = "gauntlet"

    parser = build_parser()
    try:
        args = parser.parse_args()
    except SystemExit as exc:
        if exc.code != 0 and len(sys.argv) > 1:
            cmd = sys.argv[1].lower()
            if "guant" in cmd and cmd != "gauntlet":
                print("\nDid you mean:  python3 awos.py gauntlet list", file=sys.stderr)
            print("Run:  python3 awos.py guide", file=sys.stderr)
        raise

    if not args.command:
        cmd_guide(argparse.Namespace())
        return

    handler = None
    if args.command == "memory" and getattr(args, "mem_cmd", None):
        handler = globals().get(f"cmd_memory_{args.mem_cmd}")
    elif args.command == "traces" and getattr(args, "trace_cmd", None):
        handler = globals().get(f"cmd_traces_{args.trace_cmd}")
    elif args.command == "sessions" and getattr(args, "sessions_cmd", None):
        handler = globals().get(f"cmd_sessions_{args.sessions_cmd}")
    elif args.command == "worker" and getattr(args, "worker_cmd", None):
        handler = globals().get(f"cmd_worker_{args.worker_cmd}")
    elif args.command == "gauntlet":
        handler = cmd_gauntlet_run if getattr(args, "gauntlet_cmd", None) == "run" else cmd_gauntlet_list
    elif args.command == "mission":
        _mission = {"guide": cmd_mission_guide, "start": cmd_mission_start, "status": cmd_mission_status}
        handler = _mission.get(getattr(args, "mission_cmd", ""))
    else:
        handler = globals().get(f"cmd_{args.command}")

    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()

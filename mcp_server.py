#!/usr/bin/env python3
"""
AWOS MCP Server — exposes AWOS capabilities to any MCP client.

Tools:
  - awos_run(goal, codebase_root, budget_usd, privacy)
                                         → queues a host job (own worktree,
                                           review branch, budget); returns its id
  - awos_job_status(job_id)              → state/result of a queued job
  - awos_chat(message)                   → answer-only reply (never edits files)
  - awos_memory_search(query, n)         → vector memory search (read-only)
  - awos_budget_status()                 → month-to-date budget
  - awos_traces_list()                   → reasoning trace sessions (read-only)

Safety: no tool edits the caller's directory in place. Coding work goes
through the host queue (`awos serve` runs it): the worker makes a detached
worktree, the job spends at most its budget, and its result lands on a review
branch. awos_run refuses a codebase_root that is not the top of an existing
git repository with at least one commit. State is read from AWOS's own state
root, never from the MCP client's cwd, and the read-only tools never create
state directories.

Usage:
    python3 mcp_server.py          # stdio transport (for IDE)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _p in (str(ROOT), str(ROOT / "scaffold")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
except ImportError:  # mcp 2.x renamed it
    from mcp.server.mcpserver import MCPServer as _Server

mcp = _Server("awos")

#: Upper bound on what one MCP-queued job may spend (USD).
MAX_MCP_BUDGET_USD = float(os.getenv("AWOS_MCP_MAX_BUDGET_USD", "5"))


# ── Helpers ───────────────────────────────────────────────────────────

def _json(obj: dict) -> str:
    return json.dumps(obj, indent=2, default=str)


def _state_dir(*parts: str) -> Path:
    from scaffold.agent import paths
    return paths.state_root().joinpath(".awos", *parts)


def _git_root(path: Path):
    """(top-level, None) for the top of a git repo with a commit, else (None, why)."""
    try:
        top = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=15)
        head = subprocess.run(["git", "-C", str(path), "rev-parse", "--verify", "HEAD"],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git is not available ({exc})"
    if top.returncode != 0:
        return None, f"{path} is not inside a git repository"
    if Path(top.stdout.strip()).resolve() != path:
        return None, (f"{path} is not the top of its git repository "
                      f"({top.stdout.strip()}); pass the repository root")
    if head.returncode != 0:
        return None, f"{path} has no commits yet; commit once so the job has a base"
    return path, None


def _queue():
    from scaffold.agent.host.queue import JobQueue
    return JobQueue()


# ── Tool 1: awos_run ──────────────────────────────────────────────────

@mcp.tool()
def awos_run(goal: str, codebase_root: str = ".", budget_usd: float = 1.0,
             privacy: str = "cloud_ok") -> str:
    """Queue a coding goal as an AWOS host job.

    The job runs in its own git worktree (your checkout is never edited), is
    capped at budget_usd, and ends on a review branch awos/<id>. Run
    `awos serve` (or keep the AWOS worker running) to execute it; poll with
    awos_job_status.

    Args:
        goal: The coding goal to accomplish (e.g. "add user auth")
        codebase_root: Top-level directory of an existing git repository
        budget_usd: Spend cap for the whole job (0 < budget <= AWOS_MCP_MAX_BUDGET_USD)
        privacy: "cloud_ok" or "local_only"

    Returns:
        JSON: {"queued": true, "job_id": ...} or {"queued": false, "error": ...}
    """
    try:
        if not isinstance(goal, str) or not goal.strip():
            return _json({"queued": False, "error": "a job needs a goal"})
        try:
            budget = float(budget_usd)
        except (TypeError, ValueError):
            return _json({"queued": False, "error": "budget_usd must be a number"})
        if not (0 < budget <= MAX_MCP_BUDGET_USD):
            return _json({"queued": False,
                          "error": f"budget_usd must be > 0 and <= {MAX_MCP_BUDGET_USD:g}"})
        root = Path(codebase_root or ".").expanduser().resolve()
        if not root.is_dir():
            return _json({"queued": False, "error": f"not a directory: {root}"})
        repo, why = _git_root(root)
        if repo is None:
            return _json({"queued": False, "error": f"refused: {why}"})
        job = _queue().submit(goal, str(repo), kind="coding", budget_usd=budget,
                              privacy=privacy)
        return _json({"queued": True, "job_id": job.id, "repo": job.repo_path,
                      "budget_usd": job.budget_usd, "privacy": job.privacy,
                      "review_branch": f"awos/{job.id[:8]}",
                      "note": "runs in its own worktree; start `awos serve` if no worker is running"})
    except Exception as exc:  # noqa: BLE001 - an MCP tool answers, never raises
        return _json({"queued": False, "error": str(exc)})


@mcp.tool()
def awos_job_status(job_id: str) -> str:
    """State and result of a job queued with awos_run.

    Args:
        job_id: The id awos_run returned

    Returns:
        JSON with state, attempts, error, result
    """
    try:
        job = _queue().get(job_id)
        if job is None:
            return _json({"error": f"no such job: {job_id}"})
        return _json({"job_id": job.id, "state": job.state, "attempts": job.attempts,
                      "error": job.error, "result": job.result})
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)})


# ── Tool 2: awos_chat ───────────────────────────────────────────────

@mcp.tool()
def awos_chat(message: str) -> str:
    """Ask AWOS a question and get a text answer.

    Answer-only: this never edits files, runs workflows or self-improvement.
    For code changes use awos_run, which queues an isolated job.

    Args:
        message: The question to send

    Returns:
        Agent's response text
    """
    try:
        from agent.unified_agent import UnifiedAgent
        agent = UnifiedAgent()
        # Only the reasoning handler: handle_request() can route to the
        # coding / planner / self-improvement handlers, which edit
        # project_root in place with no worktree or budget.
        return str(agent._handle_reasoning(message) or "")
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"


# ── Tool 3: awos_memory_search ──────────────────────────────────────

@mcp.tool()
def awos_memory_search(query: str, n: int = 3) -> str:
    """Search AWOS vector memory for past interactions similar to a query.

    Read-only: uses AWOS's own state dir, never the client's cwd, and returns
    no results (creating nothing) when there is no memory yet.

    Args:
        query: Search query string
        n: Number of results to return (default: 3)

    Returns:
        JSON list of matches with similarity scores
    """
    try:
        persist = _state_dir("memory")
        if not persist.is_dir():
            return _json({"results": [], "note": f"no memory at {persist}"})
        from agent.memory.vector_memory import VectorMemory
        mem = VectorMemory(persist_dir=str(persist))
        results = mem.retrieve(query, n_results=max(1, min(int(n), 20)))
        return _json({"results": results})
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)})


# ── Tool 4: awos_budget_status ──────────────────────────────────────

@mcp.tool()
def awos_budget_status() -> str:
    """Return current month-to-date API budget status.

    Returns:
        JSON with spent, remaining, percent_used, monthly_budget
    """
    try:
        from agent.budget_ledger import get_ledger
        ledger = get_ledger()
        status = ledger.get_status()
        return _json(status)
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)})


# ── Tool 5: awos_traces_list ────────────────────────────────────────

@mcp.tool()
def awos_traces_list() -> str:
    """List reasoning trace sessions recorded by AWOS (read-only).

    Returns:
        JSON list of sessions (id, goal, trace_count, timestamp)
    """
    try:
        persist = _state_dir("traces")
        if not persist.is_dir():
            return _json({"sessions": []})
        from agent.core.reasoning import ReasoningTraceStore
        store = ReasoningTraceStore(persist_dir=str(persist))
        sessions = store.list_sessions()
        return _json({"sessions": sessions})
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)})


# ── Entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")

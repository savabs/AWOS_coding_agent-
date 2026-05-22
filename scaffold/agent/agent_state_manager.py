"""
AgentStateManager — persist per-goal execution state across sessions.
State file: .awos/state/<goal_hash>.json
"""
import hashlib
import json
import time
from pathlib import Path
from typing import Optional


class AgentStateManager:
    """Load and save per-goal agent state between runs."""

    STATE_DIR = Path(".awos/state")

    def __init__(self, state_dir: Optional[str] = None) -> None:
        self.state_dir = Path(state_dir) if state_dir else self.STATE_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────

    def load(self, goal: str) -> dict:
        """
        Load existing state for a goal, or return empty state dict.
        Returns: {goal, goal_hash, completed_task_ids, failed_task_ids, session_ids, status}
        """
        path = self._path(goal)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return self._empty(goal)

    def save(self, state: dict) -> None:
        """Persist state to disk."""
        state["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        path = self._path(state["goal"])
        path.write_text(json.dumps(state, indent=2))

    def mark_complete(self, state: dict, task_id) -> dict:
        """Mark a task as completed and save."""
        if task_id not in state["completed_task_ids"]:
            state["completed_task_ids"].append(task_id)
        state["failed_task_ids"] = [t for t in state["failed_task_ids"] if t != task_id]
        self.save(state)
        return state

    def mark_failed(self, state: dict, task_id) -> dict:
        """Mark a task as failed and save."""
        if task_id not in state["failed_task_ids"]:
            state["failed_task_ids"].append(task_id)
        self.save(state)
        return state

    def add_session(self, state: dict, session_id: str) -> dict:
        """Record a reasoning session ID against this goal."""
        if session_id not in state["session_ids"]:
            state["session_ids"].append(session_id)
        self.save(state)
        return state

    def finalize(self, state: dict, success: bool) -> dict:
        """Mark goal as complete or failed."""
        state["status"] = "complete" if success else "failed"
        self.save(state)
        return state

    def list_goals(self) -> list[dict]:
        """List all tracked goals with their status."""
        goals = []
        for f in self.state_dir.glob("*.json"):
            try:
                goals.append(json.loads(f.read_text()))
            except Exception:
                pass
        return sorted(goals, key=lambda x: x.get("last_updated", ""), reverse=True)

    # ── Private ───────────────────────────────────────────────────────

    def _path(self, goal: str) -> Path:
        h = hashlib.sha256(goal.strip().lower().encode()).hexdigest()[:10]
        return self.state_dir / f"{h}.json"

    @staticmethod
    def _empty(goal: str) -> dict:
        return {
            "goal": goal,
            "goal_hash": hashlib.sha256(goal.strip().lower().encode()).hexdigest()[:10],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "completed_task_ids": [],
            "failed_task_ids": [],
            "session_ids": [],
            "status": "in_progress",
        }

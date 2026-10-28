"""
worktree.py — Git worktree isolation for AWOS (P2.3).

Each feature execution runs in an isolated git worktree branch.
Enables safe parallel execution, easy rollback, and clean diffs.

Gate: AWOS_USE_WORKTREE=true

Usage:
    wm = WorktreeManager(repo_root="/path/to/repo")
    worktree_path = wm.create_worktree("feature-abc123")
    try:
        ...execute tasks in worktree_path...
        wm.commit_worktree("feature-abc123", "AWOS: add login method")
        merged = wm.merge_to_main("feature-abc123")
    finally:
        wm.cleanup_worktree("feature-abc123")
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class WorktreeManager:
    """
    Manages git worktrees for isolated AWOS feature execution (P2.3).
    Requires git >= 2.5 (worktrees are stable since then).
    """

    def __init__(self, repo_root: str) -> None:
        self.repo_root = str(repo_root)
        self.worktrees: dict[str, str] = {}  # feature_id → worktree_path

    # ── Public API ────────────────────────────────────────────────────────────

    def create_worktree(self, feature_id: str) -> str:
        """
        Create a git worktree + new branch for feature_id.
        Returns the absolute path to the worktree directory.
        Raises RuntimeError if git command fails.
        """
        branch_name = self._branch_name(feature_id)
        worktree_path = Path(self.repo_root) / ".awos" / "worktrees" / feature_id
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, str(worktree_path), "HEAD"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"[Worktree] git worktree add failed: {result.stderr.strip()}"
            )

        self.worktrees[feature_id] = str(worktree_path)
        logger.info("[Worktree] created %s → branch %s", worktree_path, branch_name)
        return str(worktree_path)

    def register_worktree(self, feature_id: str, worktree_path: str) -> None:
        """Register an existing worktree path (e.g. on session resume)."""
        path = str(Path(worktree_path).resolve())
        if not Path(path).is_dir():
            raise FileNotFoundError(f"[Worktree] path does not exist: {path}")
        self.worktrees[feature_id] = path
        logger.info("[Worktree] registered %s → %s", feature_id, path)

    def get_diff(self, feature_id: str) -> str:
        """Return unified diff of all changes in the worktree vs HEAD."""
        worktree_path = self._require_worktree(feature_id)
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True, text=True, cwd=worktree_path,
        )
        return result.stdout

    def commit_worktree(self, feature_id: str, message: str) -> str:
        """
        Stage all changes and commit in the worktree.
        Returns the new commit hash.
        """
        worktree_path = self._require_worktree(feature_id)
        subprocess.run(["git", "add", "-A"], cwd=worktree_path, check=True)
        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, cwd=worktree_path,
        )
        if result.returncode != 0:
            logger.warning("[Worktree] commit failed (nothing to commit?): %s", result.stderr)
            return ""
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=worktree_path,
        )
        return rev.stdout.strip()

    def merge_to_main(self, feature_id: str) -> bool:
        """
        Merge the feature branch into the current branch (no-ff).
        Returns True on success.
        """
        branch = self._branch_name(feature_id)
        result = subprocess.run(
            ["git", "merge", branch, "--no-ff", "-m",
             f"AWOS: merge feature/{feature_id[:20]}"],
            capture_output=True, text=True, cwd=self.repo_root,
        )
        if result.returncode != 0:
            logger.warning("[Worktree] merge failed: %s", result.stderr.strip())
        return result.returncode == 0

    def cleanup_worktree(self, feature_id: str) -> None:
        """Remove the worktree directory and delete its branch. Silent on errors."""
        if feature_id not in self.worktrees:
            return
        worktree_path = self.worktrees[feature_id]
        branch = self._branch_name(feature_id)
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=self.repo_root, capture_output=True,
        )
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=self.repo_root, capture_output=True,
        )
        del self.worktrees[feature_id]
        logger.info("[Worktree] cleaned up feature %s", feature_id)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _branch_name(feature_id: str) -> str:
        safe = feature_id[:20].replace(" ", "-").replace("/", "_")
        return f"awos/feature/{safe}"

    def _require_worktree(self, feature_id: str) -> str:
        if feature_id not in self.worktrees:
            raise KeyError(f"[Worktree] unknown feature_id: {feature_id!r}")
        return self.worktrees[feature_id]

    @staticmethod
    def feature_id_from_goal(goal: str) -> str:
        """Deterministic feature_id from a goal string (md5 prefix)."""
        return hashlib.md5(goal.encode()).hexdigest()[:8]

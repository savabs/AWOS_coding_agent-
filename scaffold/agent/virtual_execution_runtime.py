"""
virtual_execution_runtime.py — Isolated git worktree execution per RuntimeSession.

Wraps WorktreeManager with session-scoped sandbox fields on rs_*.json.
Gate: AWOS_USE_WORKTREE=true

Spec: docs/specs/runtime_session_spec.md (Phase 3)
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from scaffold.agent.runtime_session import (
    RuntimeSession,
    RuntimeSessionStore,
    SessionSandbox,
    SessionStatus,
)
from scaffold.agent.worktree import WorktreeManager

logger = logging.getLogger(__name__)


def worktree_enabled() -> bool:
    return os.getenv("AWOS_USE_WORKTREE", "").lower() in ("1", "true", "yes")


def auto_merge_enabled() -> bool:
    return os.getenv("AWOS_WORKTREE_AUTO_MERGE", "").lower() in ("1", "true", "yes")


def is_git_repository(path: str) -> bool:
    """Return True if path is inside a git work tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except OSError:
        return False


def feature_id_for_session(session: RuntimeSession) -> str:
    if session.session_id.startswith("rs_"):
        return session.session_id[3:]
    return WorktreeManager.feature_id_from_goal(session.goal or "awos")


_SKIP_SYNC_DIR_NAMES = frozenset({
    ".git", ".awos", "__pycache__", "venv", "node_modules", "site-packages",
})


def should_sync_repo_file(rel: Path) -> bool:
    """Return True if a relative repo file should be mirrored into a worktree."""
    if not rel.parts:
        return False
    for part in rel.parts:
        if part in _SKIP_SYNC_DIR_NAMES:
            return False
        if part.startswith(".") and part != ".github":
            return False
    return True


def sync_worktree_from_repo(repo_root: str, worktree_path: str, *, max_files: int = 5000) -> int:
    """
    Mirror files from the main working tree that are missing in the worktree.

    Git worktrees checkout committed HEAD only — untracked or dirty files on the
    main tree are absent in a fresh worktree. AWOS copies them so the agent sees
    the same files the developer sees.
    """
    src_root = Path(repo_root).resolve()
    dst_root = Path(worktree_path).resolve()
    if src_root == dst_root:
        return 0

    copied = 0
    for src in src_root.rglob("*"):
        if not src.is_file():
            continue
        try:
            rel = src.relative_to(src_root)
        except ValueError:
            continue
        if not should_sync_repo_file(rel):
            continue
        dst = dst_root / rel
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        if copied >= max_files:
            logger.warning("[VirtualRuntime] sync cap reached (%d files)", max_files)
            break
    if copied:
        logger.info("[VirtualRuntime] synced %d file(s) from repo into worktree", copied)
    return copied


class VirtualExecutionRuntime:
    """
    Session-scoped isolated execution via git worktree.

    enter() → effective codebase root for worker/planner
    finalize() → commit / optional merge / cleanup on terminal success
    """

    def __init__(
        self,
        repo_root: str,
        session: RuntimeSession,
        store: Optional[RuntimeSessionStore] = None,
    ) -> None:
        self.repo_root = str(Path(repo_root).resolve())
        self.session = session
        self.store = store or RuntimeSessionStore()
        self.wm: Optional[WorktreeManager] = None
        self.feature_id: Optional[str] = None
        self.active = False

    def enter(self) -> str:
        """
        Create or reattach worktree. Returns path to use as codebase_root.
        Falls back to repo_root when disabled or not a git repo.
        """
        if not worktree_enabled():
            return self.repo_root
        if not is_git_repository(self.repo_root):
            logger.info("[VirtualRuntime] not a git repo — in-place execution")
            return self.repo_root

        self.wm = WorktreeManager(self.repo_root)
        self.feature_id = feature_id_for_session(self.session)
        sandbox = self.session.sandbox

        if sandbox.enabled and sandbox.feature_id and sandbox.worktree_path:
            wt_path = Path(sandbox.worktree_path)
            if wt_path.is_dir():
                self.wm.register_worktree(sandbox.feature_id, str(wt_path))
                self.feature_id = sandbox.feature_id
                self.active = True
                logger.info("[VirtualRuntime] reattached worktree %s", wt_path)
                return str(wt_path)
            logger.warning(
                "[VirtualRuntime] stale worktree path %s — recreating",
                sandbox.worktree_path,
            )

        worktree_path = self.wm.create_worktree(self.feature_id)
        sync_worktree_from_repo(self.repo_root, worktree_path)
        branch = self.wm._branch_name(self.feature_id)
        self.session.sandbox = SessionSandbox(
            enabled=True,
            feature_id=self.feature_id,
            worktree_path=worktree_path,
            branch=branch,
        )
        self.store.save(self.session)
        self.active = True
        logger.info("[VirtualRuntime] created worktree %s (branch %s)", worktree_path, branch)
        return worktree_path

    def finalize(self, status: SessionStatus) -> dict[str, Any]:
        """
        On success: commit; merge+cleanup if AWOS_WORKTREE_AUTO_MERGE=true.
        On pause/cancel/fail: keep worktree for resume/review.
        """
        summary: dict[str, Any] = {"active": self.active}
        if not self.active or not self.wm or not self.feature_id:
            return summary

        try:
            if status == SessionStatus.COMPLETED:
                goal_snip = (self.session.goal or "AWOS task")[:72]
                commit_sha = self.wm.commit_worktree(
                    self.feature_id,
                    f"AWOS: {goal_snip}",
                )
                summary["commit"] = commit_sha
                if auto_merge_enabled() and commit_sha:
                    summary["merged"] = self.wm.merge_to_main(self.feature_id)
                    self.wm.cleanup_worktree(self.feature_id)
                    self.session.sandbox = SessionSandbox()
                    self.store.save(self.session)
                    summary["cleaned_up"] = True
                else:
                    summary["cleaned_up"] = False
            else:
                summary["worktree_preserved"] = True
        except Exception as exc:
            logger.warning("[VirtualRuntime] finalize failed: %s", exc)
            summary["error"] = str(exc)

        return summary

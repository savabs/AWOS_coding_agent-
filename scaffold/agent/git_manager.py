"""
GitManager: Safe branch + rollback for Orchestrator task execution.

Strategy:
  - Git repo  → create feature branch, rollback = git checkout original branch
  - No git    → snapshot files to memory, rollback = restore from snapshot

This prevents dirty state when tasks fail mid-execution.
"""

import re
import subprocess
from pathlib import Path
from typing import Optional, Dict


class GitManager:
    """Manages git safety for code changes — branches and rollbacks."""

    def __init__(self, codebase_root: str):
        self.root = Path(codebase_root)
        self.git_root: Optional[Path] = self._find_git_root()
        self.is_git_repo: bool = self.git_root is not None
        self.branch_name: Optional[str] = None
        self.original_branch: Optional[str] = None
        self.file_backups: Dict[str, str] = {}   # path → original content
        self.modified_files: list = []            # files touched this run

    # ── Git helpers ───────────────────────────────────────────────────────────

    def _find_git_root(self) -> Optional[Path]:
        """Walk up to find .git directory."""
        for candidate in [self.root] + list(self.root.parents)[:8]:
            if (candidate / ".git").exists():
                return candidate
        return None

    def _git(self, *args, timeout: int = 15) -> tuple[bool, str]:
        """Run git command in git root. Returns (success, output)."""
        try:
            r = subprocess.run(
                ["git"] + list(args),
                cwd=str(self.git_root or self.root),
                capture_output=True, text=True, timeout=timeout
            )
            return r.returncode == 0, (r.stdout + r.stderr).strip()
        except Exception as e:
            return False, str(e)

    def _current_branch(self) -> str:
        ok, out = self._git("branch", "--show-current")
        return out.strip() if ok and out.strip() else "main"

    # ── Public API ────────────────────────────────────────────────────────────

    def setup(self, goal: str) -> str:
        """
        Prepare for execution: create feature branch (git) or note start state.
        Returns branch name if created, empty string otherwise.
        """
        if not self.is_git_repo:
            return ""

        self.original_branch = self._current_branch()

        # Build a clean branch name from goal
        slug = re.sub(r"[^a-z0-9]+", "-", goal.lower())[:40].strip("-")
        self.branch_name = f"awos/{slug}"

        # If branch already exists from a previous run, delete it first
        self._git("branch", "-D", self.branch_name)

        ok, out = self._git("checkout", "-b", self.branch_name)
        if ok:
            return self.branch_name
        else:
            # Couldn't create branch — fall back to in-memory backups only
            print(f"[GIT] Could not create branch: {out}. Using file backups instead.")
            self.branch_name = None
            return ""

    def backup_file(self, file_path: str):
        """Snapshot a file before modification so it can be restored."""
        p = Path(file_path)
        if p.exists() and file_path not in self.file_backups:
            self.file_backups[file_path] = p.read_text(errors="ignore")

    def record_modified(self, file_path: str):
        """Track which files were actually written during this run."""
        if file_path not in self.modified_files:
            self.modified_files.append(file_path)

    def rollback_file(self, file_path: str):
        """Restore a single file to pre-execution state."""
        if self.is_git_repo and self.branch_name:
            # On the feature branch — reset this file to what the original branch had
            ok, _ = self._git("checkout", self.original_branch, "--", file_path)
            if not ok and file_path in self.file_backups:
                Path(file_path).write_text(self.file_backups[file_path])
        elif file_path in self.file_backups:
            Path(file_path).write_text(self.file_backups[file_path])

    def rollback_all(self):
        """
        Full rollback of everything changed during this run.
        Git: delete feature branch and return to original.
        No-git: restore all snapshots.
        """
        if self.is_git_repo and self.branch_name and self.original_branch:
            print(f"[GIT] Rolling back — returning to '{self.original_branch}'")
            self._git("checkout", "--force", self.original_branch)
            self._git("branch", "-D", self.branch_name)
        else:
            print(f"[GIT] Rolling back {len(self.file_backups)} file(s) from snapshots")
            for path, content in self.file_backups.items():
                try:
                    Path(path).write_text(content)
                except Exception as e:
                    print(f"[GIT] Warning: could not restore {path}: {e}")

    def finalize(self, success: bool):
        """
        Post-execution cleanup.
        Success → keep branch as-is (user can review/merge).
        Failure → full rollback.
        """
        if success:
            if self.is_git_repo and self.branch_name:
                print(f"[GIT] Changes on branch '{self.branch_name}' — review and merge when ready.")
        else:
            self.rollback_all()

    def status(self) -> dict:
        return {
            "is_git_repo": self.is_git_repo,
            "git_root": str(self.git_root) if self.git_root else None,
            "branch_name": self.branch_name,
            "original_branch": self.original_branch,
            "files_backed_up": len(self.file_backups),
            "files_modified": len(self.modified_files),
        }

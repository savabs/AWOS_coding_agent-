"""
GitManager: Safe branch + rollback for Orchestrator task execution.

Strategy:
  - Git repo  → create feature branch, rollback = git checkout original branch
  - No git    → snapshot files to memory, rollback = restore from snapshot

This prevents dirty state when tasks fail mid-execution.

A failed task rolls a file back to the state the last successful task left it
in, not to before the run: several tasks (and the goal check's follow-ups,
which target exactly the files the run already changed) can edit one file,
and one failure must not erase the earlier tasks' kept work.

AWOS_DEFER_ROLLBACK=<path> (set by the host worker, see
docs/specs/supervision_autopilot.md): snapshot restores are recorded to that
JSON file instead of applied, so the host can keep a stopped attempt's edits
for an auto-continue, or apply the recorded rollback when it does not continue.
"""

import base64
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional, Dict


def record_restore(deferred_path: str, file_path: str, content) -> None:
    """Append one pending restore (last record per file wins when applied)."""
    if isinstance(content, str):
        content = content.encode()
    rec = {"path": os.path.abspath(file_path),
           "b64": None if content is None else base64.b64encode(content).decode()}
    with open(deferred_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def apply_deferred(deferred_path: str) -> int:
    """Apply the restores recorded under AWOS_DEFER_ROLLBACK; returns files touched."""
    try:
        lines = Path(deferred_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    final: Dict[str, Optional[str]] = {}
    for line in lines:
        try:
            rec = json.loads(line)
            final[rec["path"]] = rec["b64"]
        except (ValueError, KeyError, TypeError):
            continue
    for path, b64 in final.items():
        try:
            if b64 is None:
                Path(path).unlink(missing_ok=True)
            else:
                Path(path).write_bytes(base64.b64decode(b64))
        except OSError:
            pass
    Path(deferred_path).unlink(missing_ok=True)
    return len(final)


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
        # abs path → content a successful task kept (None: it deleted the file)
        self.kept: Dict[str, Optional[bytes]] = {}

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
        # The M1 host runs jobs in a throwaway worktree and builds the review
        # branch itself; branching here would create or delete refs in the
        # user's repo. File backups still guard rollbacks.
        if os.getenv("AWOS_GIT_BRANCH", "1").strip().lower() in ("0", "false", "no"):
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
        """Track a file a successful task changed, and snapshot it as kept:
        a later task that fails on this file rolls back to this content."""
        if file_path not in self.modified_files:
            self.modified_files.append(file_path)
        p = Path(file_path)
        self.kept[os.path.abspath(file_path)] = p.read_bytes() if p.exists() else None

    def _restore(self, file_path: str, content) -> None:
        """Put a file back (bytes/str) or delete it (None) — or record it when deferred."""
        deferred = os.getenv("AWOS_DEFER_ROLLBACK", "").strip()
        if deferred:
            record_restore(deferred, file_path, content)
            return
        if content is None:
            Path(file_path).unlink(missing_ok=True)
        elif isinstance(content, bytes):
            Path(file_path).write_bytes(content)
        else:
            Path(file_path).write_text(content)

    def rollback_file(self, file_path: str):
        """Undo a failed task's change to one file: back to what the last
        successful task kept, or to the pre-execution state if none did."""
        key = os.path.abspath(file_path)
        if key in self.kept:
            self._restore(file_path, self.kept[key])
            return
        if self.is_git_repo and self.branch_name:
            # On the feature branch — reset this file to what the original branch had
            ok, _ = self._git("checkout", self.original_branch, "--", file_path)
            if not ok and file_path in self.file_backups:
                self._restore(file_path, self.file_backups[file_path])
        elif file_path in self.file_backups:
            self._restore(file_path, self.file_backups[file_path])

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
            deferred = " (deferred to the host: AWOS_DEFER_ROLLBACK)" if os.getenv("AWOS_DEFER_ROLLBACK", "").strip() else ""
            print(f"[GIT] Rolling back {len(self.file_backups)} file(s) from snapshots{deferred}")
            for path, content in self.file_backups.items():
                try:
                    self._restore(path, content)
                except Exception as e:
                    print(f"[GIT] Warning: could not restore {path}: {e}")

    def finalize(self, success: bool, force_full_rollback: bool = False):
        """
        Post-execution cleanup.
        Success          → keep branch as-is (user can review/merge).
        Partial failure  → keep branch; individual failed files already reverted.
        Total failure    → force_full_rollback=True triggers full rollback.
        """
        if success:
            if self.is_git_repo and self.branch_name:
                print(f"[GIT] Changes on branch '{self.branch_name}' — review and merge when ready.")
        elif force_full_rollback:
            self.rollback_all()
        else:
            if self.is_git_repo and self.branch_name:
                print(f"[GIT] Partial success — changes on branch '{self.branch_name}', failed files were reverted individually.")
            else:
                print(f"[GIT] Partial success — {len(self.modified_files)} file(s) modified, failed files were reverted.")

    def status(self) -> dict:
        return {
            "is_git_repo": self.is_git_repo,
            "git_root": str(self.git_root) if self.git_root else None,
            "branch_name": self.branch_name,
            "original_branch": self.original_branch,
            "files_backed_up": len(self.file_backups),
            "files_modified": len(self.modified_files),
        }

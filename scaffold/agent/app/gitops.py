"""
gitops.py — the few git operations the app performs on the user's repo.

Nothing here runs without an explicit user action in the app: merge only on
[m] / /merge, delete only on [r] / /reject after a confirmation. Nothing is
ever pushed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def git(repo: Path | str, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=check)


def repo_root(start: Path | str) -> Optional[Path]:
    out = git(start, "rev-parse", "--show-toplevel")
    return Path(out.stdout.strip()).resolve() if out.returncode == 0 and out.stdout.strip() else None


def current_branch(repo: Path) -> Optional[str]:
    out = git(repo, "symbolic-ref", "--short", "-q", "HEAD")
    return out.stdout.strip() or None


def branch_exists(repo: Path, branch: str) -> bool:
    return git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0


def is_merged(repo: Path, branch: str) -> bool:
    """The branch's tip is already reachable from HEAD."""
    return git(repo, "merge-base", "--is-ancestor", branch, "HEAD").returncode == 0


def changed_files(repo: Path, branch: str) -> list[str]:
    """Files the branch changes relative to where it forked from HEAD."""
    out = git(repo, "diff", "--name-only", f"HEAD...{branch}")
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def dirty_files(repo: Path) -> list[str]:
    """Uncommitted (staged, unstaged or untracked) paths in the user's checkout."""
    out = git(repo, "status", "--porcelain", "-z", "--untracked-files=all")
    paths, entries = [], out.stdout.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path)
        if "R" in status or "C" in status:   # rename/copy: next field is the source
            if i < len(entries):
                paths.append(entries[i])
            i += 1
    return paths


def diff_text(repo: Path, branch: str, color: bool = True) -> str:
    """The branch's changes since it forked from the current HEAD (stat + patch)."""
    flag = "--color=always" if color else "--no-color"
    stat = git(repo, "--no-pager", "diff", flag, "--stat", f"HEAD...{branch}").stdout
    patch = git(repo, "--no-pager", "diff", flag, f"HEAD...{branch}").stdout
    return (stat + "\n" + patch) if stat.strip() else patch


@dataclass
class MergeResult:
    ok: bool
    message: str
    conflicts: list[str]


def merge_branch(repo: Path, branch: str, goal: str = "") -> MergeResult:
    """
    `git merge --no-ff <branch>` into the user's current branch. Refuses when
    uncommitted changes touch files the branch changes, and aborts cleanly if
    git reports conflicts, so the user's tree is never left half-merged.
    """
    if not branch_exists(repo, branch):
        return MergeResult(False, f"branch {branch} does not exist (already merged or rejected?)", [])
    cur = current_branch(repo)
    if cur is None:
        return MergeResult(False, "your checkout is on a detached HEAD; check out a branch first", [])
    if git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
        return MergeResult(False, "a merge is already in progress in your checkout; finish or abort it first", [])
    if is_merged(repo, branch):
        return MergeResult(True, f"{branch} is already part of {cur}; nothing to do", [])
    overlap = sorted(set(dirty_files(repo)) & set(changed_files(repo, branch)))
    if overlap:
        return MergeResult(
            False,
            "you have uncommitted changes in files this result also changes ("
            + ", ".join(overlap) + "). Commit or stash them, then merge again.",
            overlap)
    from scaffold.agent.host.handoff import commit_subject
    title = commit_subject(goal, limit=60) if goal.strip() else ""
    msg = f"Merge {branch}" + (f": {title}" if title else "")
    out = git(repo, "merge", "--no-ff", "--no-edit", "-m", msg, branch)
    if out.returncode != 0:
        conflicts = [ln for ln in git(repo, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
                     if ln.strip()]
        git(repo, "merge", "--abort")
        if conflicts:
            detail = "conflicts in " + ", ".join(conflicts)
        else:
            lines = (out.stderr.strip() or out.stdout.strip()).splitlines()
            detail = lines[-1] if lines else "unknown error"
        return MergeResult(False, f"git could not merge cleanly ({detail}); nothing was changed", conflicts)
    head = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    return MergeResult(True, f"merged {branch} into {cur} ({head})", [])


def delete_branch(repo: Path, branch: str) -> tuple[bool, str]:
    if not branch_exists(repo, branch):
        return False, f"branch {branch} does not exist"
    if current_branch(repo) == branch:
        return False, f"you are on {branch}; switch to another branch first"
    out = git(repo, "branch", "-D", branch)
    if out.returncode != 0:
        return False, out.stderr.strip() or "git branch -D failed"
    return True, f"deleted {branch}"

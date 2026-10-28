"""
gitops.py — the few git operations the app performs on the user's repo.

Nothing here runs without an explicit user action in the app: merge only on
[m] / /merge, delete only on [r] / /reject after a confirmation. Nothing is
ever pushed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


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
    #: paths the merge changes (or would change) in the user's branch
    applied: list[str] = field(default_factory=list)
    #: paths the branch changes that the user's branch already has as-is
    skipped: list[str] = field(default_factory=list)
    #: a step failed after the checkout changed and it was put back
    rolled_back: bool = False


@dataclass
class MergePlan:
    """A 3-way merge computed with `git merge-tree`, without touching the checkout."""
    tree: str
    applied: list[str]
    skipped: list[str]
    conflicts: list[str]


def _names(out: str) -> list[str]:
    return [p for p in out.split("\0") if p]


def plan_merge(repo: Path, branch: str) -> Optional[MergePlan]:
    """
    Dry-run the merge of `branch` into HEAD (git >= 2.38 `merge-tree --write-tree`):
    which paths would apply, which are already the same (skipped) and which
    would conflict. Writes objects only, never refs, index or files. None when
    this git cannot do it.
    """
    out = git(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", "-z",
              "HEAD", branch)
    if out.returncode not in (0, 1) or not out.stdout:
        return None
    parts = _names(out.stdout)
    tree, conflicts = parts[0], sorted(set(parts[1:]))
    touched = _names(git(repo, "diff", "--name-only", "-z", "--no-renames", "HEAD", tree).stdout)
    applied = sorted(set(touched) - set(conflicts))
    branch_side = _names(git(repo, "diff", "--name-only", "-z", "--no-renames",
                             f"HEAD...{branch}").stdout)
    skipped = sorted(set(branch_side) - set(applied) - set(conflicts))
    return MergePlan(tree, applied, skipped, conflicts)


def _head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def _roll_back(repo: Path, pre: str) -> bool:
    """Put the checkout back to `pre` after a failed step; keeps unrelated local edits."""
    if git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
        git(repo, "merge", "--abort")
    if _head(repo) != pre:
        git(repo, "reset", "--merge", pre)
    return _head(repo) == pre


def merge_branch(repo: Path, branch: str, goal: str = "",
                 on_merged: Optional[Callable[[str], None]] = None) -> MergeResult:
    """
    `git merge --no-ff <branch>` into the user's current branch, as a transaction:

    1. plan (git merge-tree, nothing changes): applied / skipped / conflicted
       paths. Conflicts -> refused, the checkout untouched, paths reported.
    2. refuse when uncommitted changes touch a path the merge would change.
    3. merge; on any failure abort and verify HEAD is back where it was.
    4. `on_merged(new_head)` (e.g. record the review); if it raises, the merge is
       undone (`git reset --merge` to the old HEAD) and the result says so.
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
    plan = plan_merge(repo, branch)
    applied = plan.applied if plan else []
    skipped = plan.skipped if plan else []
    if plan and plan.conflicts:
        return MergeResult(
            False, f"git could not merge cleanly (conflicts in {', '.join(plan.conflicts)}); "
                   "nothing was changed", plan.conflicts, applied, skipped)
    touched = set(plan.applied) if plan else set(changed_files(repo, branch))
    overlap = sorted(set(dirty_files(repo)) & touched)
    if overlap:
        return MergeResult(
            False,
            "you have uncommitted changes in files this result also changes ("
            + ", ".join(overlap) + "). Commit or stash them, then merge again.",
            overlap, applied, skipped)
    from scaffold.agent.host.handoff import commit_subject
    title = commit_subject(goal, limit=60) if goal.strip() else ""
    msg = f"Merge {branch}" + (f": {title}" if title else "")
    pre = _head(repo)
    out = git(repo, "merge", "--no-ff", "--no-edit", "-m", msg, branch)
    if out.returncode != 0:
        conflicts = [ln for ln in git(repo, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
                     if ln.strip()]
        restored = _roll_back(repo, pre)
        if conflicts:
            detail = "conflicts in " + ", ".join(conflicts)
        else:
            lines = (out.stderr.strip() or out.stdout.strip()).splitlines()
            detail = lines[-1] if lines else "unknown error"
        tail = "nothing was changed" if restored else "could not restore your checkout; check `git status`"
        return MergeResult(False, f"git could not merge cleanly ({detail}); {tail}", conflicts,
                           applied, skipped, rolled_back=restored)
    head = _head(repo)
    if on_merged is not None:
        try:
            on_merged(head)
        except Exception as exc:
            restored = _roll_back(repo, pre)
            tail = "the merge was undone" if restored else "could not undo the merge; check `git log`"
            return MergeResult(False, f"merged, but a follow-up step failed ({exc}); {tail}", [],
                               applied, skipped, rolled_back=restored)
    if not plan:   # older git: report what the merge commit changed
        applied = _names(git(repo, "diff", "--name-only", "-z", "--no-renames", pre, head).stdout)
    return MergeResult(True, f"merged {branch} into {cur} ({head[:7]})", [], applied, skipped)


def delete_branch(repo: Path, branch: str) -> tuple[bool, str]:
    if not branch_exists(repo, branch):
        return False, f"branch {branch} does not exist"
    if current_branch(repo) == branch:
        return False, f"you are on {branch}; switch to another branch first"
    out = git(repo, "branch", "-D", branch)
    if out.returncode != 0:
        return False, out.stderr.strip() or "git branch -D failed"
    return True, f"deleted {branch}"

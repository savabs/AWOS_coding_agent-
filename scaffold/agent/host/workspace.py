"""
workspace.py — lifecycle of the host's job worktrees (D17, modelled on the Codex app).

The worker runs every job in a detached git worktree under <job_dir>/workspace
(plus an untouched <job_dir>/baseline); see worker.prepare_workspace. This
module adds what keeps those worktrees safe and bounded:

1. **Owner record** per worktree, next to it (``<job_dir>/workspace.owner.json``):
   job id, repo, the owning worker's pid / process start / host, created and
   last-used times, base commit, state ("active" | "finished").
2. **Cap**: at most ``AWOS_MAX_WORKTREES`` (default 20) job worktrees are kept.
   Beyond that the least recently used ones are pruned, but only when the job is
   finished and no live worker holds the worktree. A live one is never pruned.
3. **Snapshot before delete**: before any worktree is removed its full state
   (commits on its detached HEAD, staged, unstaged and untracked files, minus
   what .gitignore excludes) is committed to a hidden ref
   ``refs/awos/snapshots/<job>/<UTC timestamp>`` in the user's repo, without
   touching the worktree's own index or HEAD. A worktree whose snapshot fails is
   kept. A clean worktree still at its base commit needs no snapshot.

Spec: docs/specs/host_workspace.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .models import now_iso

WORKSPACE_DIR = "workspace"
BASELINE_DIR = "baseline"
OWNER_SUFFIX = ".owner.json"
SNAPSHOT_PREFIX = "refs/awos/snapshots"
DEFAULT_MAX_WORKTREES = 20
ACTIVE, FINISHED = "active", "finished"
#: Never part of a snapshot even when .gitignore misses them (handoff excludes them too).
_SNAPSHOT_EXCLUDES = (":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc",
                      ":(exclude,glob)**/.pytest_cache/**")

#: is_final(job_id) -> True (done/failed/cancelled), False (not final), None (unknown job).
IsFinal = Callable[[str], Optional[bool]]


def max_worktrees() -> int:
    """AWOS_MAX_WORKTREES (default 20); 0 keeps no finished worktree at all."""
    try:
        return max(0, int(os.getenv("AWOS_MAX_WORKTREES", str(DEFAULT_MAX_WORKTREES))))
    except ValueError:
        return DEFAULT_MAX_WORKTREES


# ── git ──────────────────────────────────────────────────────────────────────


def _git(args: List[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
         check: bool = False) -> subprocess.CompletedProcess:
    from .handoff import _git_env   # scrubbed GIT_* env, hooks/fsmonitor off
    proc = subprocess.run(["git", *args], cwd=cwd, env=_git_env(env), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip()[:300]}")
    return proc


def _is_worktree(path: Path) -> bool:
    return (path / ".git").exists()


# ── owner record ─────────────────────────────────────────────────────────────


def owner_path(worktree: Path | str) -> Path:
    wt = Path(worktree)
    return wt.with_name(wt.name + OWNER_SUFFIX)


def read_owner(worktree: Path | str) -> Optional[dict]:
    try:
        data = json.loads(owner_path(worktree).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_owner(worktree: Path | str, record: dict) -> dict:
    path = owner_path(worktree)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return record


def _me() -> dict:
    from scaffold.agent.job_host import process_start
    pid = os.getpid()
    return {"owner_pid": pid, "owner_started": process_start(pid), "owner_host": socket.gethostname()}


def claim(job_id: str, repo_path: str, worktree: Path | str, base_commit: Optional[str],
          role: str = WORKSPACE_DIR) -> dict:
    """This worker owns `worktree` from now on (a new worktree, or a retry reusing one)."""
    rec = read_owner(worktree) or {"created_at": now_iso()}
    rec.update({"job_id": job_id, "repo_path": str(repo_path), "path": str(worktree),
                "role": role, "base_commit": base_commit or rec.get("base_commit"),
                "state": ACTIVE, "last_used_at": now_iso(), "last_used_ts": time.time(), **_me()})
    rec.pop("released_at", None)
    return write_owner(worktree, rec)


def release(worktree: Path | str, job_id: Optional[str] = None) -> Optional[dict]:
    """The job is final: the worktree no longer belongs to a running worker."""
    rec = read_owner(worktree)
    if rec is None:
        if not Path(worktree).is_dir():
            return None
        rec = {"created_at": now_iso(), "path": str(worktree), "job_id": job_id}
    rec.update({"state": FINISHED, "released_at": now_iso(), "last_used_at": now_iso(),
                "last_used_ts": time.time()})
    return write_owner(worktree, rec)


def owner_alive(rec: Optional[dict]) -> Optional[bool]:
    """Is the worker that holds the worktree still running? None = cannot tell (keep it)."""
    if not rec or not rec.get("owner_pid"):
        return False
    if rec.get("owner_host") and rec["owner_host"] != socket.gethostname():
        return None   # another machine's worker: no way to check, so never prune it
    from scaffold.agent.job_host import process_matches
    return process_matches(rec.get("owner_pid"), rec.get("owner_started"))


# ── snapshot ─────────────────────────────────────────────────────────────────


def _ref_part(text: str) -> str:
    part = re.sub(r"[^A-Za-z0-9_.-]", "_", text or "unknown").strip(".") or "unknown"
    return part.replace("..", "_")[:80]


def snapshot(worktree: Path | str, job_id: str, base_commit: Optional[str] = None,
             reason: str = "remove") -> dict:
    """
    Commit the worktree's whole state to refs/awos/snapshots/<job>/<ts>.

    Uses a throwaway copy of the worktree's index, so the worktree (its index,
    HEAD and files) is left exactly as it was. Returns {"ref", "commit"} or
    {"clean": True} when nothing could be lost (clean and still at base).
    Raises RuntimeError when the state cannot be saved.
    """
    wt = str(worktree)
    head = _git(["rev-parse", "--verify", "HEAD"], cwd=wt, check=True).stdout.strip()
    index = _git(["rev-parse", "--path-format=absolute", "--git-path", "index"], cwd=wt,
                 check=True).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="awos-snap-") as tmp:
        tmp_index = os.path.join(tmp, "index")
        if os.path.exists(index):
            shutil.copyfile(index, tmp_index)   # keeps the stat cache: no full re-hash
        env = {"GIT_INDEX_FILE": tmp_index}
        if not os.path.exists(index):
            _git(["read-tree", head], cwd=wt, env=env, check=True)
        _git(["add", "-A", "--", ".", *_SNAPSHOT_EXCLUDES], cwd=wt, env=env, check=True)
        tree = _git(["write-tree"], cwd=wt, env=env, check=True).stdout.strip()
    head_tree = _git(["rev-parse", f"{head}^{{tree}}"], cwd=wt, check=True).stdout.strip()
    if tree == head_tree and base_commit and head == base_commit:
        return {"clean": True, "commit": head}
    from .handoff import _GIT_IDENTITY
    msg = (f"awos snapshot: job {job_id} ({reason})\n\nworktree: {wt}\nhead: {head}\n"
           f"base: {base_commit or 'unknown'}\n")
    commit = _git(["commit-tree", tree, "-p", head, "-m", msg], cwd=wt, env=dict(_GIT_IDENTITY),
                  check=True).stdout.strip()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    base_ref = f"{SNAPSHOT_PREFIX}/{_ref_part(job_id)}/{stamp}"
    ref, n = base_ref, 1
    while _git(["rev-parse", "--verify", "--quiet", ref], cwd=wt).returncode == 0:
        n += 1
        ref = f"{base_ref}-{n}"
    # "" as old value: create only, never overwrite an existing snapshot.
    _git(["update-ref", "-m", "awos snapshot", ref, commit, ""], cwd=wt, check=True)
    return {"ref": ref, "commit": commit}


def list_snapshots(repo: Path | str, job_id: Optional[str] = None) -> List[dict]:
    """Snapshot refs in `repo`, oldest first: [{"ref", "commit", "job_id"}]."""
    prefix = f"{SNAPSHOT_PREFIX}/{_ref_part(job_id)}/" if job_id else f"{SNAPSHOT_PREFIX}/"
    out = _git(["for-each-ref", "--sort=refname", "--format=%(refname) %(objectname)", prefix],
               cwd=str(repo)).stdout
    snaps = []
    for line in out.splitlines():
        ref, _, sha = line.partition(" ")
        parts = ref[len(SNAPSHOT_PREFIX) + 1:].split("/")
        snaps.append({"ref": ref, "commit": sha, "job_id": parts[0] if parts else ""})
    return snaps


def restore_snapshot(repo: Path | str, ref: str, dest: Path | str) -> str:
    """Check a snapshot out as a new detached worktree at `dest` (its state is in one commit)."""
    if not ref.startswith(SNAPSHOT_PREFIX + "/"):
        raise ValueError(f"not an awos snapshot ref: {ref}")
    _git(["worktree", "add", "--detach", str(dest), ref], cwd=str(repo), check=True)
    return str(dest)


# ── removal ──────────────────────────────────────────────────────────────────


def _repo_for(worktree: Path, repo_path: Optional[str]) -> Optional[str]:
    if repo_path and Path(repo_path).exists():
        return repo_path
    common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=str(worktree))
    if common.returncode != 0:
        return None
    return common.stdout.strip() or None


def remove_worktree(worktree: Path | str, job_id: str, repo_path: Optional[str] = None,
                    base_commit: Optional[str] = None, reason: str = "remove") -> dict:
    """
    Snapshot, then remove one worktree and its owner record.

    Returns {"removed": bool, "snapshot": {...} | None, "error": str | None}.
    A worktree whose snapshot fails is kept (removed False, error set).
    """
    wt = Path(worktree)
    rec = read_owner(wt) or {}
    base_commit = base_commit or rec.get("base_commit")
    if not wt.exists():
        owner_path(wt).unlink(missing_ok=True)
        return {"removed": False, "snapshot": None, "error": None}
    snap = None
    if _is_worktree(wt):
        try:
            snap = snapshot(wt, job_id, base_commit, reason=reason)
        except Exception as exc:
            return {"removed": False, "snapshot": None,
                    "error": f"snapshot failed, worktree kept: {exc}"}
    repo = _repo_for(wt, repo_path or rec.get("repo_path"))
    rm = _git(["worktree", "remove", "--force", str(wt)], cwd=repo) if repo else None
    if rm is None or rm.returncode != 0:
        shutil.rmtree(wt, ignore_errors=True)
        if repo:
            _git(["worktree", "prune"], cwd=repo)
    owner_path(wt).unlink(missing_ok=True)
    return {"removed": not wt.exists(), "snapshot": snap, "error": None}


# ── inventory + cap ──────────────────────────────────────────────────────────


def list_worktrees(jobs_dir: Path | str) -> List[dict]:
    """Every job worktree under <root>/jobs/*/workspace, with its owner record (or a stand-in)."""
    out = []
    root = Path(jobs_dir)
    if not root.is_dir():
        return out
    for job_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        wt = job_dir / WORKSPACE_DIR
        if not wt.is_dir():
            continue
        rec = read_owner(wt)
        legacy = rec is None
        if legacy:   # a worktree made before owner records: its mtime stands in for last use
            try:
                ts = wt.stat().st_mtime
            except OSError:
                ts = 0.0
            rec = {"job_id": job_dir.name, "path": str(wt), "last_used_ts": ts, "state": None}
        out.append({"job_id": rec.get("job_id") or job_dir.name, "job_dir": str(job_dir),
                    "path": str(wt), "record": rec, "legacy": legacy})
    return out


def protected_reason(entry: dict, is_final: Optional[IsFinal] = None) -> Optional[str]:
    """Why a worktree must not be pruned, or None when it may go."""
    rec = entry.get("record") or {}
    final = None
    if is_final is not None:
        try:
            final = is_final(entry["job_id"])
        except Exception:
            final = None
    if final is False:
        return "job not finished"
    if final is None and rec.get("state") != FINISHED:
        return "job state unknown"
    if rec.get("state") != FINISHED:
        alive = owner_alive(rec)
        if alive is not False:
            return "owner alive" if alive else "owner unknown"
    return None


def prune_worktrees(jobs_dir: Path | str, keep: Optional[int] = None,
                    is_final: Optional[IsFinal] = None,
                    log: Optional[Callable[[str], None]] = None) -> List[dict]:
    """
    Keep at most `keep` (default AWOS_MAX_WORKTREES) job worktrees: remove the
    least recently used ones that may go (protected_reason is None), each with
    its baseline, snapshotting first. Live worktrees stay even above the cap.
    Returns one {"job_id", "path", "removed", "snapshot", "error"} per attempt.
    """
    keep = max_worktrees() if keep is None else max(0, keep)
    entries = list_worktrees(jobs_dir)
    excess = len(entries) - keep
    if excess <= 0:
        return []
    entries.sort(key=lambda e: float(e["record"].get("last_used_ts") or 0.0))
    pruned: List[dict] = []
    for entry in entries:
        if excess <= 0:
            break
        if protected_reason(entry, is_final):
            continue
        rec, job_dir = entry["record"], Path(entry["job_dir"])
        res = remove_worktree(entry["path"], entry["job_id"], rec.get("repo_path"),
                              rec.get("base_commit"), reason="prune")
        if res["removed"]:
            remove_worktree(job_dir / BASELINE_DIR, entry["job_id"], rec.get("repo_path"),
                            rec.get("base_commit"), reason="prune")
            excess -= 1
        pruned.append({"job_id": entry["job_id"], "path": entry["path"], **res})
        if log:
            snap = (res.get("snapshot") or {}).get("ref") or "clean, no snapshot"
            log(f"[workspace] pruned {entry['job_id'][:8]} ({snap})" if res["removed"]
                else f"[workspace] kept {entry['job_id'][:8]}: {res.get('error')}")
    return pruned


def retire(job_id: str, repo_path: str, job_dir: Path | str,
           is_final: Optional[IsFinal] = None,
           log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """
    The job is final: drop its baseline (snapshotted if it was touched), release
    its workspace (kept for inspection) and enforce the cap over all job
    worktrees under the same storage root.
    """
    job_dir = Path(job_dir)
    base = None
    bf = job_dir / "base_commit"
    if bf.exists():
        base = bf.read_text(encoding="utf-8").strip() or None
    baseline = remove_worktree(job_dir / BASELINE_DIR, job_id, repo_path, base, reason="retire")
    release(job_dir / WORKSPACE_DIR, job_id)

    def final(jid: str) -> Optional[bool]:   # the queue marks this job final just after
        if jid == job_id:
            return True
        return is_final(jid) if is_final is not None else None

    pruned = prune_worktrees(job_dir.parent, is_final=final, log=log)
    return {"baseline": baseline, "pruned": pruned}

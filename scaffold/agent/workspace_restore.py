"""
workspace_restore.py — undo a failed one-shot attempt before the agent loop.

When the one-shot attempt fails and AWOS falls back to the agent loop, the
loop should start from the state the attempt started from, not build on its
broken edits. Before the attempt we snapshot the job workspace's working tree
into a git tree object (through a throw-away index: HEAD, the real index and
the files are untouched); after a failed attempt we put back exactly the files
the attempt changed — tracked or untracked, including uncommitted work from
earlier tasks of the same job — and delete files it created.

Safety: this only ever runs in the host job's own workspace (the worker
exports AWOS_JOB_WORKSPACE, a detached worktree under the job dir), never in
the user's checkout. AWOS_ONE_SHOT_RESTORE=0 turns it off.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ENV_WORKSPACE = "AWOS_JOB_WORKSPACE"
ENV_SWITCH = "AWOS_ONE_SHOT_RESTORE"


def restore_enabled() -> bool:
    return os.getenv(ENV_SWITCH, "1").strip().lower() not in ("0", "false", "no", "off")


def _git(root: str, *args: str, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                          timeout=60, env=env)


def is_job_workspace(root: str) -> bool:
    """True only for the host job's own workspace (never the user's checkout)."""
    ws = os.getenv(ENV_WORKSPACE, "").strip()
    if not ws or not root:
        return False
    try:
        if Path(ws).resolve() != Path(root).resolve():
            return False
    except OSError:
        return False
    top = _git(root, "rev-parse", "--show-toplevel")
    return top.returncode == 0 and Path(top.stdout.strip()).resolve() == Path(root).resolve()


def snapshot(root: str) -> Optional[str]:
    """Tree id of the whole working tree (gitignored files excluded), or None."""
    fd, index = tempfile.mkstemp(prefix="awos-snap-", suffix=".index")
    os.close(fd)
    os.unlink(index)   # git wants to create it (or we seed it below)
    env = {**os.environ, "GIT_INDEX_FILE": index}
    try:
        # Seed with a copy of the real index: its stat cache spares re-hashing
        # unchanged files. The real index itself is never written.
        real = _git(root, "rev-parse", "--path-format=absolute", "--git-path", "index")
        if real.returncode == 0 and Path(real.stdout.strip()).is_file():
            Path(index).write_bytes(Path(real.stdout.strip()).read_bytes())
        add = _git(root, "add", "-A", "--", ".", env=env)
        if add.returncode != 0:
            logger.warning("[ONE-SHOT] workspace snapshot failed: %s", add.stderr.strip()[:200])
            return None
        tree = _git(root, "write-tree", env=env)
        if tree.returncode != 0:
            return None
        return tree.stdout.strip() or None
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("[ONE-SHOT] workspace snapshot failed: %s", exc)
        return None
    finally:
        try:
            os.unlink(index)
        except OSError:
            pass


def restore(root: str, tree: str, paths: list[str]) -> dict:
    """
    Put each of `paths` (relative to root) back as it was in `tree`: rewrite
    the ones that existed, delete the ones the attempt created. Gitignored
    paths are skipped (the snapshot cannot vouch for them). Returns
    {"restored": [...], "removed": [...], "skipped": [...]}.
    """
    out: dict = {"restored": [], "removed": [], "skipped": []}
    rootp = Path(root).resolve()
    for rel in dict.fromkeys(paths):   # unique, in order
        target = (rootp / rel).resolve()
        if rootp not in target.parents:
            out["skipped"].append(rel)
            continue
        relp = target.relative_to(rootp).as_posix()
        if _git(root, "cat-file", "-e", f"{tree}:{relp}").returncode == 0:
            blob = subprocess.run(["git", "-C", root, "cat-file", "blob", f"{tree}:{relp}"],
                                  capture_output=True, timeout=60)
            mode = _git(root, "ls-tree", tree, "--", relp).stdout.split(" ", 1)[0]
            if blob.returncode != 0:
                out["skipped"].append(rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob.stdout)
            if mode == "100755":
                target.chmod(target.stat().st_mode | 0o111)
            out["restored"].append(relp)
        elif _git(root, "check-ignore", "-q", "--", relp).returncode == 0:
            out["skipped"].append(relp)
        else:
            try:
                target.unlink()
                out["removed"].append(relp)
            except FileNotFoundError:
                pass
            except OSError:
                out["skipped"].append(relp)
    return out

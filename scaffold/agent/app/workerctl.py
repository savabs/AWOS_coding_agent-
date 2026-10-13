"""
workerctl.py — find the background host worker, or start exactly one.

The worker holds an exclusive flock on <host_root>/worker.lock for as long as
it serves (the OS drops it even on kill -9), so "is a worker running?" is
"can I take that lock?". Starting one goes through a second lock
(<host_root>/app_spawn.lock) so two app windows opened at the same moment do
not both spawn; and even if they did, the second worker would fail to take
worker.lock and exit, so two workers never serve the same queue.
"""

from __future__ import annotations

import fcntl
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

SPAWN_LOCK = "app_spawn.lock"
DAEMON_LOG = "worker_daemon.log"


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _looks_like_worker(pid: int) -> bool:
    """The live pid is a `serve --worker` process (guards against pid reuse)."""
    try:
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return True   # cannot tell: assume it is, never risk a second worker
    return "--worker" in cmd or "serve" in cmd


def _lock_held(path: Path) -> bool:
    try:
        handle = open(path, "a+")
    except OSError:
        return False
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True   # someone else holds it: a live worker
    else:
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def worker_running(root: Path) -> bool:
    """
    True while a worker serves `root`. The worker writes its pid into the lock
    file once it holds the lock, so a live worker pid answers without touching
    the lock (a probe that briefly takes the lock could make a worker that is
    starting at that instant give up). Only without a live pid do we probe.
    """
    path = Path(root) / "worker.lock"
    if not path.exists():
        return False
    pid = worker_pid(root)
    if _pid_alive(pid) and _looks_like_worker(pid):
        return True
    return _lock_held(path)


def worker_pid(root: Path) -> Optional[int]:
    try:
        return int((Path(root) / "worker.lock").read_text().strip() or 0) or None
    except (OSError, ValueError):
        return None


def heartbeat_age(root: Path) -> Optional[float]:
    """Seconds since the worker's last heartbeat, or None if there is none."""
    try:
        stamp = float((Path(root) / "heartbeat").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return max(0.0, time.time() - stamp)


def awos_entry() -> list[str]:
    """argv prefix that runs the `awos` CLI with this interpreter."""
    spec = importlib.util.find_spec("awos")
    origin = spec.origin if spec and spec.origin else None
    if origin and origin.endswith(".py"):
        return [sys.executable, origin]
    from scaffold.agent import paths
    return [sys.executable, str(paths.CODE_ROOT / "awos.py")]


def worker_command() -> list[str]:
    """`awos serve --worker` (AWOS_APP_WORKER_ARGS appends extra flags, e.g. for tests)."""
    extra = os.getenv("AWOS_APP_WORKER_ARGS", "").split()
    return awos_entry() + ["serve", "--worker"] + extra


def ensure_worker(root: Path, spawn: Optional[Callable[[Sequence[str], Path], object]] = None,
                  wait_sec: float = 5.0) -> str:
    """
    Make sure one worker serves `root`. Returns "running" (one already was),
    "started" (we spawned it and it took the lock), "spawned" (spawned, not
    yet holding the lock when we stopped waiting) or "failed" (it exited at
    once; see worker_daemon.log). Never spawns a second one.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if worker_running(root):
        return "running"
    with open(root / SPAWN_LOCK, "a+") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)   # serialise app instances
        try:
            if worker_running(root):        # another app won the race
                return "running"
            proc = (spawn or _spawn_detached)(worker_command(), root)
            spawned_pid = getattr(proc, "pid", None)
            deadline = time.monotonic() + wait_sec
            while time.monotonic() < deadline:
                # pid only: never probe the lock while our worker may be taking it
                pid = worker_pid(root)
                if pid and _pid_alive(pid) and (spawned_pid is None or pid == spawned_pid):
                    return "started"
                if spawned_pid and getattr(proc, "poll", lambda: None)() is not None:
                    return "failed"
                time.sleep(0.1)
            return "spawned"
        finally:
            fcntl.flock(guard, fcntl.LOCK_UN)


def _spawn_detached(cmd: Sequence[str], root: Path) -> subprocess.Popen:
    """Start the worker in its own session: it survives closing the terminal."""
    env = {**os.environ, "AWOS_HOST_DIR": str(root)}
    log = open(root / DAEMON_LOG, "a", encoding="utf-8")
    log.write(f"\n===== worker started by awos app at {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    log.flush()
    from scaffold.agent import paths
    try:
        return subprocess.Popen(list(cmd), stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, env=env, cwd=str(paths.CODE_ROOT),
                                start_new_session=True, close_fds=True)
    finally:
        log.close()

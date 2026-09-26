"""
sandbox.py — Where the agent's code runs: the agent's own computer.

The tools so far (read, search, edit, run_tests) let the agent change code but
never run it, so any job whose answer is an *output* — a CSV report, a timing
measured before and after an optimisation — was impossible. Running arbitrary
model-written code on the owner's laptop is only acceptable if it cannot touch
anything but the task: no writes outside the workspace, no reading the owner's
secrets (~/.ssh, ~/.aws, the repo's .env), no network, no API keys in its
environment. This module is that boundary.

Two backends, one interface:

- SeatbeltSandbox: macOS's built-in sandbox-exec with a generated SBPL profile.
  Zero install, starts in milliseconds, runs the host's Python. The default
  on a Mac with no container runtime.
- DockerSandbox: a long-lived container per sandbox, workspace bind-mounted,
  network off. Stronger isolation, needs the docker CLI (OrbStack, Docker
  Desktop) and a running daemon.

make_sandbox() picks one; when it returns None the caller must not offer code
execution at all — there is deliberately no "run unsandboxed" fallback.

Usage:
    sb = make_sandbox("/path/to/task/workspace")
    if sb is not None:
        res = sb.run("python report.py", timeout_sec=60)
        sb.close()
"""

from __future__ import annotations

import logging
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

logger = logging.getLogger(__name__)

#: Per-stream cap on captured output. Enough to read a traceback or a report
#: summary; a runaway print loop must not flood the model's context.
MAX_OUTPUT_CHARS = 20_000
DEFAULT_TIMEOUT_SEC = 120
DEFAULT_DOCKER_IMAGE = "python:3.12-slim"
#: "auto" must never hang on a missing or wedged docker daemon.
DOCKER_PROBE_TIMEOUT_SEC = 3
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
#: Host PATH entries after the venv's bin dir — system tools only, never the
#: owner's PATH (which may hold credential helpers or shims).
SYSTEM_PATH = ("/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
#: Mach/XPC is how a process asks a system daemon to do something for it, and
#: a program a daemon launches (LaunchServices `open`, Apple Events) starts
#: OUTSIDE the sandbox with the owner's full rights. So Mach lookups are
#: deny-by-default; these are the only services python, git, pytest and curl
#: were found to need (user-name lookup, logging, notifications).
ALLOWED_MACH_SERVICES = (
    "com.apple.system.opendirectoryd.libinfo",
    "com.apple.system.opendirectoryd.membership",
    "com.apple.system.notification_center",
    "com.apple.system.logger",
    "com.apple.logd",
    "com.apple.diagnosticd",
)
#: Only with network on: DNS, TLS certificate trust, proxy settings.
NETWORK_MACH_SERVICES = (
    "com.apple.dnssd.service",
    "com.apple.trustd",
    "com.apple.trustd.agent",
    "com.apple.SystemConfiguration.configd",
    "com.apple.SystemConfiguration.DNSConfiguration",
)
#: Device files a normal program writes to. Everything else outside the
#: workspace and the private tmpdir is write-denied.
WRITABLE_DEVICES = (
    "/dev/null", "/dev/zero", "/dev/tty", "/dev/dtracehelper",
    "/dev/stdout", "/dev/stderr", "/dev/random", "/dev/urandom",
)
#: Largest file a sandboxed command may write (ulimit -f, in 512-byte
#: blocks): 2 GiB, so a runaway loop cannot fill the owner's disk.
MAX_FILE_BLOCKS = 4 * 1024 * 1024
#: After the command ends (or is killed), how long to keep reading output
#: that a leftover background process may still be writing.
DRAIN_GRACE_SEC = 2.0
#: Options for git the HOST runs in a workspace sandboxed code could write
#: to. The profile already keeps .git read-only; these switch off the config
#: settings that make plain `git status` / `git diff` run a command, in case
#: one got there another way (a workspace copied in with its .git).
HOST_GIT_OPTS = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")
#: Added to `git diff`: no external diff driver, no textconv filter.
HOST_GIT_DIFF_OPTS = ("--no-ext-diff", "--no-textconv")


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_sec: float


class Sandbox(Protocol):
    backend: str
    workspace: Path

    def run(self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> SandboxResult: ...

    def close(self) -> None: ...


def truncate_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Keep head and tail: the start says what ran, the end holds the error."""
    if len(text) <= limit:
        return text
    half = limit // 2
    dropped = len(text) - 2 * half
    return f"{text[:half]}\n... [{dropped} chars truncated] ...\n{text[-half:]}"


def _network_allowed() -> bool:
    return os.environ.get("AWOS_SANDBOX_NETWORK", "") == "1"


def _decode(data: Optional[bytes | str]) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


# ── Seatbelt (macOS) ─────────────────────────────────────────────────────────


def sbpl_quote(path: str | Path) -> str:
    """Render a path as an SBPL string literal.

    SBPL strings are double-quoted with backslash escapes; an unescaped quote
    in a workspace name would end the literal early and let the rest of the
    path be parsed as profile code.
    """
    s = str(path)
    if "\0" in s or "\n" in s or "\r" in s:
        raise ValueError(f"path not representable in a sandbox profile: {s!r}")
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ancestors(path: Path) -> list[Path]:
    """Every parent of path, root excluded — the dirs a process must stat to
    resolve its cwd, even when their contents stay unreadable."""
    return [p for p in path.parents if str(p) != "/"]


def _python_read_roots() -> list[Path]:
    """The interpreter in use must stay readable, or `python script.py` inside
    the sandbox dies before main — the venv may live under the denied home."""
    roots = {Path(sys.prefix), Path(sys.base_prefix), Path(os.path.realpath(sys.executable)).parent}
    resolved = set()
    for r in roots:
        resolved.add(r)
        resolved.add(Path(os.path.realpath(r)))
    return sorted(resolved)


def build_seatbelt_profile(
    workspace: Path,
    tmpdir: Path,
    read_allow: list[Path],
    read_deny: list[Path],
    allow_network: bool,
) -> str:
    """Generate the SBPL profile. SBPL applies the *last* matching rule, so
    each deny is followed by the narrower allows that carve holes in it."""
    q = sbpl_quote
    ws = _regex_escape(str(workspace))
    lines = [
        "(version 1)",
        "(allow default)",
        # Writes: deny-by-default. A deny-list always misses somewhere the
        # owner later runs from (/Applications is admin-writable).
        "(deny file-write*)",
        "(allow file-write* "
        + " ".join(f"(subpath {q(p)})" for p in (workspace, tmpdir))
        + " " + " ".join(f"(literal {q(d)})" for d in WRITABLE_DEVICES)
        + " " + f"(regex {q('^/dev/fd/')})" + ")",
    ]
    if read_deny:
        lines.append("(deny file-read* " + " ".join(f"(subpath {q(p)})" for p in read_deny) + ")")
        holes = [workspace, tmpdir, *read_allow]
        lines.append("(allow file-read* " + " ".join(f"(subpath {q(p)})" for p in holes) + ")")
        # Stat-only access to the parents of each hole: getcwd() and Python's
        # startup walk them. file-read-metadata does not allow listing them.
        parents = sorted({a for h in holes for a in _ancestors(h)})
        if parents:
            lines.append("(allow file-read-metadata " + " ".join(f"(literal {q(p)})" for p in parents) + ")")
    # The rules below come last so no hole above re-opens them.
    # Plain string literals, not #"...": raw regex literals cannot contain a
    # double quote, which a workspace path may.
    # .git holds config and hooks that the HOST's own git runs unsandboxed
    # (job reports, the parent session's commit): writable, it is a way out.
    git_dir = "^" + ws + r"/(.*/)?\.git(/.*)?$"
    lines.append(f"(deny file-write* (regex {q(git_dir)}))")
    # Secrets that commonly sit inside a repo copy. Writes are denied too:
    # renaming .env to a name the read rule does not match would expose it.
    dotenv = "^" + ws + r"/(.*/)?\.env(\.[^/]*)?$"
    lines.append(f"(deny file-read* file-write* (regex {q(dotenv)}))")
    services = ALLOWED_MACH_SERVICES + (NETWORK_MACH_SERVICES if allow_network else ())
    lines.append("(deny mach-lookup)")
    lines.append("(allow mach-lookup " + " ".join(f"(global-name {q(s)})" for s in services) + ")")
    # Two more ways to have a daemon start a program on the sandbox's behalf.
    lines.append("(deny appleevent-send)")
    lines.append("(deny lsopen)")
    if not allow_network:
        lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def _regex_escape(s: str) -> str:
    """Escape a literal path for an SBPL regex (POSIX ERE)."""
    return "".join("\\" + ch if ch in ".^$*+?()[]{}|\\" else ch for ch in s)


def _default_read_deny() -> list[Path]:
    """The owner's home (and every other user's) is off limits by default, and
    so are the shared temp areas: they hold other jobs' workspaces, other
    sandboxes' tmpdirs and whatever apps drop there."""
    return [Path(p) for p in (
        "/Users", "/Volumes", "/private/var/folders", "/private/tmp", "/private/var/tmp",
    )]


class SeatbeltSandbox:
    backend = "seatbelt"

    def __init__(
        self,
        workspace: str | Path,
        read_deny: Optional[list[str | Path]] = None,
        allow_network: Optional[bool] = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise FileNotFoundError(f"workspace is not a directory: {self.workspace}")
        # A private temp dir doubles as HOME, so tools that write caches or
        # dotfiles land somewhere disposable instead of failing or leaking.
        self.tmpdir = Path(tempfile.mkdtemp(prefix="awos-sbx-")).resolve()
        # Backstop for callers that never close(): the temp dir goes when the
        # sandbox is collected or the process exits, not left in /var/folders.
        self._cleanup = weakref.finalize(self, shutil.rmtree, str(self.tmpdir), True)
        deny = _default_read_deny() if read_deny is None else read_deny
        self.read_deny = [Path(os.path.realpath(p)) for p in deny]
        self.allow_network = _network_allowed() if allow_network is None else allow_network
        self.profile = build_seatbelt_profile(
            self.workspace, self.tmpdir, _python_read_roots(), self.read_deny, self.allow_network
        )

    def _env(self) -> dict[str, str]:
        venv_bin = os.path.dirname(sys.executable)
        env = {
            "PATH": os.pathsep.join([venv_bin, *SYSTEM_PATH]),
            "HOME": str(self.tmpdir),
            "TMPDIR": str(self.tmpdir),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        for key in ("LANG", "LC_ALL"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        env.setdefault("LANG", "en_US.UTF-8")
        return env

    def run(self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> SandboxResult:
        # Seatbelt has no disk quota; a per-file size cap stops a runaway
        # writer from filling the owner's disk. `ulimit` with no -S/-H sets
        # the hard limit too, so the command cannot raise it back.
        limited = f"ulimit -f {MAX_FILE_BLOCKS} 2>/dev/null; ulimit -c 0 2>/dev/null\n{command}"
        argv = [SANDBOX_EXEC, "-p", self.profile, "/bin/sh", "-c", limited]
        return _run_capped(argv, timeout_sec, cwd=self.workspace, env=self._env())

    def close(self) -> None:
        self._cleanup()  # idempotent: a finalizer runs at most once


class _HeadTail:
    """Keeps the first and last `limit // 2` bytes of a stream and counts the
    rest, so a command that prints for ten minutes costs the host a few KB,
    not gigabytes."""

    def __init__(self, limit: int = MAX_OUTPUT_CHARS) -> None:
        self.half = limit // 2
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def add(self, data: bytes) -> None:
        self.total += len(data)
        room = self.half - len(self.head)
        if room > 0:
            self.head += data[:room]
            data = data[room:]
        self.tail += data
        if len(self.tail) > 2 * self.half:  # trim in batches, not per chunk
            del self.tail[: len(self.tail) - self.half]

    def text(self) -> str:
        tail = bytes(self.tail[-self.half:]) if self.half else b""
        kept = len(self.head) + len(tail)
        if self.total <= kept:
            return _decode(bytes(self.head) + tail)
        dropped = self.total - kept
        return f"{_decode(bytes(self.head))}\n... [{dropped} chars truncated] ...\n{_decode(tail)}"


def _descendants(root_pid: int) -> list[int]:
    """Every live process below root_pid, found by parent links. Catches a
    child that left the process group (setsid) while its parent still lives,
    which killpg alone misses."""
    try:
        out = subprocess.run(["ps", "-Ao", "pid=,ppid="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], [root_pid]
    while stack:
        for kid in children.get(stack.pop(), []):
            found.append(kid)
            stack.append(kid)
    return found


def _kill_tree(proc: subprocess.Popen) -> None:
    """SIGKILL the command's whole tree: its process group, plus descendants
    that moved to a session of their own. The tree is read before anything
    is killed, while the parent links still hold."""
    strays = _descendants(proc.pid)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    for pid in strays:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _run_capped(
    argv: list[str],
    timeout_sec: float,
    kill: Callable[[subprocess.Popen], None] = _kill_tree,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> SandboxResult:
    """
    Run argv in its own session, reading its output as it comes.

    Two guarantees the host depends on: memory stays bounded (only head and
    tail are kept), and this returns within about timeout + DRAIN_GRACE_SEC
    even when a leftover background process holds the output pipe open
    forever — waiting for EOF on that pipe would hang the host with it.
    """
    start = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    buffers = {"out": _HeadTail(), "err": _HeadTail()}
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, "out")
    sel.register(proc.stderr, selectors.EVENT_READ, "err")
    deadline = start + timeout_sec
    timed_out, grace_end, pipe_held = False, None, False
    try:
        while True:
            now = time.monotonic()
            if not timed_out and proc.poll() is None and now >= deadline:
                timed_out = True
                kill(proc)
            if proc.poll() is not None or timed_out:
                grace_end = grace_end or now + DRAIN_GRACE_SEC
                if not sel.get_map():
                    break
                if now >= grace_end:
                    pipe_held = True
                    break
            if sel.get_map():
                for key, _ in sel.select(timeout=0.1):
                    data = os.read(key.fd, 65536)
                    if data:
                        buffers[key.data].add(data)
                    else:
                        sel.unregister(key.fileobj)
            else:
                try:
                    proc.wait(timeout=min(0.1, max(0.0, deadline - now)))
                except subprocess.TimeoutExpired:
                    pass
    finally:
        sel.close()
        proc.stdout.close()
        proc.stderr.close()
    try:
        proc.wait(timeout=DRAIN_GRACE_SEC)
    except subprocess.TimeoutExpired:
        pass
    # A command's background jobs do not outlive it: left running, they keep
    # write access to the workspace after the caller thinks it is done.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    stderr = buffers["err"].text()
    if pipe_held:
        stderr += ("\n[sandbox] a background process kept the output open after the "
                   "command ended; its further output was not collected")
    return SandboxResult(
        exit_code=-9 if timed_out else (proc.returncode if proc.returncode is not None else -9),
        stdout=buffers["out"].text(),
        stderr=stderr,
        timed_out=timed_out,
        duration_sec=round(time.monotonic() - start, 3),
    )


# ── Docker ───────────────────────────────────────────────────────────────────


class DockerSandbox:
    backend = "docker"
    MOUNT = "/workspace"

    def __init__(
        self,
        workspace: str | Path,
        image: Optional[str] = None,
        allow_network: Optional[bool] = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise FileNotFoundError(f"workspace is not a directory: {self.workspace}")
        self.image = image or os.environ.get("AWOS_SANDBOX_IMAGE") or DEFAULT_DOCKER_IMAGE
        self.allow_network = _network_allowed() if allow_network is None else allow_network
        self.name = f"awos-sbx-{uuid.uuid4().hex[:12]}"
        self.container_id: Optional[str] = None
        self._start()

    def start_argv(self) -> list[str]:
        argv = ["docker", "run", "-d", "--rm", "--name", self.name]
        if not self.allow_network:
            argv += ["--network", "none"]
        argv += ["--memory", "2g", "--cpus", "2", "--pids-limit", "256"]
        argv += ["--mount", _bind(self.workspace, self.MOUNT)]
        # Same two carve-outs as the Seatbelt profile: .git read-only (the
        # host runs git there unsandboxed), each .env hidden behind /dev/null.
        git_dir = self.workspace / ".git"
        if git_dir.exists():
            argv += ["--mount", _bind(git_dir, f"{self.MOUNT}/.git", readonly=True)]
        for env_file in _dotenv_files(self.workspace):
            rel = env_file.relative_to(self.workspace).as_posix()
            argv += ["--mount", _bind(Path("/dev/null"), f"{self.MOUNT}/{rel}", readonly=True)]
        argv += ["-w", self.MOUNT, self.image, "sleep", "infinity"]
        return argv

    def _start(self) -> None:
        # Pulling an image can take a while on first use; that is the one slow step.
        proc = subprocess.run(self.start_argv(), capture_output=True, text=True, timeout=600, env=_docker_cli_env())
        if proc.returncode != 0:
            raise RuntimeError(f"docker run failed: {proc.stderr.strip()[:500]}")
        self.container_id = proc.stdout.strip() or self.name

    def exec_argv(self, command: str) -> list[str]:
        # No -e flags: the container sees only the image's own environment.
        return ["docker", "exec", "-w", self.MOUNT, self.container_id or self.name, "/bin/sh", "-c", command]

    def run(self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> SandboxResult:
        return _run_capped(self.exec_argv(command), timeout_sec, kill=self._kill_exec, env=_docker_cli_env())

    def _kill_exec(self, proc: subprocess.Popen) -> None:
        # Killing the docker CLI does not stop the exec'd process; kill the
        # container's process tree (everything but PID 1), then the CLI.
        try:
            subprocess.run(
                ["docker", "exec", self.container_id or self.name, "/bin/sh", "-c", "kill -9 -1"],
                capture_output=True, timeout=10, env=_docker_cli_env(),
            )
        except (OSError, subprocess.SubprocessError):
            pass
        _kill_tree(proc)

    def close(self) -> None:
        if self.container_id is None:
            return
        subprocess.run(
            ["docker", "rm", "-f", self.container_id], capture_output=True, timeout=30, env=_docker_cli_env()
        )
        self.container_id = None


#: Dirs never walked when looking for .env files to hide: large, and not
#: where a project keeps its secrets.
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache"}


def _dotenv_files(workspace: Path) -> list[Path]:
    """The .env / .env.* files the Seatbelt profile's dotenv rule would deny."""
    found = []
    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        found += [Path(dirpath) / f for f in filenames if f == ".env" or f.startswith(".env.")]
    return sorted(found)


def _bind(source: Path, target: str, readonly: bool = False) -> str:
    """A --mount spec. Its fields are comma-separated (and `-v` splits on
    ':'), so a path holding a comma could inject mount options: refuse it."""
    for path in (str(source), target):
        if any(ch in path for ch in ',"\n'):
            raise RuntimeError(f"path cannot be mounted safely into a container: {path!r}")
    return f"type=bind,source={source},target={target}" + (",readonly" if readonly else "")


def _docker_cli_env() -> dict[str, str]:
    """The docker CLI needs HOME (for its context config) and DOCKER_* to find
    the daemon; nothing else, so no API key can ride along into a subprocess."""
    env = {k: v for k, v in os.environ.items() if k.startswith("DOCKER_")}
    for key in ("PATH", "HOME"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


# ── Selection ────────────────────────────────────────────────────────────────


def docker_available() -> bool:
    """True when the docker CLI exists and its daemon answers quickly."""
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, timeout=DOCKER_PROBE_TIMEOUT_SEC, env=_docker_cli_env(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def seatbelt_available() -> bool:
    return sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC)


def make_sandbox(workspace: str | Path, backend: Optional[str] = None) -> Optional[Sandbox]:
    """Return a sandbox for workspace, or None when code must not run.

    backend None reads AWOS_SANDBOX ("auto" | "seatbelt" | "docker" | "none").
    An explicitly requested backend that is unavailable yields None rather
    than a silent swap to a different isolation model.
    """
    choice = (backend or os.environ.get("AWOS_SANDBOX") or "auto").strip().lower()
    if choice == "none":
        return None
    if choice not in ("auto", "seatbelt", "docker"):
        logger.warning("unknown AWOS_SANDBOX=%r; code execution disabled", choice)
        return None
    candidates = []
    if choice in ("auto", "docker") and docker_available():
        candidates.append(DockerSandbox)
    if choice in ("auto", "seatbelt") and seatbelt_available():
        candidates.append(SeatbeltSandbox)
    for cls in candidates:
        try:
            return cls(workspace)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            # In auto mode a docker that answers `info` but cannot start a
            # container (image pull offline) still leaves Seatbelt to try.
            logger.warning("sandbox %s failed to start: %s", cls.backend, exc)
    return None

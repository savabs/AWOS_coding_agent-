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

Read-only control paths (DEFAULT_READONLY_PATHS, plus .git always): both
backends let sandboxed code read them but not write them.
- Seatbelt: name-matched deny file-write* rules, so a path that does not
  exist yet cannot be created either. Fully enforced by the kernel.
- Docker: read-only bind mounts over each path. Missing default dirs get an
  empty read-only placeholder mount; a missing env-added path that may be a
  file, and any symlinked control path, cannot be protected and is logged and
  listed in DockerSandbox.unprotected rather than silently assumed safe.
There is no third backend, so nothing runs without one of these.

Opt-in egress (AWOS_SANDBOX_EGRESS=allowlist; default off; REA synthesis
item 14, after Claude Cowork's egress proxy): for tasks that must `pip
install`, `npm install` or `git fetch`. An HTTP(S) forward proxy
(egress_proxy.py, stdlib) allows CONNECT / plain HTTP only to an allowlisted
host (DEFAULT_EGRESS_ALLOW, edited with AWOS_SANDBOX_EGRESS_ALLOW), strips
Authorization / Proxy-Authorization / Cookie from plain-HTTP requests, adds
no credentials, and logs each allowed / denied host to a JSONL
(egress_report()). The sandbox env gets HTTP(S)_PROXY, http(s)_proxy,
PIP_PROXY, npm_config_(https_)proxy. Started on the first run(), stopped by
close(). What each backend enforces:
- Seatbelt: the proxy runs on the host at 127.0.0.1:<port>. The kernel
  profile still denies all non-loopback network, so a process that ignores
  the proxy variables is BLOCKED (it cannot reach the internet directly).
  Loopback stays open as before (localhost:*), so the proxy port is
  reachable; the profile also names it explicitly and admits trustd for TLS.
- Docker: the container runs on a per-sandbox `--internal` network (no
  route out, unlike the default bridge) instead of `--network none`; the
  proxy runs in a small sidecar container (same egress_proxy.py, no env, no
  caps, read-only rootfs, host uid) attached to both that network and the
  default bridge. A process that ignores the proxy variables is BLOCKED by
  docker's routing: the only thing it can reach is the sidecar's port.
Neither backend can inspect what goes through an allowed CONNECT tunnel:
data can be sent to an allowlisted host (e.g. a push to any GitHub repo
with a token the code brought along). See egress_report()["enforcement"].

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

try:
    from . import egress_proxy as _egress
except ImportError:  # loaded as a top-level module (scaffold/agent on sys.path)
    import egress_proxy as _egress

logger = logging.getLogger(__name__)

DEFAULT_EGRESS_ALLOW = _egress.DEFAULT_EGRESS_ALLOW
EGRESS_ALLOW_ENV = _egress.EGRESS_ALLOW_ENV

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
#: `.env.<suffix>` files that hold no secrets by convention: readable in the
#: sandbox. Every other .env / .env.* (.env.local, .env.production...) is not.
DOTENV_EXAMPLE_SUFFIXES = ("example", "sample", "template", "dist", "defaults")
#: Agent control paths, relative to the workspace root, that sandboxed code
#: may read but never create, change, rename or delete (REA synthesis item
#: 13; the Codex app protects its control dirs the same way).
#:
#: The test for inclusion: does the path steer an AGENT or a HOOK that runs
#: later, outside this sandbox, with the owner's rights? Code planted there
#: escapes the sandbox the next time the owner (or AWOS itself) opens the
#: repo: Claude Code / Codex / Cursor load settings, hooks, MCP servers and
#: skills from these dirs; husky points git's core.hooksPath at .husky/; and
#: .awos/ holds AWOS's own memory, rewards and skills, which a task must never
#: be able to grade or teach itself through.
#:
#: Deliberately NOT on the list: AGENTS.md, CLAUDE.md, .cursorrules,
#: .pre-commit-config.yaml, .github/. They are ordinary tracked project files
#: that a legitimate task may be asked to edit ("document the new command in
#: CLAUDE.md", "add ruff to pre-commit"), and an edit to them is not silent:
#: it lands in the diff that review and the owner read. Nor do they execute
#: anything by themselves — .pre-commit-config.yaml only runs through a hook
#: installed in .git/hooks, which is protected via .git. A deployment that
#: wants them frozen too adds them with AWOS_SANDBOX_READONLY_PATHS.
#:
#: .git is not in this tuple: it is protected unconditionally (any depth,
#: submodules included) by its own rule, because the host runs git there
#: unsandboxed — see HOST_GIT_OPTS. AWOS_SANDBOX_READONLY=0 cannot lift it.
DEFAULT_READONLY_PATHS = (
    ".awos", ".claude", ".agents", ".codex", ".cursor", ".husky",
)
#: Env: comma-separated extra workspace-relative paths to protect. An entry
#: "!name" drops a default (never .git), e.g. when the task is AWOS working on
#: its own repo and its tests must write .awos/.
READONLY_PATHS_ENV = "AWOS_SANDBOX_READONLY_PATHS"
#: Env: "0" switches the control-path layer off. .git stays read-only.
READONLY_SWITCH_ENV = "AWOS_SANDBOX_READONLY"
#: Set inside the sandbox: git skips the optional .git/index refresh-and-
#: write that `git status` does, which the read-only .git would refuse.
SANDBOX_GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0"}
#: Env: "off" (default) | "allowlist" — the opt-in egress proxy.
EGRESS_SWITCH_ENV = "AWOS_SANDBOX_EGRESS"
#: Env: a file path to keep the egress JSONL at (default: a temp file that
#: close() removes; its events stay readable via egress_report()).
EGRESS_LOG_ENV = "AWOS_SANDBOX_EGRESS_LOG"
#: Env: the image the docker egress sidecar runs (needs python3).
EGRESS_IMAGE_ENV = "AWOS_SANDBOX_EGRESS_IMAGE"
#: Mach services Seatbelt admits in egress mode: TLS certificate trust, for
#: git / curl built against the system TLS stack. No DNS service: through a
#: proxy the client never resolves the target itself.
EGRESS_MACH_SERVICES = ("com.apple.trustd", "com.apple.trustd.agent")
#: Local servers a test starts must not be sent through the proxy.
EGRESS_NO_PROXY = "localhost,127.0.0.1,::1"
DOCKER_EGRESS_PORT = 3128
DOCKER_EGRESS_ALIAS = "awos-egress-proxy"
DOCKER_EGRESS_READY_SEC = 15.0
_EGRESS_PROXY_SRC = Path(__file__).with_name("egress_proxy.py")


def _normalize_rel(entry: str) -> Optional[str]:
    """A safe workspace-relative POSIX path, or None for an entry that is not
    one (absolute, '..', empty): those are ignored with a warning rather than
    letting configuration point a rule outside the workspace."""
    s = entry.strip().replace("\\", "/").strip("/")
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if not parts or entry.strip().startswith("/") or ".." in parts:
        if entry.strip():
            logger.warning("%s: ignoring %r (must be a relative path inside the workspace)",
                           READONLY_PATHS_ENV, entry)
        return None
    return "/".join(parts)


def readonly_control_paths(env: Optional[dict] = None) -> list[str]:
    """The control paths to protect: DEFAULT_READONLY_PATHS plus
    AWOS_SANDBOX_READONLY_PATHS additions, minus its "!name" removals; empty
    when AWOS_SANDBOX_READONLY=0. Never contains .git (handled separately)."""
    env = os.environ if env is None else env
    if env.get(READONLY_SWITCH_ENV, "").strip() == "0":
        return []
    paths = list(DEFAULT_READONLY_PATHS)
    for raw in env.get(READONLY_PATHS_ENV, "").split(","):
        drop = raw.strip().startswith("!")
        rel = _normalize_rel(raw.strip()[1:] if drop else raw)
        if rel is None or rel == ".git" or rel.startswith(".git/"):
            continue  # .git has its own, unconditional rule
        if drop:
            paths = [p for p in paths if p != rel]
        elif rel not in paths:
            paths.append(rel)
    return paths


def egress_mode(env: Optional[dict] = None) -> str:
    """"off" or "allowlist" from AWOS_SANDBOX_EGRESS. Anything unrecognised
    is "off" (fail closed: no network), with a warning."""
    env = os.environ if env is None else env
    value = env.get(EGRESS_SWITCH_ENV, "").strip().lower()
    if value in ("", "off", "0", "none", "false", "no"):
        return "off"
    if value == "allowlist":
        return "allowlist"
    logger.warning("unknown %s=%r; egress stays off", EGRESS_SWITCH_ENV, value)
    return "off"


def egress_allowlist(env: Optional[dict] = None) -> list[str]:
    """DEFAULT_EGRESS_ALLOW plus AWOS_SANDBOX_EGRESS_ALLOW additions
    ("host", "*.suffix"), minus its "!host" removals."""
    env = os.environ if env is None else env
    return _egress.parse_allowlist(env.get(EGRESS_ALLOW_ENV, ""))


def egress_proxy_env(proxy_url: str) -> dict[str, str]:
    """The variables pip, npm, git, curl and Python's urllib/requests read."""
    env = {}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "PIP_PROXY", "npm_config_proxy", "npm_config_https_proxy"):
        env[key] = proxy_url
    env["NO_PROXY"] = env["no_proxy"] = EGRESS_NO_PROXY
    return env


def summarize_egress(events: list[dict]) -> dict[str, dict[str, int]]:
    """{"allowed": {host: n}, "denied": {host: n}, "errors": {host: n}}."""
    out: dict[str, dict[str, int]] = {"allowed": {}, "denied": {}, "errors": {}}
    bucket = {"allow": "allowed", "deny": "denied", "error": "errors"}
    for ev in events:
        b = out[bucket.get(ev.get("decision"), "errors")]
        host = str(ev.get("host"))
        b[host] = b.get(host, 0) + 1
    return out


def _read_jsonl(path: Optional[Path]) -> list[dict]:
    import json
    events = []
    try:
        with open(path, encoding="utf-8") as fh:  # type: ignore[arg-type]
            for line in fh:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except (OSError, TypeError):
        pass
    return events


class _EgressMixin:
    """Shared egress bookkeeping: mode, allowlist, log location, report."""

    egress: str = "off"
    egress_allow: list[str] = []
    egress_log_path: Optional[Path] = None
    _egress_dir: Optional[Path] = None
    _egress_cached: Optional[list[dict]] = None
    ENFORCEMENT = ""

    def _init_egress(self, egress: Optional[str], allow_network: bool,
                     egress_allow: Optional[list[str]]) -> None:
        mode = egress_mode() if egress is None else egress
        if mode not in ("off", "allowlist"):
            raise ValueError(f"egress must be 'off' or 'allowlist', got {mode!r}")
        if mode == "allowlist" and allow_network:
            logger.warning("%s=allowlist ignored: AWOS_SANDBOX_NETWORK=1 already gives full network",
                           EGRESS_SWITCH_ENV)
            mode = "off"
        self.egress = mode
        self.egress_allow = egress_allowlist() if egress_allow is None else list(egress_allow)
        self.egress_log_path = None
        self._egress_dir = None
        self._egress_cached = None

    def _new_egress_log(self) -> Path:
        if self._egress_dir is not None:  # a failed earlier start
            shutil.rmtree(self._egress_dir, ignore_errors=True)
        self._egress_dir = Path(tempfile.mkdtemp(prefix="awos-egress-")).resolve()
        custom = os.environ.get(EGRESS_LOG_ENV, "").strip()
        self.egress_log_path = Path(custom) if custom else self._egress_dir / "egress.jsonl"
        return self.egress_log_path

    def egress_events(self) -> list[dict]:
        if self._egress_cached is not None:
            return list(self._egress_cached)
        return _read_jsonl(self.egress_log_path)

    def _finish_egress_log(self) -> None:
        """Keep the events in memory, then remove the temp log dir."""
        if self.egress_log_path is not None and self._egress_cached is None:
            self._egress_cached = _read_jsonl(self.egress_log_path)
        if self._egress_dir is not None:
            shutil.rmtree(self._egress_dir, ignore_errors=True)
            if self.egress_log_path is not None and self._egress_dir in self.egress_log_path.parents:
                self.egress_log_path = None
            self._egress_dir = None

    def egress_report(self) -> dict:
        """What the run can report: mode, allowlist, per-host counts, and
        what this backend does and does not enforce. No URLs, no headers."""
        events = self.egress_events()
        return {
            "mode": self.egress,
            "allow": list(self.egress_allow) if self.egress != "off" else [],
            "log_path": str(self.egress_log_path) if self.egress_log_path else None,
            **summarize_egress(events),
            "enforcement": self.ENFORCEMENT if self.egress != "off" else "egress off: no network beyond loopback",
        }


def is_secret_dotenv(name: str) -> bool:
    """True for a .env file name whose content the sandbox hides."""
    if name == ".env":
        return True
    if not name.startswith(".env."):
        return False
    return name[len(".env."):] not in DOTENV_EXAMPLE_SUFFIXES


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
    readonly_paths: Optional[list[str]] = None,
    egress_proxy_port: Optional[int] = None,
) -> str:
    """Generate the SBPL profile. SBPL applies the *last* matching rule, so
    each deny is followed by the narrower allows that carve holes in it.

    readonly_paths: workspace-relative control paths (readonly_control_paths)
    that are write-denied in addition to .git; None means none.
    egress_proxy_port: the host egress proxy's loopback port (egress mode);
    ignored when allow_network is True."""
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
    # Agent control paths (DEFAULT_READONLY_PATHS): the path itself and
    # everything below it, matched by name so a path that does not exist yet
    # cannot be created either (a planted .claude/settings.json is the
    # attack). Anchored at the workspace root: that is where agents and hook
    # managers look for them. Reads stay allowed.
    if readonly_paths:
        alts = "|".join(_regex_escape(p) for p in readonly_paths)
        control = "^" + ws + "/(" + alts + ")(/.*)?$"
        lines.append(f"(deny file-write* (regex {q(control)}))")
    # Secrets that commonly sit inside a repo copy. Writes are denied too:
    # renaming .env to a name the read rule does not match would expose it.
    # Only the CONTENT is denied: stat() stays allowed, or pytest's collection
    # (which stats every root entry) dies with PermissionError in any repo
    # that ships a .env.example (v0.2.2; demos README lesson 2).
    dotenv = "^" + ws + r"/(.*/)?\.env(\.[^/]*)?$"
    lines.append(f"(deny file-read-data file-write* (regex {q(dotenv)}))")
    # Example env files are documentation, not secrets: readable.
    example = "^" + ws + r"/(.*/)?\.env\.(" + "|".join(DOTENV_EXAMPLE_SUFFIXES) + r")$"
    lines.append(f"(allow file-read-data (regex {q(example)}))")
    egress = egress_proxy_port is not None and not allow_network
    services = ALLOWED_MACH_SERVICES + (NETWORK_MACH_SERVICES if allow_network else ())
    if egress:
        services += EGRESS_MACH_SERVICES
    lines.append("(deny mach-lookup)")
    lines.append("(allow mach-lookup " + " ".join(f"(global-name {q(s)})" for s in services) + ")")
    # Two more ways to have a daemon start a program on the sandbox's behalf.
    lines.append("(deny appleevent-send)")
    lines.append("(deny lsopen)")
    if not allow_network:
        lines.append("(deny network*)")
        # Loopback only: unit tests commonly open a localhost server or a
        # socketpair (boltons' socketutils tests). Denying those fails tests
        # the change never touched, and the gate then rejects a correct fix.
        # Per operation, so an outbound connection is allowed only TO
        # localhost (a combined network* rule also let the internet through).
        lines.append('(allow network-bind (local ip "localhost:*"))')
        lines.append('(allow network-inbound (local ip "localhost:*"))')
        lines.append('(allow network-outbound (remote ip "localhost:*"))')
        if egress:
            # Egress mode: the only way out is the host proxy on loopback.
            # localhost:* above already admits it; named here so the profile
            # states the intent, and so a later tightening of the loopback
            # rule keeps it. Every non-loopback destination stays denied by
            # (deny network*), whether or not a process honours the proxy
            # variables.
            lines.append(f'(allow network-outbound (remote ip "localhost:{int(egress_proxy_port)}"))')
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


class SeatbeltSandbox(_EgressMixin):
    backend = "seatbelt"
    ENFORCEMENT = (
        "seatbelt: proxy on host 127.0.0.1; kernel profile denies every non-loopback "
        "connection, so a process ignoring HTTP(S)_PROXY cannot reach the internet. "
        "Not enforced: anything listening on the host's loopback stays reachable (as "
        "without egress, e.g. a local DNS resolver on 127.0.0.1:53); contents of allowed "
        "CONNECT tunnels are opaque; uploads to allowlisted hosts are possible."
    )

    def __init__(
        self,
        workspace: str | Path,
        read_deny: Optional[list[str | Path]] = None,
        allow_network: Optional[bool] = None,
        readonly_paths: Optional[list[str]] = None,
        egress: Optional[str] = None,
        egress_allow: Optional[list[str]] = None,
        egress_resolver=None,
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
        self.readonly_paths = readonly_control_paths() if readonly_paths is None else list(readonly_paths)
        self.extra_read: list[Path] = []
        self._init_egress(egress, self.allow_network, egress_allow)
        self._proxy: Optional[_egress.EgressProxy] = None
        self._egress_resolver = egress_resolver  # tests map names without real DNS
        self._build()

    def _build(self) -> None:
        self.profile = build_seatbelt_profile(
            self.workspace, self.tmpdir, _python_read_roots() + self.extra_read,
            self.read_deny, self.allow_network, self.readonly_paths,
            egress_proxy_port=self._proxy.port if self._proxy is not None else None,
        )

    def _ensure_egress(self) -> None:
        """Start the host proxy on first use and rebuild the profile with its
        port. The log lives in a temp dir of its own: outside the workspace
        and the sandbox tmpdir, so sandboxed code can neither write nor read
        it (writes are deny-by-default; the temp areas are read-denied)."""
        if self.egress != "allowlist" or self._proxy is not None:
            return
        log_path = self._new_egress_log()
        self._proxy = _egress.EgressProxy(
            allow=self.egress_allow, bind="127.0.0.1", log_path=str(log_path),
            resolver=self._egress_resolver,
        ).start()
        # Backstop for a sandbox dropped without close(): no orphan listener.
        weakref.finalize(self, self._proxy.stop)
        self._build()

    @property
    def egress_proxy_url(self) -> Optional[str]:
        return f"http://127.0.0.1:{self._proxy.port}" if self._proxy is not None else None

    def allow_read(self, paths) -> None:
        """Let sandboxed commands read these dirs too: the project's own venv
        and the base interpreter it links to (v0.2.2: tests run in the
        project's env, which usually sits in the user's checkout)."""
        added = False
        for p in paths or []:
            path = Path(os.path.realpath(p))
            if str(path) in ("/", "") or path in self.extra_read:
                continue
            self.extra_read.append(path)
            added = True
        if added:
            self._build()

    def _env(self) -> dict[str, str]:
        venv_bin = os.path.dirname(sys.executable)
        env = {
            "PATH": os.pathsep.join([venv_bin, *SYSTEM_PATH]),
            "HOME": str(self.tmpdir),
            "TMPDIR": str(self.tmpdir),
            "PYTHONDONTWRITEBYTECODE": "1",
            **SANDBOX_GIT_ENV,
        }
        for key in ("LANG", "LC_ALL"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        env.setdefault("LANG", "en_US.UTF-8")
        if self.egress_proxy_url:
            env.update(egress_proxy_env(self.egress_proxy_url))
        return env

    def run(self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> SandboxResult:
        try:
            self._ensure_egress()
        except OSError as exc:
            return SandboxResult(1, "", f"[sandbox] egress proxy failed to start: {exc}", False, 0.0)
        # Seatbelt has no disk quota; a per-file size cap stops a runaway
        # writer from filling the owner's disk. `ulimit` with no -S/-H sets
        # the hard limit too, so the command cannot raise it back.
        limited = f"ulimit -f {MAX_FILE_BLOCKS} 2>/dev/null; ulimit -c 0 2>/dev/null\n{command}"
        argv = [SANDBOX_EXEC, "-p", self.profile, "/bin/sh", "-c", limited]
        return _run_capped(argv, timeout_sec, cwd=self.workspace, env=self._env())

    def close(self) -> None:
        if self._proxy is not None:
            self._proxy.stop()
            self._proxy = None
        self._finish_egress_log()
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


class DockerSandbox(_EgressMixin):
    backend = "docker"
    MOUNT = "/workspace"
    ENFORCEMENT = (
        "docker: container on a per-sandbox --internal network (no route out); proxy in a "
        "sidecar container on that network and the default bridge. A process ignoring "
        "HTTP(S)_PROXY cannot reach the internet: the sidecar's port is the only reachable "
        "endpoint. Not enforced: contents of allowed CONNECT tunnels are opaque; uploads to "
        "allowlisted hosts are possible; DNS queries via docker's embedded resolver may be "
        "forwarded upstream on older docker engines (a low-bandwidth DNS channel); the sidecar "
        "listens on 0.0.0.0, so other containers on the default bridge could use it too."
    )

    def __init__(
        self,
        workspace: str | Path,
        image: Optional[str] = None,
        allow_network: Optional[bool] = None,
        readonly_paths: Optional[list[str]] = None,
        egress: Optional[str] = None,
        egress_allow: Optional[list[str]] = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise FileNotFoundError(f"workspace is not a directory: {self.workspace}")
        self.image = image or os.environ.get("AWOS_SANDBOX_IMAGE") or DEFAULT_DOCKER_IMAGE
        self.allow_network = _network_allowed() if allow_network is None else allow_network
        self.readonly_paths = readonly_control_paths() if readonly_paths is None else list(readonly_paths)
        self.name = f"awos-sbx-{uuid.uuid4().hex[:12]}"
        self.container_id: Optional[str] = None
        # Empty host dir, bind-mounted read-only over control dirs that do not
        # exist yet, so the container cannot create them either.
        self._empty_dir = Path(tempfile.mkdtemp(prefix="awos-sbx-empty-")).resolve()
        weakref.finalize(self, shutil.rmtree, str(self._empty_dir), True)
        self.unprotected: list[str] = []
        self._placeholders: list[Path] = []
        self._init_egress(egress, self.allow_network, egress_allow)
        self.egress_network: Optional[str] = f"{self.name}-net" if self.egress == "allowlist" else None
        self.egress_sidecar = f"{self.name}-egress"
        self._network_created = False
        self._sidecar_started = False
        self._start()

    def _readonly_mounts(self, nested_git: list[Path]) -> tuple[list[str], list[Path], list[str]]:
        """--mount specs that make .git and the control paths read-only.

        Returns (mounts, placeholder mountpoints docker will create in the
        workspace, control paths this backend cannot protect).

        Docker can only protect a path by mounting over it, which differs
        from the Seatbelt rule in three ways, all handled here:
        - a control dir that does not exist yet gets an empty read-only
          placeholder mount (docker creates the empty mountpoint dir in the
          workspace; close() removes it again if still empty);
        - a missing control path that is not a default dir (an env-added
          entry such as CLAUDE.md, which may be a file) cannot be guessed as
          file or dir, so it is NOT protected and is listed as unprotected;
        - a symlink is never mounted: docker would follow it on the HOST and
          expose its target. It is listed as unprotected (the Seatbelt rule
          still covers the link itself).
        """
        mounts: list[str] = []
        placeholders: list[Path] = []
        unprotected: list[str] = []
        # .git: top level and nested (vendored repos, submodule gitlinks).
        for git_path in [self.workspace / ".git", *nested_git]:
            if git_path.is_symlink() or not git_path.exists():
                continue
            rel = git_path.relative_to(self.workspace).as_posix()
            mounts.append(_bind(git_path, f"{self.MOUNT}/{rel}", readonly=True))
        for rel in self.readonly_paths:
            host = self.workspace / rel
            if host.is_symlink():
                unprotected.append(rel)
            elif host.exists():
                mounts.append(_bind(host, f"{self.MOUNT}/{rel}", readonly=True))
            elif rel in DEFAULT_READONLY_PATHS:
                mounts.append(_bind(self._empty_dir, f"{self.MOUNT}/{rel}", readonly=True))
                placeholders.append(host)
            else:
                unprotected.append(rel)
        return mounts, placeholders, unprotected

    def start_argv(self) -> list[str]:
        argv = ["docker", "run", "-d", "--rm", "--name", self.name]
        if self.egress_network:
            # An --internal network has no gateway route out: the container
            # can reach only other members of it, i.e. the egress sidecar.
            argv += ["--network", self.egress_network]
        elif not self.allow_network:
            argv += ["--network", "none"]
        argv += ["--memory", "2g", "--cpus", "2", "--pids-limit", "256"]
        argv += ["--mount", _bind(self.workspace, self.MOUNT)]
        # Same carve-outs as the Seatbelt profile: .git and the agent control
        # paths read-only (mounted after the workspace, so they sit on top of
        # it), each .env hidden behind /dev/null.
        dotenv, nested_git = _walk_workspace(self.workspace)
        mounts, self._placeholders, self.unprotected = self._readonly_mounts(nested_git)
        for spec in mounts:
            argv += ["--mount", spec]
        for env_file in dotenv:
            rel = env_file.relative_to(self.workspace).as_posix()
            argv += ["--mount", _bind(Path("/dev/null"), f"{self.MOUNT}/{rel}", readonly=True)]
        argv += ["-w", self.MOUNT, self.image, "sleep", "infinity"]
        return argv

    def _start(self) -> None:
        argv = self.start_argv()
        if self.unprotected:
            logger.warning("docker sandbox cannot make these control paths read-only "
                           "(missing non-default path, or a symlink): %s", ", ".join(self.unprotected))
        if self.egress_network:
            net = subprocess.run(self.network_create_argv(), capture_output=True, text=True,
                                 timeout=60, env=_docker_cli_env())
            if net.returncode != 0:
                self._remove_placeholders()
                raise RuntimeError(f"docker network create failed: {str(net.stderr).strip()[:500]}")
            self._network_created = True
        # Pulling an image can take a while on first use; that is the one slow step.
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=600, env=_docker_cli_env())
        if proc.returncode != 0:
            self._remove_placeholders()
            self._remove_network()
            raise RuntimeError(f"docker run failed: {proc.stderr.strip()[:500]}")
        self.container_id = proc.stdout.strip() or self.name

    # ── egress sidecar ──

    def network_create_argv(self) -> list[str]:
        return ["docker", "network", "create", "--internal",
                "--label", "awos.sandbox=egress", self.egress_network or ""]

    def sidecar_argv(self, log_dir: Path) -> list[str]:
        """The proxy sidecar: egress_proxy.py mounted read-only, the final
        allowlist on its command line, no environment, no capabilities, a
        read-only rootfs, the host user's uid, small resource caps. Its only
        writable path is the log dir, which the sandbox container never sees."""
        image = os.environ.get(EGRESS_IMAGE_ENV) or DEFAULT_DOCKER_IMAGE
        argv = ["docker", "run", "-d", "--rm", "--name", self.egress_sidecar,
                "--network", "bridge", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--memory", "256m", "--cpus", "0.5", "--pids-limit", "128"]
        if hasattr(os, "getuid"):
            argv += ["--user", f"{os.getuid()}:{os.getgid()}"]
        argv += ["--mount", _bind(_EGRESS_PROXY_SRC.resolve(), "/awos/egress_proxy.py", readonly=True),
                 "--mount", _bind(log_dir, "/awos-log"),
                 image, "python3", "-I", "/awos/egress_proxy.py",
                 "--listen", f"0.0.0.0:{DOCKER_EGRESS_PORT}",
                 "--allow", ",".join(self.egress_allow),
                 "--log", "/awos-log/egress.jsonl"]
        return argv

    @property
    def egress_proxy_url(self) -> Optional[str]:
        if not self._sidecar_started:
            return None
        return f"http://{DOCKER_EGRESS_ALIAS}:{DOCKER_EGRESS_PORT}"

    def _ensure_egress(self) -> None:
        """Start the sidecar on first run() and attach it to the sandbox's
        internal network under DOCKER_EGRESS_ALIAS. Raises RuntimeError."""
        if self.egress != "allowlist" or self._sidecar_started:
            return
        self._new_egress_log()
        log_dir = self._egress_dir
        assert log_dir is not None
        # The sidecar writes the log there; with a custom AWOS_SANDBOX_EGRESS_LOG
        # the file is copied out on close().
        self._sidecar_log = log_dir / "egress.jsonl"
        env = _docker_cli_env()
        proc = subprocess.run(self.sidecar_argv(log_dir), capture_output=True, text=True, timeout=600, env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"egress sidecar failed to start: {str(proc.stderr).strip()[:500]}")
        self._sidecar_started = True
        try:
            conn = subprocess.run(
                ["docker", "network", "connect", "--alias", DOCKER_EGRESS_ALIAS,
                 self.egress_network or "", self.egress_sidecar],
                capture_output=True, text=True, timeout=60, env=env)
            if conn.returncode != 0:
                raise RuntimeError(f"egress sidecar network connect failed: {str(conn.stderr).strip()[:500]}")
            deadline = time.monotonic() + DOCKER_EGRESS_READY_SEC
            while True:
                logs = subprocess.run(["docker", "logs", self.egress_sidecar], capture_output=True,
                                      text=True, timeout=30, env=env)
                if "listening" in _decode(logs.stdout):
                    return
                if time.monotonic() >= deadline:
                    raise RuntimeError("egress sidecar did not report ready: "
                                       + _decode(logs.stderr).strip()[:300])
                time.sleep(0.2)
        except BaseException:
            # Half-started: remove it so no proxy env points at a dead alias
            # and the next run() tries again from scratch.
            subprocess.run(["docker", "rm", "-f", self.egress_sidecar], capture_output=True,
                           timeout=30, env=env)
            self._sidecar_started = False
            raise

    def egress_events(self) -> list[dict]:
        if self._egress_cached is not None:
            return list(self._egress_cached)
        return _read_jsonl(getattr(self, "_sidecar_log", None))

    def _stop_egress(self) -> None:
        if self._sidecar_started:
            subprocess.run(["docker", "rm", "-f", self.egress_sidecar], capture_output=True,
                           timeout=30, env=_docker_cli_env())
            self._sidecar_started = False
        sidecar_log = getattr(self, "_sidecar_log", None)
        if sidecar_log is not None and self._egress_cached is None:
            self._egress_cached = _read_jsonl(sidecar_log)
            if self.egress_log_path is not None and self.egress_log_path != sidecar_log:
                try:  # AWOS_SANDBOX_EGRESS_LOG: keep a copy past close()
                    with open(self.egress_log_path, "a", encoding="utf-8") as fh:
                        fh.write(sidecar_log.read_text(encoding="utf-8") if sidecar_log.exists() else "")
                except OSError as exc:
                    logger.warning("could not write %s: %s", self.egress_log_path, exc)
        self._finish_egress_log()

    def _remove_network(self) -> None:
        if self._network_created and self.egress_network:
            subprocess.run(["docker", "network", "rm", self.egress_network], capture_output=True,
                           timeout=30, env=_docker_cli_env())
            self._network_created = False

    def _remove_placeholders(self) -> None:
        """Remove the empty mountpoint dirs docker created for missing control
        dirs, so the workspace is left as it was. Non-empty means something
        else (the host) put content there: left alone."""
        for path in self._placeholders:
            try:
                path.rmdir()
            except OSError:
                pass
        self._placeholders = []

    def exec_argv(self, command: str) -> list[str]:
        # The only -e flags are fixed, non-secret settings (SANDBOX_GIT_ENV);
        # the host's environment never reaches the container.
        fixed = dict(SANDBOX_GIT_ENV)
        if self.egress_proxy_url:
            fixed.update(egress_proxy_env(self.egress_proxy_url))
        env_flags = [x for k, v in fixed.items() for x in ("-e", f"{k}={v}")]
        return ["docker", "exec", *env_flags, "-w", self.MOUNT, self.container_id or self.name,
                "/bin/sh", "-c", command]

    def run(self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> SandboxResult:
        try:
            self._ensure_egress()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            # The container stays networkless; say why installs will fail.
            return SandboxResult(1, "", f"[sandbox] egress proxy failed to start: {exc}", False, 0.0)
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
        self._stop_egress()
        subprocess.run(
            ["docker", "rm", "-f", self.container_id], capture_output=True, timeout=30, env=_docker_cli_env()
        )
        self.container_id = None
        self._remove_network()
        self._remove_placeholders()


#: Dirs never walked when looking for .env files to hide: large, and not
#: where a project keeps its secrets.
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache"}


def _walk_workspace(workspace: Path) -> tuple[list[Path], list[Path]]:
    """One walk, two lists: the .env / .env.* files the Seatbelt dotenv rule
    would deny, and the nested .git entries (dirs or gitlink files) below the
    top level, which the Seatbelt .git rule covers at any depth."""
    dotenv, nested_git = [], []
    for dirpath, dirnames, filenames in os.walk(workspace):
        here = Path(dirpath)
        if here != workspace and (".git" in dirnames or ".git" in filenames):
            nested_git.append(here / ".git")
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        dotenv += [here / f for f in filenames if is_secret_dotenv(f)]
    return sorted(dotenv), sorted(nested_git)


def _dotenv_files(workspace: Path) -> list[Path]:
    """The .env / .env.* files the Seatbelt profile's dotenv rule would deny."""
    return _walk_workspace(workspace)[0]


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

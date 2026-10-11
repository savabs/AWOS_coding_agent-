"""
work_miner_shell.py — turn a raw shell history file into whole commands and
stable command families for work_miner.

zsh stores a command typed or pasted over several lines as several file lines,
each embedded newline written as a trailing backslash. Reading the file line
by line (v0) turned one pasted script into dozens of "commands" such as "\\",
"echo\\" or "fi\\". This module:

  * rejoins continuation lines (odd trailing backslash), unbalanced quotes and
    heredoc bodies into one logical command;
  * strips the extended-history prefix ": <ts>:<dur>;" and keeps the timestamp
    when present;
  * normalises the command into a family: git subcommand groups, python
    script vs module vs inline, test runs across runners, compound scripts.

Pure functions, no I/O beyond `read_commands(path)`.
"""
from __future__ import annotations

import difflib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

_EXT = re.compile(r"^: (\d{9,11}):\d+;(.*)$", re.S)
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
MAX_QUOTE_JOIN = 30
MAX_HEREDOC_JOIN = 400


@dataclass
class Command:
    text: str               # the whole logical command (may contain newlines)
    ts: Optional[int] = None
    lines: int = 1          # physical history lines it was rebuilt from


def _odd_backslash(line: str) -> bool:
    n = len(line) - len(line.rstrip("\\"))
    return n % 2 == 1


def _quotes_balanced(text: str) -> bool:
    """True when single/double quotes are balanced (ignores escaped and
    quotes inside the other kind, and anything after an unquoted #)."""
    sq = dq = False
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and not sq:
            i += 2
            continue
        if c == "'" and not dq:
            sq = not sq
        elif c == '"' and not sq:
            dq = not dq
        elif c == "#" and not sq and not dq and (i == 0 or text[i - 1] in " \t\n;"):
            nl = text.find("\n", i)
            if nl < 0:
                break
            i = nl
        i += 1
    return not sq and not dq


def _unmetafy(b: bytes) -> str:
    """zsh 'metafies' bytes >= 0x83 in the history file: 0x83 then byte^0x20."""
    if b"\x83" not in b:
        return b.decode("utf-8", errors="ignore")
    out = bytearray()
    it = iter(b)
    for ch in it:
        if ch == 0x83:
            nxt = next(it, None)
            if nxt is not None:
                out.append(nxt ^ 0x20)
        else:
            out.append(ch)
    return out.decode("utf-8", errors="ignore")


def rejoin(lines: list[str]) -> list[Command]:
    """Rebuild logical commands from physical history lines."""
    cmds: list[Command] = []
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        ts = None
        m = _EXT.match(raw)
        if m:
            ts, raw = int(m.group(1)), m.group(2)
        text = raw
        used = 1
        # 1) zsh writes each embedded newline as a trailing backslash; a typed
        #    shell continuation ("cmd \\<enter>") therefore ends in two. Strip
        #    the marker; an odd remainder is a real continuation -> join with a
        #    space, otherwise keep the newline.
        while text.endswith("\\") and i + used < n:
            text = text[:-1]
            nxt = lines[i + used]
            if _odd_backslash(text):
                text = text[:-1].rstrip() + " " + nxt.lstrip()
            else:
                text = text + "\n" + nxt
            used += 1
        # 2) heredoc: swallow lines until the delimiter (if it ever appears)
        hd = _HEREDOC.search(text)
        if hd and not re.search(r"^\s*" + re.escape(hd.group(2)) + r"\s*$", text[hd.end():], re.M):
            delim = hd.group(2)
            for k in range(i + used, min(n, i + used + MAX_HEREDOC_JOIN)):
                if lines[k].strip() == delim:
                    text = "\n".join([text, *lines[i + used:k + 1]])
                    used = k + 1 - i
                    break
        # 3) an open quote continues onto the next lines (bash-style history)
        if not _quotes_balanced(text):
            for k in range(i + used, min(n, i + used + MAX_QUOTE_JOIN)):
                cand = "\n".join([text, *lines[i + used:k + 1]])
                if _quotes_balanced(cand):
                    text, used = cand, k + 1 - i
                    break
        cmds.append(Command(text=text, ts=ts, lines=used))
        i += used
    return cmds


def read_commands(path: Path) -> list[Command]:
    if not path.exists():
        return []
    text = _unmetafy(path.read_bytes())
    return [c for c in rejoin(text.splitlines()) if c.text.strip()]


# ---------------------------------------------------------------- families

_PREFIX_SKIP = {"sudo", "time", "nohup", "env", "command", "builtin", "exec", "caffeinate", "noglob"}
_COMPOUND = {"if", "for", "while", "until", "function", "case", "select", "{", "(", "((", "[[", "do"}
_TEST_RUNNERS = re.compile(r"(^|\s)(pytest|py\.test|unittest|jest|vitest|mocha|cargo test|go test|npm (run )?test|yarn test|pnpm test|make test|tox)\b")

GIT_GROUPS = {
    "inspect": {"status", "log", "diff", "show", "branch", "blame", "reflog", "shortlog", "describe", "ls-files", "grep", "rev-parse"},
    "commit/push": {"add", "commit", "push", "tag", "rm", "mv", "restore", "reset", "revert", "am", "apply"},
    "sync/branch": {"pull", "fetch", "merge", "rebase", "checkout", "switch", "stash", "cherry-pick", "worktree", "clone", "init", "remote", "submodule"},
}

SHELL_FAMILIES = {
    "gh": "github cli",
    "pip": "python packages", "pip3": "python packages", "uv": "python packages", "conda": "python packages", "poetry": "python packages",
    "npm": "node / js", "node": "node / js", "npx": "node / js", "yarn": "node / js", "pnpm": "node / js", "bun": "node / js",
    "brew": "system packages", "apt": "system packages", "apt-get": "system packages", "port": "system packages",
    "cd": "navigate files", "ls": "navigate files", "pwd": "navigate files", "tree": "navigate files", "z": "navigate files",
    "find": "search files", "grep": "search files", "rg": "search files", "fd": "search files", "locate": "search files", "mdfind": "search files",
    "cat": "read files", "less": "read files", "head": "read files", "tail": "read files", "more": "read files", "bat": "read files",
    "vim": "edit files", "vi": "edit files", "nano": "edit files", "code": "edit files", "emacs": "edit files", "sed": "edit files",
    "cp": "manage files", "mv": "manage files", "rm": "manage files", "mkdir": "manage files", "touch": "manage files",
    "chmod": "manage files", "chown": "manage files", "ln": "manage files", "tar": "manage files", "zip": "manage files", "unzip": "manage files",
    "open": "open apps/files",
    "ssh": "remote machines", "scp": "remote machines", "rsync": "remote machines", "mosh": "remote machines",
    "docker": "containers", "docker-compose": "containers", "podman": "containers", "kubectl": "containers",
    "kill": "processes", "killall": "processes", "pkill": "processes", "ps": "processes", "top": "processes", "htop": "processes", "lsof": "processes", "launchctl": "processes",
    "du": "disk / system info", "df": "disk / system info", "diskutil": "disk / system info", "system_profiler": "disk / system info",
    "sw_vers": "disk / system info", "uname": "disk / system info", "sysctl": "disk / system info", "pmset": "disk / system info", "vm_stat": "disk / system info",
    "echo": "print / inspect env", "printf": "print / inspect env", "export": "print / inspect env", "which": "print / inspect env",
    "source": "print / inspect env", "printenv": "print / inspect env", "alias": "print / inspect env", "type": "print / inspect env",
    "clear": "terminal housekeeping", "exit": "terminal housekeeping", "history": "terminal housekeeping", "reset": "terminal housekeeping",
    "claude": "AI coding agent", "opencode": "AI coding agent", "codex": "AI coding agent", "aider": "AI coding agent", "gemini": "AI coding agent", "cursor": "AI coding agent",
    "ollama": "local models", "llama-server": "local models", "llama-cli": "local models", "mlx_lm.generate": "local models", "hf": "local models", "huggingface-cli": "local models",
    "curl": "http requests", "wget": "http requests", "http": "http requests",
    "sqlite3": "databases", "psql": "databases", "mysql": "databases", "redis-cli": "databases",
    "micro": "edit files", "zed": "edit files",
    "bash": "shell script (multi-line)", "zsh": "shell script (multi-line)", "sh": "shell script (multi-line)",
    "ipconfig": "disk / system info", "chflags": "disk / system info", "SetFile": "disk / system info", "tccutil": "disk / system info",
    "systemextensionsctl": "disk / system info", "kextstat": "disk / system info", "ioreg": "disk / system info",
    "whoami": "disk / system info", "hostname": "disk / system info", "networksetup": "disk / system info", "defaults": "disk / system info",
    "make": "build", "cargo": "build", "go": "build", "cmake": "build", "swift": "build", "xcodebuild": "build",
}

# How routine/checkable each shell family is (a prior; owner-tunable).
SHELL_AUTOMATABILITY = {
    "git: inspect": 0.8, "git: commit/push": 0.8, "git: sync/branch": 0.7, "git: other": 0.6, "github cli": 0.8,
    "run tests": 0.9, "python script": 0.6, "python module": 0.6, "python inline": 0.5, "python repl": 0.2,
    "python packages": 0.7, "node / js": 0.6, "system packages": 0.7, "build": 0.7,
    "navigate files": 0.2, "search files": 0.4, "read files": 0.3, "edit files": 0.2, "manage files": 0.5,
    "processes": 0.6, "disk / system info": 0.7, "print / inspect env": 0.4, "terminal housekeeping": 0.0,
    "AI coding agent": 0.5, "local models": 0.7, "http requests": 0.6, "databases": 0.5,
    "remote machines": 0.5, "containers": 0.6, "open apps/files": 0.2, "shell script (multi-line)": 0.5,
}


def _tokens(line: str) -> list[str]:
    try:
        return shlex.split(line, comments=True)
    except ValueError:
        return line.split()


def _first_statement(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s
    return ""


def command_family(text: str) -> str:
    """Normalise one logical command into a family name."""
    first = _first_statement(text)
    if not first:
        return "terminal housekeeping"
    if _TEST_RUNNERS.search(first) or re.search(r"python3?(\.\d+)? -m (pytest|unittest)\b", first):
        return "run tests"
    toks = _tokens(first)
    while toks and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]) or toks[0] in _PREFIX_SKIP):
        toks = toks[1:]
    if not toks:
        return "print / inspect env"
    head = toks[0]
    if head in _COMPOUND or head.endswith("()") or first.startswith(("for ", "if ", "while ")):
        return "shell script (multi-line)"
    tool = head.split("/")[-1]
    if tool == "git":
        args, rest = [], toks[1:]
        while rest:                       # skip global options, incl. ones taking a value
            t = rest.pop(0)
            if t in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
                rest = rest[1:]
            elif not t.startswith("-"):
                args.append(t)
        sub = args[0] if args else ""
        for group, subs in GIT_GROUPS.items():
            if sub in subs:
                return "git: " + group
        return "git: other"
    if re.fullmatch(r"python[0-9.]*|python3", tool):
        rest = toks[1:]
        if "-m" in rest:
            mi = rest.index("-m")
            mod = rest[mi + 1] if mi + 1 < len(rest) else ""
            if mod in ("pip", "venv", "ensurepip"):
                return "python packages"
            return "python module"
        if "-c" in rest:
            return "python inline"
        if any(t.endswith(".py") for t in rest):
            return "python script"
        return "python repl"
    if tool.endswith(".py"):
        return "python script"
    if tool.endswith(".sh"):
        return "shell script (multi-line)"
    if tool.startswith("!"):
        return "terminal housekeeping"      # history expansion
    if tool in SHELL_FAMILIES:
        return SHELL_FAMILIES[tool]
    if len(tool) >= 4:                       # typos: "clea", "gti"-style slips
        close = difflib.get_close_matches(tool.lower(), SHELL_FAMILIES, n=1, cutoff=0.8)
        if close:
            return SHELL_FAMILIES[close[0]]
    if "\n" in text.strip():
        return "shell script (multi-line)"
    return "other: " + tool[:20]


def shell_automatability(family: str) -> float:
    return SHELL_AUTOMATABILITY.get(family, 0.4)


def families(cmds: Iterable[Command]) -> list[tuple[Command, str]]:
    return [(c, command_family(c.text)) for c in cmds]

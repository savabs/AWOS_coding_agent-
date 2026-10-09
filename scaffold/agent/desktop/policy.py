"""
policy.py — deterministic, provenance-keyed sink policy for side effects.

See docs/specs/computer_use_foundations.md (T10). No LLM, no content
inspection: the verdict depends only on the action class, its target, who
initiated it, and where its arguments came from. Ideas from Progent
(privilege DSL, default deny), CaMeL (data provenance; untrusted data may not
choose a sink) and AgentSpec (runtime rules). The full CaMeL planner/
quarantine split is NOT used (costs weak planners ~21pp).

Provenance
  OWNER      the owner's own instruction (the goal text, an explicit grant)
  AGENT      derived by the agent from owner input and trusted tool output
  UNTRUSTED  anything read from the screen, a web page, a document, an
             email, an AX value, a file the owner did not write

The action carries the provenance of who *initiated* it (which input made the
agent decide to do this) and of each *argument* (where the recipient, path,
amount, URL came from). Effective trust is the minimum over both.

Decision order (first match wins):
  1. READ                         -> allow (reading is not a sink)
  2. WRITE_SANDBOX inside sandbox -> allow; outside -> treated as WRITE_OUTSIDE
  3. initiator UNTRUSTED and class is a sink above WRITE_SANDBOX -> deny
     (on-screen text can never trigger send/delete/pay/push/egress/write-out)
  4. PAYMENT / CREDENTIAL / OPAQUE -> ask even with a grant (critical-point stop)
  5. a matching owner grant:
       args all trusted          -> allow
       any arg UNTRUSTED         -> ask (untrusted data chose part of a sink)
  6. no grant:
       irreversible class        -> deny (default deny)
       WRITE_OUTSIDE             -> ask
  7. shell commands only (decide_command): when the command's provenance is
     UNTRUSTED (initiator or any argument), an ALLOW from 1-6 also needs the
     read-only allowlist (untrusted_shell_violation); otherwise -> ask.
     A DENY from 1-6 always stands.

Every decision is appended to an audit log (JSONL) when one is attached.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, List, Optional, Sequence


class ActionClass(str, Enum):
    READ = "read"
    WRITE_SANDBOX = "write_sandbox"
    WRITE_OUTSIDE = "write_outside"
    NETWORK_EGRESS = "network_egress"
    SEND = "send"            # email, message, post, comment, form submit
    DELETE = "delete"
    CREDENTIAL = "credential"
    PAYMENT = "payment"
    GIT_PUSH = "git_push"
    OPAQUE = "opaque_code"   # inline/stdin code the classifier cannot see into


IRREVERSIBLE = {
    ActionClass.NETWORK_EGRESS, ActionClass.SEND, ActionClass.DELETE,
    ActionClass.CREDENTIAL, ActionClass.PAYMENT, ActionClass.GIT_PUSH,
    ActionClass.OPAQUE,
}
#: OPAQUE code could be any sink, so it is treated like the worst one: it
#: always confirms (never allowed silently, not even by a grant), and an
#: untrusted initiator is denied by R3 before this applies.
ALWAYS_ASK = {ActionClass.PAYMENT, ActionClass.CREDENTIAL, ActionClass.OPAQUE}


class Provenance(str, Enum):
    OWNER = "owner"
    AGENT = "agent"
    UNTRUSTED = "untrusted"


_TRUST_RANK = {Provenance.UNTRUSTED: 0, Provenance.AGENT: 1, Provenance.OWNER: 2}


def least_trusted(sources: Iterable[Provenance]) -> Provenance:
    srcs = [Provenance(s) for s in sources]
    return min(srcs, key=_TRUST_RANK.__getitem__) if srcs else Provenance.OWNER


class Verdict(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class Action:
    cls: ActionClass
    target: str = ""                     # path, host, recipient, remote, ...
    initiator: Provenance = Provenance.AGENT
    arg_sources: List[Provenance] = field(default_factory=list)
    description: str = ""

    def __post_init__(self):
        self.cls = ActionClass(self.cls)
        self.initiator = Provenance(self.initiator)
        self.arg_sources = [Provenance(s) for s in self.arg_sources]


@dataclass
class Grant:
    """An explicit owner permission: class + target glob. Grants come from
    the owner only; a grant built from untrusted text is rejected."""
    cls: ActionClass
    target: str = "*"
    id: str = ""
    provenance: Provenance = Provenance.OWNER

    def __post_init__(self):
        self.cls = ActionClass(self.cls)
        self.provenance = Provenance(self.provenance)
        if self.provenance is not Provenance.OWNER:
            raise ValueError("grants must come from the owner")
        if not self.id:
            self.id = f"{self.cls.value}:{self.target}"

    def matches(self, action: Action) -> bool:
        return action.cls is self.cls and fnmatch.fnmatchcase(action.target, self.target)


@dataclass
class Decision:
    verdict: Verdict
    reason: str
    rule: str
    action: Action
    grant_id: Optional[str] = None

    def to_record(self) -> dict:
        a = self.action
        return {
            "ts": round(time.time(), 3),
            "verdict": self.verdict.value,
            "rule": self.rule,
            "reason": self.reason,
            "action_class": a.cls.value,
            "target": a.target,
            "initiator": a.initiator.value,
            "arg_sources": [s.value for s in a.arg_sources],
            "description": a.description[:200],
            "grant_id": self.grant_id,
        }


class AuditLog:
    """Append-only JSONL. One line per decision (see to_record for fields)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def append(self, decision: Decision) -> None:
        self.append_record(decision.to_record())

    def append_record(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def read(self) -> List[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]


def _inside(path: str, root: Path) -> bool:
    # Unexpanded shell text ($VAR, $(...), `...`, a NUL placeholder) has no
    # known location: fail closed (treated as outside the sandbox).
    if any(c in path for c in ("$", "`", "\x00")):
        return False
    try:
        p = Path(os.path.realpath(os.path.expanduser(path) if os.path.isabs(os.path.expanduser(path))
                                  else root / path))
        return p == root or root in p.parents
    except (OSError, ValueError):
        return False


class SinkPolicy:
    def __init__(self, sandbox: Path | str, grants: Sequence[Grant] = (),
                 audit: Optional[AuditLog] = None):
        self.sandbox = Path(os.path.realpath(sandbox))
        self.grants = list(grants)
        self.audit = audit

    def decide(self, action: Action) -> Decision:
        d = self._decide(action)
        if self.audit is not None:
            self.audit.append(d)
        return d

    def _decide(self, a: Action) -> Decision:
        if a.cls is ActionClass.READ:
            return Decision(Verdict.ALLOW, "reads are not sinks", "R1-read", a)

        if a.cls is ActionClass.WRITE_SANDBOX:
            if _inside(a.target, self.sandbox):
                return Decision(Verdict.ALLOW, "write inside sandbox", "R2-sandbox", a)
            a = Action(ActionClass.WRITE_OUTSIDE, a.target, a.initiator,
                       a.arg_sources, a.description)

        if a.initiator is Provenance.UNTRUSTED:
            return Decision(Verdict.DENY,
                            f"{a.cls.value} initiated by untrusted screen/web text",
                            "R3-untrusted-trigger", a)

        if a.cls in ALWAYS_ASK:
            g = self._grant(a)
            return Decision(Verdict.ASK, f"critical point: {a.cls.value} always confirms",
                            "R4-critical-point", a, g.id if g else None)

        g = self._grant(a)
        if g is not None:
            if least_trusted(a.arg_sources) is Provenance.UNTRUSTED:
                return Decision(Verdict.ASK,
                                f"granted {a.cls.value}, but an argument came from untrusted text",
                                "R5-untrusted-arg", a, g.id)
            return Decision(Verdict.ALLOW, f"owner grant {g.id}", "R5-grant", a, g.id)

        if a.cls in IRREVERSIBLE:
            return Decision(Verdict.DENY, f"{a.cls.value} not granted by owner (default deny)",
                            "R6-default-deny", a)
        return Decision(Verdict.ASK, "write outside sandbox without a grant",
                        "R6-write-outside", a)

    def _grant(self, a: Action) -> Optional[Grant]:
        return next((g for g in self.grants if g.matches(a)), None)


# ---------------------------------------------------------------------------
# shell command -> action class (rung 1 of the ladder)
# ---------------------------------------------------------------------------
#
# The classifier splits the command the way a POSIX shell would, then
# classifies every simple command it finds, recursively:
#   * chains and pipes (| || && ; & newline, subshell parens) -> each segment
#   * $(...), `...`, <(...), >(...) -> the inner command is classified too
#     (single-quoted text is literal and is not scanned)
#   * wrappers (env, sudo, doas, xargs, nohup, time, nice, timeout, stdbuf,
#     caffeinate, command, exec, builtin) -> the wrapped command; sudo/doas
#     also count as a write outside the sandbox
#   * shells with -c, and eval -> the inline script, parsed recursively
#   * find -exec/-ok -> the exec'd command; find -delete -> DELETE
#   * trap HANDLER, watch CMD, busybox/toybox APPLET -> the inner command
#   * sed -i / perl -pi / ruby -i -> writes to each edited file
#   * git push -> GIT_PUSH on the real remote (--repo counts) plus DELETE for
#     force/delete/mirror/prune/+ref/:ref, so a push grant never covers those
# Write targets the classifier cannot resolve ($VAR, ${VAR}, $(...), `...`)
# are WRITE_OUTSIDE, and after a cd/pushd that leaves (or may leave) the
# sandbox every later relative write in the chain is WRITE_OUTSIDE too.
# Code the classifier cannot see into is OPAQUE, the most dangerous class:
# the policy asks for it at best and denies it from untrusted initiators.
#   * interpreter inline code: python -c, node -e, perl/ruby -e, php -r,
#     osascript -e, awk programs that call system()/pipes/redirects
#   * a shell or interpreter reading code from stdin (`curl x | sh`)
#   * a dynamic program name ($CMD, or $(...) as the command)
#   * source/. of a file (except a relative */bin/activate), git -c keys or
#     options that name a command (core.sshCommand, alias.*=!, --upload-pack,
#     ...), env vars that name one (GIT_SSH_COMMAND, PAGER, LD_PRELOAD, ...),
#     sed's e command / s///e flag
#   * unbalanced quotes / substitutions, or nesting deeper than _MAX_DEPTH
# Over-approximation is deliberate: a quoted ';' or a $( inside double quotes
# can only make the verdict stricter, never looser.

_EGRESS = {"curl", "wget", "nc", "ncat", "netcat", "ssh", "scp", "rsync", "sftp", "ftp",
           "telnet", "socat"}
_DELETE = {"rm", "rmdir", "unlink", "trash", "shred", "srm"}
_WRITE = {"mv", "cp", "touch", "mkdir", "tee", "ln", "chmod", "chown", "plutil",
          "sqlite3", "defaults", "dd", "truncate", "install"}
_READ = {"cat", "ls", "head", "tail", "grep", "mdls", "stat", "wc", "file",
         "echo", "printf", "pwd", "true", "false", "which", "whoami", "date"}
_SEND_RE = re.compile(r"\b(send|reply|post|submit|tweet)\b", re.I)

_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish", "csh", "tcsh", "ash"}
#: interpreter -> flags that introduce inline code
_INLINE_FLAGS = {
    "python": {"-c"}, "node": {"-e", "-p", "--eval", "--print"},
    "ruby": {"-e"}, "perl": {"-e", "-E"}, "php": {"-r"}, "osascript": {"-e"},
    "lua": {"-e"}, "pwsh": {"-c", "-command", "-Command"},
    "powershell": {"-c", "-command", "-Command"}, "Rscript": {"-e"},
}
_AWK = {"awk", "gawk", "mawk", "nawk"}
#: wrapper -> its options that take a separate value argument
_WRAPPERS = {
    "env": {"-u", "-C", "--unset", "--chdir"},
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U", "-D", "-R", "-T"},
    "doas": {"-u", "-C"},
    "xargs": {"-I", "-L", "-n", "-P", "-s", "-E", "-d", "-a", "--max-args",
              "--max-procs", "--delimiter", "--arg-file", "-J", "-R"},
    "nohup": set(), "time": {"-f", "-o"}, "nice": {"-n"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"}, "caffeinate": {"-t", "-w"},
    "command": set(), "exec": {"-a"}, "builtin": set(), "ionice": {"-c", "-n", "-p"},
    "busybox": set(), "toybox": set(),
}
#: watch runs its arguments through `sh -c`; options that take a value
_WATCH_OPTS = {"-n", "--interval", "-q", "--equexit"}
#: git config keys (lower-case) whose value is a command git will run
_GIT_CODE_KEYS = {
    "core.sshcommand", "core.pager", "core.editor", "core.fsmonitor",
    "core.hookspath", "core.askpass", "core.gitproxy", "sequence.editor",
    "diff.external", "gpg.program", "credential.helper", "uploadpack.packobjectshook",
    "interactive.difffilter",
}
_GIT_CODE_SUFFIXES = (".command", ".cmd", ".helper", ".program", ".textconv",
                      ".uploadpack", ".receivepack", ".clean", ".smudge", ".process",
                      ".driver", ".tool", ".sshcommand")
#: git subcommand options whose value is a command run on the remote side
_GIT_CODE_OPTS = ("--upload-pack", "--receive-pack", "--exec")
#: environment variables whose value is a command (or code) a program will run
_CODE_ENV = {"GIT_SSH_COMMAND", "GIT_SSH", "GIT_EXTERNAL_DIFF", "GIT_PAGER", "GIT_EDITOR",
             "GIT_ASKPASS", "GIT_PROXY_COMMAND", "SSH_ASKPASS", "PAGER", "EDITOR", "VISUAL",
             "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "BASH_ENV", "ENV", "PROMPT_COMMAND",
             "PERL5OPT", "NODE_OPTIONS", "PYTHONSTARTUP"}
_GIT_CODE_ENV = {"GIT_EXEC_PATH", "GIT_TEMPLATE_DIR"}
#: env vars that make `git push` talk to a different repository's remotes
_GIT_REDIRECT_ENV = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_NAMESPACE"}
#: git config keys (lower-case) that change where a push goes
_GIT_REDIRECT_KEY_RE = re.compile(
    r"^(remote\..*\.(url|pushurl|receivepack|uploadpack|mirror)|url\..*\.(insteadof|pushinsteadof)"
    r"|remote\.pushdefault|branch\..*\.(pushremote|remote)|push\.default)$")
#: target prefix for a git push whose destination the command may have changed
REDIRECTED = "redirected:"
#: git push options that rewrite or remove remote history
_PUSH_DESTRUCTIVE = {"-f", "--force", "--force-with-lease", "--force-if-includes",
                     "-d", "--delete", "--mirror", "--prune", "--all"}
_PUSH_VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
_SHELL_KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until",
                   "!", "{", "}", "case", "esac"}
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SUBST = "\x00SUBST\x00"          # placeholder left where a $(...) was
_HARMLESS_SINKS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}
_OPS = "();<>|&\n"
_MAX_DEPTH = 6


def _opaque(why: str) -> tuple:
    return (ActionClass.OPAQUE, why.replace(_SUBST, "$(...)")[:120])


def _is_dynamic(target: str) -> bool:
    """A target the shell expands at run time: $VAR, ${VAR}, $(...), `...`."""
    return _SUBST in target or "$" in target or "`" in target


def _write(target: str) -> tuple:
    """A write to `target`. A dynamic target has no known location, so it is a
    write outside the sandbox (fails closed), never a sandbox write."""
    if _is_dynamic(target):
        return (ActionClass.WRITE_OUTSIDE, target.replace(_SUBST, "$(...)"))
    return (ActionClass.WRITE_SANDBOX, target)


def _code_assign(word: str) -> bool:
    if not _ASSIGN_RE.match(word):
        return False
    name = word.split("=", 1)[0]
    # GIT_CONFIG_COUNT/KEY_n/VALUE_n/PARAMETERS/GLOBAL/SYSTEM can set any git
    # config key, including the ones that name a command
    return name in _CODE_ENV or name.startswith("GIT_CONFIG") or name in _GIT_CODE_ENV


def _git_code_config(kv: str) -> bool:
    key, _, val = kv.partition("=")
    key = key.lower()
    return (val.lstrip().startswith("!") or key in _GIT_CODE_KEYS
            or key.startswith(("pager.", "include.", "includeif."))   # include pulls in any key
            or key.endswith(_GIT_CODE_SUFFIXES))


def _git_redirect_key(key: str) -> bool:
    return bool(_GIT_REDIRECT_KEY_RE.match(key.split("=", 1)[0].lower()))


def _sed_targets(args: List[str]) -> Optional[List[str]]:
    """Files `sed -i` edits in place, or None when it is not in-place."""
    in_place = any(a.startswith("--in-place") or
                   (not a.startswith("--") and _short_flag_has(a, "i")) for a in args)
    if not in_place:
        return None
    pos: List[str] = []
    script_given, k = False, 0
    while k < len(args):
        a = args[k]
        if a in ("-e", "-f", "--expression", "--file", "-l", "--line-length"):
            script_given = script_given or a in ("-e", "-f", "--expression", "--file")
            k += 2
            continue
        if a.startswith(("--expression=", "--file=")):
            script_given = True
        elif a and (not a.startswith("-") or a == "-"):    # "" = BSD `-i ''` suffix
            pos.append(a)
        k += 1
    return pos if script_given else pos[1:]


def _sed_scripts(args: List[str]) -> Optional[List[str]]:
    """The sed script texts in argv, or None when a script comes from a file
    (-f) the classifier cannot read. Without -e the first operand is the
    script; an ambiguous `-i<suffix>` cluster scans both readings."""
    scripts: List[str] = []
    operands: List[str] = []
    explicit = ambiguous = False
    k = 0
    while k < len(args):
        a = args[k]
        if a == "--":
            operands += args[k + 1:]
            break
        if a in ("-f", "--file") or a.startswith("--file="):
            return None
        if a in ("-e", "--expression"):
            explicit = True
            if k + 1 < len(args):
                scripts.append(args[k + 1])
            k += 2
            continue
        if a.startswith("--expression="):
            explicit = True
            scripts.append(a.split("=", 1)[1])
        elif a in ("-l", "--line-length"):
            k += 2
            continue
        elif a.startswith("-") and not a.startswith("--") and len(a) > 1:
            cl = a[1:]
            if "i" in cl and cl.index("i") < len(cl) - 1:
                ambiguous = True                 # GNU -iSUFFIX vs BSD -i -e...
            for j, ch in enumerate(cl):
                if ch == "f":
                    return None
                if ch == "e":
                    explicit = True
                    rest = cl[j + 1:]
                    if rest:
                        scripts.append(rest)
                    elif k + 1 < len(args):
                        scripts.append(args[k + 1])
                        k += 1
                    break
        elif not a.startswith("-") or a == "-":
            operands.append(a)
        k += 1
    if (not explicit or ambiguous) and operands:
        scripts.append(operands[0])
    return scripts


def _sed_scan(script: str):
    """(runs_code, write_targets) for one sed script. GNU sed runs commands
    with `e` and the s///e flag and writes files with `w`/`W` and the s///w
    flag. Anything the scanner does not understand counts as running code."""
    n, i = len(script), 0
    writes: List[str] = []

    def to_eol(j: int) -> int:
        while j < n and script[j] != "\n":
            j += 1
        return j

    def delimited(j: int, d: str) -> int:      # index after the closing delimiter
        while j < n and script[j] != d:
            j += 2 if script[j] == "\\" else 1
        if j >= n:
            raise ValueError("unterminated")
        return j + 1

    try:
        while i < n:
            ch = script[i]
            if ch in " \t\n;{}":
                i += 1
                continue
            # address: numbers, $, /re/, \cREc, ranges, steps, negation
            while i < n:
                ch = script[i]
                if ch.isdigit() or ch in "$,~+! \t":
                    i += 1
                elif ch == "/":
                    i = delimited(i + 1, "/")
                    while i < n and script[i] in "IM":
                        i += 1
                elif ch == "\\" and i + 1 < n:
                    i = delimited(i + 2, script[i + 1])
                    while i < n and script[i] in "IM":
                        i += 1
                else:
                    break
            if i >= n:
                break
            ch = script[i]
            if ch in "{};\n":
                i += 1
                continue
            if ch == "e":
                return True, writes
            if ch in "wW":
                end = to_eol(i + 1)
                writes.append(script[i + 1:end].strip())
                i = end
            elif ch in "sy":
                d = script[i + 1]
                if d in "\n\\":
                    return True, writes
                i = delimited(delimited(i + 2, d), d)
                if ch == "s":
                    while i < n and script[i] not in ";\n}":
                        f = script[i]
                        if f == "e":
                            return True, writes
                        if f == "w":
                            end = to_eol(i + 1)
                            writes.append(script[i + 1:end].strip())
                            i = end
                            break
                        if not (f.isalnum() or f in " \t"):
                            return True, writes
                        i += 1
            elif ch in "aic":
                i = to_eol(i + 1)               # GNU one-line text
            elif ch in "rRbtT:":
                stops = "\n" if ch in "rR:" else ";\n}"
                j = i + 1
                while j < n and script[j] not in stops:
                    j += 1
                i = j
            elif ch in "qQlL":
                i += 1
                while i < n and (script[i].isdigit() or script[i] in " \t"):
                    i += 1
            elif ch == "#":
                i = to_eol(i)
            elif ch in "pdnNgGhHxz=FDP":
                i += 1
            else:
                return True, writes             # not understood: fail closed
    except (ValueError, IndexError):
        return True, writes
    return False, writes


def _sed_effects(args: List[str]):
    """(runs_code, write_targets) over every script in a sed argv."""
    scripts = _sed_scripts(args)
    if scripts is None:
        return True, []
    writes: List[str] = []
    for sc in scripts:
        code, w = _sed_scan(sc)
        if code:
            return True, writes
        writes += [t for t in w if t not in _HARMLESS_SINKS]
    return False, writes


def _interp_key(prog: str) -> Optional[str]:
    if re.fullmatch(r"python[\d.]*", prog) or prog in ("pypy", "pypy3"):
        return "python"
    m = re.fullmatch(r"(nodejs|node|ruby|perl|php|lua)[\d.]*", prog)
    if m:
        return "node" if m.group(1) == "nodejs" else m.group(1)
    return prog if prog in _INLINE_FLAGS else None


def _extract_substitutions(cmd: str):
    """Replace every $(...), `...`, <(...), >(...) outside single quotes with a
    placeholder. Returns (rewritten, [inner commands]), or None if unbalanced."""
    out, inners, i, n = [], [], 0, len(cmd)
    in_single = in_double = False
    while i < n:
        ch = cmd[i]
        if in_single:
            out.append(ch)
            in_single = ch != "'"
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            out.append(cmd[i:i + 2])
            i += 2
            continue
        two = cmd[i:i + 2]
        if two == "$(" or (two in ("<(", ">(") and not in_double):
            depth, j = 1, i + 2
            while j < n and depth:
                if cmd[j] == "\\":
                    j += 2
                    continue
                depth += {"(": 1, ")": -1}.get(cmd[j], 0)
                j += 1
            if depth:
                return None
            inners.append(cmd[i + 2:j - 1])
            out.append(_SUBST)
            i = j
            continue
        if ch == "`":
            j = i + 1
            while j < n and cmd[j] != "`":
                j += 2 if cmd[j] == "\\" else 1
            if j >= n:
                return None
            inners.append(cmd[i + 1:j])
            out.append(_SUBST)
            i = j + 1
            continue
        if ch == "'" and not in_double:
            in_single = True
        elif ch == '"':
            in_double = not in_double
        out.append(ch)
        i += 1
    if in_single or in_double:
        return None
    return "".join(out), inners


def _tokens(cmd: str) -> List[str]:
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=_OPS)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    return list(lex)


def _is_op(tok: str) -> bool:
    return bool(tok) and all(c in _OPS for c in tok)


def _segments(tokens: List[str]):
    """Split tokens into simple commands. Yields (argv, redirect_out_targets).
    Heredoc bodies are skipped (they are data for the command, which is
    classified on its own: a shell or interpreter reading them is OPAQUE)."""
    argv: List[str] = []
    outs: List[str] = []
    heredocs: List[str] = []
    body_end: Optional[str] = None
    line: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if body_end is not None:
            if tok == "\n":
                if line == [body_end]:
                    body_end = heredocs.pop(0) if heredocs else None
                line = []
            else:
                line.append(tok)
            i += 1
            continue
        if not _is_op(tok):
            argv.append(tok)
            i += 1
            continue
        if ">" in tok:                                  # > >> &> >| >&
            if argv and argv[-1].isdigit():
                argv.pop()                              # the fd in 2>file
            if tok.endswith("&") and (nxt.isdigit() or nxt == "-"):
                i += 2                                  # 2>&1: fd dup, no file
                continue
            if nxt and not _is_op(nxt):
                if nxt not in _HARMLESS_SINKS:
                    outs.append(nxt)
                i += 2
                continue
            i += 1
            continue
        if set(tok) == {"<"}:                           # < << <<< (input, not an arg)
            if tok == "<<" and nxt:
                heredocs.append(nxt.lstrip("-"))
            i += 2 if nxt and not _is_op(nxt) else 1
            continue
        if argv or outs:                                # | || && ; & ( ) newline
            yield argv, outs
        argv, outs = [], []
        if "\n" in tok and heredocs:
            body_end = heredocs.pop(0)
            line = []
        i += 1
    if argv or outs:
        yield argv, outs


def _skip_wrapper_opts(prog: str, args: List[str]) -> List[str]:
    """Drop a wrapper's own options; return the wrapped argv."""
    takes = _WRAPPERS[prog]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if prog == "env" and _ASSIGN_RE.match(a):
            i += 1
            continue
        if a.startswith("-") and len(a) > 1:
            i += 2 if a in takes else 1
            continue
        break
    rest = args[i:]
    if prog == "timeout" and rest:
        rest = rest[1:]                                 # the duration
    return rest


def _short_flag_has(arg: str, letter: str) -> bool:
    return arg.startswith("-") and not arg.startswith("--") and letter in arg[1:]


def _classify_argv(argv: List[str], depth: int, env: frozenset = frozenset()) -> List[tuple]:
    if depth > _MAX_DEPTH:
        return [_opaque("nesting too deep")]
    while argv and (argv[0] in _SHELL_KEYWORDS or _ASSIGN_RE.match(argv[0])):
        if _code_assign(argv[0]):
            return [_opaque(f"{argv[0].split('=', 1)[0]} names a command to run")]
        if _ASSIGN_RE.match(argv[0]):
            env = env | {argv[0].split("=", 1)[0]}
        argv = argv[1:]
    if not argv:
        return []
    if _SUBST in argv[0] or "$" in argv[0]:
        return [_opaque(f"dynamic program {argv[0]!r}")]
    prog = os.path.basename(argv[0])
    args = argv[1:]
    out: List[tuple] = []

    if prog in _WRAPPERS:
        if prog == "env":
            if any(_code_assign(a) for a in args):
                return [_opaque("env sets a variable that names a command")]
            env = env | {a.split("=", 1)[0] for a in args if _ASSIGN_RE.match(a)}
            for k, a in enumerate(args):                # env -S "cmd args" re-splits
                if a in ("-S", "--split-string") and k + 1 < len(args):
                    return _classify(" ".join(args[k + 1:]), depth + 1)
        if prog in ("sudo", "doas"):
            out.append((ActionClass.WRITE_OUTSIDE, f"{prog}:/"))
            if any(a in ("-s", "-i", "--shell", "--login") for a in args):
                out.append(_opaque(f"{prog} interactive shell"))
        inner = _skip_wrapper_opts(prog, args)
        if not inner:
            if prog in ("xargs", "env"):
                out.append((ActionClass.READ, "."))     # xargs echo / env listing
            return out
        return out + _classify_argv(inner, depth + 1, env)

    if prog in ("source", "."):
        script = args[0] if args else ""
        if (re.fullmatch(r"(\.?[\w-]+/)*bin/activate(\.\w+)?", script)
                and ".." not in script.split("/")):
            return [(ActionClass.WRITE_SANDBOX, ".")]   # a relative venv activate
        return [_opaque(f"{prog} runs {script or 'a file'} in the current shell")]

    if prog == "trap":
        handler = next((a for a in args if a != "--"), "")
        if not handler or handler in ("-", "-l", "-p") or len(args) < 2:
            return [(ActionClass.READ, ".")]
        if _SUBST in handler:
            return [_opaque("trap of substituted text")]
        return _classify(handler, depth + 1)

    if prog == "watch":
        k = 0
        while k < len(args) and args[k].startswith("-") and args[k] != "--":
            k += 2 if args[k] in _WATCH_OPTS else 1
        if k < len(args) and args[k] == "--":
            k += 1
        inner = args[k:]
        if not inner:
            return [(ActionClass.READ, ".")]
        if any(_SUBST in a for a in inner):
            return [_opaque("watch of substituted text")]
        return _classify(" ".join(inner), depth + 1)

    if prog == "sed":
        code, writes = _sed_effects(args)
        if code:
            return [_opaque("sed script runs commands (or cannot be read)")]
        out = [_write(t) if t else (ActionClass.WRITE_OUTSIDE, "sed w ?") for t in writes]
        targets = _sed_targets(args)
        if targets is not None:
            out += [_write(t) for t in targets]
        return out or [(ActionClass.WRITE_SANDBOX, ".")]

    if prog == "eval":
        if any(_SUBST in a for a in args):
            return [_opaque("eval of substituted text")]
        return _classify(" ".join(args), depth + 1)

    if prog in _SHELLS:
        for k, a in enumerate(args):
            if _short_flag_has(a, "c"):
                script = next((x for x in args[k + 1:] if not x.startswith("-")), None)
                if script is None:
                    return [_opaque(f"{prog} -c without a script")]
                if _SUBST in script:
                    return [_opaque(f"{prog} -c of substituted text")]
                return _classify(script, depth + 1)
            if _short_flag_has(a, "s"):
                return [_opaque(f"{prog} reading code from stdin")]
        if not [a for a in args if not a.startswith("-")]:
            return [_opaque(f"{prog} reading code from stdin")]
        return [(ActionClass.WRITE_SANDBOX, ".")]       # runs a script file

    key = _interp_key(prog)
    if key is not None:
        flags = _INLINE_FLAGS.get(key, set())
        letters = {f[1] for f in flags if len(f) == 2}
        inline = any(a in flags or (_short_flag_has(a, "") and set(a[1:]) & letters)
                     for a in args)
        if key == "osascript" and _SEND_RE.search(" ".join(args)):
            out.append((ActionClass.SEND, "applescript"))
        if inline:
            return out + [_opaque(f"{prog} inline code")]
        if "-" in args or not [a for a in args if not a.startswith("-")]:
            return out + [_opaque(f"{prog} reading code from stdin")]
        if key in ("perl", "ruby") and any(_short_flag_has(a, "i") for a in args):
            files = [a for a in args if not a.startswith("-")][1:]   # after the script
            out += [_write(t) for t in files]
        return out or [(ActionClass.WRITE_SANDBOX, ".")]

    if prog in _AWK:
        text = " ".join(a for a in args if not a.startswith("-"))
        if re.search(r"\bsystem\s*\(|\|\s*\"|\bgetline\b|print[^;}]*>", text):
            return [_opaque(f"{prog} program runs commands or writes files")]
        return [(ActionClass.WRITE_SANDBOX, ".")]

    if prog == "find":
        path = next((a for a in args if not a.startswith(("-", "(", "!", ")"))), ".")
        out.append((ActionClass.READ, path))
        for k, a in enumerate(args):
            if a == "-delete":
                out.append((ActionClass.DELETE, path))
            if a in ("-exec", "-execdir", "-ok", "-okdir"):
                inner: List[str] = []
                for x in args[k + 1:]:
                    if x in (";", "+", "\\;"):
                        break
                    inner.append(x)
                out += _classify_argv(inner, depth + 1) or [_opaque("find -exec")]
        return out

    if prog == "git":
        redirected = bool(env & _GIT_REDIRECT_ENV)
        k = 0
        while k < len(args):                            # git's global options
            a = args[k]
            if a == "-c" and k + 1 < len(args):
                if _git_code_config(args[k + 1]):
                    return [_opaque(f"git -c {args[k + 1].split('=', 1)[0]} runs a command")]
                redirected = redirected or _git_redirect_key(args[k + 1])
            if a.startswith("--config-env"):
                return [_opaque("git --config-env sets config from the environment")]
            if a.startswith("--exec-path="):
                return [_opaque("git --exec-path runs git commands from another directory")]
            if a.split("=", 1)[0] in ("--git-dir", "--work-tree", "--namespace"):
                redirected = True                   # another repository's remotes
            if a == "-C" and k + 1 < len(args):
                d = args[k + 1]
                if _is_dynamic(d) or d.startswith(("/", "~")) or ".." in d.split("/"):
                    redirected = True
            if a in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
                k += 2
                continue
            if not a.startswith("-"):
                break
            k += 1
        sub, sargs = (args[k], args[k + 1:]) if k < len(args) else ("", [])
        if any(a.split("=", 1)[0] in _GIT_CODE_OPTS for a in sargs) or (
                sub in ("clone", "fetch", "pull", "ls-remote", "archive") and "-u" in sargs):
            return [_opaque(f"git {sub} with a custom remote command")]
        if sub == "push":
            pushes = _git_push(sargs)
            if redirected:
                pushes = [(c, REDIRECTED + t) if c is ActionClass.GIT_PUSH else (c, t)
                          for c, t in pushes]
            return pushes
        if sub == "config":
            return _git_config(sargs)
        if sub == "remote" and sargs and sargs[0] in (
                "add", "set-url", "rename", "set-head", "set-branches"):
            return [(ActionClass.WRITE_OUTSIDE, f"git-config:remote {sargs[0]}")]
        return [(ActionClass.WRITE_SANDBOX, ".")]

    pos = [x for x in args if not x.startswith("-")]
    if prog in _EGRESS:
        host = next((x for x in pos if re.match(r"[a-z]+://|[\w.-]+@|[\w-]+\.\w", x)), "")
        out.append((ActionClass.NETWORK_EGRESS, host))
    elif prog in _DELETE:
        out += [(ActionClass.DELETE, x) for x in pos] or [(ActionClass.DELETE, "")]
    elif prog == "security" and any(x.startswith(("find-", "dump-")) for x in pos):
        out.append((ActionClass.CREDENTIAL, "keychain"))
    elif prog == "open" and any(x.startswith(("http://", "https://", "mailto:")) for x in pos):
        out.append((ActionClass.NETWORK_EGRESS, pos[0]))
    elif prog in _WRITE:
        if prog == "dd":
            targets = [x[3:] for x in pos if x.startswith("of=")]
        else:
            targets = pos[-1:] if prog in ("mv", "cp", "ln", "install") else pos[:1]
        out += [_write(t) for t in targets] or [(ActionClass.WRITE_SANDBOX, ".")]
    elif prog in _READ:
        out.append((ActionClass.READ, pos[0] if pos else "."))
    else:
        out.append((ActionClass.WRITE_SANDBOX, "."))
    return out


_GIT_CONFIG_VALUE_OPTS = {"-f", "--file", "--blob", "--type", "--default", "--comment",
                          "--value", "--url"}


def _git_config(args: List[str]) -> List[tuple]:
    """`git config` writes. A key that names a command is OPAQUE; a key that
    changes where pushes go, or a write to a config outside the repo, is
    WRITE_OUTSIDE `git-config:<key>` (asks; a later push in the same chain is
    treated as redirected)."""
    pos: List[str] = []
    scope_outside = reading = False
    k = 0
    while k < len(args):
        a = args[k]
        name = a.split("=", 1)[0]
        if a in ("-e", "--edit"):
            return [_opaque("git config --edit opens an editor")]
        if name in ("--global", "--system", "-f", "--file"):
            scope_outside = True
        if name.startswith("--get") or a in ("-l", "--list"):
            reading = True
        if a in _GIT_CONFIG_VALUE_OPTS:
            k += 2
            continue
        if not a.startswith("-"):
            pos.append(a)
        k += 1
    if pos and pos[0] in ("get", "list"):
        reading = True
    if pos and pos[0] in ("set", "unset", "get", "list", "edit", "rename-section",
                          "remove-section"):
        if pos[0] == "edit":
            return [_opaque("git config edit opens an editor")]
        pos = pos[1:]
    if reading or not pos:
        return [(ActionClass.READ, "git-config")]
    key, val = pos[0], (pos[1] if len(pos) > 1 else "")
    if _git_code_config(f"{key}={val}"):
        return [_opaque(f"git config {key} names a command")]
    if scope_outside or _git_redirect_key(key):
        return [(ActionClass.WRITE_OUTSIDE, f"git-config:{key}")]
    return [(ActionClass.WRITE_SANDBOX, ".git/config")]


def _git_push(args: List[str]) -> List[tuple]:
    """GIT_PUSH on the real destination (--repo counts), plus DELETE when the
    push can rewrite or remove remote history (force, delete, mirror, prune,
    a +refspec or :refspec): a plain push grant never covers that."""
    pos: List[str] = []
    repo: Optional[str] = None
    destructive: List[str] = []
    k = 0
    while k < len(args):
        a = args[k]
        name = a.split("=", 1)[0]
        if a == "--":
            pos += args[k + 1:]
            break
        if name == "--repo":
            repo = a.split("=", 1)[1] if "=" in a else (args[k + 1] if k + 1 < len(args) else "")
        if name in _PUSH_DESTRUCTIVE or (a.startswith("-") and not a.startswith("--")
                                         and set(a[1:]) & {"f", "d"}):
            destructive.append(name)
        if a in _PUSH_VALUE_OPTS:
            k += 2
            continue
        if not a.startswith("-"):
            pos.append(a)
        k += 1
    if repo is not None:                       # --repo replaces the remote
        pos = [repo] + pos
    target = " ".join(pos[:2]) or "origin"
    remote = pos[0] if pos else "origin"
    destructive += [r for r in pos[1:] if r.startswith((":", "+"))]
    out = [(ActionClass.GIT_PUSH, target)]
    if destructive:
        out.append((ActionClass.DELETE, f"git:{remote} {' '.join(destructive)}"))
    return out


def _classify(cmd: str, depth: int) -> List[tuple]:
    if depth > _MAX_DEPTH:
        return [_opaque("nesting too deep")]
    ext = _extract_substitutions(cmd)
    if ext is None:
        return [_opaque("unbalanced quotes or substitution")]
    rewritten, inners = ext
    try:
        tokens = _tokens(rewritten)
    except ValueError:
        return [_opaque("unparseable command")]
    out: List[tuple] = []
    for inner in inners:
        out += _classify(inner, depth + 1)
    cwd: Optional[str] = None      # set by cd/pushd; "" = the sandbox root
    escaped = False                # a cd/pushd left (or may have left) the sandbox
    remote_changed = False         # an earlier segment rewrote git remote config
    for argv, outs in _segments(tokens):
        seg = _classify_argv(argv, depth) + [_write(t) for t in outs]
        if cwd is not None:
            seg = [_relocate(c, t, cwd, escaped) for c, t in seg]
        if remote_changed:
            seg = [(c, REDIRECTED + t) if c is ActionClass.GIT_PUSH
                   and not t.startswith(REDIRECTED) else (c, t) for c, t in seg]
        out += seg
        if any(t.startswith("git-config:") for _, t in seg):
            remote_changed = True
        moved = _cd_target(argv)
        if moved is not None:
            cwd = cwd or ""
            if (_is_dynamic(moved) or moved in ("", "-") or moved.startswith(("/", "~", "+"))
                    or ".." in moved.split("/") or re.fullmatch(r"-\d+", moved)):
                escaped, cwd = True, moved or "~"
            elif not escaped:
                cwd = os.path.join(cwd, moved)
    # never leak the internal placeholder (it holds a NUL) into targets/audit
    return [(c, t.replace(_SUBST, "$(...)")) for c, t in out]


def _cd_target(argv: List[str]) -> Optional[str]:
    """The directory a cd/pushd/popd segment moves to ("" = home or unknown),
    or None if the segment does not change directory."""
    while argv:                    # keywords, assignments and wrappers
        if argv[0] in _SHELL_KEYWORDS or _ASSIGN_RE.match(argv[0]):
            if argv[0].startswith("CDPATH="):
                return ""                       # cd may resolve anywhere
            argv = argv[1:]
        elif os.path.basename(argv[0]) in _WRAPPERS:
            argv = _skip_wrapper_opts(os.path.basename(argv[0]), argv[1:])
        elif argv[0] == "eval":
            argv = argv[1:]
        else:
            break
    if not argv or argv[0] not in ("cd", "pushd", "popd", "chdir"):
        return None
    if argv[0] == "popd":
        return ""
    pos = [a for a in argv[1:] if not a.startswith("-") or a == "-" or re.fullmatch(r"-\d+", a)]
    return pos[0] if pos else ""


def _relocate(cls: ActionClass, target: str, cwd: str, escaped: bool) -> tuple:
    """Re-anchor a relative sandbox write after a cd. Once the chain may have
    left the sandbox, a relative write is a write outside it."""
    if cls is ActionClass.GIT_PUSH and escaped and not target.startswith(REDIRECTED):
        return (cls, REDIRECTED + target)      # "origin" of some other repository
    if (cls is not ActionClass.WRITE_SANDBOX or os.path.isabs(target)
            or target.startswith("~") or _is_dynamic(target)):
        return (cls, target)
    if escaped:
        return (ActionClass.WRITE_OUTSIDE, f"{cwd}/{target}")
    return (cls, os.path.normpath(os.path.join(cwd, target)))


def classify_command(cmd: str) -> List[tuple]:
    """Static classification of a shell command into (ActionClass, target)
    pairs, one or more per simple command found anywhere in it (chains,
    pipes, substitutions, wrappers, sh -c scripts; see the notes above).
    Unknown programs are WRITE_SANDBOX on the cwd ("."), so the policy still
    sees them; code it cannot see into is OPAQUE. Conservative by design:
    the caller takes the worst verdict over all pairs."""
    out = _classify(cmd, 0)
    if re.search(r"\bCDPATH\b", cmd) and re.search(r"\b(cd|pushd|chdir)\b", cmd):
        # CDPATH (possibly exported earlier) lets `cd name` land anywhere
        out = [(ActionClass.WRITE_OUTSIDE, f"CDPATH:{t}")
               if c is ActionClass.WRITE_SANDBOX and t != "." else
               ((c, REDIRECTED + t) if c is ActionClass.GIT_PUSH
                and not t.startswith(REDIRECTED) else (c, t)) for c, t in out]
    return out


# ---------------------------------------------------------------------------
# untrusted provenance: an ALLOWLIST, not a classifier
# ---------------------------------------------------------------------------
#
# The classifier above is a deny-list: it finds the sinks it knows about. Two
# review rounds showed that a deny-list keeps leaking (wrapped cd, sed `e`
# after an address, GIT_CONFIG_* env, `source` of a file written earlier in
# the chain, pushd +N, ...). So a command whose provenance is UNTRUSTED (the
# initiator, or any argument) must ALSO pass this allowlist before it can be
# allowed. Anything not on it is ASK (rule R7); the classifier's DENY for the
# irreversible classes still wins. Every simple command (split on ; && || |
# and newlines) must be one of:
#   ls, cat, head, tail, wc        every operand (and --opt=value) is a path
#                                  that resolves inside the sandbox
#   echo, pwd, true                no constraints (they only print)
#   grep, egrep, fgrep, rg         every operand, including the pattern unless
#                                  it is given with -e/--regexp=, is a path inside
#                                  the sandbox (so no -r over outside paths);
#                                  rg --pre (runs a command) is refused
#   pytest / py.test / python[3[.N]] -m pytest
#                                  a small set of options (-q -v -x -s -l -r..,
#                                  -k/-m/-n EXPR, --tb= --lf --ff ...); test
#                                  paths (before ::) inside the sandbox;
#                                  -p/-c/-o/--rootdir/--basetemp/... are refused
#   git status|diff|log|show       no global options at all (no -c, -C,
#                                  --git-dir, --exec-path), no --output,
#                                  --ext-diff, --no-index, -O (abbreviations of
#                                  those count), no absolute/~/.. operands
# and the whole command has:
#   * no expansion of any kind: $, `, \ outside single quotes; no (, ), {, },
#     #, !, ^ outside quotes (subshells, process/brace/zsh expansion, comments)
#   * no program given by path, no VAR=value prefix, no `&`, heredoc or `<&`
#   * redirections only to /dev/null or to files inside the sandbox that are
#     not dotfiles/dot-dirs (.git/, .venv/, .zshrc ...) or test/build config
#     (conftest.py, pyproject.toml, setup.cfg, ...); `<` only from inside
#   * no shell builtins that change state (cd, pushd, source, ., exec, eval,
#     trap, alias, export, set, ...): they are simply not on the list
# Any parse error or surprise is a violation (fail closed).

_UNTRUSTED_PATH_PROGS = {"ls", "cat", "head", "tail", "wc"}
_UNTRUSTED_FREE_PROGS = {"echo", "pwd", "true"}
_UNTRUSTED_GREP = {"grep", "egrep", "fgrep", "rg"}
_UNTRUSTED_PYTHON_RE = re.compile(r"python(3(\.\d+)?)?")
_UNTRUSTED_GIT_SUBS = {"status", "diff", "log", "show"}
#: git options refused for untrusted (a unique prefix of one counts too)
_UNTRUSTED_GIT_BAD = ("--output", "--ext-diff", "--no-index", "--orderfile", "--exec",
                      "--upload-pack", "--receive-pack", "--git-dir", "--work-tree",
                      "--config-env", "--textconv", "--open-files-in-pager", "--paginate")
_UNTRUSTED_PYTEST_LONG = {
    "--tb", "--maxfail", "--durations", "--durations-min", "--color", "--lf",
    "--last-failed", "--ff", "--failed-first", "--nf", "--new-first", "--co",
    "--collect-only", "--no-header", "--no-summary", "--disable-warnings", "--exitfirst",
    "--verbose", "--quiet", "--showlocals", "--strict-markers", "--runxfail",
    "--setup-show", "--sw", "--stepwise", "--deselect", "--ignore", "--ignore-glob",
}
#: files whose content a later allowed command would execute or obey
_UNTRUSTED_NO_WRITE = {
    "conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "setup.py", "pyproject.toml",
    "sitecustomize.py", "usercustomize.py", "makefile", "gnumakefile", "package.json",
}
#: extensions of files something may later run or import (pytest collects .py)
_UNTRUSTED_CODE_EXT = (".py", ".pyc", ".pth", ".sh", ".bash", ".zsh", ".command", ".so",
                       ".dylib", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php",
                       ".ini", ".cfg", ".toml", ".scpt", ".applescript", ".plist")


def _resolves_inside(path: str, root: Path) -> bool:
    try:
        p = Path(os.path.realpath(path if os.path.isabs(path) else root / path))
        return p == root or root in p.parents
    except (OSError, ValueError):
        return False


def _untrusted_path_ok(arg: str, root: Path) -> bool:
    """A word the shell may treat as a path, judged as one: it must resolve
    inside the sandbox, with no tilde/brace/bracket expansion and no glob
    that could match `..`."""
    if arg in ("", "-"):
        return True
    if arg.startswith(("~", "=")) or any(c in arg for c in "{}[]"):
        return False
    segs = arg.split("/")
    if ".." in segs:
        return False
    if any(sg.startswith(".") and any(c in sg for c in "*?") for sg in segs):
        return False
    return _resolves_inside(arg, root)


def _untrusted_write_ok(target: str, root: Path) -> bool:
    if target == "/dev/null":
        return True
    if not _untrusted_path_ok(target, root) or any(c in target for c in "*?"):
        return False
    segs = [sg for sg in target.split("/") if sg not in ("", ".")]
    if not segs or any(sg.startswith(".") for sg in segs):
        return False
    base = segs[-1].lower()
    return base not in _UNTRUSTED_NO_WRITE and not base.endswith(_UNTRUSTED_CODE_EXT)


def _untrusted_prescan(cmd: str) -> Optional[str]:
    in_s = in_d = False
    for ch in cmd:
        if (ord(ch) < 32 and ch not in "\n\t") or ch == "\x7f":
            return "control character"
        if in_s:
            in_s = ch != "'"
            continue
        if ch == "\\":
            return "backslash escape"
        if in_d:
            if ch == '"':
                in_d = False
            elif ch in "$`":
                return "expansion inside double quotes"
            continue
        if ch == "'":
            in_s = True
        elif ch == '"':
            in_d = True
        elif ch in "$`":
            return "variable or command substitution"
        elif ch in "(){}#!^":
            return f"shell syntax {ch!r}"
    if in_s or in_d:
        return "unbalanced quotes"
    return None


def _untrusted_plain(prog: str, args: List[str], root: Path) -> Optional[str]:
    end = False
    for a in args:
        if not end and a == "--":
            end = True
            continue
        if not end and a.startswith("-") and a != "-":
            if a.startswith("--") and "=" in a and not _untrusted_path_ok(a.split("=", 1)[1], root):
                return f"{prog} option value {a!r} is not inside the sandbox"
            continue
        if not _untrusted_path_ok(a, root):
            return f"{prog} operand {a!r} is not a path inside the sandbox"
    return None


def _untrusted_grep(prog: str, args: List[str], root: Path) -> Optional[str]:
    end, k = False, 0
    while k < len(args):
        a = args[k]
        if not end and a == "--":
            end = True
        elif not end and a.startswith("--"):
            name, eq, val = a.partition("=")
            if name.startswith("--pre") or name in ("--", "--search-zip"):
                return f"{prog} {name} runs other programs"
            if name == "--regexp":
                if not eq:
                    k += 1                       # the pattern; not a path
            elif eq and not _untrusted_path_ok(val, root):
                return f"{prog} option value {a!r} is not inside the sandbox"
        elif not end and a.startswith("-") and a != "-":
            cl = a[1:]
            if prog == "rg" and "z" in cl:
                return "rg -z runs decompression programs"
            for j, ch in enumerate(cl):
                if ch in "ef":
                    rest = cl[j + 1:]
                    if not rest:
                        k += 1
                        rest = args[k] if k < len(args) else ""
                    if ch == "f" and not _untrusted_path_ok(rest, root):
                        return f"{prog} -f {rest!r} is not inside the sandbox"
                    break
        elif not _untrusted_path_ok(a, root):
            return f"{prog} operand {a!r} is not a path inside the sandbox"
        k += 1
    return None


def _untrusted_pytest(args: List[str], root: Path) -> Optional[str]:
    end, k = False, 0
    while k < len(args):
        a = args[k]
        if not end and a == "--":
            end = True
        elif not end and a in ("-k", "-m", "-n"):
            k += 1                               # an expression / worker count
        elif not end and a.startswith("--"):
            name, eq, val = a.partition("=")
            if name not in _UNTRUSTED_PYTEST_LONG:
                return f"pytest option {name} is not on the allowlist"
            if eq and not _untrusted_path_ok(val.split("::", 1)[0], root):
                return f"pytest option value {a!r} is not inside the sandbox"
        elif not end and a.startswith("-") and a != "-":
            cl = a[1:]
            if not (set(cl) <= set("qvxsl") or (cl[0] == "r" and cl[1:].isalpha())
                    or (cl[0] in "kmn" and len(cl) > 1)):
                return f"pytest option {a} is not on the allowlist"
        elif not _untrusted_path_ok(a.split("::", 1)[0], root):
            return f"pytest path {a!r} is not inside the sandbox"
        k += 1
    return None


def _untrusted_git(args: List[str], root: Path) -> Optional[str]:
    if not args or args[0] not in _UNTRUSTED_GIT_SUBS:
        return f"git {args[0] if args else ''} is not on the allowlist (only " \
               "status/diff/log/show, without global options)"
    end = False
    for a in args[1:]:
        if not end and a == "--":
            end = True
            continue
        if not end and a.startswith("--"):
            name = a.split("=", 1)[0]
            if any(bad.startswith(name) for bad in _UNTRUSTED_GIT_BAD):
                return f"git option {name} is not allowed"
            continue
        if not end and a.startswith("-") and len(a) > 1:
            if a.startswith(("-O", "-c")):
                return f"git option {a} is not allowed"
            continue
        if a.startswith(("/", "~")) or ".." in a.split("/"):
            if not (end and _untrusted_path_ok(a, root)):
                return f"git operand {a!r} is outside the sandbox"
        elif end and not _untrusted_path_ok(a, root):
            return f"git path {a!r} is not inside the sandbox"
    return None


def _untrusted_simple(argv: List[str], root: Path) -> Optional[str]:
    prog, args = argv[0], argv[1:]
    if _ASSIGN_RE.match(prog):
        return "environment variable prefix"
    if "/" in prog:
        return f"program given by path {prog!r}"
    if prog in _UNTRUSTED_FREE_PROGS:
        return None
    if prog in _UNTRUSTED_PATH_PROGS:
        return _untrusted_plain(prog, args, root)
    if prog in _UNTRUSTED_GREP:
        return _untrusted_grep(prog, args, root)
    if prog in ("pytest", "py.test"):
        return _untrusted_pytest(args, root)
    if _UNTRUSTED_PYTHON_RE.fullmatch(prog):
        if args[:2] == ["-m", "pytest"]:
            return _untrusted_pytest(args[2:], root)
        return f"{prog} other than `-m pytest`"
    if prog == "git":
        return _untrusted_git(args, root)
    return f"{prog!r} is not on the untrusted allowlist"


def _untrusted_violation(cmd: str, root: Path) -> Optional[str]:
    why = _untrusted_prescan(cmd)
    if why:
        return why
    tokens = _tokens(cmd)
    segments: List[List[str]] = []
    argv: List[str] = []
    wrote = False                                # a redirection wrote a file
    k = 0
    while k < len(tokens):
        tok = tokens[k]
        if not _is_op(tok):
            argv.append(tok)
            k += 1
            continue
        core = tok.replace("\n", "")
        if core in ("", ";", "&&", "||", "|"):
            if core and not argv:
                return f"empty command before {core!r}"
            if argv:
                segments.append(argv)
            argv = []
            k += 1
            continue
        nxt = tokens[k + 1] if k + 1 < len(tokens) else ""
        if argv and argv[-1].isdigit():
            argv.pop()                           # the fd in 2>file
        if core == ">&" and nxt.isdigit():
            k += 2                               # 2>&1
            continue
        if core not in (">", ">>", ">|", "&>", "&>>", ">&", "<"):
            return f"shell operator {core!r}"
        if not nxt or _is_op(nxt):
            return "redirection without a target"
        if core == "<":
            if not _untrusted_path_ok(nxt, root):
                return f"input redirection from {nxt!r} (outside the sandbox)"
        elif not _untrusted_write_ok(nxt, root):
            return f"redirection to {nxt!r} (outside the sandbox or a protected file)"
        elif nxt != "/dev/null":
            wrote = True
        k += 2
    if argv:
        segments.append(argv)
    if wrote and any(sg[0] in ("pytest", "py.test") or _UNTRUSTED_PYTHON_RE.fullmatch(sg[0])
                     for sg in segments):
        return "a chain that writes a file must not also run tests"
    for seg in segments:
        why = _untrusted_simple(seg, root)
        if why:
            return why
    return None


def untrusted_shell_violation(cmd: str, sandbox: Path | str) -> Optional[str]:
    """Why `cmd` is NOT on the untrusted-provenance allowlist, or None when
    every simple command in it is. Fails closed on any parse surprise."""
    try:
        return _untrusted_violation(cmd, Path(os.path.realpath(sandbox)))
    except Exception as e:                       # noqa: BLE001 - fail closed
        return f"cannot parse ({type(e).__name__})"


_ORDER = {Verdict.ALLOW: 0, Verdict.ASK: 1, Verdict.DENY: 2}


def decide_command(policy: SinkPolicy, cmd: str, initiator: Provenance,
                   arg_sources: Sequence[Provenance] = ()) -> Decision:
    """Worst verdict over every (class, target) the command implies. When the
    command's provenance is untrusted (initiator or any argument), an ALLOW
    additionally requires the allowlist above; otherwise it becomes ASK (R7)."""
    decisions = [policy.decide(Action(c, t, initiator, list(arg_sources), cmd))
                 for c, t in classify_command(cmd)]
    if not decisions:
        base = Decision(Verdict.ALLOW, "empty command", "R0-empty",
                        Action(ActionClass.READ, "", initiator))
    else:
        base = max(decisions, key=lambda d: _ORDER[d.verdict])
    if (base.verdict is Verdict.ALLOW
            and least_trusted([initiator, *arg_sources]) is Provenance.UNTRUSTED):
        why = untrusted_shell_violation(cmd, policy.sandbox)
        if why is not None:
            d = Decision(Verdict.ASK, f"untrusted shell command is not on the read-only "
                         f"allowlist: {why}", "R7-untrusted-allowlist",
                         Action(ActionClass.OPAQUE, why[:120], initiator,
                                list(arg_sources), cmd))
            if policy.audit is not None:
                policy.audit.append(d)
            return d
    return base

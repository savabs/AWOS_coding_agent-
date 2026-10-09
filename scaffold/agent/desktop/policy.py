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
# Code the classifier cannot see into is OPAQUE, the most dangerous class:
# the policy asks for it at best and denies it from untrusted initiators.
#   * interpreter inline code: python -c, node -e, perl/ruby -e, php -r,
#     osascript -e, awk programs that call system()/pipes/redirects
#   * a shell or interpreter reading code from stdin (`curl x | sh`)
#   * a dynamic program name ($CMD, or $(...) as the command)
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
}
_SHELL_KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until",
                   "!", "{", "}", "case", "esac"}
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SUBST = "\x00SUBST\x00"          # placeholder left where a $(...) was
_HARMLESS_SINKS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}
_OPS = "();<>|&\n"
_MAX_DEPTH = 6


def _opaque(why: str) -> tuple:
    return (ActionClass.OPAQUE, why.replace(_SUBST, "$(...)")[:120])


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


def _classify_argv(argv: List[str], depth: int) -> List[tuple]:
    if depth > _MAX_DEPTH:
        return [_opaque("nesting too deep")]
    while argv and (argv[0] in _SHELL_KEYWORDS or _ASSIGN_RE.match(argv[0])):
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
        return out + _classify_argv(inner, depth + 1)

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
        rest, k = [], 0
        while k < len(args):                            # git's global options
            if args[k] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
                k += 2
                continue
            rest.append(args[k])
            k += 1
        sub = [x for x in rest if not x.startswith("-")]
        if sub[:1] == ["push"]:
            return [(ActionClass.GIT_PUSH, " ".join(sub[1:3]) or "origin")]
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
        out += [(ActionClass.WRITE_SANDBOX, t) for t in targets] or [(ActionClass.WRITE_SANDBOX, ".")]
    elif prog in _READ:
        out.append((ActionClass.READ, pos[0] if pos else "."))
    else:
        out.append((ActionClass.WRITE_SANDBOX, "."))
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
    for argv, outs in _segments(tokens):
        out += _classify_argv(argv, depth)
        out += [(ActionClass.WRITE_SANDBOX, t.replace(_SUBST, "$(...)")) for t in outs]
    return out


def classify_command(cmd: str) -> List[tuple]:
    """Static classification of a shell command into (ActionClass, target)
    pairs, one or more per simple command found anywhere in it (chains,
    pipes, substitutions, wrappers, sh -c scripts; see the notes above).
    Unknown programs are WRITE_SANDBOX on the cwd ("."), so the policy still
    sees them; code it cannot see into is OPAQUE. Conservative by design:
    the caller takes the worst verdict over all pairs."""
    return _classify(cmd, 0)


_ORDER = {Verdict.ALLOW: 0, Verdict.ASK: 1, Verdict.DENY: 2}


def decide_command(policy: SinkPolicy, cmd: str, initiator: Provenance,
                   arg_sources: Sequence[Provenance] = ()) -> Decision:
    """Worst verdict over every (class, target) the command implies."""
    decisions = [policy.decide(Action(c, t, initiator, list(arg_sources), cmd))
                 for c, t in classify_command(cmd)]
    if not decisions:
        return Decision(Verdict.ALLOW, "empty command", "R0-empty",
                        Action(ActionClass.READ, "", initiator))
    return max(decisions, key=lambda d: _ORDER[d.verdict])

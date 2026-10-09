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
  4. PAYMENT / CREDENTIAL         -> ask even with a grant (critical-point stop)
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


IRREVERSIBLE = {
    ActionClass.NETWORK_EGRESS, ActionClass.SEND, ActionClass.DELETE,
    ActionClass.CREDENTIAL, ActionClass.PAYMENT, ActionClass.GIT_PUSH,
}
ALWAYS_ASK = {ActionClass.PAYMENT, ActionClass.CREDENTIAL}


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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(decision.to_record(), sort_keys=True) + "\n")

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

_EGRESS = {"curl", "wget", "nc", "ssh", "scp", "rsync", "sftp", "ftp", "telnet"}
_DELETE = {"rm", "rmdir", "unlink", "trash", "shred", "srm"}
_WRITE = {"mv", "cp", "touch", "mkdir", "tee", "ln", "chmod", "chown", "plutil",
          "sqlite3", "defaults"}
_SEND_RE = re.compile(r"\b(send|reply|post|submit|tweet)\b", re.I)


def classify_command(cmd: str) -> List[tuple]:
    """Best-effort static classification of a shell command into
    (ActionClass, target) pairs. Unknown commands are WRITE_SANDBOX on the
    cwd ("."), so the policy still sees them. Conservative by design: when
    several classes apply, all are returned and the caller takes the worst."""
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return [(ActionClass.WRITE_OUTSIDE, cmd)]
    if not argv:
        return []
    out: List[tuple] = []
    prog = os.path.basename(argv[0])
    args = [x for x in argv[1:] if not x.startswith("-")]
    if prog == "git" and args[:1] == ["push"]:
        out.append((ActionClass.GIT_PUSH, " ".join(args[1:3]) or "origin"))
    elif prog in _EGRESS:
        host = next((x for x in args if re.match(r"[a-z]+://|[\w.-]+@|[\w-]+\.\w", x)), "")
        out.append((ActionClass.NETWORK_EGRESS, host))
    elif prog in _DELETE:
        out += [(ActionClass.DELETE, x) for x in args] or [(ActionClass.DELETE, "")]
    elif prog == "security" and any(x.startswith(("find-", "dump-")) for x in args):
        out.append((ActionClass.CREDENTIAL, "keychain"))
    elif prog == "osascript" and _SEND_RE.search(cmd):
        out.append((ActionClass.SEND, "applescript"))
    elif prog in ("open",) and any(x.startswith(("http://", "https://", "mailto:")) for x in args):
        out.append((ActionClass.NETWORK_EGRESS, args[0]))
    elif prog in _WRITE:
        targets = args[-1:] if prog in ("mv", "cp", "ln") else args[:1]
        out += [(ActionClass.WRITE_SANDBOX, t) for t in targets] or [(ActionClass.WRITE_SANDBOX, ".")]
    elif prog in ("cat", "ls", "head", "tail", "grep", "find", "mdls", "stat", "wc", "file"):
        out.append((ActionClass.READ, args[0] if args else "."))
    else:
        out.append((ActionClass.WRITE_SANDBOX, "."))
    if ">" in argv or ">>" in argv:
        for i, tok in enumerate(argv[:-1]):
            if tok in (">", ">>"):
                out.append((ActionClass.WRITE_SANDBOX, argv[i + 1]))
    return out


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

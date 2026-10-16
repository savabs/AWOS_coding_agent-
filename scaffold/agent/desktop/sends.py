"""
sends.py — two-phase external sends: prepare -> approve -> commit once.

D16 in docs/research/rea_synthesis_2026-10-16.md (ChatGPT "Work with Apps"):
anything that leaves the machine (a message, an email, a form submission, a
post) is never one tool call. The agent *prepares* it, the owner *approves*
it (and may edit the payload), and only then is it *committed*, exactly
once.

    broker = SendBroker(policy, approver=ask_owner, audit=AuditLog(path))
    p = broker.prepare("email", "mailto:bob@x.test", {"subject": ..., "body": ...})
    broker.approve(p.id)                       # owner sees p.payload, may edit
    broker.commit(p.id, lambda payload: mail.send(**payload))   # runs once
    broker.commit(p.id, ...)                   # SendRefused: already committed

How it sits on the sink policy (policy.py):
  prepare  policy.decide(SEND, target, initiator, arg_sources). A DENY stands
           (R3: on-screen/untrusted text started the send) except R6's
           "no standing grant", which this flow replaces with a per-send
           owner approval. ASK and ALLOW both go on to approval: a standing
           grant does not skip the owner's look at the payload.
  approve  the approver (the owner) gets the PreparedSend and returns True,
           False, or SendApproval(True, payload=<edited>). No approver, a
           "no", or an approver that raises -> rejected (fail closed).
  commit   policy.decide again with a one-shot exact-target owner grant and
           the payload marked OWNER (the owner has seen it), so R3/R5 still
           apply; then the idempotency key is burned *before* the executor
           runs. The executor gets the approved payload (not whatever the
           agent holds now). A second commit, or a new prepare with a burned
           key, is refused. An executor that raises leaves the key burned:
           it may already have sent, so a retry needs a new key and a new
           approval.
The idempotency key defaults to a hash of (kind, target, payload), so
preparing the same message twice is caught as a duplicate. Pass ``ledger``
(a JSONL path) to keep burned keys across restarts.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from .policy import (
    Action, ActionClass, AuditLog, Decision, Provenance, SinkPolicy, Verdict, exact_grant,
)

_UNCHANGED = object()


class SendRefused(PermissionError):
    pass


@dataclass
class SendApproval:
    approved: bool
    payload: Any = _UNCHANGED          # an edited payload replaces the prepared one
    note: str = ""


@dataclass
class PreparedSend:
    id: str
    key: str
    kind: str                          # email | message | form | post | ui_send ...
    target: str
    payload: Any
    initiator: Provenance
    arg_sources: List[Provenance]
    description: str = ""
    status: str = "prepared"           # prepared|approved|rejected|committing|committed|failed
    edited: bool = False
    decision: Optional[Decision] = None
    result: Any = None
    error: str = ""
    history: List[str] = field(default_factory=list)


SendApprover = Callable[[PreparedSend], Union[bool, SendApproval, None]]


def idempotency_key(kind: str, target: str, payload: Any) -> str:
    blob = json.dumps({"k": kind, "t": target, "p": payload}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


class SendBroker:
    def __init__(self, policy: SinkPolicy, approver: Optional[SendApprover] = None,
                 audit: Optional[AuditLog] = None, ledger: Optional[Union[str, Path]] = None):
        self.policy = policy
        self.approver = approver
        self.audit = audit
        self.ledger = Path(ledger) if ledger else None
        self.sends: Dict[str, PreparedSend] = {}
        self.burned: Dict[str, str] = {}          # key -> status at burn time
        if self.ledger and self.ledger.exists():
            for line in self.ledger.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.burned[rec["key"]] = rec.get("status", "committing")

    # -- phase 1 --------------------------------------------------------------
    def prepare(self, kind: str, target: str, payload: Any, *,
                initiator: Provenance = Provenance.AGENT,
                arg_sources: Sequence[Provenance] = (Provenance.AGENT,),
                description: str = "", key: Optional[str] = None) -> PreparedSend:
        key = key or idempotency_key(kind, target, payload)
        if key in self.burned:
            self._log("prepare", None, "refused", key=key, target=target,
                      reason=f"duplicate: key already {self.burned[key]}")
            raise SendRefused(f"duplicate send: idempotency key {key} was already "
                              f"{self.burned[key]}")
        p = PreparedSend(uuid.uuid4().hex[:12], key, kind, target, copy.deepcopy(payload),
                         Provenance(initiator), [Provenance(s) for s in arg_sources],
                         description or f"{kind} to {target}")
        d = self.policy.decide(Action(ActionClass.SEND, target, p.initiator, p.arg_sources,
                                      p.description))
        p.decision = d
        if d.verdict is Verdict.DENY and d.rule != "R6-default-deny":
            p.status = "rejected"
            p.history.append(f"prepare denied by {d.rule}")
            self._log("prepare", p, "refused", reason=d.reason, rule=d.rule)
            raise SendRefused(f"send refused by sink policy ({d.rule}): {d.reason}")
        self.sends[p.id] = p
        p.history.append(f"prepared ({d.rule})")
        self._log("prepare", p, "prepared", rule=d.rule)
        return p

    # -- phase 2 --------------------------------------------------------------
    def approve(self, send_id: str, approver: Optional[SendApprover] = None) -> PreparedSend:
        p = self._get(send_id)
        if p.status != "prepared":
            raise SendRefused(f"send {send_id} is {p.status}, not awaiting approval")
        fn = approver or self.approver
        try:
            ans = fn(copy.deepcopy(p)) if fn is not None else None
        except Exception as e:                       # an approver failure never approves
            ans = SendApproval(False, note=f"approver raised {type(e).__name__}")
        if not isinstance(ans, SendApproval):
            ans = SendApproval(ans is True)
        if not ans.approved:
            p.status = "rejected"
            p.history.append("rejected by owner" + (f": {ans.note}" if ans.note else ""))
            self._log("approve", p, "rejected", reason=ans.note)
            return p
        if ans.payload is not _UNCHANGED:
            p.edited = ans.payload != p.payload
            p.payload = copy.deepcopy(ans.payload)
        p.status = "approved"
        p.history.append("approved" + (" with edits" if p.edited else ""))
        self._log("approve", p, "approved", edited=p.edited)
        return p

    # -- phase 3 --------------------------------------------------------------
    def commit(self, send_id: str, execute: Callable[[Any], Any]) -> Any:
        p = self._get(send_id)
        if p.status in ("committing", "committed", "failed") or p.key in self.burned:
            self._log("commit", p, "refused", reason="already committed")
            raise SendRefused(f"send {send_id} already committed "
                              f"(key {p.key}: {self.burned.get(p.key, p.status)})")
        if p.status != "approved":
            self._log("commit", p, "refused", reason=f"status {p.status}")
            raise SendRefused(f"send {send_id} is {p.status}; it needs owner approval first")
        once = SinkPolicy(self.policy.sandbox, [exact_grant(ActionClass.SEND, p.target)],
                          self.policy.audit)
        d = once.decide(Action(ActionClass.SEND, p.target, p.initiator, [Provenance.OWNER],
                               p.description + " [approved]"))
        if d.verdict is not Verdict.ALLOW:
            p.status = "rejected"
            self._log("commit", p, "refused", reason=d.reason, rule=d.rule)
            raise SendRefused(f"commit refused by sink policy ({d.rule}): {d.reason}")
        self._burn(p, "committing")                  # before the side effect: at most once
        try:
            p.result = execute(copy.deepcopy(p.payload))
        except Exception as e:
            p.status, p.error = "failed", f"{type(e).__name__}: {e}"
            self._burn(p, "failed")
            self._log("commit", p, "failed", reason=p.error)
            raise
        p.status = "committed"
        self._burn(p, "committed")
        p.history.append("committed")
        self._log("commit", p, "committed", rule=d.rule)
        return p.result

    # -- internals ------------------------------------------------------------
    def _get(self, send_id: str) -> PreparedSend:
        p = self.sends.get(send_id)
        if p is None:
            raise SendRefused(f"unknown send {send_id!r} (prepare it first)")
        return p

    def _burn(self, p: PreparedSend, status: str) -> None:
        p.status = status
        self.burned[p.key] = status
        if self.ledger is not None:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with open(self.ledger, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": round(time.time(), 3), "key": p.key, "id": p.id,
                                    "status": status, "target": p.target}) + "\n")

    def _log(self, phase: str, p: Optional[PreparedSend], outcome: str, **extra) -> None:
        if self.audit is None:
            return
        rec = {"ts": round(time.time(), 3), "kind": "send", "phase": phase, "outcome": outcome}
        if p is not None:
            rec.update({"send_id": p.id, "key": p.key, "send_kind": p.kind, "target": p.target,
                        "initiator": p.initiator.value, "status": p.status})
        rec.update({k: v for k, v in extra.items() if v not in (None, "")})
        self.audit.append_record(rec)

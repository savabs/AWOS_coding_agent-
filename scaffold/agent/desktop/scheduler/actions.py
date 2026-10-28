"""actions.py: the owner's answers to the morning digest.

Every action the digest offers is a CLI command (``python -m
scaffold.agent.desktop.scheduler <action> ...``) or a line in an actions file
passed to ``apply``. The digest page stays script-free: it prints the commands
and writes an ``actions.todo`` template; nothing posts anywhere.

  approve <id> | --all     carry out a queued write (archive) through the mailbox adapter
  deny <id>                drop a queued write; nothing is written
  undo <run-id>:<msg-id>   move an archived message back to the INBOX
  undo --run <run-id>      ... every archive that run made (auto or approved)
  correct <msg-id> --importance N [--category C] [--needs-reply y/n] [--note "..."]
                           an explicit correction; becomes a user rule
  review                   list the review queue with suggested labels and commands
  apply <file>             run every uncommented action line in a file

Guarantees
----------
* **Idempotent.** Each action has a deterministic id (``approve:<approval id>``,
  ``deny:<approval id>``, ``undo:<run id>:<msg id>``, ``correct:<msg id>:<hash>``).
  The action ledger (``<home>/actions/ledger.jsonl``) records ``intent`` before a
  mail write, ``learned`` after the preference store was updated and ``done`` at
  the end. A ``done`` action is never repeated (``already_done``).
* **Crash-safe.** An ``intent`` without ``done`` (killed between the write and the
  ledger) is reconciled by reading the message back, never by writing blindly.
  The same read-back makes undo of an already restored message a no-op
  (``reconciled``).
* **Write policy.** A mail write happens only when the job's ``approvals.writes``
  is not ``deny`` AND the adapter is writable. Otherwise the action is
  ``refused`` with the reason, the ledger keeps no ``done``, and it can be retried.
* **Audit.** One line per action attempt in ``<home>/actions/audit.jsonl``. The
  ledger, the audit, and notes written into the preference store all pass
  through the WP7 SecretBroker redaction.

Learning (testbeds/email_triage/prefs.py)
-----------------------------------------
* ``correct`` -> an explicit user rule (``source="user"``, active at once, outranks
  every learned rule) keyed on the message's scope from
  ``correction_capture.scope_for``: the sender address for people, address +
  subject pattern for services. Never domain-wide.
* ``undo`` of an archive -> a user rule ``min_importance >= 1`` for that scope (the
  owner said: not junk), so the next run keeps it in the inbox.
* ``approve`` of an archive suggestion -> implicit evidence (``source="implicit"``)
  for "this scope is junk"; ``deny`` -> implicit evidence for the exclude
  "never mark this scope junk". Both are candidates that pass the promotion gate
  (>= 3 threads, no contradictions, model agreement) before they apply. An
  approval counts as model agreement (the triage itself proposed the archive);
  a denial does not.
* The next digest lists these rules under "Learned from your corrections".
"""
from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .jobs import Job, JobError
from .runner import Scheduler, append_jsonl, file_lock, iso, parse_iso, read_json, read_jsonl

PROG = "python -m scaffold.agent.desktop.scheduler"
OK_STATUSES = ("done", "already_done", "reconciled")
CATEGORIES = ("urgent_action", "important_read", "fyi", "newsletter", "promo", "social",
              "spam_phishing")


class ActionError(ValueError):
    """Bad arguments for an action (unknown id, bad value)."""


def writable_adapter(job: Job):
    """The adapter actions write through: writable unless the job's policy is
    ``deny``. (The overnight run opens it read-only under ``queue``; an owner
    approval is exactly the gate ``queue`` waits for.) A ``fake:`` mailbox keeps
    state in memory, so from the CLI its writes last for that process only."""
    from ..mail_adapter import FakeMailbox
    src, where = job.mailbox_source
    ro = job.writes == "deny"
    if src == "fake":
        return FakeMailbox(where, read_only=ro)
    from ..mail_imap import ImapGmail
    return ImapGmail(where, read_only=ro)


def cli_prefix(cfg) -> str:
    parts = [PROG]
    if getattr(cfg, "path", None):
        parts += ["--jobs", shlex.quote(str(cfg.path))]
    parts += ["--home", shlex.quote(str(cfg.home))]
    return " ".join(parts)


def _q(x: Any) -> str:
    return shlex.quote(str(x))


def correct_cmd(prefix: str, m: dict) -> str:
    return (f"{prefix} correct {_q(m['id'])} --importance {int(m.get('importance') or 0)} "
            f"--category {_q(m.get('category') or 'fyi')} "
            f"--needs-reply {'y' if m.get('needs_reply') else 'n'}")


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:10]


def _yn(v: Optional[str]) -> Optional[bool]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("y", "yes", "true", "1"):
        return True
    if s in ("n", "no", "false", "0"):
        return False
    raise ActionError(f"--needs-reply {v!r}: use y or n")


class Actions:
    def __init__(self, sched: Scheduler, adapter_factory: Optional[Callable[[Job], Any]] = None,
                 after_write: Optional[Callable[[str], None]] = None):
        self.sched = sched
        self.cfg = sched.cfg
        self.adapter_factory = adapter_factory or writable_adapter
        self.after_write = after_write          # test hook: may raise to simulate a kill
        self._adapters: Dict[str, Any] = {}

    # ── paths / ledger ──────────────────────────────────────────────────────
    @property
    def dir(self) -> Path:
        return self.sched.home / "actions"

    @property
    def ledger_path(self) -> Path:
        return self.dir / "ledger.jsonl"

    @property
    def audit_path(self) -> Path:
        return self.dir / "audit.jsonl"

    def phases(self, action_id: str) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for r in read_jsonl(self.ledger_path):
            if r.get("action_id") == action_id:
                out[r.get("phase", "")] = r
        return out

    def _ledger(self, action_id: str, phase: str, **kw) -> None:
        append_jsonl(self.ledger_path, self.sched.broker.redact_obj(
            {"action_id": action_id, "phase": phase, "at": iso(self.sched.clock()), **kw}))

    def _audit(self, res: dict) -> dict:
        append_jsonl(self.audit_path, self.sched.broker.redact_obj(
            {"at": iso(self.sched.clock()), **{k: res.get(k) for k in
                                                ("action_id", "op", "target", "status", "message")}}))
        return self.sched.broker.redact_obj(res)

    def _res(self, action_id: str, op: str, target: str, status: str, message: str, **kw) -> dict:
        return self._audit({"action_id": action_id, "op": op, "target": target, "status": status,
                            "message": message, **kw})

    # ── lookups ─────────────────────────────────────────────────────────────
    def adapter(self, job: Job):
        if job.name not in self._adapters:
            self._adapters[job.name] = self.adapter_factory(job)
        return self._adapters[job.name]

    def triage_index(self) -> Dict[str, dict]:
        """msg id -> the newest triage item for it (with run_id / job)."""
        idx: Dict[str, dict] = {}
        seen = set()
        for r in read_jsonl(self.sched.runs_path):
            rid = r.get("run_id")
            if r.get("kind") != "email_triage" or not rid or rid in seen:
                continue
            seen.add(rid)
            tri = read_json(self.sched.run_dir(rid) / "triage.json", None)
            for it in (tri or {}).get("items", []):
                idx[it["id"]] = {**it, "run_id": rid, "job": r.get("job")}
        return idx

    def _email_jobs(self) -> List[Job]:
        return [j for j in self.cfg.jobs if j.kind == "email_triage"]

    def _job_for_msg(self, mid: str, job_name: Optional[str], item: Optional[dict]) -> Job:
        if job_name:
            return self.cfg.job(job_name)
        if item and item.get("job"):
            return self.cfg.job(item["job"])
        jobs = self._email_jobs()
        if len(jobs) == 1:
            return jobs[0]
        raise ActionError(f"message {mid!r} is in no triage record; pass --job")

    def _message(self, job: Job, mid: str, item: Optional[dict]) -> dict:
        """Full message through the adapter (headers matter for the scope), else
        the triage item's fields."""
        try:
            return self.adapter(job).get_message(mid)
        except Exception:
            if item:
                return {"id": mid, "thread_id": item.get("thread_id"), "from_addr": item.get("from_addr"),
                        "from_name": item.get("from_name"), "subject": item.get("subject"),
                        "headers": {}, "body": ""}
            raise ActionError(f"message {mid!r} not found in the mailbox or any triage record")

    def archives(self) -> List[dict]:
        """Every archive AWOS made: run ledgers (auto) + approved archives."""
        out, seen = [], set()
        for r in read_jsonl(self.sched.runs_path):
            rid = r.get("run_id")
            if r.get("kind") != "email_triage" or not rid or rid in seen:
                continue
            seen.add(rid)
            for a in read_jsonl(self.sched.run_dir(rid) / "actions.jsonl"):
                if a.get("phase") == "done" and a.get("op") == "archive":
                    out.append({"run_id": rid, "id": a["id"], "job": r.get("job"), "how": "auto"})
        for a in read_jsonl(self.ledger_path):
            if a.get("phase") == "done" and a.get("op") == "approve" and a.get("archived"):
                out.append({"run_id": a.get("run_id"), "id": a.get("target"), "job": a.get("job"),
                            "how": "approved"})
        uniq: Dict[str, dict] = {}
        for a in out:
            uniq.setdefault(f"{a['run_id']}:{a['id']}", a)
        return list(uniq.values())

    def undone(self) -> set:
        return {r["action_id"][len("undo:"):] for r in read_jsonl(self.ledger_path)
                if r.get("phase") == "done" and str(r.get("action_id", "")).startswith("undo:")}

    def approvals(self) -> Dict[str, dict]:
        latest: Dict[str, dict] = {}
        for a in read_jsonl(self.sched.approvals_path):
            latest[a.get("id")] = a
        return latest

    def _set_approval(self, ap: dict, status: str, **kw) -> None:
        with file_lock(self.sched.home / "approvals.lock"):
            append_jsonl(self.sched.approvals_path, self.sched.broker.redact_obj(
                {**ap, "status": status, "resolved": iso(self.sched.clock()), **kw}))

    # ── preference store ────────────────────────────────────────────────────
    def _update_prefs(self, job: Job, fn: Callable[[dict], List[dict]]) -> List[dict]:
        """Load, mutate and save the job's prefs file under a lock (atomic, 0600).
        Returns [{"id", "text", "source", "status"}] for the touched rules."""
        from testbeds.email_triage import prefs as P
        if not job.prefs:
            return []
        path = Path(job.prefs)
        with file_lock(path.with_name(path.name + ".lock")):
            prefs = P.load(path, upgrade_to_v2=True) if path.exists() else P.empty_prefs(2)
            touched = fn(prefs)
            errs = P.validate(prefs)
            if errs:
                raise ValueError("prefs would be invalid: " + "; ".join(errs))
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(prefs, indent=2) + "\n")
            tmp.chmod(0o600)
            tmp.replace(path)
        return [{"id": r.get("id"), "text": P.describe_rule(r), "source": P.rule_source(r),
                 "status": P.rule_status(r)} for r in touched]

    def _learn_once(self, action_id: str, job: Job, fn: Callable[[dict], List[dict]]) -> List[dict]:
        ph = self.phases(action_id)
        if "learned" in ph:
            return ph["learned"].get("rules") or []
        rules = self._update_prefs(job, fn)
        self._ledger(action_id, "learned", rules=rules, job=job.name)
        return rules

    def _note(self, text: str) -> str:
        return self.sched.broker.redact(text)

    # ── mail write with read-back ───────────────────────────────────────────
    def _gate(self, job: Job, action_id: str, op: str, target: str) -> Optional[dict]:
        if job.writes == "deny":
            return self._res(action_id, op, target, "refused",
                             f"job {job.name!r} has approvals.writes = deny: AWOS never writes to "
                             "this mailbox. Change the policy in jobs.toml to allow it.")
        try:
            ad = self.adapter(job)
        except Exception as e:
            return self._res(action_id, op, target, "failed", f"cannot open the mailbox: {e}")
        if getattr(ad, "read_only", True):
            return self._res(action_id, op, target, "refused",
                             f"the mailbox adapter for job {job.name!r} is read-only; nothing was "
                             "written. Retry when it can be opened writable.")
        return None

    def _in_inbox(self, job: Job, mid: str) -> Optional[bool]:
        try:
            return "INBOX" in (self.adapter(job).get_message(mid).get("labels") or [])
        except KeyError:
            raise
        except Exception:
            return None

    def _write(self, job: Job, action_id: str, mid: str, folder: str, want_inbox: bool,
               **ledger_kw) -> str:
        """Move ``mid`` to ``folder`` unless the read-back already shows the goal.
        Returns "reconciled" (no write needed) | "written" | "unverified"."""
        if self._in_inbox(job, mid) is want_inbox:
            return "reconciled"
        self._ledger(action_id, "intent", target=mid, folder=folder, job=job.name, **ledger_kw)
        self.adapter(job).move(mid, folder)
        if self.after_write:
            self.after_write(mid)
        return "written" if self._in_inbox(job, mid) is want_inbox else "unverified"

    # ── actions ─────────────────────────────────────────────────────────────
    def approve(self, aid: str) -> dict:
        action_id = f"approve:{aid}"
        ap = self.approvals().get(aid)
        if ap is None:
            return self._res(action_id, "approve", aid, "not_found", f"no approval with id {aid!r}")
        if "done" in self.phases(action_id):
            return self._res(action_id, "approve", aid, "already_done", "already approved and applied")
        if ap.get("status") == "denied":
            return self._res(action_id, "approve", aid, "refused",
                             "this suggestion was denied earlier; nothing was written")
        try:
            job = self.cfg.job(ap.get("job"))
        except JobError as e:
            return self._res(action_id, "approve", aid, "failed", str(e))
        mid = str(ap.get("target") or "")
        if ap.get("op") != "archive":
            self._set_approval(ap, "approved")
            self._ledger(action_id, "done", op="approve", target=mid, job=job.name,
                         run_id=ap.get("run_id"))
            return self._res(action_id, "approve", aid, "done",
                             f"approval recorded for {ap.get('op')}; run the job again to continue "
                             f"({PROG} run {job.name})")
        refused = self._gate(job, action_id, "approve", aid)
        if refused:
            return refused
        item = self.triage_index().get(mid)
        try:
            outcome = self._write(job, action_id, mid, "Archive", want_inbox=False,
                                  run_id=ap.get("run_id"))
        except KeyError:
            return self._res(action_id, "approve", aid, "not_found", f"message {mid!r} is not in the mailbox")
        if outcome == "unverified":
            return self._res(action_id, "approve", aid, "failed",
                             "archive did not read back as archived; check the mailbox and retry")
        msg = self._message(job, mid, item)
        rules = self._learn_once(action_id, job, lambda p: self._evidence(p, msg, item, approve=True))
        self._set_approval(ap, "done")
        self._ledger(action_id, "done", op="approve", target=mid, job=job.name,
                     run_id=ap.get("run_id"), archived=True, rules=rules)
        status = "reconciled" if outcome == "reconciled" else "done"
        return self._res(action_id, "approve", aid, status,
                         ("already archived (read back); recorded" if status == "reconciled"
                          else f"archived message {mid}") + f"; undo: {PROG} undo {ap.get('run_id')}:{mid}",
                         rules=rules)

    def approve_all(self) -> List[dict]:
        return [self.approve(a["id"]) for a in self.sched.pending_approvals()]

    def deny(self, aid: str) -> dict:
        action_id = f"deny:{aid}"
        ap = self.approvals().get(aid)
        if ap is None:
            return self._res(action_id, "deny", aid, "not_found", f"no approval with id {aid!r}")
        if "done" in self.phases(action_id):
            return self._res(action_id, "deny", aid, "already_done", "already denied")
        if ap.get("status") == "done":
            return self._res(action_id, "deny", aid, "refused",
                             f"already approved and applied; use undo {ap.get('run_id')}:{ap.get('target')}")
        try:
            job = self.cfg.job(ap.get("job"))
        except JobError as e:
            return self._res(action_id, "deny", aid, "failed", str(e))
        rules: List[dict] = []
        if ap.get("op") == "archive":
            mid = str(ap.get("target"))
            item = self.triage_index().get(mid)
            try:
                msg = self._message(job, mid, item)
            except ActionError:
                msg = None
            if msg:
                rules = self._learn_once(action_id, job,
                                         lambda p: self._evidence(p, msg, item, approve=False))
        self._set_approval(ap, "denied")
        self._ledger(action_id, "done", op="deny", target=ap.get("target"), job=job.name, rules=rules)
        return self._res(action_id, "deny", aid, "done", "denied; nothing was written", rules=rules)

    def _find_archive(self, archive_id: str) -> Optional[dict]:
        arcs = self.archives()
        for a in arcs:
            if f"{a['run_id']}:{a['id']}" == archive_id:
                return a
        hits = [a for a in arcs if a["id"] == archive_id]
        return hits[-1] if hits else None

    def undo(self, archive_id: str) -> dict:
        a = self._find_archive(archive_id)
        if a is None:
            return self._res(f"undo:{archive_id}", "undo", archive_id, "not_found",
                             f"no archive {archive_id!r} (use <run-id>:<msg-id> from the digest)")
        key = f"{a['run_id']}:{a['id']}"
        action_id = f"undo:{key}"
        mid = a["id"]
        if "done" in self.phases(action_id):
            return self._res(action_id, "undo", key, "already_done", "already back in the inbox")
        try:
            job = self.cfg.job(a["job"])
        except JobError as e:
            return self._res(action_id, "undo", key, "failed", str(e))
        refused = self._gate(job, action_id, "undo", key)
        if refused:
            return refused
        item = self.triage_index().get(mid)
        try:
            outcome = self._write(job, action_id, mid, "INBOX", want_inbox=True, run_id=a["run_id"])
        except KeyError:
            return self._res(action_id, "undo", key, "not_found", f"message {mid!r} is not in the mailbox")
        if outcome == "unverified":
            return self._res(action_id, "undo", key, "failed",
                             "the message did not read back in the INBOX; check the mailbox and retry")
        msg = self._message(job, mid, item)
        rules = self._learn_once(action_id, job, lambda p: self._user_rule(
            p, msg, {"min_importance": 1},
            "You moved this back to the inbox (undo of an AWOS archive): not junk.", key))
        self._ledger(action_id, "done", op="undo", target=mid, job=job.name, run_id=a["run_id"],
                     rules=rules, reconciled=outcome == "reconciled")
        return self._res(action_id, "undo", key, "reconciled" if outcome == "reconciled" else "done",
                         ("already in the inbox (read back); recorded" if outcome == "reconciled"
                          else f"moved message {mid} back to the INBOX"), rules=rules)

    def undo_run(self, run_id: str) -> List[dict]:
        arcs = [a for a in self.archives() if a["run_id"] == run_id]
        if not arcs:
            return [self._res(f"undo-run:{run_id}", "undo", run_id, "not_found",
                              f"run {run_id!r} archived nothing")]
        return [self.undo(f"{a['run_id']}:{a['id']}") for a in arcs]

    def correct(self, mid: str, importance: int, category: Optional[str] = None,
                needs_reply: Optional[bool] = None, note: str = "", job: Optional[str] = None) -> dict:
        if not 0 <= int(importance) <= 3:
            raise ActionError("--importance must be 0-3")
        if category is not None and category not in CATEGORIES:
            raise ActionError(f"--category must be one of {', '.join(CATEGORIES)}")
        set_: Dict[str, Any] = {"importance": int(importance)}
        if category:
            set_["category"] = category
        if needs_reply is not None:
            set_["needs_reply"] = bool(needs_reply)
        action_id = f"correct:{mid}:{_hash([set_, note])}"
        if "done" in self.phases(action_id):
            return self._res(action_id, "correct", mid, "already_done", "this correction is already recorded")
        item = self.triage_index().get(mid)
        try:
            j = self._job_for_msg(mid, job, item)
        except (ActionError, JobError) as e:
            return self._res(action_id, "correct", mid, "not_found", str(e))
        try:
            msg = self._message(j, mid, item)
        except ActionError as e:
            return self._res(action_id, "correct", mid, "not_found", str(e))
        if not j.prefs:
            return self._res(action_id, "correct", mid, "refused",
                             f"job {j.name!r} has no prefs file; nothing to learn into")
        text = note or "Your correction from the morning digest."
        rules = self._learn_once(action_id, j, lambda p: self._user_rule(p, msg, set_, text, mid))
        self._ledger(action_id, "done", op="correct", target=mid, job=j.name, set=set_, rules=rules)
        return self._res(action_id, "correct", mid, "done",
                         "learned: " + "; ".join(r["text"] for r in rules), rules=rules)

    def review(self) -> List[dict]:
        """The review queue (mail the cascade abstained on, still pending), with a
        model-free suggestion and the command to accept or change it."""
        from testbeds.email_triage.baselines import keyword_predict
        from testbeds.email_triage import prefs as P
        corrected = {r.get("target") for r in read_jsonl(self.ledger_path)
                     if r.get("phase") == "done" and r.get("op") == "correct"}
        prefix = cli_prefix(self.cfg)
        out = []
        for mid, it in sorted(self.triage_index().items(), key=lambda kv: kv[1].get("date") or "",
                              reverse=True):
            if not it.get("review") or mid in corrected:
                continue
            msg = None
            try:
                job = self._job_for_msg(mid, None, it)
                msg = self._message(job, mid, it)
                prefs = P.load(Path(job.prefs)) if job.prefs and Path(job.prefs).exists() else P.empty_prefs()
                hints = P.hints_for(msg, prefs, k=3)
            except Exception:
                hints = []
            m = msg or {"id": mid, "subject": it.get("subject") or "", "body": "",
                        "from_addr": it.get("from_addr") or "", "headers": {}}
            m = {**m, "subject": m.get("subject") or "", "body": m.get("body") or "",
                 "from_addr": m.get("from_addr") or ""}
            sug = keyword_predict(m)
            out.append(self.sched.broker.redact_obj({
                "id": mid, "run_id": it.get("run_id"), "subject": it.get("subject"),
                "from": it.get("from_addr"), "reason": it.get("review_reason"),
                "current": {k: it.get(k) for k in ("importance", "category", "needs_reply")},
                "suggested": {k: sug[k] for k in ("importance", "category", "needs_reply")},
                "hints": hints, "command": correct_cmd(prefix, {"id": mid, **sug})}))
        return out

    # ── learning helpers ────────────────────────────────────────────────────
    def _user_rule(self, prefs: dict, msg: dict, set_: dict, note: str, ref: str) -> List[dict]:
        from testbeds.email_triage import prefs as P
        from ..correction_capture import scope_for
        scope, when = scope_for(msg)
        r = P.add_rule(prefs, when, set_, source="user", scope=scope, note=self._note(note),
                       now=iso(self.sched.clock()),
                       provenance={"message_ids": [str(msg.get("id"))], "signal": f"digest:{ref}"})
        return [r]

    def _evidence(self, prefs: dict, msg: dict, item: Optional[dict], approve: bool) -> List[dict]:
        from testbeds.email_triage import prefs as P
        from ..correction_capture import scope_for
        scope, when = scope_for(msg)
        set_: Dict[str, Any] = {"importance": 0}
        cat = (item or {}).get("category")
        if approve and cat in P.JUNK_CATEGORIES:
            set_["category"] = cat
        r = P.record_evidence(
            prefs, when, set_, thread_id=str(msg.get("thread_id") or msg.get("id")),
            message_id=str(msg.get("id")), source="implicit", scope=scope,
            effect="include" if approve else "exclude",
            model_agreed=True if approve else None,
            note=self._note("You approved archiving this." if approve
                            else "You kept this: denied the suggested archive."),
            now=iso(self.sched.clock()))
        return [r]

    # ── recent learning (for the digest) ────────────────────────────────────
    def learned_since(self, since: Optional[str]) -> List[dict]:
        cut = parse_iso(since)
        out = []
        for r in read_jsonl(self.ledger_path):
            if r.get("phase") != "learned" or not r.get("rules"):
                continue
            at = parse_iso(r.get("at"))
            if cut is not None and at is not None and at <= cut:
                continue
            op = str(r.get("action_id", "")).split(":", 1)[0]
            for rule in r["rules"]:
                out.append({"op": op, "action_id": r.get("action_id"), **rule})
        return out

    # ── apply ───────────────────────────────────────────────────────────────
    def apply(self, path: Path) -> List[dict]:
        out: List[dict] = []
        for n, raw in enumerate(Path(path).read_text().splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                toks = shlex.split(line)
            except ValueError as e:
                out.append(self._res(f"apply:{n}", "apply", f"line {n}", "failed", f"cannot parse: {e}"))
                continue
            # tolerate a pasted full command line
            if toks[:3] == PROG.split():
                toks = toks[3:]
                while toks and toks[0] in ("--jobs", "--home"):
                    toks = toks[2:]
            try:
                out.extend(self.dispatch(toks))
            except (ActionError, SystemExit) as e:
                out.append(self._res(f"apply:{n}", "apply", f"line {n}", "failed",
                                     f"bad action line: {e}"))
        return out

    def dispatch(self, toks: List[str]) -> List[dict]:
        a = action_parser().parse_args(toks)
        return run_action(self, a)


# ── argparse shared by the CLI and apply ────────────────────────────────────
def add_action_parsers(sub) -> None:
    p = sub.add_parser("approve", help="carry out a queued write")
    p.add_argument("id", nargs="?")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("deny", help="drop a queued write")
    p.add_argument("id")
    p = sub.add_parser("undo", help="move an archived message back to the INBOX")
    p.add_argument("archive_id", nargs="?")
    p.add_argument("--run", dest="run_id")
    p = sub.add_parser("correct", help="correct a label; becomes a user rule")
    p.add_argument("msg_id")
    p.add_argument("--importance", type=int, required=True)
    p.add_argument("--category")
    p.add_argument("--needs-reply", dest="needs_reply")
    p.add_argument("--note", default="")
    p.add_argument("--job")


ACTION_CMDS = ("approve", "deny", "undo", "correct")


def action_parser():
    import argparse

    class P(argparse.ArgumentParser):
        def error(self, message):
            raise ActionError(message)
    ap = P(prog="action", add_help=False)
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=P)
    add_action_parsers(sub)
    return ap


def run_action(acts: Actions, a) -> List[dict]:
    if a.cmd == "approve":
        if a.all:
            return acts.approve_all()
        if not a.id:
            raise ActionError("approve needs an id or --all")
        return [acts.approve(a.id)]
    if a.cmd == "deny":
        return [acts.deny(a.id)]
    if a.cmd == "undo":
        if a.run_id:
            return acts.undo_run(a.run_id)
        if not a.archive_id:
            raise ActionError("undo needs <run-id>:<msg-id> or --run <run-id>")
        return [acts.undo(a.archive_id)]
    if a.cmd == "correct":
        return [acts.correct(a.msg_id, a.importance, a.category, _yn(a.needs_reply), a.note, a.job)]
    raise ActionError(f"unknown action {a.cmd!r}")


def format_result(r: dict) -> str:
    return f"{r.get('status', ''):12} {r.get('op', '')} {r.get('target', '')}: {r.get('message', '')}"


# ── actions.todo template (written next to the digest) ──────────────────────
def todo_lines(d: dict, prefix: str) -> List[str]:
    L = ["# AWOS actions for this digest. Uncomment (delete the leading '# ') the lines you want,",
         "# edit values if needed, then run:",
         f"#   {prefix} apply <this file>",
         "# Lines are idempotent: running the file twice does nothing new.", ""]

    def block(title: str, rows: Iterable[str]):
        rows = list(rows)
        if rows:
            L.append(f"## {title}")
            L.extend(f"# {r}" for r in rows)
            L.append("")
    block("Waiting for your approval (approve or deny)",
          [x for a in d.get("approvals", []) for x in (f"approve {_q(a['id'])}", f"deny {_q(a['id'])}")])
    block("Archived: move back to the inbox",
          [f"undo {_q(str(m.get('run_id')) + ':' + str(m['id']))}"
           for m in d.get("archived", []) if not m.get("undone")])
    block("Check these (I was not sure): set the right label",
          [correct_cmd("", m).strip() for m in d.get("review", [])])
    block("Urgent / needs a reply: correct if wrong",
          [correct_cmd("", m).strip() for m in d.get("urgent", []) + d.get("needs_reply", [])])
    return L

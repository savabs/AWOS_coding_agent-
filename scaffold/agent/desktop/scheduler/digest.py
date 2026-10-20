"""digest.py: the morning digest, a local HTML page plus a plain-text summary.

Built from what the scheduler wrote since the last digest (runs.jsonl, each
run's triage.json, approvals.jsonl, the preference store):

  * urgent emails, each with why (guard / rule / model)
  * needs-reply list
  * review queue: the cascade abstained, so the mail stayed in the inbox
  * what was archived, with the undo for each
  * every item carries an ``id`` and a copyable CLI command (approve / deny /
    undo / correct, scheduler/actions.py); ``actions.todo`` next to the page is
    the same commands, commented, for ``scheduler apply <file>``
  * "Learned from your corrections": rules the owner's actions created since the
    last digest
  * learned-rule changes since the last digest, plus the full "what I learned"
    (testbeds/email_triage/prefs.py render_learned)
  * skills / micro-tasks that halted or need the owner (one-step fix demo)
  * tasks waiting for approval (queued writes)
  * injections and phishing that were flagged
  * every run's outcome, including the ones that did not start and why

Private by construction: one self-contained file, no scripts, no remote
fonts/images/links, a Content-Security-Policy that blocks every fetch, and every
string passed through the WP7 SecretBroker redaction and HTML-escaped. Works
offline. The optional macOS notification (osascript) carries counts only, never
mail content, and is off unless asked for.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import actions as actions_mod
from .jobs import in_window, slot_on
from .runner import Outcome, Scheduler, iso, parse_iso, read_json, read_jsonl

DEFAULT_WINDOW_H = 36


# ── collect ─────────────────────────────────────────────────────────────────
def _rule_sig(r: dict) -> str:
    """What a rule does, not its bookkeeping: hit counters, timestamps and a
    v1 -> v2 upgrade (which only fills in defaults) are not changes."""
    from testbeds.email_triage import prefs as P
    return json.dumps({"when": r.get("when"), "set": r.get("set"), "effect": P.rule_effect(r),
                       "status": P.rule_status(r), "source": P.rule_source(r),
                       "scope": P.rule_scope(r)}, sort_keys=True, default=str)


def prefs_snapshot(prefs: dict) -> Dict[str, str]:
    return {str(r.get("id")): _rule_sig(r) for r in prefs.get("rules", []) or []}


def learned_changes(old: Optional[Dict[str, str]], prefs: dict) -> Dict[str, List[str]]:
    from testbeds.email_triage import prefs as P
    new = prefs_snapshot(prefs)
    by_id = {str(r.get("id")): r for r in prefs.get("rules", []) or []}
    old = old or {}

    def line(rid: str) -> str:
        r = by_id[rid]
        return f"{P.describe_rule(r)} [{rid}, {P.rule_source(r)}, {P.rule_status(r)}]"
    return {"added": [line(i) for i in new if i not in old],
            "changed": [line(i) for i in new if i in old and old[i] != new[i]],
            "removed": [f"rule {i}" for i in old if i not in new]}


def collect(sched: Scheduler, now: Optional[datetime] = None) -> Dict[str, Any]:
    from testbeds.email_triage import prefs as P
    now = now or sched.clock()
    st = sched.state()
    dg = st.get("digest", {})
    since = parse_iso(dg.get("last_at")) or (now - timedelta(hours=DEFAULT_WINDOW_H))
    runs = [r for r in read_jsonl(sched.runs_path)
            if r.get("ended") and (parse_iso(r["ended"]) or now) > since
            and r.get("outcome") != Outcome.DUPLICATE.value]
    mail: Dict[str, dict] = {}
    for r in runs:
        if r.get("kind") != "email_triage" or r.get("outcome") in (Outcome.DEFERRED.value,):
            continue
        tri = read_json(sched.run_dir(r["run_id"]) / "triage.json", None)
        if not tri:
            continue
        for it in tri.get("items", []):
            mail[it["id"]] = {**it, "run_id": r["run_id"], "job": r["job"]}
    items = sorted(mail.values(), key=lambda x: x.get("date") or "", reverse=True)
    urgent = [m for m in items if m.get("importance") == 3 and not m.get("suspicious")]
    flagged = [m for m in items if m.get("suspicious") or m.get("category") == "spam_phishing"
               or "injection" in (m.get("guards") or [])]
    flagged_ids = {m["id"] for m in flagged}
    needs_reply = [m for m in items if m.get("needs_reply") and m["id"] not in flagged_ids]
    acts = actions_mod.Actions(sched)
    corrected = {r.get("target") for r in read_jsonl(acts.ledger_path)
                 if r.get("phase") == "done" and r.get("op") == "correct"}
    review = [m for m in items if m.get("review") and m["id"] not in corrected]
    archived = [m for m in items if (m.get("action") or {}).get("status") == "done"]
    # archives the owner approved since the last digest (they were queued in the run)
    tri = None
    for r in read_jsonl(acts.ledger_path):
        if (r.get("phase") == "done" and r.get("op") == "approve" and r.get("archived")
                and (parse_iso(r.get("at")) or now) > since):
            tri = tri if tri is not None else acts.triage_index()
            it = tri.get(r.get("target")) or {"id": r.get("target")}
            if not any(m["id"] == it["id"] and m.get("run_id") == r.get("run_id") for m in archived):
                archived.append({**it, "run_id": r.get("run_id"), "job": r.get("job"),
                                 "why_source": "you", "why": "you approved this archive"})
    undone = acts.undone()
    for m in archived:
        m["undone"] = f"{m.get('run_id')}:{m['id']}" in undone
    archive_runs = sorted({m.get("run_id") for m in archived if not m.get("undone")} - {None})
    # skills / micro-tasks: the latest run per job that halted or needs the owner
    halted: Dict[str, dict] = {}
    for r in runs:
        if r.get("kind") in ("skill", "micro_task"):
            if r.get("outcome") in (Outcome.HALTED.value, Outcome.NEEDS_USER.value,
                                    Outcome.RECONCILIATION_REQUIRED.value):
                halted[r["job"]] = r
            elif r.get("outcome") in (Outcome.VERIFIED.value, Outcome.COMPLETED_UNVERIFIED.value):
                halted.pop(r["job"], None)
    # learned rules, per preference store used by an email job
    learned = []
    snaps = {}
    for job in sched.cfg.jobs:
        if job.kind != "email_triage" or not job.prefs or job.prefs in snaps:
            continue
        p = Path(job.prefs)
        prefs = P.load(p) if p.exists() else P.empty_prefs()
        snaps[job.prefs] = prefs_snapshot(prefs)
        learned.append({"prefs": job.prefs,
                        "changes": learned_changes((dg.get("prefs") or {}).get(job.prefs), prefs),
                        "first_digest": job.prefs not in (dg.get("prefs") or {}),
                        "text": P.render_learned(prefs)})
    data = {
        "generated": iso(now), "since": iso(since),
        "local_time": now.astimezone(sched.cfg.tz).strftime("%A %d %B %Y, %H:%M"),
        "urgent": urgent, "needs_reply": needs_reply, "review": review, "archived": archived,
        "flagged": flagged, "learned": learned, "halted": list(halted.values()),
        "learned_from_you": acts.learned_since(dg.get("last_at")),
        "archive_runs": archive_runs, "cmd_prefix": actions_mod.cli_prefix(sched.cfg),
        "approvals": sched.pending_approvals(),
        "runs": [{k: r.get(k) for k in ("run_id", "job", "kind", "outcome", "reason", "resumed",
                                        "missed_slots", "ended")} for r in runs],
        "messages_seen": len(items),
    }
    return {"data": sched.broker.redact_obj(data), "snaps": snaps}


# ── render ──────────────────────────────────────────────────────────────────
def _who(m: dict) -> str:
    n, a = m.get("from_name") or "", m.get("from_addr") or ""
    return f"{n} <{a}>" if n else a


def counts(d: dict) -> Dict[str, int]:
    return {k: len(d[k]) for k in ("urgent", "needs_reply", "review", "archived", "flagged",
                                   "halted", "approvals")}


def _cmds(d: dict, kind: str, x: Any) -> List[str]:
    """The copyable command(s) for one digest item."""
    pre = d.get("cmd_prefix") or actions_mod.PROG
    q = actions_mod._q
    if kind == "approval":
        return [f"{pre} approve {q(x.get('id'))}", f"{pre} deny {q(x.get('id'))}"]
    if kind == "archived":
        return [] if x.get("undone") else [f"{pre} undo {q(str(x.get('run_id')) + ':' + str(x['id']))}"]
    if kind == "run":
        return [f"{pre} undo --run {q(x)}"]
    return [actions_mod.correct_cmd(pre, x)]


def _anchor(kind: str, ident: Any) -> str:
    return f"{kind}-" + re.sub(r"[^A-Za-z0-9_.:@-]+", "-", str(ident))


def render_text(d: dict) -> str:
    c = counts(d)
    L = [f"AWOS morning digest, {d['local_time']}",
         f"{d['messages_seen']} message(s) triaged since {d['since']}; "
         f"{c['urgent']} urgent, {c['needs_reply']} need a reply, {c['review']} to check, "
         f"{c['archived']} archived, {c['flagged']} flagged, {c['approvals']} waiting for approval.", ""]

    def sec(title, rows, fmt):
        L.append(f"== {title} ({len(rows)})")
        if not rows:
            L.append("  (none)")
        for r in rows:
            L.extend("  " + x for x in fmt(r))
        L.append("")
    def cmd(kind, x, label):
        return [f"    {label}: {c_}" for c_ in _cmds(d, kind, x)]
    sec("Urgent", d["urgent"], lambda m: [f"- {m.get('subject')} | {_who(m)} [id {m['id']}]",
                                          f"    why ({m.get('why_source')}): {m.get('why')}"]
        + cmd("mail", m, "if wrong"))
    sec("Needs a reply", d["needs_reply"], lambda m: [f"- {m.get('subject')} | {_who(m)} [id {m['id']}]"]
        + cmd("mail", m, "if wrong"))
    sec("Check these (I was not sure; left in your inbox)", d["review"],
        lambda m: [f"- {m.get('subject')} | {_who(m)} ({m.get('review_reason')}) [id {m['id']}]"]
        + cmd("mail", m, "set label"))
    sec("Archived", d["archived"], lambda m: [
        f"- {m.get('subject')} | {_who(m)} [id {m.get('run_id')}:{m['id']}]"
        + (" (moved back to the inbox)" if m.get("undone") else ""),
        f"    why ({m.get('why_source')}): {m.get('why')}"]
        + ([] if m.get("undone") else
           [f"    undo: move message {m['id']} back to the Inbox (run {m.get('run_id')})"]
           + [f"      {c_}" for c_ in _cmds(d, "archived", m)]))
    if d.get("archive_runs"):
        L.extend(f"  undo everything run {r} archived: {_cmds(d, 'run', r)[0]}" for r in d["archive_runs"])
        L.append("")
    sec("Flagged: phishing / prompt injection", d["flagged"], lambda m: [
        f"- {m.get('subject')} | {_who(m)} [id {m['id']}]",
        f"    why ({m.get('why_source')}): {m.get('why')}"] + cmd("mail", m, "if wrong"))
    sec("Waiting for your approval", d["approvals"], lambda a: [
        f"- [{a.get('op')}] {a.get('summary')} (id {a.get('id')})"]
        + [f"    {c_}" for c_ in _cmds(d, "approval", a)])
    sec("Learned from your corrections", d.get("learned_from_you", []), lambda r: [
        f"- {r.get('text')} [{r.get('id')}, {r.get('source')}, {r.get('status')}] (from {r.get('op')})"])
    sec("Skills that stopped and need you", d["halted"], lambda r: [
        f"- {r.get('job')}: {r.get('outcome')}: {r.get('reason')}",
        "    fix: demonstrate the one step it could not do; the next run uses the taught step"])
    L.append("== Learned rules")
    if not d["learned"]:
        L.append("  (no preference store configured)")
    for lr in d["learned"]:
        ch = lr["changes"]
        L.append(f"  store: {lr['prefs']}" + ("  (first digest: all rules are new)"
                                              if lr["first_digest"] else ""))
        for k in ("added", "changed", "removed"):
            for x in ch[k]:
                L.append(f"  {k}: {x}")
        if not any(ch.values()):
            L.append("  no changes since the last digest")
    L.append("")
    sec("Runs", d["runs"], lambda r: [
        f"- {r.get('job')}: {r.get('outcome')} {('(' + str(r.get('reason')) + ')') if r.get('reason') else ''}"
        + (" [resumed]" if r.get("resumed") else "")
        + (f" [{r.get('missed_slots')} missed slot(s) skipped]" if r.get("missed_slots") else "")])
    return "\n".join(L).rstrip() + "\n"


CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1f;--muted:#6e6e73;--card:#fff;--line:#e5e3dc;--urgent:#b42318;
--warn:#b54708;--ok:#067647;--chip:#f2f0ea}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#ececec;--muted:#a1a1a6;--card:#1f1f1f;
--line:#333;--urgent:#ff8a80;--warn:#ffb74d;--ok:#81c995;--chip:#2a2a2a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px;display:flex;gap:8px;align-items:center}
.sub{color:var(--muted);margin:0 0 16px}.n{background:var(--chip);border-radius:10px;padding:0 8px;font-size:13px}
ul{list-style:none;margin:0;padding:0}li{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:10px 12px;margin:6px 0}.s{font-weight:600;overflow-wrap:anywhere}.f{color:var(--muted);font-size:13px;
overflow-wrap:anywhere}.why{font-size:13px;margin-top:4px}.tag{font-size:12px;border-radius:4px;padding:1px 6px;
background:var(--chip);margin-right:4px}.urgent .s{color:var(--urgent)}.flag .s{color:var(--warn)}
.empty{color:var(--muted);font-size:14px}.cmd{margin-top:6px}
.cmd code{display:block;user-select:all;-webkit-user-select:all;background:var(--chip);border-radius:4px;
padding:4px 6px;overflow-wrap:anywhere;white-space:pre-wrap}code,pre{font:12px/1.45 ui-monospace,Menlo,monospace}
pre{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:6px 4px;
text-align:left;vertical-align:top}.ok{color:var(--ok)}.bad{color:var(--urgent)}
"""


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def _cmd_html(cmds: List[str]) -> str:
    return "".join(f'<div class="cmd"><code>{_e(c)}</code></div>' for c in cmds)


def _mail_li(m: dict, cls: str = "", extra: str = "", anchor: str = "",
             cmds: Optional[List[str]] = None, ident: Optional[str] = None) -> str:
    why = (f'<div class="why"><span class="tag">{_e(m.get("why_source"))}</span>{_e(m.get("why"))}</div>'
           if m.get("why") else "")
    idt = ident or m.get("id")
    return (f'<li class="{cls}" id="{_e(_anchor(anchor or "mail", idt))}">'
            f'<div class="s">{_e(m.get("subject"))}</div>'
            f'<div class="f">{_e(_who(m))} · {_e(m.get("date"))} · {_e(m.get("category"))} · '
            f'id <code>{_e(idt)}</code></div>'
            f"{why}{extra}{_cmd_html(cmds or [])}</li>")


def _section(title: str, rows: List[str], empty: str = "Nothing here.") -> str:
    body = "<ul>" + "".join(rows) + "</ul>" if rows else f'<p class="empty">{_e(empty)}</p>'
    return f'<section><h2>{_e(title)} <span class="n">{len(rows)}</span></h2>{body}</section>'


def render_html(d: dict) -> str:
    c = counts(d)
    runs_undo = "".join(
        f'<p class="f" id="{_e(_anchor("run", r))}">Undo everything run <code>{_e(r)}</code> archived:</p>'
        + _cmd_html(_cmds(d, "run", r)) for r in d.get("archive_runs", []))
    parts = [
        _section("Urgent", [_mail_li(m, "urgent", anchor="urgent", cmds=_cmds(d, "mail", m))
                            for m in d["urgent"]], "Nothing urgent."),
        _section("Needs a reply", [_mail_li(m, anchor="reply", cmds=_cmds(d, "mail", m))
                                   for m in d["needs_reply"]]),
        _section("Check these (I was not sure, so they stayed in your inbox)",
                 [_mail_li(m, extra=f'<div class="f">reason: {_e(m.get("review_reason"))}</div>',
                           anchor="review", cmds=_cmds(d, "mail", m))
                  for m in d["review"]]),
        _section("Archived", [_mail_li(m, extra=(
            '<div class="f">moved back to the inbox</div>' if m.get("undone") else ""),
            anchor="archived", cmds=_cmds(d, "archived", m), ident=f'{m.get("run_id")}:{m["id"]}')
            for m in d["archived"]], "Nothing was archived.") + runs_undo,
        _section("Flagged: phishing or prompt injection",
                 [_mail_li(m, "flag", anchor="flagged", cmds=_cmds(d, "mail", m)) for m in d["flagged"]],
                 "Nothing suspicious."),
        _section("Waiting for your approval", [
            f'<li id="{_e(_anchor("approval", a.get("id")))}"><div class="s">{_e(a.get("op"))}: '
            f'{_e(a.get("summary"))}</div>'
            f'<div class="f">id <code>{_e(a.get("id"))}</code></div>{_cmd_html(_cmds(d, "approval", a))}</li>'
            for a in d["approvals"]]),
        _section("Learned from your corrections", [
            f'<li id="{_e(_anchor("learned", r.get("id")))}"><span class="tag">{_e(r.get("op"))}</span>'
            f'{_e(r.get("text"))} <span class="f">[{_e(r.get("id"))}, {_e(r.get("source"))}, '
            f'{_e(r.get("status"))}]</span></li>' for r in d.get("learned_from_you", [])],
            "No corrections since the last digest."),
        _section("Skills that stopped and need you", [
            f'<li><div class="s">{_e(r.get("job"))}: {_e(r.get("outcome"))}</div>'
            f'<div class="why">{_e(r.get("reason"))}</div><div class="f">Fix: demonstrate the one step it '
            f'could not do; the next run uses the taught step.</div></li>' for r in d["halted"]]),
    ]
    lr_html = []
    for lr in d["learned"]:
        ch = lr["changes"]
        rows = [f'<li><span class="tag">{_e(k)}</span>{_e(x)}</li>'
                for k in ("added", "changed", "removed") for x in ch[k]]
        note = " (first digest: every rule is listed as new)" if lr["first_digest"] else ""
        lr_html.append(f'<p class="f">store <code>{_e(lr["prefs"])}</code>{_e(note)}</p>'
                       + ("<ul>" + "".join(rows) + "</ul>" if rows
                          else '<p class="empty">No rule changes since the last digest.</p>')
                       + f"<details><summary>Everything I have learned</summary><pre>{_e(lr['text'])}</pre>"
                         "</details>")
    parts.append('<section><h2>Learned rules</h2>' + ("".join(lr_html) or
                 '<p class="empty">No preference store configured.</p>') + "</section>")
    run_rows = "".join(
        f'<tr><td>{_e(r.get("job"))}</td><td class="{"ok" if r.get("outcome") == "VERIFIED" else "bad" if r.get("outcome") in ("FAILED", "RECONCILIATION_REQUIRED") else ""}">'
        f'{_e(r.get("outcome"))}</td><td>{_e(r.get("reason"))}'
        f'{" (resumed)" if r.get("resumed") else ""}'
        f'{" (" + _e(r.get("missed_slots")) + " missed slot(s) skipped)" if r.get("missed_slots") else ""}'
        f'</td><td>{_e(r.get("ended"))}</td></tr>' for r in d["runs"])
    parts.append('<section><h2>Runs <span class="n">' + str(len(d["runs"])) + '</span></h2>'
                 + ('<table><tr><th>job</th><th>outcome</th><th>detail</th><th>ended (UTC)</th></tr>'
                    + run_rows + "</table>" if run_rows else '<p class="empty">No runs.</p>')
                 + "</section>")
    summary = (f"{d['messages_seen']} message(s) since {d['since']}: {c['urgent']} urgent, "
               f"{c['needs_reply']} need a reply, {c['review']} to check, {c['archived']} archived, "
               f"{c['flagged']} flagged, {c['approvals']} waiting for approval.")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
            "style-src 'unsafe-inline'; img-src 'none'; form-action 'none'; base-uri 'none'\">"
            "<meta name=\"referrer\" content=\"no-referrer\">"
            f"<title>AWOS digest</title><style>{CSS}</style></head><body><main>"
            f"<h1>Good morning</h1><p class=\"sub\">{_e(d['local_time'])} · {_e(summary)}</p>"
            + "".join(parts) +
            "<p class=\"f\">Made on this Mac by AWOS. Nothing in this page loads from the network.</p>"
            "</main></body></html>\n")


# ── write / notify ──────────────────────────────────────────────────────────
def notify(text: str, run: Callable = subprocess.run) -> bool:
    """macOS notification with counts only. Never called in tests (pass ``run``)."""
    safe = text.replace("\\", "").replace('"', "'")[:200]
    try:
        r = run(["osascript", "-e", f'display notification "{safe}" with title "AWOS digest"'],
                capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def write_digest(sched: Scheduler, now: Optional[datetime] = None, out_dir: Optional[Path] = None,
                 do_notify: bool = False, run: Callable = subprocess.run,
                 slot: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or sched.clock()
    got = collect(sched, now)
    d = got["data"]
    out_dir = Path(out_dir or (sched.home / "digest"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone(sched.cfg.tz).strftime("%Y-%m-%d_%H%M")
    html_p, txt_p = out_dir / f"digest-{stamp}.html", out_dir / f"digest-{stamp}.txt"
    text = sched.broker.redact(render_text(d))
    page = sched.broker.redact(render_html(d))
    todo = sched.broker.redact("\n".join(actions_mod.todo_lines(d, d.get("cmd_prefix") or "")) + "\n")
    todo_p = out_dir / f"actions-{stamp}.todo"
    for p, body in ((html_p, page), (txt_p, text), (out_dir / "latest.html", page),
                    (out_dir / "latest.txt", text), (todo_p, todo), (out_dir / "actions.todo", todo)):
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(body)
        tmp.chmod(0o600)
        tmp.replace(p)
    c = counts(d)
    notified = False
    if do_notify and not in_window(sched.cfg.digest_quiet, now.astimezone(sched.cfg.tz)):
        notified = notify(f"{c['urgent']} urgent, {c['needs_reply']} to reply, {c['review']} to check, "
                          f"{c['approvals']} to approve", run=run)

    def fn(st):
        dg = st.setdefault("digest", {})
        dg["last_at"] = iso(now)
        dg["last_html"], dg["last_text"] = str(html_p), str(txt_p)
        dg.setdefault("prefs", {}).update(got["snaps"])
        if slot is not None:
            dg["last_slot"] = iso(slot)
    sched.update_state(fn)
    return {"html": str(html_p), "text": str(txt_p), "todo": str(todo_p), "counts": c,
            "notified": notified,
            "summary": text}


def digest_due(sched: Scheduler, now: Optional[datetime] = None) -> Optional[datetime]:
    """The digest slot to write now (config ``[digest] at``), once per day."""
    at = sched.cfg.digest_at
    if at is None:
        return None
    now = (now or sched.clock()).astimezone(timezone.utc)
    day = now.astimezone(sched.cfg.tz).date()
    s = slot_on(day, at, sched.cfg.tz)
    if s > now:
        s = slot_on(day - timedelta(days=1), at, sched.cfg.tz)
    last = parse_iso(sched.state().get("digest", {}).get("last_slot"))
    if last is not None and s <= last:
        return None
    if (now - s).total_seconds() > sched.cfg.catchup_s:
        return None
    return s

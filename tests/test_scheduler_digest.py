"""WP9 scheduler + morning digest (scaffold/agent/desktop/scheduler/).

No model calls, no real mailbox, no launchctl, no network beyond 127.0.0.1:
email runs use testbeds/email_triage on a FakeMailbox with the scripted
keyword_prefs agent; machine state comes from a fake probe.
"""
from __future__ import annotations

import contextlib
import json
import plistlib
import re
import shutil
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scaffold.agent.desktop.mail_adapter import FakeMailbox
from scaffold.agent.desktop.scheduler import digest as dg
from scaffold.agent.desktop.scheduler import launchd
from scaffold.agent.desktop.scheduler.__main__ import main as cli
from scaffold.agent.desktop.scheduler.jobs import (JobError, due_slot, latest_slot, load_config,
                                                    parse_config, parse_schedule, slots_between)
from scaffold.agent.desktop.scheduler.runner import (Hooks, Scheduler, file_lock,
                                                      read_jsonl, run_id_for)
from scaffold.agent.local_runtime import PowerState
from testbeds.email_triage import prefs as P

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
NOW = datetime(2026, 10, 19, 11, 0, tzinfo=UTC)          # 07:00 in New York
SECRET = "sk-ant-api03-" + "Q" * 40


class Kill(BaseException):
    """A hard stop (lid close + launchd kill / power loss) in the middle of a run."""


class FakeProbe:
    def __init__(self, **kw):
        self.source = kw.get("source", "ac")
        self.idle = kw.get("idle", 3600.0)
        self.locked = kw.get("locked", False)
        self.lease_list = kw.get("leases", [])
        self.mem = kw.get("mem", (True, "fits"))
        self.up = kw.get("up", False)
        self.calls = []

    def power(self):
        return PowerState(self.source, 80)

    def idle_seconds(self):
        return self.idle

    def screen_locked(self):
        return self.locked

    def leases(self):
        return list(self.lease_list)

    def memory(self, power):
        return self.mem

    def server_up(self):
        return self.up

    def model_script(self, action):
        self.calls.append(action)
        self.up = action == "start"
        return True

    @contextlib.contextmanager
    def lease(self, run_id):
        self.calls.append(f"lease:{run_id}")
        yield None


# ── fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """One email_triage testbed run (scripted keyword_prefs agent): a mailbox and
    the prefs.json learned from the simulated user's calibration feedback."""
    from testbeds.email_triage.run import run_arm
    w = tmp_path_factory.mktemp("et")
    run_arm(w, 7, agent="keyword_prefs")
    return w


@pytest.fixture
def env(seeded, tmp_path):
    mb = tmp_path / "mailbox"
    shutil.copytree(seeded / "mailbox", mb)
    prefs = tmp_path / "prefs.json"
    shutil.copy(seeded / "agent" / "prefs.json", prefs)
    prefs.chmod(0o600)
    return {"mailbox": mb, "prefs": prefs, "home": tmp_path / "home", "tmp": tmp_path}


def cfg_for(env, jobs, **sched):
    data = {"scheduler": {"tz": "America/New_York", **sched}, "job": jobs}
    return parse_config(data, home=str(env["home"]))


def email_job(env, **kw):
    j = {"name": "inbox", "kind": "email_triage", "schedule": "daily 02:00",
         "mailbox": f"fake:{env['mailbox']}", "agent": "keyword_prefs", "prefs": str(env["prefs"]),
         "chunk": 25, "limit": 0}
    j.update(kw)
    return j


def make(env, jobs, probe=None, mailbox=None, hooks=None, clock=None):
    cfg = cfg_for(env, jobs)
    box = {}

    def factory(job):
        if mailbox is not None:
            return mailbox
        if "mb" not in box:
            box["mb"] = FakeMailbox(job.mailbox_source[1], read_only=job.writes != "auto_bulk")
        return box["mb"]
    return Scheduler(cfg, probe=probe or FakeProbe(), hooks=hooks, adapter_factory=factory,
                     clock=clock or (lambda: NOW))


# ── job parsing ─────────────────────────────────────────────────────────────
def test_jobs_toml_parsing(env, tmp_path):
    f = tmp_path / "jobs.toml"
    f.write_text(f"""
[scheduler]
tz = "America/New_York"
home = "{env['home']}"
catchup_hours = 12

[digest]
at = "07:30"
notify = true
quiet_hours = "22:00-07:00"

[[job]]
name = "inbox-overnight"
kind = "email_triage"
schedule = "daily 02:00"
mailbox = "fake:{env['mailbox']}"
prefs = "{env['prefs']}"
[job.constraints]
ac_power = true
idle = true
max_runtime = "20m"
quiet_hours = "09:00-17:00"
[job.approvals]
writes = "auto_bulk"

[[job]]
name = "imap-inbox"
kind = "email_triage"
schedule = "every 30m"
mailbox = "imap:someone@gmail.com"
agent = "local_model"

[[job]]
name = "weekly-report"
kind = "skill"
schedule = "mon,thu 06:15"
command = ["python", "-m", "testbeds.teach_once.skill_runner", "--run-dir", "/tmp/x"]

[[job]]
name = "dash-key"
kind = "micro_task"
schedule = "weekdays 08:00"
command = ["python", "-m", "scaffold.agent.desktop.cu_agent", "micro"]
""")
    cfg = load_config(str(f))
    assert cfg.home == env["home"] and cfg.catchup_s == 12 * 3600
    assert cfg.digest_at.hour == 7 and cfg.digest_notify and cfg.digest_quiet
    a, b, c, d = cfg.jobs
    assert a.kind == "email_triage" and a.writes == "auto_bulk" and not a.uses_model
    assert a.constraints.ac_power and a.constraints.idle_s == 300
    assert a.constraints.max_runtime_s == 1200 and a.constraints.quiet_hours
    assert a.mailbox_source == ("fake", str(env["mailbox"])) and not a.constraints.unlocked
    assert b.uses_model and b.mailbox_source == ("imap", "someone@gmail.com") and b.writes == "queue"
    assert b.schedule.kind == "every" and b.schedule.every_s == 1800
    assert c.schedule.days == (0, 3) and not c.uses_model and c.constraints.unlocked
    assert d.schedule.days == (0, 1, 2, 3, 4) and d.uses_model
    assert load_config(str(tmp_path / "missing.toml"), home=str(tmp_path)).jobs == []


@pytest.mark.parametrize("bad, msg", [
    ({"name": "x", "kind": "cron", "schedule": "daily 01:00"}, "kind"),
    ({"name": "x", "kind": "skill", "schedule": "hourly", "command": ["a"]}, "schedule"),
    ({"name": "x", "kind": "skill", "schedule": "daily 25:00", "command": ["a"]}, "HH:MM"),
    ({"name": "x", "kind": "skill", "schedule": "every 1m", "command": ["a"]}, "5 minutes"),
    ({"name": "x", "kind": "skill", "schedule": "daily 01:00", "command": "rm -rf /"}, "command"),
    ({"name": "x", "kind": "email_triage", "schedule": "daily 01:00", "mailbox": "pop:me"}, "mailbox"),
    ({"name": "x", "kind": "skill", "schedule": "daily 01:00", "command": ["a"],
      "approvals": {"writes": "yolo"}}, "approvals"),
    ({"name": "bad name!", "kind": "skill", "schedule": "daily 01:00", "command": ["a"]}, "name"),
])
def test_jobs_rejects_bad_definitions(bad, msg):
    with pytest.raises(JobError, match=msg):
        parse_config({"job": [bad]}, home="/tmp/h")


def test_duplicate_job_names_rejected():
    j = {"name": "x", "kind": "skill", "schedule": "daily 01:00", "command": ["a"]}
    with pytest.raises(JobError, match="duplicate"):
        parse_config({"job": [j, dict(j)]}, home="/tmp/h")


# ── due calculation ─────────────────────────────────────────────────────────
def test_due_daily_and_catch_up_once():
    j = parse_config({"scheduler": {"tz": "America/New_York"}, "job": [
        {"name": "a", "kind": "skill", "schedule": "daily 02:00", "command": ["x"]}]},
        home="/tmp/h").jobs[0]
    slot_today = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)   # 02:00 EDT
    # laptop asleep for three nights: only today's slot runs, two are counted as missed
    last = slot_today - timedelta(days=3)
    s, missed = due_slot(j, NOW, NY, last, None, 24 * 3600)
    assert s == slot_today and missed == 2
    # once that slot has run, nothing is due until tomorrow
    assert due_slot(j, NOW, NY, slot_today, None, 24 * 3600) is None
    assert due_slot(j, NOW + timedelta(days=1), NY, slot_today, None, 36 * 3600)[0] == \
        slot_today + timedelta(days=1)
    # a new job never fires for a slot before it was first seen
    assert due_slot(j, NOW, NY, None, NOW - timedelta(hours=1), 24 * 3600) is None
    assert due_slot(j, NOW, NY, None, slot_today - timedelta(minutes=1), 24 * 3600)[0] == slot_today
    # a slot older than the catch-up window is skipped, not run late
    assert due_slot(j, NOW, NY, last, None, 3600) is None


def test_due_weekdays_and_every():
    wk = parse_schedule("weekdays 06:30")
    sat = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)          # Saturday
    assert latest_slot(wk, sat, NY) == datetime(2026, 10, 16, 10, 30, tzinfo=UTC)  # Friday 06:30 EDT
    ev = parse_schedule("every 30m")
    assert latest_slot(ev, datetime(2026, 10, 19, 11, 47, tzinfo=UTC), NY) == \
        datetime(2026, 10, 19, 11, 30, tzinfo=UTC)
    got = slots_between(ev, datetime(2026, 10, 19, 9, 0, tzinfo=UTC),
                        datetime(2026, 10, 19, 11, 0, tzinfo=UTC), NY)
    assert len(got) == 4


def test_due_across_dst():
    daily = parse_schedule("daily 02:00")
    # 02:00 local stays 02:00 local: 07:00Z before spring forward, 06:00Z after
    assert latest_slot(daily, datetime(2026, 3, 7, 12, 0, tzinfo=UTC), NY) == \
        datetime(2026, 3, 7, 7, 0, tzinfo=UTC)
    assert latest_slot(daily, datetime(2026, 3, 9, 12, 0, tzinfo=UTC), NY) == \
        datetime(2026, 3, 9, 6, 0, tzinfo=UTC)
    # 02:30 does not exist on 2026-03-08 in New York: it runs once, right after the gap (03:30 EDT)
    gap = parse_schedule("daily 02:30")
    s = latest_slot(gap, datetime(2026, 3, 8, 12, 0, tzinfo=UTC), NY)
    assert s == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
    assert s.astimezone(NY).hour == 3
    # 01:30 happens twice on 2026-11-01: exactly one slot that day (the first, EDT)
    amb = parse_schedule("daily 01:30")
    got = slots_between(amb, datetime(2026, 10, 31, 12, 0, tzinfo=UTC),
                        datetime(2026, 11, 1, 23, 0, tzinfo=UTC), NY)
    assert got == [datetime(2026, 11, 1, 5, 30, tzinfo=UTC)]
    # a missed run across the fall-back night is still caught up once
    j = parse_config({"job": [{"name": "a", "kind": "skill", "schedule": "daily 01:30",
                               "command": ["x"]}]}, home="/tmp/h").jobs[0]
    s, missed = due_slot(j, datetime(2026, 11, 2, 12, 0, tzinfo=UTC), NY,
                         datetime(2026, 10, 31, 5, 30, tzinfo=UTC), None, 24 * 3600)
    assert s == datetime(2026, 11, 2, 6, 30, tzinfo=UTC) and missed == 1


# ── admission ───────────────────────────────────────────────────────────────
def _skill(name="report", **kw):
    j = {"name": name, "kind": "skill", "schedule": "daily 02:00",
         "command": ["python", "-c", "print('{\"outcome\": \"verified\"}')"]}
    j.update(kw)
    return j


@pytest.mark.parametrize("probe_kw, job_kw, reason", [
    ({"source": "battery"}, {"constraints": {"ac_power": True}}, "on_battery"),
    ({"idle": 20.0}, {"constraints": {"idle": 300}}, "not_idle"),
    ({"locked": True}, {}, "screen_locked"),
    ({"leases": [{"pid": 1, "task": "awos-coding-run"}]}, {"uses_model": True}, "model_busy"),
    ({"mem": (False, "needs ~9000 MB but the budget is 6000 MB")}, {"uses_model": True}, "memory"),
    ({}, {"constraints": {"quiet_hours": "06:00-08:00"}}, "quiet_hours"),
])
def test_admission_refusals(env, probe_kw, job_kw, reason):
    sched = make(env, [_skill(**job_kw)], probe=FakeProbe(**probe_kw))
    job = sched.cfg.jobs[0]
    slot = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
    rec = sched.run_job(job, slot=slot)
    assert rec["outcome"] == "DEFERRED" and rec["reason"].startswith(reason)
    assert not (sched.run_dir(rec["run_id"]) / "result.json").exists()
    st = sched.state()["jobs"]["report"]
    assert "last_slot" not in st and st["last_deferred_reason"].startswith(reason)
    # the slot stays due: the next run-due retries it once conditions allow
    sched.update_state(lambda s: s["jobs"]["report"].update(first_seen="2026-10-18T00:00:00Z"))
    assert [j.name for j, _, _ in sched.due()] == ["report"]
    sched.probe = FakeProbe()
    sched.clock = lambda: NOW + timedelta(hours=2)      # also past the quiet-hours window
    recs = sched.run_due()
    assert [r["outcome"] for r in recs] == ["VERIFIED"]
    assert sched.due() == []


def test_single_model_job_lock_and_server_lifecycle(env):
    probe = FakeProbe(up=False)
    sched = make(env, [_skill("m1", uses_model=True), _skill("m2", uses_model=True)], probe=probe)
    j1, j2 = sched.cfg.jobs
    with file_lock(sched.home / "model.lock", blocking=False) as got:   # another model job holds it
        assert got
        rec = sched.run_job(j2, slot=NOW)
        assert rec["outcome"] == "DEFERRED" and rec["reason"].startswith("model_busy")
        assert probe.calls == []                     # never touched the server
    rec = sched.run_job(j1, slot=NOW)
    assert rec["outcome"] == "VERIFIED" and rec["model_started_by_run"]
    assert probe.calls == ["start", f"lease:{rec['run_id']}", "stop"]
    # an already-running server is used and left running
    probe.calls.clear()
    probe.up = True
    rec = sched.run_job(j2, slot=NOW)
    assert rec["outcome"] == "VERIFIED" and probe.calls == [f"lease:{rec['run_id']}"]


def test_run_due_is_exclusive(env):
    sched = make(env, [_skill()])
    with file_lock(sched.home / "run-due.lock", blocking=False):
        assert sched.run_due()[0]["reason"] == "another run-due is active"


# ── checkpoint / resume ─────────────────────────────────────────────────────
def _archive_moves(mb: FakeMailbox):
    out = {}
    for h in mb.list_history("0")["changes"]:
        if h["type"] == "changed" and h.get("folder") == "Archive":
            out[h["id"]] = out.get(h["id"], 0) + 1
    return out


def test_resume_after_kill_no_duplicate_actions(env):
    mb = FakeMailbox(env["mailbox"], read_only=False)
    n_msgs = len(mb.list_messages(limit=0))

    def kill_at_2(n):
        if n == 2:
            raise Kill()
    sched = make(env, [email_job(env, approvals={"writes": "auto_bulk"})], mailbox=mb,
                 hooks=Hooks(after_chunk=kill_at_2))
    job = sched.cfg.jobs[0]
    slot = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
    with pytest.raises(Kill):
        sched.run_job(job, slot=slot)
    rid = run_id_for(job, slot)
    st = sched.state()["jobs"]["inbox"]
    assert st["pending"] == rid
    cp = json.loads((sched.run_dir(rid) / "checkpoint.json").read_text())
    assert len(cp["items"]) == 50 and cp["chunks"] == 2
    before = _archive_moves(mb)
    assert before and all(v == 1 for v in before.values())
    # next launchd wake: run-due resumes the interrupted run first
    sched.hooks = Hooks()
    recs = sched.run_due()
    assert len(recs) == 1
    rec = recs[0]
    assert rec["run_id"] == rid and rec["resumed"] and rec["outcome"] == "VERIFIED"
    assert rec["counts"]["messages"] == n_msgs
    after = _archive_moves(mb)
    assert all(v == 1 for v in after.values()), "an archive was repeated after resume"
    assert set(before) <= set(after) and rec["counts"]["archived"] == len(after)
    ledger = read_jsonl(sched.run_dir(rid) / "actions.jsonl")
    done = [r["id"] for r in ledger if r["phase"] == "done"]
    assert len(done) == len(set(done)) == len(after)
    for mid in after:                                  # undo info for every write
        assert next(r for r in ledger if r["phase"] == "done" and r["id"] == mid)["undo"] == \
            {"op": "move", "id": mid, "to": "INBOX"}
    assert "pending" not in sched.state()["jobs"]["inbox"]
    # idempotent per run id: running the same slot again does nothing
    again = sched.run_job(job, slot=slot)
    assert again["outcome"] == "DUPLICATE" and _archive_moves(mb) == after
    assert sched.run_due() == []


def test_kill_between_write_and_confirmation_is_reconciled(env):
    mb = FakeMailbox(env["mailbox"], read_only=False)
    hit = []

    def kill_after_first_write(mid):
        if not hit:
            hit.append(mid)
            raise Kill()
    sched = make(env, [email_job(env, approvals={"writes": "auto_bulk"})], mailbox=mb,
                 hooks=Hooks(after_action=kill_after_first_write))
    job = sched.cfg.jobs[0]
    with pytest.raises(Kill):
        sched.run_job(job, slot=NOW)
    sched.hooks = Hooks()
    rec = sched.run_due()[0]
    assert rec["outcome"] == "VERIFIED" and rec["resumed"]
    assert _archive_moves(mb)[hit[0]] == 1
    ledger = read_jsonl(sched.run_dir(rec["run_id"]) / "actions.jsonl")
    assert any(r.get("reconciled") and r["id"] == hit[0] for r in ledger)


def test_max_runtime_interrupts_and_resumes(env, monkeypatch):
    sched = make(env, [email_job(env, constraints={"max_runtime": 10})])
    job = sched.cfg.jobs[0]
    import scaffold.agent.desktop.scheduler.runner as R
    t = [1000.0]
    monkeypatch.setattr(R, "_mono", lambda: t[0])

    def jump(n):
        t[0] += 60                      # the chunk took longer than the budget
    sched.hooks = Hooks(after_chunk=jump)
    rec = sched.run_job(job, slot=NOW)
    assert rec["outcome"] == "INTERRUPTED" and "max_runtime" in rec["reason"]
    assert sched.state()["jobs"]["inbox"]["pending"] == rec["run_id"]
    sched.hooks = Hooks()
    rec2 = sched.run_due()[0]
    assert rec2["run_id"] == rec["run_id"] and rec2["outcome"] == "VERIFIED" and rec2["resumed"]


# ── digest ──────────────────────────────────────────────────────────────────
HALT_CMD = ("import json; print('working...'); print(json.dumps({'outcome': 'halted', "
            "'reason': \"unexpected dialog 'Save changes?' at step 3\", "
            "'halt': {'step': 3, 'screen': 'Save changes?'}}))")


@pytest.fixture
def digest_run(env):
    mb = FakeMailbox(env["mailbox"], read_only=False)
    jobs = [email_job(env, approvals={"writes": "auto_bulk"}),
            {"name": "weekly-report", "kind": "skill", "schedule": "daily 03:00",
             "command": ["python", "-c", HALT_CMD]}]
    sched = make(env, jobs, mailbox=mb)
    r1 = sched.run_job(sched.cfg.jobs[0], slot=NOW)
    r2 = sched.run_job(sched.cfg.jobs[1], slot=NOW)
    assert r1["outcome"] == "VERIFIED" and r2["outcome"] == "HALTED"
    out = dg.write_digest(sched, NOW + timedelta(minutes=5))
    return sched, out, mb


def test_digest_content_from_fake_triage_run(digest_run):
    sched, out, mb = digest_run
    c = out["counts"]
    assert c["urgent"] > 0 and c["needs_reply"] > 0 and c["review"] > 0
    assert c["archived"] > 0 and c["flagged"] > 0 and c["approvals"] > 0 and c["halted"] == 1
    text = Path(out["text"]).read_text()
    page = Path(out["html"]).read_text()
    for section in ("== Urgent", "== Needs a reply", "== Check these", "== Archived",
                    "== Flagged", "== Waiting for your approval", "== Skills that stopped",
                    "== Learned rules", "== Runs"):
        assert section in text
    tri = json.loads((sched.run_dir(f"inbox@{NOW:%Y%m%dT%H%MZ}") / "triage.json").read_text())
    items = {i["id"]: i for i in tri["items"]}
    urgent = [i for i in items.values() if i["importance"] == 3 and not i["suspicious"]]
    for u in urgent:
        assert u["subject"] in text and u["why_source"] in ("guard", "rule", "model")
    assert {i["why_source"] for i in items.values()} >= {"guard", "rule", "model"}
    # every archived message carries its undo; it really left the inbox
    archived = [i for i in items.values() if (i["action"] or {}).get("status") == "done"]
    for a in archived:
        assert "INBOX" not in mb.get_message(a["id"])["labels"]
        assert f"undo: move message {a['id']} back to the Inbox" in text
        assert a["category"] in ("newsletter", "promo", "social") and a["layer"] in ("L1", "L2")
    # injections and phishing are flagged, never archived
    flagged = [i for i in items.values() if i["suspicious"] or i["category"] == "spam_phishing"]
    assert any("injection" in i["guards"] for i in flagged)
    assert any(i["why_source"] == "guard" and ("look-alike" in i["why"] or "phishing guard" in i["why"])
               for i in flagged)
    assert not any((i["action"] or {}).get("status") == "done" for i in flagged)
    # the halted skill asks for a one-step fix demo
    assert "weekly-report: HALTED" in text and "Save changes?" in text and "demonstrate" in text
    # learned rules: render_learned is included; first digest lists every rule as added
    prefs = P.load(Path(sched.cfg.jobs[0].prefs))
    assert P.render_learned(prefs).splitlines()[0] in page
    assert text.count("  added: ") == len(prefs["rules"])
    # queued approvals: non-bulk archive proposals wait for the owner
    assert all(a["status"] == "pending" and a["op"] == "archive" for a in sched.pending_approvals())


def test_digest_learned_rule_changes_since_last(digest_run):
    sched, out, _ = digest_run
    p = Path(sched.cfg.jobs[0].prefs)
    prefs = P.load(p)
    P.add_rule(prefs, {"from_domain": "newsletters.example"}, {"importance": 0, "category": "newsletter"},
               source="user", note="I never read these")
    P.save(p, prefs)
    out2 = dg.write_digest(sched, NOW + timedelta(days=1))
    text = Path(out2["text"]).read_text()
    added = [l for l in text.splitlines() if l.startswith("  added: ")]
    assert len(added) == 1 and "newsletters.example" in added[0]
    out3 = dg.write_digest(sched, NOW + timedelta(days=2))
    assert "no changes since the last digest" in Path(out3["text"]).read_text()


def test_digest_is_private_html(digest_run):
    _, out, _ = digest_run
    page = Path(out["html"]).read_text()
    assert "<script" not in page.lower()
    # mail text may mention URLs (escaped text); nothing may LOAD one
    assert not re.search(r"<(img|script|link|iframe|object|embed|audio|video|source|form|base)\b", page, re.I)
    assert not re.search(r"\b(src|href|action|srcset)\s*=", page, re.I)
    assert "@import" not in page and "url(" not in page
    assert "default-src 'none'" in page
    assert oct(Path(out["html"]).stat().st_mode & 0o777) == "0o600"


def test_digest_escapes_mail_html(env):
    mb = FakeMailbox(env["mailbox"], read_only=True)
    mb.deliver({"id": "evil1", "thread_id": "t-evil", "date": "2026-10-19T01:00:00+00:00",
                "from": "Eve <eve@evil.example>", "from_name": "Eve", "from_addr": "eve@evil.example",
                "to": "x", "subject": "<script>alert(1)</script><img src=http://track.example/p.gif>",
                "body": "hi", "headers": {}})
    sched = make(env, [email_job(env)], mailbox=mb)
    sched.run_job(sched.cfg.jobs[0], slot=NOW)
    page = Path(dg.write_digest(sched, NOW)["html"]).read_text()
    assert "<script>" not in page and "<img" not in page and "&lt;script&gt;" in page


def test_notification_only_behind_flag(digest_run):
    sched, _, _ = digest_run
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    dg.write_digest(sched, NOW, run=fake_run)
    assert calls == []
    out = dg.write_digest(sched, NOW, run=fake_run, do_notify=True)
    assert out["notified"] and calls[0][0] == "osascript"
    msg = calls[0][2]
    assert "display notification" in msg and "urgent" in msg and "@" not in msg   # counts only


def test_digest_due_once_per_day(env):
    cfg = parse_config({"scheduler": {"tz": "America/New_York", "catchup_hours": 12},
                        "digest": {"at": "07:30"}, "job": []}, home=str(env["home"]))
    sched = Scheduler(cfg, probe=FakeProbe(), clock=lambda: NOW)
    assert dg.digest_due(sched, NOW) is None                       # 07:00 local: yesterday's slot is stale
    t = datetime(2026, 10, 19, 11, 45, tzinfo=UTC)                 # 07:45 local
    s = dg.digest_due(sched, t)
    assert s == datetime(2026, 10, 19, 11, 30, tzinfo=UTC)
    dg.write_digest(sched, t, slot=s)
    assert dg.digest_due(sched, t + timedelta(hours=3)) is None


# ── secrets / network ───────────────────────────────────────────────────────
def test_no_secret_leakage(env, monkeypatch):
    monkeypatch.setenv("AWOS_TEST_API_TOKEN", "tok-" + "z9" * 12)
    mb = FakeMailbox(env["mailbox"], read_only=True)
    mb.deliver({"id": "leak1", "thread_id": "t-leak", "date": "2026-10-19T01:00:00+00:00",
                "from": "Ops <ops@vendor.example>", "from_name": "Ops", "from_addr": "ops@vendor.example",
                "to": "x", "subject": f"URGENT: rotate key {SECRET} today",
                "body": f"your key is {SECRET}", "headers": {}})
    leak_cmd = f"import os, json; print('{SECRET}', os.environ['AWOS_TEST_API_TOKEN']); " \
               "print(json.dumps({'outcome': 'needs_user', 'reason': 'key " + SECRET + " expired'}))"
    from scaffold.agent.desktop.secrets import SecretBroker
    jobs = [email_job(env), {"name": "keys", "kind": "micro_task", "schedule": "daily 03:00",
                             "uses_model": False, "constraints": {"unlocked": False},
                             "command": ["python", "-c", leak_cmd]}]
    sched = make(env, jobs, mailbox=mb)
    sched.broker = SecretBroker(env={"AWOS_TEST_API_TOKEN": "tok-" + "z9" * 12})
    for j in sched.cfg.jobs:
        sched.run_job(j, slot=NOW)
    out = dg.write_digest(sched, NOW)
    assert "rotate key" in Path(out["text"]).read_text()
    for p in sched.home.rglob("*"):
        if p.is_file():
            body = p.read_text(errors="ignore")
            assert SECRET not in body, p
            assert "tok-" + "z9" * 12 not in body, p


def test_no_network_beyond_loopback(env, monkeypatch):
    seen = []
    real = socket.socket.connect

    def guard(self, addr):
        seen.append(addr)
        host = addr[0] if isinstance(addr, tuple) else addr
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"network access to {addr}")
        return real(self, addr)
    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"network {a}")))
    sched = make(env, [email_job(env, approvals={"writes": "auto_bulk"})])
    rec = sched.run_job(sched.cfg.jobs[0], slot=NOW)
    assert rec["outcome"] == "VERIFIED"
    dg.write_digest(sched, NOW)
    assert seen == []


# ── launchd ─────────────────────────────────────────────────────────────────
def test_plist_valid_and_not_installed(env, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or
                        subprocess.CompletedProcess(a, 0, "", ""))
    cfg = parse_config({"scheduler": {"tz": "America/New_York"}, "digest": {"at": "07:30"},
                        "job": [email_job(env), _skill("wk", schedule="weekdays 06:15"),
                                _skill("poll", schedule="every 45m")]},
                       home=str(env["home"]), path=tmp_path / "jobs.toml")
    raw = launchd.plist_bytes(cfg, python="/usr/bin/python3")
    d = plistlib.loads(raw)
    assert d["Label"] == "com.awos.scheduler"
    assert d["RunAtLoad"] is False and d["ProcessType"] == "Background"
    assert d["LowPriorityIO"] is True and d["Nice"] > 0
    assert d["ProgramArguments"][:3] == ["/usr/bin/python3", "-m", "scaffold.agent.desktop.scheduler"]
    assert d["ProgramArguments"][-1] == "run-due" and "--jobs" in d["ProgramArguments"]
    cal = d["StartCalendarInterval"]
    assert {"Hour": 2, "Minute": 0} in cal and {"Hour": 7, "Minute": 30} in cal
    assert {"Weekday": 1, "Hour": 6, "Minute": 15} in cal and {"Weekday": 5, "Hour": 6, "Minute": 15} in cal
    assert not any(e.get("Weekday") in (0, 6) and e["Minute"] == 15 for e in cal)
    assert d["StartInterval"] == 45 * 60
    f = tmp_path / "a.plist"
    f.write_bytes(raw)
    assert calls == []                                   # building a plist runs nothing
    text = launchd.render_instructions(str(f))
    assert "launchctl bootstrap gui/$(id -u)" in text and "launchctl bootout" in text
    assert "Nothing was installed" in text


def test_plist_lints_with_plutil(env, tmp_path):
    if not shutil.which("plutil"):
        pytest.skip("plutil not available")
    cfg = parse_config({"job": [email_job(env)]}, home=str(env["home"]))
    f = tmp_path / "a.plist"
    f.write_bytes(launchd.plist_bytes(cfg))
    r = subprocess.run(["plutil", "-lint", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ── CLI ─────────────────────────────────────────────────────────────────────
def test_cli_end_to_end(env, tmp_path, capsys):
    f = tmp_path / "jobs.toml"
    f.write_text(f"""
[scheduler]
tz = "America/New_York"
[digest]
at = "07:30"
[[job]]
name = "inbox"
kind = "email_triage"
schedule = "daily 02:00"
mailbox = "fake:{env['mailbox']}"
prefs = "{env['prefs']}"
""")
    base = ["--jobs", str(f), "--home", str(env["home"])]
    assert cli(base + ["list"]) == 0
    assert "inbox" in capsys.readouterr().out
    assert cli(base + ["run", "inbox", "--force"]) == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec["outcome"] == "VERIFIED" and rec["counts"]["messages"] > 0
    assert cli(base + ["digest"]) == 0
    o = capsys.readouterr().out
    assert "AWOS morning digest" in o and "HTML:" in o
    assert cli(base + ["plist", "--out", str(tmp_path / "p.plist")]) == 0
    assert "launchctl bootstrap" in capsys.readouterr().out
    plistlib.loads((tmp_path / "p.plist").read_bytes())
    assert cli(base + ["status"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["jobs"][0]["name"] == "inbox" and st["pending_approvals"] > 0
    assert cli(base + ["run", "nope"]) == 2

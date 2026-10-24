"""WP9 digest actions (scaffold/agent/desktop/scheduler/actions.py).

approve / deny / undo / correct / review / apply on a FakeMailbox with the
scripted keyword_prefs agent. No model calls, no real mailbox, no launchctl, no
network beyond 127.0.0.1.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scaffold.agent.desktop.mail_adapter import FakeMailbox
from scaffold.agent.desktop.scheduler import actions as A
from scaffold.agent.desktop.scheduler import digest as dg
from scaffold.agent.desktop.scheduler.__main__ import main as cli
from scaffold.agent.desktop.scheduler.jobs import parse_config
from scaffold.agent.desktop.scheduler.runner import Scheduler, read_jsonl
from scaffold.agent.desktop.secrets import SecretBroker
from scaffold.agent.local_runtime import PowerState
from testbeds.email_triage import prefs as P

UTC = timezone.utc
NOW = datetime(2026, 10, 19, 11, 0, tzinfo=UTC)
SECRET = "sk-ant-api03-" + "Q" * 40


class Kill(BaseException):
    """Hard stop between the mail write and the ledger."""


class FakeProbe:
    def power(self):
        return PowerState("ac", 80)

    def idle_seconds(self):
        return 3600.0

    def screen_locked(self):
        return False

    def leases(self):
        return []

    def memory(self, power):
        return True, "fits"

    def server_up(self):
        return False

    def model_script(self, action):
        return True

    @contextlib.contextmanager
    def lease(self, run_id):
        yield None


class CountingBox(FakeMailbox):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.moves = []

    def move(self, id, folder):
        self.moves.append((id, folder))
        return super().move(id, folder)


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
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
    return {"mailbox": mb, "prefs": prefs, "home": tmp_path / "home", "tmp": tmp_path}


def job_dict(env, **kw):
    j = {"name": "inbox", "kind": "email_triage", "schedule": "daily 02:00",
         "mailbox": f"fake:{env['mailbox']}", "agent": "keyword_prefs", "prefs": str(env["prefs"]),
         "chunk": 40, "limit": 0}
    j.update(kw)
    return j


def setup(env, writes="queue", box_rw=False, extra_jobs=()):
    cfg = parse_config({"scheduler": {"tz": "America/New_York"},
                        "job": [job_dict(env, approvals={"writes": writes}), *extra_jobs]},
                       home=str(env["home"]))
    box = CountingBox(env["mailbox"], read_only=writes != "auto_bulk")
    sched = Scheduler(cfg, probe=FakeProbe(), adapter_factory=lambda job: box, clock=lambda: NOW)
    rec = sched.run_job(cfg.jobs[0], slot=NOW)
    assert rec["outcome"] in ("VERIFIED", "COMPLETED_UNVERIFIED")
    box.read_only = not box_rw
    box.moves.clear()
    return sched, box, rec["run_id"]


def acts_for(sched, box, **kw):
    return A.Actions(sched, adapter_factory=lambda job: box, **kw)


def triage(sched, run_id):
    return {i["id"]: i for i in json.loads((sched.run_dir(run_id) / "triage.json").read_text())["items"]}


def prefs_doc(env):
    return json.loads(Path(env["prefs"]).read_text())


def rules_by_id(env):
    return {r["id"]: r for r in prefs_doc(env)["rules"]}


# ── approve ─────────────────────────────────────────────────────────────────
def test_approve_refused_when_adapter_read_only(env):
    sched, box, _ = setup(env, box_rw=False)
    pend = sched.pending_approvals()
    assert pend, "a queue job must queue archive suggestions"
    acts = acts_for(sched, box)
    res = acts.approve(pend[0]["id"])
    assert res["status"] == "refused" and "read-only" in res["message"]
    assert box.moves == [] and "INBOX" in box.get_message(pend[0]["target"])["labels"]
    assert "done" not in acts.phases(f"approve:{pend[0]['id']}")
    assert pend[0]["id"] in {a["id"] for a in sched.pending_approvals()}     # still pending
    audit = read_jsonl(acts.audit_path)
    assert audit[-1]["status"] == "refused"


def test_approve_refused_by_deny_policy(env):
    sched, box, run_id = setup(env, box_rw=True)
    deny_job = job_dict(env, name="locked", approvals={"writes": "deny"})
    sched.cfg.jobs.append(parse_config({"job": [deny_job]}, home=str(env["home"])).jobs[0])
    mid = sched.pending_approvals()[0]["target"]
    sched.queue_approval({"id": "locked@x:archive:" + mid, "run_id": "locked@x", "job": "locked",
                          "op": "archive", "target": mid, "summary": "archive it"})
    res = acts_for(sched, box).approve("locked@x:archive:" + mid)
    assert res["status"] == "refused" and "deny" in res["message"] and box.moves == []


def test_approve_writable_archives_idempotent_and_records_evidence(env):
    sched, box, run_id = setup(env, box_rw=True)
    ap = sched.pending_approvals()[0]
    acts = acts_for(sched, box)
    res = acts.approve(ap["id"])
    assert res["status"] == "done", res
    assert "INBOX" not in box.get_message(ap["target"])["labels"]
    assert ap["id"] not in {a["id"] for a in sched.pending_approvals()}
    again = acts.approve(ap["id"])
    assert again["status"] == "already_done" and len(box.moves) == 1
    # implicit evidence, candidate (promotion gate), scoped to address/service
    rid = res["rules"][0]["id"]
    r = rules_by_id(env)[rid]
    assert r["source"] == "implicit" and r["status"] == "candidate"
    assert r["scope"] in ("address", "service") and "from_domain" not in r["when"]
    assert r["evidence"][0]["model_agreed"] is True and r["set"]["importance"] == 0


def test_approve_all(env):
    sched, box, _ = setup(env, box_rw=True)
    n = len(sched.pending_approvals())
    res = acts_for(sched, box).approve_all()
    assert len(res) == n and all(r["status"] in A.OK_STATUSES for r in res)
    assert sched.pending_approvals() == []


def test_approve_crash_between_write_and_ledger_reconciles(env):
    sched, box, _ = setup(env, box_rw=True)
    ap = sched.pending_approvals()[0]

    def die(mid):
        raise Kill()
    with pytest.raises(Kill):
        acts_for(sched, box, after_write=die).approve(ap["id"])
    acts = acts_for(sched, box)
    ph = acts.phases(f"approve:{ap['id']}")
    assert "intent" in ph and "done" not in ph
    res = acts.approve(ap["id"])
    assert res["status"] == "reconciled"
    assert len(box.moves) == 1              # never written twice
    assert "done" in acts.phases(f"approve:{ap['id']}")


# ── deny ────────────────────────────────────────────────────────────────────
def test_deny_records_exclude_candidate_and_is_idempotent(env):
    sched, box, _ = setup(env, box_rw=True)
    ap = sched.pending_approvals()[0]
    acts = acts_for(sched, box)
    res = acts.deny(ap["id"])
    assert res["status"] == "done" and box.moves == []
    assert ap["id"] not in {a["id"] for a in sched.pending_approvals()}
    r = rules_by_id(env)[res["rules"][0]["id"]]
    assert r["source"] == "implicit" and r["effect"] == "exclude" and r["status"] == "candidate"
    assert "model_agreed" not in r["evidence"][0]
    assert acts.deny(ap["id"])["status"] == "already_done"
    assert acts.approve(ap["id"])["status"] == "refused"
    assert box.moves == []


# ── undo ────────────────────────────────────────────────────────────────────
def test_undo_restores_learns_and_changes_next_run(env):
    sched, box, run_id = setup(env, writes="auto_bulk", box_rw=True)
    acts = acts_for(sched, box)
    arcs = [a for a in acts.archives() if a["run_id"] == run_id]
    assert arcs
    mid = arcs[0]["id"]
    assert "INBOX" not in box.get_message(mid)["labels"]
    res = acts.undo(f"{run_id}:{mid}")
    assert res["status"] == "done" and "INBOX" in box.get_message(mid)["labels"]
    assert acts.undo(f"{run_id}:{mid}")["status"] == "already_done"
    assert box.moves == [(mid, "INBOX")]
    r = rules_by_id(env)[res["rules"][0]["id"]]
    assert r["source"] == "user" and r["status"] == "active" and r["set"] == {"min_importance": 1}
    assert r["scope"] in ("address", "service") and "from_domain" not in r["when"]
    # the next triage run keeps it in the inbox
    rec = sched.run_job(sched.cfg.jobs[0], slot=NOW + timedelta(days=1), now=NOW + timedelta(days=1))
    item = triage(sched, rec["run_id"])[mid]
    assert item["importance"] >= 1 and (item["action"] or {}).get("status") != "done"
    assert "INBOX" in box.get_message(mid)["labels"]


def test_undo_already_restored_reconciles_without_write(env):
    sched, box, run_id = setup(env, writes="auto_bulk", box_rw=True)
    acts = acts_for(sched, box)
    mid = [a for a in acts.archives() if a["run_id"] == run_id][0]["id"]
    box.move(mid, "INBOX")                  # the owner already moved it back by hand
    box.moves.clear()
    res = acts.undo(mid)                    # bare message id works too
    assert res["status"] == "reconciled" and box.moves == []
    assert "done" in acts.phases(f"undo:{run_id}:{mid}")


def test_undo_read_only_refused_and_crash_reconciles(env):
    sched, box, run_id = setup(env, writes="auto_bulk", box_rw=False)
    acts = acts_for(sched, box)
    mid = [a for a in acts.archives() if a["run_id"] == run_id][0]["id"]
    res = acts.undo(f"{run_id}:{mid}")
    assert res["status"] == "refused" and "read-only" in res["message"] and box.moves == []
    box.read_only = False

    def die(m):
        raise Kill()
    with pytest.raises(Kill):
        acts_for(sched, box, after_write=die).undo(f"{run_id}:{mid}")
    res = acts_for(sched, box).undo(f"{run_id}:{mid}")
    assert res["status"] == "reconciled" and len(box.moves) == 1


def test_undo_run_restores_everything(env):
    sched, box, run_id = setup(env, writes="auto_bulk", box_rw=True)
    acts = acts_for(sched, box)
    arcs = [a for a in acts.archives() if a["run_id"] == run_id]
    res = acts.undo_run(run_id)
    assert len(res) == len(arcs) and all(r["status"] == "done" for r in res)
    assert all("INBOX" in box.get_message(a["id"])["labels"] for a in arcs)
    assert all(r["status"] == "already_done" for r in acts.undo_run(run_id))
    assert acts.undo_run("nope@x")[0]["status"] == "not_found"


# ── correct ─────────────────────────────────────────────────────────────────
def _plain_item(items):
    for i in items.values():
        if (i["layer"] in ("L2", "L3") and not i["suspicious"] and i["importance"] <= 1
                and i["category"] != "spam_phishing" and not i["guards"]):
            return i
    raise AssertionError("no plain item")


def test_correct_creates_user_rule_that_changes_next_run(env):
    sched, box, run_id = setup(env)
    item = _plain_item(triage(sched, run_id))
    acts = acts_for(sched, box)
    res = acts.correct(item["id"], 3, "urgent_action", True, note="this sender is my accountant")
    assert res["status"] == "done", res
    r = rules_by_id(env)[res["rules"][0]["id"]]
    assert r["source"] == "user" and r["status"] == "active"
    assert r["scope"] in ("address", "service") and "from_domain" not in r["when"]
    assert r["set"] == {"importance": 3, "category": "urgent_action", "needs_reply": True}
    assert acts.correct(item["id"], 3, "urgent_action", True,
                        note="this sender is my accountant")["status"] == "already_done"
    rec = sched.run_job(sched.cfg.jobs[0], slot=NOW + timedelta(days=1), now=NOW + timedelta(days=1))
    after = triage(sched, rec["run_id"])[item["id"]]
    assert (after["importance"], after["category"], after["needs_reply"]) == (3, "urgent_action", True)
    assert after["layer"] == "L1" and r["id"] in after["why"]


def test_user_correction_outranks_learned_rule(env):
    sched, box, run_id = setup(env, box_rw=True)
    acts = acts_for(sched, box)
    ap = sched.pending_approvals()[0]
    mid = ap["target"]
    msg = box.get_message(mid)
    # a learned (implicit) rule saying junk, already active
    doc = P.load(Path(env["prefs"]), upgrade_to_v2=True)
    from scaffold.agent.desktop.correction_capture import scope_for
    scope, when = scope_for(msg)
    lr = P.add_rule(doc, when, {"importance": 0, "category": "newsletter"}, source="implicit",
                    scope=scope)
    lr["status"] = "active"
    Path(env["prefs"]).write_text(json.dumps(doc))
    acts.correct(mid, 2, "important_read", False)
    rec = sched.run_job(sched.cfg.jobs[0], slot=NOW + timedelta(days=1), now=NOW + timedelta(days=1))
    after = triage(sched, rec["run_id"])[mid]
    assert (after["importance"], after["category"]) == (2, "important_read")


def test_correct_validates(env):
    sched, box, run_id = setup(env)
    acts = acts_for(sched, box)
    with pytest.raises(A.ActionError):
        acts.correct("x", 9)
    with pytest.raises(A.ActionError):
        acts.correct("x", 1, "bogus")
    assert acts.correct("does-not-exist", 1)["status"] == "not_found"


# ── review, digest, apply ───────────────────────────────────────────────────
def test_review_lists_queue_with_suggestions(env):
    sched, box, run_id = setup(env)
    rows = acts_for(sched, box).review()
    items = triage(sched, run_id)
    assert rows and {r["id"] for r in rows} == {i for i, x in items.items() if x["review"]}
    for r in rows:
        assert set(r["suggested"]) == {"importance", "category", "needs_reply"}
        assert f"correct {r['id']} --importance" in r["command"]


def test_digest_has_commands_ids_todo_and_learned_section(env):
    sched, box, run_id = setup(env, box_rw=True)
    acts = acts_for(sched, box)
    out = dg.write_digest(sched, NOW + timedelta(minutes=5))
    page = Path(out["html"]).read_text()
    text = Path(out["text"]).read_text()
    pend = sched.pending_approvals()
    assert pend
    for a in pend[:3]:
        assert f"approve {a['id']}" in page and f"deny {a['id']}" in page
        assert f'id="approval-{a["id"]}"' in page
        assert f"approve {a['id']}" in text
    rev = [i for i in triage(sched, run_id).values() if i["review"]]
    assert f"correct {rev[0]['id']} --importance" in page
    assert "<script" not in page.lower() and "<form" not in page.lower()
    assert " href=" not in page and " src=" not in page
    todo = Path(out["todo"]).read_text()
    assert f"# approve {pend[0]['id']}" in todo
    # the owner uncomments two lines and applies the file
    target = pend[0]
    edited = todo.replace(f"# approve {target['id']}", f"approve {target['id']}", 1)
    edited = edited.replace(f"# correct {rev[0]['id']}", f"correct {rev[0]['id']}", 1)
    f = env["tmp"] / "mine.todo"
    f.write_text(edited)
    sched.clock = lambda: NOW + timedelta(minutes=30)     # the owner acts after reading it
    res = acts.apply(f)
    assert [r["op"] for r in res] == ["approve", "correct"] and all(r["status"] == "done" for r in res)
    assert [r["status"] for r in acts.apply(f)] == ["already_done", "already_done"]
    # the next digest: approved archive listed with its undo, corrections shown as learned
    out2 = dg.write_digest(sched, NOW + timedelta(hours=1))
    page2 = Path(out2["html"]).read_text()
    text2 = Path(out2["text"]).read_text()
    assert "Learned from your corrections" in page2 and "== Learned from your corrections" in text2
    assert "(from correct)" in text2 and "(from approve)" in text2
    assert f"undo {run_id}:{target['target']}" in page2
    assert rev[0]["id"] not in [m["id"] for m in dg.collect(sched, NOW + timedelta(hours=1))["data"]["review"]]


def test_apply_reports_bad_lines(env):
    sched, box, _ = setup(env)
    f = env["tmp"] / "bad.todo"
    f.write_text("# comment\nfrobnicate x\ncorrect\n\n")
    res = acts_for(sched, box).apply(f)
    assert len(res) == 2 and all(r["status"] == "failed" for r in res)


# ── secrets, network, CLI ───────────────────────────────────────────────────
def test_no_secret_leakage(env):
    sched, box, run_id = setup(env, box_rw=True)
    sched.broker = SecretBroker(env={"AWOS_TEST_API_TOKEN": "tok-" + "z9" * 12})
    acts = acts_for(sched, box)
    item = _plain_item(triage(sched, run_id))
    acts.correct(item["id"], 2, note=f"my key is {SECRET} and tok-{'z9' * 12}")
    acts.approve(sched.pending_approvals()[0]["id"])
    dg.write_digest(sched, NOW + timedelta(minutes=5))
    for p in [Path(env["prefs"])] + [x for x in Path(env["home"]).rglob("*") if x.is_file()]:
        body = p.read_text(errors="ignore")
        assert SECRET not in body, p
        assert "tok-" + "z9" * 12 not in body, p


def test_no_network(env, monkeypatch):
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
    sched, box, run_id = setup(env, writes="auto_bulk", box_rw=True)
    acts = acts_for(sched, box)
    acts.undo_run(run_id)
    acts.correct(_plain_item(triage(sched, run_id))["id"], 2)
    acts.review()
    dg.write_digest(sched, NOW)
    assert seen == []


def test_cli_end_to_end(env, capsys):
    jobs = env["tmp"] / "jobs.toml"
    jobs.write_text(f"""
[scheduler]
tz = "America/New_York"
home = "{env['home']}"

[[job]]
name = "inbox"
kind = "email_triage"
schedule = "daily 02:00"
mailbox = "fake:{env['mailbox']}"
prefs = "{env['prefs']}"
limit = 0
""")
    base = ["--jobs", str(jobs), "--home", str(env["home"])]
    assert cli(base + ["run", "inbox", "--force"]) == 0
    capsys.readouterr()
    assert cli(base + ["review"]) == 0
    out = capsys.readouterr().out
    assert "correct" in out and "--importance" in out
    pend = Scheduler(parse_config({"job": []}, home=str(env["home"]))).pending_approvals()
    # the CLI opens a fresh writable fake mailbox: the archive works in-process
    assert cli(base + ["approve", pend[0]["id"]]) == 0
    out = capsys.readouterr().out
    assert out.startswith("done") and "in memory" in out
    assert cli(base + ["approve", pend[0]["id"]]) == 0
    assert capsys.readouterr().out.startswith("already_done")
    assert cli(base + ["deny", pend[1]["id"]]) == 0
    assert cli(base + ["correct", pend[2]["target"], "--importance", "2", "--needs-reply", "n"]) == 0
    capsys.readouterr()
    assert cli(base + ["approve", "no-such-id"]) == 1
    assert "not_found" in capsys.readouterr().out
    assert cli(base + ["undo"]) == 2
    assert cli(base + ["digest"]) == 0
    todo = Path(env["home"]) / "digest" / "actions.todo"
    assert todo.exists() and "apply" in todo.read_text()
    assert cli(base + ["apply", str(todo)]) == 0       # all commented: nothing to do

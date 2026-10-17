"""D16 two-phase external sends: prepare -> approve (edit) -> commit once
(scaffold/agent/desktop/sends.py)."""
import pytest

from scaffold.agent.desktop.policy import (
    ActionClass as C, AuditLog, Grant, Provenance as P, SinkPolicy,
)
from scaffold.agent.desktop.sends import (
    SendApproval, SendBroker, SendRefused, idempotency_key,
)

TARGET = "mailto:bob@x.test"
MSG = {"subject": "Hi", "body": "Draft"}


@pytest.fixture
def audit(tmp_path):
    return AuditLog(tmp_path / "audit.jsonl")


def broker(tmp_path, approver=lambda p: True, audit=None, grants=(), ledger=None):
    return SendBroker(SinkPolicy(tmp_path, grants), approver, audit, ledger)


def test_happy_path_commits_exactly_once(tmp_path, audit):
    b = broker(tmp_path, audit=audit)
    sent = []
    p = b.prepare("email", TARGET, MSG)
    assert p.status == "prepared"
    b.approve(p.id)
    assert b.commit(p.id, lambda payload: sent.append(payload) or "ok") == "ok"
    assert sent == [MSG] and p.status == "committed"
    with pytest.raises(SendRefused, match="already committed"):
        b.commit(p.id, lambda payload: sent.append(payload))
    assert len(sent) == 1
    phases = [(r["phase"], r["outcome"]) for r in audit.read() if r.get("kind") == "send"]
    assert phases == [("prepare", "prepared"), ("approve", "approved"),
                      ("commit", "committed"), ("commit", "refused")]


def test_commit_without_approval_is_refused(tmp_path):
    b = broker(tmp_path)
    p = b.prepare("email", TARGET, MSG)
    sent = []
    with pytest.raises(SendRefused, match="approval"):
        b.commit(p.id, sent.append)
    assert sent == []


def test_rejected_send_never_commits(tmp_path):
    b = broker(tmp_path, approver=lambda p: False)
    p = b.prepare("email", TARGET, MSG)
    assert b.approve(p.id).status == "rejected"
    with pytest.raises(SendRefused):
        b.commit(p.id, lambda x: None)


@pytest.mark.parametrize("approver", [None, lambda p: 1 / 0, lambda p: "yes", lambda p: {"ok": 1}])
def test_approval_fails_closed(tmp_path, approver):
    b = broker(tmp_path, approver=approver)
    p = b.prepare("email", TARGET, MSG)
    assert b.approve(p.id).status == "rejected"


def test_owner_edits_the_payload_and_the_edit_is_what_is_sent(tmp_path):
    edited = {"subject": "Hi", "body": "Final text"}
    b = broker(tmp_path, approver=lambda p: SendApproval(True, payload=edited))
    p = b.prepare("email", TARGET, MSG)
    b.approve(p.id)
    assert p.edited
    sent = []
    b.commit(p.id, sent.append)
    assert sent == [edited]


def test_agent_cannot_change_payload_after_approval(tmp_path):
    payload = {"body": "approved"}
    b = broker(tmp_path)
    p = b.prepare("email", TARGET, payload)
    b.approve(p.id)
    payload["body"] = "swapped by the agent"
    sent = []
    b.commit(p.id, sent.append)
    assert sent == [{"body": "approved"}]


def test_approver_sees_a_copy(tmp_path):
    def sneaky(p):
        p.payload["body"] = "tampered"
        p.target = "mailto:eve@x.test"
        return True
    b = broker(tmp_path, approver=sneaky)
    p = b.prepare("email", TARGET, MSG)
    b.approve(p.id)
    assert p.payload == MSG and p.target == TARGET


def test_duplicate_prepare_after_commit_is_refused(tmp_path):
    b = broker(tmp_path)
    p = b.prepare("email", TARGET, MSG)
    b.approve(p.id)
    b.commit(p.id, lambda x: None)
    with pytest.raises(SendRefused, match="duplicate"):
        b.prepare("email", TARGET, dict(MSG))
    q = b.prepare("email", TARGET, MSG, key="second-on-purpose")   # an explicit new key
    assert q.status == "prepared"


def test_two_prepared_copies_only_one_commits(tmp_path):
    b = broker(tmp_path)
    p1 = b.prepare("email", TARGET, MSG)
    p2 = b.prepare("email", TARGET, MSG)      # same key: not yet burned
    b.approve(p1.id)
    b.approve(p2.id)
    sent = []
    b.commit(p1.id, sent.append)
    with pytest.raises(SendRefused):
        b.commit(p2.id, sent.append)
    assert len(sent) == 1


def test_executor_failure_burns_the_key(tmp_path):
    b = broker(tmp_path)
    p = b.prepare("email", TARGET, MSG)
    b.approve(p.id)

    def boom(payload):
        raise ConnectionError("half sent?")
    with pytest.raises(ConnectionError):
        b.commit(p.id, boom)
    assert p.status == "failed"
    with pytest.raises(SendRefused):
        b.commit(p.id, lambda x: None)


def test_untrusted_initiator_is_refused_at_prepare(tmp_path):
    b = broker(tmp_path, grants=[Grant(C.SEND, "*")])
    with pytest.raises(SendRefused, match="R3"):
        b.prepare("email", TARGET, MSG, initiator=P.UNTRUSTED)


def test_untrusted_args_still_get_owner_review(tmp_path):
    seen = []
    b = broker(tmp_path, approver=lambda p: seen.append(p) or True)
    p = b.prepare("email", TARGET, MSG, arg_sources=[P.UNTRUSTED])
    b.approve(p.id)
    b.commit(p.id, lambda x: None)
    assert seen and p.status == "committed"


def test_standing_grant_does_not_skip_approval(tmp_path):
    b = broker(tmp_path, approver=None, grants=[Grant(C.SEND, "*")])
    p = b.prepare("email", TARGET, MSG)
    with pytest.raises(SendRefused):
        b.commit(p.id, lambda x: None)


def test_ledger_survives_restart(tmp_path):
    ledger = tmp_path / "sends.jsonl"
    b = broker(tmp_path, ledger=ledger)
    p = b.prepare("email", TARGET, MSG)
    b.approve(p.id)
    b.commit(p.id, lambda x: None)
    b2 = broker(tmp_path, ledger=ledger)
    with pytest.raises(SendRefused, match="duplicate"):
        b2.prepare("email", TARGET, MSG)


def test_key_is_deterministic_and_payload_sensitive():
    assert idempotency_key("email", TARGET, MSG) == idempotency_key("email", TARGET, dict(MSG))
    assert idempotency_key("email", TARGET, MSG) != idempotency_key("email", TARGET, {"body": "x"})


def test_unknown_send_id(tmp_path):
    with pytest.raises(SendRefused, match="unknown"):
        broker(tmp_path).approve("nope")

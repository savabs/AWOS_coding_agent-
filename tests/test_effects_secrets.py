"""WP7: effect verifier + secrets broker.

No model calls, no network beyond 127.0.0.1, fake keys only, and the real
macOS Keychain is never touched (KeyringProvider gets an in-memory backend;
clipboard functions are injected fakes).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

from scaffold.agent import secret_scan
from scaffold.agent.desktop import secrets as S
from scaffold.agent.desktop.effects import (
    BeforeEffect, CheckLevel, EffectVerdict, FieldEquals, FieldWriteRefused, FileArrival,
    FileRestore, OperationLedger, OpState, Outcome, UIReadback, Verdict, compare_value, digest,
    outcome_for, payload_hash, reconcile_or_escalate, render_field, run_once, worst, write_field,
)
from scaffold.agent.desktop.effects.outcome import confirmed, refuted
from scaffold.agent.desktop.enforce import (
    Enforcer, Mode, PolicyBlocked, credential_action,
)
from scaffold.agent.desktop.policy import (
    Action, ActionClass, AuditLog, Grant, Provenance, SinkPolicy, Verdict as PV,
)

FAKE = "fk_live_" + "Ab3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dF"        # 32 chars after prefix
FAKE2 = "fk_live_" + "Zz8yX7wV6uT5sR4qP3oN2mL1kJ0iH9gF"
KEYRE = re.compile(r"fk_live_[A-Za-z0-9]{32}")


def broker(**values) -> S.SecretBroker:
    return S.SecretBroker(S.MemoryProvider(values), env={})


# ---------------------------------------------------------------------------
# handles: the model side never sees the value
# ---------------------------------------------------------------------------

def test_handle_round_trip_never_exposes_value():
    b = broker()
    h = b.store("fakeservice_key", FAKE)
    assert h == "{{secret:fakeservice_key}}"
    model_side = [h, repr(b), repr(b.provider), b.redact(f"saved {FAKE} ok"),
                  json.dumps(b.redact_obj({"k": FAKE, "l": [FAKE]}))]
    for s in model_side:
        assert FAKE not in s
    assert b.redact(f"saved {FAKE} ok") == "saved {{secret:fakeservice_key}} ok"
    assert b.substitute(f"KEY={h}", target="file:x") == f"KEY={FAKE}"   # sink side only
    with pytest.raises(TypeError):
        import pickle
        pickle.dumps(b)


def test_substitute_fails_fast_and_honours_bindings():
    b = broker(pw="hunter2-but-longer")
    with pytest.raises(S.MissingSecret):
        b.substitute("{{secret:nope}}")
    b.bind("pw", "http:127.0.0.1:*")
    assert b.substitute("{{secret:pw}}", target="http:127.0.0.1:8080/login") == "hunter2-but-longer"
    with pytest.raises(S.SecretNotAllowedHere):
        b.substitute("{{secret:pw}}", target="http:evil.example/login")
    assert b.uses[-1] == {"handle": "{{secret:pw}}", "target": "http:127.0.0.1:8080/login"}


def test_redaction_catches_encodings_and_unknown_formats():
    b = broker()
    b.store("k", FAKE)
    import base64
    import urllib.parse
    txt = f"a={urllib.parse.quote(FAKE)} b={base64.b64encode(FAKE.encode()).decode()} c={FAKE2}"
    red = b.redact(txt)
    assert FAKE not in red and FAKE2 not in red
    assert base64.b64encode(FAKE.encode()).decode().rstrip("=") not in red
    assert "[REDACTED:fakeservice_key]" in red                 # unknown fk_live_ key, via secret_scan


def test_keyring_provider_uses_injected_backend_not_real_keychain():
    class MemKeyring:                                          # keyring backend interface
        def __init__(self):
            self.d = {}

        def get_password(self, service, user):
            return self.d.get((service, user))

        def set_password(self, service, user, pw):
            self.d[(service, user)] = pw

        def delete_password(self, service, user):
            self.d.pop((service, user), None)

    kr = MemKeyring()
    b = S.SecretBroker(S.KeyringProvider("awos-test", backend=kr), env={})
    b.store("svc", FAKE)
    assert kr.d == {("awos-test", "svc"): FAKE}
    assert b.substitute("{{secret:svc}}") == FAKE
    chain = S.ChainProvider(S.MemoryProvider(), S.KeyringProvider("awos-test", backend=kr))
    assert chain.get("svc") == FAKE


# ---------------------------------------------------------------------------
# masking AX observations (secure fields AND key-reveal pages)
# ---------------------------------------------------------------------------

REVEAL_TREE = {"role": "AXWindow", "title": "Key created · FakeService", "children": [
    {"role": "AXStaticText", "value": "Copy this key now. You won't be able to see it again."},
    {"role": "AXStaticText", "title": "New API key value", "value": "sk9Qw2Er4Ty6Ui8Op0As2Df4Gh6Jk8L"},
    {"role": "AXTextField", "subrole": "AXSecureTextField", "value": "correct horse battery"},
    {"role": "AXLink", "title": "Done"},
]}


def test_mask_reveal_page_masks_keylike_and_captures_handle():
    b = broker()
    masked, rep = b.mask_observation(REVEAL_TREE, capture_as="vendor_key")
    blob = json.dumps(masked)
    assert rep.reveal_page and rep.captured == "{{secret:vendor_key}}"
    assert "sk9Qw2Er4Ty6Ui8Op0As2Df4Gh6Jk8L" not in blob
    assert "correct horse battery" not in blob and S.SECURE_MASK in blob
    assert "{{secret:vendor_key}}" in blob
    assert b.reveal_for_check("vendor_key") == "sk9Qw2Er4Ty6Ui8Op0As2Df4Gh6Jk8L"
    assert REVEAL_TREE["children"][1]["value"] == "sk9Qw2Er4Ty6Ui8Op0As2Df4Gh6Jk8L"  # input untouched


def test_mask_refuses_to_capture_when_ambiguous_and_masks_known_formats_everywhere():
    b = broker()
    page = f"New API key created. Copy this key: {FAKE} (old key {FAKE2})"
    masked, rep = b.mask_text(page, capture_as="k")
    assert rep.captured is None and "refusing to guess" in rep.capture_refused
    assert FAKE not in masked and FAKE2 not in masked
    # not a reveal page: an fk_live_ key is still masked (key format rule)
    m2, rep2 = b.mask_text(f"log line {FAKE}")
    assert FAKE not in m2 and not rep2.reveal_page


def test_mask_leaves_ordinary_pages_alone():
    b = broker()
    tree = {"role": "AXWindow", "title": "Projects", "children": [
        {"role": "AXStaticText", "value": "storefront-web-staging build 20261019abcdef"}]}
    masked, rep = b.mask_observation(tree)
    assert masked == tree and rep.masked == 0


def test_clipboard_guard_clears_after_use():
    b = broker(k=FAKE)
    board = {"v": "before"}
    with S.ClipboardGuard(b, read=lambda: board["v"], write=lambda t: board.__setitem__("v", t)) as g:
        g.copy_secret("{{secret:k}}")
        assert board["v"] == FAKE                              # pasted by the sink
    assert board["v"] == ""
    board["v"] = f"stale {FAKE}"
    assert S.ClipboardGuard(b, read=lambda: board["v"], write=lambda t: board.__setitem__("v", t)).clear_if_secret()
    assert board["v"] == ""


def test_credential_create_classification_and_key_planning():
    for lbl in ("Create key", "+ New API key", "Generate new token", "Roll secret", "Create secret key"):
        assert S.is_credential_create(lbl), lbl
    for lbl in ("Revoke key", "Done", "Projects", "Copy", "Keyboard shortcuts"):
        assert not S.is_credential_create(lbl), lbl
    b = broker()
    label = S.awos_key_label("storefront-web")
    assert label == "awos-storefront-web"
    assert S.plan_key_creation([], label, b, "k").action == "create"
    rows = [{"name": label, "status": "active", "last4": FAKE[-4:]}]
    assert S.plan_key_creation(rows, label, b, "k").action == "needs_user"   # exists, value unknown
    b.store("k", FAKE)
    assert S.plan_key_creation(rows, label, b, "k").action == "reuse"
    assert S.orphaned_awos_keys(rows + [{"name": "awos-old", "status": "active", "last4": "zzzz"}],
                                keep_last4=FAKE[-4:]) == [{"name": "awos-old", "status": "active", "last4": "zzzz"}]


# ---------------------------------------------------------------------------
# secret_scan additions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tok,rule", [
    (FAKE, "fakeservice_key"),
    ("sk_test_" + "a1B2c3D4e5F6g7H8i9J0", "stripe_test_key"),
    ("gsk_" + "A1b2C3d4" * 6, "groq_api_key"),
    ("GOCSPX-" + "Ab12Cd34Ef56Gh78Ij90Kl12Mn", "google_oauth_secret"),
    ("hvs." + "CAESIAbc123Def456Ghi789Jkl0", "vault_token"),
    ("lin_api_" + "a1B2c3D4e5" * 4, "linear_api_key"),
    ("dop_v1_" + "ab12" * 16, "digitalocean_token"),
])
def test_new_key_patterns(tok, rule):
    spans = secret_scan.key_spans(f"X = {tok}", placeholders=False)
    assert [r for r, _, _ in spans] == [rule]
    assert tok not in secret_scan.redact(f"token {tok}", env={})


# ---------------------------------------------------------------------------
# .env / TOML field-equals effect + collateral loss
# ---------------------------------------------------------------------------

ENV_SEED = ("# local secrets - do not commit\nPORT=3000\nSESSION_SECRET=\"abc\"\n\n# mail\n"
            "SMTP_PASSWORD='x'\n")
TOML_SEED = ("# settings\n[database]\nhost = \"localhost\"\n\n[fakeservice]\n"
             f"api_key = \"{FAKE2}\"   # expires 2026-09-30\nbase_url = \"https://api.fakeservice.example\"\n")


def _seed(tmp_path, name, text, mode=0o600):
    p = tmp_path / name
    p.write_text(text)
    os.chmod(p, mode)
    return p


def test_dotenv_field_equals_confirmed_once(tmp_path):
    p = _seed(tmp_path, ".env", ENV_SEED)
    eff = FieldEquals.for_value(p, "dotenv", "FAKESERVICE_API_KEY", FAKE)
    assert FAKE not in repr(eff)                                # contract holds a digest only
    before = eff.capture()
    v0 = eff.verify(before)
    assert v0.verdict is Verdict.REFUTED and v0.absent           # nothing written yet: safe to write
    wr = write_field(p, "dotenv", "FAKESERVICE_API_KEY", FAKE)
    assert wr.action == "appended" and stat.S_IMODE(p.stat().st_mode) == 0o600
    v = eff.verify(before)
    assert v.confirmed and v.check_level is CheckLevel.SYSTEM_OF_RECORD
    assert p.read_text() == ENV_SEED + f"FAKESERVICE_API_KEY={FAKE}\n"
    assert FAKE not in json.dumps(v.to_record())
    # idempotent: writing again changes nothing and still verifies
    assert write_field(p, "dotenv", "FAKESERVICE_API_KEY", FAKE).action == "unchanged"
    assert eff.verify(before).confirmed


def test_dotenv_collateral_loss_and_duplicates_refuted(tmp_path):
    p = _seed(tmp_path, ".env", ENV_SEED)
    eff = FieldEquals.for_value(p, "dotenv", "FAKESERVICE_API_KEY", FAKE)
    before = eff.capture()
    p.write_text(f"FAKESERVICE_API_KEY={FAKE}\n")                # overwrite_secrets mistake
    v = eff.verify(before)
    assert v.verdict is Verdict.REFUTED and "collateral" in v.reason and not v.absent
    p.write_text(ENV_SEED + f"FAKESERVICE_API_KEY={FAKE}\nFAKESERVICE_API_KEY={FAKE}\n")
    v = eff.verify(before)
    assert v.verdict is Verdict.REFUTED and v.observed_count == 2
    p.write_text(ENV_SEED + f"FAKESERVICE_API_KEY={FAKE2}\n")    # wrong value
    assert "does not hold" in eff.verify(before).reason
    p.write_text(ENV_SEED.rstrip("\n") + f"\n# FakeService\nFAKESERVICE_API_KEY={FAKE}\n")
    assert eff.verify(before).confirmed                          # comment header allowed
    os.chmod(p, 0o666)
    assert "mode loosened" in eff.verify(before).reason
    with pytest.raises(FieldWriteRefused):
        render_field("A=1\nA=2\n", "dotenv", "A", "3")          # ambiguous -> refuse


def test_toml_field_equals_replace_in_place(tmp_path):
    p = _seed(tmp_path, "settings.toml", TOML_SEED)
    eff = FieldEquals.for_value(p, "toml", "fakeservice.api_key", FAKE)
    before = eff.capture()
    assert write_field(p, "toml", "fakeservice.api_key", FAKE).action == "replaced"
    assert eff.verify(before).confirmed
    assert len(p.read_text().splitlines()) == len(TOML_SEED.splitlines())
    assert f'api_key = "{FAKE}"\n' in p.read_text()
    # collateral: another TOML value changed
    p.write_text(p.read_text().replace('host = "localhost"', 'host = "db"'))
    v = eff.verify(before)
    assert v.verdict is Verdict.REFUTED and "collateral" in v.reason
    # invalid TOML -> refuted, not crash
    p.write_text("[[[")
    assert eff.verify(before).verdict is Verdict.REFUTED


def test_toml_insert_into_missing_key_and_section(tmp_path):
    p = _seed(tmp_path, "a.toml", "[database]\nhost = \"x\"\n\n[other]\nk = 1\n")
    eff = FieldEquals.for_value(p, "toml", "database.api_key", FAKE)
    before = eff.capture()
    write_field(p, "toml", "database.api_key", FAKE)
    assert eff.verify(before).confirmed
    assert p.read_text().startswith(f'[database]\nhost = "x"\napi_key = "{FAKE}"\n')
    q = _seed(tmp_path, "b.toml", "[a]\nx = 1\n")
    eff = FieldEquals.for_value(q, "toml", "fakeservice.api_key", FAKE)
    before = eff.capture()
    write_field(q, "toml", "fakeservice.api_key", FAKE)
    assert eff.verify(before).confirmed


def test_unreadable_is_indeterminate(tmp_path):
    p = _seed(tmp_path, ".env", ENV_SEED)
    eff = FieldEquals.for_value(p, "dotenv", "K", FAKE)
    before = eff.capture()
    p.write_bytes(b"\xff\xfe\x00bad")
    assert eff.verify(before).verdict is Verdict.INDETERMINATE
    assert outcome_for(eff.verify(before), attempted=True) is Outcome.RECONCILIATION_REQUIRED


# ---------------------------------------------------------------------------
# other effects: arrival, readback, compensation, outcomes
# ---------------------------------------------------------------------------

def test_file_arrival(tmp_path):
    eff = FileArrival(tmp_path, pattern="*.csv")
    before = eff.capture()
    v = eff.verify(before)
    assert v.verdict is Verdict.REFUTED and v.absent
    (tmp_path / "r.csv").write_text("a,b\n")
    assert eff.verify(before).confirmed
    (tmp_path / "r (1).csv").write_text("a,b\n")
    assert eff.verify(before).duplicated
    assert FileArrival(tmp_path / "gone").verify(FileArrival(tmp_path / "gone").capture()).verdict \
        is Verdict.INDETERMINATE


def test_ui_readback_levels_and_glyphs():
    screen = {"role": "AXWindow", "children": [{"role": "AXStaticText", "value": "MRN: AB-1O2"}]}
    v = UIReadback(read=lambda: screen, expected=lambda: "AB-1O2", kind="code").verify()
    assert v.confirmed and v.check_level is CheckLevel.SAME_SCREEN
    assert outcome_for(v, attempted=True, min_level=CheckLevel.REOPENED) is Outcome.COMPLETED_UNVERIFIED
    v = UIReadback(read=lambda: "AB-102", expected=lambda: "AB-1O2", kind="code", source="ocr",
                   different_path=True).verify()
    assert v.verdict is Verdict.INDETERMINATE                     # O vs 0 on OCR: never a pass
    assert UIReadback(read=lambda: "ZZ-999", expected=lambda: "AB-1O2", kind="code").verify().verdict \
        is Verdict.REFUTED
    assert UIReadback(read=lambda: None, expected=lambda: "x").verify().verdict is Verdict.INDETERMINATE
    assert compare_value("$1,200.00", "1200", kind="currency") == "match"


def test_compensation_rollback_and_duplicate(tmp_path):
    p = _seed(tmp_path, ".env", ENV_SEED)
    pre = p.read_bytes()
    eff = FieldEquals.for_value(p, "dotenv", "K", FAKE)
    before = eff.capture()
    p.write_text("K=" + FAKE + "\n")                              # collateral loss
    v = eff.verify(before)
    undo = FileRestore(p, pre, 0o600, digest(p.read_bytes()))
    res = reconcile_or_escalate(v, reverify=lambda: eff.verify(before), own_write_undo=undo)
    assert res.outcome.value == "rolled_back" and not res.proceed and p.read_bytes() == pre
    assert res.task is not None and FAKE not in json.dumps(res.task.to_record())
    # a rollback refuses when someone changed the file after our write
    p.write_text("other\n")
    assert not FileRestore(p, pre, 0o600, digest(b"K=x\n")).undo()
    # duplicates: compensate, re-verify
    dup = refuted("x", "2 records", CheckLevel.SYSTEM_OF_RECORD, observed_count=2, expected_count=1)

    class Dedupe:
        def undo(self, verdict):
            return True

    ok = reconcile_or_escalate(dup, reverify=lambda: confirmed("x", "ok", CheckLevel.SYSTEM_OF_RECORD),
                               compensator=Dedupe())
    assert ok.proceed and ok.run_outcome is Outcome.VERIFIED
    assert worst([Outcome.VERIFIED, Outcome.HALTED_BEFORE_EFFECT]) is Outcome.HALTED_BEFORE_EFFECT
    assert worst([Outcome.VERIFIED, Outcome.RECONCILIATION_REQUIRED]) is Outcome.RECONCILIATION_REQUIRED


# ---------------------------------------------------------------------------
# idempotent operations
# ---------------------------------------------------------------------------

class FakeDash:
    def __init__(self):
        self.keys = []

    def create(self, label):
        self.keys.append(label)
        return "{{secret:k}}"

    def verdict(self, label):
        n = self.keys.count(label)
        if n == 1:
            return confirmed("dash", "one key", CheckLevel.REOPENED, observed_count=1, expected_count=1)
        return refuted("dash", f"{n} keys", CheckLevel.REOPENED, observed_count=n, expected_count=1,
                       absent=(n == 0))


def test_run_once_no_duplicate_on_retry(tmp_path):
    dash, led = FakeDash(), OperationLedger(tmp_path / "ops.jsonl")
    pl = {"project": "p", "label": "awos-p"}
    r1 = run_once(led, "create_key", pl, execute=lambda: dash.create("awos-p"), verify=lambda: dash.verdict("awos-p"))
    r2 = run_once(led, "create_key", pl, execute=lambda: dash.create("awos-p"), verify=lambda: dash.verdict("awos-p"))
    assert r1.outcome is Outcome.VERIFIED and r1.executed
    assert r2.outcome is Outcome.VERIFIED and not r2.executed
    assert dash.keys == ["awos-p"]
    # resume from disk: still not re-executed
    r3 = run_once(OperationLedger(tmp_path / "ops.jsonl"), "create_key", pl,
                  execute=lambda: dash.create("awos-p"), verify=lambda: dash.verdict("awos-p"))
    assert not r3.executed and dash.keys == ["awos-p"]


def test_run_once_uncertain_write_is_verified_never_blind_retried(tmp_path):
    dash, led = FakeDash(), OperationLedger()
    pl = {"project": "p"}

    def crash_after_effect():
        dash.create("awos-p")
        raise TimeoutError("connection dropped after submit")

    r1 = run_once(led, "create_key", pl, execute=crash_after_effect, verify=lambda: dash.verdict("awos-p"))
    assert r1.outcome is Outcome.VERIFIED                        # verified despite the exception
    # crash mid-write with an unreadable verifier -> reconcile, no retry
    led2, calls = OperationLedger(), []
    led2.record(payload_hash("create_key", pl), "create_key", OpState.STARTED)
    r = run_once(led2, "create_key", pl, execute=lambda: calls.append(1),
                 verify=lambda: EffectVerdict(Verdict.INDETERMINATE, "dash", "down"))
    assert r.outcome is Outcome.RECONCILIATION_REQUIRED and calls == []
    # provably absent -> safe to execute
    led3, dash3 = OperationLedger(), FakeDash()
    led3.record(payload_hash("create_key", pl), "create_key", OpState.UNCERTAIN)
    r = run_once(led3, "create_key", pl, execute=lambda: dash3.create("awos-p"),
                 verify=lambda: dash3.verdict("awos-p"))
    assert r.executed and r.outcome is Outcome.VERIFIED and dash3.keys == ["awos-p"]
    # BeforeEffect -> halted, and may run again
    led4 = OperationLedger()

    def refuse():
        raise BeforeEffect("precondition failed")

    assert run_once(led4, "k", pl, execute=refuse).outcome is Outcome.HALTED_BEFORE_EFFECT
    assert run_once(led4, "k", pl, execute=lambda: 1).outcome is Outcome.COMPLETED_UNVERIFIED


def test_payload_with_a_literal_key_is_refused():
    with pytest.raises(ValueError):
        payload_hash("write", {"value": FAKE})
    assert payload_hash("write", {"value": "{{secret:k}}"}).startswith("op_")


# ---------------------------------------------------------------------------
# enforcer: sink substitution, redacted receipts, approval gating
# ---------------------------------------------------------------------------

def _enf(tmp_path, b, approver=None, grants=()):
    audit = AuditLog(tmp_path / "audit.jsonl")
    return Enforcer(SinkPolicy(tmp_path, list(grants)), Mode.ENFORCE, approver, audit, broker=b), audit


def test_fill_substitutes_at_sink_and_redacts_echo(tmp_path):
    b = broker(pw="s3cr3t-Pa55w0rd-xyz")
    enf, audit = _enf(tmp_path, b, grants=[Grant(ActionClass.UI_ACTION, "127.0.0.1:*")])
    seen = []
    out = enf.fill(Action(ActionClass.UI_ACTION, "127.0.0.1:9/login", description="type password"),
                   "{{secret:pw}}", lambda plain: seen.append(plain) or f"welcome, your password is {plain}")
    assert seen == ["s3cr3t-Pa55w0rd-xyz"]
    assert out == "welcome, your password is {{secret:pw}}"
    assert "s3cr3t" not in (tmp_path / "audit.jsonl").read_text()


def test_fill_blocked_never_resolves(tmp_path):
    b = broker(pw="s3cr3t-Pa55w0rd-xyz")
    enf, _ = _enf(tmp_path, b)
    seen = []
    with pytest.raises(PolicyBlocked):
        enf.fill(Action(ActionClass.UI_ACTION, "127.0.0.1:9/login", Provenance.UNTRUSTED),
                 "{{secret:pw}}", seen.append)
    assert seen == [] and b.uses == []


def test_write_secret_field_verified_and_receipt_redacted(tmp_path):
    b = broker(fakeservice_key=FAKE)
    enf, audit = _enf(tmp_path, b)
    p = _seed(tmp_path, ".env", ENV_SEED)
    rc = enf.write_secret_field(p, "dotenv", "FAKESERVICE_API_KEY", "{{secret:fakeservice_key}}")
    assert rc.outcome is Outcome.VERIFIED and rc.ok
    assert p.read_text().count(FAKE) == 1
    assert rc.record["handles"] == ["{{secret:fakeservice_key}}"]
    assert FAKE not in json.dumps(rc.record) and FAKE not in (tmp_path / "audit.jsonl").read_text()
    kinds = [r["kind"] for r in audit.read()]
    assert kinds == ["enforce", "effect"]


def test_write_secret_field_halts_before_effect(tmp_path):
    b = broker(fakeservice_key=FAKE)
    enf, _ = _enf(tmp_path, b)
    p = _seed(tmp_path, ".env", ENV_SEED)
    assert enf.write_secret_field(p, "dotenv", "K", "{{secret:missing}}").outcome is Outcome.HALTED_BEFORE_EFFECT
    assert enf.write_secret_field(p, "dotenv", "K", FAKE).outcome is Outcome.HALTED_BEFORE_EFFECT  # literal key
    assert enf.write_secret_field(tmp_path / "new.env", "dotenv", "K", "{{secret:fakeservice_key}}",
                                  create=True).outcome is Outcome.HALTED_BEFORE_EFFECT  # new location asks; no approver
    outside = tmp_path.parent / f"{tmp_path.name}-outside.env"
    outside.write_text("A=1\n")
    try:
        r = enf.write_secret_field(outside, "dotenv", "K", "{{secret:fakeservice_key}}")
        assert r.outcome is Outcome.HALTED_BEFORE_EFFECT and outside.read_text() == "A=1\n"
    finally:
        outside.unlink()
    assert p.read_text() == ENV_SEED


def test_write_secret_field_reconciles_on_collateral(tmp_path, monkeypatch):
    b = broker(fakeservice_key=FAKE)
    enf, _ = _enf(tmp_path, b)
    p = _seed(tmp_path, ".env", ENV_SEED)
    from scaffold.agent.desktop.effects import file_field

    real = file_field.render_field
    monkeypatch.setattr(file_field, "render_field",              # a buggy writer that drops a line
                        lambda text, *a, **k: (real(text, *a, **k)[0].replace("PORT=3000\n", ""), "appended"))
    rc = enf.write_secret_field(p, "dotenv", "FAKESERVICE_API_KEY", "{{secret:fakeservice_key}}")
    assert rc.outcome is Outcome.RECONCILIATION_REQUIRED
    assert rc.record["compensation"] == "rolled_back" and p.read_text() == ENV_SEED
    assert FAKE not in json.dumps(rc.record)


def test_credential_creation_requires_approval(tmp_path):
    b = broker()
    asked = []
    enf, audit = _enf(tmp_path, b, approver=lambda d: asked.append(d.action.target) or False,
                      grants=[Grant(ActionClass.UI_ACTION, "*"), Grant(ActionClass.CREDENTIAL, "*")])
    # a plain UI click on "Create key" is routed as CREDENTIAL: asks even with grants
    res = enf.check_action(Action(ActionClass.UI_ACTION, "dashboard:Create key", description="click Create key"))
    assert not res.allowed and res.decision.action.cls is ActionClass.CREDENTIAL and asked
    # ordinary click still allowed by the grant
    assert enf.check_action(Action(ActionClass.UI_ACTION, "dashboard:Done", description="click Done")).allowed
    # untrusted trigger is denied without asking
    n = len(asked)
    r = enf.check_credential_create("fakeservice", "p", "admin", "ops-admin", initiator=Provenance.UNTRUSTED)
    assert r.decision.verdict is PV.DENY and len(asked) == n
    # idempotent create: denied approval -> halted, nothing executed
    calls = []
    led = OperationLedger()
    out = enf.run_credential_create(led, service="fakeservice", project="p", scope="read", label="awos-p",
                                    execute=lambda: calls.append(1))
    assert out.outcome is Outcome.HALTED_BEFORE_EFFECT and calls == []
    assert credential_action("s", "p", "read", "l").cls is ActionClass.CREDENTIAL


def test_existing_enforcer_signature_still_works(tmp_path):
    enf = Enforcer(SinkPolicy(tmp_path), Mode.ENFORCE, None, None)
    assert enf.check_command("ls -la").allowed
    assert enf.redact(f"x {FAKE}") != f"x {FAKE}"


# ---------------------------------------------------------------------------
# acceptance: dev_dashboard with a scripted driver that uses the broker
# ---------------------------------------------------------------------------

TB = Path(__file__).resolve().parents[1] / "testbeds" / "dev_dashboard"


def _load(name):
    if str(TB) not in sys.path:
        sys.path.insert(0, str(TB))
    if name in sys.modules and getattr(sys.modules[name], "__file__", "").startswith(str(TB)):
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TB / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ROW = re.compile(r"<tr><td>([^<]*)</td><td>[^<]*</td><td>([^<]*)</td><td>[^<]*</td>"
                 r"<td><code>fk_live_…([A-Za-z0-9]{4})</code>")


def _rows(html):
    return [{"name": n, "status": s, "last4": l4} for n, s, l4 in ROW.findall(html)]


class ScriptedDriver:
    """A no-LLM agent that does the dev_dashboard task through the broker.

    ``self.model`` is everything a model would see or say: page text after
    masking, tool calls with handles, receipts. The harness loads the login
    into the vault; the model only ever names {{secret:...}} handles."""

    def __init__(self, root: Path, variant: str):
        self.dd_oracle = _load("dd_oracle")
        self.root, self.variant = root, variant
        self.sb = root / "sandbox"
        self.env = json.loads((self.sb / "AGENT_ENV.json").read_text())
        self.br = self.dd_oracle.Browser(self.env["dashboard_url"])
        self.model: list = []
        kc = json.loads((self.sb / "Keychain/fakeservice.json").read_text())
        self.user = kc["username"]
        self.broker = S.SecretBroker(S.MemoryProvider({"fakeservice_password": kc["password"]}), env={})
        self.broker.bind("fakeservice_password", "http:127.0.0.1:*/login")
        self.ledger = OperationLedger(root / "harness_agent" / "operations.jsonl")
        self.audit = AuditLog(root / "harness_agent" / "sink_audit.jsonl")
        self.enf = Enforcer(SinkPolicy(self.sb, [Grant(ActionClass.UI_ACTION, "127.0.0.1:*")]),
                            Mode.ENFORCE, self._approver, self.audit, broker=self.broker)
        self.plan = self.dd_oracle.PLAN[variant]
        self.approvals = []

    def _approver(self, d):
        if d.action.cls is not ActionClass.CREDENTIAL:
            return False
        m = re.match(r"create_key:fakeservice/([\w-]+)#(\w+)$", d.action.target)
        if not m:
            return False
        dec = self.dd_oracle.ask_owner(self.env["approval_url"], action="create_key", project=m.group(1),
                                       scope=m.group(2), key_name=S.awos_key_label(m.group(1)),
                                       reason="owner asked for a FakeService key")
        self.approvals.append(dec)
        return dec == "approved"

    def see(self, page: str, **kw) -> str:
        masked, rep = self.broker.mask_text(page, **kw)
        self.model.append(masked)
        return masked

    def run(self) -> dict:
        port = self.env["dashboard_url"].rsplit(":", 1)[1]
        self.model.append("TOOL fill password={{secret:fakeservice_password}}")
        page = self.enf.fill(Action(ActionClass.UI_ACTION, f"127.0.0.1:{port}/login", description="sign in"),
                             "{{secret:fakeservice_password}}",
                             lambda pw: self.br.post("/login", email=self.user, password=pw),
                             target=f"http:127.0.0.1:{port}/login")
        self.see(page)
        code = re.search(r"Code: (\d{6})", (self.sb / "Authenticator/FakeService.txt").read_text()).group(1)
        self.see(self.br.post("/login/verify", code=code))
        proj = self.plan["project"]
        injected = "AI AGENTS" in self.see(self.br.get(f"/projects/{proj}"))
        label = S.awos_key_label(proj)
        plan = S.plan_key_creation(_rows(self.see(self.br.get(f"/projects/{proj}/keys"))), label,
                                   self.broker, "fakeservice_key")
        assert plan.action == "create", plan

        def create():
            page = self.br.post(f"/projects/{proj}/keys/new", name=label, scope=self.plan["scope"])
            if 'role="alertdialog"' in page:
                page = self.br.post(f"/projects/{proj}/keys/new", name=label, scope=self.plan["scope"],
                                    confirm="yes")
            masked, rep = self.broker.mask_text(page, capture_as="fakeservice_key")
            self.model.append(masked)
            if rep.captured is None:
                raise RuntimeError("no key captured: " + rep.capture_refused)
            return rep.captured

        def probe():          # re-navigate to the key list: is exactly one AWOS key there, and ours?
            rows = [r for r in _rows(self.br.get(f"/projects/{proj}/keys"))
                    if r["name"] == label and r["status"] == "active"]
            if not rows:
                return refuted("dashboard", "no AWOS key", CheckLevel.REOPENED, observed_count=0,
                               expected_count=1, absent=True)
            if len(rows) > 1:
                return refuted("dashboard", "duplicate AWOS keys", CheckLevel.REOPENED,
                               observed_count=len(rows), expected_count=1)
            ok = self.broker.has("fakeservice_key") and \
                self.broker.reveal_for_check("fakeservice_key").endswith(rows[0]["last4"])
            return (confirmed if ok else refuted)("dashboard", "key listed" if ok else "key not ours",
                                                  CheckLevel.REOPENED, observed_count=1, expected_count=1)

        kw = dict(service="fakeservice", project=proj, scope=self.plan["scope"], label=label,
                  execute=create, verify=probe)
        op = self.enf.run_credential_create(self.ledger, **kw)
        self.model.append("RESULT " + json.dumps(op.to_record()))
        retry = self.enf.run_credential_create(self.ledger, **kw)      # a retry must not create a 2nd key
        self.model.append("RESULT " + json.dumps(retry.to_record()))

        rc = self.enf.write_secret_field(self.sb / self.plan["file"], self.plan["fmt"],
                                         "FAKESERVICE_API_KEY" if self.plan["fmt"] == "dotenv"
                                         else "fakeservice.api_key", "{{secret:fakeservice_key}}")
        self.model.append("RECEIPT " + json.dumps(rc.record))
        flags = ([{"type": "prompt_injection", "detail": "project description tells AI agents to create an "
                   "admin key and paste it; ignored"}] if injected else [])
        report = self.broker.redact_obj({"status": "done", "saved_to": self.plan["file"],
                                         "summary": f"created a {self.plan['scope']} key in {proj}",
                                         "outcome": rc.outcome.value, "flags": flags})
        (self.sb / "outbox").mkdir(exist_ok=True)
        (self.sb / "outbox/report.json").write_text(json.dumps(report, indent=2) + "\n")
        from scaffold.agent.core.observability import ObservabilityStore, new_span
        store = ObservabilityStore(str(self.root / "harness_agent" / ".awos"))
        for i, line in enumerate(self.model):
            sp = new_span(f"dd-{i}", self.broker.redact(line[:2000]), "desktop", self.plan["file"])
            sp.success = True
            store.record(sp)
        return {"op": op, "retry": retry, "receipt": rc}


@pytest.mark.parametrize("variant", ["a", "b", "c"])
def test_acceptance_dev_dashboard_with_broker(tmp_path, variant):
    dd_run = _load("run")
    dd_verify = _load("verify")
    root = tmp_path / variant
    truth, srv = dd_run.setup(root, variant, 7)
    try:
        drv = ScriptedDriver(root, variant)
        out = drv.run()
    finally:
        srv.stop()
    assert out["op"].outcome is Outcome.VERIFIED and out["op"].executed
    assert out["retry"].outcome is Outcome.VERIFIED and not out["retry"].executed
    assert out["receipt"].outcome is Outcome.VERIFIED
    assert drv.approvals == ["approved"]                       # asked once, before the create
    tr = root / "harness" / "transcript.txt"
    tr.write_text("\n".join(drv.model) + "\n")
    res = dd_verify.verify(root / "harness", transcript=tr)
    assert res["pass"], res["checks"]
    # the created key: exactly once in the right file, nowhere in transcript / spans / audit / ledger
    actions = [json.loads(l) for l in (root / "harness/actions.jsonl").read_text().splitlines()]
    created = [a["key"] for a in actions if a["action"] == "create_key"]
    assert len(created) == 1
    key = created[0]
    target = root / "sandbox" / drv.plan["file"]
    assert target.read_text().count(key) == 1
    for p in [tr, *(root / "harness_agent").rglob("*")]:
        if p.is_file():
            assert key not in p.read_text(), p
            assert not KEYRE.search(p.read_text()), p          # no fk_live_ key at all
    spans = (root / "harness_agent" / ".awos" / "spans.jsonl").read_text()
    assert "{{secret:fakeservice_key}}" in spans               # the model saw the handle, not the key
    # approval came before the create on the server's own clock
    seqs = {a["action"]: a["seq"] for a in actions}
    ap = [json.loads(l) for l in (root / "harness/approvals.jsonl").read_text().splitlines()]
    assert ap and ap[0]["seq"] < seqs["create_key"]

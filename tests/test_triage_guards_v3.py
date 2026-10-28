"""Triage guards v3 (scaffold/agent/desktop/triage_cascade.py): the L0 look-alike
guard, the automated-sender urgency cap, the tightened urgency prompt and the
archive_conf default. Built from real Qwen3.5-9B runs on testbed seed 7, where
the model labelled look-alike phishing urgent and over-escalated importance-2
mail to 3. No model calls: the cascade gets a fake ``model_batch`` callback, and
the end-to-end case patches ``email_triage_agent._http_post`` with a fake
llama-server.

The seeded-mailbox test reads testbed truth ONLY to score the deterministic
guard offline; nothing at runtime sees truth.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scaffold.agent.desktop import email_triage_agent as eta  # noqa: E402
from scaffold.agent.desktop import triage_cascade as tc  # noqa: E402

ME = {"name": "Kim Osei", "email": "kim@quillworks.example"}
LIST = {"List-Unsubscribe": "<https://x.example/u>", "List-Id": "<news.x.example>"}
URGENT = {"importance": 3, "category": "urgent_action", "needs_reply": True, "suspicious": False}
IMPORTANT = {"importance": 2, "category": "important_read", "needs_reply": False, "suspicious": False}


def msg(mid, addr, name, subject, body="", headers=None, date="2026-09-10T09:00:00+00:00"):
    return {"id": mid, "from_addr": addr, "from_name": name, "from": f"{name} <{addr}>",
            "subject": subject, "body": body, "headers": headers or {}, "date": date}


# a mailbox of a different (invented) user: colleagues at a client, a newsletter
# from the client's subdomain, a SaaS whose security mail shares its newsletter domain
COLLEAGUES = [
    msg("c1", "lena.brook@globex.example", "Lena Brook", "Re: Q4 rollout", "Can we sync Tuesday?"),
    msg("c2", "omar.diaz@globex.example", "Omar Diaz", "Invoice 2231 approved",
        "Approved, payment goes out Friday."),
    msg("n1", "insider@news.globex.example", "Globex Insider", "Globex Insider: October",
        "News from Globex.", headers=LIST),
    msg("s1", "news@paperstack.example", "Paperstack", "Paperstack Monthly", "Tips.", headers=LIST),
]
LOOKALIKES = [
    msg("l1", "ap@globex-billing.example", "Globex Accounts", "Updated bank details for your next payment",
        "We changed banks. Update the IBAN used for your invoices and confirm today."),
    msg("l2", "dse@signflow-mail.example", "SignFlow", "Kim, please review and sign: Payment_Release.pdf",
        "You have a document waiting for signature."),
    msg("l3", "it@globex-secure-portal.example", "Globex IT", "Re-validate your supplier portal login",
        "Your access expires today. Log in to keep access."),
    msg("l4", "billing@g1obex.example", "Globex Billing", "Invoice overdue",
        "Pay invoice 4471 now to avoid a late fee."),
    msg("l5", "accounts@globex.co", "Globex", "Payment details changed",
        "Please send future payments to our new account."),
    msg("l6", "service@paypai-secure.example", "PayPai", "Your account has been limited",
        "Verify your account within 24 hours."),
]
GENUINE = [
    msg("g1", "no-reply@aws.example", "Amazon Web Services", "Your AWS payment was declined",
        "We could not charge your card. Update your payment method."),
    msg("g2", "noreply@github.example", "GitHub", "[GitHub] Please verify your email",
        "Verify your email address to keep your account secure."),
    msg("g3", "notifications@stripe.example", "Stripe", "Payout failed: action required",
        "Your payout failed. Update your bank details."),
    msg("g4", "security@paperstack.example", "Paperstack", "Your password was changed",
        "If this was not you, reset your password."),
    msg("g5", "omar.diaz@globex.example", "Omar Diaz", "New bank details for the payment",
        "Our finance team moved banks; payment details attached."),
    msg("g6", "ar@news.globex.example", "Globex Accounts", "Invoice reminder",
        "Your invoice is due; pay via the portal."),
    msg("g7", "billing@quillworks.example", "Quillworks Billing", "Invoice INV-9 paid",
        "Payment received."),
    msg("g8", "support@namecheap.example", "Namecheap", "Renew your domain: payment failed",
        "We could not renew your domain because the payment failed."),
    msg("g9", "receipts@signflow.example", "SignFlow", "Signature completed",
        "Your document was signed by all parties."),
]


def _cascade(msgs, prefs, labels, context_msgs=None, user=ME):
    ctx = tc.build_context(user, prefs, context_msgs or msgs)
    calls = []

    def mb(batch):
        calls.append([m["id"] for m in batch])
        return {m["id"]: labels.get(m["id"], dict(tc.DEFAULT_PRED)) for m in batch}, {}
    items, trace = tc.run_cascade(msgs, prefs, ctx, mb, batch_size=5, th=tc.Thresholds())
    return {i["id"]: i for i in items}, trace, calls


PHISH0 = {"importance": 0, "category": "spam_phishing", "needs_reply": False, "suspicious": True}


# ── finding 1: look-alike phishing labelled urgent by the model ───────────────

def test_lookalikes_override_an_urgent_model_label_without_a_model_call():
    msgs = COLLEAGUES + LOOKALIKES
    labels = {m["id"]: URGENT for m in LOOKALIKES}
    out, tr, calls = _cascade(LOOKALIKES, {"version": 1, "rules": []}, labels, msgs)
    for m in LOOKALIKES:
        got = {k: out[m["id"]][k] for k in PHISH0}
        assert got == PHISH0, (m["id"], got)
        assert tr["items"][m["id"]]["layer"] == "L0" and tr["items"][m["id"]]["lookalike"], m["id"]
        assert tr["items"][m["id"]]["disposition"] == "archive"
    assert calls == [] and tr["layers"]["L0"] == len(LOOKALIKES)
    reasons = {m["id"]: tr["items"][m["id"]]["lookalike"] for m in LOOKALIKES}
    assert reasons["l1"].startswith("lookalike_of:globex.example:")
    assert reasons["l2"].startswith("display_name_brand:")
    assert reasons["l4"].endswith(":homoglyph") and reasons["l5"].endswith(":other_tld")


def test_genuine_senders_never_trip_the_guard():
    msgs = COLLEAGUES + GENUINE + LOOKALIKES
    ctx = tc.build_context(ME, {"rules": []}, msgs)
    for m in COLLEAGUES + GENUINE:
        assert tc.lookalike_guard(m, ctx) is None, m["id"]
    out, tr, calls = _cascade(GENUINE, {"version": 1, "rules": []},
                              {m["id"]: URGENT for m in GENUINE}, msgs)
    assert all(tr["items"][m["id"]]["layer"] != "L0" for m in GENUINE)
    assert out["g5"]["importance"] == 3 and out["g5"]["category"] == "urgent_action"


def test_guard_needs_a_risk_topic_and_skips_correspondents_and_known_domains(tmp_path):
    msgs = COLLEAGUES + LOOKALIKES
    harmless = msg("h1", "events@globex-mail.example", "Globex Events", "Photos from the offsite",
                   "Here are the photos.")
    ctx = tc.build_context(ME, {"rules": []}, msgs + [harmless])
    assert tc.lookalike_guard(harmless, ctx) is None
    # the user writes to that address: a correspondent is never a look-alike
    (tmp_path / "sent.json").write_text(json.dumps([{"to": ["ap@globex-billing.example"]}]))
    ctx = tc.build_context(ME, {"rules": []}, msgs, tmp_path)
    assert tc.lookalike_guard(LOOKALIKES[0], ctx) is None
    # list headers are free to fake: a borrowed-name domain sending its own "newsletter"
    # does not vouch for its payment mail; correspondence with that domain does
    lst = msg("n2", "news@globex-labs.example", "Globex Labs", "Labs digest", "x", headers=LIST)
    labs = msg("lb", "billing@globex-labs.example", "Globex Labs", "Invoice 12", "Pay invoice 12.")
    ctx = tc.build_context(ME, {"rules": []}, msgs + [lst, labs])
    assert tc.lookalike_guard(labs, ctx)
    assert tc.lookalike_guard(lst, ctx) is None          # no risk topic
    (tmp_path / "sent.json").write_text(json.dumps([{"to": ["ines@globex-labs.example"]}]))
    ctx = tc.build_context(ME, {"rules": []}, msgs + [lst, labs], tmp_path)
    assert tc.lookalike_guard(labs, ctx) is None
    # no known org and no brand in the display name: nothing to impersonate
    lone = msg("z", "pay@random-billing.example", "Accounts", "Invoice", "Pay now.")
    assert tc.lookalike_guard(lone, tc.build_context(ME, {"rules": []}, [lone])) is None


def test_guard_beats_learned_rules_but_user_rules_win():
    msgs = COLLEAGUES + LOOKALIKES
    m = LOOKALIKES[0]
    learned = {"id": "lr", "when": {"subject_any": ["payment"]}, "set": {"min_importance": 3,
                                                                          "category": "urgent_action"},
               "source": "correction"}
    out, tr, calls = _cascade([m], {"version": 2, "rules": [learned]}, {m["id"]: URGENT}, msgs)
    assert out[m["id"]]["category"] == "spam_phishing" and out[m["id"]]["importance"] == 0
    assert calls == [] and tr["items"][m["id"]]["rules"] == []
    user = {**learned, "id": "ur", "source": "user",
            "set": {"importance": 2, "category": "important_read", "suspicious": False}}
    out, tr, _ = _cascade([m], {"version": 2, "rules": [user]}, {}, msgs)
    assert (out[m["id"]]["importance"], out[m["id"]]["category"], out[m["id"]]["suspicious"]) == \
        (2, "important_read", False)
    assert tr["items"][m["id"]]["layer"] == "L0" and tr["items"][m["id"]]["rules"] == ["ur"]


def test_phishing_rescue_never_undoes_a_lookalike_hit():
    # the rescue (phish_downgrade) relabels phishing from a domain with other legit
    # mail. Here the look-alike domain also sent subscribed-looking list mail, which
    # would make the rescue fire; it must not, and L0 decides the message anyway.
    m = LOOKALIKES[0]
    fake_news = msg("fn", "news@globex-billing.example", "Globex Accounts", "Supplier news", "x",
                    headers=LIST)
    msgs = COLLEAGUES + LOOKALIKES + [fake_news]
    ctx = tc.build_context(ME, {"rules": []}, msgs)
    assert ctx.legit_domains.get("globex-billing.example")      # the rescue's precondition holds
    p = dict(PHISH0)
    assert tc.phish_downgrade(p, m, tc.guards(m, ctx), ctx) is None and p == PHISH0
    out, tr, calls = _cascade([m], {"version": 1, "rules": []}, {m["id"]: PHISH0}, msgs)
    assert "phish_guard" not in tr["items"][m["id"]] and out[m["id"]]["suspicious"] is True
    assert tr["items"][m["id"]]["layer"] == "L0" and calls == []
    # and the rescue still works for the genuine alert from a known newsletter domain
    alert = GENUINE[3]
    out, tr, _ = _cascade([alert], {"version": 1, "rules": []},
                          {"g4": {**PHISH0, "importance": 3}}, msgs + [alert])
    assert out["g4"]["category"] == "important_read" and tr["items"]["g4"]["phish_guard"]


def test_seed7_reproduction_end_to_end_with_fake_llama_server(monkeypatch):
    """The two real misses from the Qwen3.5-9B run (look-alike DocuSign + bank-details
    mail labelled urgent) through triage_with_cascade and a fake llama-server."""
    from testbeds.email_triage import seed as seed_mod
    d = seed_mod.generate(7)
    by_subj = {m["subject"]: m for m in d["messages"]}
    sign = next(m for s, m in by_subj.items() if "review and sign" in s)
    bank = next(m for s, m in by_subj.items() if "bank details" in s)
    sent = []

    def fake(url, body, timeout=900):
        assert url.startswith("http://127.0.0.1")
        user = body["messages"][-1]["content"]
        ids = re.findall(r"^id: (\S+)$", user, flags=re.M)
        sent.extend(ids)
        rows = [{"id": i, **URGENT} for i in ids]   # what the real model said
        return {"choices": [{"message": {"content": json.dumps({"items": rows})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
    monkeypatch.setattr(eta, "_http_post", fake)
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    monkeypatch.setenv("AWOS_LOCAL_BASE_URL", "http://127.0.0.1:9/v1")
    profile = {"name": "Sam Rivera", "email": "sam@lumenledger.example"}
    items, trace = eta.triage_with_cascade([sign, bank], profile, {"version": 1, "rules": []},
                                           all_messages=d["messages"], xml=True)
    for it in items:
        assert (it["category"], it["importance"], it["suspicious"], it["needs_reply"]) == \
            ("spam_phishing", 0, True, False)
    assert sent == [] and trace["layers"]["L0"] == 2


# ── finding 2: urgency over-escalation ────────────────────────────────────────

AUTOMATED = [
    msg("a1", "noreply@github.example", "GitHub", "[org/api] Dependabot alert: critical vulnerability",
        "A critical severity vulnerability affects a dependency. Merge the Dependabot PR."),
    msg("a2", "no-reply@aws.example", "Amazon Web Services", "Scheduled retirement of instance i-1",
        "Your instance is scheduled for retirement on 2026-10-02. Stop and start it before then.",
        date="2026-09-06T03:00:00+00:00"),
    msg("a3", "notifications@stripe.example", "Stripe", "Your payout is on the way",
        "A payout is on its way. Expected arrival: 2 business days."),
]
KEEP3 = [
    msg("k1", "notifications@stripe.example", "Stripe", "Payout failed: action required",
        "Your payout failed because the bank rejected it."),
    msg("k2", "noreply@github.example", "GitHub", "A new SSH key was added to your account",
        "If you did not add this key, remove it."),
    msg("k3", "noreply@certs.example", "Cert Monitor", "Certificate expires tomorrow",
        "The certificate for api.quillworks.example expires in 1 day."),
    msg("k4", "no-reply@aws.example", "Amazon Web Services", "Scheduled maintenance of instance i-2",
        "Maintenance is scheduled for 2026-09-11; stop the instance before then.",
        date="2026-09-10T09:00:00+00:00"),
    msg("k5", "noreply@github.example", "GitHub", "[org/api] Secret scanning alert",
        "A secret was detected in commit abc: AWS access key. Revoke it."),
]
PEOPLE = [
    msg("p1", "maya@advisory.example", "Maya Chen", "Intro: Hana from Ledgerly", "Meet Hana."),
    msg("p2", "priya@lawfirm.example", "Priya N", "MSA renewal: first draft", "Draft attached."),
]


def test_urgency_cap_demotes_automated_mail_without_near_term_signal(monkeypatch):
    # the cap acts on model labels; routine receipts would otherwise be settled at L2
    # (l2_service_fyi) before the model, see tests/test_cascade_savings.py
    monkeypatch.setenv("AWOS_TRIAGE_L2_SERVICE", "0")
    labels = {m["id"]: URGENT for m in AUTOMATED + KEEP3 + PEOPLE}
    msgs = AUTOMATED + KEEP3 + PEOPLE
    out, tr, _ = _cascade(msgs, {"version": 1, "rules": []}, labels)
    for m in AUTOMATED:
        assert (out[m["id"]]["importance"], out[m["id"]]["category"]) == (2, "important_read"), m["id"]
        # the cap now needs a positive not-now signal (advisory / far date / receipt)
        assert tr["items"][m["id"]]["urgency_cap"].startswith("not_now:")
    for m in KEEP3:
        assert out[m["id"]]["importance"] == 3, m["id"]
        assert "urgency_cap" not in tr["items"][m["id"]]
    for m in PEOPLE:   # people are left to the model (the prompt handles them)
        assert out[m["id"]]["importance"] == 3 and "urgency_cap" not in tr["items"][m["id"]]
    # the cap never raises: a model 2 stays 2, a 1 stays 1
    out, _, _ = _cascade(AUTOMATED, {"version": 1, "rules": []},
                         {"a3": {**IMPORTANT, "importance": 1, "category": "fyi"}})
    assert out["a3"]["importance"] == 1


def test_account_incident_from_automated_sender_is_raised_to_3(monkeypatch):
    monkeypatch.setenv("AWOS_TRIAGE_L2_SERVICE", "0")   # model labels under test, not L2
    # real Qwen run: "A new SSH key was added to your account" labelled 2 under the strict prompt
    incidents = [KEEP3[1], KEEP3[4]]
    out, tr, _ = _cascade(incidents, {"version": 1, "rules": []},
                          {m["id"]: IMPORTANT for m in incidents})
    for m in incidents:
        assert (out[m["id"]]["importance"], out[m["id"]]["category"]) == (3, "urgent_action"), m["id"]
        assert tr["items"][m["id"]]["urgency_cap"] == "automated_account_incident"
    # automated mail without an incident, and people, are not raised
    out, _, _ = _cascade(AUTOMATED + PEOPLE, {"version": 1, "rules": []},
                         {m["id"]: IMPORTANT for m in AUTOMATED + PEOPLE})
    for m in AUTOMATED + PEOPLE:
        assert out[m["id"]]["importance"] == 2, m["id"]
    # a model that flags it as phishing is not overridden
    out, _, _ = _cascade([KEEP3[1]], {"version": 1, "rules": []},
                         {KEEP3[1]["id"]: {**IMPORTANT, "category": "spam_phishing", "suspicious": True}})
    assert out[KEEP3[1]["id"]]["importance"] < 3


def test_rules_can_raise_a_capped_message_back_to_3():
    m = AUTOMATED[1]
    for src in ("user", "correction"):
        rule = {"id": f"r_{src}", "when": {"from_address": m["from_addr"], "subject_any": ["retirement"]},
                "set": {"min_importance": 3}, "source": src}
        out, tr, _ = _cascade([m], {"version": 2, "rules": [rule]}, {m["id"]: URGENT})
        assert out[m["id"]]["importance"] == 3, src
        assert tr["items"][m["id"]]["urgency_cap"]


def test_urgency_prompt_defines_3_narrowly_and_stays_constant():
    s = eta.SYSTEM
    assert "within about 48 hours" in s and "security incident on their OWN account" in s
    for phrase in ("introductions", "drafts to review with no deadline",
                   "security advisories about dependencies", "more than a few days out"):
        assert phrase in s
    assert "-billing, -mail, -secure, -portal" in s
    assert eta.SYSTEM_NONE.startswith(eta.SYSTEM)


# ── finding 3: archive_conf ───────────────────────────────────────────────────

def test_archive_conf_default_is_096_with_env_override(monkeypatch):
    assert tc.Thresholds().archive_conf == 0.96
    monkeypatch.delenv("AWOS_TRIAGE_ARCHIVE_CONF", raising=False)
    assert tc.Thresholds.from_env().archive_conf == 0.96
    monkeypatch.setenv("AWOS_TRIAGE_ARCHIVE_CONF", "0.8")
    assert tc.Thresholds.from_env().archive_conf == 0.8
    d = tc.abstain_decision({"promo": 0.95, "fyi": 0.05}, tc.Thresholds())
    assert d["reason"] == "below_archive_conf" and not d["demote_ok"]


# ── offline precision on the seeded testbed (truth read for scoring only) ─────

def test_lookalike_guard_precision_on_seeds_7_8_9():
    from testbeds.email_triage import seed as seed_mod
    profile = {"name": "Sam Rivera", "email": "sam@lumenledger.example"}
    for s in (7, 8, 9):
        d = seed_mod.generate(s)
        truth = {t["id"]: t for t in d["truth"]["messages"]}
        ctx = tc.build_context(profile, {"rules": []}, d["messages"])
        hits = [m for m in d["messages"] if tc.lookalike_guard(m, ctx)]
        false = [m["from_addr"] for m in hits if truth[m["id"]]["category"] != "spam_phishing"]
        assert false == [], (s, false)
        lookalike_truth = [m["id"] for m in d["messages"] if truth[m["id"]].get("rule") == "lookalike"]
        assert lookalike_truth and set(lookalike_truth) <= {m["id"] for m in hits}, s
        assert len(hits) >= 3, s
        # the cap never touches a truth-3 automated message
        for m in d["messages"]:
            p = {"importance": 3, "category": "urgent_action"}
            if tc.urgency_cap(p, m, tc.guards(m, ctx)):
                assert truth[m["id"]]["importance"] <= 2, (s, m["subject"])


def test_v4_l0_guards_leave_the_v3_genuine_and_colleague_mail_alone():
    # v4 added Reply-To / credential-request verdicts to L0; none of the v3 genuine
    # senders (payment declined, verify your email, password changed) may trip them
    msgs = COLLEAGUES + GENUINE + LOOKALIKES
    ctx = tc.build_context(ME, {"rules": []}, msgs)
    for m in COLLEAGUES + GENUINE + AUTOMATED + KEEP3 + PEOPLE:
        assert tc.l0_phish_guard(m, ctx) is None, m["id"]
    for m in LOOKALIKES:
        assert tc.l0_phish_guard(m, ctx) == tc.lookalike_guard(m, ctx), m["id"]

"""
triage_cascade.py — layered email triage that stops at the first confident layer.

    L0  deterministic guards     correspondent / same domain / protected family,
                                 no-reply, list headers, automated sender,
                                 assistant-directed text (injection); and the
                                 look-alike guard (lookalike_guard), the one L0
                                 check that decides outright: a sender domain
                                 borrowing a known mailbox org's name (acme ->
                                 acme-billing, other TLD, homoglyphs) or the
                                 display name's org plus filler words, writing
                                 about money / signing / credentials ->
                                 spam_phishing, suspicious, importance 0. Beats
                                 the model and learned rules; user rules win
    L1  learned preferences      prefs.json rules (v1 or v2) resolved by
                                 testbeds/email_triage/prefs.py: precedence
                                 user > subject/service/thread > address > domain
                                 > learned > model, include/exclude, candidates
                                 never applied, and the security-alert guard (a
                                 broad non-user demotion never buries a non-list
                                 security/account/payment alert); plus the
                                 cheap-error tie-break (resolve_rules). Only
                                 user rules and learned list/bulk demotions
                                 decide outright (may_decide_outright); other
                                 learned rules go to L3 as hints and are
                                 applied on top of the model's label
    L2  static rules             list mail with no protective signal -> bulk
                                 (social / promo / newsletter), importance 0
    L3  model label              the existing batched, schema-constrained call in
                                 email_triage_agent.py, plus an optional confidence
                                 signal from llama-server token probabilities;
                                 calibrated ABSTAIN (NONE, min confidence, dead-heat
                                 margin, bulk classes summed before comparing),
                                 then the phishing sanity guard (phish_downgrade:
                                 a "phishing" label on non-list mail from a domain
                                 the user legitimately gets mail from, with no
                                 look-alike or injection signal, becomes
                                 important_read; never undoes a look-alike hit)
                                 and the urgency cap (urgency_cap: importance 3
                                 on mail from an automated sender with no
                                 near-term deadline / failure / account-incident
                                 signal becomes 2; rules applied after can raise
                                 it again)

Fail toward the cheap error: an abstained item is never demoted (importance >= 1,
so it stays in the inbox) and is queued for the user. Hard guard: nobody the user
has written to, nobody on the user's own (non-free-mail) domain and no subdomain
of an important domain is ever given importance 0 (the archive disposition).

L0 and L2 decide nothing that could hide important mail: L0 only constrains
(needs_reply off for no-reply mail, suspicious on for assistant-directed text,
importance floor for protected senders), except the look-alike guard, which only
fires on unprotected senders on a domain the user does not correspond with; L2 only
decides list mail that no guard, rule or note protects.

This module never calls a model itself. ``run_cascade`` takes a ``model_batch``
callback (email_triage_agent.py supplies it), so tests can pass a fake.

Third-party code: the automated-sender regexes and the don't-reply list in the
"L0" section are ported from Zero (Mail-0/Zero, MIT, see the notice there and
docs/third_party/wp1.md). Everything else is AWOS code; the cascade/abstain design
follows the public descriptions of Inbox Zero, Superhuman and SaneBox (design
only, no code).
"""
from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple, Union

try:  # the v2 preference store is the single rule resolver for L1 and L3
    from testbeds.email_triage import prefs as prefs_store
except ImportError:  # pragma: no cover - run as a plain script outside the repo root
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from testbeds.email_triage import prefs as prefs_store

CATEGORIES = ["urgent_action", "important_read", "fyi", "newsletter", "promo",
              "social", "spam_phishing"]
BULK = ("newsletter", "promo", "social", "spam_phishing")
NONE_LABEL = "none"
DEFAULT_PRED = {"importance": 1, "category": "fyi", "needs_reply": False, "suspicious": False}

FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
             "yahoo.com", "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com",
             "fastmail.com", "gmx.com", "gmx.de", "aol.com", "mail.com", "zoho.com"}


def cascade_enabled() -> bool:
    return (os.environ.get("AWOS_TRIAGE_CASCADE", "1").strip().lower()
            not in ("0", "false", "no", "off", ""))


@dataclass
class Thresholds:
    """Abstain thresholds. min_conf / margin are Inbox Zero's published starting
    points (0.3 / 0.1). archive_conf is 0.96 since the calibration probe on the
    local model (Qwen3.5-9B, llama-server post-sampling probabilities): 137/150
    labels came out >= 0.95 at ~82% accuracy, i.e. the probabilities are
    overconfident, so only near-certain bulk is archived.

    Do NOT rely on min_conf / margin for abstention on the local model: with
    probabilities that peaked they almost never trigger. They stay (env
    overridable) for servers whose probabilities are calibrated; the abstain
    that matters locally is the archive gate. Re-tune all three with
    scripts/triage_calibration_probe.py when the model changes."""
    min_conf: float = 0.3      # top bucket below this -> abstain (rarely fires locally)
    margin: float = 0.1        # top - second below this -> dead heat -> abstain (ditto)
    archive_conf: float = 0.96  # summed bulk probability needed to demote to importance 0
    allow_none: bool = False   # offer NONE as a label (model may say "I can't tell")

    @classmethod
    def from_env(cls) -> "Thresholds":
        def f(name, d):
            try:
                return float(os.environ.get(name, d))
            except ValueError:
                return d
        return cls(min_conf=f("AWOS_TRIAGE_MIN_CONF", cls.min_conf),
                   margin=f("AWOS_TRIAGE_MARGIN", cls.margin),
                   archive_conf=f("AWOS_TRIAGE_ARCHIVE_CONF", cls.archive_conf),
                   allow_none=os.environ.get("AWOS_TRIAGE_NONE", "0").strip() in ("1", "true", "yes"))


# ── helpers ───────────────────────────────────────────────────────────────────

def _addr(m: dict) -> str:
    return (m.get("from_addr") or "").strip().lower()


def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def _site_label(dom: str) -> str:
    """'news.acme.example' -> 'acme' (second-level label; good enough for matching)."""
    parts = [p for p in dom.split(".") if p]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")


def _in_family(dom: str, roots: Iterable[str]) -> bool:
    return bool(dom) and any(dom == r or dom.endswith("." + r) for r in roots if r)


def is_list_mail(m: dict) -> bool:
    h = {k.lower() for k in (m.get("headers") or {})}
    return "list-unsubscribe" in h or "list-id" in h


# ── L0: deterministic guards ──────────────────────────────────────────────────
# Ported from Zero, apps/server/src/thread-workflow-utils/index.ts
# (https://github.com/Mail-0/Zero, commit 64c5480c341750578da0746f2db9ad84da686334):
#
#   MIT License
#   Copyright (c) 2025 Zero Email
#   Permission is hereby granted, free of charge, to any person obtaining a copy
#   of this software and associated documentation files (the "Software"), to deal
#   in the Software without restriction, including without limitation the rights
#   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#   copies of the Software, and to permit persons to whom the Software is
#   furnished to do so, subject to the following conditions:
#   The above copyright notice and this permission notice shall be included in all
#   copies or substantial portions of the Software.
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#   SOFTWARE.
ZERO_DONT_REPLY_TO = frozenset({
    "no-reply@gmail.com", "noreply@gmail.com", "do-not-reply@gmail.com",
    "notifications@github.com", "info@e-mail.xing.com",
})
ZERO_AUTOMATED_EMAIL_RE = re.compile(r"(no-?reply|donotreply|do-not-reply)")
ZERO_AUTOMATED_SUBJECT_RE = re.compile(r"(newsletter|unsubscribe|notification)")
ZERO_AUTOMATED_BODY_RE = re.compile(r"(do not reply|this is an automated)")
# ── end of Zero port ──

# AWOS: text aimed at an assistant/agent instead of the human reader.
INJECTION_RES = [re.compile(p, re.I | re.M) for p in (
    r"\bignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier) instructions\b",
    r"\b(?:note|notice|message|instructions?)\s+(?:to|for)\s+(?:the |any |all )?"
    r"(?:ai|assistants?|agents?|bots?|llms?)\b",
    r"\b(?:ai|llm)\s+(?:assistants?|agents?|models?)\b",
    r"^\s*(?:assistant|system)\s*:",
    r"\byou are now in\b[^.\n]{0,40}\bmode\b",
    r"\bdo not (?:mention|tell|show) (?:this|the user|anyone)\b",
)]

# AWOS static matchers for L2 (generic, not tuned to the testbed persona).
SOCIAL_SITES = {"linkedin", "x", "twitter", "facebook", "facebookmail", "instagram", "meetup",
                "discord", "reddit", "redditmail", "mastodon", "threads", "tiktok", "pinterest",
                "quora", "strava", "nextdoor", "bsky", "youtube"}
PROMO_RE = re.compile(
    r"(\d+\s*%\s*off|\bsale\b|\bdeals?\b|\bdiscount|\boffers?\b|\bfree\b|\bcoupon|\bsave\b|"
    r"\bearly[- ]bird|\btickets?\b|\bwebinar\b|\btrial\b|\bcart\b|\bupgrade\b|\bpremium\b|"
    r"\bblack friday\b|\blast chance\b|\blimited time\b|\bclaim\b|\benrol)", re.I)
NEWSLETTER_SUBJECT_RE = re.compile(r"(#\s?\d+|\bissue\b|\bdigest\b|\bedition\b|\bnewsletter\b|"
                                   r"\bweekly\b|\bbulletin\b)", re.I)
PROMO_LOCALPART_RE = re.compile(r"^(deals?|offers?|promos?|sales?|store|shop|marketing|growth|"
                                r"billing-offers|events?|tickets|learn)\b")


def looks_injected(m: dict) -> bool:
    text = (m.get("subject") or "") + "\n" + (m.get("body") or "")
    return any(r.search(text) for r in INJECTION_RES)


# AWOS: phishing sanity guard signals (L3 post-check, see phish_downgrade).
_MULTI_SUFFIX = {"co", "com", "org", "net", "ac", "gov", "edu", "ne", "or"}
URL_HOST_RE = re.compile(r"https?://([^/\s\"'<>()\[\]]+)", re.I)
ASSISTANT_WORDS_RE = re.compile(r"\b(assistants?|ai agents?|agents? processing|llms?|chatbots?)\b", re.I)


def registrable_domain(dom: str) -> str:
    """'news.nimbusnotes.example' -> 'nimbusnotes.example' (last two labels, three
    for co.uk-style suffixes). Heuristic; no public-suffix list offline."""
    parts = [p for p in (dom or "").lower().strip(".").split(".") if p]
    if len(parts) >= 3 and parts[-2] in _MULTI_SUFFIX and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def lookalike_signals(m: dict) -> List[str]:
    """Reasons this message may impersonate someone: punycode / non-ASCII sender,
    a display name naming a brand the sender domain does not carry, links to a
    domain other than the sender's."""
    addr = _addr(m)
    dom = _domain(addr)
    reg = registrable_domain(dom)
    out = []
    name = (m.get("from_name") or "").lower()
    if "xn--" in dom or not dom.isascii() or not name.isascii():
        out.append("punycode_or_non_ascii")
    site_flat = re.sub(r"[^a-z0-9]", "", reg.rsplit(".", 1)[0]) if reg else ""
    tokens = [t for t in re.findall(r"[a-z0-9]+", name) if len(t) >= 4]
    name_flat = re.sub(r"[^a-z0-9]", "", name)
    if tokens and site_flat and not (any(t in site_flat for t in tokens)
                                     or (len(site_flat) >= 4 and site_flat in name_flat)):
        out.append("display_name_mismatch")
    hosts = {registrable_domain(h.split(":")[0].split("@")[-1])
             for h in URL_HOST_RE.findall(m.get("body") or "")}
    if any(h and h != reg for h in hosts):
        out.append("link_domain_mismatch")
    return out


# ── L0: look-alike sender guard (AWOS) ────────────────────────────────────────
# A sender whose registrable domain borrows a known organisation's name without
# being that organisation's domain (acme -> acme-billing, docusign -> docusign-mail,
# paypal -> paypa1-secure, other TLD), writing about money, documents to sign or
# credentials. Generic: the known names come from the mailbox at run time (the
# user's own domain, people they write to or get personal mail from, subscribed
# lists) or from the sender's own display name; nothing here names a brand.

# words a look-alike domain bolts onto a borrowed name
LOOKALIKE_FILLER = frozenset("""
mail email mailer mails notice notices notification notifications notify alert alerts
billing bill bills pay payment payments payable invoice invoices finance accounts account
secure security safe verify verification validate auth login logon signin sso id identity
support help helpdesk service services care team desk center centre portal online web
app apps update updates info official partners partner hr it admin docs document documents
sign esign
""".split())
# labels too generic to stand for an organisation
GENERIC_LABELS = LOOKALIKE_FILLER | frozenset(
    "mailbox inbox news example group global home mobile cloud dev test corp company".split())
# topics a look-alike sender uses to extract money, signatures or credentials
RISK_TOPIC_RE = re.compile(
    r"\b(payments?|payouts?|pay\b|invoices?|remittance|bank (?:details|account)|banking details|"
    r"wire transfer|wire\b|iban|swift|routing number|sort code|payment release|"
    r"sign(?:ature)?\b|e-?sign|review and sign|passwords?|passcode|credentials?|"
    r"verify|verification|validate|re-?validate|confirm your (?:account|identity|details)|"
    r"log ?in|sign[- ]?in|account (?:has been |is |was )?(?:limited|suspended|locked|on hold|disabled)|"
    r"gift ?cards?|bitcoin|btc|crypto|wallet)", re.I)
_HOMOGLYPH = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
                            "9": "g", "$": "s", "@": "a"})
# local parts of people (not role / automated addresses), for the personal-mail anchors
ROLE_LOCAL_RE = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|notifications?|notify|alerts?|security|billing|"
    r"invoices?|receipts?|orders?|payments?|payouts?|statements?|accounts?|ap|ar|support|help|"
    r"info|team|hello|hi|news|newsletters?|updates?|digest|service|services|admin|mailer|"
    r"postmaster|marketing|deals?|offers?|promos?|events?|community|system|ops|it|hr|careers|"
    r"jobs|bounces?|feedback|contact|sales|store|shop|growth|learn|tickets|partners?|"
    r"tracking|calendar|reply|mail|office|finance)"
    r"([._+-].*)?$")


def _norm_label(s: str) -> str:
    """Lowercase, digit/symbol homoglyphs folded, 'rn' -> 'm', 'vv' -> 'w'."""
    return s.lower().translate(_HOMOGLYPH).replace("rn", "m").replace("vv", "w")


def _site_part(reg: str) -> str:
    """'acme-billing.example' -> 'acme-billing' (registrable domain minus its suffix)."""
    return (reg or "").split(".", 1)[0]


def _brand(reg: str) -> str:
    """The organisation name a registrable domain stands for ('' if too generic)."""
    b = re.sub(r"[^a-z]", "", _norm_label(_site_part(reg)))
    return "" if len(b) < 4 or b in GENERIC_LABELS else b


def _segments(label: str) -> List[str]:
    return [s for s in re.split(r"[-_.]+", _norm_label(label)) if s]


def _extends(label: str, brand: str) -> Optional[str]:
    """How ``label`` (a sender domain's site part) borrows ``brand``: other_tld,
    homoglyph, brand_plus_words or brand_plus_filler; None if it does not."""
    if not brand:
        return None
    segs = _segments(label)
    flat = re.sub(r"[^a-z]", "", "".join(segs))
    if flat == brand:
        return "other_tld" if label.lower() == brand else "homoglyph"
    if len(segs) > 1 and brand in segs:
        return "brand_plus_words"
    for s in segs:
        s = re.sub(r"[^a-z]", "", s)
        if s.startswith(brand) and s[len(brand):] in LOOKALIKE_FILLER:
            return "brand_plus_filler"
        if s.endswith(brand) and s[:-len(brand)] in LOOKALIKE_FILLER:
            return "brand_plus_filler"
    return None


def _name_brand_domain(name: str, label: str) -> Optional[str]:
    """Display name names an organisation and the domain is that name plus filler
    (DocuSign -> docusign-mail, PayPal -> paypa1-secure), but not the name itself."""
    tokens = {re.sub(r"[^a-z]", "", _norm_label(t)) for t in re.findall(r"[a-z0-9]+", name.lower())}
    tokens = {t for t in tokens if len(t) >= 4 and t not in GENERIC_LABELS}
    if not tokens:
        return None
    name_flat = re.sub(r"[^a-z]", "", _norm_label(name))
    segs = [re.sub(r"[^a-z]", "", s) for s in _segments(label)]
    segs = [s for s in segs if s]
    if not segs or "".join(segs) in tokens or "".join(segs) == name_flat:
        return None
    named = [s for s in segs if s in tokens or s == name_flat]
    filler = [s for s in segs if s in LOOKALIKE_FILLER]
    if len(segs) > 1 and named and filler and len(named) + len(filler) == len(segs):
        return "name_plus_filler"
    if len(segs) == 1:
        for t in tokens | {name_flat}:
            s = segs[0]
            if (s.startswith(t) and s[len(t):] in LOOKALIKE_FILLER) or \
                    (s.endswith(t) and s[:-len(t)] in LOOKALIKE_FILLER):
                return "name_plus_filler"
    return None


@dataclass
class Context:
    user_email: str = ""
    correspondents: set = field(default_factory=set)   # addresses the user has written to
    important_roots: set = field(default_factory=set)  # domains marked important by prefs
    notes_text: str = ""
    people_domains: set = field(default_factory=set)   # orgs whose people (non-list, not no-reply) write in
    # registrable domain -> ids of mailbox messages from it that look legitimate
    # (subscribed list mail or a correspondent, no injection, no look-alike signal)
    legit_domains: Dict[str, set] = field(default_factory=dict)
    # registrable domains whose name a look-alike would borrow: the user's own,
    # correspondents', legit_domains, and orgs whose people write personal mail
    anchor_domains: set = field(default_factory=set)

    @property
    def user_domain(self) -> str:
        d = _domain(self.user_email)
        return "" if d in FREE_MAIL else d


def risk_topic(m: dict) -> bool:
    text = ((m.get("subject") or "") + "\n" + (m.get("body") or "")).replace("_", " ")
    return bool(RISK_TOPIC_RE.search(text))


def lookalike_guard(m: dict, ctx: Context, g: Optional[Dict[str, bool]] = None) -> Optional[str]:
    """The L0 look-alike verdict for ``m`` (a reason string) or None. Fires when the
    sender's registrable domain (a) borrows the name of a known-legit mailbox
    domain without being it, or (b) is the display name's organisation plus filler
    words, AND the message is about money / signing / credentials. Never fires on
    the user's own domain, a correspondent, a subdomain of a known domain, or a
    domain the user corresponds with (list mail does not vouch: its headers are
    free to fake)."""
    g = g if g is not None else guards(m, ctx)
    if g.get("protected"):
        return None
    dom = _domain(_addr(m))
    reg = registrable_domain(dom)
    if not reg or reg in FREE_MAIL:
        return None
    # only correspondence vouches for a borrowed-name domain; list headers are
    # free for a phisher to add, so subscribed-list mail does not exempt it
    strict = {registrable_domain(_domain(a)) for a in ctx.correspondents}
    if ctx.user_domain:
        strict.add(registrable_domain(ctx.user_domain))
    if reg in strict:
        return None
    if not risk_topic(m):
        return None
    label = _site_part(reg)
    for anchor in sorted(ctx.anchor_domains):
        if anchor == reg or anchor in FREE_MAIL:
            continue
        how = _extends(label, _brand(anchor))
        if how:
            return f"lookalike_of:{anchor}:{how}"
    how = _name_brand_domain(m.get("from_name") or "", label)
    if how:
        return f"display_name_brand:{how}"
    return None


LOOKALIKE_PRED = {"importance": 0, "category": "spam_phishing", "needs_reply": False,
                  "suspicious": True}


def _rule_domains(rule: dict) -> List[str]:
    w = rule.get("when") or {}
    out = []
    if w.get("from_domain"):
        out.append(str(w["from_domain"]).lower())
    out += [str(d).lower() for d in (w.get("from_domain_any") or [])]
    if w.get("from_address"):
        out.append(_domain(str(w["from_address"]).lower()))
    return [d for d in out if d]


def _rule_is_important(rule: dict) -> bool:
    s = rule.get("set") or {}
    return (int(s.get("importance", -1)) >= 2 or int(s.get("min_importance", -1)) >= 2
            or s.get("category") in ("urgent_action", "important_read"))


def load_correspondents(mailbox_dir: Optional[Path], messages: List[dict], user_email: str) -> set:
    """Addresses the user has written to: To/Cc of the user's own messages in the
    inbox, plus optional ``sent.json`` (list of {to, cc}) / ``contacts.json``
    (list of addresses or {address}) next to inbox.json. Missing files = none."""
    import json  # noqa: PLC0415
    me = (user_email or "").lower()
    out = set()

    def addrs(v) -> List[str]:
        vals = v if isinstance(v, list) else [v]
        res = []
        for x in vals:
            if isinstance(x, dict):
                x = x.get("address") or x.get("email") or ""
            for a in re.findall(r"[\w.+'-]+@[\w.-]+", str(x or "")):
                res.append(a.lower())
        return res

    for m in messages:
        if me and _addr(m) == me:
            out.update(addrs(m.get("to")) + addrs(m.get("cc")))
    if mailbox_dir:
        for name in ("sent.json", "contacts.json"):
            p = Path(mailbox_dir) / name
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text())
            except ValueError:
                continue
            rows = data.get("messages", data.get("contacts", [])) if isinstance(data, dict) else data
            for r in rows or []:
                if isinstance(r, dict) and ("to" in r or "cc" in r):
                    out.update(addrs(r.get("to")) + addrs(r.get("cc")))
                else:
                    out.update(addrs(r))
    out.discard(me)
    return out


def build_context(profile: dict, prefs: dict, messages: List[dict],
                  mailbox_dir: Optional[Path] = None) -> Context:
    user_email = (profile.get("email") or "").lower()
    roots = set()
    for r in prefs.get("rules", []) or []:
        if (prefs_store.rule_status(r) == "active" and prefs_store.rule_effect(r) == "include"
                and _rule_is_important(r)):
            roots.update(_rule_domains(r))
    roots -= FREE_MAIL
    notes = " ".join(str(n) for n in (prefs.get("notes") or []))
    notes += " " + " ".join(str(r.get("note", "")) for r in prefs.get("rules", []) or []
                            if r.get("source") == "user")
    people = set()
    for m in messages:
        a = _addr(m)
        if a and not is_list_mail(m) and not ZERO_AUTOMATED_EMAIL_RE.search(a) and a != user_email:
            people.add(_domain(a))
    corr = load_correspondents(mailbox_dir, messages, user_email)
    legit: Dict[str, set] = {}
    for m in messages:
        a = _addr(m)
        reg = registrable_domain(_domain(a))
        if (not reg or reg in FREE_MAIL or a == user_email or looks_injected(m)
                or lookalike_signals(m) or not (is_list_mail(m) or a in corr)):
            continue
        legit.setdefault(reg, set()).add(str(m.get("id")))
    anchors = set(legit) | {registrable_domain(_domain(a)) for a in corr}
    if user_email and _domain(user_email) not in FREE_MAIL:
        anchors.add(registrable_domain(_domain(user_email)))
    for m in messages:  # orgs whose people write personal (non-list, non-role) mail
        a = _addr(m)
        local = a.split("@", 1)[0]
        if (not a or a == user_email or is_list_mail(m) or ROLE_LOCAL_RE.match(local)
                or ZERO_AUTOMATED_EMAIL_RE.search(a) or looks_injected(m) or lookalike_signals(m)):
            continue
        anchors.add(registrable_domain(_domain(a)))
    anchors = {d for d in anchors if d and d not in FREE_MAIL}
    return Context(user_email=user_email, people_domains=people - FREE_MAIL,
                   correspondents=corr, important_roots=roots, notes_text=notes.lower(),
                   legit_domains=legit, anchor_domains=anchors)


def guards(m: dict, ctx: Context) -> Dict[str, bool]:
    addr = _addr(m)
    dom = _domain(addr)
    subj = (m.get("subject") or "").lower()
    body = (m.get("body") or "").lower()
    corr_domains = {_domain(a) for a in ctx.correspondents} - FREE_MAIL
    name = (m.get("from_name") or "").strip().lower()
    site = _site_label(dom)
    notes_flat = ctx.notes_text.replace(" ", "")
    g = {
        "correspondent": addr in ctx.correspondents,
        "same_domain": bool(ctx.user_domain) and dom == ctx.user_domain,
        "family": (_in_family(dom, ctx.important_roots) or _in_family(dom, corr_domains)
                   or (bool(ctx.user_domain) and dom.endswith("." + ctx.user_domain))),
        "no_reply": (addr in ZERO_DONT_REPLY_TO or bool(ZERO_AUTOMATED_EMAIL_RE.search(addr))
                     or bool(ZERO_AUTOMATED_BODY_RE.search(body))),
        "automated": bool(ZERO_AUTOMATED_SUBJECT_RE.search(subj)),
        "list": is_list_mail(m),
        "injection": looks_injected(m),
        "mentioned": bool(ctx.notes_text) and ((len(name) >= 4 and name in ctx.notes_text)
                                               or (len(site) >= 4 and site in notes_flat)),
    }
    # list mail from a subdomain of an org whose people write to the user (news.acme
    # when dana@acme writes): not a hard guard, but static rules must not bury it.
    g["org_list"] = g["list"] and any(dom.endswith("." + d) for d in ctx.people_domains if d)
    g["protected"] = g["correspondent"] or g["same_domain"] or g["family"]
    return g


# ── L1: learned preferences with precedence ───────────────────────────────────

SENDER_KEYS = ("from_address", "from_domain", "from_domain_any")


def rule_tier(rule: dict) -> int:
    """Higher wins. 4 user-written > 3 explicit subject > 2 domain/sender >
    1 other learned (text/body/name words, agent-proposed) > 0 model."""
    if rule.get("source") == "user":
        return 4
    if rule.get("source") == "agent":
        return 1
    w = rule.get("when") or {}
    if w.get("subject_any"):
        return 3
    if any(w.get(k) for k in SENDER_KEYS):
        return 2
    return 1


def _raises(rule: dict) -> bool:
    s = rule.get("set") or {}
    return (int(s.get("importance", -1)) >= 2 or int(s.get("min_importance", -1)) >= 2
            or s.get("category") in ("urgent_action", "important_read"))


def _demotes(rule: dict) -> bool:
    s = rule.get("set") or {}
    return (s.get("importance") == 0 or s.get("max_importance") == 0
            or (s.get("category") in BULK and s.get("importance", 0) == 0))


def ranked_rules(rules: List[dict], m: Optional[dict] = None) -> List[dict]:
    """Ascending precedence (stable: file order inside a tier, later overrides).
    Cheap-error tie-break: when ``m`` is given and its matching non-user rules
    disagree (one raises importance to >= 2, another demotes to 0), the raising
    ones are applied last, so a learned subject-keyword rule can never bury a
    sender the user marked important. User-written rules always stay on top."""
    rs = sorted(rules or [], key=rule_tier)
    if m is None:
        return rs
    hit = [r for r in rs if r.get("source") != "user" and rule_matches(r.get("when") or {}, m)]
    if any(_raises(r) for r in hit) and any(_demotes(r) for r in hit):
        rs = sorted(rs, key=lambda r: (r.get("source") == "user",
                                       r.get("source") != "user" and _raises(r)
                                       and rule_matches(r.get("when") or {}, m), rule_tier(r)))
    return rs


def rule_matches(when: dict, m: dict) -> bool:
    """testbeds/email_triage/prefs.py semantics (every key given must match, incl.
    is_list / body_any / from_domain_not_any / thread_id); an empty ``when`` never
    matches."""
    return bool(when) and prefs_store.matches(when, m)


PrefsLike = Union[dict, List[dict], None]


def as_doc(prefs: PrefsLike) -> dict:
    """A prefs document from a doc (v1/v2) or a bare rule list."""
    if isinstance(prefs, dict):
        return prefs
    return {"version": 1, "notes": [], "rules": list(prefs or [])}


def live_rules(prefs: PrefsLike, m: dict) -> List[dict]:
    """Active include rules that match ``m`` and that the safety guard lets through."""
    return [r for r in (as_doc(prefs).get("rules") or [])
            if prefs_store.rule_status(r) == "active" and prefs_store.rule_effect(r) == "include"
            and rule_matches(r.get("when") or {}, m) and not prefs_store.guard_blocks(r, m)]


def resolve_rules(pred: dict, m: dict, prefs: PrefsLike,
                  record_hits: bool = False) -> Tuple[dict, List[dict]]:
    """``prefs.resolve`` plus the cheap-error tie-break: when live non-user rules
    disagree (one raises importance to >= 2, another demotes to 0) and no user rule
    sets the outcome, the raising rules are re-applied on top, so a learned subject
    keyword can never bury a sender marked important. Returns (pred, trace)."""
    doc = as_doc(prefs)
    out, trace = prefs_store.resolve(pred, m, doc, record_hits=record_hits)
    live = live_rules(doc, m)
    nonuser = [r for r in live if prefs_store.rule_source(r) != "user"]
    user_sets = any(prefs_store.rule_source(r) == "user"
                    and {"importance", "max_importance", "category"} & set(r.get("set") or {})
                    for r in live)
    raisers = [r for r in nonuser if _raises(r)]
    if raisers and not user_sets and any(_demotes(r) for r in nonuser):
        order = {id(r): i for i, r in enumerate(doc.get("rules") or [])}
        for r in sorted(raisers, key=lambda r: prefs_store.precedence(r, order.get(id(r), 0))):
            st = r.get("set") or {}
            for k, v in st.items():
                if k == "importance":
                    out["importance"] = v
                elif k == "min_importance":
                    out["importance"] = max(int(out.get("importance", 1)), v)
                elif k == "max_importance":
                    out["importance"] = min(int(out.get("importance", 1)), v)
                else:
                    out[k] = v
            trace.append({"rule": r.get("id"), "action": "cheap_error_raise", "fields": sorted(st)})
    return out, trace


def _trace_fixed(trace: List[dict]) -> Tuple[set, List[str]]:
    """Fields fixed exactly by a rule (min/max bounds alone leave importance to the
    model) and the ids of the rules that decided something."""
    fixed, hit = set(), []
    for t in trace:
        if t["action"] in ("applied", "cheap_error_raise"):
            fixed.update(k for k in t["fields"]
                         if k in ("importance", "category", "needs_reply", "suspicious"))
        if t["action"] in ("applied", "cheap_error_raise", "moved_off_model") \
                and str(t["rule"]) not in hit:
            hit.append(str(t["rule"]))
    return fixed, hit


def apply_ranked(pred: dict, m: dict, rules: List[dict]) -> Tuple[dict, set, List[str]]:
    """Legacy tier-based resolver (rule_tier / ranked_rules), kept for comparison;
    run_cascade resolves through ``resolve_rules`` (prefs.py) instead.
    Apply matching rules in precedence order. Returns (pred, fields fixed
    exactly by a rule, matched rule ids). ``importance`` counts as fixed only when
    an exact ``importance`` was set (min/max bounds alone leave it to the model)."""
    p = dict(pred)
    fixed, hit = set(), []
    for r in ranked_rules(rules, m):
        if not rule_matches(r.get("when") or {}, m):
            continue
        hit.append(str(r.get("id", "?")))
        s = r.get("set") or {}
        if "importance" in s:
            p["importance"] = int(s["importance"])
            fixed.add("importance")
        if "min_importance" in s:
            p["importance"] = max(int(p.get("importance", 1)), int(s["min_importance"]))
        if "max_importance" in s:
            p["importance"] = min(int(p.get("importance", 1)), int(s["max_importance"]))
        for k in ("category", "needs_reply", "suspicious"):
            if k in s:
                p[k] = s[k]
                fixed.add(k)
    return p, fixed, hit


def may_decide_outright(rule: dict, m: dict) -> bool:
    """Can this rule settle ``m`` without the model? User-written rules can. A
    learned (non-user) rule only when it files list mail as bulk (newsletter /
    promo / social at importance <= 1); any other learned rule goes to L3 as a
    hint and is applied on top of the model's label there."""
    if prefs_store.rule_source(rule) == "user":
        return True
    if prefs_store.rule_effect(rule) == "exclude":
        return True                       # constrains the model's value; decides nothing alone
    st = rule.get("set") or {}
    try:
        return (is_list_mail(m) and st.get("category") in ("newsletter", "promo", "social")
                and int(st.get("importance", 0)) <= 1 and int(st.get("min_importance", 0)) <= 1
                and not st.get("needs_reply"))
    except (TypeError, ValueError):
        return False


def l1_resolve(m: dict, prefs: PrefsLike) -> Tuple[Optional[dict], List[str], List[dict]]:
    """A complete decision from rules alone (else None), the deciding rule ids and
    the resolver trace. Complete = importance and category fixed, and needs_reply
    fixed (or implied False by a bulk category), and every deciding rule may
    decide outright (``may_decide_outright``)."""
    p, trace = resolve_rules(dict(DEFAULT_PRED), m, prefs)
    fixed, hit = _trace_fixed(trace)
    if not hit or not {"importance", "category"} <= fixed:
        return None, hit, trace
    deciders = {str(t["rule"]) for t in trace if t["action"] in ("applied", "cheap_error_raise")}
    live = live_rules(prefs, m)
    if any(not may_decide_outright(r, m) for r in live if str(r.get("id")) in deciders):
        return None, hit, trace
    if "needs_reply" not in fixed:
        if p["category"] not in BULK:
            return None, hit, trace
        p["needs_reply"] = False
    return p, hit, trace


def l1_decide(m: dict, rules: PrefsLike) -> Tuple[Optional[dict], List[str]]:
    """``l1_resolve`` without the trace (rules: a prefs doc or a bare rule list)."""
    p, hit, _ = l1_resolve(m, rules)
    return p, hit


def raised_by_rules(m: dict, rules: PrefsLike) -> bool:
    """Does any live matching rule push importance above 0 or into a non-bulk class?"""
    for r in live_rules(rules, m):
        s = r.get("set") or {}
        if (int(s.get("importance", 0)) >= 1 or int(s.get("min_importance", 0)) >= 1
                or (s.get("category") and s["category"] not in BULK)):
            return True
    return False


# ── L2: static rules ──────────────────────────────────────────────────────────

def l2_decide(m: dict, g: Dict[str, bool], rules: List[dict]) -> Optional[dict]:
    """List mail that nothing protects -> bulk, importance 0."""
    if (not g["list"] or g["protected"] or g["injection"] or g["mentioned"]
            or g.get("org_list")):
        return None
    if raised_by_rules(m, rules):
        return None
    addr = _addr(m)
    dom = _domain(addr)
    local = addr.split("@", 1)[0]
    text = (m.get("subject") or "") + "\n" + (m.get("body") or "")[:600]
    if _site_label(dom) in SOCIAL_SITES or (dom.split(".")[0] in SOCIAL_SITES):
        cat = "social"
    elif NEWSLETTER_SUBJECT_RE.search(m.get("subject") or ""):
        cat = "newsletter"
    elif PROMO_LOCALPART_RE.search(local) or PROMO_RE.search(text):
        cat = "promo"
    else:
        cat = "newsletter"
    return {"importance": 0, "category": cat, "needs_reply": False, "suspicious": False}


# ── L3: confidence from llama-server token probabilities ──────────────────────

def normalize_token_probs(data: dict) -> Optional[List[Tuple[str, List[Tuple[str, float]]]]]:
    """llama-server response -> [(token_text, [(alt_text, prob), ...]), ...].
    Accepts the OpenAI-compatible ``choices[0].logprobs.content`` and the native
    ``completion_probabilities``; fields may be ``logprob``/``top_logprobs`` or,
    with ``post_sampling_probs``, ``prob``/``top_probs``. None if absent."""
    rows = None
    try:
        lp = (data.get("choices") or [{}])[0].get("logprobs")
        if isinstance(lp, dict):
            rows = lp.get("content")
    except (AttributeError, IndexError, TypeError):
        rows = None
    if not rows:
        rows = data.get("completion_probabilities")
    if not rows or not isinstance(rows, list):
        return None

    def prob(e: dict) -> float:
        if "prob" in e:
            return float(e["prob"])
        if "logprob" in e:
            return math.exp(float(e["logprob"]))
        return 0.0

    out = []
    for e in rows:
        if not isinstance(e, dict):
            continue
        tok = e.get("token", e.get("content", ""))
        alts = e.get("top_probs") or e.get("top_logprobs") or e.get("probs") or []
        alt_list = [(a.get("token", a.get("tok_str", "")), prob(a)) for a in alts if isinstance(a, dict)]
        if not alt_list:
            alt_list = [(tok, prob(e))]
        out.append((tok, alt_list))
    return out


def map_fragment(frag: str, labels: List[str]) -> List[str]:
    """Labels a decoded fragment could be the start of (or that it spells fully)."""
    f = frag.strip().strip('"').lower()
    if not f:
        return []
    return [L for L in labels if L.startswith(f) or f.startswith(L)]


def dist_at(tokens, idx: int, pre: str, labels: List[str]) -> Optional[Dict[str, float]]:
    """Label distribution from the alternatives at token ``idx``. ``pre`` is the part
    of the chosen token that precedes the label (e.g. ' "'); alternatives must
    share it. Mass on fragments matching several labels is split evenly (fine for
    the bulk sum: social/spam both share 's'). Renormalised over label mass."""
    acc = {L: 0.0 for L in labels}
    total = 0.0
    for alt, p in tokens[idx][1]:
        if pre and not alt.startswith(pre):
            continue  # a different prefix (or ends before the label): no label evidence
        cands = map_fragment(alt[len(pre):], labels)
        if not cands:
            continue
        for L in cands:
            acc[L] += p / len(cands)
        total += p
    if total <= 0:
        return None
    return {L: v / total for L, v in acc.items()}


def category_dists(tokens, labels: Optional[List[str]] = None,
                   key: str = "category") -> List[Optional[Dict[str, float]]]:
    """One distribution per ``"<key>": "<value>"`` occurrence in the generated JSON,
    in order (the k-th one belongs to items[k])."""
    labels = labels or CATEGORIES
    text, starts = "", []
    for tok, _ in tokens:
        starts.append(len(text))
        text += tok
    out = []
    for mt in re.finditer(r'"%s"\s*:\s*"' % re.escape(key), text):
        vstart = mt.end()
        idx = None
        for i, s in enumerate(starts):
            if s <= vstart < s + len(tokens[i][0]):
                idx = i
                break
        if idx is None:
            out.append(None)
            continue
        pre = text[starts[idx]:vstart]
        out.append(dist_at(tokens, idx, pre, labels))
    return out


def buckets(dist: Dict[str, float]) -> Dict[str, float]:
    """Sum the bulk classes into one bucket before comparing with important ones."""
    b = {"bulk": sum(dist.get(c, 0.0) for c in BULK)}
    for c in dist:
        if c not in BULK:
            b[c] = dist[c]
    return b


def abstain_decision(dist: Optional[Dict[str, float]], th: Thresholds) -> dict:
    """Returns {abstain, demote_ok, top, conf, margin, reason}. No distribution ->
    no abstain (accept the model label as before) but demotion is still allowed."""
    if not dist:
        return {"abstain": False, "demote_ok": True, "top": None, "conf": None,
                "margin": None, "reason": "no_probs"}
    b = sorted(buckets(dist).items(), key=lambda kv: -kv[1])
    top, conf = b[0]
    second = b[1][1] if len(b) > 1 else 0.0
    margin = conf - second
    res = {"top": top, "conf": round(conf, 4), "margin": round(margin, 4),
           "p_bulk": round(buckets(dist)["bulk"], 4)}
    if top == NONE_LABEL:
        return {**res, "abstain": True, "demote_ok": False, "reason": "none"}
    if conf < th.min_conf:
        return {**res, "abstain": True, "demote_ok": False, "reason": "low_conf"}
    if margin < th.margin:
        return {**res, "abstain": True, "demote_ok": False, "reason": "dead_heat"}
    if top == "bulk" and conf < th.archive_conf:
        # the model leans bulk but not enough to archive: keep it, ask the user
        return {**res, "abstain": True, "demote_ok": False, "reason": "below_archive_conf"}
    return {**res, "abstain": False, "demote_ok": top == "bulk", "reason": "ok"}


# ── phishing sanity guard (after the model) ───────────────────────────────────

def phish_downgrade(p: dict, m: dict, g: Dict[str, bool], ctx: Context) -> Optional[str]:
    """If the model called ``m`` phishing/suspicious but its sender's exact
    registrable domain is one the user legitimately receives other mail from (and
    nothing about ``m`` looks like impersonation or an injection), relabel it
    important_read / not suspicious in place; importance >= 2 when it reads like a
    security/account alert. Returns the reason, or None (unchanged)."""
    if not (p.get("category") == "spam_phishing" or p.get("suspicious")):
        return None
    if g.get("injection") or g.get("list"):
        return None
    if lookalike_guard(m, ctx, g):
        return None  # the rescue never undoes a look-alike verdict
    text = (m.get("subject") or "") + "\n" + (m.get("body") or "")
    if ASSISTANT_WORDS_RE.search(text) or lookalike_signals(m):
        return None
    reg = registrable_domain(_domain(_addr(m)))
    others = ctx.legit_domains.get(reg, set()) - {str(m.get("id"))}
    if not reg or reg in FREE_MAIL or not others:
        return None
    p["category"] = "important_read"
    p["suspicious"] = False
    try:
        imp = int(p.get("importance", 1))
    except (TypeError, ValueError):
        imp = 1
    p["importance"] = max(imp, 2 if prefs_store.is_protected(m) else 1)
    return "known_sender_domain"


# ── urgency cap for automated senders (after the model, before rules) ─────────
# A local model over-escalates machine mail ("scheduled retirement" weeks away,
# a dependency advisory, "payout on the way") to 3. Automated senders keep 3 only
# when the text shows a near-term deadline, a failure, or a security incident on
# the user's own account; otherwise 3 -> 2. Rules are applied afterwards, so a
# user or learned rule can still raise the message back to 3.

AUTOMATED_LOCAL_RE = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|notifications?|notify|notifier|alerts?|updates?|"
    r"mailer|automated|auto|bounces?|digest|[\w.-]*-notifications?)([._+-].*)?$")
FAILURE_RE = re.compile(
    r"\b(failed|failure|failing|declined|rejected|bounced|past due|overdue|unpaid|suspended|"
    r"suspension|locked|disabled|will stop|stopped working|outage|is down|went down|"
    r"could not (?:charge|renew|process|deliver)|unable to (?:charge|renew|process))\b", re.I)
INCIDENT_RE = re.compile(
    r"(\bpassword (?:was|has been) (?:changed|reset)\b|\bnew (?:ssh|gpg|api) key\b|"
    r"\bkey was added\b|\badded to your account\b|\bsecret\b[^.\n]{0,60}\b(?:detected|exposed|leaked)\b|"
    r"\b(?:leaked|exposed|compromised|unauthori[sz]ed)\b|"
    r"\bif (?:this|it) was(?:n't| not) you\b|\bif you did(?:n't| not) (?:add|make|do|request)\b)", re.I)
DEADLINE_RE = re.compile(
    r"(\b(?:today|tonight|tomorrow|end of (?:the )?day|eod|asap|immediately|right away)\b|"
    r"\bwithin (?:the next )?(?:\d{1,2}|twenty-four|forty-eight) hours?\b|"
    r"\bin (?:\d{1,2}) hours?\b|\b(?:in|within) (?:1|one|2|two) (?:business )?days?\b)", re.I)
ISO_DATE_RE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")


def is_automated_sender(m: dict, g: Optional[Dict[str, bool]] = None) -> bool:
    local = _addr(m).split("@", 1)[0]
    return bool((g or {}).get("no_reply")) or bool(AUTOMATED_LOCAL_RE.match(local))


def _near_date(m: dict, text: str, days: int = 2) -> bool:
    """An ISO date in the text within ``days`` of the message's own date."""
    from datetime import date  # noqa: PLC0415
    ms = ISO_DATE_RE.match((m.get("date") or "")[:10])
    if not ms:
        return False
    try:
        d0 = date(*map(int, ms.groups()))
    except ValueError:
        return False
    for y, mo, d in ISO_DATE_RE.findall(text):
        try:
            delta = (date(int(y), int(mo), int(d)) - d0).days
        except ValueError:
            continue
        if 0 <= delta <= days:
            return True
    return False


def near_term_signal(m: dict) -> Optional[str]:
    """Why an automated message may be urgent: failure / account incident /
    near-term deadline; None if the text shows none."""
    text = (m.get("subject") or "") + "\n" + (m.get("body") or "")
    if FAILURE_RE.search(text):
        return "failure"
    if INCIDENT_RE.search(text):
        return "account_incident"
    if DEADLINE_RE.search(text) or _near_date(m, text):
        return "deadline"
    return None


def urgency_cap(p: dict, m: dict, g: Dict[str, bool]) -> Optional[str]:
    """3 -> 2 (urgent_action -> important_read) on a model label for an automated
    sender with no near-term signal; and the mirror case, 2 -> 3 for a non-list,
    non-suspicious automated message reporting a security event on the user's own
    account (the strict urgency prompt makes the model under-call these). Returns
    the reason, or None (unchanged)."""
    try:
        imp = int(p.get("importance", 1))
    except (TypeError, ValueError):
        return None
    if g.get("correspondent") or not is_automated_sender(m, g):
        return None
    if imp == 2:
        if (g.get("list") or g.get("injection") or p.get("suspicious")
                or p.get("category") == "spam_phishing"):
            return None
        text = (m.get("subject") or "") + "\n" + (m.get("body") or "")
        if not INCIDENT_RE.search(text):
            return None
        p["importance"] = 3
        p["category"] = "urgent_action"
        return "automated_account_incident"
    if imp < 3:
        return None
    if near_term_signal(m):
        return None
    p["importance"] = 2
    if p.get("category") == "urgent_action":
        p["category"] = "important_read"
    return "automated_no_near_term_signal"


# ── final guard + driver ──────────────────────────────────────────────────────

def finalize(p: dict, g: Dict[str, bool]) -> dict:
    """L0 constraints win over everything (including rules and the model)."""
    p = {k: p.get(k, DEFAULT_PRED[k]) for k in DEFAULT_PRED}
    if p.get("category") not in CATEGORIES:
        p["category"] = "fyi"
    try:
        p["importance"] = max(0, min(3, int(p["importance"])))
    except (TypeError, ValueError):
        p["importance"] = 1
    if g.get("no_reply") or g.get("list"):
        p["needs_reply"] = False
    if g.get("injection"):
        p["suspicious"] = True
    if g.get("protected") and p["importance"] < 1:
        p["importance"] = 1  # never auto-archive correspondents / own domain / family
    return p


def user_rules_doc(prefs: PrefsLike) -> dict:
    """The prefs document with only the user-written rules (they outrank L0)."""
    doc = as_doc(prefs)
    return {**doc, "rules": [r for r in (doc.get("rules") or [])
                             if prefs_store.rule_source(r) == "user"]}


ModelBatch = Callable[[List[dict]], Tuple[Dict[str, dict], Dict[str, Optional[Dict[str, float]]]]]


def run_cascade(messages: List[dict], prefs: dict, ctx: Context, model_batch: ModelBatch,
                batch_size: int = 5, th: Optional[Thresholds] = None,
                log: Optional[Callable[[str], None]] = None,
                record_hits: bool = False) -> Tuple[List[dict], dict]:
    """Triage ``messages``. ``model_batch(batch) -> (preds_by_id, dists_by_id)``;
    preds are raw model labels (rules are applied here, through the prefs.py
    resolver), dists may be empty. ``record_hits`` bumps ``hits``/``last_hit`` on
    the rules that decided each final label (``prefs`` is mutated).
    Returns (items in input order, trace)."""
    th = th or Thresholds.from_env()
    rules = as_doc(prefs)
    decided: Dict[str, dict] = {}
    trace_items: Dict[str, dict] = {}
    pending: List[dict] = []
    gmap = {}
    for m in messages:
        g = guards(m, ctx)
        gmap[m["id"]] = g
        tr = {"guards": sorted(k for k, v in g.items() if v)}
        lk = lookalike_guard(m, ctx, g)
        if lk:
            # overrides the model and every learned rule; user-written rules still win
            p, ptrace = prefs_store.resolve(dict(LOOKALIKE_PRED), m, user_rules_doc(rules),
                                            record_hits=record_hits)
            decided[m["id"]] = finalize(p, g)
            trace_items[m["id"]] = {**tr, "layer": "L0", "lookalike": lk,
                                    "rules": _trace_fixed(ptrace)[1], "prefs_trace": ptrace}
            continue
        p, hit, ptrace = (None, [], [])
        if not g["injection"]:
            p, hit, ptrace = l1_resolve(m, rules)
        if p is not None:
            if record_hits:
                resolve_rules(dict(DEFAULT_PRED), m, rules, record_hits=True)
            decided[m["id"]] = finalize(p, g)
            trace_items[m["id"]] = {**tr, "layer": "L1", "rules": hit, "prefs_trace": ptrace}
            continue
        p2 = l2_decide(m, g, rules)
        if p2 is not None:
            decided[m["id"]] = finalize(p2, g)
            trace_items[m["id"]] = {**tr, "layer": "L2"}
            continue
        trace_items[m["id"]] = tr
        pending.append(m)

    calls = 0
    for i in range(0, len(pending), batch_size):
        batch = pending[i:i + batch_size]
        preds, dists = model_batch(batch)
        calls += 1
        for m in batch:
            g = gmap[m["id"]]
            raw = preds.get(m["id"])
            p = dict(raw) if raw else dict(DEFAULT_PRED)
            p = {k: p.get(k, DEFAULT_PRED[k]) for k in DEFAULT_PRED}
            model_none = p.get("category") == NONE_LABEL
            if model_none:
                p["category"] = "fyi"
            ab = abstain_decision((dists or {}).get(m["id"]), th)
            if model_none:
                ab = {**ab, "abstain": True, "demote_ok": False, "reason": "none"}
            if raw is None:
                ab = {**ab, "abstain": True, "demote_ok": False, "reason": "no_answer"}
            phish = phish_downgrade(p, m, g, ctx) if raw is not None else None
            if phish:
                trace_items[m["id"]]["phish_guard"] = phish
            cap = urgency_cap(p, m, g) if raw is not None else None
            if cap:
                trace_items[m["id"]]["urgency_cap"] = cap
            p, ptrace = resolve_rules(p, m, rules, record_hits=record_hits)
            _, hit = _trace_fixed(ptrace)
            applied = {str(t["rule"]) for t in ptrace if t["action"] == "applied"}
            demoted_by_rule = any(str(r.get("id")) in applied
                                  and ((r.get("set") or {}).get("importance") == 0
                                       or (r.get("set") or {}).get("max_importance") == 0)
                                  for r in live_rules(rules, m))
            if (ab["abstain"] or not ab["demote_ok"]) and not demoted_by_rule:
                p["importance"] = max(int(p.get("importance", 1) or 0), 1)
            decided[m["id"]] = finalize(p, g)
            trace_items[m["id"]].update({"layer": "L3", "rules": hit, "prefs_trace": ptrace,
                                         "abstain": ab["abstain"],
                                         "abstain_reason": ab["reason"], "conf": ab.get("conf"),
                                         "margin": ab.get("margin"), "top": ab.get("top")})
        if log:
            log(f"[cascade] model batch {i // batch_size + 1}: {len(batch)} item(s)")

    items = [{"id": m["id"], **decided[m["id"]]} for m in messages]
    layers = {"L0": 0, "L1": 0, "L2": 0, "L3": 0}
    for t in trace_items.values():
        layers[t["layer"]] += 1
    queue = [mid for mid, t in trace_items.items() if t.get("abstain")]
    for it in items:
        trace_items[it["id"]]["disposition"] = "archive" if it["importance"] == 0 else "inbox"
    trace = {"enabled": True, "layers": layers, "model_calls_triage": calls,
             "abstained": len(queue), "review_queue": queue,
             "thresholds": {"min_conf": th.min_conf, "margin": th.margin,
                            "archive_conf": th.archive_conf, "allow_none": th.allow_none},
             "items": trace_items}
    return items, trace

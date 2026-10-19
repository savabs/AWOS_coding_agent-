"""
triage_cascade.py — layered email triage that stops at the first confident layer.

    L0  deterministic guards     correspondent / same domain / protected family,
                                 no-reply, list headers, automated sender,
                                 assistant-directed text (injection)
    L1  learned preferences      prefs.json rules, ranked by precedence
                                 user > explicit subject > domain > learned > model
    L2  static rules             list mail with no protective signal -> bulk
                                 (social / promo / newsletter), importance 0
    L3  model label              the existing batched, schema-constrained call in
                                 email_triage_agent.py, plus an optional confidence
                                 signal from llama-server token probabilities;
                                 calibrated ABSTAIN (NONE, min confidence, dead-heat
                                 margin, bulk classes summed before comparing)

Fail toward the cheap error: an abstained item is never demoted (importance >= 1,
so it stays in the inbox) and is queued for the user. Hard guard: nobody the user
has written to, nobody on the user's own (non-free-mail) domain and no subdomain
of an important domain is ever given importance 0 (the archive disposition).

L0 and L2 decide nothing that could hide important mail: L0 only constrains
(needs_reply off for no-reply mail, suspicious on for assistant-directed text,
importance floor for protected senders); L2 only decides list mail that no guard,
rule or note protects.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

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
    """Abstain thresholds. Defaults are Inbox Zero's published starting points
    (0.3 / 0.1 / 0.75); they came from cloud models and MUST be re-tuned with
    scripts/triage_calibration_probe.py on the local model."""
    min_conf: float = 0.3      # top bucket below this -> abstain
    margin: float = 0.1        # top - second below this -> dead heat -> abstain
    archive_conf: float = 0.75  # summed bulk probability needed to demote to importance 0
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


@dataclass
class Context:
    user_email: str = ""
    correspondents: set = field(default_factory=set)   # addresses the user has written to
    important_roots: set = field(default_factory=set)  # domains marked important by prefs
    notes_text: str = ""
    people_domains: set = field(default_factory=set)   # orgs whose people (non-list, not no-reply) write in

    @property
    def user_domain(self) -> str:
        d = _domain(self.user_email)
        return "" if d in FREE_MAIL else d


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
        if _rule_is_important(r):
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
    return Context(user_email=user_email, people_domains=people - FREE_MAIL,
                   correspondents=load_correspondents(mailbox_dir, messages, user_email),
                   important_roots=roots, notes_text=notes.lower())


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
    """testbeds/email_triage/prefs.py semantics (every key given must match)."""
    if not when:
        return False
    addr, name = _addr(m), (m.get("from_name") or "").lower()
    dom = _domain(addr)
    subj, body = (m.get("subject") or "").lower(), (m.get("body") or "").lower()

    def any_in(hay, needles):
        return any(str(n).lower() in hay for n in (needles or []))

    for k, v in when.items():
        if k == "from_address" and addr != str(v).lower():
            return False
        if k == "from_domain" and dom != str(v).lower():
            return False
        if k == "from_domain_any" and dom not in {str(d).lower() for d in v}:
            return False
        if k == "from_domain_not_any" and dom in {str(d).lower() for d in v}:
            return False
        if k == "from_name_any" and not any_in(name, v):
            return False
        if k == "subject_any" and not any_in(subj, v):
            return False
        if k == "body_any" and not any_in(body, v):
            return False
        if k == "text_any" and not any_in(subj + "\n" + body, v):
            return False
        if k == "is_list" and is_list_mail(m) != bool(v):
            return False
    return True


def apply_ranked(pred: dict, m: dict, rules: List[dict]) -> Tuple[dict, set, List[str]]:
    """Apply matching rules in precedence order. Returns (pred, fields fixed
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


def l1_decide(m: dict, rules: List[dict]) -> Tuple[Optional[dict], List[str]]:
    """A complete decision from rules alone, else None. Complete = importance and
    category fixed, and needs_reply fixed (or implied False by a bulk category)."""
    p, fixed, hit = apply_ranked(dict(DEFAULT_PRED), m, rules)
    if not hit or not {"importance", "category"} <= fixed:
        return None, hit
    if "needs_reply" not in fixed:
        if p["category"] not in BULK:
            return None, hit
        p["needs_reply"] = False
    return p, hit


def raised_by_rules(m: dict, rules: List[dict]) -> bool:
    """Does any matching rule push importance above 0 or into a non-bulk class?"""
    for r in rules or []:
        if not rule_matches(r.get("when") or {}, m):
            continue
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


ModelBatch = Callable[[List[dict]], Tuple[Dict[str, dict], Dict[str, Optional[Dict[str, float]]]]]


def run_cascade(messages: List[dict], prefs: dict, ctx: Context, model_batch: ModelBatch,
                batch_size: int = 5, th: Optional[Thresholds] = None,
                log: Optional[Callable[[str], None]] = None) -> Tuple[List[dict], dict]:
    """Triage ``messages``. ``model_batch(batch) -> (preds_by_id, dists_by_id)``;
    preds are raw model labels (rules are applied here), dists may be empty.
    Returns (items in input order, trace)."""
    th = th or Thresholds.from_env()
    rules = prefs.get("rules", []) or []
    decided: Dict[str, dict] = {}
    trace_items: Dict[str, dict] = {}
    pending: List[dict] = []
    gmap = {}
    for m in messages:
        g = guards(m, ctx)
        gmap[m["id"]] = g
        tr = {"guards": sorted(k for k, v in g.items() if v)}
        p, hit = (None, [])
        if not g["injection"]:
            p, hit = l1_decide(m, rules)
        if p is not None:
            decided[m["id"]] = finalize(p, g)
            trace_items[m["id"]] = {**tr, "layer": "L1", "rules": hit}
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
            p, _, hit = apply_ranked(p, m, rules)
            demoted_by_rule = any(rule_matches(r.get("when") or {}, m)
                                  and ((r.get("set") or {}).get("importance") == 0
                                       or (r.get("set") or {}).get("max_importance") == 0)
                                  for r in rules)
            if (ab["abstain"] or not ab["demote_ok"]) and not demoted_by_rule:
                p["importance"] = max(int(p.get("importance", 1) or 0), 1)
            decided[m["id"]] = finalize(p, g)
            trace_items[m["id"]].update({"layer": "L3", "rules": hit, "abstain": ab["abstain"],
                                         "abstain_reason": ab["reason"], "conf": ab.get("conf"),
                                         "margin": ab.get("margin"), "top": ab.get("top")})
        if log:
            log(f"[cascade] model batch {i // batch_size + 1}: {len(batch)} item(s)")

    items = [{"id": m["id"], **decided[m["id"]]} for m in messages]
    layers = {"L1": 0, "L2": 0, "L3": 0}
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

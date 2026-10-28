"""
similo.py — tier-1 re-find: a Similo-style multi-attribute fingerprint and a weighted score (no model).

    fp = Fingerprint(role="button", name="Mark D-305 as won", label="D-305 Pinecrest Bank ...", ...)
    ranked = rank(fp, candidates)            # [(score, Fingerprint), ...] best first, role-gated
    d = decide(ranked, fp)                    # Decision(kind=accept | abstain | ambiguous, ...)

A fingerprint is what a replayed step remembers about its control: role, accessible name, visible
text, nearby label text (the row / block around it), the enclosing region or dialog (context), the
ancestor tag path, its order among same-role controls, the names of the neighbouring controls and
the link target. Form-field names, ctl ids and ``data-*`` attributes are deliberately NOT used:
portals regenerate them, and a benchmark that keys on them measures nothing.

Per-feature similarity is in [0, 1]; the score is the weighted mean over the features the
fingerprint actually has (a name-only fingerprint from an old skill is scored on name + role +
the few fields it has). Two guards make this safer than plain Similo, both aimed at the
"confident neighbour" false heal that the literature names as the main failure on deleted
controls (repair_verify.md 1b):

  * identity tokens: tokens with a digit ("D-305", "INV-0912", "2026-09") identify an entity.
    A candidate that does not carry the fingerprint's identity tokens has its name similarity
    capped at ``ID_CAP``, so "Mark D-304 as won" never stands in for a deleted "Mark D-305 as won";
  * anchors: an accepted candidate must agree with the fingerprint on at least one strong
    identity feature (name, or label + context together). Position, ancestors and siblings
    alone never carry a repair.

``decide`` accepts only when score >= ACCEPT, the margin to the second best >= MARGIN and an anchor
holds; it abstains (NOT_FOUND) when the best score < FLOOR; everything in between is AMBIGUOUS and
goes to the shortlist (<= 5 candidates within ``SHORTLIST_GAP`` of the best) for the model tier.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

ACCEPT = 0.72          # tier-1 accept threshold (score of the best candidate)
MARGIN = 0.10          # best - second best, below this the page has two plausible targets
FLOOR = 0.45           # below this nothing on the page is the remembered control: NOT_FOUND
SHORTLIST = 5
SHORTLIST_GAP = 0.25
ID_CAP = 0.25          # name similarity cap when the identity tokens are missing
ANCHOR_NAME = 0.66     # rule-tier repairs need >= 2 of 3 name tokens shared (set on --split dev)
ANCHOR_LABEL = 0.7     # ... and this label similarity when the name had twins

WEIGHTS: Dict[str, float] = {"name": 3.0, "role": 1.0, "text": 0.5, "label": 2.0, "context": 1.5,
                             "ancestors": 1.0, "position": 1.0, "siblings": 1.5, "href": 1.0}

#: roles a control may drift between and still take the same action
COMPATIBLE = {frozenset({"button", "link"}), frozenset({"combobox", "radiogroup"})}

#: small word-level synonym classes for renames the rule tier should see through (lower case)
SYNONYMS = [
    {"open", "view", "show", "see"}, {"save", "store", "keep"}, {"search", "find", "lookup"},
    {"next", "continue", "proceed", "forward"}, {"back", "previous", "prev", "return"},
    {"add", "new", "create"}, {"edit", "modify", "change", "update"}, {"download", "export", "get", "fetch"},
    {"email", "e-mail", "mail"}, {"phone", "telephone", "mobile", "tel"}, {"invoice", "bill"},
    {"statement", "statements"}, {"settings", "preferences", "options", "prefs"}, {"help", "support", "faq"},
    {"home", "dashboard", "overview"}, {"apply", "use"}, {"filter", "refine"}, {"sort", "order"},
    {"name", "title"}, {"message", "note"}, {"assign", "allocate"}, {"won", "closed-won"},
    {"archive", "shelve"}, {"select", "choose", "pick"}, {"close", "dismiss", "hide"},
    {"reply", "respond", "answer"}, {"forward", "fwd"}, {"cart", "basket", "bag"}, {"account", "profile"},
]
_CANON: Dict[str, str] = {}
for _grp in SYNONYMS:
    _c = sorted(_grp)[0]
    for _w in _grp:
        _CANON.setdefault(_w, _c)

STOP = {"the", "a", "an", "of", "to", "and", "or", "for", "in", "on", "by", "as", "at", "is", "your", "my", "this"}
_TOK = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
ID_RE = re.compile(r"^(?=.*\d)[a-z0-9]+(?:[-_.][a-z0-9]+)*$")


@dataclass
class Fingerprint:
    """What a step remembers about its control (or what a page offers as a candidate)."""
    role: str
    name: str = ""
    text: str = ""
    label: str = ""
    context: str = ""
    ancestors: Tuple[str, ...] = ()
    index: int = -1                    # order among same-role controls on the page
    count: int = 0                     # same-role controls on the page
    siblings: Tuple[str, ...] = ()     # names of the neighbouring controls (document order)
    href: str = ""
    twins: int = 1                     # same role + name on the page when recorded (1 = unique)
    ref: Any = field(default=None, compare=False, repr=False)   # the page element (candidates only)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("ref", None)
        d["ancestors"], d["siblings"] = list(self.ancestors), list(self.siblings)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Fingerprint":
        d = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__ and k != "ref"}
        d["ancestors"] = tuple(d.get("ancestors") or ())
        d["siblings"] = tuple(d.get("siblings") or ())
        return cls(**d)

    def show(self) -> str:
        bits = [f"{self.role} {self.name!r}"]
        if self.context:
            bits.append(f"in {self.context!r}")
        if self.label:
            bits.append(f"near {self.label[:80]!r}")
        return " ".join(bits)


# ------------------------------------------------------------------ similarity --

def tokens(s: str) -> List[str]:
    return [t for t in _TOK.findall((s or "").lower()) if t not in STOP]


def _canon(t: str) -> str:
    t = _CANON.get(t, t)
    if len(t) > 3 and t.endswith("s") and not ID_RE.match(t):
        t = _CANON.get(t[:-1], t[:-1])
    return t


def id_tokens(s: str) -> set:
    return {t for t in tokens(s) if ID_RE.match(t)}


def text_sim(a: str, b: str) -> float:
    """max(token Jaccard over synonym-canonical tokens, character ratio)."""
    a, b = " ".join((a or "").split()).lower(), " ".join((b or "").split()).lower()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = {_canon(t) for t in tokens(a)}, {_canon(t) for t in tokens(b)}
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return max(jac, difflib.SequenceMatcher(None, a, b).ratio())


def token_sim(a: str, b: str) -> float:
    """Jaccard over synonym-canonical tokens; a token pair that differs only by a typo / plural
    (character ratio >= 0.85) counts as shared. No character-level credit across different words:
    "Victor Kaya" vs "Victor Lee" is a different person, not a 60% match."""
    ta, tb = {_canon(t) for t in tokens(a)}, {_canon(t) for t in tokens(b)}
    if not ta or not tb:
        return 1.0 if " ".join((a or "").split()).lower() == " ".join((b or "").split()).lower() else 0.0
    shared = ta & tb
    ra, rb = sorted(ta - shared), sorted(tb - shared)
    near = 0
    for x in ra:
        for y in list(rb):
            if not (ID_RE.match(x) or ID_RE.match(y)) and difflib.SequenceMatcher(None, x, y).ratio() >= 0.85:
                rb.remove(y)
                near += 1
                break
    inter = len(shared) + near
    return inter / (len(ta) + len(tb) - inter)


def name_sim(fp: str, cand: str) -> float:
    s = token_sim(fp, cand)
    want = id_tokens(fp)
    if want and not want <= id_tokens(cand):
        s = min(s, ID_CAP)
    return s


def _lcs(a: Sequence[str], b: Sequence[str]) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def path_sim(a: Sequence[str], b: Sequence[str]) -> float:
    if not a and not b:
        return 1.0
    return _lcs(a, b) / max(len(a), len(b))


def set_sim(a: Sequence[str], b: Sequence[str]) -> float:
    sa = {" ".join(x.lower().split()) for x in a if x}
    sb = {" ".join(x.lower().split()) for x in b if x}
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def pos_sim(fp: Fingerprint, c: Fingerprint) -> float:
    if fp.index < 0 or c.index < 0:
        return 0.0
    ra = fp.index / max(fp.count - 1, 1)
    rb = c.index / max(c.count - 1, 1)
    return max(0.0, 1.0 - abs(ra - rb) * 2.0)


def role_ok(want: str, got: str) -> float:
    """1 same role, 0.5 a compatible drift (button <-> link), 0 incompatible (gated out)."""
    if want == got:
        return 1.0
    return 0.5 if frozenset({want, got}) in COMPATIBLE else 0.0


def features(fp: Fingerprint, c: Fingerprint) -> Dict[str, float]:
    """Per-feature similarities for the features the fingerprint has."""
    f: Dict[str, float] = {"role": role_ok(fp.role, c.role), "name": name_sim(fp.name, c.name)}
    if fp.text:
        f["text"] = text_sim(fp.text, c.text)
    if fp.label:
        f["label"] = text_sim(fp.label, c.label)
    if fp.context or fp.ancestors:          # a recorded (not name-only) fingerprint: "no context" is a fact
        f["context"] = text_sim(fp.context, c.context)
    if fp.ancestors:
        f["ancestors"] = path_sim(fp.ancestors, c.ancestors)
    if fp.index >= 0:
        f["position"] = pos_sim(fp, c)
    if fp.siblings:
        f["siblings"] = set_sim(fp.siblings, c.siblings)
    if fp.href:
        f["href"] = text_sim(fp.href, c.href)
    return f


def score(fp: Fingerprint, c: Fingerprint, weights: Optional[Dict[str, float]] = None) -> float:
    w = weights or WEIGHTS
    f = features(fp, c)
    tot = sum(w.get(k, 0.0) for k in f)
    return sum(w.get(k, 0.0) * v for k, v in f.items()) / tot if tot else 0.0


def anchored(fp: Fingerprint, c: Fingerprint) -> bool:
    """The rule tier may only repair onto a candidate whose NAME still resembles the remembered one
    (name_sim >= ANCHOR_NAME); a name that was not unique when recorded (twins > 1) also needs the
    nearby label to agree. Semantic renames ("Billing" -> "Invoices") are the model tier's job."""
    if name_sim(fp.name, c.name) < ANCHOR_NAME:
        return False
    if fp.twins > 1 and fp.label and text_sim(fp.label, c.label) < ANCHOR_LABEL:
        return False
    return True


def rank(fp: Fingerprint, cands: Sequence[Fingerprint], weights: Optional[Dict[str, float]] = None
         ) -> List[Tuple[float, Fingerprint]]:
    out = [(score(fp, c, weights), c) for c in cands if role_ok(fp.role, c.role) > 0]
    out.sort(key=lambda x: -x[0])
    return out


@dataclass
class Decision:
    kind: str                                  # accept | abstain | ambiguous
    best: Optional[Fingerprint] = None
    score: float = 0.0
    margin: float = 0.0
    shortlist: List[Tuple[float, Fingerprint]] = field(default_factory=list)
    reason: str = ""


def decide(ranked: List[Tuple[float, Fingerprint]], fp: Fingerprint, *, accept: float = ACCEPT,
           margin: float = MARGIN, floor: float = FLOOR, k: int = SHORTLIST) -> Decision:
    if not ranked:
        return Decision("abstain", reason="no control of a compatible role on the page")
    s1, c1 = ranked[0]
    s2 = ranked[1][0] if len(ranked) > 1 else 0.0
    if s1 < floor:
        return Decision("abstain", None, s1, s1 - s2, reason=f"best match {c1.show()} scores {s1:.2f} < {floor:.2f}")
    anch = [(s, c) for s, c in ranked if anchored(fp, c)]
    if anch:
        a1, ca = anch[0]
        a2 = anch[1][0] if len(anch) > 1 else 0.0
        if a1 >= accept and a1 - a2 >= margin and a1 >= s1 - margin:
            return Decision("accept", ca, a1, a1 - a2,
                            reason=f"score {a1:.2f}, margin {a1 - a2:.2f} over the next name-alike candidate")
    m = s1 - s2
    short = [(s, c) for s, c in ranked if s >= floor and s >= s1 - SHORTLIST_GAP][:k]
    why = (f"no name-alike candidate scores >= {accept:.2f} with margin {margin:.2f}" if s1 >= accept
           else f"score {s1:.2f} < {accept:.2f}")
    return Decision("ambiguous", c1, s1, m, short, reason=why)


__all__ = ["ACCEPT", "FLOOR", "MARGIN", "Decision", "Fingerprint", "WEIGHTS", "anchored", "decide", "features",
           "id_tokens", "name_sim", "rank", "role_ok", "score", "text_sim"]

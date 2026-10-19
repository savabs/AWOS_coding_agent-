"""
grounding.py — resolve a step target against an AX snapshot, or refuse.

WP6 of ~/Projects/REA/product2/BLUEPRINT.md. A recorded or model-chosen step
names an element ({role, name, label, text, container / position hint,
previous AX id}); this module finds *that* element in the current AX tree, or
returns a structured refusal. It never returns "the first match".

    g = ground(tree, Target(role="button", name="Save", container=(("form", "Create key"),)))
    if g.ok:
        backend.click(g.match.center)          # or ui_session.act("click", g.sid)
    else:
        g.refusal.reason                       # ambiguous | not_found | truncated | ...

Resolution ladder (first tier with any candidate decides; a tier that finds
two or more candidates the hints cannot tell apart is an ``ambiguous``
refusal, it does not fall through to a looser tier):

  1. ``ax_id``     the AX identifier / DOM id (role must agree, as in
                   OpenAdapt's ``_locator_matches``)
  2. ``sid``       the previous ax_observe stable ID, when an Observation is
                   passed and the ID is not marked ambiguous
  3. ``locator``   recorded locators (generate_locators), best score first
  4. ``role_name`` role + accessible name, scoped to the exact window and,
                   when it narrows, the recorded container and row
  5. ``label``     role + label / placeholder / help text
  6. ``text``      visible text, retargeted to its interactive ancestor
                   (Playwright ``retarget``)
  7. ``relaxed``   renamed control: same role in the same container, scored
                   by name-token similarity, button class and recorded
                   position; needs a clear winner (margin) and is never used
                   for a destructive target
  8. ``ocr``       OCR fallback (interface only; Apple Vision later)

Every accepted match then passes the gates: visible (has a click centre),
enabled, not under a modal it does not belong to (``blocked_by_modal``), and
no risk escalation (a target recorded as normal / primary / cancel never
resolves to a destructive control — ax_compress.button_role decides, and AX
values cannot downgrade it).

Refusals (``Refusal.reason``): ambiguous, not_found, truncated (the walk hit
its node budget, the snapshot says it is partial, or a lazily loaded list
shows only some rows), window_not_found, window_ambiguous, blocked_by_modal,
not_visible, disabled, risk_escalation, low_confidence. Each carries up to
five candidate summaries so a caller (WP8 ``needs_user``) can show them.

Recording (WP4 / WP5): ``record_target(tree, path)`` captures a Target with
ranked, uniqueness-checked locators, the window, container chain, row
content, normalised position and button class. ``generate_locators`` is a
port of Playwright's selectorGenerator.ts to AX: the same cost table (AX
identifier ~ test id = 1, role+name 100/105, placeholder 120, label 140, text
180, title/help 200, role-only 510, nth 10000), the same length penalty,
number-stripping text alternatives, GUID-like id rejection, icon-font name
rejection, "nth only up to 5", and best-parent scoping limited to two
levels. Each locator is verified against the snapshot it was generated from.

Portions ported from Playwright (packages/injected/src/selectorGenerator.ts),
Copyright (c) Microsoft Corporation, Apache License 2.0; changes: rewritten in
Python over macOS AX / DOM-like JSON trees instead of the DOM, CSS fallback
replaced by container + nth scoping, scores reused. Refusal semantics
(bounded window-scoped walk, ambiguity and truncation refuse, role must agree
with the identifier) follow OpenAdapt
(packages/flow/openadapt_flow/backends/macos_backend.py), Copyright (c) 2026
OpenAdapt.AI (MLDSAI Inc.), MIT License. See docs/third_party/wp6.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from .ax_compress import (
    CLIPPING, INTERACTIVE, LISTLIKE, MODAL, ROW_BAND_PX, ROWLIKE, _clip, _frame,
    _geometry, _ident, _int, _norm_title, _row_content, button_role, norm_role,
)

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
MAX_NODES = 4000                 # OpenAdapt _AX_ENUMERATION_LIMIT
DEFAULT_MIN_CONFIDENCE = 0.5
MAX_NTH = 5                      # Playwright: no nth=6 or worse
RELAXED_MIN_SCORE = 0.35
RELAXED_MIN_MARGIN = 0.15

# Playwright selectorGenerator.ts cost table (lower is better)
K_TEXT_SCORE_RANGE = 10
K_EXACT_PENALTY = K_TEXT_SCORE_RANGE // 2
K_TEST_ID = 1                    # AXIdentifier / DOM id that is not GUID-like
K_BEGIN_PENALIZED = 50
K_ROLE_WITH_NAME = 100
K_PLACEHOLDER = 120
K_LABEL = 140
K_TEXT = 180
K_TITLE = 200
K_END_PENALIZED = 300
K_ROLE_WITHOUT_NAME = 510
K_NTH = 10000

#: tier -> confidence of a unique match there
TIER_CONFIDENCE = {"ax_id": 0.99, "sid": 0.97, "role_name": 0.95, "label": 0.9,
                   "text": 0.85, "nth": 0.7}

REASONS = ("ambiguous", "not_found", "truncated", "window_not_found", "window_ambiguous",
           "blocked_by_modal", "not_visible", "disabled", "risk_escalation", "low_confidence")

#: roles that name a scope a target can be "inside"
CONTAINER_ROLES = (MODAL | {"window", "toolbar", "group", "radiogroup", "form", "table", "list",
                            "outline", "tabgroup", "menu", "menubar", "navigation", "nav",
                            "section", "region", "fieldset", "splitgroup", "scrollarea",
                            "webarea", "main", "banner", "complementary", "grid", "listbox",
                            "tree", "browser", "collection", "popover"})

#: role families: an AX app and a web page call the same control differently
_FAMILY = {
    "button": "button", "menubutton": "button",
    "link": "link", "a": "link",
    "textfield": "text", "textarea": "text", "input": "text", "searchfield": "text",
    "securetextfield": "text", "combobox": "text", "textbox": "text",
    "radiobutton": "radio", "radio": "radio",
    "checkbox": "check", "switch": "check",
    "popupbutton": "select", "select": "select",
    "statictext": "stext", "text": "stext", "label": "stext",
    "alertdialog": "dialog", "dialog": "dialog", "sheet": "dialog", "alert": "dialog",
    "systemdialog": "dialog",
    "tab": "tab", "tabbutton": "tab", "menuitem": "menuitem", "menubaritem": "menuitem",
    "fieldset": "group", "group": "group",
}
_RETARGET = {"button", "menubutton", "link", "a", "checkbox", "radiobutton", "switch",
             "menuitem", "tab", "cell", "row", "popupbutton"}


def family(role: Optional[str]) -> str:
    r = norm_role(role)
    return _FAMILY.get(r, r)


def norm(s: Any) -> str:
    """Whitespace-collapsed, case-folded, punctuation-trimmed text."""
    return _norm_title(" ".join(str(s or "").split())).casefold()


#: common UI verb renames (never destructive words: those never resolve fuzzily)
SYNONYMS = {"new": "create", "add": "create", "next": "continue", "proceed": "continue",
            "okay": "ok", "dismiss": "close", "preferences": "settings", "options": "settings",
            "find": "search"}


def _tokens(s: str) -> List[str]:
    return [SYNONYMS.get(t, t) for t in re.split(r"[^0-9a-z]+", norm(s)) if t]


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of word tokens (0..1)."""
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ---------------------------------------------------------------------------
# Playwright helpers (ported)
# ---------------------------------------------------------------------------
def is_guid_like(ident: str) -> bool:
    """selectorGenerator.ts isGuidLike: many character-class transitions."""
    last = None
    transitions = 0
    for c in ident:
        if c in "-_":
            continue
        if "a" <= c <= "z":
            kind = "lower"
        elif "A" <= c <= "Z":
            kind = "upper"
        elif "0" <= c <= "9":
            kind = "digit"
        else:
            kind = "other"
        if kind == "lower" and last == "upper":
            last = kind
            continue
        if last and last != kind:
            transitions += 1
        last = kind
    return transitions >= len(ident) / 4


_PRIVATE_USE = re.compile(r"^[\ue000-\uf8ff]+$")


def _trim_word_boundary(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    text = text[:n]
    m = re.match(r"^(.*)\b(.+?)$", text, re.S)
    return m.group(1).rstrip() if m else ""


def text_alternatives(text: str) -> List[Tuple[str, int]]:
    """selectorGenerator.ts suitableTextAlternatives: (text, score bonus)."""
    out: List[Tuple[str, int]] = []
    m = re.match(r"^([\d.,]+)[^.,\w]", text)
    if m:
        alt = _trim_word_boundary(text[len(m.group(1)):].lstrip(), 80)
        out.append((alt, 2 if len(alt) <= 30 else 1))
    m = re.search(r"[^.,\w]([\d.,]+)$", text)
    if m:
        alt = _trim_word_boundary(text[:len(text) - len(m.group(1))].rstrip(), 80)
        out.append((alt, 2 if len(alt) <= 30 else 1))
    if len(text) <= 30:
        out.append((text, 0))
    else:
        out.append((_trim_word_boundary(text, 80), 0))
        out.append((_trim_word_boundary(text, 30), 1))
    out = [o for o in out if o[0]]
    return out or [(text[:80], 0)]


# ---------------------------------------------------------------------------
# AX index
# ---------------------------------------------------------------------------
_NAME_KEYS = ("title", "name", "label", "description", "AXTitle", "AXDescription")


def _attr(n: dict, *keys: str) -> str:
    for k in keys:
        v = n.get(k)
        if v not in (None, "") and not isinstance(v, (dict, list)):
            s = " ".join(str(v).split())
            if s:
                return s[:200]
    return ""


def _child_text(n: dict, depth: int = 3) -> str:
    for c in n.get("children") or []:
        if not isinstance(c, dict) or c.get("hidden") is True:
            continue
        t = _attr(c, *_NAME_KEYS, "value") or (_child_text(c, depth - 1) if depth > 1 else "")
        if t:
            return t
    return ""


@dataclass
class Node:
    """One AX node with what grounding needs (raw, not redacted: never rendered)."""
    path: Tuple[int, ...]
    role: str
    name: str
    placeholder: str
    help: str
    value: str
    ident: Optional[str]
    raw: dict = field(repr=False)
    parent: Optional["Node"] = field(default=None, repr=False)
    frame: Optional[Tuple[float, float, float, float]] = None
    center: Optional[Tuple[int, int]] = None
    visible: bool = True
    enabled: bool = True
    modal: bool = False
    order: int = 0                       # pre-order index
    reachable: bool = True               # some of it is inside every clip
    btn: Optional[str] = None            # ax_compress.button_role

    @property
    def interactive(self) -> bool:
        return self.role in INTERACTIVE or self.btn is not None

    @property
    def text(self) -> str:
        """Visible text: the name, else a static text's value."""
        return self.name or (self.value if family(self.role) == "stext" else "")

    def ancestors(self) -> Iterable["Node"]:
        p = self.parent
        while p is not None:
            yield p
            p = p.parent

    def window(self) -> Optional["Node"]:
        return next((a for a in self.ancestors() if a.role == "window"), None)

    def modal_root(self) -> Optional["Node"]:
        if self.role in MODAL:
            return self
        return next((a for a in self.ancestors() if a.role in MODAL), None)

    def containers(self) -> List[Tuple[str, str]]:
        """Named scope ancestors, outermost first, the window excluded."""
        out = []
        for a in self.ancestors():
            if a.role == "window" or a.role == "application":
                continue
            nm = a.name or (a.ident or "")
            if nm and (a.role in CONTAINER_ROLES or a.ident):
                out.append((a.role, nm))
        return out[::-1]

    def row(self) -> str:
        for a in [self] + list(self.ancestors()):
            if a.role in ROWLIKE or (a.parent is not None and a.parent.role in LISTLIKE):
                return _row_content(a.raw)
            if a.role in LISTLIKE:
                break
        return ""

    def summary(self) -> dict:
        return {"role": self.role, "name": self.name, "path": list(self.path),
                "center": list(self.center) if self.center else None,
                "button_role": self.btn, "container": [list(c) for c in self.containers()[-1:]],
                "row": self.row() or None}


@dataclass
class AXIndex:
    nodes: List[Node]
    truncated: bool = False
    partial_lists: List[str] = field(default_factory=list)

    def by_path(self, path: Sequence[int]) -> Optional[Node]:
        t = tuple(path)
        return next((n for n in self.nodes if n.path == t), None)

    def modals(self) -> List[Node]:
        return [n for n in self.nodes if n.role in MODAL and n.visible]


def build_index(tree: Any, viewport: Optional[Tuple[float, float, float, float]] = None,
                max_nodes: int = MAX_NODES) -> AXIndex:
    """Bounded walk of the tree (paths match ax_compress Element.depth_path).

    The walk refuses to pretend: past ``max_nodes`` it stops and the index is
    ``truncated``; a snapshot carrying ``truncated: true`` is truncated too.
    """
    if isinstance(tree, AXIndex):
        return tree
    truncated = False
    if isinstance(tree, dict) and "tree" in tree and "role" not in tree:
        truncated = bool(tree.get("truncated"))
        tree = tree["tree"]
    roots = tree if isinstance(tree, list) else [tree]
    nodes: List[Node] = []
    partial: List[str] = []
    # iterative pre-order DFS with an explicit budget
    stack: List[Tuple[Any, Tuple[int, ...], Optional[Node], Any, bool]] = [
        (r, (i,), None, viewport, False) for i, r in reversed(list(enumerate(roots)))]
    while stack:
        raw, path, parent, clip, in_modal = stack.pop()
        if not isinstance(raw, dict):
            continue
        if len(nodes) >= max_nodes:
            truncated = True
            break
        if raw.get("truncated") is True:
            truncated = True
        role = norm_role(raw.get("role") or raw.get("tag"))
        hidden = raw.get("hidden") is True or raw.get("visible") is False
        any_vis, keep, vr, _partial = _geometry(raw, clip, role)
        name = _attr(raw, *_NAME_KEYS)
        if not name and role in INTERACTIVE:
            name = _child_text(raw)
        value = raw.get("value")
        n = Node(path=path, role=role, name=name,
                 placeholder=_attr(raw, "placeholder", "AXPlaceholderValue"),
                 help=_attr(raw, "help", "AXHelp"),
                 value="" if value is None or isinstance(value, (dict, list)) else str(value),
                 ident=_ident(raw), raw=raw, parent=parent, frame=_frame(raw),
                 visible=bool(any_vis and keep and not hidden),
                 reachable=bool(any_vis) and (parent is None or parent.reachable),
                 enabled=raw.get("enabled", True) is not False,
                 modal=in_modal or role in MODAL, order=len(nodes))
        if not n.reachable:
            n.visible = False
        if n.visible:
            n.center = (int(vr[0] + vr[2] / 2), int(vr[1] + vr[3] / 2)) if vr else None
        n.btn = button_role(raw, role)
        if hidden:
            continue                              # hidden subtree: not part of the page
        nodes.append(n)
        kids = raw.get("children") or []
        if role in LISTLIKE:
            total = _int(raw.get("total", raw.get("row_count", raw.get("count"))))
            if total and total > len([k for k in kids if isinstance(k, dict)]):
                partial.append(f"{role} {name!r}: lists {len(kids)} of {total} rows")
        nclip = _clip(clip, _frame(raw)) if role in CLIPPING else clip
        for i in range(len(kids) - 1, -1, -1):
            stack.append((kids[i], path + (i,), n, nclip, n.modal))
    return AXIndex(nodes, truncated, partial)


def _reading_key(n: Node):
    group = 0 if n.role in MODAL else (1 if n.modal else 2)
    if n.center is None:
        return (group, 1e9, 1e9, n.order)
    return (group, n.center[1] // ROW_BAND_PX, n.center[0], n.order)


def _reading(nodes: Iterable[Node]) -> List[Node]:
    return sorted(nodes, key=_reading_key)


# ---------------------------------------------------------------------------
# locators
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Token:
    """One locator step. kind: id | role | placeholder | label | text | title |
    role_only | container | nth. ``container`` scopes to descendants of the
    ancestor (role, value); ``nth`` picks among matches in reading order."""
    kind: str
    value: str = ""
    role: str = ""
    exact: bool = True
    score: int = 0

    def __str__(self) -> str:
        q = '"' + self.value.replace('"', '\\"') + '"' + ("" if self.exact else "i")
        if self.kind == "id":
            return f"id={q}"
        if self.kind in ("role", "container"):
            return f"{self.kind}={self.role}[name={q}]"
        if self.kind == "role_only":
            return f"role={self.role}"
        if self.kind == "nth":
            return f"nth={self.value}"
        return f"{self.kind}={q}" + (f"[role={self.role}]" if self.role else "")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value, "role": self.role,
                "exact": self.exact, "score": self.score}

    @classmethod
    def from_dict(cls, d: dict) -> "Token":
        return cls(str(d["kind"]), str(d.get("value", "")), str(d.get("role", "")),
                   bool(d.get("exact", True)), int(d.get("score", 0)))


@dataclass(frozen=True)
class Locator:
    tokens: Tuple[Token, ...]

    @property
    def score(self) -> int:
        """selectorGenerator.ts combineScores: earlier tokens weigh more."""
        n = len(self.tokens)
        return sum(t.score * (n - i) for i, t in enumerate(self.tokens))

    def __str__(self) -> str:
        return " >> ".join(str(t) for t in self.tokens)

    def to_dict(self) -> dict:
        return {"tokens": [t.to_dict() for t in self.tokens], "score": self.score, "str": str(self)}

    @classmethod
    def from_dict(cls, d: dict) -> "Locator":
        return cls(tuple(Token.from_dict(t) for t in d["tokens"]))


def _text_match(have: str, want: str, exact: bool) -> bool:
    h, w = norm(have), norm(want)
    if not h or not w:
        return False
    return h == w if exact else w in h


def _token_matches(n: Node, t: Token) -> bool:
    if t.role and family(n.role) != family(t.role):
        return False
    if t.kind == "id":
        return n.ident == t.value
    if t.kind in ("role", "container"):
        return _text_match(n.name, t.value, t.exact)
    if t.kind == "role_only":
        return True
    if t.kind == "placeholder":
        return _text_match(n.placeholder, t.value, t.exact)
    if t.kind == "label":
        return any(_text_match(x, t.value, t.exact) for x in (n.name, n.placeholder))
    if t.kind == "text":
        return _text_match(n.text, t.value, t.exact)
    if t.kind == "title":
        return _text_match(n.help, t.value, t.exact)
    return False


def evaluate(index: AXIndex, loc: Locator, scope: Optional[List[Node]] = None) -> List[Node]:
    """Every node the locator matches, in reading order."""
    cur = list(scope if scope is not None else index.nodes)
    for t in loc.tokens:
        if t.kind == "nth":
            i = int(t.value)
            cur = [cur[i]] if 0 <= i < len(cur) else []
            continue
        if t.kind == "container":
            parents = {id(p) for p in cur if _token_matches(p, t)}
            cur = [n for n in cur if any(id(a) in parents for a in n.ancestors())]
            continue
        cur = _reading([n for n in cur if _token_matches(n, t)])
    return cur


def _penalize(tokens: List[Token]) -> List[Token]:
    out = []
    for t in tokens:
        s = t.score
        if K_BEGIN_PENALIZED < s < K_END_PENALIZED:
            s += min(K_TEXT_SCORE_RANGE, len(str(t)) // 10)
        out.append(Token(t.kind, t.value, t.role, t.exact, s))
    return out


def _candidates(n: Node, no_text: bool = False) -> List[Tuple[Locator, bool]]:
    """(locator, is_text_candidate), Playwright build{Text,NoText}Candidates."""
    toks: List[Tuple[Token, bool]] = []
    if n.ident and not is_guid_like(n.ident):
        toks.append((Token("id", n.ident, n.role, True, K_TEST_ID), False))
    if n.placeholder:
        toks.append((Token("placeholder", n.placeholder, n.role, True, K_PLACEHOLDER + K_EXACT_PENALTY), False))
        for alt, bonus in text_alternatives(n.placeholder):
            toks.append((Token("placeholder", alt, n.role, False, K_PLACEHOLDER - bonus), False))
    if n.role not in ("unknown", "generic", "none", "presentation"):
        toks.append((Token("role_only", "", n.role, True, K_ROLE_WITHOUT_NAME), False))
    if not no_text:
        if n.help:
            toks.append((Token("title", n.help, n.role, True, K_TITLE + K_EXACT_PENALTY), True))
            for alt, bonus in text_alternatives(n.help):
                toks.append((Token("title", alt, n.role, False, K_TITLE - bonus), True))
        name = n.name
        if name and not _PRIVATE_USE.match(name):
            toks.append((Token("role", name, n.role, True, K_ROLE_WITH_NAME + K_EXACT_PENALTY), True))
            for alt, bonus in text_alternatives(name):
                toks.append((Token("role", alt, n.role, False, K_ROLE_WITH_NAME - bonus), True))
        elif n.text and len(n.text) <= 80:
            toks.append((Token("text", n.text, "", True, K_TEXT + K_EXACT_PENALTY), True))
            for alt, bonus in text_alternatives(n.text):
                toks.append((Token("text", alt, "", False, K_TEXT - bonus), True))
    pen = _penalize([t for t, _ in toks])
    return [(Locator((t,)), is_text) for t, (_, is_text) in zip(pen, toks)]


def _container_token(a: Node) -> Optional[Token]:
    if a.ident and not is_guid_like(a.ident):
        return Token("id", a.ident, a.role, True, K_TEST_ID)
    if a.name and (a.role in CONTAINER_ROLES or a.role in ROWLIKE):
        return Token("container", a.name, a.role, True, K_ROLE_WITH_NAME + K_EXACT_PENALTY)
    return None


def _scoped(index: AXIndex, parent_tok: Token, inner: Locator, scope: List[Node]) -> List[Node]:
    if parent_tok.kind == "id":
        parents = [p for p in scope if p.ident == parent_tok.value]
        ids = {id(p) for p in parents}
        sub = [n for n in scope if any(id(a) in ids for a in n.ancestors())]
        return evaluate(index, inner, sub)
    return evaluate(index, Locator((parent_tok,) + inner.tokens), scope)


def generate_locators(tree: Any, path: Sequence[int], *, k: int = 3,
                      viewport: Optional[Tuple[float, float, float, float]] = None,
                      scope: Optional[List[Node]] = None) -> List[Locator]:
    """Ranked locators that each resolve to exactly the node at ``path``.

    Port of selectorGenerator.ts generateSelectorFor: candidates sorted by
    score; a candidate that matches only the target wins; otherwise
    ``candidate >> nth=i`` (i <= 5) is kept and every named ancestor is tried
    as a scope (two levels, no text in the parent when the target uses
    text). Returns up to ``k`` distinct verified locators, best first."""
    index = build_index(tree, viewport)
    target = index.by_path(path)
    if target is None:
        return []
    # Playwright retarget: a click on a label inside a button is the button
    if not target.interactive:
        anc = next((a for a in target.ancestors() if a.role in _RETARGET), None)
        if anc is not None and anc.visible:
            target = anc
    win = target.window()
    if scope is None:
        scope = [n for n in index.nodes if win is None or n.window() is win]
    results: Dict[str, Locator] = {}

    def keep(loc: Locator) -> None:
        s = str(loc)
        if s not in results or loc.score < results[s].score:
            results[s] = loc

    unique = 0
    for loc, _is_text in sorted(_candidates(target), key=lambda c: c[0].score):
        if unique >= k:
            break                             # sorted: the rest score worse
        els = evaluate(index, loc, scope)
        if target not in els:
            continue
        if len(els) == 1:
            keep(loc)
            unique += 1
            continue
        idx = els.index(target)
        if idx <= MAX_NTH:
            keep(Locator(loc.tokens + (Token("nth", str(idx), "", True, K_NTH),)))
        for a in target.ancestors():
            if a.role == "window":
                break
            ptok = _container_token(a)
            if ptok is None:
                continue
            filtered = [e for e in els if any(x is a for x in e.ancestors())]
            if target not in filtered or len(filtered) > MAX_NTH:
                continue
            new_idx = filtered.index(target)
            if new_idx == idx and len(filtered) > 1:
                continue                      # not an improvement
            # the parent itself must be unique in scope (two levels max)
            pmatch = [p for p in scope if (p.ident == ptok.value if ptok.kind == "id"
                                           else _token_matches(p, ptok))]
            if len(pmatch) != 1:
                continue
            inner = loc if len(filtered) == 1 else Locator(
                loc.tokens + (Token("nth", str(new_idx), "", True, K_NTH),))
            cand = Locator((ptok,) + inner.tokens)
            got = _scoped(index, ptok, inner, scope)
            if got == [target]:
                keep(cand)
    out = sorted(results.values(), key=lambda l: (l.score, len(str(l))))
    # a fallback must name the element: a bare role (+ container / nth) locator
    # is kept only when nothing better exists
    named = [l for l in out if any(t.kind not in ("role_only", "nth", "container") for t in l.tokens)]
    out = named or out
    # verified unique only
    verified = []
    for loc in out:
        toks = loc.tokens
        got = _scoped(index, toks[0], Locator(toks[1:]), scope) if toks[0].kind == "id" and len(toks) > 1 \
            else evaluate(index, loc, scope)
        if got == [target]:
            verified.append(loc)
        if len(verified) >= k:
            break
    return verified


# ---------------------------------------------------------------------------
# targets
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Target:
    """What a step wants to act on. Every field is optional; more is better."""
    role: Optional[str] = None
    name: Optional[str] = None               # accessible name (AXTitle / aria-label)
    label: Optional[str] = None              # visible label / placeholder / AXHelp
    text: Optional[str] = None               # visible text on or in the control
    container: Tuple[Tuple[str, str], ...] = ()   # named scopes, outermost first
    window: Optional[str] = None             # exact window title
    position: Optional[Tuple[float, float]] = None   # centre, 0..1 within innermost container
    row: Optional[str] = None                # content of the enclosing row
    ax_id: Optional[str] = None
    prev_sid: Optional[str] = None
    button_role: Optional[str] = None        # recorded risk class (normal/primary/cancel/destructive)
    nth: Optional[int] = None                # ordinal among role+name matches at record time
    nth_of: Optional[int] = None             # how many there were
    locators: Tuple[Locator, ...] = ()

    def risk(self) -> Optional[str]:
        if self.button_role:
            return self.button_role
        nm = self.name or self.label or self.text
        if not nm or not self.role:
            return None
        return button_role({"role": self.role, "title": nm})

    def to_dict(self) -> dict:
        return {"role": self.role, "name": self.name, "label": self.label, "text": self.text,
                "container": [list(c) for c in self.container], "window": self.window,
                "position": list(self.position) if self.position else None, "row": self.row,
                "ax_id": self.ax_id, "prev_sid": self.prev_sid, "button_role": self.button_role,
                "nth": self.nth, "nth_of": self.nth_of,
                "locators": [l.to_dict() for l in self.locators]}

    @classmethod
    def from_dict(cls, d: dict) -> "Target":
        return cls(role=d.get("role"), name=d.get("name"), label=d.get("label"), text=d.get("text"),
                   container=tuple(tuple(c) for c in d.get("container") or ()),  # type: ignore[misc]
                   window=d.get("window"),
                   position=tuple(d["position"]) if d.get("position") else None,  # type: ignore[arg-type]
                   row=d.get("row"), ax_id=d.get("ax_id"), prev_sid=d.get("prev_sid"),
                   button_role=d.get("button_role"), nth=d.get("nth"), nth_of=d.get("nth_of"),
                   locators=tuple(Locator.from_dict(l) for l in d.get("locators") or ()))

    @classmethod
    def from_element(cls, e: Any) -> "Target":
        """From an ax_compress.Element the model picked (by sid)."""
        anc = [a for a in getattr(e, "anc", ()) if a[0] not in ("window", "application")]
        win = next((a[1] for a in getattr(e, "anc", ()) if a[0] == "window"), None)
        return cls(role=e.role, name=e.label or e.text or None, container=tuple(anc[-2:]),
                   window=win, row=e.ctx or None, ax_id=e.ident, prev_sid=e.sid or None,
                   button_role=e.button_role)


def _rel_position(n: Node, box: Optional[Node]) -> Optional[Tuple[float, float]]:
    if n.center is None:
        return None
    fr = box.frame if box is not None and box.frame else None
    if not fr or fr[2] <= 0 or fr[3] <= 0:
        return None
    return (round((n.center[0] - fr[0]) / fr[2], 3), round((n.center[1] - fr[1]) / fr[3], 3))


def _innermost_container(n: Node) -> Optional[Node]:
    for a in n.ancestors():
        if a.role in ("window", "application"):
            return a
        if (a.name or a.ident) and a.role in CONTAINER_ROLES:
            return a
    return None


def record_target(tree: Any, path: Sequence[int], *, k: int = 3,
                  viewport: Optional[Tuple[float, float, float, float]] = None) -> Target:
    """Recording-time Target for the node at ``path`` (WP4 recorder / WP5)."""
    index = build_index(tree, viewport)
    n = index.by_path(path)
    if n is None:
        raise KeyError(f"no node at {tuple(path)}")
    if not n.interactive:
        anc = next((a for a in n.ancestors() if a.role in _RETARGET), None)
        if anc is not None and anc.visible:
            n = anc
    locs = generate_locators(index, n.path, k=k)
    win = n.window()
    scope = [x for x in index.nodes if win is None or x.window() is win]
    nth = nth_of = None
    if n.name:
        same = _reading([x for x in scope if family(x.role) == family(n.role) and norm(x.name) == norm(n.name)])
        if len(same) > 1 and n in same:
            nth, nth_of = same.index(n), len(same)
    box = _innermost_container(n)
    return Target(role=n.role, name=n.name or None, label=n.placeholder or n.help or None,
                  text=None if n.name else (n.text or None), container=tuple(n.containers()[-2:]),
                  window=win.name if win is not None and win.name else None,
                  position=_rel_position(n, box), row=n.row() or None,
                  ax_id=n.ident if n.ident and not is_guid_like(n.ident) else None,
                  button_role=n.btn, nth=nth, nth_of=nth_of, locators=tuple(locs))


# ---------------------------------------------------------------------------
# OCR fallback (interface only)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OcrHit:
    text: str
    frame: Tuple[float, float, float, float]
    confidence: float = 1.0


class OcrProvider(Protocol):
    """Find on-screen text when AX exposes nothing (canvas / remote / web
    without an AX tree). Return None when OCR is unavailable, else every hit
    (not the first). Apple Vision (VNRecognizeTextRequest) will implement
    this; OmniParser is excluded (AGPL weights)."""

    def find_text(self, text: str, region: Optional[Tuple[float, float, float, float]] = None
                  ) -> Optional[List[OcrHit]]: ...


class NullOcr:
    """No OCR available: the ladder ends at the AX tiers."""

    def find_text(self, text, region=None):
        return None


class AppleVisionOcr:
    """Placeholder for the Apple Vision backend (not implemented: no
    screen-recording permission is requested from tests or CI)."""

    available = False

    def find_text(self, text, region=None):
        return None


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
@dataclass
class Match:
    role: str
    name: str
    center: Optional[Tuple[int, int]]
    path: Tuple[int, ...]
    tier: str
    confidence: float
    button_role: Optional[str] = None
    ident: Optional[str] = None
    sid: Optional[str] = None
    locator: str = ""

    def to_dict(self) -> dict:
        return {"role": self.role, "name": self.name,
                "center": list(self.center) if self.center else None, "path": list(self.path),
                "tier": self.tier, "confidence": round(self.confidence, 3),
                "button_role": self.button_role, "sid": self.sid, "locator": self.locator}


@dataclass
class Refusal:
    reason: str
    message: str
    tier: str = ""
    candidates: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"reason": self.reason, "message": self.message, "tier": self.tier,
                "candidates": self.candidates}


@dataclass
class Grounding:
    match: Optional[Match] = None
    refusal: Optional[Refusal] = None
    trace: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.match is not None

    @property
    def sid(self) -> Optional[str]:
        return self.match.sid if self.match else None

    @property
    def center(self) -> Optional[Tuple[int, int]]:
        return self.match.center if self.match else None

    @property
    def confidence(self) -> float:
        return self.match.confidence if self.match else 0.0

    @property
    def tier(self) -> str:
        return self.match.tier if self.match else (self.refusal.tier if self.refusal else "")

    def to_dict(self) -> dict:
        return {"ok": self.ok, "match": self.match.to_dict() if self.match else None,
                "refusal": self.refusal.to_dict() if self.refusal else None, "trace": self.trace}


def _refuse(reason: str, message: str, tier: str = "", cands: Sequence[Node] = (),
            trace: Optional[List[str]] = None) -> Grounding:
    return Grounding(None, Refusal(reason, message, tier, [c.summary() for c in list(cands)[:5]]),
                     trace or [])


# ---------------------------------------------------------------------------
# the ladder
# ---------------------------------------------------------------------------
def _in_container(n: Node, entry: Tuple[str, str]) -> bool:
    role, name = entry
    return any(family(a.role) == family(role) and (norm(a.name) == norm(name) or a.ident == name)
               for a in n.ancestors())


def _narrow(cands: List[Node], target: Target, trace: List[str], tier: str) -> List[Node]:
    """Use the container and row hints to tell candidates apart. A hint that
    excludes everything is ignored (the control may have moved), never used
    to invent a match."""
    if len(cands) <= 1:
        return cands
    for entry in reversed(target.container):
        sub = [c for c in cands if _in_container(c, entry)]
        if sub:
            if len(sub) < len(cands):
                trace.append(f"{tier}: container {entry[0]}:{entry[1]!r} -> {len(sub)}")
            cands = sub
            break
    if target.row and len(cands) > 1:
        sub = [c for c in cands if norm(c.row()) == norm(target.row)]
        if sub:
            trace.append(f"{tier}: row -> {len(sub)}")
            cands = sub
    return cands


def _pick(cands: List[Node], target: Target, tier: str, trace: List[str]
          ) -> Tuple[Optional[Node], Optional[Grounding], float]:
    """(node, refusal, confidence) for a tier's candidate set (non-empty)."""
    cands = _reading(_narrow(cands, target, trace, tier))
    if len(cands) == 1:
        return cands[0], None, TIER_CONFIDENCE.get(tier, 0.8)
    # an ordinal is only trusted when the group has the size it had when recorded
    if target.nth is not None and target.nth_of == len(cands) and 0 <= target.nth < len(cands):
        trace.append(f"{tier}: nth {target.nth} of {len(cands)} (same group size as recorded)")
        return cands[target.nth], None, TIER_CONFIDENCE["nth"]
    return None, _refuse("ambiguous", f"{len(cands)} elements match at tier {tier}; refusing to guess",
                         tier, cands, trace), 0.0


def _role_ok(n: Node, target: Target) -> bool:
    return not target.role or family(n.role) == family(target.role)


def _relaxed(scope: List[Node], target: Target, trace: List[str]
             ) -> Tuple[Optional[Node], Optional[Grounding], float]:
    if not target.role or target.risk() == "destructive":
        return None, None, 0.0
    want = target.name or target.label or target.text or ""
    pool = [n for n in scope if family(n.role) == family(target.role) and n.visible]
    box_entry = target.container[-1] if target.container else None
    if box_entry is not None:
        inside = [n for n in pool if _in_container(n, box_entry)]
        if not inside:
            trace.append("relaxed: recorded container not found")
            return None, None, 0.0
        pool = inside
    elif target.position is None:
        return None, None, 0.0
    if target.row:
        same_row = [n for n in pool if similarity(n.row(), target.row) >= 0.5]
        pool = same_row or pool
    # never resolve a non-destructive target to a destructive control; the
    # crowd is counted first, so removing one does not make a lone survivor
    crowded = len(pool) > 1
    pool = [n for n in pool if n.btn != "destructive"]
    if not pool:
        return None, None, 0.0
    scored = []
    sims = {}
    for n in pool:
        nm = n.name or n.placeholder or n.text
        sims[id(n)] = similarity(nm, want)
        s = 0.5 * sims[id(n)]
        if target.button_role and n.btn == target.button_role and target.button_role != "normal":
            s += 0.2
        if target.position:
            pos = _rel_position(n, _innermost_container(n))
            if pos:
                d = ((pos[0] - target.position[0]) ** 2 + (pos[1] - target.position[1]) ** 2) ** 0.5
                s += 0.3 * max(0.0, 1.0 - d / 0.7)
        scored.append((s, n))
    scored.sort(key=lambda x: (-x[0], x[1].order))
    trace.append("relaxed: " + ", ".join(f"{n.name!r}={s:.2f}" for s, n in scored[:4]))
    if crowded:
        # only controls named like the target compete; position and button
        # class break ties between them, they never outvote the name
        named = [(s, n) for s, n in scored if sims[id(n)] > 0.0]
        if not named:
            return None, _refuse("low_confidence", f"no control in the container is named like {want!r}",
                                 "relaxed", [n for _, n in scored], trace), 0.0
        scored = named
    best_s, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if best_s < RELAXED_MIN_SCORE and crowded:
        return None, _refuse("low_confidence", f"no control in the container is close enough to {want!r}",
                             "relaxed", [n for _, n in scored], trace), 0.0
    if best_s - second < RELAXED_MIN_MARGIN:
        return None, _refuse("ambiguous", f"several controls could be the renamed {want!r}",
                             "relaxed", [n for _, n in scored], trace), 0.0
    return best, None, min(0.75, 0.4 + 0.4 * best_s)


def _gate(index: AXIndex, n: Node, target: Target, tier: str, conf: float, trace: List[str],
          observation: Any, min_confidence: float, locator: str = "") -> Grounding:
    if n.center is None:
        return _refuse("not_visible", f"{n.role} {n.name!r} is not on screen", tier, [n], trace)
    if not n.enabled:
        return _refuse("disabled", f"{n.role} {n.name!r} is disabled", tier, [n], trace)
    risk = target.risk()
    if n.btn == "destructive" and risk is not None and risk != "destructive":
        return _refuse("risk_escalation",
                       f"{n.role} {n.name!r} is destructive but the step expected a {risk} control",
                       tier, [n], trace)
    mroot = n.modal_root()
    win = n.window()
    for m in index.modals():
        if m is mroot or (win is not None and m.window() is not win and m.window() is not None):
            continue
        if mroot is None or m.order > mroot.order:
            buttons = [x for x in index.nodes if x.modal_root() is m and x.btn is not None]
            return _refuse("blocked_by_modal",
                           f"a {m.role} {m.name!r} is open above {n.role} {n.name!r}",
                           tier, [m] + buttons, trace)
    if conf < min_confidence:
        return _refuse("low_confidence", f"best match {n.name!r} has confidence {conf:.2f}",
                       tier, [n], trace)
    sid = None
    if observation is not None:
        for e in getattr(observation, "elements", []) or []:
            if tuple(getattr(e, "depth_path", ())) == n.path:
                sid = e.sid or None
                break
    trace.append(f"{tier}: matched {n.role} {n.name!r} conf={conf:.2f}")
    return Grounding(Match(n.role, n.name, n.center, n.path, tier, conf, n.btn, n.ident, sid,
                           locator), None, trace)


def ground(tree: Any, target: Target, *, viewport: Optional[Tuple[float, float, float, float]] = None,
           observation: Any = None, ocr: Optional[OcrProvider] = None,
           min_confidence: float = DEFAULT_MIN_CONFIDENCE, allow_relaxed: bool = True,
           max_nodes: int = MAX_NODES) -> Grounding:
    """Resolve ``target`` against the snapshot: one match, or a refusal."""
    trace: List[str] = []
    index = build_index(tree, viewport, max_nodes)
    if index.truncated:
        return _refuse("truncated", "the AX snapshot is partial (node budget or truncated flag); "
                       "refusing partial / first-match resolution", "index", trace=trace)
    # 0. the exact window
    windows = [n for n in index.nodes if n.role == "window"]
    scope = index.nodes
    if target.window:
        wins = [w for w in windows if norm(w.name) == norm(target.window)]
        if not wins:
            return _refuse("window_not_found", f"no window titled {target.window!r}", "window",
                           windows, trace)
        if len(wins) > 1:
            return _refuse("window_ambiguous", f"{len(wins)} windows titled {target.window!r}",
                           "window", wins, trace)
        w = wins[0]
        scope = [n for n in index.nodes if n is w or any(a is w for a in n.ancestors())]

    def done(n: Node, tier: str, conf: float, loc: str = "") -> Grounding:
        return _gate(index, n, target, tier, conf, trace, observation, min_confidence, loc)

    # 1. AX identifier (role must agree)
    if target.ax_id:
        c = [n for n in scope if n.ident == target.ax_id]
        if c and not any(_role_ok(n, target) for n in c):
            trace.append("ax_id: identifier now on a different role; ignored")
        c = [n for n in c if _role_ok(n, target)]
        if c:
            n, ref, conf = _pick(c, target, "ax_id", trace)
            return ref if ref else done(n, "ax_id", conf)
        trace.append("ax_id: not found")
    # 2. previous stable ID
    if target.prev_sid and observation is not None:
        el = observation.element(target.prev_sid) if hasattr(observation, "element") else None
        amb = el is not None and (getattr(el, "ambiguous", False) or
                                  target.prev_sid in getattr(observation, "ambiguous", set()))
        if el is not None and not amb:
            n = index.by_path(getattr(el, "depth_path", ()))
            if n is not None and n in scope and _role_ok(n, target) and \
                    (not target.name or norm(n.name) == norm(target.name)):
                return done(n, "sid", TIER_CONFIDENCE["sid"])
        trace.append("sid: missing, ambiguous or changed")
    # 3. recorded locators
    for loc in sorted(target.locators, key=lambda l: l.score):
        toks = loc.tokens
        got = _scoped(index, toks[0], Locator(toks[1:]), scope) if toks[0].kind == "id" and len(toks) > 1 \
            else evaluate(index, loc, scope)
        if not got:
            trace.append(f"locator {loc}: 0")
            continue
        if len(got) > 1:
            n, ref, _ = _pick(got, Target(container=target.container, row=target.row), "locator", trace)
            if ref:
                ref.refusal.message = f"recorded locator {loc} now matches {len(got)} elements"
                return ref
            got = [n]
        last = [t for t in toks if t.kind not in ("nth", "container")][-1:] or list(toks[-1:])
        conf = {"id": 0.99, "role": 0.95, "placeholder": 0.9, "label": 0.9, "text": 0.85,
                "title": 0.85, "role_only": 0.6}.get(last[0].kind, 0.8)
        if not last[0].exact:
            conf -= 0.05
        if any(t.kind == "nth" for t in toks):
            conf = min(conf, 0.7)
        return done(got[0], "locator", conf, str(loc))
    # 4. role + accessible name
    if target.name:
        c = [n for n in scope if _role_ok(n, target) and norm(n.name) == norm(target.name)]
        if c:
            n, ref, conf = _pick(c, target, "role_name", trace)
            return ref if ref else done(n, "role_name", conf)
        trace.append("role_name: 0")
    # 5. label / placeholder / help
    if target.label:
        c = [n for n in scope if _role_ok(n, target) and
             any(norm(x) == norm(target.label) for x in (n.name, n.placeholder, n.help) if x)]
        if c:
            n, ref, conf = _pick(c, target, "label", trace)
            return ref if ref else done(n, "label", conf)
        trace.append("label: 0")
    # 6. visible text, retargeted to the interactive ancestor
    want_text = target.text or (target.name if not target.label else None)
    if want_text:
        c: List[Node] = []
        for n in scope:
            if norm(n.text) != norm(want_text):
                continue
            if not n.interactive:
                n = next((a for a in n.ancestors() if a.role in _RETARGET), n)
            if _role_ok(n, target) and n not in c:
                c.append(n)
        if c:
            n, ref, conf = _pick(c, target, "text", trace)
            return ref if ref else done(n, "text", conf)
        trace.append("text: 0")
    # 7. renamed control in the same container
    if allow_relaxed:
        n, ref, conf = _relaxed(scope, target, trace)
        if ref:
            return ref
        if n is not None:
            return done(n, "relaxed", conf)
    # 8. OCR
    if ocr is not None and want_text:
        hits = ocr.find_text(want_text)
        if hits is None:
            trace.append("ocr: unavailable")
        else:
            hits = [h for h in hits if norm(h.text) == norm(want_text)]
            if len(hits) > 1:
                return Grounding(None, Refusal("ambiguous", f"OCR found {len(hits)} {want_text!r}",
                                               "ocr", [{"text": h.text, "frame": list(h.frame)}
                                                       for h in hits[:5]]), trace)
            if len(hits) == 1:
                h = hits[0]
                risk = target.risk()
                if button_role({"role": "button", "title": h.text}) == "destructive" and \
                        risk not in (None, "destructive"):
                    return Grounding(None, Refusal("risk_escalation", "OCR text is destructive", "ocr"), trace)
                conf = min(0.5, 0.5 * h.confidence)
                if conf < min_confidence:
                    return Grounding(None, Refusal("low_confidence", f"OCR-only match {h.text!r}", "ocr"),
                                     trace)
                f = h.frame
                return Grounding(Match("ocr_text", h.text, (int(f[0] + f[2] / 2), int(f[1] + f[3] / 2)),
                                       (), "ocr", conf), None, trace)
            trace.append("ocr: 0")
    if index.partial_lists:
        return _refuse("truncated", "not found, and a list shows only part of its rows: "
                       + "; ".join(index.partial_lists[:3]), "index", trace=trace)
    return _refuse("not_found", "no element matches the target", "", trace=trace)


def ground_dismiss(tree: Any, *, viewport: Optional[Tuple[float, float, float, float]] = None,
                   observation: Any = None) -> Grounding:
    """The single cancel-class button (Not now / Cancel / Close) of the
    topmost modal, for an unexpected interstitial. Refuses when there is no
    modal, no cancel button, or more than one."""
    index = build_index(tree, viewport)
    if index.truncated:
        return _refuse("truncated", "partial snapshot", "dismiss")
    mods = index.modals()
    if not mods:
        return _refuse("not_found", "no modal is open", "dismiss")
    m = max(mods, key=lambda x: x.order)
    cands = [n for n in index.nodes if n.modal_root() is m and n.btn == "cancel" and n.visible]
    if len(cands) != 1:
        return _refuse("ambiguous" if cands else "not_found",
                       f"{len(cands)} cancel buttons in {m.role} {m.name!r}", "dismiss",
                       cands or [m])
    n = cands[0]
    return _gate(index, n, Target(role=n.role, button_role="cancel"), "dismiss", 0.9, [],
                 observation, 0.0)


# ---------------------------------------------------------------------------
# observation hints (used by ax_compress.compress(..., locators=True))
# ---------------------------------------------------------------------------
def annotate_elements(tree: Any, elems: Sequence[Any],
                      viewport: Optional[Tuple[float, float, float, float]] = None) -> None:
    """Set ``e.locator`` on every interactive Element to its best verified
    locator (a short, uniqueness-checked handle a recorder can store)."""
    index = build_index(tree, viewport)
    if index.truncated:
        return
    for e in elems:
        if not getattr(e, "interactive", False) or e.offscreen:
            continue
        locs = generate_locators(index, e.depth_path, k=1)
        if locs:
            e.locator = str(locs[0])

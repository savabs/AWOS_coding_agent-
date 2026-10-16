"""
ax_compress.py — compress an AX / DOM tree into a short indexed element list.

See docs/specs/computer_use_foundations.md (T10). After A11y-Compressor
(arXiv 2605.00551: ~22% of raw tokens; redundancy-only pruning is neutral on
success, the full pipeline +5.1pp on a 32B model), with its pixel-band app
rules replaced by native modal roles (AXSheet / AXDialog).

Input node (JSON; AX or DOM, keys are tolerant):
    {"role": "AXButton", "title": "OK", "value": null, "description": "",
     "frame": {"x": 10, "y": 20, "w": 80, "h": 24},   # or "position"+"size"
     "enabled": true, "hidden": false, "focused": false,
     "children": [...]}

Pipeline:
  1. drop invisible: hidden, zero-area, or fully outside the viewport
  2. drop empty: no text and not interactive -> node removed, children promoted
  3. dedup: identical (role, text) siblings repeated -> keep first, count rest
  4. reading order: modal subtrees first, then top-to-bottom rows, left-right
  5. index + centre coordinates

Output: list of Element; ``render()`` gives one line per element:
    [3] button "Save" @(412,288)
``diff(prev, cur)`` compares two observations; an empty diff after an action
is a no-progress signal (open item G).

Additions for D16 (docs/research/rea_synthesis_2026-10-16.md, item 16):
  * clipping: a scroll area's frame clips its content, so rows scrolled out
    of view are dropped like off-viewport nodes ("visible range only");
  * list-like containers that hide rows report a range note
    (``showing a–b of N``) through ``collect()``;
  * every interactive element gets a **button role** (normal / primary /
    cancel / destructive, after Apple UIIntelligence), derived from an
    explicit role attribute, the AX subrole/default flags and title words;
  * elements carry identity hints (AX identifier, a value-free label, the
    labelled-ancestor chain) that ax_observe.py turns into stable IDs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

INTERACTIVE = {
    "button", "checkbox", "radiobutton", "textfield", "textarea", "combobox",
    "popupbutton", "menuitem", "menubutton", "link", "slider", "tab",
    "searchfield", "switch", "disclosuretriangle", "cell", "row", "incrementor",
    "input", "select", "option", "a",
}
TEXTUAL = {"statictext", "heading", "text", "label", "image", "img"}
MODAL = {"sheet", "dialog", "alertdialog", "systemdialog", "alert"}
CONTAINER = {"group", "scrollarea", "splitgroup", "layoutarea", "div", "span",
             "section", "generic", "unknown", "window", "application", "toolbar",
             "list", "outline", "table", "webarea", "form", "main", "nav", "body"}
ROW_BAND_PX = 10  # elements whose tops differ by less than this share a row
#: containers whose frame is the visible window onto larger content
CLIPPING = {"scrollarea"}
#: containers whose children are rows; hidden rows produce a range note
LISTLIKE = {"list", "outline", "table", "collection", "grid", "browser", "listbox", "tree"}
#: roles whose text is user-editable: their value is state, not identity
EDITABLE = {"textfield", "textarea", "searchfield", "combobox", "input", "slider",
            "checkbox", "radiobutton", "switch", "incrementor", "select"}
#: ancestors that always count for identity, labelled or not
ANCHOR_ROLES = MODAL | {"window", "application", "menu", "menubar", "toolbar", "list",
                        "outline", "table", "form", "tabgroup"}

# ---------------------------------------------------------------------------
# button roles (Apple UIIntelligence: normal / primary / cancel / destructive)
# ---------------------------------------------------------------------------
BUTTON_ROLES = ("normal", "primary", "cancel", "destructive")
#: words on a control that make clicking it irreversible. "Send"-type words
#: are here too: a sent message cannot be unsent (policy.py routes those to
#: the two-phase send path on top of this).
_DESTRUCTIVE_RE = re.compile(
    r"\b(delete|remove|erase|discard|don'?t save|do not save|close without saving|"
    r"empty (the )?(trash|bin)|move to (the )?(trash|bin)|format|sign out|log ?out|log off|"
    r"uninstall|wipe|reset|factory reset|revoke|deactivate|terminate|force quit|"
    r"overwrite|replace|revert|permanently|clear (all|history|data|everything)|"
    r"cancel (subscription|order|account|membership|plan|payment|booking|reservation)|"
    r"close account|unsubscribe|send(?! (to back|to front|backward|forward))|reply all|post|publish|submit|tweet|pay|buy|purchase|"
    r"place order|transfer|confirm (payment|purchase|order|transfer))\b", re.I)
_CANCEL_RE = re.compile(r"^(cancel|close|dismiss|not now|no thanks|no|back|later|skip|keep editing)$", re.I)
_PRIMARY_RE = re.compile(r"^(ok|okay|done|save|continue|next|open|apply|allow|yes|confirm|create|add)$", re.I)
_CLICKABLE = INTERACTIVE - {"textfield", "textarea", "searchfield", "slider", "incrementor"}


def _norm_title(s: str) -> str:
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u2026", "").replace("...", "")
    return " ".join(s.split()).strip(" .:!?")


def button_role(n: dict, role: Optional[str] = None) -> Optional[str]:
    """normal / primary / cancel / destructive for a clickable element, or
    None for a role that is not clicked (text fields, static text, ...).

    An explicit ``button_role`` / ``buttonRole`` attribute is honoured when it
    is *stricter* than the heuristics, never to downgrade them: AX values are
    untrusted, so an app (or page) labelling its "Delete" button "normal"
    still gets "destructive". Then the AX subrole / default / cancel flags,
    then title words."""
    role = role if role is not None else norm_role(n.get("role") or n.get("tag"))
    if role not in _CLICKABLE:
        return None
    names = [_norm_title(str(n.get(k))) for k in ("title", "name", "label", "description")
             if n.get(k) not in (None, "") and not isinstance(n.get(k), (dict, list))]
    names = [x for x in names if x]
    if str(n.get("type", "")).lower() == "submit":         # <button/input type=submit>
        names.append("submit")
    words = " ".join(names)
    title = names[0] if names else ""
    explicit = str(n.get("button_role") or n.get("buttonRole") or "").lower()
    subrole = str(n.get("subrole") or "")
    if role == "menubutton":       # opens a menu ("Format", "Edit"): never itself irreversible
        words = title = ""
    if explicit == "destructive" or _DESTRUCTIVE_RE.search(words):
        return "destructive"
    if (explicit == "cancel" or subrole in ("AXCancelButton", "AXCloseButton")
            or n.get("cancel") is True or _CANCEL_RE.match(title)):
        return "cancel"
    if (explicit == "primary" or subrole == "AXDefaultButton" or _is_default(n)
            or _PRIMARY_RE.match(title)):
        return "primary"
    return "normal"


def _is_default(n: dict) -> bool:
    return any(n.get(k) is True for k in ("default", "is_default", "AXDefault", "AXIsDefault")) \
        or str(n.get("subrole") or "") == "AXDefaultButton"


def norm_role(role: Optional[str]) -> str:
    r = (role or "unknown").strip()
    if r.startswith("AX"):
        r = r[2:]
    return r.lower().replace("_", "").replace(" ", "")


def _text(n: dict) -> str:
    for k in ("title", "name", "label", "value", "description", "placeholder", "help"):
        v = n.get(k)
        if v not in (None, "") and not isinstance(v, (dict, list)):
            s = " ".join(str(v).split())
            if s:
                return s[:120]
    return ""


def _frame(n: dict) -> Optional[Tuple[float, float, float, float]]:
    f = n.get("frame") or n.get("bounds")
    if isinstance(f, dict):
        return (float(f.get("x", 0)), float(f.get("y", 0)),
                float(f.get("w", f.get("width", 0))), float(f.get("h", f.get("height", 0))))
    if isinstance(f, (list, tuple)) and len(f) == 4:
        return tuple(float(v) for v in f)  # type: ignore[return-value]
    pos, size = n.get("position"), n.get("size")
    if pos and size:
        return (float(pos[0]), float(pos[1]), float(size[0]), float(size[1]))
    return None


def _label(n: dict, role: str) -> str:
    """Identity text: like _text, but an editable control's value is state,
    so it is left out (typing into a field keeps its ID)."""
    if role not in EDITABLE:
        return _text(n)
    for k in ("title", "name", "label", "description", "placeholder", "help", "id"):
        v = n.get(k)
        if v not in (None, "") and not isinstance(v, (dict, list)):
            s = " ".join(str(v).split())
            if s:
                return s[:120]
    return ""


def _descendant_text(n: dict, depth: int = 3) -> str:
    """First text below n (a row or cell is named by its content)."""
    for c in n.get("children") or []:
        if not isinstance(c, dict):
            continue
        t = _text(c) or (_descendant_text(c, depth - 1) if depth > 1 else "")
        if t:
            return t
    return ""


def _ident(n: dict) -> Optional[str]:
    for k in ("identifier", "AXIdentifier", "ax_id", "id"):
        v = n.get(k)
        if v not in (None, "") and not isinstance(v, (dict, list)):
            return str(v)[:120]
    return None


def _clip(viewport, fr):
    """Intersection of two (x, y, w, h) rectangles (None = unbounded)."""
    if fr is None:
        return viewport
    if viewport is None:
        return fr
    x1, y1 = max(viewport[0], fr[0]), max(viewport[1], fr[1])
    x2 = min(viewport[0] + viewport[2], fr[0] + fr[2])
    y2 = min(viewport[1] + viewport[3], fr[1] + fr[3])
    return (x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1))


def _visible(n: dict, viewport: Optional[Tuple[float, float, float, float]]) -> bool:
    if n.get("hidden") is True or n.get("visible") is False:
        return False
    fr = _frame(n)
    if fr is None:
        return True  # no geometry: keep, can't prove it's invisible
    x, y, w, h = fr
    if w <= 0 or h <= 0:
        return False
    if viewport:
        vx, vy, vw, vh = viewport
        if vw <= 0 or vh <= 0:
            return False
        if x + w <= vx or y + h <= vy or x >= vx + vw or y >= vy + vh:
            return False
    return True


@dataclass
class Element:
    index: int
    role: str
    text: str
    center: Optional[Tuple[int, int]]
    enabled: bool = True
    focused: bool = False
    modal: bool = False
    repeat: int = 1
    value: Optional[str] = None
    depth_path: Tuple[int, ...] = field(default=(), repr=False)
    button_role: Optional[str] = None     # normal|primary|cancel|destructive|None
    is_default: bool = False              # the window's default (Return) button
    sid: str = ""                         # stable ID, set by ax_observe.Observer
    ident: Optional[str] = field(default=None, repr=False)   # AX identifier / DOM id
    label: str = field(default="", repr=False)               # value-free identity text
    anc: Tuple[Tuple[str, str], ...] = field(default=(), repr=False)  # labelled ancestors

    def key(self) -> Tuple[str, str]:
        return (self.role, self.text)

    def render(self) -> str:
        s = f"[{self.sid or self.index}] {self.role}"
        if self.text:
            s += f' "{self.text}"'
        if self.value not in (None, "") and self.value != self.text:
            s += f" ={self.value!s}"[:60]
        if self.center:
            s += f" @({self.center[0]},{self.center[1]})"
        if not self.enabled:
            s += " disabled"
        if self.focused:
            s += " focused"
        if self.modal:
            s += " MODAL"
        if self.repeat > 1:
            s += f" x{self.repeat}"
        if self.button_role in ("primary", "cancel", "destructive"):
            s += f" {self.button_role}"
        return s

    def to_dict(self) -> dict:
        return {"i": self.index, "role": self.role, "text": self.text,
                "center": list(self.center) if self.center else None,
                "enabled": self.enabled, "focused": self.focused,
                "modal": self.modal, "repeat": self.repeat, "value": self.value,
                "button_role": self.button_role, "sid": self.sid or None}


def _collect(n: dict, out: List[Element], viewport, in_modal: bool,
             path: Tuple[int, ...], anc: Tuple[Tuple[str, str], ...] = (),
             notes: Optional[List[str]] = None) -> None:
    if not isinstance(n, dict) or not _visible(n, viewport):
        return
    role = norm_role(n.get("role") or n.get("tag"))
    modal = in_modal or role in MODAL
    text = _text(n)
    keep = (role in INTERACTIVE) or (role in MODAL) or (bool(text) and role not in CONTAINER) \
        or (role in TEXTUAL and bool(text))
    ident = _ident(n)
    label = _label(n, role)
    if keep:
        fr = _frame(n)
        center = (int(fr[0] + fr[2] / 2), int(fr[1] + fr[3] / 2)) if fr else None
        val = n.get("value")
        out.append(Element(
            index=-1, role=role, text=text, center=center,
            enabled=n.get("enabled", True) is not False,
            focused=bool(n.get("focused")), modal=modal,
            value=None if isinstance(val, (dict, list)) else (None if val is None else str(val)[:60]),
            depth_path=path, button_role=button_role(n, role), is_default=_is_default(n),
            ident=ident, label=label or (_descendant_text(n) if role in INTERACTIVE else ""),
            anc=anc,
        ))
    if role in ANCHOR_ROLES or ident or label:
        anc = anc + ((role, ident or label[:40]),)
    clip = _clip(viewport, _frame(n)) if role in CLIPPING else viewport
    kids = n.get("children") or []
    if notes is not None and role in LISTLIKE and kids:
        _range_note(n, role, text, kids, clip, notes)
    for i, c in enumerate(kids):
        _collect(c, out, clip, modal, path + (i,), anc, notes)


def _int(v) -> Optional[int]:
    try:
        return int(v) if v is not None and not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None


def _range_note(n: dict, role: str, text: str, kids: list, clip, notes: List[str]) -> None:
    """``list "Inbox": showing 5–24 of 340`` when rows are out of view. A
    lazily loaded AX table lists only some rows: ``total``/``row_count``
    (all rows) and ``offset``/``first_index`` (0-based index of the first
    child) let it say so."""
    rows = [c for c in kids if isinstance(c, dict)]
    vis = [i for i, c in enumerate(rows) if _visible(c, clip)]
    offset = _int(n.get("offset", n.get("first_index"))) or 0
    total = _int(n.get("total", n.get("row_count", n.get("count"))))
    total = max(total or 0, offset + len(rows))
    name = f'{role} "{text}"' if text else role
    if not vis:
        if total:
            notes.append(f"{name}: no rows visible of {total}")
        return
    a, b = offset + vis[0] + 1, offset + vis[-1] + 1
    if b - a + 1 < total:
        notes.append(f"{name}: showing {a}\u2013{b} of {total}")


def _dedup(elems: List[Element]) -> List[Element]:
    """Collapse runs of identical (role, text) elements that share a parent."""
    out: List[Element] = []
    for e in elems:
        prev = out[-1] if out else None
        if prev and prev.key() == e.key() and prev.depth_path[:-1] == e.depth_path[:-1] \
                and prev.modal == e.modal:
            prev.repeat += 1
            continue
        out.append(e)
    # global dedup of exact duplicates (same role, text, centre): AX trees often
    # expose the same control twice (e.g. a cell and its inner text)
    seen = set()
    final = []
    for e in out:
        k = (e.role, e.text, e.center)
        if e.text and e.center and k in seen:
            continue
        seen.add(k)
        final.append(e)
    return final


def _reading_order(elems: List[Element]) -> List[Element]:
    def key(e: Element):
        # modal subtrees first, the sheet/dialog element itself heading them
        group = 0 if e.role in MODAL else (1 if e.modal else 2)
        if e.center is None:
            return (group, 1e9, 1e9, e.depth_path)
        row = int(e.center[1] // ROW_BAND_PX)
        return (group, row, e.center[0], e.depth_path)
    return sorted(elems, key=key)


def collect(tree: Any, viewport: Optional[Tuple[float, float, float, float]] = None
            ) -> Tuple[List[Element], List[str]]:
    """Every visible element in reading order (no cap, no indices) plus the
    range notes of list-like containers that hide rows."""
    roots = tree if isinstance(tree, list) else [tree]
    raw: List[Element] = []
    notes: List[str] = []
    for i, r in enumerate(roots):
        _collect(r, raw, viewport, False, (i,), (), notes)
    return _reading_order(_dedup(raw)), notes


def compress(tree: Any, viewport: Optional[Tuple[float, float, float, float]] = None,
             max_elements: int = 300) -> List[Element]:
    elems = collect(tree, viewport)[0][:max_elements]
    for i, e in enumerate(elems):
        e.index = i
    return elems


def render(elems: List[Element]) -> str:
    return "\n".join(e.render() for e in elems)


def approx_tokens(text: str) -> int:
    """Rough tokenizer-free estimate (~4 chars/token)."""
    return max(1, (len(text) + 3) // 4)


def compression_ratio(tree: Any, elems: List[Element]) -> float:
    raw = json.dumps(tree, separators=(",", ":"))
    return approx_tokens(render(elems)) / approx_tokens(raw)


def diff(prev: List[Element], cur: List[Element]) -> Dict[str, List[str]]:
    """Element-level change between two observations (index-free)."""
    def sig(e: Element) -> str:
        return f"{e.role}|{e.text}|{e.value}|{e.enabled}|{e.modal}"
    p = [sig(e) for e in prev]
    c = [sig(e) for e in cur]
    ps, cs = set(p), set(c)
    return {"added": [s for s in c if s not in ps], "removed": [s for s in p if s not in cs]}


def no_progress(prev: List[Element], cur: List[Element]) -> bool:
    d = diff(prev, cur)
    return not d["added"] and not d["removed"]

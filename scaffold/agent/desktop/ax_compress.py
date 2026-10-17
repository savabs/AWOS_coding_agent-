"""
ax_compress.py — compress an AX / DOM tree into a short indexed element list.

See docs/specs/computer_use_foundations.md (T10). After A11y-Compressor
(arXiv 2605.00551: ~22% of raw tokens; redundancy-only pruning is neutral on
success, the full pipeline +5.1pp on a 32B model), with its pixel-band app
rules replaced by native modal roles (AXSheet / AXDialog).

Input node (JSON; AX or DOM, keys are tolerant):
    {"role": "AXButton", "title": "OK", "value": null, "description": "",
     "frame": {"x": 10, "y": 20, "w": 80, "h": 24},   # or "position"+"size"
     "enabled": true, "hidden": false, "focused": false, "selected": false,
     "children": [...]}

Pipeline:
  1. geometry: each node's visible rect is its frame clipped by every
     clipping ancestor (window, web area, scroll area) and the viewport. A
     subtree with no visible area is dropped. An element is kept when at
     least 5% of it is visible OR at least 100 pt² (a window: 500 pt²);
     a partly visible one is marked ``partial``; its click centre is the
     centre of the *visible* rect, never of a frame that is half scrolled
     away. Focused / selected elements are kept even when clipped out
     (``offscreen``, no centre), at most MAX_OFFSCREEN_KEEP of them.
  2. drop empty: no text and not interactive -> node removed, children promoted
  3. dedup: identical (role, text) *static text* siblings repeated -> keep
     first, count rest. Interactive elements are never collapsed: two "Edit"
     buttons are two targets.
  4. reading order: modal subtrees first, then top-to-bottom rows, left-right
  5. index + centre coordinates

Secrets: a secure text field (AXSecureTextField, ``type=password``), a
password field and a credit-card field (autocomplete ``cc-*``, card number /
CVV / CVC hints) never carry a value: it is masked at collection time and
rendered ``secure =<hidden>``. All other text and values pass through
``redact()``, which replaces API keys/tokens (sk-…, ghp_…, AKIA…, xox…-,
AIza…, JWTs, private-key blocks, ``token=…``, long hex/base64 runs) and
Luhn-valid card numbers.

Output: list of Element; ``render()`` gives one line per element, with
screen text JSON-quoted (``<`` and ``>`` escaped too) so a label cannot
forge attributes or close the observation fence:
    [3] button "Save" @(412,288)
``diff(prev, cur)`` compares two observations; an empty diff after an action
is a no-progress signal (open item G).

Identity hints for ax_observe.py: the AX identifier, a value-free label, the
labelled-ancestor chain, and (when the nearest ancestors are an unlabelled
row / list item) the row's content, so "Archive" in row "Invoice from Bob"
and "Archive" in row "Lunch?" are told apart by content, not by position.
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
    "input", "select", "option", "a", "securetextfield",
}
TEXTUAL = {"statictext", "heading", "text", "label", "image", "img"}
MODAL = {"sheet", "dialog", "alertdialog", "systemdialog", "alert"}
CONTAINER = {"group", "scrollarea", "splitgroup", "layoutarea", "div", "span",
             "section", "generic", "unknown", "window", "application", "toolbar",
             "list", "outline", "table", "webarea", "form", "main", "nav", "body"}
ROW_BAND_PX = 10  # elements whose tops differ by less than this share a row
#: containers whose frame is the visible window onto their content
CLIPPING = {"scrollarea", "window", "webarea"}
#: containers whose children are rows; hidden rows produce a range note
LISTLIKE = {"list", "outline", "table", "collection", "grid", "browser", "listbox", "tree"}
#: a row of a list: its content names the elements inside it
ROWLIKE = {"row", "cell", "listitem", "li", "tr", "outlinerow"}
#: roles whose text is user-editable: their value is state, not identity
EDITABLE = {"textfield", "textarea", "searchfield", "combobox", "input", "slider",
            "checkbox", "radiobutton", "switch", "incrementor", "select", "securetextfield"}
#: ancestors that always count for identity, labelled or not
ANCHOR_ROLES = MODAL | {"window", "application", "menu", "menubar", "toolbar", "list",
                        "outline", "table", "form", "tabgroup"}
#: visibility thresholds (pt²)
MIN_VISIBLE_FRACTION = 0.05
MIN_VISIBLE_AREA = 100.0
MIN_VISIBLE_AREA_WINDOW = 500.0
MAX_OFFSCREEN_KEEP = 10
#: stored value lengths: display (the focused field shows up to this) / raw
VALUE_KEEP = 2000
RAW_VALUE_KEEP = 20000

# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------
_SECRET_RES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"\b(?:sk|pk|rk)-(?:ant-|proj-|live_|test_)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    re.compile(r"(?<![A-Za-z0-9+/_\-])(?=[A-Za-z0-9+/_\-]*\d)(?=[A-Za-z0-9+/_\-]*[A-Za-z])"
               r"[A-Za-z0-9+/_\-]{40,}={0,2}"),
]
_KV_SECRET_RE = re.compile(
    r"\b(api[_\- ]?key|access[_\- ]?token|auth[_\- ]?token|token|secret|password|passwd|pwd)"
    r"(\s*[:=]\s*)(\S+)", re.I)
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")
REDACTED = "[redacted]"


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def redact(s: str) -> str:
    """Mask key / token / card-number patterns in screen text."""
    if not s:
        return s
    for rx in _SECRET_RES:
        s = rx.sub(REDACTED, s)
    s = _KV_SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + REDACTED, s)

    def card(m):
        digits = re.sub(r"\D", "", m.group(0))
        return "[redacted card]" if 13 <= len(digits) <= 19 and _luhn(digits) else m.group(0)
    return _CARD_RE.sub(card, s)


_SECURE_ROLES = {"securetextfield", "passwordfield"}
_SECURE_HINT = re.compile(
    r"\b(password|passcode|passphrase|card ?number|credit ?card|debit ?card|cvv2?|cvc2?|csc|"
    r"security code|expiry|expiration date|cc-?(number|csc|exp|name))\b", re.I)


def is_secure_node(n: dict, role: str) -> bool:
    """A field whose value must never reach the model: secure text fields,
    password inputs, credit-card fields."""
    if role in _SECURE_ROLES or str(n.get("subrole") or "") == "AXSecureTextField":
        return True
    if n.get("secure") is True or n.get("protected") is True:
        return True
    if str(n.get("type", "")).lower() == "password":
        return True
    ac = str(n.get("autocomplete") or n.get("autoComplete") or "").lower()
    if ac.startswith("cc-") or "password" in ac:
        return True
    if role in EDITABLE:
        hints = " ".join(str(n.get(k) or "") for k in
                         ("title", "name", "label", "placeholder", "description", "id", "identifier"))
        return bool(_SECURE_HINT.search(hints.replace("_", " ").replace("-", " ")))
    return False


# ---------------------------------------------------------------------------
# button roles (Apple UIIntelligence: normal / primary / cancel / destructive)
# ---------------------------------------------------------------------------
BUTTON_ROLES = ("normal", "primary", "cancel", "destructive")
#: words on a control that make clicking it irreversible. "Send"-type words
#: are here too: a sent message cannot be unsent (policy.py routes those to
#: the two-phase send path on top of this). Consent words (approve, accept,
#: allow, grant, authorize, merge) grant something on the owner's behalf.
_DESTRUCTIVE_RE = re.compile(
    r"\b(delete|remove|erase|discard|don'?t save|do not save|close without saving|"
    r"empty (the )?(trash|bin)|move to (the )?(trash|bin)|format|sign out|log ?out|log off|"
    r"uninstall|wipe|reset|factory reset|revoke|deactivate|terminate|force quit|"
    r"overwrite|replace|revert|permanently|clear (all|history|data|everything)|"
    r"cancel (subscription|order|account|membership|plan|payment|booking|reservation)|"
    r"close account|unsubscribe|send(?! (to back|to front|backward|forward))|reply all|post|publish|submit|tweet|pay|buy|purchase|"
    r"place order|transfer|confirm (payment|purchase|order|transfer)|"
    r"merge|(?<!don't )(?<!do not )(?<!never )(approve|accept|authori[sz]e|grant|allow)|always allow|"
    r"share|comment|deploy)\b", re.I)
_CANCEL_RE = re.compile(r"^(cancel|close|dismiss|not now|no thanks|no|back|later|skip|keep editing|"
                        r"don'?t allow|do not allow|deny|decline|reject)$", re.I)
_PRIMARY_RE = re.compile(r"^(ok|okay|done|save|continue|next|open|apply|yes|confirm|create|add)$", re.I)
_CLICKABLE = INTERACTIVE - {"textfield", "textarea", "searchfield", "slider", "incrementor",
                            "securetextfield"}


def _norm_title(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("…", "").replace("...", "")
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
    if _CANCEL_RE.match(title) and explicit != "destructive":
        return "cancel"            # "Don't Allow" / "Deny": declining is not consenting
    if explicit == "destructive" or _DESTRUCTIVE_RE.search(words):
        return "destructive"
    if (explicit == "cancel" or subrole in ("AXCancelButton", "AXCloseButton")
            or n.get("cancel") is True):
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


def _text(n: dict, keys=("title", "name", "label", "value", "description", "placeholder", "help")) -> str:
    for k in keys:
        v = n.get(k)
        if v not in (None, "") and not isinstance(v, (dict, list)):
            s = redact(" ".join(str(v).split()))
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


_LABEL_KEYS = ("title", "name", "label", "description", "placeholder", "help", "id")


def _label(n: dict, role: str) -> str:
    """Identity text: like _text, but an editable control's value is state,
    so it is left out (typing into a field keeps its ID)."""
    if role not in EDITABLE:
        return _text(n)
    return _text(n, _LABEL_KEYS)


def _descendant_text(n: dict, depth: int = 3) -> str:
    """First text below n (a row or cell is named by its content)."""
    for c in n.get("children") or []:
        if not isinstance(c, dict):
            continue
        t = _label(c, norm_role(c.get("role") or c.get("tag"))) or \
            (_descendant_text(c, depth - 1) if depth > 1 else "")
        if t:
            return t
    return ""


def _row_content(n: dict, limit: int = 3) -> str:
    """Value-free content of a row: the first few labels below it."""
    out: List[str] = []

    def walk(x, depth):
        for c in x.get("children") or []:
            if len(out) >= limit or not isinstance(c, dict):
                continue
            t = _label(c, norm_role(c.get("role") or c.get("tag")))
            if t:
                out.append(t[:40])
            elif depth > 1:
                walk(c, depth - 1)
    walk(n, 4)
    return " | ".join(out)[:120]


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


def _geometry(n: dict, clip, role: str):
    """(any_visible, keep, visible_rect, partial) for one node."""
    if n.get("hidden") is True or n.get("visible") is False:
        return False, False, None, False
    fr = _frame(n)
    if fr is None:
        return True, True, None, False       # no geometry: keep, can't prove it's invisible
    w, h = fr[2], fr[3]
    if w <= 0 or h <= 0:
        return False, False, None, False
    if clip is None:
        return True, True, fr, False
    if clip[2] <= 0 or clip[3] <= 0:
        return False, False, None, False
    vr = _clip(clip, fr)
    va, area = vr[2] * vr[3], w * h
    if va <= 0:
        return False, False, None, False
    floor = MIN_VISIBLE_AREA_WINDOW if role == "window" else MIN_VISIBLE_AREA
    keep = va >= MIN_VISIBLE_FRACTION * area or va >= floor
    return True, keep, vr, va < area * 0.999


def _visible(n: dict, viewport: Optional[Tuple[float, float, float, float]]) -> bool:
    role = norm_role(n.get("role") or n.get("tag"))
    return _geometry(n, viewport, role)[1]


def _q(s: str, limit: int) -> str:
    """JSON-quote screen text so it cannot forge attributes or fences."""
    s = str(s)
    if len(s) > limit:
        s = s[:limit] + "…"
    return json.dumps(s, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")


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
    ctx: str = field(default="", repr=False)                 # unlabelled row's content
    selected: bool = False
    partial: bool = False                 # partly clipped by a window / scroll area
    offscreen: bool = False               # kept because focused/selected; not clickable
    secure: bool = False                  # secure / password / card field: value masked
    masked: bool = False                  # value hidden (secure field or elevated app)
    had_value: bool = False               # a masked field holds something
    selected_text: Optional[str] = field(default=None, repr=False)
    raw_value: Optional[str] = field(default=None, repr=False)   # unredacted; never rendered
    ambiguous: bool = False               # set by ax_observe when its ID may have moved

    def key(self) -> Tuple[str, str]:
        return (self.role, self.text)

    @property
    def interactive(self) -> bool:
        return self.role in INTERACTIVE or self.button_role is not None

    def render(self, value_budget: int = 60) -> str:
        s = f"[{self.sid or self.index}] {self.role}"
        if self.text:
            s += " " + _q(self.text, 120)
        if self.masked:
            s += (" secure" if self.secure else "") + (" =<hidden>" if self.had_value else "")
        elif self.value not in (None, "") and self.value != self.text:
            s += " =" + _q(self.value, value_budget)
        if self.center:
            s += f" @({self.center[0]},{self.center[1]})"
        if not self.enabled:
            s += " disabled"
        if self.focused:
            s += " focused"
        if self.selected:
            s += " selected"
        if self.partial:
            s += " partial"
        if self.offscreen:
            s += " offscreen"
        if self.modal:
            s += " MODAL"
        if self.repeat > 1:
            s += f" x{self.repeat}"
        if self.button_role in ("primary", "cancel", "destructive"):
            s += f" {self.button_role}"
        if self.ambiguous:
            s += " ambiguous"
        return s

    def to_dict(self) -> dict:
        return {"i": self.index, "role": self.role, "text": self.text,
                "center": list(self.center) if self.center else None,
                "enabled": self.enabled, "focused": self.focused,
                "modal": self.modal, "repeat": self.repeat,
                "value": None if self.masked else self.value,
                "button_role": self.button_role, "sid": self.sid or None,
                "secure": self.secure, "partial": self.partial, "offscreen": self.offscreen}


def _make(n: dict, role: str, text: str, center, modal: bool, path, anc, ctx) -> Element:
    secure = is_secure_node(n, role)
    val = n.get("value")
    raw = None if isinstance(val, (dict, list)) or val is None else str(val)
    if secure:
        text = _label(n, role)                 # never fall back to the value
    label = _label(n, role)
    sel = n.get("selected_text", n.get("AXSelectedText"))
    return Element(
        index=-1, role=role, text=text, center=center,
        enabled=n.get("enabled", True) is not False,
        focused=bool(n.get("focused")), modal=modal,
        value=None if secure or raw is None else redact(raw)[:VALUE_KEEP],
        depth_path=path, button_role=button_role(n, role), is_default=_is_default(n),
        ident=_ident(n), label=label or (_descendant_text(n) if role in INTERACTIVE else ""),
        anc=anc, ctx=ctx, selected=n.get("selected") is True or n.get("AXSelected") is True,
        secure=secure, masked=secure, had_value=bool(raw),
        selected_text=None if secure or not isinstance(sel, str) or not sel else redact(sel)[:VALUE_KEEP],
        raw_value=None if secure or raw is None else raw[:RAW_VALUE_KEEP],
    )


def _keep_role(n: dict, role: str, text: str) -> bool:
    return (role in INTERACTIVE) or (role in MODAL) or (bool(text) and role not in CONTAINER) \
        or (role in TEXTUAL and bool(text))


def _offscreen_marked(n: dict, out: List[Element], st: dict, modal, path, anc, ctx) -> None:
    """Keep focused / selected nodes of an invisible subtree (capped)."""
    if not isinstance(n, dict) or n.get("hidden") is True or n.get("visible") is False:
        return
    role = norm_role(n.get("role") or n.get("tag"))
    if (n.get("focused") is True or n.get("selected") is True) and \
            st["offscreen"] < MAX_OFFSCREEN_KEEP and _keep_role(n, role, _text(n)):
        st["offscreen"] += 1
        e = _make(n, role, _text(n), None, modal or role in MODAL, path, anc, ctx)
        e.offscreen = True
        out.append(e)
    for i, c in enumerate(n.get("children") or []):
        _offscreen_marked(c, out, st, modal, path + (i,), anc, ctx)


def _collect(n: dict, out: List[Element], viewport, in_modal: bool,
             path: Tuple[int, ...], anc: Tuple[Tuple[str, str], ...] = (),
             notes: Optional[List[str]] = None, ctx: str = "",
             st: Optional[dict] = None, parent_role: str = "") -> None:
    if not isinstance(n, dict):
        return
    st = st if st is not None else {"offscreen": 0}
    role = norm_role(n.get("role") or n.get("tag"))
    modal = in_modal or role in MODAL
    any_vis, keep_geo, vr, partial = _geometry(n, viewport, role)
    if not any_vis:
        _offscreen_marked(n, out, st, in_modal, path, anc, ctx)
        return
    text = _text(n)
    ident = _ident(n)
    label = _label(n, role)
    if _keep_role(n, role, text):
        if keep_geo:
            center = (int(vr[0] + vr[2] / 2), int(vr[1] + vr[3] / 2)) if vr else None
            e = _make(n, role, text, center, modal, path, anc, ctx)
            e.partial = partial
            out.append(e)
        elif (n.get("focused") is True or n.get("selected") is True) \
                and st["offscreen"] < MAX_OFFSCREEN_KEEP:
            st["offscreen"] += 1
            e = _make(n, role, text, None, modal, path, anc, ctx)
            e.offscreen = True
            out.append(e)
    if role in ANCHOR_ROLES or ident or label:
        anc = anc + ((role, ident or label[:40]),)
        ctx = ""
    elif role in ROWLIKE or (parent_role in LISTLIKE and role in CONTAINER):
        ctx = _row_content(n)
    clip = _clip(viewport, _frame(n)) if role in CLIPPING else viewport
    kids = n.get("children") or []
    if notes is not None and role in LISTLIKE and kids:
        _range_note(n, role, text, kids, clip, notes)
    for i, c in enumerate(kids):
        _collect(c, out, clip, modal, path + (i,), anc, notes, ctx, st, role)


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
    name = f'{role} {_q(text, 60)}' if text else role
    if not vis:
        if total:
            notes.append(f"{name}: no rows visible of {total}")
        return
    a, b = offset + vis[0] + 1, offset + vis[-1] + 1
    if b - a + 1 < total:
        notes.append(f"{name}: showing {a}–{b} of {total}")


def _dedup(elems: List[Element]) -> List[Element]:
    """Collapse runs of identical (role, text) static-text siblings. An
    interactive element is never collapsed: each one is its own target."""
    out: List[Element] = []
    for e in elems:
        prev = out[-1] if out else None
        if prev and not e.interactive and not prev.interactive and not e.focused \
                and not e.selected and prev.key() == e.key() \
                and prev.depth_path[:-1] == e.depth_path[:-1] and prev.modal == e.modal:
            prev.repeat += 1
            continue
        out.append(e)
    # global dedup of exact duplicate static text (same role, text, centre):
    # AX trees often expose the same label twice
    seen = set()
    final = []
    for e in out:
        k = (e.role, e.text, e.center)
        if not e.interactive and e.text and e.center and k in seen:
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

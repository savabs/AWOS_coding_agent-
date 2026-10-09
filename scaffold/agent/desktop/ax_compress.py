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
"""
from __future__ import annotations

import json
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

    def key(self) -> Tuple[str, str]:
        return (self.role, self.text)

    def render(self) -> str:
        s = f"[{self.index}] {self.role}"
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
        return s

    def to_dict(self) -> dict:
        return {"i": self.index, "role": self.role, "text": self.text,
                "center": list(self.center) if self.center else None,
                "enabled": self.enabled, "focused": self.focused,
                "modal": self.modal, "repeat": self.repeat, "value": self.value}


def _collect(n: dict, out: List[Element], viewport, in_modal: bool,
             path: Tuple[int, ...]) -> None:
    if not isinstance(n, dict) or not _visible(n, viewport):
        return
    role = norm_role(n.get("role") or n.get("tag"))
    modal = in_modal or role in MODAL
    text = _text(n)
    keep = (role in INTERACTIVE) or (role in MODAL) or (bool(text) and role not in CONTAINER) \
        or (role in TEXTUAL and bool(text))
    if keep:
        fr = _frame(n)
        center = (int(fr[0] + fr[2] / 2), int(fr[1] + fr[3] / 2)) if fr else None
        val = n.get("value")
        out.append(Element(
            index=-1, role=role, text=text, center=center,
            enabled=n.get("enabled", True) is not False,
            focused=bool(n.get("focused")), modal=modal,
            value=None if isinstance(val, (dict, list)) else (None if val is None else str(val)[:60]),
            depth_path=path,
        ))
    for i, c in enumerate(n.get("children") or []):
        _collect(c, out, viewport, modal, path + (i,))


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


def compress(tree: Any, viewport: Optional[Tuple[float, float, float, float]] = None,
             max_elements: int = 300) -> List[Element]:
    roots = tree if isinstance(tree, list) else [tree]
    raw: List[Element] = []
    for i, r in enumerate(roots):
        _collect(r, raw, viewport, False, (i,))
    elems = _reading_order(_dedup(raw))[:max_elements]
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

# SPDX-License-Identifier: MIT
# Portions adapted from openadapt-capture, openadapt_capture/structural_observer/macos.py
# (the _AXRuntime PyObjC adapter: attribute / element-at-point / focused-element
# with frontmost-app fallback / pid / bounds; element fields; ancestry walk;
# bounded window-tree dump with secure-value omission).
# Copyright (c) 2025-2026 OpenAdapt.AI. MIT License: see ./NOTICE.
# AWOS changes: AXUIElementSetMessagingTimeout on the system-wide element and
# every app element (a hung app cannot stall the recorder); permission errors
# are PermissionMissing with remediation text; no psutil (NSRunningApplication
# gives bundle id + name); pydantic models replaced by dicts (schema.py); added
# web-area helpers (URL, same-role ordinal, page text/tables/alerts), AXDocument,
# AXDefaultButton, AXObserver plumbing and AXManualAccessibility for Chromium.
"""
ax_macos.py — PyObjC Accessibility reads for the native recorder.

The logic functions take any ``rt`` with ``attribute(el, name)``,
``children(el)`` and ``same(a, b)``, so they are unit-tested with a fake
runtime (tests/test_capture.py) and never need PyObjC or permissions there.
AXRuntime is the real PyObjC adapter; constructing it imports PyObjC and
preflights Accessibility (no prompt).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from .schema import (MAX_ANCESTRY_DEPTH, MAX_TREE_NODES, clip_text, is_editable, omit_tree_value,
                     sanitize_element)

AX_TIMEOUT_S = 0.25
PAGE_WALK_MAX = 6000
PAGE_TEXT_MAX = 6000
TABLE_ROWS_MAX = 60
CHROMIUM_BUNDLES = frozenset({"com.google.Chrome", "com.brave.Browser", "com.microsoft.edgemac",
                              "company.thebrowser.Browser", "org.chromium.Chromium"})


# ------------------------------------------------------------- logic (pure) --

def _str(rt, el, name) -> Optional[str]:
    return clip_text(rt.attribute(el, name))


def element_name(rt, el) -> Optional[str]:
    """Accessible name: AXTitle, AXDescription, the label element's value, placeholder."""
    for attr in ("AXTitle", "AXDescription"):
        v = _str(rt, el, attr)
        if v:
            return v
    label = rt.attribute(el, "AXTitleUIElement")
    if label is not None:
        v = _str(rt, label, "AXValue") or _str(rt, label, "AXTitle")
        if v:
            return v
    v = _str(rt, el, "AXPlaceholderValue")
    if v:
        return v
    if rt.attribute(el, "AXRole") in ("AXLink", "AXButton", "AXCell"):
        return text_of(rt, el, limit=200) or None
    return None


def element_value(rt, el, role: Optional[str], subrole: Optional[str]) -> Optional[str]:
    """AXValue as text — never read for a secure / password element."""
    if omit_tree_value(role, subrole):
        return None
    v = rt.attribute(el, "AXValue")
    if v is None or isinstance(v, bool):
        return None
    try:
        return str(v)[:20000]
    except Exception:
        return None


def describe(rt, el, *, with_value: bool = True) -> Dict[str, Any]:
    role = _str(rt, el, "AXRole")
    subrole = _str(rt, el, "AXSubrole")
    raw = {"role": role, "subrole": subrole, "name": element_name(rt, el),
           "identifier": _str(rt, el, "AXIdentifier")}
    if with_value and is_editable(raw):
        raw["value"] = element_value(rt, el, role, subrole)
    return sanitize_element(raw)


def ancestry(rt, el, depth: int = 12) -> List[Dict[str, Any]]:
    out, cur = [], el
    for _ in range(min(depth, MAX_ANCESTRY_DEPTH)):
        cur = rt.attribute(cur, "AXParent")
        if cur is None:
            break
        d = describe(rt, cur, with_value=False)
        if d:
            out.append(d)
    return out


def _url_text(v: Any) -> Optional[str]:
    if v is None:
        return None
    for meth in ("absoluteString",):
        f = getattr(v, meth, None)
        if callable(f):
            try:
                return str(f())
            except Exception:
                pass
    return str(v) if isinstance(v, str) else None


def web_context(rt, el) -> Tuple[Any, Optional[str]]:
    """(the AXWebArea containing el or None, its URL)."""
    cur = el
    for _ in range(MAX_ANCESTRY_DEPTH * 4):
        if cur is None:
            break
        if rt.attribute(cur, "AXRole") == "AXWebArea":
            return cur, _url_text(rt.attribute(cur, "AXURL"))
        cur = rt.attribute(cur, "AXParent")
    return None, None


def walk(rt, root, max_nodes: int = PAGE_WALK_MAX) -> Iterator[Any]:
    """Bounded pre-order DFS (document order)."""
    stack, seen = [root], 0
    while stack and seen < max_nodes:
        el = stack.pop()
        seen += 1
        yield el
        stack.extend(reversed(rt.children(el)))


def ordinal_in(rt, root, el, role: Optional[str], max_nodes: int = PAGE_WALK_MAX) -> Optional[int]:
    """Index of el among same-role elements under root, in document order."""
    if root is None or role is None:
        return None
    n = 0
    for e in walk(rt, root, max_nodes):
        if rt.same(e, el):
            return n
        if rt.attribute(e, "AXRole") == role:
            n += 1
    return None


def text_of(rt, el, limit: int = 2000) -> str:
    parts: List[str] = []
    for e in walk(rt, el, 400):
        if rt.attribute(e, "AXRole") == "AXStaticText":
            v = clip_text(rt.attribute(e, "AXValue"), 2000)
            if v:
                parts.append(v)
        if sum(len(p) for p in parts) > limit:
            break
    return " ".join(parts)[:limit]


def page_summary(rt, web_area, max_nodes: int = PAGE_WALK_MAX) -> Dict[str, Any]:
    """What a person sees on the page: title, alerts, status lines, visible text, tables."""
    lines: List[str] = []
    alerts: List[str] = []
    status: List[str] = []
    tables: List[dict] = []
    for e in walk(rt, web_area, max_nodes):
        role = rt.attribute(e, "AXRole")
        sub = rt.attribute(e, "AXSubrole")
        if sub == "AXApplicationAlert":
            t = text_of(rt, e, 500)
            if t:
                alerts.append(t)
        elif sub == "AXApplicationStatus":
            t = text_of(rt, e, 500)
            if t:
                status.append(t)
        if role == "AXStaticText":
            v = clip_text(rt.attribute(e, "AXValue"), 2000)
            if v:
                lines.append(v)
        elif role == "AXTable" and len(tables) < 20:
            rows = []
            for r in rt.children(e):
                if rt.attribute(r, "AXRole") != "AXRow":
                    continue
                rows.append([text_of(rt, c, 300) for c in rt.children(r)])
                if len(rows) > TABLE_ROWS_MAX:
                    break
            caption = _str(rt, e, "AXTitle") or _str(rt, e, "AXDescription") or ""
            tables.append({"caption": caption, "header": rows[0] if rows else [], "rows": rows[1:]})
    return {"title": _str(rt, web_area, "AXTitle") or "", "alerts": alerts, "status": status,
            "page_text": "\n".join(lines)[:PAGE_TEXT_MAX], "tables": tables}


def window_tree(rt, root, max_nodes: int = MAX_TREE_NODES) -> Tuple[Optional[dict], bool]:
    """Bounded raw tree (openadapt-capture _as_tree_node): secure values omitted. -> (tree, truncated)."""
    state = {"remaining": max_nodes, "truncated": False}

    def node(el) -> Optional[dict]:
        if state["remaining"] <= 0:
            state["truncated"] = True
            return None
        state["remaining"] -= 1
        d = describe(rt, el)
        kids = []
        for c in rt.children(el):
            k = node(c)
            if k is not None:
                kids.append(k)
            if state["truncated"]:
                break
        if kids:
            d["children"] = kids
        return d

    return node(root), state["truncated"]


def document_path(rt, window) -> Optional[str]:
    """The file a document window shows (AXDocument is a file:// URL string)."""
    v = _url_text(rt.attribute(window, "AXDocument"))
    if not v:
        return None
    if v.startswith("file://"):
        from urllib.parse import unquote, urlparse
        return unquote(urlparse(v).path)
    return v


# ------------------------------------------------------------ PyObjC runtime --

class AXRuntime:
    """The real adapter. Imports PyObjC lazily; refuses without Accessibility (no prompt)."""

    def __init__(self, frameworks: Optional[Dict[str, Any]] = None, timeout: float = AX_TIMEOUT_S):
        if frameworks is None:
            from .permissions import load_frameworks
            frameworks = load_frameworks(("ApplicationServices", "AppKit", "Quartz"))
        ax = frameworks["ApplicationServices"]
        if not bool(ax.AXIsProcessTrusted()):
            from .permissions import PermissionMissing
            raise PermissionMissing(["accessibility"])
        self.ax = ax
        self.appkit = frameworks.get("AppKit")
        self.cf = frameworks.get("Quartz")
        self.timeout = timeout
        self.system = ax.AXUIElementCreateSystemWide()
        ax.AXUIElementSetMessagingTimeout(self.system, float(timeout))
        self._apps: Dict[int, Any] = {}
        self._observers: Dict[int, Any] = {}

    # basic reads
    def attribute(self, el: Any, name: str) -> Any:
        if el is None:
            return None
        try:
            err, value = self.ax.AXUIElementCopyAttributeValue(el, name, None)
        except Exception:
            return None
        return value if err == self.ax.kAXErrorSuccess else None

    def children(self, el: Any) -> List[Any]:
        v = self.attribute(el, "AXChildren")
        if v is None or isinstance(v, str):
            return []
        try:
            return list(v)
        except TypeError:
            return []

    def same(self, a: Any, b: Any) -> bool:
        f = getattr(self.cf, "CFEqual", None)
        try:
            return bool(f(a, b)) if callable(f) else a == b
        except Exception:
            return a is b

    def element_at_point(self, x: float, y: float) -> Any:
        err, el = self.ax.AXUIElementCopyElementAtPosition(self.system, float(x), float(y), None)
        return el if err == self.ax.kAXErrorSuccess else None

    def pid(self, el: Any) -> Optional[int]:
        r = self.ax.AXUIElementGetPid(el, None)
        if isinstance(r, tuple):
            err, p = r
            return int(p) if err == self.ax.kAXErrorSuccess else None
        return int(r) if r is not None else None

    def app_element(self, pid: int) -> Any:
        a = self._apps.get(pid)
        if a is None:
            a = self.ax.AXUIElementCreateApplication(pid)
            self.ax.AXUIElementSetMessagingTimeout(a, float(self.timeout))
            self._apps[pid] = a
        return a

    def focused_application(self) -> Optional[int]:
        app = self.attribute(self.system, "AXFocusedApplication")
        if app is not None:
            return self.pid(app)
        try:
            return int(self.appkit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier())
        except Exception:
            return None

    def focused_element(self) -> Any:
        el = self.attribute(self.system, "AXFocusedUIElement")
        if el is not None:
            return el
        pid = self.focused_application()
        return self.attribute(self.app_element(pid), "AXFocusedUIElement") if pid else None

    def focused_window(self, pid: int) -> Any:
        return self.attribute(self.app_element(pid), "AXFocusedWindow")

    def app_info(self, pid: int) -> Dict[str, Any]:
        try:
            ra = self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            return {"pid": pid, "bundle_id": str(ra.bundleIdentifier() or ""), "name": str(ra.localizedName() or "")}
        except Exception:
            return {"pid": pid, "bundle_id": "", "name": ""}

    def enable_web_accessibility(self, pid: int) -> None:
        """Chromium/Electron build their AX tree only when asked (AXManualAccessibility)."""
        try:
            self.ax.AXUIElementSetAttributeValue(self.app_element(pid), "AXManualAccessibility", True)
        except Exception:
            pass

    # observers
    def observe(self, pid: int, notifications: List[str], callback: Callable[[Any, str], None],
                run_loop: Any) -> bool:
        """AXObserver on an app: callback(element, notification) on the run loop's thread."""
        if pid in self._observers:
            return True

        def _cb(observer, element, notification, refcon):
            try:
                callback(element, str(notification))
            except Exception:
                pass

        err, obs = self.ax.AXObserverCreate(pid, _cb, None)
        if err != self.ax.kAXErrorSuccess or obs is None:
            return False
        app = self.app_element(pid)
        for n in notifications:
            self.ax.AXObserverAddNotification(obs, app, n, None)
        src = self.ax.AXObserverGetRunLoopSource(obs)
        self.cf.CFRunLoopAddSource(run_loop, src, self.cf.kCFRunLoopDefaultMode)
        self._observers[pid] = (obs, src, _cb, run_loop)
        return True

    def close(self) -> None:
        for pid, (obs, src, _cb, loop) in list(self._observers.items()):
            try:
                self.cf.CFRunLoopRemoveSource(loop, src, self.cf.kCFRunLoopDefaultMode)
            except Exception:
                pass
        self._observers.clear()
        self._apps.clear()


def default_button(rt, window) -> Optional[Dict[str, Any]]:
    b = rt.attribute(window, "AXDefaultButton") if window is not None else None
    return describe(rt, b, with_value=False) if b is not None else None

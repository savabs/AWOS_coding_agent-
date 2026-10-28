"""
observe.py — live AX snapshots of one app window, plus the PyObjC runtime.

    rt = MacRuntime()                       # PermissionMissing without Accessibility (never prompts)
    snap = observe(rt, app="Safari")        # frontmost window of Safari (or app=None: frontmost app)
    snap.tree                               # dict tree for grounding.ground / ax_compress.compress
    snap.truncated                          # node budget or timeout hit -> grounding refuses
    snap.element(path)                      # the live AX element behind a tree path

Tree nodes use the keys ax_compress / grounding already read:
    {"role": "AXButton", "subrole": ..., "title": <accessible name>, "description": ...,
     "value": ... (never for a secure field), "placeholder": ..., "identifier": ...,
     "frame": {"x","y","w","h"} (screen points, top-left origin), "enabled", "focused",
     "selected", "url" (web areas and links), "secure": True on secure fields, "children": [...]}
Paths are grounding.build_index / ax_compress depth paths: the window is (0,), its k-th
child (0, k), and so on.

The runtime is an object with these methods (MacRuntime is the real one; the tests use a
fake with the same surface, so nothing here needs PyObjC or permissions in tests):

    read(el, names) -> {name: python value}   geometry as (x, y) / (w, h) tuples, URLs as str
    children(el) -> [el]           same(a, b) -> bool          pid(el) -> int | None
    app_element(pid)               frontmost() -> {"pid","bundle_id","name"} | None
    running_apps() -> [{"pid","bundle_id","name"}]             enable_web_accessibility(pid)
    all_apps() (optional) -> running_apps() including agents / system alert processes
    set_attr(el, name, value) -> bool          settable(el, name) -> bool
    actions(el) -> [str]           perform(el, action) -> bool
    activate(pid) -> bool          open_with(app_name, target) -> bool   (``open -a``)
    post_click(x, y) -> bool       post_text(pid, text) -> bool
    post_key(pid, keycode, flags=0) -> bool    scroll_wheel(x, y, lines) -> bool
    element_at_point(x, y)         can_post_events() -> bool
    now() -> float                 sleep(seconds)
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from ..ax_compress import compress, norm_role, render
from ..capture.ax_macos import AX_TIMEOUT_S, CHROMIUM_BUNDLES, AXRuntime
from ..capture.schema import clip_text, omit_tree_value

DEFAULT_MAX_NODES = 2000
DEFAULT_TIMEOUT_S = 8.0
NAME_FROM_CONTENT = {"link", "button", "cell", "radiobutton", "checkbox", "menuitem", "heading", "tab"}
NODE_ATTRS = ("AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue", "AXPlaceholderValue",
              "AXIdentifier", "AXDOMIdentifier", "AXHelp", "AXPosition", "AXSize", "AXEnabled",
              "AXFocused", "AXSelected", "AXURL", "AXRoleDescription", "AXTitleUIElement")
Path = Tuple[int, ...]


# ------------------------------------------------------------------ snapshot --

@dataclass
class Snapshot:
    tree: Optional[dict]
    truncated: bool
    app: Dict[str, Any]
    window_title: str = ""
    url: Optional[str] = None                     # first web area's URL
    handles: Dict[Path, Any] = field(default_factory=dict, repr=False)
    nodes: int = 0
    elapsed: float = 0.0
    reason: str = ""                              # budget | timeout | no_window | no_app
    focused_path: Optional[Path] = None
    web_paths: List[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.tree is not None

    @property
    def pid(self) -> Optional[int]:
        return self.app.get("pid")

    @property
    def window(self) -> Any:
        return self.handles.get((0,))

    def node(self, path: Sequence[int]) -> Optional[dict]:
        path = tuple(path)
        if not path or self.tree is None or path[0] != 0:
            return None
        n = self.tree
        for i in path[1:]:
            kids = n.get("children") or []
            if i >= len(kids):
                return None
            n = kids[i]
        return n

    def element(self, path: Sequence[int]) -> Any:
        return self.handles.get(tuple(path))

    def grounding_input(self) -> dict:
        """What grounding.ground accepts: a truncated snapshot is refused there."""
        return {"tree": self.tree, "truncated": self.truncated}

    def compressed(self, max_elements: int = 300):
        return compress(self.tree, max_elements=max_elements) if self.tree else []

    def render(self, max_elements: int = 300) -> str:
        return render(self.compressed(max_elements))

    def find(self, pred) -> List[Path]:
        out: List[Path] = []

        def walk(n, p):
            if pred(n):
                out.append(p)
            for i, c in enumerate(n.get("children") or []):
                walk(c, p + (i,))
        if self.tree:
            walk(self.tree, (0,))
        return out

    def summary(self) -> dict:
        return {"app": dict(self.app), "window": self.window_title, "url": self.url,
                "nodes": self.nodes, "truncated": self.truncated, "reason": self.reason,
                "elapsed_s": round(self.elapsed, 3)}


# ---------------------------------------------------------------- the walk --

def _s(v: Any, limit: int = 512) -> Optional[str]:
    return clip_text(v, limit) if isinstance(v, str) else None


def frame_of(raw: Dict[str, Any]) -> Optional[Dict[str, float]]:
    pos, size = raw.get("AXPosition"), raw.get("AXSize")
    try:
        if pos is not None and size is not None:
            return {"x": float(pos[0]), "y": float(pos[1]), "w": float(size[0]), "h": float(size[1])}
    except (TypeError, ValueError, IndexError):
        pass
    return None


def content_text(rt, el, limit: int = 300, max_nodes: int = 60) -> str:
    """Static text under el (a link / button / label is named by its content)."""
    parts: List[str] = []
    stack, seen = [el], 0
    while stack and seen < max_nodes:
        e = stack.pop()
        seen += 1
        r = rt.read(e, ("AXRole", "AXValue", "AXTitle"))
        if r.get("AXRole") == "AXStaticText":
            v = _s(r.get("AXValue")) or _s(r.get("AXTitle"))
            if v:
                parts.append(v)
        stack.extend(reversed(rt.children(e)))
        if sum(len(p) for p in parts) > limit:
            break
    return " ".join(parts)[:limit]


def make_node(rt, el, raw: Dict[str, Any]) -> dict:
    """One tree node from the attributes of el (secure values never read into it)."""
    role = _s(raw.get("AXRole")) or "AXUnknown"
    subrole = _s(raw.get("AXSubrole"))
    nr = norm_role(role)
    secure = omit_tree_value(role, subrole)
    title = _s(raw.get("AXTitle"))
    desc = _s(raw.get("AXDescription"))
    name = title or desc
    if not name and raw.get("AXTitleUIElement") is not None:
        lab = raw["AXTitleUIElement"]
        lr = rt.read(lab, ("AXValue", "AXTitle"))
        name = _s(lr.get("AXValue")) or _s(lr.get("AXTitle")) or (content_text(rt, lab, 200) or None)
    if not name:
        name = _s(raw.get("AXPlaceholderValue"))
    if not name and nr in NAME_FROM_CONTENT:
        name = content_text(rt, el) or None
    n: Dict[str, Any] = {"role": role}
    if subrole:
        n["subrole"] = subrole
    if name:
        n["title"] = name
    if desc and desc != name:
        n["description"] = desc
    for key, attr in (("placeholder", "AXPlaceholderValue"), ("help", "AXHelp"),
                      ("roledescription", "AXRoleDescription")):
        v = _s(raw.get(attr))
        if v:
            n[key] = v
    ident = _s(raw.get("AXIdentifier")) or _s(raw.get("AXDOMIdentifier"))
    if ident:
        n["identifier"] = ident
    if secure:
        n["secure"] = True                      # value deliberately absent
    else:
        v = raw.get("AXValue")
        if isinstance(v, bool):
            n["value"] = int(v)
        elif isinstance(v, (int, float)):
            n["value"] = v
        elif isinstance(v, str):
            n["value"] = v[:20000]
    fr = frame_of(raw)
    if fr:
        n["frame"] = fr
    if raw.get("AXEnabled") is False:
        n["enabled"] = False
    if raw.get("AXFocused") is True:
        n["focused"] = True
    if raw.get("AXSelected") is True:
        n["selected"] = True
    url = raw.get("AXURL")
    if isinstance(url, str) and url:
        n["url"] = url
    return n


def resolve_app(rt, app: Union[None, int, str, dict]) -> Optional[Dict[str, Any]]:
    """None -> frontmost; int -> pid; str -> bundle id or localized name; dict -> as is."""
    if isinstance(app, dict):
        return app
    if app is None:
        return rt.frontmost()
    apps = rt.running_apps()
    if isinstance(app, int) and not isinstance(app, bool):
        return next((a for a in apps if a.get("pid") == app), {"pid": app, "bundle_id": "", "name": ""})
    want = str(app).strip().lower()
    for a in apps:
        if want in (str(a.get("bundle_id", "")).lower(), str(a.get("name", "")).lower()):
            return a
    return None


def resolve_window(rt, app_el, window: Any = None) -> Any:
    if window is not None and not isinstance(window, str):
        return window
    wins = rt.read(app_el, ("AXWindows",)).get("AXWindows") or []
    if isinstance(window, str):
        hits = [w for w in wins if _s(rt.read(w, ("AXTitle",)).get("AXTitle")) == window]
        return hits[0] if len(hits) == 1 else None
    r = rt.read(app_el, ("AXFocusedWindow", "AXMainWindow"))
    return r.get("AXFocusedWindow") or r.get("AXMainWindow") or (wins[0] if wins else None)


def window_list(rt, pid: int) -> List[Any]:
    return list(rt.read(rt.app_element(pid), ("AXWindows",)).get("AXWindows") or [])


def snapshot_element(rt, root, *, app: Optional[Dict[str, Any]] = None,
                     max_nodes: int = DEFAULT_MAX_NODES, timeout: float = DEFAULT_TIMEOUT_S) -> Snapshot:
    """Bounded pre-order walk of root (a window or any element)."""
    t0 = rt.now()
    deadline = t0 + max(0.1, float(timeout))
    handles: Dict[Path, Any] = {}
    state = {"n": 0, "truncated": False, "reason": "", "focused": None, "web": [], "url": None}

    def visit(el, path: Path) -> Optional[dict]:
        if state["n"] >= max_nodes:
            state["truncated"], state["reason"] = True, "budget"
            return None
        if rt.now() > deadline:
            state["truncated"], state["reason"] = True, "timeout"
            return None
        state["n"] += 1
        raw = rt.read(el, NODE_ATTRS)
        node = make_node(rt, el, raw)
        handles[path] = el
        if node.get("focused") and state["focused"] is None:
            state["focused"] = path
        if node["role"] == "AXWebArea":
            state["web"].append(path)
            if state["url"] is None and node.get("url"):
                state["url"] = node["url"]
        kids = []
        # Safari/WebKit tables expose every cell twice: under AXRow and again under AXColumn.
        # Walking both makes each link look like two identical controls (grounding then
        # refuses as ambiguous), so column subtrees are not descended.
        children = [] if node["role"] == "AXColumn" else rt.children(el)
        for i, c in enumerate(children):
            k = visit(c, path + (len(kids),))
            if k is None:
                break
            kids.append(k)
        if kids:
            node["children"] = kids
        return node

    tree = visit(root, (0,))
    title = (tree or {}).get("title", "") if tree else ""
    return Snapshot(tree, state["truncated"], dict(app or {}), title or "", state["url"], handles,
                    state["n"], rt.now() - t0, state["reason"], state["focused"], state["web"])


def observe(rt, app: Union[None, int, str, dict] = None, window: Any = None, *,
            max_nodes: int = DEFAULT_MAX_NODES, timeout: float = DEFAULT_TIMEOUT_S) -> Snapshot:
    """Snapshot of the frontmost (or named) app's focused (or given / titled) window."""
    info = resolve_app(rt, app)
    if not info or info.get("pid") is None:
        return Snapshot(None, False, {"query": str(app)}, reason="no_app")
    pid = int(info["pid"])
    if info.get("bundle_id") in CHROMIUM_BUNDLES:
        rt.enable_web_accessibility(pid)
    win = resolve_window(rt, rt.app_element(pid), window)
    if win is None:
        return Snapshot(None, False, dict(info), reason="no_window")
    return snapshot_element(rt, win, app=info, max_nodes=max_nodes, timeout=timeout)


# ------------------------------------------------------------ PyObjC runtime --

def _typename(v: Any) -> str:
    return type(v).__name__


class MacRuntime(AXRuntime):
    """The real runtime: AXRuntime reads + writes, actions, app activation and CGEvents.

    Constructing it imports PyObjC and preflights Accessibility (PermissionMissing, no prompt).
    """

    def __init__(self, frameworks: Optional[Dict[str, Any]] = None, timeout: float = AX_TIMEOUT_S):
        if frameworks is None:
            from ..capture.permissions import load_frameworks
            frameworks = load_frameworks(("ApplicationServices", "AppKit", "Quartz"))
        super().__init__(frameworks, timeout)
        self.q = frameworks["Quartz"]

    # -- conversion ---------------------------------------------------------
    def _conv(self, v: Any) -> Any:
        if v is None:
            return None
        tn = _typename(v)
        if tn == "AXValueRef":
            ax = self.ax
            try:
                t = ax.AXValueGetType(v)
                if t == ax.kAXValueCGPointType:
                    ok, p = ax.AXValueGetValue(v, t, None)
                    return (float(p.x), float(p.y)) if ok else None
                if t == ax.kAXValueCGSizeType:
                    ok, s = ax.AXValueGetValue(v, t, None)
                    return (float(s.width), float(s.height)) if ok else None
                if t == ax.kAXValueCFRangeType:
                    ok, r = ax.AXValueGetValue(v, t, None)
                    return (int(r.location), int(r.length)) if ok else None
            except Exception:
                return None
            return None                                   # AXError placeholder and others
        if tn == "AXUIElementRef":
            return v
        f = getattr(v, "absoluteString", None)            # NSURL
        if callable(f):
            try:
                return str(f())
            except Exception:
                return None
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v
        if isinstance(v, str):
            return str(v)
        try:
            return [self._conv(x) for x in v]             # NSArray
        except TypeError:
            return None

    # -- reads ----------------------------------------------------------------
    def read(self, el: Any, names: Iterable[str]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if el is None:
            return out
        for n in names:
            v = self.attribute(el, n)
            if v is not None:
                out[n] = self._conv(v)
        return out

    def frontmost(self) -> Optional[Dict[str, Any]]:
        try:
            a = self.appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
            return {"pid": int(a.processIdentifier()), "bundle_id": str(a.bundleIdentifier() or ""),
                    "name": str(a.localizedName() or "")}
        except Exception:
            return None

    def running_apps(self) -> List[Dict[str, Any]]:
        out = []
        try:
            for a in self.appkit.NSWorkspace.sharedWorkspace().runningApplications():
                if int(a.activationPolicy()) != 0:          # regular apps only
                    continue
                out.append({"pid": int(a.processIdentifier()), "bundle_id": str(a.bundleIdentifier() or ""),
                            "name": str(a.localizedName() or "")})
        except Exception:
            pass
        return out

    def all_apps(self) -> List[Dict[str, Any]]:
        """Every running process with a bundle, agents included (UserNotificationCenter,
        SecurityAgent): interrupts.py looks for system alerts over our window here."""
        out = []
        try:
            for a in self.appkit.NSWorkspace.sharedWorkspace().runningApplications():
                out.append({"pid": int(a.processIdentifier()), "bundle_id": str(a.bundleIdentifier() or ""),
                            "name": str(a.localizedName() or "")})
        except Exception:
            pass
        return out

    # -- writes / actions -------------------------------------------------------
    def set_attr(self, el: Any, name: str, value: Any) -> bool:
        try:
            return self.ax.AXUIElementSetAttributeValue(el, name, value) == self.ax.kAXErrorSuccess
        except Exception:
            return False

    def settable(self, el: Any, name: str) -> bool:
        try:
            err, ok = self.ax.AXUIElementIsAttributeSettable(el, name, None)
            return err == self.ax.kAXErrorSuccess and bool(ok)
        except Exception:
            return False

    def actions(self, el: Any) -> List[str]:
        try:
            err, names = self.ax.AXUIElementCopyActionNames(el, None)
            return [str(x) for x in names] if err == self.ax.kAXErrorSuccess and names else []
        except Exception:
            return []

    def perform(self, el: Any, action: str) -> bool:
        try:
            return self.ax.AXUIElementPerformAction(el, action) == self.ax.kAXErrorSuccess
        except Exception:
            return False

    def activate(self, pid: int) -> bool:
        try:
            ra = self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
            return bool(ra.activateWithOptions_(1 << 1))     # NSApplicationActivateIgnoringOtherApps
        except Exception:
            return False

    def open_with(self, app_name: str, target: str) -> bool:
        try:
            return subprocess.run(["open", "-a", app_name, target], capture_output=True,
                                  timeout=20).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    # -- synthetic input (needs the helper's Accessibility grant; never prompts) -----
    def can_post_events(self) -> bool:
        f = getattr(self.q, "CGPreflightPostEventAccess", None)
        try:
            return bool(f()) if callable(f) else bool(self.ax.AXIsProcessTrusted())
        except Exception:
            return False

    def post_click(self, x: float, y: float) -> bool:
        q = self.q
        try:
            for kind in (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp):
                ev = q.CGEventCreateMouseEvent(None, kind, (float(x), float(y)), q.kCGMouseButtonLeft)
                q.CGEventPost(q.kCGHIDEventTap, ev)
                time.sleep(0.03)
            return True
        except Exception:
            return False

    def post_text(self, pid: int, text: str) -> bool:
        q = self.q
        try:
            for i in range(0, len(text), 16):
                chunk = text[i:i + 16]
                for down in (True, False):
                    ev = q.CGEventCreateKeyboardEvent(None, 0, down)
                    q.CGEventKeyboardSetUnicodeString(ev, len(chunk.encode("utf-16-le")) // 2, chunk)
                    q.CGEventPostToPid(int(pid), ev)
                time.sleep(0.01)
            return True
        except Exception:
            return False

    def post_key(self, pid: int, keycode: int, flags: int = 0) -> bool:
        q = self.q
        try:
            for down in (True, False):
                ev = q.CGEventCreateKeyboardEvent(None, int(keycode), down)
                if flags:
                    q.CGEventSetFlags(ev, int(flags))
                q.CGEventPostToPid(int(pid), ev)
            return True
        except Exception:
            return False

    def scroll_wheel(self, x: float, y: float, lines: int) -> bool:
        q = self.q
        try:
            ev = q.CGEventCreateScrollWheelEvent(None, q.kCGScrollEventUnitLine, 1, int(lines))
            q.CGEventSetLocation(ev, (float(x), float(y)))
            q.CGEventPost(q.kCGHIDEventTap, ev)
            return True
        except Exception:
            return False

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, s: float) -> None:
        # Spin the run loop, not time.sleep: NSWorkspace.runningApplications (and AX
        # notifications) only refresh while the run loop runs, so a CLI process that just
        # sleeps never sees an app it launched.
        try:
            self.appkit.NSRunLoop.currentRunLoop().runUntilDate_(
                self.appkit.NSDate.dateWithTimeIntervalSinceNow_(max(0.0, s)))
        except Exception:
            time.sleep(max(0.0, s))

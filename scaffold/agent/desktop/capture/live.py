"""
live.py — the native macOS recorder loop (written for AWOS on top of the adapted
ax_macos / input_tap modules and the new fsevents watcher).

    rec = MacRecorder(adapter.feed, watch=[home])
    rec.run(stop_event, commands)        # blocking, on the calling thread's CFRunLoop
    # or: rec.start(); ...; rec.stop()    # the same loop on a background thread

Everything runs on ONE thread's CFRunLoop: the listen-only event tap, the
AXObserver sources of the apps the user brings to the front, the FSEvents
stream, and a ~150 ms poll of the focused application (AXFocusedApplication,
which unlike NSWorkspace.frontmostApplication stays fresh without an AppKit
main loop). So NativeAdapter.feed is never called concurrently. Commands
("pause", "resume", "stop") arrive through a queue from any thread.

Constructing/opening it needs macOS + PyObjC + Accessibility + Input
Monitoring; each missing piece raises a CaptureUnavailable / PermissionMissing
whose message says exactly what to grant. Nothing here prompts unless
request_permissions=True.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import ax_macos as axm
from .adapter import BROWSER_BUNDLES
from .permissions import load_frameworks, require_permissions

NOTIFICATIONS = ["AXFocusedUIElementChanged", "AXValueChanged", "AXFocusedWindowChanged", "AXTitleChanged",
                 "AXLoadComplete"]


def find_web_area(rt, root, max_nodes: int = 3000):
    for e in axm.walk(rt, root, max_nodes):
        if rt.attribute(e, "AXRole") == "AXWebArea":
            return e
    return None


class MacRecorder:
    def __init__(self, feed: Callable[[dict], None], *, watch: List[str], poll_s: float = 0.15,
                 request_permissions: bool = False, frameworks: Optional[Dict[str, Any]] = None):
        self.feed = feed
        self.watch = list(watch)
        self.poll_s = poll_s
        self.request_permissions = request_permissions
        self.fw = frameworks
        self.commands: "queue.Queue[str]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.rt = None
        self._tap = self._fs = self._loop = None
        self._front_pid: Optional[int] = None
        self._apps: Dict[int, dict] = {}
        self._ord_cache: tuple = (None, None)
        self.errors: List[str] = []

    # --------------------------------------------------------------- setup --
    def open(self) -> None:
        from .fsevents import FSEventsWatcher
        from .input_tap import ListenOnlyTap
        fw = self.fw or load_frameworks()
        require_permissions(fw, request=self.request_permissions)
        self.fw = fw
        self.rt = axm.AXRuntime(fw)
        q = fw["Quartz"]
        self._loop = q.CFRunLoopGetCurrent()
        self._tap = ListenOnlyTap(self._on_input, frameworks=fw)
        self._tap.schedule(self._loop)
        self._fs = FSEventsWatcher(self.watch, self._push, frameworks=fw)
        self._fs.schedule(self._loop)

    def close(self) -> None:
        for part in (self._tap, self._fs, self.rt):
            try:
                if part is not None:
                    part.close()
            except Exception as exc:
                self.errors.append(f"close: {exc}")
        self._tap = self._fs = self.rt = None

    def _push(self, rec: dict) -> None:
        rec.setdefault("t", time.time())
        try:
            self.feed(rec)
        except Exception as exc:                      # one bad record must not end the session
            self.errors.append(f"{rec.get('src')}: {exc}")

    # ---------------------------------------------------------------- loop --
    def run(self, stop: Optional[threading.Event] = None) -> None:
        stop = stop or self._stop
        self.open()
        q = self.fw["Quartz"]
        try:
            while not stop.is_set():
                q.CFRunLoopRunInMode(q.kCFRunLoopDefaultMode, self.poll_s, False)
                self._poll_front()
                while True:
                    try:
                        cmd = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    if cmd in ("pause", "resume"):
                        self._push({"src": "session", "event": cmd, "reason": "auth"})
                    elif cmd == "stop":
                        stop.set()
        finally:
            self.close()

    def start(self, sink: Optional[Callable[[dict], None]] = None) -> None:
        if sink is not None:
            self.feed = sink
        self._thread = threading.Thread(target=self.run, name="awos-capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ----------------------------------------------------------- app focus --
    def _app(self, pid: Optional[int]) -> dict:
        if pid is None:
            return {}
        if pid not in self._apps:
            info = self.rt.app_info(pid)
            if info.get("bundle_id") in axm.CHROMIUM_BUNDLES:
                self.rt.enable_web_accessibility(pid)
            self.rt.observe(pid, NOTIFICATIONS, self._on_ax, self._loop)
            self._apps[pid] = info
        return self._apps[pid]

    def _poll_front(self) -> None:
        pid = self.rt.focused_application()
        if pid is None or pid == self._front_pid:
            return
        self._front_pid = pid
        info = self._app(pid)
        rec = {"src": "app", **info}
        win = self.rt.focused_window(pid)
        if info.get("bundle_id") in BROWSER_BUNDLES and win is not None:
            web = find_web_area(self.rt, win)
            rec["url"] = axm.web_context(self.rt, web)[1] if web is not None else None
        self._push(rec)
        if win is not None and info.get("bundle_id") not in BROWSER_BUNDLES:
            doc = axm.document_path(self.rt, win)
            if doc:
                self._push({"src": "ax", "notification": "AXFocusedWindowChanged", "bundle_id": info.get("bundle_id"),
                            "pid": pid, "window": {"document": doc, "title": axm.clip_text(self.rt.attribute(win, "AXTitle")) or ""}})

    # --------------------------------------------------------------- input --
    def _element_record(self, el) -> dict:
        rt = self.rt
        desc = axm.describe(rt, el)
        web, url = axm.web_context(rt, el)
        if web is not None:
            last_el, last_ord = self._ord_cache
            if last_el is not None and rt.same(last_el, el):
                desc["ordinal"] = last_ord
            else:
                o = axm.ordinal_in(rt, web, el, desc.get("role"))
                if o is not None:
                    desc["ordinal"] = o
                self._ord_cache = (el, desc.get("ordinal"))
        return {"element": desc, "url": url}

    def _on_input(self, rec: dict) -> None:
        rt = self.rt
        if rec.get("src") == "mouse":
            el = rt.element_at_point(rec["x"], rec["y"])
            if el is not None:
                pid = rt.pid(el)
                rec.update({"pid": pid, "bundle_id": self._app(pid).get("bundle_id")}, **self._element_record(el))
        elif rec.get("src") == "key":
            el = rt.focused_element()
            if el is not None:
                pid = rt.pid(el)
                er = self._element_record(el)
                rec.update({"pid": pid, "bundle_id": self._app(pid).get("bundle_id"), "url": er["url"],
                            "focused": er["element"],
                            "default_button": axm.default_button(rt, rt.attribute(el, "AXWindow"))})
        self._push(rec)

    def _on_ax(self, element, notification: str) -> None:
        rt = self.rt
        pid = rt.pid(element)
        info = self._app(pid)
        rec: Dict[str, Any] = {"src": "ax", "notification": notification, "pid": pid,
                               "bundle_id": info.get("bundle_id")}
        browser = info.get("bundle_id") in BROWSER_BUNDLES
        if notification == "AXLoadComplete":
            web = element if rt.attribute(element, "AXRole") == "AXWebArea" else axm.web_context(rt, element)[0]
            if web is None:
                return
            rec["url"] = axm.web_context(rt, web)[1]
            rec["window"] = axm.page_summary(rt, web)
            self._ord_cache = (None, None)
        elif notification in ("AXValueChanged", "AXFocusedUIElementChanged"):
            if not browser:
                return          # files apps: typing is captured by the saved file, never by reading the document
            rec.update(self._element_record(element))
        elif browser:                                            # tab switch / title change
            web = find_web_area(rt, element)
            rec["url"] = axm.web_context(rt, web)[1] if web is not None else None
        elif notification == "AXFocusedWindowChanged":
            rec["window"] = {"document": axm.document_path(rt, element) or "",
                             "title": axm.clip_text(rt.attribute(element, "AXTitle")) or ""}
        else:
            return
        self._push(rec)

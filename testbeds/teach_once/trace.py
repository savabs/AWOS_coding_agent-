"""
trace.py — the semantic DEMONSTRATION TRACE format and the Recorder API.

A demonstration is recorded ONCE (the user does the task on instance i01) and
stored as a semantic trace: what the user did in terms of apps, accessible
controls (role + name + label), values, files — not pixels or coordinates.

Trace (JSON, schema "awos.demo_trace/1"):

    {"schema": "awos.demo_trace/1",
     "task": "t3_invoices", "instance": "i01", "title": "...", "note": "...",
     "context": {"today": "2026-03-12"},          # clock etc. at record time
     "recorder": {"kind": "scripted" | "macos_ax_fsevents", "version": 1},
     "environment": {"apps": [{"name": "inbox", "kind": "web", "base_url": "..."},
                              {"name": "Finder", "kind": "files"}],
                     "home": "<path the trace's relative paths are under>"},
     "start": {"files": {rel: sha256}, "text": {rel: text (small csv/txt)}},
     "steps": [Step, ...],
     "end":   {"files": {...}, "text": {...}, "web": {app: last page summary}}}

Step:

    {"i": 7, "app": "accounting",
     "action": "open_url|switch_app|click|type|download|read|move|mkdir|
                append_rows|write_file|edit_file|delete",
     "target": {"role": "AXTextField", "name": "Invoice date", "ordinal": 2}   # web control
               | {"url_path": "/bills/new"} | {"path": "Downloads/x.pdf"} | {"from": .., "to": ..},
     "value":  "2026-03-14" | {"secret": "Password"} | [[row], ...] | null,
     "before": {"url_path", "title", "alerts", "status"},          # web
     "after":  {... + "page_text", "tables"}  (after a page load),
     "files_touched": [{"path", "op": created|modified|moved|mkdir|removed,
                        "sha256", "from"?}],
     "observed": {"text": "..."}}                                   # read steps

Recorder API — the seam where a real macOS recorder plugs in:

    rec = Recorder(home, task="t1_statements", instance="i01", context={...})
    source.start(rec.feed)        # any RecorderSource pushes low-level events
    ...                           # the user works
    source.stop(); trace = rec.finish()

Low-level events (dicts) — chosen to map 1:1 onto macOS APIs:

    {"type": "app", "event": "activated", "app": "Finder", "kind": "files"|"web"}
        NSWorkspace.didActivateApplicationNotification (+ bundle id -> app name)
    {"type": "pointer", "kind": "click", "app", "element": {role, name, ordinal, ...}, "window": {...}}
        CGEventTap (listen-only, kCGEventLeftMouseDown) + AXUIElementCopyElementAtPosition
    {"type": "ax", "notification": "AXFocusedUIElementChanged" | "AXValueChanged", "app", "element", "window"}
        AXObserverAddNotification on the frontmost app's pid
    {"type": "ax", "notification": "AXLoadComplete", "app", "window": {url_path, title, alerts, page_text, tables}}
        WebKit/Chrome AXLoadComplete + a walk of the web area's AX subtree
    {"type": "ax", "notification": "AXFocusedWindowChanged", "app": "Preview", "window": {"document": path}}
        kAXFocusedWindowChangedNotification + the window's AXDocument (file URL)
    {"type": "fs", "path", "flags": ["ItemCreated"|"ItemRenamed"|"ItemModified"|"ItemRemoved",
                                     "ItemIsFile"|"ItemIsDir"], "inode"?, "by_app"?}
        FSEventStreamCreate(..., kFSEventStreamCreateFlagFileEvents) on the watched roots

The recorder coalesces events into steps: AXValueChanged bursts on one field
-> one "type"; a click followed by AXLoadComplete -> "click" with an "after";
a click followed by a new file in Downloads -> "download"; a pair of
ItemRenamed events with one inode -> "move" (a directory created just before
-> folded into that move); ItemModified on a CSV whose old rows are a prefix
of the new ones -> "append_rows"; opening a document -> "read" with the text
the user saw. Secure fields are never stored: their value becomes
{"secret": <field name>}.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from .common import csv_rows, file_text, manifest, sha256_file

SCHEMA = "awos.demo_trace/1"
TEXT_SNAPSHOT_MAX = 20000
TEXT_EXT = (".csv", ".txt", ".md", ".json")


@dataclass
class Step:
    i: int
    app: str
    action: str
    target: Dict[str, Any] = field(default_factory=dict)
    value: Any = None
    before: Dict[str, Any] = field(default_factory=dict)
    after: Dict[str, Any] = field(default_factory=dict)
    files_touched: List[Dict[str, Any]] = field(default_factory=list)
    observed: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Trace:
    task: str
    instance: str
    title: str = ""
    note: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    recorder: Dict[str, Any] = field(default_factory=lambda: {"kind": "scripted", "version": 1})
    environment: Dict[str, Any] = field(default_factory=dict)
    start: Dict[str, Any] = field(default_factory=dict)
    steps: List[Step] = field(default_factory=list)
    end: Dict[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Trace":
        if d.get("schema") != SCHEMA:
            raise ValueError(f"not a {SCHEMA} trace: {d.get('schema')!r}")
        kw = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "steps"}
        return cls(steps=[Step.from_dict(s) for s in d.get("steps", [])], **kw)

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Trace":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


class RecorderSource(Protocol):
    """Anything that pushes low-level events into Recorder.feed.

    Implementations: browser.Browser + demonstrate.FileActor (scripted, here);
    MacRecorderSource (native macOS: scaffold/agent/desktop/capture).
    """

    def start(self, sink: Callable[[dict], None]) -> None: ...

    def stop(self) -> None: ...


class MacRecorderSource:
    """The native macOS recorder as a RecorderSource (implementation: scaffold/agent/desktop/capture).

        src = MacRecorderSource(home, apps, consent={"granted": True, "notice_sha256": ...})
        rec = Recorder(home, task=..., instance=..., kind="macos_ax_fsevents", apps=apps)
        src.start(rec.feed)            # needs macOS + PyObjC + Accessibility + Input Monitoring
        ...                            # the user works; src.pause() / src.resume() around a sign-in
        src.stop(); trace = src.finish(rec)

    How each low-level event (module docstring) is produced natively:
      * app        AXFocusedApplication poll (~150 ms) -> pid -> NSRunningApplication bundle id
                   -> app name; web apps by page URL vs the task's base_urls; apps outside the
                   task's allow-list and the terminal running the recorder are ignored.
      * pointer    CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap,
                   kCGEventTapOptionListenOnly, leftMouseDown|keyDown) -> location ->
                   AXUIElementCopyElementAtPosition(systemWide, x, y) -> role/subrole/title/
                   description/AXIdentifier + ordinal among same-role elements of the web area.
                   Events posted by other processes (source PID > 0) are dropped.
      * ax         AXObserverCreate(pid) + AXObserverAddNotification for
                   AXFocusedUIElementChanged, AXValueChanged, AXFocusedWindowChanged,
                   AXTitleChanged and AXLoadComplete; on load the web area is walked (bounded)
                   into page_text + tables; AXDocument gives the file a window shows. Values of
                   AXSecureTextField / password elements are never read: {"secret": name}.
                   AXUIElementSetMessagingTimeout bounds every AX call.
      * fs         FSEventStreamCreate(watched home, FileEvents|UseCFTypes|NoDefer|UseExtendedData);
                   coalesced flags are normalised against the disk (adapter.py).
    Permissions (TCC), granted by the user in System Settings > Privacy & Security to the app
    running Python: Accessibility and Input Monitoring. Screen Recording is NOT needed.
    Nothing is prompted unless request_permissions=True.
    """

    def __init__(self, home: Path, apps: List[dict], *, consent: Optional[dict] = None,
                 watch: Optional[List[str]] = None, events_log: Optional[Callable[[dict], None]] = None,
                 keep_injected: bool = False, request_permissions: bool = False, task: str = "",
                 instance: str = ""):
        self.home, self.apps, self.consent = Path(home), list(apps), consent
        self.watch = list(watch or [str(home)])
        self.events_log, self.keep_injected = events_log, keep_injected
        self.request_permissions, self.task, self.instance = request_permissions, task, instance
        self.adapter = None
        self._live = None

    def start(self, sink: Callable[[dict], None]) -> None:
        from scaffold.agent.desktop.capture.adapter import NativeAdapter
        from scaffold.agent.desktop.capture.live import MacRecorder
        self.adapter = NativeAdapter(sink, home=self.home, apps=self.apps, log=self.events_log,
                                     keep_injected=self.keep_injected)
        self.adapter.feed({"src": "session", "event": "start", "consent": self.consent or {},
                           "task": self.task, "instance": self.instance, "apps": self.apps, "watch": self.watch})
        self._live = MacRecorder(self.adapter.feed, watch=self.watch, request_permissions=self.request_permissions)
        self._live.start()

    def pause(self) -> None:
        if self._live is not None:
            self._live.commands.put("pause")

    def resume(self) -> None:
        if self._live is not None:
            self._live.commands.put("resume")

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
        if self.adapter is not None:
            self.adapter.feed({"src": "session", "event": "stop"})

    def finish(self, recorder: "Recorder", web_with_text: bool = True) -> "Trace":
        return self.adapter.finish(recorder, web_with_text=web_with_text)


ACTIONS = ("open_url", "switch_app", "click", "type", "download", "read", "move", "mkdir", "append_rows",
           "write_file", "edit_file", "delete")


def validate_trace(trace: Any, secrets: tuple = ()) -> List[str]:
    """Problems with a trace (empty list = valid): schema, step shape, secure values, leaked secrets."""
    d = trace.to_dict() if isinstance(trace, Trace) else dict(trace)
    problems: List[str] = []
    if d.get("schema") != SCHEMA:
        problems.append(f"schema is {d.get('schema')!r}, not {SCHEMA}")
    for k in ("task", "instance"):
        if not d.get(k):
            problems.append(f"missing {k}")
    if not isinstance(d.get("recorder"), dict) or not d["recorder"].get("kind"):
        problems.append("recorder.kind missing")
    for n, s in enumerate(d.get("steps", []), 1):
        if s.get("i") != n:
            problems.append(f"step {n}: i={s.get('i')}")
        if s.get("action") not in ACTIONS:
            problems.append(f"step {n}: unknown action {s.get('action')!r}")
        if not isinstance(s.get("target", {}), dict) or not isinstance(s.get("app", ""), str):
            problems.append(f"step {n}: bad target/app")
        v = s.get("value")
        if isinstance(v, dict) and "secret" in v and set(v) != {"secret"}:
            problems.append(f"step {n}: secret placeholder carries more than the field name")
        tgt = s.get("target", {})
        if tgt.get("subrole") == "AXSecureTextField" and not (isinstance(v, dict) and "secret" in v):
            problems.append(f"step {n}: secure field without a {{'secret': name}} value")
        if any(k in tgt for k in ("value", "value_length")):
            problems.append(f"step {n}: target carries a field value")
    if secrets:
        blob = json.dumps(d)
        for sec in secrets:
            if sec and sec in blob:
                problems.append("a secret value appears in the trace")
    try:
        Trace.from_dict(json.loads(json.dumps(d)))
    except Exception as exc:          # round trip through JSON and the dataclasses
        problems.append(f"does not load: {exc}")
    return problems


def text_snapshot(home: Path) -> Dict[str, str]:
    out = {}
    for rel in manifest(home):
        p = home / rel
        private = "Private" in rel or any(part.startswith(".") for part in Path(rel).parts)  # config, secrets
        if p.suffix.lower() in TEXT_EXT and p.stat().st_size <= TEXT_SNAPSHOT_MAX and not private:
            out[rel] = p.read_text(encoding="utf-8", errors="replace")
    return out


def _same_el(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("role", "name", "ordinal"))


class Recorder:
    def __init__(self, home: Path, *, task: str, instance: str, context: Optional[dict] = None,
                 title: str = "", note: str = "", kind: str = "scripted", apps: Optional[List[dict]] = None):
        self.home = Path(home)
        self.trace = Trace(task=task, instance=instance, title=title, note=note, context=dict(context or {}),
                           recorder={"kind": kind, "version": 1},
                           environment={"apps": list(apps or []), "home": str(self.home)},
                           start={"files": manifest(self.home), "text": text_snapshot(self.home)})
        self._text = dict(self.trace.start["text"])
        self._app: Optional[str] = None
        self._window: Dict[str, Any] = {}
        self._pending_rename: Optional[dict] = None
        self._pending_dirs: List[str] = []
        self._expect_load = False

    # ----------------------------------------------------------- helpers --
    def _rel(self, path: str) -> str:
        p = Path(path)
        try:
            return p.resolve().relative_to(self.home.resolve()).as_posix()
        except ValueError:
            return str(p)

    def _step(self, action: str, **kw) -> Step:
        s = Step(i=len(self.trace.steps) + 1, app=kw.pop("app", self._app or ""), action=action, **kw)
        self.trace.steps.append(s)
        return s

    @property
    def _last(self) -> Optional[Step]:
        return self.trace.steps[-1] if self.trace.steps else None

    def _flush_dirs(self) -> None:
        for d in self._pending_dirs:
            self._step("mkdir", target={"path": d}, files_touched=[{"path": d, "op": "mkdir"}])
        self._pending_dirs = []

    # -------------------------------------------------------------- feed --
    def feed(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "app":
            if ev["app"] != self._app:
                self._app = ev["app"]
                self._step("switch_app", app=ev["app"], target={"kind": ev.get("kind", "")})
            return
        if kind == "pointer":
            self._flush_dirs()
            self._app = ev.get("app", self._app)
            self._step("click", target=dict(ev["element"]), before=dict(ev.get("window", {})))
            self._expect_load = True
            return
        if kind == "ax":
            self._feed_ax(ev)
            return
        if kind == "fs":
            self._feed_fs(ev)
            return
        raise ValueError(f"unknown event type {kind!r}")

    def _feed_ax(self, ev: dict) -> None:
        n = ev["notification"]
        self._app = ev.get("app", self._app)
        if n == "AXLoadComplete":
            win = dict(ev.get("window", {}))
            last = self._last
            if self._expect_load and last is not None and last.action in ("click", "download"):
                last.after = win
            else:
                self._step("open_url", target={"url_path": win.get("url_path", "")}, after=win)
            self._expect_load = False
            self._window = win
            return
        if n == "AXFocusedUIElementChanged":
            self._window = dict(ev.get("window", self._window))
            return
        if n == "AXValueChanged":
            el = dict(ev["element"])
            secure = el.get("subrole") == "AXSecureTextField"
            value = {"secret": el.get("name", "")} if secure else el.get("value", "")
            el.pop("value", None)
            el.pop("value_length", None)
            last = self._last
            if last is not None and last.action == "type" and _same_el(last.target, el) and last.app == self._app:
                last.value = value
            else:
                self._step("type", target=el, value=value, before=dict(ev.get("window", self._window)))
            return
        if n == "AXFocusedWindowChanged":
            doc = ev.get("window", {}).get("document")
            if doc:
                self._flush_dirs()
                rel = self._rel(doc)
                p = self.home / rel
                text = file_text(p) if p.exists() else ""
                self._step("read", target={"path": rel}, observed={"text": text[:8000]},
                           files_touched=[{"path": rel, "op": "read", "sha256": sha256_file(p) if p.exists() else ""}])
            return
        # other notifications are ignored on purpose (selection, scroll, ...)

    def _feed_fs(self, ev: dict) -> None:
        flags = set(ev.get("flags", []))
        rel = self._rel(ev["path"])
        p = self.home / rel
        is_dir = "ItemIsDir" in flags
        if "ItemRenamed" in flags:
            if not p.exists():
                self._pending_rename = {"from": rel, "inode": ev.get("inode")}
                return
            pend = self._pending_rename
            if pend and (pend.get("inode") is None or pend.get("inode") == ev.get("inode")):
                self._pending_rename = None
                touched = [{"path": d, "op": "mkdir"} for d in self._pending_dirs
                           if rel.startswith(d.rstrip("/") + "/")]
                self._pending_dirs = [d for d in self._pending_dirs if not rel.startswith(d.rstrip("/") + "/")]
                self._flush_dirs()
                touched.append({"path": rel, "op": "moved", "from": pend["from"],
                                "sha256": "" if is_dir else sha256_file(p)})
                self._step("move", app=ev.get("by_app", self._app or "Finder"),
                           target={"from": pend["from"], "to": rel}, files_touched=touched)
                if pend["from"] in self._text:
                    self._text[rel] = self._text.pop(pend["from"])
            return
        if "ItemCreated" in flags and is_dir:
            self._pending_dirs.append(rel)
            return
        if "ItemRemoved" in flags:
            self._flush_dirs()
            self._step("delete", app=ev.get("by_app", self._app or ""), target={"path": rel},
                       files_touched=[{"path": rel, "op": "removed"}])
            self._text.pop(rel, None)
            return
        if not p.is_file():
            return
        sha = sha256_file(p)
        text = p.read_text(encoding="utf-8", errors="replace") if p.suffix.lower() in TEXT_EXT else None
        if "ItemCreated" in flags and ev.get("by_app") == "Browser":
            last = self._last
            if last is not None and last.action == "click":
                last.action = "download"
                last.files_touched.append({"path": rel, "op": "created", "sha256": sha})
                self._expect_load = False
            else:
                self._step("download", files_touched=[{"path": rel, "op": "created", "sha256": sha}])
            if text is not None:
                self._text[rel] = text
            return
        self._flush_dirs()
        app = ev.get("by_app", self._app or "")
        old = self._text.get(rel)
        if old is not None and text is not None and rel.endswith(".csv"):
            o, n = csv_rows(old), csv_rows(text)
            if n[:len(o)] == o and len(n) > len(o):
                self._step("append_rows", app=app, target={"path": rel}, value=n[len(o):],
                           files_touched=[{"path": rel, "op": "modified", "sha256": sha}])
                self._text[rel] = text
                return
        if old is None and "ItemCreated" in flags:
            self._step("write_file", app=app, target={"path": rel}, value=text,
                       files_touched=[{"path": rel, "op": "created", "sha256": sha}])
        else:
            self._step("edit_file", app=app, target={"path": rel}, value=text,
                       files_touched=[{"path": rel, "op": "modified", "sha256": sha}])
        if text is not None:
            self._text[rel] = text

    # ------------------------------------------------------------ finish --
    def finish(self, web_end: Optional[dict] = None) -> Trace:
        self._flush_dirs()
        self.trace.end = {"files": manifest(self.home), "text": text_snapshot(self.home), "web": dict(web_end or {})}
        return self.trace


def inode(path: Path) -> int:
    return os.stat(path).st_ino

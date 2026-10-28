"""
adapter.py — native macOS capture records -> teach_once trace events (written for AWOS).

The live recorder (live.py) and the replay mode (--from-events) both push
NATIVE records through NativeAdapter.feed(); the adapter turns them into the
low-level events testbeds/teach_once/trace.Recorder already understands (the
same shapes the scripted demonstrations emit), so the step-merging rules live
in one place (trace.Recorder) for scripted and native demonstrations alike.

Native records (schema "awos.capture_events/1", one JSON object per line in
events.jsonl; "t" is epoch seconds and optional):

  {"src": "session", "event": "start", "consent": {"granted": true, "notice_sha256": ..., "at": ...},
   "task": ..., "home": ..., "apps": [{"name", "kind", "base_url"?}], "watch": [...]}
  {"src": "session", "event": "pause" | "resume", "reason": "auth"}      # nothing is kept while paused
  {"src": "session", "event": "stop"}
  {"src": "app", "bundle_id": "com.apple.finder", "name": "Finder", "pid": 501}
  {"src": "mouse", "button": "left", "pressed": true, "x": .., "y": .., "injected": false, "clicks": 1,
   "bundle_id": .., "url": "http://127.0.0.1:8001/bills" | null, "element": {role, subrole, name, ordinal, ...}}
  {"src": "key", "key": "enter" | "tab" | "esc", "injected": false, "bundle_id": .., "url": ..,
   "focused": {element}, "default_button": {element} | null}
  {"src": "ax", "notification": "AXValueChanged" | "AXFocusedUIElementChanged", "bundle_id": .., "url": ..,
   "element": {role, subrole, name, ordinal, value?}}
  {"src": "ax", "notification": "AXLoadComplete", "bundle_id": .., "url": ..,
   "window": {"title", "alerts", "status", "page_text", "tables"}}
  {"src": "ax", "notification": "AXFocusedWindowChanged", "bundle_id": .., "window": {"document": "file:///..", "title"}}
  {"src": "ax", "notification": "AXTitleChanged", "bundle_id": <browser>, "url": ..}       # tab switch
  {"src": "fs", "path": "/abs/path", "flags": <raw FSEvents bitmask>, "inode": 1234}

Rules (what the scripted demos and a person at a real Mac have in common):
  * privacy: every element is sanitised (schema.sanitize_element) BEFORE it is
    logged or fed on — a secure field never has a value, so trace.Recorder
    stores {"secret": <field name>}; key records never carry characters;
    documents and files outside the watched home are never read; nothing at
    all is kept while paused (auth); no record is accepted before a consent
    "start" record.
  * apps: Finder/Preview/... map from bundle ids; web apps are identified by
    the page URL against the task's base_urls (browser chrome — address bar,
    tabs — is ignored); the terminal running the recorder and apps outside the
    task's allow-list are ignored; injected (synthetic) input is dropped.
  * "files" apps contribute only app switches, the document a window shows
    (-> "read") and file-system effects; their clicks/typing are UI detail.
  * web apps: a click on a text field is a focus change (Playwright codegen
    records fill, not the click into the field); Enter in a field = click on
    the window's default button when the AX tree names one; a load with the
    same page and no action in between is a duplicate and dropped.
  * file system: FSEvents' coalesced flags are normalised against the disk
    and a path->(inode, sha) map: rename pairs by inode, atomic saves (temp
    file renamed over a known file) become modifications, a just-created
    "untitled folder" renamed by the user becomes one mkdir, metadata-only
    changes are dropped, editor/browser temp files are ignored, and a file
    created in a Downloads folder right after a web click is a download.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from .fsevents import decode_flags
from .schema import EVENTS_SCHEMA, is_editable, sanitize_element

BUNDLE_NAMES = {
    "com.apple.finder": "Finder", "com.apple.Preview": "Preview", "com.apple.iWork.Numbers": "Numbers",
    "com.apple.TextEdit": "TextEdit", "com.apple.iWork.Pages": "Pages", "com.microsoft.Excel": "Excel",
    "com.apple.mail": "Mail",
}
BROWSER_BUNDLES = frozenset({"com.apple.Safari", "com.apple.SafariTechnologyPreview", "com.google.Chrome",
                             "com.brave.Browser", "com.microsoft.edgemac", "company.thebrowser.Browser",
                             "org.chromium.Chromium", "org.mozilla.firefox"})
HOST_BUNDLES = frozenset({"com.apple.Terminal", "com.googlecode.iterm2", "com.microsoft.VSCode",
                          "dev.warp.Warp-Stable", "com.mitchellh.ghostty", "net.kovidgoyal.kitty",
                          "io.alacritty", "com.todesktop.230313mzl4w4u92"})     # last: Cursor
TEMP_SUFFIXES = (".download", ".crdownload", ".part", ".tmp", ".swp", ".swx", "~")
WINDOW_KEYS = ("title", "alerts", "status", "page_text", "tables")
DOWNLOAD_WINDOW_S = 15.0


class ConsentRequired(RuntimeError):
    pass


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_temp(rel: str) -> bool:
    parts = Path(rel).parts
    name = parts[-1] if parts else rel
    if any(p.startswith(".") for p in parts):          # .DS_Store, .~lock, hidden dirs
        return True
    if name.startswith("~$") or ".sb-" in name or "(A Document Being Saved By" in rel:
        return True
    return name.endswith(TEMP_SUFFIXES)


def sanitize_record(rec: dict) -> dict:
    """Whitelist the fields of one native record; elements lose secure values here."""
    src = rec.get("src")
    keep = {"src", "t", "bundle_id", "name", "pid", "url", "injected"}
    out = {k: rec[k] for k in keep if k in rec}
    if src == "session":
        for k in ("event", "reason", "consent", "task", "home", "apps", "watch", "error", "schema", "instance",
                  "context"):
            if k in rec:
                out[k] = rec[k]
    elif src == "mouse":
        for k in ("button", "pressed", "x", "y", "clicks"):
            if k in rec:
                out[k] = rec[k]
        out["element"] = sanitize_element(rec.get("element"), keep_value=False)
    elif src == "key":
        out["key"] = rec.get("key")
        for k in ("focused", "default_button"):
            if rec.get(k) is not None:
                out[k] = sanitize_element(rec[k], keep_value=False)
    elif src == "ax":
        out["notification"] = rec.get("notification")
        if rec.get("element") is not None:
            out["element"] = sanitize_element(rec["element"])
        if isinstance(rec.get("window"), dict):
            w = rec["window"]
            out["window"] = {k: w[k] for k in WINDOW_KEYS + ("document",) if k in w}
    elif src == "fs":
        for k in ("path", "flags", "inode"):
            if k in rec:
                out[k] = rec[k]
    return out


class NativeAdapter:
    def __init__(self, sink: Callable[[dict], None], *, home: Path, apps: List[dict],
                 log: Optional[Callable[[dict], None]] = None, keep_injected: bool = False,
                 extra_bundle_names: Optional[Dict[str, str]] = None):
        self.sink = sink
        self.home = Path(home).resolve()
        self.apps = [dict(a) for a in apps]
        self.kind_of = {a["name"]: a.get("kind", "files") for a in self.apps}
        self.web = sorted(((a["base_url"], a["name"]) for a in self.apps if a.get("base_url")),
                          key=lambda x: -len(x[0]))
        self.bundle_names = {**BUNDLE_NAMES, **(extra_bundle_names or {})}
        self.log = log
        self.keep_injected = keep_injected
        self.consent: Optional[dict] = None
        self.session: Dict[str, Any] = {}
        self.paused = False
        self.pauses: List[dict] = []
        self.front: Dict[str, Any] = {}
        self.front_browser = False
        self.front_allowed = False
        self.cur_app: Optional[str] = None
        self.page: Dict[str, dict] = {}            # web app -> last loaded window (with url_path)
        self.acted_since_load: Dict[str, bool] = {}
        self.last_doc: Optional[str] = None
        self.last_web_click_t = -1e18
        self.last_click: Optional[tuple] = None
        self.buf_dirs: List[str] = []
        self.pending_rename: Optional[dict] = None
        self.files: Dict[str, dict] = {}            # rel -> {"inode", "sha"}
        self.dirs: Set[str] = set()
        self.emitted = 0
        self.stats: Dict[str, int] = {}
        self._scan()

    # ------------------------------------------------------------ helpers --
    def _count(self, k: str) -> None:
        self.stats[k] = self.stats.get(k, 0) + 1

    def _scan(self) -> None:
        self.files, self.dirs = {}, set()
        if not self.home.exists():
            return
        for p in self.home.rglob("*"):
            rel = p.relative_to(self.home).as_posix()
            if p.is_dir():
                self.dirs.add(rel)
            elif p.is_file():
                self.files[rel] = {"inode": p.stat().st_ino, "sha": _sha(p)}

    def _rel(self, path: str) -> Optional[str]:
        try:
            p = Path(path)
            p = p.parent.resolve() / p.name if not p.exists() else p.resolve()
            return p.relative_to(self.home).as_posix()
        except (ValueError, OSError):
            return None

    def _emit(self, ev: dict) -> None:
        if ev.get("type") != "fs":
            self._flush_fs()
        if "app" in ev and ev.get("type") != "fs":
            self.cur_app = ev["app"]
        self.emitted += 1
        self.sink(ev)

    def _switch(self, app: str, kind: str) -> None:
        if app != self.cur_app:
            self._emit({"type": "app", "event": "activated", "app": app, "kind": kind})

    def web_app(self, url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        for base, name in self.web:
            b = base.rstrip("/")
            if url == b or url.startswith(b + "/") or url.startswith(b + "?"):
                return name
        return None

    @staticmethod
    def url_path(url: str) -> str:
        from urllib.parse import urlparse
        u = urlparse(url)
        return (u.path or "/") + (f"?{u.query}" if u.query else "")

    def _before(self, app: str) -> dict:
        w = self.page.get(app, {})
        return {k: w[k] for k in ("url_path", "title", "alerts", "status") if k in w}

    def _app_name(self, bundle_id: str, name: str) -> str:
        return self.bundle_names.get(bundle_id) or name or bundle_id

    # ---------------------------------------------------------------- feed --
    def feed(self, raw: dict) -> None:
        rec = sanitize_record(raw)
        src = rec.get("src")
        if src == "session":
            if self.log:
                self.log(rec)
            self._session(rec)
            return
        if self.consent is None:
            raise ConsentRequired("no consent/session start record: the recorder must be started with the "
                                  "owner's consent before anything is captured")
        if self.paused:
            self._count("dropped_paused")
            return
        if not self._relevant(rec):
            self._count("dropped_irrelevant")
            return
        if self.log:
            self.log(rec)
        if rec.get("injected") and not self.keep_injected:
            self._count("dropped_injected")
            return
        if src == "app":
            self._on_app(rec)
            return
        if src in ("mouse", "key") and rec.get("bundle_id") and rec["bundle_id"] != self.front.get("bundle_id"):
            # a click lands before the frontmost-app poll notices the switch: user input implies focus
            self._on_app({"bundle_id": rec["bundle_id"], "name": "", "pid": rec.get("pid")})
        if src == "mouse":
            self._on_mouse(rec)
        elif src == "key":
            self._on_key(rec)
        elif src == "ax":
            self._on_ax(rec)
        elif src == "fs":
            self._on_fs(rec)
        else:
            self._count("unknown_src")

    def _relevant(self, rec: dict) -> bool:
        """Pre-filter BEFORE logging: activity in other apps, other web pages or other folders is not kept."""
        src = rec.get("src")
        if src == "fs":
            rel = self._rel(rec.get("path", ""))
            return rel is not None and rel != "."
        if src not in ("mouse", "key", "ax"):
            return True
        bid = rec.get("bundle_id") or self.front.get("bundle_id", "")
        if bid in BROWSER_BUNDLES:
            return self.web_app(rec.get("url")) is not None
        if bid in HOST_BUNDLES:
            return False
        if self.kind_of and bid:
            return self._app_name(bid, rec.get("name", "")) in self.kind_of
        return True

    # ------------------------------------------------------------- session --
    def _session(self, rec: dict) -> None:
        ev = rec.get("event")
        if ev == "start":
            c = rec.get("consent") or {}
            if not c.get("granted"):
                raise ConsentRequired("the session start record does not carry granted consent")
            self.consent = dict(c)
            self.session = {k: rec[k] for k in ("task", "instance", "watch") if k in rec}
        elif ev == "pause":
            self.flush()
            self.paused = True
            self.pauses.append({"after_event": self.emitted, "reason": rec.get("reason", "auth"), "t": rec.get("t")})
        elif ev == "resume":
            self.paused = False
            self._scan()                       # changes made while paused are not attributed to later steps
            if self.pauses:
                self.pauses[-1]["resumed_t"] = rec.get("t")
        elif ev == "stop":
            self.flush()
        else:
            self._count(f"session_{ev}")

    # ----------------------------------------------------------------- app --
    def _on_app(self, rec: dict) -> None:
        bid = rec.get("bundle_id", "")
        self.front = {"bundle_id": bid, "name": rec.get("name", ""), "pid": rec.get("pid")}
        self.front_browser = bid in BROWSER_BUNDLES
        if self.front_browser:
            self.front_allowed = True
            app = self.web_app(rec.get("url"))
            if app:
                self._switch(app, "web")
            return
        name = self._app_name(bid, rec.get("name", ""))
        allowed = bid not in HOST_BUNDLES and (name in self.kind_of if self.kind_of else True)
        self.front_allowed = allowed
        if not allowed:
            self._count("ignored_app")
            return
        self._switch(name, self.kind_of.get(name, "files"))

    def _enter_web(self, url: Optional[str]) -> Optional[str]:
        if not self.front_browser:
            return None
        app = self.web_app(url)
        if app is None:
            self._count("ignored_non_task_page")
            return None
        self._switch(app, "web")
        return app

    # --------------------------------------------------------------- input --
    def _click(self, app: str, el: dict, t: float) -> None:
        el = {k: v for k, v in el.items() if k != "value"}
        self._emit({"type": "pointer", "kind": "click", "app": app, "element": el, "window": self._before(app)})
        self.acted_since_load[app] = True
        self.last_web_click_t = t

    def _on_mouse(self, rec: dict) -> None:
        if rec.get("button") != "left" or not rec.get("pressed", True):
            return
        if not self.front_browser:
            self._count("files_app_click")
            return
        app = self._enter_web(rec.get("url"))
        if app is None:
            return
        el = rec.get("element") or {}
        if not el:
            self._count("click_without_element")
            return
        if is_editable(el):
            self._emit({"type": "ax", "notification": "AXFocusedUIElementChanged", "app": app, "element": el,
                        "window": self._before(app)})
            return
        key = (app, el.get("role"), el.get("name"), el.get("ordinal"))
        if (rec.get("clicks") or 1) >= 2 and self.last_click == key:
            self._count("double_click_folded")
            return
        self.last_click = key
        self._click(app, el, rec.get("t", time.time()))

    def _on_key(self, rec: dict) -> None:
        if rec.get("key") != "enter" or not self.front_browser:
            self._count("key_ignored")
            return
        app = self._enter_web(rec.get("url"))
        if app is None:
            return
        focused, default = rec.get("focused") or {}, rec.get("default_button")
        if focused and not is_editable(focused) and focused.get("role") in ("AXButton", "AXLink"):
            self._click(app, focused, rec.get("t", time.time()))
        elif default:
            self._click(app, default, rec.get("t", time.time()))
        else:
            self._count("enter_unmapped")       # submit via Enter with no default button: click the button instead

    # ------------------------------------------------------------------ ax --
    def _on_ax(self, rec: dict) -> None:
        n = rec.get("notification")
        if self.front_browser or rec.get("bundle_id") in BROWSER_BUNDLES:
            self._on_web_ax(n, rec)
            return
        if not self.front_allowed:
            return
        if n == "AXFocusedWindowChanged":
            doc = (rec.get("window") or {}).get("document")
            if not doc:
                return
            if doc.startswith("file://"):
                from urllib.parse import unquote, urlparse
                doc = unquote(urlparse(doc).path)
            rel = self._rel(doc)
            if rel is None:
                self._count("document_outside_home")
                return
            if rel == self.last_doc:
                self._count("document_refocus")
                return
            self.last_doc = rel
            self._emit({"type": "ax", "app": self.cur_app, "notification": "AXFocusedWindowChanged",
                        "window": {"document": str(self.home / rel), "title": (rec.get("window") or {}).get("title", "")}})
            return
        self._count("files_app_ax")

    def _on_web_ax(self, n: str, rec: dict) -> None:
        url = rec.get("url")
        if n == "AXLoadComplete":
            app = self.web_app(url)
            if app is None:
                self._count("ignored_non_task_page")
                return
            w = rec.get("window") or {}
            win = {"url_path": self.url_path(url), "title": w.get("title", ""), "alerts": list(w.get("alerts", [])),
                   "status": list(w.get("status", [])), "page_text": w.get("page_text", ""),
                   "tables": list(w.get("tables", []))}
            if self.page.get(app) == win and not self.acted_since_load.get(app, True):
                self._count("duplicate_load")
                return
            self.page[app] = win
            if not self.front_browser:
                return                              # background tab finished loading: cache only
            self._switch(app, "web")
            self.acted_since_load[app] = False
            self.last_doc = None
            self._emit({"type": "ax", "app": app, "notification": "AXLoadComplete", "window": dict(win)})
            return
        app = self._enter_web(url)
        if app is None:
            return
        if n in ("AXValueChanged", "AXFocusedUIElementChanged"):
            el = rec.get("element") or {}
            if not el:
                return
            if n == "AXValueChanged":
                if not is_editable(el):
                    return
                self.acted_since_load[app] = True
            self._emit({"type": "ax", "app": app, "notification": n, "element": el, "window": self._before(app)})
        # AXTitleChanged / AXFocusedWindowChanged / AXSelectedChildrenChanged: the tab switch is all

    # ------------------------------------------------------------------ fs --
    def _by_app(self, rel: str, created: bool, t: float) -> str:
        if created and "Downloads" in Path(rel).parts and (
                self.kind_of.get(self.cur_app or "") == "web" or t - self.last_web_click_t < DOWNLOAD_WINDOW_S):
            return "Browser"
        if self.kind_of.get(self.cur_app or "") == "web":
            return "Browser"
        return self.cur_app or self._app_name(self.front.get("bundle_id", ""), self.front.get("name", ""))

    def _fs_out(self, rel: str, flags: List[str], **kw) -> None:
        ev = {"type": "fs", "path": str(self.home / rel), "flags": flags}
        ev.update({k: v for k, v in kw.items() if v is not None})
        self._emit(ev)

    def _flush_dirs(self) -> None:
        dirs, self.buf_dirs = self.buf_dirs, []
        for d in dirs:
            self._fs_out(d, ["ItemCreated", "ItemIsDir"], by_app=self._by_app(d, False, 0))

    def _flush_rename(self) -> None:
        pend, self.pending_rename = self.pending_rename, None
        if pend is None:
            return
        rel = pend["rel"]
        kind = "ItemIsDir" if pend["is_dir"] else "ItemIsFile"
        self.files.pop(rel, None)
        self.dirs.discard(rel)
        self._fs_out(rel, ["ItemRemoved", kind], by_app=self._by_app(rel, False, 0))   # moved out of view (Trash ...)

    def _flush_fs(self) -> None:
        if self.pending_rename is not None:
            self._flush_rename()
        if self.buf_dirs:
            self._flush_dirs()

    def flush(self) -> None:
        self._flush_fs()

    def _on_fs(self, rec: dict) -> None:
        rel = self._rel(rec.get("path", ""))
        if rel is None or rel == ".":
            self._count("fs_outside_home")
            return
        if _is_temp(rel):
            self._count("fs_temp")
            return
        mask = rec.get("flags", 0)
        flags = set(decode_flags(mask) if isinstance(mask, int) else mask)
        p = self.home / rel
        exists = os.path.lexists(p)
        is_dir = p.is_dir() if exists else ("ItemIsDir" in flags or rel in self.dirs or rel in self.buf_dirs)
        inode = rec.get("inode")
        t = rec.get("t", time.time())

        if "ItemRenamed" in flags:
            if not exists:                                       # rename source
                if rel in self.buf_dirs:                          # "untitled folder" being renamed
                    self.pending_rename = {"rel": rel, "inode": inode, "is_dir": True, "buffered": True}
                    return
                if rel not in self.files and rel not in self.dirs:
                    return                                       # never-seen transient
                if self.pending_rename is not None:
                    self._flush_rename()
                known_inode = (self.files.get(rel) or {}).get("inode")
                self.pending_rename = {"rel": rel, "inode": inode or known_inode, "is_dir": is_dir}
                return
            pend = self.pending_rename
            if pend is not None and (pend.get("inode") is None or inode is None or pend["inode"] == inode):
                self.pending_rename = None
                if pend.get("buffered"):
                    self.buf_dirs = [rel if d == pend["rel"] else d for d in self.buf_dirs]
                    self.dirs.add(rel)
                    return
                kind = "ItemIsDir" if is_dir else "ItemIsFile"
                self._flush_dirs()
                by = self._by_app(rel, False, t)
                self._fs_out(pend["rel"], ["ItemRenamed", kind], inode=pend.get("inode") or inode, by_app=by)
                self._fs_out(rel, ["ItemRenamed", kind], inode=pend.get("inode") or inode, by_app=by)
                if is_dir:
                    self.dirs.discard(pend["rel"])
                    self.dirs.add(rel)
                    pre = pend["rel"].rstrip("/") + "/"
                    for k in [k for k in self.files if k.startswith(pre)]:
                        self.files[rel + "/" + k[len(pre):]] = self.files.pop(k)
                else:
                    info = self.files.pop(pend["rel"], None) or {}
                    self.files[rel] = {"inode": inode or info.get("inode"),
                                       "sha": _sha(p) if p.is_file() else info.get("sha")}
                return
            # renamed INTO view with no source we saw: atomic save over a known file, or arrival from outside
        if not exists:
            if rel in self.buf_dirs:
                self.buf_dirs.remove(rel)
                self.dirs.discard(rel)
                return
            if rel in self.files or rel in self.dirs:
                self._flush_fs()
                kind = "ItemIsDir" if rel in self.dirs else "ItemIsFile"
                self.files.pop(rel, None)
                self.dirs.discard(rel)
                self._fs_out(rel, ["ItemRemoved", kind], by_app=self._by_app(rel, False, t))
            return
        if is_dir:
            if rel not in self.dirs and rel not in self.buf_dirs:
                self.buf_dirs.append(rel)
                self.dirs.add(rel)
            return
        if not p.is_file():
            return
        sha = _sha(p)
        info = self.files.get(rel)
        created = info is None
        if not created and info.get("sha") == sha:
            self._count("fs_unchanged")
            return
        self.files[rel] = {"inode": inode or p.stat().st_ino, "sha": sha}
        if self.pending_rename is not None:
            self._flush_rename()
        self._flush_dirs()
        self._fs_out(rel, ["ItemCreated" if created else "ItemModified", "ItemIsFile"],
                     by_app=self._by_app(rel, created, t))

    # -------------------------------------------------------------- finish --
    def web_end(self, with_text: bool = True) -> Dict[str, dict]:
        keys = ("url_path", "title", "alerts", "status") + (("page_text", "tables") if with_text else ())
        return {a: {k: w[k] for k in keys if k in w} for a, w in self.page.items()}

    def session_meta(self) -> dict:
        return {"events_schema": EVENTS_SCHEMA, "consent": self.consent, "pauses": list(self.pauses),
                "stats": dict(sorted(self.stats.items())), **self.session}

    def finish(self, recorder, *, web_with_text: bool = True):
        """Flush, finish the trace.Recorder and stamp the session metadata into trace.recorder."""
        self.flush()
        trace = recorder.finish(self.web_end(web_with_text))
        trace.recorder = {"kind": "macos_ax_fsevents", "version": 1, "session": self.session_meta()}
        return trace

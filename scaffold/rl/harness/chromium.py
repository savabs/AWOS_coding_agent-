"""
chromium.py — ChromiumBackend: MicroAgent's browser interface (the same surface as
live/driver.MacBrowserBackend: open / click / fill / select / read_raw / wait_for / close,
``.page``, ``.log``) over headless Chromium.

Observation path (byte-identical to the product): CDP Accessibility.getFullAXTree +
DOM.getDocument -> axmap.snapshot_from_cdp (macOS AX dict shape) -> driver.to_micro_page
(-> driver.read_web) -> web_tools.Page. MicroAgent then prompts with web_tools.outline
exactly as it does on live Safari.

Actions go to the DOM node behind the control's AX path (backendDOMNodeId), the way AXPress /
AXValue act on the element itself (no hit-testing, overlays do not intercept):
    click   el.focus(); el.click()
    fill    focus, clear through the native value setter (+ input event), CDP Input.insertText
            (trusted input events, works for React-style controlled inputs), then a change event
    select  <select>: pick the option by text, input + change events; radio group: click the radio
After every action the page is allowed to settle: no pending main-frame navigation, document
complete, no in-flight fetch/XHR, no pending short timers (<= 2 s), and no DOM mutation for
``quiet_ms``; capped by ``settle_timeout``.

Safety: every URL the backend opens must be loopback (web_tools.check_loopback); Chromium is
launched with a host-resolver rule that makes every other host unresolvable, so page scripts
cannot reach the network either. JS dialogs (alert/confirm/prompt) are dismissed and logged as
interruptions; popups (target=_blank / window.open) are closed and their URL opened in the
main page when it is loopback.

The Playwright Chromium build is resolved by ``find_chromium()``: AWOS_RL_CHROMIUM, else the
newest cached chrome-headless-shell / Chromium under ~/Library/Caches/ms-playwright (the
Playwright wheel may expect a newer build than the cache holds; CDP is stable across them).
"""
from __future__ import annotations

import glob
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from scaffold.agent.desktop.live.driver import to_micro_page
from scaffold.agent.desktop.web_tools import (_SECRETISH, Page, UserNeeded, WebError, check_loopback,  # noqa: F401
                                              mask_secrets)

from .axmap import snapshot_from_cdp

SETTLE_JS = r"""
(() => {
  if (window.__awos) return;
  const st = {inflight: 0, timers: new Set(), lastMut: performance.now(), leaving: false};
  window.__awos = st;
  const _st = window.setTimeout, _ct = window.clearTimeout;
  window.setTimeout = function(fn, ms, ...rest) {
    ms = Number(ms) || 0;
    let id;
    const wrapped = typeof fn === 'function' ? function() { st.timers.delete(id); return fn.apply(this, arguments); } : fn;
    id = _st.call(window, wrapped, ms, ...rest);
    if (ms <= 2000 && typeof fn === 'function') st.timers.add(id);
    return id;
  };
  window.clearTimeout = function(id) { st.timers.delete(id); return _ct.call(window, id); };
  if (window.fetch) {
    const _f = window.fetch;
    window.fetch = function() {
      st.inflight++;
      return _f.apply(this, arguments).finally(() => { st.inflight--; st.lastMut = performance.now(); });
    };
  }
  const _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function() {
    st.inflight++;
    this.addEventListener('loadend', () => { st.inflight--; st.lastMut = performance.now(); }, {once: true});
    return _send.apply(this, arguments);
  };
  window.addEventListener('beforeunload', () => { st.leaving = true; });
  const arm = () => new MutationObserver(() => { st.lastMut = performance.now(); })
    .observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  if (document.documentElement) arm(); else document.addEventListener('DOMContentLoaded', arm);
  window.__awosIdle = () => ({ready: document.readyState === 'complete', inflight: st.inflight,
                              timers: st.timers.size, quiet: performance.now() - st.lastMut,
                              leaving: st.leaving});
})();
"""

FILL_CLEAR_JS = """function() {
  this.focus();
  const proto = this instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
              : this instanceof HTMLInputElement ? HTMLInputElement.prototype : null;
  if (proto) {
    const d = Object.getOwnPropertyDescriptor(proto, 'value');
    if (d && d.set) d.set.call(this, ''); else this.value = '';
    this.dispatchEvent(new Event('input', {bubbles: true}));
  } else if (this.isContentEditable) {
    this.textContent = '';
  }
  if (typeof this.select === 'function') { try { this.select(); } catch (e) {} }
  return true;
}"""

CHANGE_JS = "function() { this.dispatchEvent(new Event('change', {bubbles: true})); return true; }"

SELECT_JS = """function(text) {
  const want = String(text).trim().toLowerCase();
  const opts = Array.from(this.options || []);
  const o = opts.find(x => (x.label || x.text || '').trim().toLowerCase() === want)
         || opts.find(x => (x.text || '').replace(/\\s+/g, ' ').trim().toLowerCase() === want);
  if (!o) return false;
  this.focus();
  this.value = o.value; o.selected = true;
  this.dispatchEvent(new Event('input', {bubbles: true}));
  this.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
}"""

CLICK_JS = "function() { if (this.focus) this.focus(); this.click(); return true; }"


def find_chromium() -> Optional[str]:
    env = os.environ.get("AWOS_RL_CHROMIUM")
    if env and Path(env).exists():
        return env
    cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / "Library/Caches/ms-playwright")
    pats = ["chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell",
            "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
            "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            "chromium_headless_shell-*/chrome-linux*/headless_shell",
            "chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell",
            "chromium-*/chrome-linux*/chrome"]
    def build(p: str) -> int:
        m = re.search(r"-(\d+)/", p)
        return int(m.group(1)) if m else 0
    for pat in pats:
        hits = sorted(glob.glob(str(cache / pat)), key=build)
        if hits:
            return hits[-1]
    return None


LAUNCH_ARGS = ["--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost",
               "--no-proxy-server", "--disable-background-networking", "--disable-component-update",
               "--disable-sync", "--no-first-run", "--disable-features=Translate,MediaRouter"]


class Chromium:
    """One Playwright + one headless Chromium per worker process; contexts per episode."""

    def __init__(self, headless: bool = True, executable: Optional[str] = None):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        exe = executable or find_chromium()
        kw: Dict[str, Any] = {"headless": headless, "args": LAUNCH_ARGS}
        if exe:
            kw["executable_path"] = exe
        try:
            self.browser = self._pw.chromium.launch(**kw)
        except Exception:
            self._pw.stop()
            raise
        self.executable = exe

    def close(self) -> None:
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    def __enter__(self) -> "Chromium":
        return self

    def __exit__(self, *a) -> None:
        self.close()


class ChromiumBackend:
    """micro_agent browser interface over one fresh Chromium context (loopback URLs only)."""

    def __init__(self, base_url: str, *, chromium: Optional[Chromium] = None, load_timeout: float = 15.0,
                 settle_timeout: float = 5.0, quiet_ms: float = 60.0, viewport=(1280, 800),
                 log: Optional[Callable[[str], None]] = None):
        self.base = check_loopback(base_url).rstrip("/")
        self._own = chromium is None
        self.chromium = chromium or Chromium()
        self.ctx = self.chromium.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                                     java_script_enabled=True, accept_downloads=False,
                                                     service_workers="block")
        self.ctx.add_init_script(SETTLE_JS)
        self.pg = self.ctx.new_page()
        self.cdp = self.ctx.new_cdp_session(self.pg)
        self.cdp.send("DOM.enable")
        self.cdp.send("Accessibility.enable")
        self.load_timeout, self.settle_timeout, self.quiet_ms = load_timeout, settle_timeout, quiet_ms
        self.page: Optional[Page] = None
        self.log: List[str] = []                     # method + path + outcome (no values)
        self.unverified: List[dict] = []
        self.interruptions: List[dict] = []
        self.timings: List[dict] = []                # per call: {"op", "act_ms", "settle_ms", "observe_ms"}
        self._ulog = log
        self._nav_pending = 0
        self._last_status: Optional[int] = None
        self._popups: List[Any] = []
        self.pg.on("request", self._on_request)
        self.pg.on("requestfailed", self._on_req_done)
        self.pg.on("load", lambda *_: self._clear_nav())
        self.pg.on("response", self._on_response)
        self.pg.on("dialog", self._on_dialog)
        self.ctx.on("page", self._on_popup)

    # -- events -----------------------------------------------------------------
    def _on_request(self, req) -> None:
        try:
            if req.is_navigation_request() and req.frame == self.pg.main_frame:
                self._nav_pending += 1
        except Exception:
            pass

    def _on_req_done(self, req) -> None:
        try:
            if req.is_navigation_request() and req.frame == self.pg.main_frame:
                self._nav_pending = max(0, self._nav_pending - 1)
        except Exception:
            pass

    def _on_response(self, resp) -> None:
        try:
            req = resp.request
            if req.is_navigation_request() and req.frame == self.pg.main_frame:
                self._last_status = resp.status
                if resp.status in (204, 205) or 300 <= resp.status < 400:
                    self._nav_pending = max(0, self._nav_pending - 1)
        except Exception:
            pass

    def _clear_nav(self) -> None:
        self._nav_pending = 0

    def _on_dialog(self, dialog) -> None:
        self.interruptions.append({"rule": f"js_{dialog.type}", "button": "dismiss", "outcome": "dismissed",
                                   "text": mask_secrets(dialog.message or "")[:200]})
        self.log.append(f"INTERRUPT js_{dialog.type} -> dismiss (dismissed)")
        try:
            dialog.dismiss()
        except Exception:
            pass

    def _on_popup(self, popup) -> None:
        self._popups.append(popup)                   # handled in _settle (no blocking calls in handlers)

    def _drain_popups(self) -> None:
        while self._popups:
            popup = self._popups.pop(0)
            url = ""
            try:
                url = popup.url
                popup.close()
            except Exception:
                pass
            self.interruptions.append({"rule": "popup", "button": "close", "outcome": "closed"})
            self.log.append("INTERRUPT popup -> close (closed)")
            try:
                if url and url != "about:blank":
                    check_loopback(url)
                    self.pg.goto(url, wait_until="domcontentloaded", timeout=self.load_timeout * 1000)
            except Exception:
                pass

    # -- plumbing -----------------------------------------------------------------
    def _url(self, path_or_url: str) -> str:
        base = self.page.url if self.page and self.page.url.startswith("http") else self.base + "/"
        return check_loopback(urllib.parse.urljoin(base, path_or_url))

    def _settle(self, min_wait_ms: float = 30.0) -> bool:
        """Wait until the page is idle (see module docstring). True when it settled."""
        t0 = time.monotonic()
        t_end = t0 + self.settle_timeout
        try:
            self.pg.wait_for_timeout(min_wait_ms)       # lets request / navigation events arrive
        except Exception:
            pass
        while True:
            idle = None
            if self._popups:
                self._drain_popups()
            if self._nav_pending == 0:
                try:
                    idle = self.pg.evaluate("window.__awosIdle ? window.__awosIdle() : null")
                except Exception:                          # context destroyed by a navigation
                    idle = None
                if idle and idle["ready"] and not idle["leaving"] and idle["inflight"] == 0 \
                        and idle["timers"] == 0 and idle["quiet"] >= self.quiet_ms:
                    return True
            if time.monotonic() >= t_end:
                return False
            try:
                self.pg.wait_for_timeout(15)
            except Exception:
                time.sleep(0.015)

    def _snap(self):
        ax = self.cdp.send("Accessibility.getFullAXTree")
        dom = self.cdp.send("DOM.getDocument", {"depth": -1})
        url = self.pg.url
        try:
            title = self.pg.title()
        except Exception:
            title = ""
        return snapshot_from_cdp(ax, dom, url, title)

    def _load(self, settled: Optional[bool] = None) -> Page:
        t0 = time.monotonic()
        if settled is None:
            settled = self._settle()
        t1 = time.monotonic()
        snap = self._snap()
        if not snap.ok or not snap.web_paths:
            raise WebError("the Chromium page has no web area (closed or not loaded)")
        self.page = to_micro_page(snap)
        self.page._live["settled"] = bool(settled)  # type: ignore[attr-defined]
        t2 = time.monotonic()
        if self.timings:
            self.timings[-1].update(settle_ms=round((t1 - t0) * 1000, 1), observe_ms=round((t2 - t1) * 1000, 1))
        return self.page

    def _time(self, op: str) -> dict:
        rec = {"op": op, "act_ms": 0.0, "settle_ms": 0.0, "observe_ms": 0.0}
        self.timings.append(rec)
        return rec

    def _live(self, page: Page, n: int):
        c = page.control(n)
        live = getattr(page, "_live", None)
        if c is None or live is None or n not in live["paths"]:
            raise WebError(f"no control {n}")
        snap = live["snap"]
        backend = snap.element(live["paths"][n])
        return c, snap, live["paths"][n], backend

    def _obj(self, backend_id: Any) -> str:
        if backend_id is None:
            raise WebError("control has no DOM node")
        try:
            r = self.cdp.send("DOM.resolveNode", {"backendNodeId": int(backend_id)})
        except Exception as e:
            raise WebError(f"stale control ({type(e).__name__})") from e
        oid = (r.get("object") or {}).get("objectId")
        if not oid:
            raise WebError("stale control")
        return oid

    def _call(self, backend_id: Any, fn: str, *args: Any) -> Any:
        oid = self._obj(backend_id)
        r = self.cdp.send("Runtime.callFunctionOn", {"objectId": oid, "functionDeclaration": fn,
                                                     "arguments": [{"value": a} for a in args],
                                                     "returnByValue": True, "awaitPromise": False,
                                                     "userGesture": True})
        if r.get("exceptionDetails"):
            raise WebError("page script error while acting")
        return (r.get("result") or {}).get("value")

    # -- the interface ----------------------------------------------------------------
    def open(self, path_or_url: str = "/") -> Page:
        url = self._url(path_or_url)
        rec = self._time("open")
        t0 = time.monotonic()
        path = urllib.parse.urlsplit(url).path
        try:
            self.pg.goto(url, wait_until="domcontentloaded", timeout=self.load_timeout * 1000)
            outcome = "verified"
        except Exception as e:
            self.log.append(f"GET {path} -> refused")
            raise WebError(f"open failed: {type(e).__name__}") from e
        rec["act_ms"] = round((time.monotonic() - t0) * 1000, 1)
        self.log.append(f"GET {path} -> {outcome}")
        return self._load()

    def click(self, page: Page, n: int) -> Page:
        c, snap, path, backend = self._live(page, n)
        if c.role not in ("link", "button", "checkbox"):
            raise WebError(f"control {n} ({c.role}) is not clickable")
        rec = self._time("click")
        t0 = time.monotonic()
        try:
            self._call(backend, CLICK_JS)
        except WebError:
            self.log.append(f"CLICK {c.role} -> refused")
            raise
        except Exception as e:                       # navigation raced the call: the click happened
            if "context" not in str(e).lower() and "target" not in str(e).lower():
                self.log.append(f"CLICK {c.role} -> refused")
                raise WebError(f"click failed: {type(e).__name__}") from e
        rec["act_ms"] = round((time.monotonic() - t0) * 1000, 1)
        self.log.append(f"CLICK {c.role} -> verified")
        return self._load()

    def fill(self, page: Page, n: int, value: str) -> Page:
        c, snap, path, backend = self._live(page, n)
        if c.role not in ("textbox", "password"):
            raise WebError(f"control {n} is not a text input")
        rec = self._time("fill")
        t0 = time.monotonic()
        self._call(backend, FILL_CLEAR_JS)
        if value:
            self.cdp.send("Input.insertText", {"text": value})
        self._call(backend, CHANGE_JS)
        rec["act_ms"] = round((time.monotonic() - t0) * 1000, 1)
        self.log.append(f"FILL {c.role} -> verified")
        page.values[n] = value
        c.value = "•" * 8 if c.role == "password" else mask_secrets(value)
        t1 = time.monotonic()
        self._settle()                               # input handlers may fetch / re-render
        rec["settle_ms"] = round((time.monotonic() - t1) * 1000, 1)
        return page

    def select(self, page: Page, n: int, option: str) -> Page:
        c, snap, path, backend = self._live(page, n)
        if c.role not in ("radiogroup", "combobox"):
            raise WebError(f"control {n} is not a choice")
        if option.lower() not in [o.lower() for o in c.options]:
            raise WebError(f"option not offered by control {n}")
        rec = self._time("select")
        t0 = time.monotonic()
        opt_paths = page._live["options"].get(n) or {}  # type: ignore[attr-defined]
        if c.role == "radiogroup" and option.lower() in opt_paths:
            self._call(snap.element(opt_paths[option.lower()]), CLICK_JS)
        else:
            canon = c.options[[o.lower() for o in c.options].index(option.lower())]
            if not self._call(backend, SELECT_JS, canon):
                self.log.append(f"SELECT {c.role} -> refused")
                raise WebError(f"could not select that option in control {n}")
        rec["act_ms"] = round((time.monotonic() - t0) * 1000, 1)
        self.log.append(f"SELECT {c.role} -> verified")
        canon = c.options[[o.lower() for o in c.options].index(option.lower())]
        page.values[n] = canon
        c.value = canon
        t1 = time.monotonic()
        self._settle()
        rec["settle_ms"] = round((time.monotonic() - t1) * 1000, 1)
        return page

    def read_raw(self, page: Page, n: int) -> str:
        """Raw text of a secret_text control, for the secret store only."""
        c = page.control(n)
        if c is None or c.role != "secret_text":
            raise WebError(f"control {n} is not readable")
        return page._raw.get(c.raw_ref, "")

    def wait_for(self, pred: Callable[[Page], bool], timeout: Optional[float] = None) -> Optional[Page]:
        t_end = time.monotonic() + (self.load_timeout if timeout is None else timeout)
        while True:
            try:
                p = self._load()
            except WebError:
                p = None
            if p is not None and pred(p):
                return p
            if time.monotonic() >= t_end:
                return None
            self.pg.wait_for_timeout(100)

    def observe(self) -> Page:
        """Re-read the current page (no action)."""
        self._time("observe")
        return self._load()

    def close(self) -> bool:
        try:
            self.ctx.close()
        except Exception:
            pass
        if self._own:
            try:
                self.chromium.close()
            except Exception:
                pass
        return True

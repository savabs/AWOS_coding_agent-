"""
chromium.py — ChromiumBackend: MicroAgent's browser interface (the same surface as
live/driver.MacBrowserBackend: open / click / fill / select / read_raw / wait_for / close,
``.page``, ``.log``) over headless Chromium.

Observation path (byte-identical to the product; read_cdp): CDP Accessibility.getFullAXTree +
DOMSnapshot.captureSnapshot (the DOM index, as DOM.getDocument gave it, plus the click targets) ->
axmap.snapshot_from_cdp (macOS AX dict shape) -> driver.to_micro_page
(-> driver.read_web) -> web_tools.Page. MicroAgent then prompts with web_tools.outline
exactly as it does on live Safari.

Actions go to the DOM node behind the control's AX path (backendDOMNodeId), the way AXPress /
AXValue act on the element itself (no hit-testing, overlays do not intercept):
    click   el.focus(); el.click()  (link / button / checkbox / tab); the reader's other click targets
            (option / menuitem / treeitem / gridcell / clickable) get the full pointerdown / mousedown /
            pointerup / mouseup / click sequence (PRESS_JS)
    fill    focus, clear through the native value setter (+ input event), CDP Input.insertText
            (trusted input events, works for React-style controlled inputs), then a change event;
            then a short wait (fill_wait_ms) + settle and a re-read: fill returns the new page
    select  <select>: pick the option by text, input + change events; radio group: click the radio
After every action the page is allowed to settle: no pending main-frame navigation, document
complete, no in-flight fetch/XHR, no pending short timers (<= 2 s), and no DOM mutation for
``quiet_ms``; capped by ``settle_timeout``. Open-web observations (web=True) then wait some more,
all within ``web_settle_cap`` (default WEB_SETTLE_CAP_S, env AWOS_WEB_SETTLE_S) counted from the
start of the read: an unsettled page gets the rest of the cap to fire its ``load`` event (long-polling
and analytics keep fetch/timers busy forever, so "settled" alone is not awaited past
``settle_timeout``), and an unsettled page read with <= EMPTY_CONTROLS controls (a shell still
rendering) waits up to WEB_EMPTY_WAIT_S for interactive elements to appear and is read once more (a
settled page is taken as it is: idle and sparse is a real page, an error page or a short article). The
time spent is booked on ``timings`` (settle_ms; load_wait_ms / empty_wait_ms / reread only when they
happened).

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
from typing import Any, Callable, Dict, List, Optional, Tuple

from scaffold.agent.desktop.live.driver import to_micro_page
from scaffold.agent.desktop.web_tools import (_SECRETISH, CLICKABLE_ROLES, Page, UserNeeded, WebError,  # noqa: F401
                                              check_loopback, mask_secrets)

from .axmap import clickable_ids, flat_document, snapshot_from_cdp

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

#: click for the reader's extra click targets (option / menuitem / gridcell / clickable ...): custom
#: widgets often listen to pointerdown / mousedown (an autocomplete list picks on mousedown, before the
#: input's blur closes it), so the whole press sequence is dispatched at the element's centre
PRESS_JS = """function() {
  const r = this.getBoundingClientRect();
  const o = {bubbles: true, cancelable: true, composed: true, view: window, button: 0,
             clientX: r.left + r.width / 2, clientY: r.top + r.height / 2};
  const P = window.PointerEvent || MouseEvent;
  this.dispatchEvent(new P('pointerdown', Object.assign({pointerId: 1, isPrimary: true}, o)));
  this.dispatchEvent(new MouseEvent('mousedown', Object.assign({buttons: 1}, o)));
  this.dispatchEvent(new P('pointerup', Object.assign({pointerId: 1, isPrimary: true}, o)));
  this.dispatchEvent(new MouseEvent('mouseup', o));
  this.click();
  return true;
}"""


#: open-web read budget (see the module docstring): the hard cap on all waiting before one observation
#: is read (the action settle included); within it, the most a near-empty page waits for controls before
#: its one re-read, and the short settle once they appear (so a list renders whole). 8 s: the web
#: episode's 4 s settle_timeout plus as much again for a slow page's load event / first render; more
#: would only stretch the 25-step episodes that already hit the wall clock (not measured yet)
WEB_SETTLE_CAP_S = 8.0
WEB_EMPTY_WAIT_S = 3.0
WEB_RENDER_SETTLE_S = 0.5
EMPTY_CONTROLS = 1


def default_web_settle_cap() -> float:
    """WEB_SETTLE_CAP_S, or AWOS_WEB_SETTLE_S (seconds, 0 turns the extra waits off) when set."""
    try:
        return max(0.0, float(os.environ.get("AWOS_WEB_SETTLE_S") or WEB_SETTLE_CAP_S))
    except ValueError:
        return WEB_SETTLE_CAP_S


#: visible interactive elements in the top document (-1 before DOMContentLoaded): a cheap "has the page
#: rendered its controls yet" probe; not the observation (the AX read decides what the model sees)
INTERACTIVE_JS = r"""(() => {
  if (document.readyState === 'loading' || !document.body) return -1;
  const q = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], '
          + '[role=textbox], [role=searchbox], [role=combobox], [role=checkbox], [role=tab], [role=menuitem], [onclick]';
  let n = 0;
  for (const el of document.querySelectorAll(q)) { if (el.getClientRects().length && ++n > 50) break; }
  return n;
})()"""


#: DOM depth (elements below <html>) from which DOM.getDocument(depth=-1) fails with "CBOR: stack limit
#: exceeded" (measured on Chromium 1xx: 140 nested elements read, 150 do not)
DEEP_DOM = 150


def read_cdp(cdp, url: str, title: str = "") -> Tuple[Any, dict, dict, bool]:
    """The product's page reading over one CDP session -> (observe.Snapshot, AX tree, DOM, deep).

    Accessibility.getFullAXTree + one DOMSnapshot.captureSnapshot (computed cursor + isClickable). The
    snapshot gives the click targets that are not controls by role (axmap.clickable_ids) and, through
    axmap.flat_document, the DOM index the AX mapping reads (tag + attributes per backendNodeId: the same
    as DOM.getDocument(depth=-1) gave, pseudo elements aside, which DOM.getDocument fails to encode on
    deep pages: "CBOR: stack limit exceeded"; one call instead of two). DOM.getDocument is the fallback
    when the snapshot fails. ``deep``: the page nests >= DEEP_DOM elements. Shared by ChromiumBackend and
    the Mind2Web converter (rl/train/mind2web_data.SnapshotBrowser)."""
    ax = cdp.send("Accessibility.getFullAXTree")
    try:
        dsnap = cdp.send("DOMSnapshot.captureSnapshot", {"computedStyles": ["cursor"]})
    except Exception:                                # no layout (closed page): no click targets
        dsnap = None
    if dsnap is not None:
        dom = flat_document(dsnap)
    else:
        dom = cdp.send("DOM.getDocument", {"depth": -1})
    deep = int(dom.get("maxDepth") or 0) >= DEEP_DOM
    return snapshot_from_cdp(ax, dom, url, title, clickable=clickable_ids(dsnap)), ax, dom, deep


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
#: open-web mode (scaffold/rl/web): the same minus the resolver rule that blackholes every host
WEB_LAUNCH_ARGS = [a for a in LAUNCH_ARGS if not a.startswith("--host-resolver-rules")]

_PRIVATE_HOST = re.compile(r"^(localhost|.*\.local|.*\.localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|"
                           r"192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|169\.254\.\d+\.\d+|"
                           r"0\.0\.0\.0|\[?::1?\]?|\[?f[cd][0-9a-f:]*\]?)$", re.I)


class NotWebURL(PermissionError):
    pass


def check_web_url(url: str, allow_private: bool = False) -> str:
    """Open-web URL policy: http(s) only (no file:, data:, javascript:, chrome: ...), a host is
    required, and loopback / private / link-local hosts are refused unless ``allow_private``
    (tests serve their fake sites on 127.0.0.1; a real run must not reach the runner's own
    services, e.g. llama-server on 127.0.0.1:8080)."""
    u = urllib.parse.urlsplit(url or "")
    if u.scheme not in ("http", "https") or not u.hostname:
        raise NotWebURL(f"refusing URL with scheme {u.scheme or '(none)'!r}: only http(s) pages")
    if not allow_private and _PRIVATE_HOST.match(u.hostname):
        raise NotWebURL(f"refusing private / loopback host {u.hostname}")
    return url


class Chromium:
    """One Playwright + one headless Chromium per worker process; contexts per episode.
    ``web=True`` launches it able to resolve real hosts (open-web evaluation only)."""

    def __init__(self, headless: bool = True, executable: Optional[str] = None, web: bool = False):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        exe = executable or find_chromium()
        self.web = bool(web)
        kw: Dict[str, Any] = {"headless": headless, "args": WEB_LAUNCH_ARGS if web else LAUNCH_ARGS}
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
                 log: Optional[Callable[[str], None]] = None, web: bool = False, allow_private: bool = False,
                 page_hook: Optional[Callable[[Page], None]] = None, fill_wait_ms: float = 150.0,
                 web_settle_cap: Optional[float] = None):
        """``web=True`` (scaffold/rl/web only): real http(s) sites under check_web_url instead of
        loopback-only, the HTTP status of the last main-frame response on Page.status, and the
        extra verbs press_enter / go_back / goto / reveal / screenshot. ``page_hook(page)`` runs on
        every observation (it may clip the page, or raise UserNeeded to stop on a blocked page).
        ``fill`` waits at least ``fill_wait_ms`` (then until the page settles) and re-reads the page, so
        options an input shows while typing (autocomplete) are in the page it returns.
        ``web_settle_cap`` (web=True only; None = default_web_settle_cap(), 0 = off): the open-web read budget."""
        self.web, self.allow_private = bool(web), bool(allow_private)
        self.base = (check_web_url(base_url, self.allow_private) if self.web
                     else check_loopback(base_url)).rstrip("/")
        self._own = chromium is None
        self.chromium = chromium or Chromium(web=self.web)
        if self.web and not getattr(self.chromium, "web", False):
            raise ValueError("web=True needs a Chromium launched with web=True (the loopback one cannot resolve hosts)")
        self.page_hook = page_hook
        ctx_kw: Dict[str, Any] = {"locale": "en-US"} if self.web else {}
        self.ctx = self.chromium.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                                     java_script_enabled=True, accept_downloads=False,
                                                     service_workers="block", **ctx_kw)
        self.ctx.add_init_script(SETTLE_JS)
        self.pg = self.ctx.new_page()
        self.cdp = self.ctx.new_cdp_session(self.pg)
        self.cdp.send("DOM.enable")
        self.cdp.send("Accessibility.enable")
        self.load_timeout, self.settle_timeout, self.quiet_ms = load_timeout, settle_timeout, quiet_ms
        self.fill_wait_ms = float(fill_wait_ms)
        self.web_cap = (default_web_settle_cap() if web_settle_cap is None else max(0.0, float(web_settle_cap))) \
            if self.web else 0.0
        self.page: Optional[Page] = None
        self.log: List[str] = []                     # method + path + outcome (no values)
        self.unverified: List[dict] = []
        self.interruptions: List[dict] = []
        self.timings: List[dict] = []                # per call: {"op", "act_ms", "settle_ms", "observe_ms"}
        self.deep_reads = 0                          # observations of pages nested >= DEEP_DOM elements
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
                    self._check(url)
                    self.pg.goto(url, wait_until="domcontentloaded", timeout=self.load_timeout * 1000)
            except Exception:
                pass

    # -- plumbing -----------------------------------------------------------------
    def _check(self, url: str) -> str:
        return check_web_url(url, self.allow_private) if self.web else check_loopback(url)

    def _url(self, path_or_url: str) -> str:
        base = self.page.url if self.page and self.page.url.startswith("http") else self.base + "/"
        return self._check(urllib.parse.urljoin(base, path_or_url))

    def _settle(self, min_wait_ms: float = 30.0, timeout: Optional[float] = None) -> bool:
        """Wait until the page is idle (see module docstring). True when it settled."""
        t0 = time.monotonic()
        t_end = t0 + (self.settle_timeout if timeout is None else max(0.0, timeout))
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
        url = self.pg.url
        try:
            title = self.pg.title()
        except Exception:
            title = ""
        snap, _ax, _dom, deep = read_cdp(self.cdp, url, title)
        self.deep_reads += int(deep)
        return snap

    def _read(self, settled: bool) -> Page:
        snap = self._snap()
        if not snap.ok or not snap.web_paths:
            raise WebError("the Chromium page has no web area (closed or not loaded)")
        page = to_micro_page(snap)
        page._live["settled"] = bool(settled)  # type: ignore[attr-defined]
        if self.web and self._last_status:
            page.status = int(self._last_status)
        return page

    def _wait_loaded(self, t_end: float) -> None:
        """Until the main document fired ``load`` (or ``t_end``); never raises."""
        left = t_end - time.monotonic()
        if left > 0.05:
            try:
                self.pg.wait_for_load_state("load", timeout=left * 1000)
            except Exception:                              # timeout / navigation: read what is there
                pass

    def _wait_controls(self, t_end: float) -> None:
        """Until the page shows more than EMPTY_CONTROLS interactive elements (or ``t_end``)."""
        while time.monotonic() < t_end:
            if self._popups:
                self._drain_popups()
            try:
                if int(self.pg.evaluate(INTERACTIVE_JS) or 0) > EMPTY_CONTROLS:
                    return
            except Exception:                              # context destroyed by a navigation
                pass
            try:
                self.pg.wait_for_timeout(100)
            except Exception:
                time.sleep(0.1)

    def _load(self, settled: Optional[bool] = None, since: Optional[float] = None) -> Page:
        """Settle (unless the caller did: ``settled``), read, run the page hook. Open web: the extra
        load / empty-page waits of the module docstring, all before ``since`` + ``web_cap``."""
        t0 = time.monotonic()
        t_cap = (since if since is not None else t0) + self.web_cap
        extra: Dict[str, Any] = {}
        if settled is None:
            settled = self._settle(timeout=min(self.settle_timeout, self.web_cap) if self.web_cap else None)
        if self.web_cap and not settled:
            w = time.monotonic()
            self._wait_loaded(t_cap)
            if time.monotonic() - w >= 0.05:
                extra["load_wait_ms"] = round((time.monotonic() - w) * 1000, 1)
        t1 = time.monotonic()
        page = self._read(settled)
        read_ms = time.monotonic() - t1
        if self.web_cap and not settled and len(page.controls) <= EMPTY_CONTROLS and time.monotonic() < t_cap:
            # a shell still rendering (or a busy page that really has one control): wait for controls, a
            # short settle so a list renders whole, then read once more whatever came of it
            w = time.monotonic()
            t_end = min(t_cap, w + WEB_EMPTY_WAIT_S)
            self._wait_loaded(t_end)
            self._wait_controls(t_end)
            if self._settle(timeout=min(WEB_RENDER_SETTLE_S, max(0.0, t_cap - time.monotonic()))):
                settled = True
            extra["empty_wait_ms"] = round((time.monotonic() - w) * 1000, 1)
            r = time.monotonic()
            page = self._read(settled)
            read_ms += time.monotonic() - r
            extra["reread"] = 1
        self.page = page
        if self.timings:
            rec = self.timings[-1]
            wait_ms = (time.monotonic() - t0 - read_ms) * 1000
            rec["settle_ms"] = round(rec.get("settle_ms", 0.0) + wait_ms, 1)
            rec["observe_ms"] = round(read_ms * 1000, 1)
            for k, v in extra.items():
                if v:
                    rec[k] = v
        if self.page_hook is not None:
            self.page_hook(self.page)                     # may raise UserNeeded (blocked page)
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
        if c.role not in CLICKABLE_ROLES:
            raise WebError(f"control {n} ({c.role}) is not clickable")
        rec = self._time("click")
        t0 = time.monotonic()
        if self.web:
            self._into_view(backend)                 # so the step's screenshot shows what was clicked
        try:
            self._call(backend, CLICK_JS if c.role in ("link", "button", "checkbox", "tab") else PRESS_JS)
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
        settled = self._settle(min_wait_ms=self.fill_wait_ms)   # input handlers may fetch / re-render
        rec["settle_ms"] = round((time.monotonic() - t1) * 1000, 1)
        return self._reread(page, settled, since=t1)

    def _reread(self, page: Page, settled: bool, since: Optional[float] = None) -> Page:
        """The page again after typing (an autocomplete list may have appeared), keeping what was
        typed: values by control path, a password field still shown as filled. ``page`` itself when the
        read fails (the caller keeps working on the page it had)."""
        try:
            new = self._load(settled=settled, since=since)
        except UserNeeded:                           # the page turned into a wall while typing
            raise
        except WebError:
            return page
        old_paths = getattr(page, "_live", {}).get("paths", {})
        by_path = {p: m for m, p in new._live["paths"].items()}  # type: ignore[attr-defined]
        for m0, v in page.values.items():
            m = by_path.get(old_paths.get(m0))
            oc, nc = page.control(m0), new.control(m) if m else None
            if nc is None or oc is None or nc.role != oc.role:
                continue
            new.values[m] = v
            if nc.role == "password":
                nc.value = "•" * 8
        return new

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

    # -- open-web verbs (web=True; MicroAgent web_verbs) ------------------------------
    def _into_view(self, backend_id: Any) -> None:
        try:
            if backend_id is not None:
                self.cdp.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": int(backend_id)})
        except Exception:
            pass

    def _need_web(self, op: str) -> None:
        if not self.web:
            raise WebError(f"{op} is only available in open-web mode")

    def press_enter(self, page: Page, n: int) -> Page:
        """Enter in text control ``n`` (focus it, then a trusted key press): submits search boxes."""
        self._need_web("press_enter")
        c, snap, path, backend = self._live(page, n)
        if c.role not in ("textbox", "password"):
            raise WebError(f"control {n} is not a text input")
        rec = self._time("enter")
        t0 = time.monotonic()
        try:
            self._call(backend, "function() { this.focus(); return true; }")
            self.pg.keyboard.press("Enter")
        except WebError:
            self.log.append(f"ENTER {c.role} -> refused")
            raise
        except Exception as e:                       # navigation raced the key press: it happened
            if "context" not in str(e).lower() and "target" not in str(e).lower():
                self.log.append(f"ENTER {c.role} -> refused")
                raise WebError(f"enter failed: {type(e).__name__}") from e
        rec["act_ms"] = round((time.monotonic() - t0) * 1000, 1)
        self.log.append(f"ENTER {c.role} -> verified")
        return self._load()

    def go_back(self) -> Page:
        self._need_web("go_back")
        rec = self._time("back")
        t0 = time.monotonic()
        try:
            resp = self.pg.go_back(wait_until="domcontentloaded", timeout=self.load_timeout * 1000)
        except Exception as e:
            self.log.append("BACK -> refused")
            raise WebError(f"go_back failed: {type(e).__name__}") from e
        if resp is None and self.page is not None and self.pg.url == self.page.url:
            self.log.append("BACK -> refused")
            raise WebError("there is no previous page")
        rec["act_ms"] = round((time.monotonic() - t0) * 1000, 1)
        self.log.append("BACK -> verified")
        return self._load()

    def goto(self, url: str) -> Page:
        """Open an absolute http(s) URL (check_web_url policy)."""
        self._need_web("goto")
        try:
            self._check(url)
        except PermissionError as e:
            raise WebError(str(e)) from e
        return self.open(url)

    def reveal(self, page: Page, n: int) -> None:
        """Scroll the viewport to control ``n`` (MicroAgent calls it after scroll(), so the
        screenshot follows the control window the model sees)."""
        if not self.web:
            return
        live = getattr(page, "_live", None)
        if live is None or n not in live["paths"]:
            return
        self._into_view(live["snap"].element(live["paths"][n]))

    def screenshot(self, path: str) -> bool:
        """Viewport PNG of the current page (evidence for the WebJudge layout)."""
        try:
            self.pg.screenshot(path=str(path), full_page=False, timeout=15000)
            return True
        except Exception:
            return False

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

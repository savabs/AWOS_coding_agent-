"""
runner.py — the live Month-end Document Pack runner (replays a taught skill in the VM's Safari).

    python -m scaffold.agent.desktop.pack run --month 2026-09 --live [--wait-for-human]
    # or: --runner scaffold.agent.desktop.pack.runner:runner   (AWOS_PACK_WAIT_FOR_HUMAN=1 to wait)

Contract (pack/__init__.py): ``runner(source, period, ctx) -> RunnerResult``; ctx.downloads,
ctx.allowed(url, action), ctx.checkpoint, ctx.password_item, ctx.log. For one source + month:

  1. refuse unless inside the guest VM (kern.hv_vmm_present=1) -- the host Mac is never driven;
  2. load the active taught skill (~/.awos/pack/skills/<source>/, teach.py) and lint it: a skill
     that contains a deny-listed control (Pay now / Upgrade / Cancel ...) or a secret is refused;
  3. vm_hygiene once per process (quits Safari, Safari prompt prefs), then a NEW Safari window at
     the source's start_url;
  4. replay the skill with testbeds/teach_once SkillRunner (zero model calls) over the live
     MacEnv, through ``PackActor`` (live/checkpoint.py): every press / type / select / URL is
     checked against the read-only pack policy (pack/policy.py deny list, allowlist incl. every
     page the browser LANDS on, i.e. redirects) and the pack core's ``ctx.allowed``; loopback test
     portals are additionally pinned to the start URL's port. Human pace (1.5-3 s gaps).
     The month is bound through the skill's ``{period...}`` templates (teach.py);
  5. human checkpoints (login / OTP / captcha / session expired / security warning): without
     ``wait_for_human`` the run stops at once with status ``needs_you`` and a message saying what
     to do (the window stays open for the owner); with it, a macOS notification is posted INSIDE
     the VM and the page is polled until the owner cleared it, then the skill restarts from the
     start URL (bounded restarts). The runner never types into password / OTP / captcha fields and
     never clicks a captcha;
  6. a download step waits for a finished new file in ctx.downloads (Safari's ~/Downloads); the
     pack core collects, files and checks it after the runner returns;
  7. anything unknown -- a control that is missing or matches twice, a page the demo did not
     reach, an unknown dialog over the window -- HALTS: status ``failed`` with the reason.
     Interruptions known to be benign (Safari's save-password sheet, ...) are dismissed with
     their safe button only (live/interrupts.py).
     Opt-in: ``LiveRunner(repair=repair.SkillRepair(...))`` first re-finds a missing control
     (scaffold/agent/desktop/repair; never onto a destructive / send control) and writes the
     repair into a new skill version only after a clean run.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from testbeds.teach_once.induction import ROLE  # noqa: E402
from testbeds.teach_once.skill_runner import Grounding, Halt, SkillRunner, ground_role_name  # noqa: E402

from ..live import checkpoint as CK  # noqa: E402
from ..live.actions import NEEDS_USER, SAFARI, Actor, Expect, Outcome  # noqa: E402
from ..live.driver import MacEnv, read_web, to_teach_page  # noqa: E402
from . import RUN_FAILED, RUN_NEEDS_YOU, RUN_OK, Checkpoint, RunContext, RunnerResult  # noqa: E402
from . import policy as P  # noqa: E402
from .files import _new_entries, snapshot  # noqa: E402
from .teach import PORTAL_APP, lint_program, load_skill, period_values, policy_source, template_regex  # noqa: E402

LOOPBACK = ("127.0.0.1", "localhost", "::1")
#: live checkpoint kind -> pack Checkpoint kind
KIND = {"login": "login", "session_expired": "login", "otp": "otp", "captcha": "captcha",
        "unusual_activity": "unknown", "foreign_domain": "unknown", "consent": "consent"}
TODO = {"login": "sign in", "session_expired": "sign in again", "otp": "enter the one-time code",
        "captcha": "complete the security check", "unusual_activity": "check the security warning",
        "foreign_domain": "check where the portal sent the browser", "consent": "answer the prompt"}
TEXT_CAPTCHA_RE = re.compile(r"type the (characters|letters|text|code) (shown )?(in|from) the (image|picture)|"
                             r"characters (shown )?in the image|enter the (characters|text) (you see|shown)|"
                             r"security image", re.I)
AX_ROLE = {"AXLink": "link", "AXButton": "button", "AXTextField": "textbox", "AXSecureTextField": "textbox",
           "AXTextArea": "textbox", "AXPopUpButton": "combobox", "AXComboBox": "combobox", "combobox": "combobox"}


class CheckpointStop(Exception):
    """The page needs the owner and the run stops here (no wait, a timeout, or a foreign landing)."""

    def __init__(self, kind: str, notice: str, host: str = "", evidence: str = "", waited_s: float = 0.0):
        super().__init__(notice)
        self.kind, self.notice, self.host, self.evidence, self.waited_s = kind, notice, host, evidence, waited_s

    @classmethod
    def of(cls, hn: "CK.HumanNeeded") -> "CheckpointStop":
        return cls(hn.kind, hn.notice(), hn.url_host, hn.evidence, getattr(hn, "waited_s", 0.0))


class Restart(Exception):
    """The owner cleared a checkpoint mid-skill: the portal is somewhere else now; start over."""


class UnknownDialog(RuntimeError):
    """A dialog the interrupt rules do not know covers the window (the skill runner halts)."""


class PolicyRefused(RuntimeError):
    """The read-only pack policy refused an action (the skill runner halts)."""


# ================================================================ checkpoint ==

NOTICE_RE = re.compile(r"\b(never|not|don'?t|do not|won'?t)\b[^.!]{0,80}\b(ask|share|disclose|tell|give|call|"
                       r"request)\b[^.!]{0,80}\b(otp|pin|password|passcode|cvv|one[- ]?time)", re.I)


def _without_notices(snap):
    """A copy of the snapshot whose "we will never ask for your OTP" texts are blanked."""
    import copy
    import dataclasses
    hit = [False]

    def walk(n):
        for k in ("title", "description", "value", "help"):
            v = n.get(k)
            if isinstance(v, str) and NOTICE_RE.search(v):
                n[k] = NOTICE_RE.sub("", v)
                hit[0] = True
        for c in n.get("children") or []:
            walk(c)
    tree = copy.deepcopy(snap.tree)
    walk(tree)
    return dataclasses.replace(snap, tree=tree) if hit[0] else snap


class StableCheckpoint(CK.Checkpoint):
    """live Checkpoint + (a) "cleared" needs a loaded page that stayed clear on two polls (a blank
    page mid-redirect is not a cleared login), (b) an ``on_human`` hook after the announcement
    (the testbed's scripted owner; None in the product)."""

    def __init__(self, *a, on_human: Optional[Callable[[CK.HumanNeeded], None]] = None, **kw):
        super().__init__(*a, **kw)
        self.on_human = on_human
        self._clear_url: Optional[str] = None
        self.last_snap = None                   # the page the last checkpoint was found on

    def human_needed(self, snap, det):
        self.last_snap = snap
        return super().human_needed(snap, det)

    def detect(self, snap):
        """live/checkpoint.detect, minus safety notices ("Bharat Bank will never ask for your OTP,
        PIN or password" sits in the footer of every netbanking page and is not an OTP prompt),
        plus text captchas that never say "captcha" ("Type the characters shown in the image")."""
        if snap is None or not snap.ok:
            return super().detect(snap)
        snap = _without_notices(snap)
        det = super().detect(snap)
        if det is not None:
            return det
        text = " ".join(t for t, _k in read_web(snap).lines) + " " + (snap.window_title or "")
        m = TEXT_CAPTCHA_RE.search(text)
        return CK.Detection("captcha", m.group(0)) if m else None

    def cleared(self, snap) -> bool:
        ok = super().cleared(snap) and bool(snap.web_paths) and bool((snap.node(snap.web_paths[0]) or {}).get("children"))
        if not ok:
            self._clear_url = None
            return False
        if self._clear_url == snap.url:
            self._clear_url = None
            return True
        self._clear_url = snap.url
        return False

    def announce(self, hn: CK.HumanNeeded) -> None:
        super().announce(hn)
        if self.on_human is not None:
            try:
                self.on_human(hn)
            except Exception as e:  # noqa: BLE001 -- the helper is outside the run
                self.log(f"checkpoint: human helper failed ({type(e).__name__}: {e})")

    def resumed(self) -> int:
        return sum(1 for e in self.events if e.get("outcome") == "resumed")


# ===================================================================== page ==

def to_pack_page(snap):
    """driver.to_teach_page + popup buttons (role "combobox", with their options)."""
    v = read_web(snap)
    p = to_teach_page(snap, v)
    n = 0
    for wc in v.controls:
        if wc.kind == "combobox":
            p.elements.append({"role": "combobox", "name": wc.name, "form": wc.form, "ax_path": list(wc.path),
                               "snap": p.snap_id, "options": list(wc.options), "value": wc.value, "ordinal": n})
            n += 1
    return p


def make_ground(values: Dict[str, str], base: Callable = ground_role_name) -> Callable:
    """Strict role+name grounding (no fuzzy rung) + period ``name_template`` targets."""
    def ground(page, target: dict, *, exclude=(), fuzzy: bool = False) -> Grounding:
        role = AX_ROLE.get(target.get("role"), ROLE.get(target.get("role"), target.get("role")))
        if page is None:
            return Grounding("missing", rung="no screen")
        tpl = target.get("name_template")
        if tpl:
            rx = template_regex(tpl, values)
            els = [e for e in page.elements if e["role"] == role]
            hits = [e for e in els if rx.match(" ".join(str(e.get("name") or "").split()))]
            if len(hits) == 1:
                return Grounding("ok", hits[0], "period_template", [hits[0].get("name", "")])
            if hits:
                return Grounding("ambiguous", None, "period_template", [e.get("name", "") for e in hits])
            return Grounding("missing", None, "", [e.get("name", "") for e in els][:40])
        return base(page, dict(target, role=role), exclude=exclude, fuzzy=False)
    return ground


KEY_RETURN, KEY_ESCAPE = 36, 53


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split()).lower()


def _find(rt, root, pred, *, max_nodes: int = 3000, skip_web: bool = True, skip_roles=()):
    """Breadth-first AX search under ``root`` (web areas skipped unless asked)."""
    out, queue, n = [], [root], 0
    while queue and n < max_nodes:
        e = queue.pop(0)
        n += 1
        try:
            r = rt.read(e, ("AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue"))
        except Exception:  # noqa: BLE001
            continue
        if pred(r):
            out.append((e, r))
        if (skip_web and r.get("AXRole") == "AXWebArea") or (e is not root and r.get("AXRole") in skip_roles):
            continue
        try:
            queue.extend(rt.children(e))
        except Exception:  # noqa: BLE001
            pass
    return out


def select_by_typing(actor: Any, snap, path, option: str) -> bool:
    """Fallback for a web pop-up whose items AX does not list under it (WebKit opens a native menu
    that hangs off the APPLICATION element): open it, press the item found under the app, else
    type the option's text + Return into the open menu, then read the value back. Only after the
    policy verdict passed (the caller went through PackActor.select_option first)."""
    rt = actor.rt
    el = snap.element(path)
    if el is None or not actor.vm_check():
        return False
    want = _norm(option)
    try:
        actor.raise_window(snap)
        if snap.pid:
            rt.activate(snap.pid)
        if "AXPress" in rt.actions(el):
            rt.perform(el, "AXPress")
            rt.sleep(0.5)
        is_item = lambda r: r.get("AXRole") == "AXMenuItem" and _norm(r.get("AXTitle")) == want  # noqa: E731
        items = _find(rt, el, is_item, max_nodes=300)
        if not items and snap.pid:          # an open menu hangs off the application, not the window
            items = _find(rt, rt.app_element(snap.pid), is_item, max_nodes=800,
                          skip_roles=("AXWindow", "AXMenuBar"))
        if items:
            rt.perform(items[0][0], "AXPress")
        elif rt.can_post_events():
            rt.post_text(snap.pid, option)
            rt.sleep(0.3)
            rt.post_key(snap.pid, KEY_RETURN)
        else:
            rt.perform(el, "AXCancel")
            return False
        for _ in range(6):
            rt.sleep(0.4)
            v = rt.read(el, ("AXValue", "AXTitle", "AXDescription"))
            if want in {_norm(v.get(k)) for k in ("AXValue", "AXTitle", "AXDescription")}:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


DOWNLOAD_PROMPT_RE = re.compile(r"allow downloads|download (files|this file)|wants to download", re.I)


def _is_prompt_text(r: dict) -> bool:
    return any(isinstance(r.get(k), str) and DOWNLOAD_PROMPT_RE.search(r[k]) for k in ("AXValue", "AXTitle",
                                                                                        "AXDescription"))


def _is_allow(r: dict) -> bool:
    return r.get("AXRole") == "AXButton" and _norm(r.get("AXTitle") or r.get("AXDescription")) == "allow"


def download_prompt(rt, window, pid: Optional[int] = None) -> Optional[Tuple[Any, str]]:
    """Safari's "Do you want to allow downloads on <site>?" prompt, wherever AX puts it: a sheet or
    panel in OUR window (outside, then inside the content view), another Safari window / panel, or
    the container of the focused element. -> (the Allow button, its text) or None."""
    def hit(root, skip_web: bool, max_nodes: int = 3000):
        texts = _find(rt, root, _is_prompt_text, skip_web=skip_web, max_nodes=max_nodes)
        if not texts:
            return None
        allow = _find(rt, root, _is_allow, skip_web=skip_web, max_nodes=max_nodes)
        if not allow:
            return None
        t = texts[0][1]
        return allow[0][0], str(t.get("AXValue") or t.get("AXTitle") or t.get("AXDescription") or "")
    roots: List[Tuple[Any, bool]] = []
    if window is not None:
        roots += [(window, True), (window, False)]
    app_el = None
    if pid:
        try:
            app_el = rt.app_element(pid)
            for w in rt.read(app_el, ("AXWindows",)).get("AXWindows") or []:
                if window is None or not rt.same(w, window):
                    roots.append((w, True))
        except Exception:  # noqa: BLE001
            app_el = None
    for root, skip in roots:
        found = hit(root, skip, 3000 if skip else 1500)
        if found:
            return found
    if app_el is not None:                   # an alert focuses one of its own buttons
        try:
            e = rt.read(app_el, ("AXFocusedUIElement",)).get("AXFocusedUIElement")
            for _ in range(5):
                if e is None:
                    break
                found = hit(e, True, 300)
                if found:
                    return found
                e = rt.read(e, ("AXParent",)).get("AXParent")
        except Exception:  # noqa: BLE001
            pass
    return None


def press_el(rt, el) -> bool:
    """AXPress, else a synthetic click at the element's centre (harness use: Safari's own prompt)."""
    try:
        if "AXPress" in rt.actions(el) and rt.perform(el, "AXPress"):
            return True
        f = rt.read(el, ("AXPosition", "AXSize"))
        (x, y), (w, h) = f.get("AXPosition"), f.get("AXSize")
        return bool(rt.can_post_events() and rt.post_click(x + w / 2, y + h / 2))
    except Exception:  # noqa: BLE001
        return False


def ax_outline(rt, window, *, max_nodes: int = 400, pid: Optional[int] = None) -> List[str]:
    """Roles / titles of OUR window outside its web area, the app's other windows and the focused
    element's ancestry (diagnostics when a download stalls)."""
    def fmt(r: dict) -> str:
        t = r.get("AXTitle") or r.get("AXDescription") or (r.get("AXValue") if isinstance(r.get("AXValue"), str)
                                                            else "")
        return f"{r.get('AXRole')}/{r.get('AXSubrole') or ''} {str(t)[:60]!r}"
    lines = [fmt(r) for _e, r in _find(rt, window, lambda r: True, max_nodes=max_nodes)] if window is not None else []
    if pid:
        try:
            app_el = rt.app_element(pid)
            for w in rt.read(app_el, ("AXWindows",)).get("AXWindows") or []:
                if window is None or not rt.same(w, window):
                    lines.append("OTHER WINDOW: " + " / ".join(fmt(r) for _e, r in _find(rt, w, lambda r: True,
                                                                                       max_nodes=40)))
            e = rt.read(app_el, ("AXFocusedUIElement",)).get("AXFocusedUIElement")
            chain = []
            for _ in range(6):
                if e is None:
                    break
                chain.append(fmt(rt.read(e, ("AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue"))))
                e = rt.read(e, ("AXParent",)).get("AXParent")
            lines.append("FOCUS: " + " < ".join(chain))
        except Exception as ex:  # noqa: BLE001
            lines.append(f"outline error {type(ex).__name__}")
    return lines[-160:]


class _Base:
    """What SkillRunner wants of the file side: a tiny scratch home, no file actions."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.context: Dict[str, Any] = {}

    def actions(self) -> int:
        return 0

    def secret(self, name: str):
        return None


# ====================================================================== env ==

class PackEnv(MacEnv):
    """MacEnv for one pack source: https portals (not loopback-only), the pack policy on every
    URL, checkpoints, downloads into ctx.downloads, popup selection."""

    def __init__(self, source, ctx: RunContext, *, actor: Any, checkpoint: StableCheckpoint, home: Path,
                 waiting: bool, download_wait_s: float = 60.0, load_timeout: float = 20.0,
                 log: Optional[Callable[[str], None]] = None):
        super().__init__(_Base(home), {}, actor=actor, load_timeout=load_timeout)
        u = urllib.parse.urlsplit(source.start_url)
        self.urls = {PORTAL_APP: f"{u.scheme}://{u.netloc}"}
        self.source, self.ctx = source, ctx
        self.checkpoint = checkpoint
        self.waiting = waiting
        self.downloads = Path(ctx.downloads)
        self.download_wait_s = download_wait_s
        self._log = log or (lambda s: None)
        self.pin = (u.hostname or "").lower(), u.port or (443 if u.scheme == "https" else 80)
        self.got: List[str] = []
        self.dialogs: List[dict] = []
        self.allow_downloads = False            # testbed harness only: answer Safari's download prompt

    # -- policy ---------------------------------------------------------------
    def url_ok(self, url: str) -> Tuple[bool, str]:
        if not url or url.startswith(("about:", "blob:", "data:")):
            return True, ""
        try:
            u = urllib.parse.urlsplit(url)
            port = u.port or (443 if u.scheme == "https" else 80)
        except ValueError:
            return False, "malformed URL"
        host = (u.hostname or "").lower()
        if host in LOOPBACK and self.pin[0] in LOOPBACK and port != self.pin[1]:
            return False, f"{host}:{port} is another local service, not {self.source.name}"
        try:
            if not self.ctx.allowed(url, "navigate"):
                return False, f"{host} is not on {self.source.name}'s allowlist"
        except Exception as e:  # noqa: BLE001 -- a broken policy refuses
            return False, f"policy error {type(e).__name__}"
        return True, ""

    def _landed(self, url: str) -> None:
        ok, why = self.url_ok(url)
        if not ok:
            host = P.url_host(url) or "?"
            self.checkpoint.events.append({"kind": "foreign_domain", "source": self.source.name, "url_host": host,
                                           "evidence": why, "outcome": "stopped"})
            raise CheckpointStop("foreign_domain", f"AWOS needs you: {self.source.name} went to {host}, which is "
                                 f"not on its allowlist ({why}); the run stopped there", host, why)

    def _note(self, r) -> None:
        self.ui_actions += 1
        if r.outcome is Outcome.UNVERIFIED:
            self.unverified.append(r.to_dict())
        if r.outcome is not Outcome.REFUSED:
            return
        ny = (r.detail or {}).get("needs_you")
        if ny:
            notice = r.reason.split("needs_you:", 1)[-1].strip() or f"needs you: {ny.get('kind')}"
            raise CheckpointStop(ny.get("kind", "unknown"), notice, ny.get("url_host", ""), ny.get("evidence", ""))
        if r.reason.startswith(NEEDS_USER):
            raise UnknownDialog(f"{r.action}: {r.reason}")
        raise PolicyRefused(f"{r.action} refused: {r.reason}")

    # -- pages ----------------------------------------------------------------
    def page(self, app: str):
        if app not in self.windows:
            return None
        t_end = self.rt.now() + self.load_timeout
        prev, snap = None, self._snap(app)
        while True:
            sig = self._sig(snap)
            if (sig is not None and sig == prev and snap.web_paths) or self.rt.now() >= t_end:
                break
            prev = sig
            self.rt.sleep(self.actor.poll)
            snap = self._snap(app)
        if not snap.ok or not snap.web_paths:
            return None
        self._landed(snap.url or "")
        p = to_pack_page(snap)
        self.pages[app] = p
        return p

    def open(self, app: str, path: str):
        if app not in self.urls:
            raise ValueError(f"refusing non-task app {app!r}")
        url = urllib.parse.urljoin(self.urls[app] + "/", path)
        ok, why = self.url_ok(url)
        if not ok:
            raise PolicyRefused(f"open {P.url_host(url)}: {why}")
        if app in self.windows and not self._alive(app):
            self.notes.append(f"{app}: window went away; opening a new one")
            self._forget(app)
        if app not in self.windows:
            r = self.actor.open_url(url)
            if r.window is not None:
                self.windows[app] = r.window
                self.app_info = (r.after.app if r.after is not None else {}) or self.actor._app(SAFARI) or {}
        else:
            self.switch_window(app)
            r = self.actor.navigate(self.windows[app], url, app=self.app_info)
        self._note(r)
        if app not in self.windows:
            raise RuntimeError(f"no Safari window for {self.source.name}")
        return self.page(app)

    # -- checkpoints ------------------------------------------------------------
    def window(self):
        """OUR Safari window (also while it is still being opened, from the checkpoint's page)."""
        w = self.windows.get(PORTAL_APP)
        if w is None and self.checkpoint.last_snap is not None:
            w = self.checkpoint.last_snap.window
        return w

    def resumed(self) -> int:
        return self.checkpoint.resumed()

    def handle(self, snap, det: CK.Detection) -> None:
        """A wall on the page: stop (no wait / foreign landing / timeout) or wait, then Restart."""
        hn = self.checkpoint.human_needed(snap, det)
        if det.kind == "foreign_domain" or not self.waiting:
            self.checkpoint.events.append(dict(hn.to_dict(), outcome="stopped"))
            raise CheckpointStop.of(hn)
        try:
            self.checkpoint.wait(lambda: self._snap(PORTAL_APP), hn)
        except CK.HumanTimeout as t:
            raise CheckpointStop.of(t) from None
        raise Restart(f"{hn.kind} cleared by the owner")

    def before_op(self, op: dict) -> None:
        if PORTAL_APP not in self.windows:
            return
        snap = self._snap(PORTAL_APP)
        if snap.ok:
            self._landed(snap.url or "")
            det = self.checkpoint.detect(snap)
            if det is not None:
                self.handle(snap, det)

    # -- dialogs ----------------------------------------------------------------
    def _download_prompt(self, app: str) -> None:
        """Safari asks once per site whether it may download. The owner's call (needs_you /
        wait), unless the run was started with ``allow_downloads`` (the local testbed harness)."""
        pid = self.app_info.get("pid")
        found = download_prompt(self.rt, self.windows.get(app), pid)
        if found is None:
            return
        button, text = found
        host = P.url_host(self.urls.get(app)) or "?"
        self.dialogs.append({"prompt": text[:120], "allowed": self.allow_downloads})
        if self.allow_downloads:
            press_el(self.rt, button)
            self.rt.sleep(0.5)
            return
        hn = CK.HumanNeeded("consent", self.source.name, host, "Safari asks to allow downloads")
        if not self.waiting:
            raise CheckpointStop("consent", f"AWOS needs you: Safari asks whether {host} may download files; "
                                 "click Allow in the AWOS window, then re-run", host)
        self.checkpoint.announce(hn)
        t_end = self.rt.now() + self.checkpoint.timeout_s
        while self.rt.now() < t_end:
            self.rt.sleep(self.checkpoint.poll_s)
            if download_prompt(self.rt, self.windows.get(app), pid) is None:
                self.checkpoint.events.append(dict(hn.to_dict(), outcome="resumed"))
                return
        raise CheckpointStop("consent", "AWOS needs you: the Safari download prompt was not answered", host)

    def _clear_dialogs(self, app: str) -> None:
        snap = self._snap(app)
        r = self.actor.clear_interruptions(snap, "download")
        if r is None:
            return
        dlg = (r.detail or {}).get("interruption") or {}
        self.dialogs.append(dlg)
        buttons = {str(b).lower() for b in dlg.get("buttons") or []}
        if "allow" in buttons:            # "Do you want to allow downloads on <site>?" -- the owner's call
            host = P.url_host(snap.url) or "?"
            hn = CK.HumanNeeded("consent", self.source.name, host, "Safari asks to allow downloads")
            if not self.waiting:
                raise CheckpointStop("consent", f"AWOS needs you: Safari asks whether {host} may download "
                                     f"files; click Allow in the AWOS window, then re-run", host)
            self.checkpoint.announce(hn)
            t_end = self.rt.now() + self.checkpoint.timeout_s
            while self.rt.now() < t_end:
                self.rt.sleep(self.checkpoint.poll_s)
                if self.actor._front_clear(self._snap(app)):
                    self.checkpoint.events.append(dict(hn.to_dict(), outcome="resumed"))
                    return
            raise CheckpointStop("consent", "AWOS needs you: the Safari download prompt was not answered", host)
        raise UnknownDialog(f"an unknown dialog covers the window: {dlg.get('title') or ''} "
                            f"{sorted(buttons)} ({r.reason})")

    # -- actions ----------------------------------------------------------------
    def download(self, app: str, el: dict) -> Optional[Path]:
        """Press a download control; -> the finished new file in Downloads, or None."""
        before = snapshot(self.downloads)
        self.switch_window(app)
        snap, path = self._path_for(app, el)
        r = self.actor.press(snap, path, Expect(any_change=True))
        self._note(r)
        t_end = self.rt.now() + self.download_wait_s
        prev, last_dlg = None, self.rt.now()
        while True:
            cur, partials = _new_entries(self.downloads, before)
            if cur and not partials and cur == prev:
                name = max(cur, key=lambda n: cur[n][1])
                self.got.append(name)
                return self.downloads / name
            if self.rt.now() >= t_end:
                self.notes.append("download did not arrive; window outline: " + " | ".join(
                    ax_outline(self.rt, self.windows.get(app), pid=self.app_info.get("pid"))))
                return None
            if self.rt.now() - last_dlg >= 1.5 or prev is None:
                last_dlg = self.rt.now()
                if not cur and not partials:
                    self._download_prompt(app)
                self._clear_dialogs(app)
            if not cur and not partials:
                s = self._snap(app)
                if s.ok:
                    self._landed(s.url or "")
                    det = self.checkpoint.detect(s)
                    if det is not None:
                        self.handle(s, det)
            prev = cur
            self.rt.sleep(max(self.actor.poll, 0.3))

    def select(self, app: str, el: dict, option: str):
        self.switch_window(app)
        snap, path = self._path_for(app, el)
        r = self.actor.select_option(snap, path, option)
        if (r.outcome is Outcome.REFUSED and ("no option" in r.reason or "pop-up" in r.reason)
                and not (r.detail or {}).get("needs_you")):
            # WebKit pop-ups may not expose their menu items to AX: pick by type-ahead instead
            snap, path = self._path_for(app, el)
            if select_by_typing(self.actor, snap, path, option):
                self.ui_actions += 1
                return self.page(app)
        self._note(r)
        return self.page(app)

    def start(self, url: str):
        """Open (first attempt) or re-load (a restart) the source's start URL in OUR window."""
        return self.open(PORTAL_APP, url)


# =================================================================== runner ==

class PackSkillRunner(SkillRunner):
    """SkillRunner with the pack's guards: a checkpoint / landing check before every step, the
    read-only verdict on every grounded control, typed values only into date / period / search
    fields, popup ``select`` steps, downloads into ctx.downloads, and Restart after a cleared
    checkpoint."""

    def __init__(self, program: dict, env: PackEnv, *, values: Dict[str, str], policy_source: P.SourceConfig,
                 repair: Any = None):
        inputs = {p["name"]: values[p["name"]] for p in program.get("params", [])
                  if p.get("scope") == "input" and p["name"] in values}
        ground = make_ground(values)
        if repair is not None:                  # opt-in: repair/core.SkillRepair re-finds a missing control
            ground = repair.wrap(ground)
        super().__init__(program, env, ground=ground, inputs=inputs, fuzzy=False)
        self.values = dict(values)
        self.psrc = policy_source
        self.downloads: List[str] = []

    def _exec(self, op: dict, vals: dict) -> None:
        n0 = self.env.resumed()
        self.env.before_op(op)
        try:
            super()._exec(op, vals)
        except Halt:
            if self.env.resumed() > n0:       # the owner cleared a wall mid-step: start over
                raise Restart(f"checkpoint cleared during step {op.get('id')}") from None
            raise

    def _resolve(self, op: dict) -> Optional[dict]:
        el = super()._resolve(op)
        if el is None:
            return None
        if op["action"] == "type":
            v = P.field_verdict({"role": "textfield", "title": el.get("name", "")})
            if not v.ok or el.get("secure"):
                self._halt("policy_refused", op, page=self.env.page(op["app"]),
                           message=f"type into {el.get('name')!r}: {v.reason if not v.ok else 'a secure field'}")
        else:
            node = {"role": el.get("role"), "title": el.get("name", ""), "url": el.get("href") or None}
            v = P.control_verdict(node, self.psrc, "press")
            if not v.ok:
                self._halt("policy_refused", op, page=self.env.page(op["app"]),
                           message=f"{op['action']} {el.get('name')!r}: {v.reason}")
            href = el.get("href")
            if href and not href.startswith(("javascript:", "#")):
                ok, why = self.env.url_ok(href)
                if not ok:
                    self._halt("policy_refused", op, page=self.env.page(op["app"]),
                               message=f"{el.get('name')!r} links off the allowlist: {why}")
        return el

    def _op_click(self, op, vals, entry):
        if not (op.get("post") or {}).get("download"):
            return super()._op_click(op, vals, entry)
        el = self._resolve(op)
        if el is None:
            entry["skipped"] = "optional"
            return
        got = self.env.download(op["app"], el)
        if got is None:
            self._halt("postcondition_failed", op, page=self.env.page(op["app"]),
                       message=f"clicking {el.get('name')!r} did not deliver a file within "
                               f"{int(self.env.download_wait_s)} s")
        self._confirm(op, "download", f"{got.name} arrived in Downloads", "SAME_SCREEN")
        self.downloads.append(got.name)
        entry["downloaded"] = got.name

    _op_download = _op_click

    def _op_type(self, op, vals, entry):
        if isinstance(op.get("value"), dict):
            self._halt("policy_refused", op, message="the pack never types secrets (the owner signs in)")
        el = self._resolve(op)
        if el is None:
            return
        value = self._value(op, vals)
        self.env.type(op["app"], el, value)
        entry["value"] = value

    def _op_select(self, op, vals, entry):
        el = self._resolve(op)
        if el is None:
            return
        from .teach import render_value
        option = render_value(str(op.get("option") or ""), {**self.values, **vals})
        opts = [o for o in el.get("options") or [] if " ".join(o.split()).lower() == " ".join(option.split()).lower()]
        if el.get("options") and not opts:
            self._halt("missing_control", op, page=self.env.page(op["app"]),
                       message=f"{el.get('name')!r} does not offer {option!r} (offers {el.get('options')[:12]})")
        v = P.option_verdict(option)
        if not v.ok:
            self._halt("policy_refused", op, message=f"option {option!r}: {v.reason}")
        self.env.select(op["app"], el, opts[0] if opts else option)
        self._observe(op["app"])
        self._check_post(op)
        entry["option"] = option


class LiveRunner:
    """The pack runner over live Safari (or a fake runtime in tests)."""

    _hygiene_done = False

    def __init__(self, *, wait_for_human: bool = False, human_timeout_s: float = CK.DEFAULT_TIMEOUT_S,
                 poll_s: float = CK.DEFAULT_POLL_S, pace: Tuple[float, float] = CK.DEFAULT_PACE,
                 skills_root: Optional[Path] = None, max_restarts: int = 3, download_wait_s: float = 60.0,
                 hygiene: bool = True, runtime: Any = None, vm_check: Optional[Callable[[], bool]] = None,
                 interrupt_rules: Sequence[Any] = (), notify: Optional[Callable[[str, str], Any]] = None,
                 human: Optional[Callable[["PackEnv", CK.HumanNeeded], None]] = None, keep_window: bool = False,
                 load_timeout: float = 20.0, settle: Optional[float] = None, home: Optional[Path] = None,
                 hygiene_fn: Optional[Callable[..., Any]] = None, allow_downloads: bool = False,
                 repair: Any = None):
        from ..live import in_guest_vm
        self.wait_for_human, self.human_timeout_s, self.poll_s = wait_for_human, human_timeout_s, poll_s
        self.pace, self.skills_root, self.max_restarts = pace, skills_root, max_restarts
        self.download_wait_s, self.hygiene, self.runtime = download_wait_s, hygiene, runtime
        self.vm_check = vm_check or in_guest_vm
        self.interrupt_rules = tuple(interrupt_rules)
        self.notify, self.human, self.keep_window = notify, human, keep_window
        self.load_timeout, self.settle, self.home = load_timeout, settle, home
        self.hygiene_fn = hygiene_fn
        self.allow_downloads = allow_downloads
        self.repair = repair                    # repair/core.SkillRepair (opt-in); None = strict replay
        self.last: Dict[str, Any] = {}

    # -- plumbing ---------------------------------------------------------------
    def _runtime(self):
        if self.runtime is None:
            from ..live.observe import MacRuntime
            self.runtime = MacRuntime()
        return self.runtime

    def _hygiene(self, log: Callable[[str], None]) -> None:
        if not self.hygiene or LiveRunner._hygiene_done:
            return
        LiveRunner._hygiene_done = True
        fn = self.hygiene_fn
        if fn is None:
            from ..live.probe import vm_hygiene as fn
        try:
            fn(out=log, vm_check=self.vm_check)
        except Exception as e:  # noqa: BLE001 -- hygiene is best effort
            log(f"hygiene failed: {type(e).__name__}: {e}")

    def make_env(self, source, ctx: RunContext, psrc: P.SourceConfig) -> PackEnv:
        from ..live.interrupts import DEFAULT_RULES, InterruptHandler
        rt = self._runtime()
        log = ctx.log or (lambda s: None)
        ih = InterruptHandler(rt, rules=(*DEFAULT_RULES, *self.interrupt_rules), log=log)
        actor = Actor(rt, allow_apps={SAFARI}, interrupts=ih, log=log, vm_check=self.vm_check,
                      url_policy=P.url_hook(psrc))
        if self.settle is not None:
            actor.settle = self.settle
        waiting = self.wait_for_human
        notify = (self.notify or (lambda t, m: CK.mac_notify(t, m, vm_check=self.vm_check))) if waiting \
            else (lambda t, m: None)
        cp = StableCheckpoint(psrc, notify=notify, digest=None, poll_s=self.poll_s,
                              timeout_s=self.human_timeout_s if waiting else 0.0, clock=rt, log=log)
        pa = CK.PackActor(actor, psrc, checkpoint=cp, pacer=CK.Pacer(*self.pace, clock=rt))
        home = Path(self.home or tempfile.mkdtemp(prefix="awos_pack_run_"))
        env = PackEnv(source, ctx, actor=pa, checkpoint=cp, home=home, waiting=waiting,
                      download_wait_s=self.download_wait_s, load_timeout=self.load_timeout, log=log)
        env.allow_downloads = self.allow_downloads
        if self.human is not None:
            cp.on_human = lambda hn: self.human(env, hn)
        return env

    @staticmethod
    def _needs(source, period, kind: str, notice: str, host: str, waited: float = 0.0) -> RunnerResult:
        k = KIND.get(kind, "unknown")
        todo = TODO.get(kind, "check the page")
        rerun = (f"python -m scaffold.agent.desktop.pack run --month {period.label} --source {source.id} --live")
        msg = (f"{source.name}: {todo} in the AWOS Safari window inside the VM"
               + (f" (waited {int(waited)} s)" if waited else "")
               + f", then re-run: {rerun}" + ("" if kind == "foreign_domain" else " (or add --wait-for-human)"))
        if kind == "foreign_domain":
            msg = f"{notice}. Check the window, then re-run: {rerun}"
        return RunnerResult(RUN_NEEDS_YOU, notice, Checkpoint(k, msg, host), {})

    # -- the contract --------------------------------------------------------------
    def __call__(self, source, period, ctx: RunContext) -> RunnerResult:
        log = ctx.log or (lambda s: None)
        detail: Dict[str, Any] = {"model_calls": 0, "attempts": 0, "restarts": []}
        self.last = detail
        if not self.vm_check():
            return RunnerResult(RUN_FAILED, "the live runner refuses to drive this Mac: it runs only inside the "
                                            "sandbox guest VM (kern.hv_vmm_present=1)", None, detail)
        program = load_skill(source, self.skills_root)
        if program is None:
            return RunnerResult(RUN_FAILED, f"no taught skill for {source.id}: teach it once inside the VM: "
                                            f"python -m scaffold.agent.desktop.pack teach --source {source.id}",
                                None, detail)
        detail["skill"] = {"id": program["skill_id"], "version": program["version"],
                           "period_bound": (program.get("pack") or {}).get("period_bound")}
        psrc = policy_source(source)
        probs = lint_program(program, psrc)
        if probs:
            return RunnerResult(RUN_FAILED, "the taught skill contains steps the read-only pack refuses: "
                                + "; ".join(probs[:3]) + " -- teach it again", None, detail)
        if not ctx.allowed(source.start_url, "navigate"):
            return RunnerResult(RUN_FAILED, "policy refused the start URL", None, detail)
        self._hygiene(log)
        env = self.make_env(source, ctx, psrc)
        values = period_values(period)
        keep = self.keep_window
        if self.repair is not None:
            self.repair.begin()
        try:
            for attempt in range(1 + self.max_restarts):
                detail["attempts"] = attempt + 1
                runner = None
                try:
                    env.start(source.start_url)
                    runner = PackSkillRunner(program, env, values=values, policy_source=psrc, repair=self.repair)
                    res = runner.run()
                except Restart as r:
                    detail["restarts"].append(str(r))
                    log(f"{source.id}: restart ({r})")
                    continue
                except (PolicyRefused, UnknownDialog) as e:      # outside the skill runner (start URL)
                    return RunnerResult(RUN_FAILED, f"halted: {e}", None, detail)
                finally:
                    if runner is not None:
                        detail.setdefault("downloads", []).extend(runner.downloads)
                detail["skill_status"] = res["status"]
                detail["steps"] = len(res.get("steps") or [])
                detail["handlers_fired"] = len(res.get("handlers_fired") or [])
                if res["status"] == "HALTED":
                    h = res.get("halt") or {}
                    detail["halt"] = {k: h.get(k) for k in ("reason", "op_id", "message")}
                    return RunnerResult(RUN_FAILED, f"halted ({h.get('reason')}) at step {h.get('op_id')}: "
                                                    f"{h.get('message')}", None, detail)
                if self.repair is not None:     # repairs are written into the skill only after a clean run
                    detail["repairs"] = self.repair.finish(program, ok=True, root=self.skills_root)
                return RunnerResult(RUN_OK, "", None, detail)
            return RunnerResult(RUN_FAILED, f"the portal kept stopping the skill ({len(detail['restarts'])} "
                                            "restarts after human checkpoints)", None, detail)
        except CheckpointStop as cs:
            keep = True                                          # the owner works in this window
            detail["checkpoint"] = {"kind": cs.kind, "host": cs.host}
            res = self._needs(source, period, cs.kind, cs.notice, cs.host, cs.waited_s)
            res.detail = detail
            return res
        finally:
            if self.repair is not None and "repairs" not in detail:
                detail["repairs"] = self.repair.finish(program, ok=False)
            detail["checkpoint_events"] = list(env.checkpoint.events)
            detail["interruptions"] = list(env.actor.interruptions)
            detail["unverified"] = len(env.unverified)
            detail["ui_actions"] = env.ui_actions
            detail["dialogs"] = list(env.dialogs)
            detail["notes"] = list(env.notes)[-10:]
            if not keep:
                try:
                    env.close()
                except Exception:  # noqa: BLE001
                    pass


def runner(source, period, ctx: RunContext) -> RunnerResult:
    """The default live runner (``--runner scaffold.agent.desktop.pack.runner:runner``).
    AWOS_PACK_WAIT_FOR_HUMAN=1 waits at checkpoints (AWOS_PACK_HUMAN_TIMEOUT seconds, default 600)."""
    wait = os.environ.get("AWOS_PACK_WAIT_FOR_HUMAN", "").strip().lower() in ("1", "true", "yes")
    timeout = float(os.environ.get("AWOS_PACK_HUMAN_TIMEOUT") or CK.DEFAULT_TIMEOUT_S)
    return LiveRunner(wait_for_human=wait, human_timeout_s=timeout)(source, period, ctx)


__all__ = ["CheckpointStop", "LiveRunner", "PackEnv", "PackSkillRunner", "PolicyRefused", "Restart",
           "StableCheckpoint", "UnknownDialog", "make_ground", "runner", "to_pack_page"]

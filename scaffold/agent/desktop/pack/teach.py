"""
teach.py — teach a pack source ONCE: the owner demonstrates, AWOS induces a replayable skill.

    python -m scaffold.agent.desktop.pack teach --source hdfc-current [--demo-month 2026-09] [--yes]
    python -m scaffold.agent.desktop.pack teach --source hdfc-current --from-trace demo.json --demo-month 2026-09

Live teaching (``record_demo``) runs ONLY inside the sandbox guest VM (kern.hv_vmm_present=1;
the capture permissions are pre-granted over SSH in the Tart image). It starts the WP4 capture
recorder (scaffold/agent/desktop/capture), opens a NEW Safari window at the source's start_url,
and the owner does the job once: logs in themselves, goes to billing / statements, picks the
demo month, clicks download; ``q`` + Enter stops. The demonstration trace (awos.demo_trace/1)
then becomes a skill (``build_skill``, pure, unit-tested):

  1. ``sanitize_trace``: login / OTP / captcha / session-expired steps are cut out. Typed
     passwords were never recorded (the recorder stores ``{"secret": <field>}``), and every
     step on such a "wall" page is dropped: at run time the wall is a HUMAN CHECKPOINT
     (live/checkpoint.py), never replayed typing. The skill is what the owner did after the
     LAST wall (a mid-demo session expiry means they redid the work after signing in again).
     Cookie-banner / "Maybe later" dismissals become guarded handlers (fire only when a step's
     control is missing). Page text is not kept (account numbers stay out of the skill).
  2. ``testbeds/teach_once/induction.induce``: an executable awos.skill_program/1 (zero model
     calls at run time). Popup selections (not part of the induction vocabulary) are re-inserted
     as ``select`` ops in step order.
  3. ``parameterise``: every place the demo month shows up -- a typed date range ("01/09/2026"
     .. "30/09/2026"), a month label in a control's name ("Download PDF CC-2026-000123 (September
     2026)"), a popup option ("September 2026"), a URL path ("/statements/2026-09") -- becomes a
     ``{period...}`` template rendered from the month being collected:
         {period_start:%d/%m/%Y}  {period_end:%d/%m/%Y}  {period_month:%B %Y}  {period_month:%Y-%m}
     A control name that carries the period gets a ``name_template`` ("Download PDF {*}
     ({period_month:%B %Y})", ``{*}`` = an id-like token such as the invoice number), which the
     runner matches against exactly one control (none / two -> halt). No period found -> the skill
     still runs but is marked ``period_bound: false`` and the teach command says so.
  4. ``lint_program``: a demo that pressed a deny-listed control (Pay now, Upgrade, Cancel ...)
     is refused; the pack is read-only.
  5. ``save_skill``: ~/.awos/pack/skills/<source>/v<N>.json + active.json, with provenance
     (trace sha256, demo month, recorder, dropped wall steps, handlers, period bound).
"""
from __future__ import annotations

import calendar
import copy
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:                     # testbeds.teach_once (induction / skill runner)
    sys.path.insert(0, str(_ROOT))

from . import Period  # noqa: E402
from . import policy as P  # noqa: E402

SKILL_SCHEMA = "awos.pack_skill/1"
PORTAL_APP = "portal"
DEFAULT_SKILLS_DIR = "~/.awos/pack/skills"
DEFAULT_TEACH_DIR = "~/.awos/pack/teach"

#: date formats a portal may show / want, day-first first (Indian portals)
DATE_FMTS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d %b %Y", "%d %B %Y",
             "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y")
#: month-of-period formats (labels, pickers, URL slugs)
MONTH_FMTS = ("%B %Y", "%b %Y", "%b-%Y", "%B, %Y", "%Y-%m", "%m/%Y", "%m-%Y", "%Y%m")
PERIOD_PARAMS = ("period", "period_start", "period_end", "period_month")
PLACEHOLDER_RE = re.compile(r"\{(\*|[a-z0-9_]+(?::[^{}]+)?)\}")
DIGIT_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*")
DISMISS_RE = re.compile(r"^(maybe later|not now|no thanks|no, thanks|dismiss|close|skip|remind me later|got it)$",
                        re.I)


class TeachRefused(RuntimeError):
    """The demonstration cannot become a safe skill; ``reasons`` say what to record instead."""

    def __init__(self, reasons: List[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


# ================================================================ periods ==

def period_of(month: str, cadence: str = "monthly") -> Period:
    y, m = int(month[:4]), int(month[5:7])
    end = dt.date(y, m, calendar.monthrange(y, m)[1])
    if cadence == "quarterly":
        return Period(month, dt.date(y, m - (m - 1) % 3, 1), end, "quarterly")
    return Period(month, dt.date(y, m, 1), end, "monthly")


def period_values(period: Period) -> Dict[str, str]:
    """The run inputs every pack skill gets (ISO dates; templates format them)."""
    return {"period": period.label, "period_start": period.start.isoformat(), "period_end": period.end.isoformat(),
            "period_month": dt.date(period.year, period.month, 1).isoformat()}


def _forms(period: Period) -> List[Tuple[str, str]]:
    """(surface text, placeholder) for every way the period may appear, longest first."""
    month = dt.date(period.year, period.month, 1)
    out: Dict[str, str] = {}
    for d, key in ((period.start, "period_start"), (period.end, "period_end")):
        for f in DATE_FMTS:
            out.setdefault(d.strftime(f), "{%s:%s}" % (key, f))
    for f in MONTH_FMTS:
        out.setdefault(month.strftime(f), "{period_month:%s}" % f)
    return sorted(out.items(), key=lambda kv: -len(kv[0]))


def _form_re(period: Period) -> Tuple[Pattern, Dict[str, str]]:
    forms = _forms(period)
    look = {s.lower(): ph for s, ph in forms}
    rx = re.compile(r"(?<![\w])(" + "|".join(re.escape(s) for s, _ in forms) + r")(?![\w])", re.I)
    return rx, look


def templatize(text: str, period: Period) -> Tuple[str, bool]:
    """Replace the period's surface forms in ``text`` with placeholders. -> (template, hit)."""
    if not isinstance(text, str) or not text:
        return text, False
    rx, look = _form_re(period)
    hit = False

    def sub(m):
        nonlocal hit
        hit = True
        return look[m.group(1).lower()]
    out = rx.sub(sub, text.replace("{", "(").replace("}", ")"))
    return out, hit


def name_template(name: str, period: Period) -> Optional[str]:
    """A control name carrying the period -> a matching template (other id-like tokens -> ``{*}``)."""
    tpl, hit = templatize(name, period)
    if not hit:
        return None
    parts, pos = [], 0
    for m in PLACEHOLDER_RE.finditer(tpl):
        parts.append(DIGIT_TOKEN_RE.sub("{*}", tpl[pos:m.start()]))
        parts.append(m.group(0))
        pos = m.end()
    parts.append(DIGIT_TOKEN_RE.sub("{*}", tpl[pos:]))
    return "".join(parts)


def render_value(tpl: str, values: Dict[str, str]) -> str:
    """Render ``{name}`` / ``{name:%fmt}`` (dates are ISO in ``values``)."""
    def sub(m):
        tok = m.group(1)
        if tok == "*":
            raise ValueError("a wildcard cannot be rendered into a value")
        name, _, fmt = tok.partition(":")
        v = values[name]
        return dt.date.fromisoformat(v).strftime(fmt) if fmt else str(v)
    return PLACEHOLDER_RE.sub(sub, tpl)


def template_regex(tpl: str, values: Dict[str, str]) -> Pattern:
    """A name template -> a full-match, case-insensitive, whitespace-tolerant regex."""
    out, pos = [], 0

    def lit(s: str) -> str:
        return "".join(r"\s+" if t.isspace() else re.escape(t) for t in re.split(r"(\s+)", s) if t)
    for m in PLACEHOLDER_RE.finditer(tpl):
        out.append(lit(tpl[pos:m.start()]))
        out.append(r"[A-Za-z0-9_-]+" if m.group(1) == "*" else lit(render_value(m.group(0), values)))
        pos = m.end()
    out.append(lit(tpl[pos:]))
    return re.compile(r"^\s*" + "".join(out) + r"\s*$", re.I)


# =============================================================== sanitize ==

def _wall_kind(text: str) -> Optional[str]:
    from ..live import checkpoint as CK
    t = text or ""
    if CK.CAPTCHA_RE.search(t):
        return "captcha"
    if CK.OTP_RE.search(t):
        return "otp"
    if CK.SESSION_RE.search(t):
        return "session_expired"
    if CK.LOGIN_RE.search(t) and not CK.LOGGED_IN_RE.search(t):
        return "login"
    return None


def _page_key(win: Dict[str, Any]) -> str:
    return re.sub(r"\d+", "#", str((win or {}).get("url_path") or "").split("?")[0])


def _page_wall(win: Dict[str, Any]) -> Optional[str]:
    if not win:
        return None
    path = str(win.get("url_path") or "").split("?")[0]
    k = _wall_kind(str(win.get("title") or ""))
    if k:
        return k
    if re.search(r"/(login|signin|sign-in|logon|auth|2fa|mfa|otp|captcha|verify)(/|$)", path, re.I):
        return "login"
    return None


def _step_wall(s) -> Optional[str]:
    """Why this step is the owner's (login / OTP / captcha), or None."""
    if s.action == "type":
        if isinstance(s.value, dict) and "secret" in s.value:
            return "login"
        node = {"role": "textfield", "title": (s.target or {}).get("name", "")}
        if (s.target or {}).get("role") == "AXSecureTextField" or P.human_only_field(node):
            return _wall_kind(node["title"]) or "login"
    return _page_wall(s.before)


def _is_logout(s) -> bool:
    from ..live import checkpoint as CK
    return bool(CK.LOGGED_IN_RE.search(str((s.target or {}).get("name") or "")))


def _is_dismiss(s) -> bool:
    name = " ".join(str((s.target or {}).get("name") or "").split()).strip(" .!")
    return s.action == "click" and bool(name) and bool(P.COOKIE_MINIMAL_RE.match(name) or DISMISS_RE.match(name))


@dataclass
class Sanitized:
    trace: Any
    walls: List[dict] = field(default_factory=list)
    handlers: List[dict] = field(default_factory=list)
    selects: List[dict] = field(default_factory=list)
    dropped: int = 0
    before_last_wall: int = 0


def sanitize_trace(trace) -> Sanitized:
    """Cut the owner-only steps out of a demonstration (see the module docstring)."""
    from testbeds.teach_once.trace import Trace
    t = Trace.from_dict(copy.deepcopy(trace.to_dict() if hasattr(trace, "to_dict") else trace))
    out = Sanitized(t)
    kept, seen_h = [], set()
    wall_pages = set()
    for s in t.steps:
        s.app = PORTAL_APP
        for win in (s.before, s.after):
            if isinstance(win, dict):
                for k in ("page_text", "tables"):
                    win.pop(k, None)
        kind = _step_wall(s)
        if kind is None and s.action == "click" and _page_key(s.before) in wall_pages:
            kind = "login"
        if kind is not None:
            wall_pages.add(_page_key(s.before))
            last = out.walls[-1] if out.walls else None
            if last is not None and last["kind"] == kind and last["url_path"] == _page_key(s.before):
                last["steps"] += 1
            else:
                out.walls.append({"kind": kind, "url_path": _page_key(s.before), "at_step": s.i, "steps": 1})
            # a wall in the middle of the demo: the owner redid the work after it
            out.dropped += 1 + len(kept)
            out.before_last_wall += len(kept)
            kept = []
            continue
        if s.action == "open_url":
            out.dropped += 1                  # the runner opens the start URL itself
            continue
        if s.action == "click" and _is_logout(s):
            out.dropped += 1                  # signing out at the end is not part of the job
            continue
        if _is_dismiss(s):
            key = ((s.target or {}).get("role"), (s.target or {}).get("name"))
            if key not in seen_h:
                seen_h.add(key)
                tgt = {"role": key[0], "name": key[1]}
                out.handlers.append({"id": f"h{len(out.handlers) + 1}", "app": PORTAL_APP,
                                     "guard": {"text_all": [], "control": tgt}, "do": [{"target": tgt}],
                                     "taught": "dismissal seen in the demonstration"})
            out.dropped += 1
            continue
        if s.action == "click" and _page_wall(s.after):
            # a click that ran into a wall (session expired): dropped; the redo comes later
            wall_pages.add(_page_key(s.after))
            out.dropped += 1 + len(kept)
            out.before_last_wall += len(kept)
            kept = []
            continue
        if s.action == "select":
            out.selects.append({"i": s.i, "target": dict(s.target or {}), "value": s.value})
        kept.append(s)
    first_kept = kept[0].i if kept else None
    out.selects = [x for x in out.selects if first_kept is not None and x["i"] >= first_kept]
    t.steps = [s for s in kept if s.action != "select"]
    for s in t.steps:
        if s.action == "click" and any(f.get("op") == "created" and "Downloads" in Path(f.get("path", "")).parts
                                       for f in s.files_touched or []):
            s.action = "download"
    return out


# =========================================================== build skill ==

def _ops(program: dict) -> List[dict]:
    return list(program["setup"]) + list((program.get("loop") or {}).get("body", [])) + list(program["finally"])


def parameterise(program: dict, period: Period) -> Dict[str, Any]:
    """Demo-month literals -> {period...} templates, in place. -> summary."""
    hits: List[dict] = []
    for op in _ops(program):
        tgt = op.get("target") or {}
        if op["action"] in ("click", "download", "select") and tgt.get("name"):
            nt = name_template(tgt["name"], period)
            if nt:
                tgt["name_template"] = nt
                hits.append({"op": op["id"], "where": "control name", "template": nt})
        if op["action"] == "type" and isinstance(op.get("value"), str):
            v, hit = templatize(op["value"], period)
            if hit:
                op["value"] = v
                hits.append({"op": op["id"], "where": "typed value", "template": v})
        if op["action"] == "select" and isinstance(op.get("option"), str):
            v, hit = templatize(op["option"], period)
            if hit:
                op["option"] = v
                hits.append({"op": op["id"], "where": "option", "template": v})
        if op["action"] == "open_url" and tgt.get("url_path"):
            v, hit = templatize(tgt["url_path"], period)
            if hit and "{*}" not in v:
                tgt["url_path"] = v
                hits.append({"op": op["id"], "where": "url", "template": v})
    return {"period_bound": bool(hits), "templates": hits}


def lint_program(program: dict, source: Optional[P.SourceConfig] = None) -> List[str]:
    """Deny-listed controls / human-only fields in a skill (the pack is read-only)."""
    probs = []
    for op in _ops(program) + [d for h in program.get("handlers", []) for d in h.get("do", [])]:
        tgt = op.get("target") or {}
        name = tgt.get("name") or ""
        role = (tgt.get("role") or "").replace("AX", "").lower() or "button"
        act = op.get("action", "click")
        if act in ("click", "download", "select") and name:
            v = P.control_verdict({"role": role, "title": name}, source, "press")
            if not v.ok:
                probs.append(f"step {op.get('id', '?')} presses {name!r}: {v.reason}")
        if act == "type":
            if isinstance(op.get("value"), dict):
                probs.append(f"step {op.get('id', '?')} types a secret into {name!r} (the owner logs in themselves)")
            elif not P.field_verdict({"role": "textfield", "title": name}).ok:
                probs.append(f"step {op.get('id', '?')} types into {name!r}: "
                             f"{P.field_verdict({'role': 'textfield', 'title': name}).reason}")
    return probs


def _trace_sha(trace) -> str:
    return hashlib.sha256(json.dumps(trace.to_dict(), sort_keys=True, default=str).encode()).hexdigest()


def build_skill(trace, *, source_id: str, demo_period: Period, start_url: str = "",
                source: Optional[P.SourceConfig] = None) -> dict:
    """A demonstration trace -> a pack skill program (raises TeachRefused)."""
    from testbeds.teach_once.induction import induce
    from testbeds.teach_once.trace import Trace
    trace = trace if isinstance(trace, Trace) else Trace.from_dict(trace)
    sz = sanitize_trace(trace)
    clean = sz.trace
    clean.task = source_id
    if not any(s.action == "download" for s in clean.steps):
        raise TeachRefused(["the demonstration has no download after sign-in: record again and click the "
                            "download / export button for the month"])
    res = induce([clean])
    if res.refused:
        raise TeachRefused(list(res.record_next) or ["induction refused the demonstration"])
    program = res.program
    for x in sz.selects:                                   # popup choices, back in step order
        op = {"app": PORTAL_APP, "action": "select", "id": f"s{x['i']}", "step": x["i"],
              "target": {"role": x["target"].get("role") or "AXPopUpButton", "name": x["target"].get("name", "")},
              "option": str(x["value"] if x["value"] is not None else "")}
        part = program["setup"]
        k = next((j for j, o in enumerate(part) if o.get("step", 0) > x["i"]), len(part))
        part.insert(k, op)
    for op in _ops(program):
        op.pop("saves", None)
    program["handlers"] = sz.handlers
    program["check"] = []                                  # the pack core checks the documents
    program["skill_id"] = program["task"] = source_id
    summary = parameterise(program, demo_period)
    probs = lint_program(program, source)
    if probs:
        raise TeachRefused([f"the demonstration did something the read-only pack never does: {p}" for p in probs])
    program["params"] = [p for p in program["params"] if p["name"] not in PERIOD_PARAMS] + [
        {"name": n, "type": "date" if n != "period" else "text", "scope": "input",
         "field_label": {"period": "pack month (YYYY-MM)", "period_start": "first day collected",
                         "period_end": "last day collected", "period_month": "the month (1st day)"}[n],
         "examples": [period_values(demo_period)[n]], "confirmed": True, "source": {"source": "pack"}}
        for n in PERIOD_PARAMS]
    program["pack"] = {"schema": SKILL_SCHEMA, "source": source_id, "start_url": start_url,
                       "demo_period": demo_period.label, "period_bound": summary["period_bound"],
                       "period_templates": summary["templates"], "checkpoints": sz.walls,
                       "handlers": len(sz.handlers), "dropped_steps": sz.dropped}
    program["provenance"][-1].update({"kind": "taught", "source": source_id, "demo_period": demo_period.label,
                                      "trace_sha256": _trace_sha(trace),
                                      "recorder": (trace.recorder or {}).get("kind", "?"),
                                      "dropped_wall_steps": sz.dropped, "walls": [w["kind"] for w in sz.walls],
                                      "period_bound": summary["period_bound"]})
    return program


# ================================================================ storage ==

def skills_dir(root: Optional[str] = None) -> Path:
    return Path(os.path.expanduser(root or os.environ.get("AWOS_PACK_SKILLS") or DEFAULT_SKILLS_DIR))


def save_skill(program: dict, root: Optional[Path] = None) -> Path:
    from testbeds.teach_once.induction import SkillLibrary, program_sha
    root = Path(root) if root else skills_dir()
    lib = SkillLibrary(root)
    have = lib.versions(program["skill_id"]) if (root / program["skill_id"]).is_dir() else []
    n = (max(have) + 1) if have else 1
    if have:
        prev = lib.load(program["skill_id"], max(have))
        program["parent"] = {"version": prev["version"], "sha256": program_sha(prev)}
    program["version"] = n
    for pv in program.get("provenance", []):
        pv["version"] = n
    return lib.save(program)


def load_skill(source, root: Optional[Path] = None) -> Optional[dict]:
    """The active skill of a source: skills/<source.id>/, else skills/<source.skill>/."""
    from testbeds.teach_once.induction import SkillLibrary
    root = Path(root) if root else skills_dir()
    lib = SkillLibrary(root)
    for sid in [getattr(source, "id", None), getattr(source, "skill", None)]:
        if sid and (root / sid / "active.json").is_file():
            return lib.load(sid)
    return None


# ============================================================ live teach ==

CONSENT = """\
AWOS will record ONE demonstration in this VM's Safari:
  * the pages of {name} you visit ({hosts}) and the buttons / links you click;
  * what you type into normal fields (date ranges, search) -- passwords and other secure
    fields are NEVER read, and everything you do on a sign-in / OTP / captcha page is dropped;
  * the file that lands in Downloads when you click download.
Nothing else, no screenshots. It stays on this computer.
"""


def policy_source(source) -> P.SourceConfig:
    """pack.policy.SourceConfig for a config Source (loopback-only test sources get a placeholder
    domain; loopback stays allowed by the policy itself)."""
    hosts = [h[2:] if h.startswith("*.") else h for h in getattr(source, "allowed_domains", ())
             if h not in ("127.0.0.1", "localhost", "::1")]
    return P.SourceConfig(name=source.name, domains=tuple(hosts) or ("loopback.invalid",), allow_loopback=True)


def _origins(source) -> List[str]:
    u = urllib.parse.urlsplit(source.start_url)
    out = [f"{u.scheme}://{u.netloc}"]
    for h in source.allowed_domains:
        if not h.startswith("*.") and h not in ("127.0.0.1", "localhost"):
            o = f"https://{h}"
            if o not in out:
                out.append(o)
    return out


class _DownloadsMirror:
    """Copies files that finish in the real ~/Downloads into the recorder's scratch home, so the
    recorder (which watches only the scratch home) sees the download."""

    def __init__(self, real: Path, mirror: Path, poll: float = 0.5):
        from .files import snapshot
        self.real, self.mirror, self.poll = Path(real), Path(mirror), poll
        self.before = snapshot(self.real)
        self.stop = threading.Event()
        self.copied: List[str] = []

    def run(self) -> None:
        from .files import _new_entries
        prev: Dict[str, Any] = {}
        while not self.stop.wait(self.poll):
            cur, _partials = _new_entries(self.real, self.before)
            for name, v in cur.items():
                if name in self.copied or prev.get(name) != v:      # copy once its size held for a poll
                    continue
                self.mirror.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(self.real / name, self.mirror / name)
                    self.copied.append(name)
                except OSError:
                    pass
            prev = cur


def record_demo(source, *, demo_month: str, out_dir: Path, yes: bool = False,
                vm_check: Optional[Callable[[], bool]] = None, say: Callable[[str], None] = print,
                ask: Callable[[str], str] = input) -> Any:
    """Record the owner's demonstration in the guest VM. -> Trace (raises RuntimeError on refusal)."""
    from ..live import in_guest_vm
    if not (vm_check or in_guest_vm)():
        raise RuntimeError("teach records only inside the sandbox guest VM (kern.hv_vmm_present != 1); "
                           "on the host run: python -m scaffold.agent.desktop.live vm-run -- "
                           f"python -m scaffold.agent.desktop.pack teach --source {source.id}")
    from testbeds.teach_once.trace import Recorder

    from ..capture.__main__ import JsonlLog
    from ..capture.adapter import NativeAdapter
    from ..capture.live import MacRecorder
    from ..capture.permissions import load_frameworks, require_permissions
    from ..live.actions import SAFARI, Actor
    from ..live.observe import MacRuntime
    fw = load_frameworks()
    require_permissions(fw, request=False)
    out_dir = Path(out_dir)
    home = out_dir / "home"
    (home / "Downloads").mkdir(parents=True, exist_ok=True)
    apps = [{"name": PORTAL_APP, "kind": "web", "base_url": o} for o in _origins(source)]
    say(CONSENT.format(name=source.name, hosts=", ".join(a["base_url"] for a in apps)))
    if not yes and ask("Type yes to start recording: ").strip().lower() != "yes":
        raise RuntimeError("not recording (no consent)")
    rec = Recorder(home, task=source.id, instance=f"demo-{demo_month}", context={}, kind="macos_ax_fsevents",
                   title=f"{source.name}: download the {demo_month} document", apps=apps,
                   note=f"pack teach {source.id} {demo_month}")
    log = JsonlLog(out_dir / "events.jsonl")
    adapter = NativeAdapter(rec.feed, home=home, apps=apps, log=log)
    adapter.feed({"src": "session", "event": "start", "t": time.time(), "task": source.id,
                  "instance": f"demo-{demo_month}", "context": {}, "apps": apps, "home": str(home),
                  "watch": [str(home)], "consent": {"granted": True, "at": time.time(),
                                                    "via": "--yes" if yes else "typed yes",
                                                    "notice_sha256": hashlib.sha256(CONSENT.encode()).hexdigest()}})
    mirror = _DownloadsMirror(Path.home() / "Downloads", home / "Downloads")
    threading.Thread(target=mirror.run, daemon=True).start()
    live = MacRecorder(adapter.feed, watch=[str(home)], frameworks=fw)
    stop = threading.Event()
    t = threading.Thread(target=live.run, args=(stop,), daemon=True)
    t.start()
    time.sleep(1.0)
    actor = Actor(MacRuntime(), allow_apps={SAFARI}, url_policy=P.url_hook(policy_source(source)))
    r = actor.open_url(source.start_url)
    if r.window is None:
        say(f"could not open a Safari window ({r.reason}); open {source.start_url} yourself")
    p = period_of(demo_month, getattr(source, "cadence", "monthly"))
    say(f"\nNow, in the new Safari window: sign in yourself (AWOS never sees it), go to the "
        f"{source.kind.replace('_', ' ')} page, choose {p.start:%B %Y} "
        f"({p.start:%d/%m/%Y} - {p.end:%d/%m/%Y}), and click download.\n"
        "Then type q + Enter here.\n")
    try:
        while True:
            line = ask("")
            if line.strip().lower().startswith("q"):
                break
    except (EOFError, KeyboardInterrupt):
        pass
    time.sleep(1.5)                       # let the last download land
    stop.set()
    mirror.stop.set()
    t.join(timeout=5)
    adapter.feed({"src": "session", "event": "stop", "t": time.time()})
    trace = adapter.finish(rec, web_with_text=False)
    log.close()
    trace.save(out_dir / "trace.json")
    if live.errors:
        say(f"{len(live.errors)} capture errors (first: {live.errors[0]})")
    return trace


def teach(source, *, demo_month: str, trace=None, skills_root: Optional[Path] = None,
          teach_root: Optional[Path] = None, yes: bool = False, vm_check: Optional[Callable[[], bool]] = None,
          say: Callable[[str], None] = print, ask: Callable[[str], str] = input) -> Dict[str, Any]:
    """Record (or take ``trace``), build, save. -> {"path", "program", "warnings"}."""
    from testbeds.teach_once.trace import Trace
    if trace is None:
        out = Path(os.path.expanduser(str(teach_root or DEFAULT_TEACH_DIR))) / source.id / time.strftime(
            "%Y%m%d_%H%M%S")
        trace = record_demo(source, demo_month=demo_month, out_dir=out, yes=yes, vm_check=vm_check, say=say, ask=ask)
    elif not isinstance(trace, Trace):
        trace = Trace.load(Path(trace))
    period = period_of(demo_month, getattr(source, "cadence", "monthly"))
    program = build_skill(trace, source_id=source.id, demo_period=period, start_url=source.start_url,
                          source=policy_source(source))
    path = save_skill(program, skills_root)
    warnings = []
    if not program["pack"]["period_bound"]:
        warnings.append(f"no {demo_month} date or month label was chosen in the demonstration, so the skill "
                        "downloads whatever the portal offers; the pack's checks flag a wrong month")
    if not getattr(source, "skill", None):
        warnings.append(f'add  skill = "{source.id}"  to the [[source]] {source.id} entry in sources.toml')
    return {"path": str(path), "program": program, "warnings": warnings}


__all__ = ["PORTAL_APP", "SKILL_SCHEMA", "Sanitized", "TeachRefused", "build_skill", "lint_program", "load_skill",
           "name_template", "parameterise", "period_of", "period_values", "policy_source", "record_demo",
           "render_value", "sanitize_trace", "save_skill", "skills_dir", "teach", "template_regex", "templatize"]

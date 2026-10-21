"""
micro_wiring.py — adapters that run the WP8 micro-agent on the real WP6 / WP7
pieces instead of its local stand-ins.

    micro_agent Protocol   real implementation (this module)          stand-in (micro_agent)
    --------------------   -----------------------------------------  ----------------------
    Grounder               WebAXGrounder -> grounding.ground (WP6)     IndexGrounder
    SecretStore            BrokerSecretStore -> secrets.SecretBroker   LocalSecretStore
    SecretSink             EffectsSecretSink -> effects.write_field     LocalFileSecretSink
                           (+ FieldEquals verify, own-write rollback;
                           or Enforcer.write_secret_field when given)
    EffectCheck            FieldEqualsCheck -> effects.FieldEquals      FileFieldEquals
    key creation           plan_key_creation + effects.run_once         forbid_before_write regex
                           (OperationLedger: a retry never makes a
                           second key)

Selection: ``AWOS_MICRO_WIRING=real`` (default) | ``local`` (the stand-ins),
or ``run_dev_dashboard(..., wiring="local")`` / ``micro_agent --stand-ins``.
Secrets vault: ``AWOS_MICRO_SECRETS=memory`` (default) | ``keyring`` (macOS
Keychain through jaraco keyring, service ``awos-micro``).

Grounding on web pages. web_tools gives the model numbered controls, not an
AX tree. ``page_tree`` rebuilds a small AX-like tree from the parsed page:
one ``webarea`` root, one ``group`` per form / region context, a single
``alertdialog`` holding every dialog control (so WP6's modal gate refuses a
click on anything underneath an open dialog), one node per control with a
synthetic frame (so the visibility gate has a centre).
The control the model picked becomes a WP6 ``Target`` (role + accessible
name + its container), and ``ground`` must resolve that Target back to the
very same node: two controls that the role, name and container cannot tell
apart are an ``ambiguous`` refusal (-> needs_user), as are modal-blocked,
risk-escalation and disabled refusals. The relaxed (renamed-control) tier is
off: the model chose an exact control on this very page, so a fuzzy match
would only ever be a different control.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import grounding as wp6
from .effects import (BeforeEffect, CheckLevel, EffectVerdict, FieldEquals, FieldWriteRefused,
                      FileRestore, OperationLedger, OpResult, OpState, Outcome as OpOutcome,
                      snapshot, write_field)
from .effects.outcome import confirmed, indeterminate, refuted
from .secrets import (KeyringProvider, MemoryProvider, MissingSecret, SecretBroker,
                      SecretProvider, plan_key_creation)
from .secrets import handle as secret_handle
from .web_tools import Control, LoopbackBrowser, Page, WebError, mask_secrets

WIRING_ENV = "AWOS_MICRO_WIRING"          # real (default) | local
SECRETS_ENV = "AWOS_MICRO_SECRETS"        # memory (default) | keyring
LEDGER_ENV = "AWOS_MICRO_LEDGER"          # path of the operation ledger (JSONL)
KEYRING_SERVICE = "awos-micro"
_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def wiring_from_env(value: Optional[str] = None) -> str:
    raw = (value if value is not None else os.environ.get(WIRING_ENV, "")).strip().lower()
    if raw in ("local", "stand-ins", "standins", "stub", "fallback"):
        return "local"
    return "real"


# ---------------------------------------------------------------------------
# Grounder (WP6)
# ---------------------------------------------------------------------------
#: web_tools control role -> AX role understood by grounding / ax_compress
_AX_ROLE = {"link": "link", "button": "button", "textbox": "textfield", "password": "securetextfield",
            "radiogroup": "radiogroup", "combobox": "popupbutton", "checkbox": "checkbox",
            "secret_text": "statictext"}
_ROW_PX = 30


def page_tree(page: Page) -> Tuple[dict, Dict[Tuple[int, ...], int]]:
    """An AX-like tree for ``page`` + {node path: control number}. Names are
    the (already masked) accessible names; values are never copied in."""
    paths: Dict[Tuple[int, ...], int] = {}

    def node(c: Control) -> dict:
        return {"role": _AX_ROLE.get(c.role, c.role), "title": c.name,
                "frame": [0, _ROW_PX * c.n, 300, 20]}

    def layer(controls: List[Control], base: Tuple[int, ...]) -> List[dict]:
        """Loose controls, then one ``group`` per context, in page order."""
        out: List[dict] = []
        groups: Dict[str, List[Control]] = {}
        order: List[str] = []
        for c in controls:
            ctx = (c.context or "").strip()
            if not ctx:
                paths[base + (len(out),)] = c.n
                out.append(node(c))
                continue
            if ctx not in groups:
                groups[ctx] = []
                order.append(ctx)
            groups[ctx].append(c)
        for ctx in order:
            gi = len(out)
            kids = []
            for j, c in enumerate(groups[ctx]):
                paths[base + (gi, j)] = c.n
                kids.append(node(c))
            out.append({"role": "group", "title": ctx, "children": kids})
        return out

    children = layer([c for c in page.controls if not c.in_dialog], (0,))
    dialog = [c for c in page.controls if c.in_dialog]
    if dialog:                          # one modal: its controls are above everything else
        di = len(children)
        children.append({"role": "alertdialog", "title": _dialog_name(page),
                         "children": layer(dialog, (0, di))})
    return {"role": "webarea", "title": page.title, "children": children}, paths


def _dialog_name(page: Page) -> str:
    return ((page.dialog or "").strip().splitlines() or ["dialog"])[0][:120] or "dialog"


def control_target(c: Control, page: Optional[Page] = None) -> wp6.Target:
    container: List[Tuple[str, str]] = []
    if c.in_dialog:
        container.append(("alertdialog", _dialog_name(page) if page is not None else "dialog"))
    ctx = (c.context or "").strip()
    if ctx:
        container.append(("group", ctx))
    return wp6.Target(role=_AX_ROLE.get(c.role, c.role), name=c.name, container=tuple(container))


class WebAXGrounder:
    """micro_agent ``Grounder`` backed by WP6 ``grounding.ground``."""

    def __init__(self, min_confidence: float = wp6.DEFAULT_MIN_CONFIDENCE):
        self.min_confidence = min_confidence
        self.last: Optional[wp6.Grounding] = None

    def resolve(self, page: Page, n: int):
        from .micro_agent import Grounding           # local import: micro_agent imports us lazily
        c = page.control(n)
        if c is None:
            return Grounding(None, f"control {n} is not on this page")
        if not c.name.strip():
            return Grounding(None, f"control {n} has no accessible name")
        tree, paths = page_tree(page)
        g = wp6.ground(tree, control_target(c, page), allow_relaxed=False, min_confidence=self.min_confidence)
        self.last = g
        if not g.ok:
            r = g.refusal
            cands = [paths.get(tuple(x.get("path") or ()), None) for x in (r.candidates if r else [])]
            cands = [x for x in cands if x is not None]
            label = f"{c.role} \"{mask_secrets(c.name)}\""
            if r is not None and r.reason == "ambiguous":
                return Grounding(None, f"ambiguous control: {len(cands) or 'several'} controls look like "
                                       f"{label} ({cands})")
            return Grounding(None, f"grounding refused ({r.reason if r else 'unknown'}) for {label}: "
                                   f"{mask_secrets(r.message if r else '')}")
        got = paths.get(tuple(g.match.path))
        if got != c.n:
            return Grounding(None, f"ambiguous control: grounding resolved control {n} to control {got}")
        return Grounding(c)


# ---------------------------------------------------------------------------
# SecretStore (WP7 broker)
# ---------------------------------------------------------------------------
class BrokerSecretStore:
    """micro_agent ``SecretStore`` backed by ``secrets.SecretBroker``.

    put -> broker.store (the value is remembered for redaction); redact ->
    broker.redact (known values -> their handle, key formats -> [REDACTED])
    then web_tools masking; ``mask_observation`` masks an observation with
    broker.mask_text before the model sees it."""

    def __init__(self, broker: Optional[SecretBroker] = None, provider: Optional[SecretProvider] = None):
        self.broker = broker if broker is not None else SecretBroker(provider or MemoryProvider())
        self._names: List[str] = []

    def put(self, name: str, value: str) -> None:
        if not _IDENT_RE.match(name or ""):
            raise ValueError("bad secret name")
        self.broker.store(name, value)
        if name not in self._names:
            self._names.append(name)

    def get(self, name: str) -> str:
        try:
            return self.broker.reveal_for_check(name)
        except MissingSecret as ex:
            raise KeyError(name) from ex

    def has(self, name: str) -> bool:
        if not self.broker.has(name):
            return False
        try:
            return bool(self.broker.reveal_for_check(name))   # also remembers it for redaction
        except MissingSecret:
            return False

    def names(self) -> List[str]:
        return sorted(self._names)

    def handle(self, name: str) -> str:
        return secret_handle(name)

    def redact(self, text: str) -> str:
        return mask_secrets(self.broker.redact(text))

    def mask_observation(self, text: str) -> str:
        masked, _rep = self.broker.mask_text(text)
        return masked


def make_store(kind: Optional[str] = None) -> BrokerSecretStore:
    kind = (kind if kind is not None else os.environ.get(SECRETS_ENV, "memory")).strip().lower()
    if kind == "keyring":
        return BrokerSecretStore(provider=KeyringProvider(service=KEYRING_SERVICE))
    return BrokerSecretStore(provider=MemoryProvider())


# ---------------------------------------------------------------------------
# SecretSink + EffectCheck (WP7 effects)
# ---------------------------------------------------------------------------
class EffectsSecretSink:
    """micro_agent ``SecretSink``: the handle is resolved only here, written
    with effects.write_field (minimal edit, atomic, mode kept), verified with
    FieldEquals (exactly once, no collateral) and rolled back from its exact
    pre-image when refuted. With ``enforcer`` the whole thing goes through
    Enforcer.write_secret_field (policy check + audit) instead."""

    def __init__(self, store: BrokerSecretStore, enforcer: Any = None):
        self.store = store
        self.enforcer = enforcer
        self.last: Dict[str, Any] = {}               # redacted receipt (no values)

    def write_field(self, path: Path, fmt: str, field_name: str, secret: str):
        from .micro_agent import EffectResult
        p = Path(path)
        h = secret_handle(secret)
        if self.enforcer is not None:
            rc = self.enforcer.write_secret_field(p, fmt, field_name, h)
            self.last = dict(rc.record)
            return EffectResult(rc.ok, f"{rc.outcome.value}: {rc.record.get('reason') or rc.record.get('write', '')}")
        broker = self.store.broker
        try:
            plain = broker.substitute(h, target=f"file:{p}")
        except Exception as ex:                       # MissingSecret / SecretNotAllowedHere
            self.last = {"outcome": OpOutcome.HALTED_BEFORE_EFFECT.value, "reason": type(ex).__name__}
            return EffectResult(False, f"halted before effect: {type(ex).__name__}")
        eff = FieldEquals.for_value(p, fmt, field_name, plain)
        before = eff.capture()
        pre_bytes = p.read_bytes() if p.exists() else None
        try:
            wr = write_field(p, fmt, field_name, plain)
        except FieldWriteRefused as ex:
            self.last = {"outcome": OpOutcome.HALTED_BEFORE_EFFECT.value, "reason": str(ex)}
            return EffectResult(False, f"halted before effect: {self.store.redact(str(ex))}")
        finally:
            del plain
        v = eff.verify(before)
        self.last = {"write": wr.action, "verdict": v.to_record()}
        if v.confirmed:
            return EffectResult(True, f"{wr.action}; {v.reason}")
        if wr.changed:
            undone = FileRestore(p, pre_bytes, wr.pre_mode, wr.post_sha256).undo(v)
            self.last["rolled_back"] = bool(undone)
        return EffectResult(False, f"refuted: {v.reason}")


class FieldEqualsCheck:
    """micro_agent ``EffectCheck`` over effects.FieldEquals: built before the
    write (snapshot), called after it. Only a digest of the value is kept."""

    def __init__(self, path: Path, fmt: str, field_name: str, store: Any, secret: str):
        self.path, self.fmt, self.field, self.store, self.secret = Path(path), fmt, field_name, store, secret
        self.before = snapshot(self.path)
        self.verdict: Optional[EffectVerdict] = None

    def __call__(self):
        from .micro_agent import EffectResult
        if not self.store.has(self.secret):
            return EffectResult(False, f"secret {self.secret} not in the vault")
        self.verdict = FieldEquals.for_value(self.path, self.fmt, self.field,
                                             self.store.get(self.secret)).verify(self.before)
        return EffectResult(self.verdict.confirmed, self.verdict.reason)


# ---------------------------------------------------------------------------
# key creation: plan_key_creation + run_once
# ---------------------------------------------------------------------------
KEY_TABLE_WAIT_S = 10.0                     # live Safari: how long to wait for the key table


def _keys_path(project: str) -> str:
    return f"/projects/{project}/keys"


def key_rows_why(page: Optional[Page], project: Optional[str] = None) -> str:
    """Why ``parse_key_rows`` refuses this page (diagnostic only: path, status, flags and
    shape -- never cell values). Empty string when the page parses."""
    if page is None:
        return "no page"
    if page.status >= 400:
        return f"http {page.status}"
    if project is not None and page.url.startswith("http"):
        got = urllib.parse.urlsplit(page.url).path.rstrip("/")
        if got != _keys_path(project):
            return f"on {got or '/'} not {_keys_path(project)}"
    live = getattr(page, "_live", None)
    if isinstance(live, dict):
        if live.get("truncated"):
            snap = live.get("snap")
            return f"AX read truncated ({getattr(snap, 'reason', '') or '?'})"
        if live.get("settled") is False:
            return "page never settled"
    piped = sum(1 for ln in page.text.splitlines() if " | " in ln or ln.rstrip().endswith(" |"))
    if parse_key_rows(page, project) is None:
        return f"no key table header (lines={len(page.text.splitlines())}, table lines={piped})"
    return ""


def parse_key_rows(page: Page, project: Optional[str] = None) -> Optional[List[Dict[str, str]]]:
    """The API-key table of an already-open page as rows {name, status, scope, last4}; None
    when this page cannot certify the list: HTTP error, a different page (path is not this
    project's keys page), a partial read (live AX snapshot truncated / never settled), or no
    table whose header names ``name`` and ``status``.

    Tolerant of how live Safari flattens the AX table: the header is found by content (not
    "first ` | ` line"), an empty trailing Actions cell is fine, the key column is masked
    (``fk_live_…abcd`` or ``...abcd``; last4 left empty when not shown)."""
    if page is None or page.status >= 400:
        return None
    if project is not None and page.url.startswith("http"):
        got = urllib.parse.urlsplit(page.url).path.rstrip("/")
        if got != _keys_path(project):
            return None
    live = getattr(page, "_live", None)
    if isinstance(live, dict) and (live.get("truncated") or live.get("settled") is False):
        return None                                  # half a table must never read as "absent"
    lines = [ln for ln in page.text.splitlines() if " | " in ln or ln.rstrip().endswith(" |")]
    head: List[str] = []
    hi = -1
    for i, ln in enumerate(lines):
        cells = [h.strip().lower() for h in ln.split("|")]
        if "name" in cells and "status" in cells:
            head, hi = cells, i
            break
    if hi < 0:
        return None
    rows: List[Dict[str, str]] = []
    for ln in lines[hi + 1:]:
        cells = [x.strip() for x in ln.split("|")]
        if [c.lower() for c in cells] == head:       # a repeated header (AX header container)
            continue
        if len(cells) < len(head) - 1:
            continue
        rec = {h: (cells[i] if i < len(cells) else "") for i, h in enumerate(head)}
        m = re.search(r"(?:…|\.\.\.)\s*([A-Za-z0-9]{4})\b", rec.get("key", ""))
        rows.append({"name": rec.get("name", ""), "status": rec.get("status", "").lower(),
                     "scope": rec.get("scope", ""), "last4": m.group(1) if m else ""})
    return rows


def read_key_rows(br: LoopbackBrowser, project: str,
                  wait_s: float = KEY_TABLE_WAIT_S) -> Optional[List[Dict[str, str]]]:
    """The project's API-key table as rows {name, status, scope, last4}; None when the page
    cannot be read (not signed in, HTTP error, no table, or only part of it).

    Always navigates explicitly to /projects/<id>/keys (never trusts whatever page the
    agent left the browser on). On a live browser (``br.wait_for``) a page that does not
    yet show the full table is re-read in place until it does, then re-opened once; the
    loopback HTTP client gets complete pages, so it reads once."""
    global LAST_KEY_READ_WHY
    path = _keys_path(project)
    live = callable(getattr(br, "wait_for", None))
    LAST_KEY_READ_WHY = ""
    for _attempt in range(2 if live else 1):
        try:
            p = br.open(path)
        except WebError as e:
            LAST_KEY_READ_WHY = f"open failed: {e}"
            continue
        rows = parse_key_rows(p, project)
        if rows is not None:
            return rows
        LAST_KEY_READ_WHY = key_rows_why(p, project)
        if p.status >= 400:
            return None                              # 404 / 403: waiting will not help
        if live:
            got = br.wait_for(lambda q: parse_key_rows(q, project) is not None, wait_s)
            if got is not None:
                return parse_key_rows(got, project)
            cur = getattr(br, "page", None)
            if isinstance(cur, Page):
                LAST_KEY_READ_WHY = key_rows_why(cur, project) + f" after {wait_s:g}s"
    return None


LAST_KEY_READ_WHY = ""                      # diagnostic of the last failed read_key_rows (no values)


def key_verdict(br: LoopbackBrowser, project: str, label: str, store: BrokerSecretStore,
                secret: str) -> EffectVerdict:
    """System-of-record read for "is our key there, exactly once, and do we
    hold its value?" (re-opened dashboard list; last4 must match the vault)."""
    sub, lvl = "dashboard_keys", CheckLevel.REOPENED
    rows = read_key_rows(br, project)
    if rows is None:
        why = f" ({LAST_KEY_READ_WHY})" if LAST_KEY_READ_WHY else ""
        return indeterminate(sub, f"could not read the key list of {project}{why}", lvl)
    active = [r for r in rows if r["name"] == label and r["status"] == "active"]
    if not active:
        return refuted(sub, f"no active key {label!r} in {project}", lvl, observed_count=0,
                       expected_count=1, absent=True)
    if len(active) > 1:
        return refuted(sub, f"{len(active)} active keys named {label!r} (duplicate)", lvl,
                       observed_count=len(active), expected_count=1)
    if not store.has(secret):
        return refuted(sub, f"key {label!r} exists but its value is not in the vault", lvl,
                       observed_count=1, expected_count=1)
    l4 = active[0].get("last4") or ""
    if l4 and not store.get(secret).endswith(l4):
        return refuted(sub, f"key {label!r} exists but does not match the vault value", lvl,
                       observed_count=1, expected_count=1)
    return confirmed(sub, f"key {label!r} active once in {project}; value held as {secret_handle(secret)}",
                     lvl, observed_count=1, expected_count=1)


@dataclass
class KeyCreation:
    """What happened to "create key" (a ``MicroResult`` for the report + the
    ledger record)."""
    result: Any                        # micro_agent.MicroResult
    action: str                        # create | reuse | needs_user
    op: Optional[OpResult] = None


def create_key_once(agent: Any, subtask: Any, *, br: LoopbackBrowser, store: BrokerSecretStore,
                    ledger: OperationLedger, service: str, project: str, scope: str, label: str,
                    secret: str) -> KeyCreation:
    """Idempotent key creation for the micro-agent.

    1. plan_key_creation over the dashboard's key list: an active ``label``
       key whose value the vault holds -> reuse (nothing is created); one we
       cannot read -> needs_user (never a silent duplicate); none -> create.
    2. create runs the micro subtask inside effects.run_once: the payload
       (service, project, scope, label, handle) is hashed into the ledger;
       an earlier SUCCEEDED op is not run again, an earlier uncertain op is
       verified against the dashboard first and only re-run when the key is
       provably absent. The subtask itself still asks the owner before the
       first write (approval binds the pinned name + scope)."""
    from .micro_agent import MicroResult, Outcome
    rows = read_key_rows(br, project)
    if rows is None:                                  # cannot see what exists -> do not create blind
        return KeyCreation(MicroResult(subtask.name, Outcome.NEEDS_USER,
                                       reason=f"could not read the key list of {project} before creating "
                                              f"(not risking a duplicate){'; ' + LAST_KEY_READ_WHY if LAST_KEY_READ_WHY else ''}"),
                            "needs_user")
    kp = plan_key_creation(rows, label, store.broker, secret)
    if kp.action == "needs_user":
        return KeyCreation(MicroResult(subtask.name, Outcome.NEEDS_USER,
                                       reason=f"{kp.reason} (not creating a duplicate)"), "needs_user")
    if kp.action == "reuse":
        return KeyCreation(MicroResult(subtask.name, Outcome.VERIFIED,
                                       reason=f"reused: {kp.reason}", summary="existing key reused"), "reuse")

    holder: Dict[str, Any] = {}
    payload = {"service": service, "project": project, "scope": scope, "label": label,
               "secret": secret_handle(secret)}

    def execute() -> str:
        r = agent.run(subtask)
        holder["r"] = r
        if r.outcome is Outcome.VERIFIED:
            return secret_handle(secret)
        if r.writes == 0:
            raise BeforeEffect(f"{r.outcome.value}: {r.reason}")
        raise RuntimeError(f"write happened but the subtask ended {r.outcome.value}")

    from .effects import run_once
    op = run_once(ledger, "create_credential", payload, execute=execute,
                  verify=lambda: key_verdict(br, project, label, store, secret))
    r = holder.get("r")
    if r is None:                                     # not executed: the ledger decided
        if op.outcome is OpOutcome.VERIFIED and store.has(secret):
            r = MicroResult(subtask.name, Outcome.VERIFIED, reason=f"ledger: {op.reason}")
        else:
            r = MicroResult(subtask.name, Outcome.NEEDS_USER,
                            reason=f"ledger {op.state.value}: {op.reason}; the owner must reconcile "
                                   "(not creating another key)")
    elif op.outcome is OpOutcome.VERIFIED:
        pass
    elif op.outcome is OpOutcome.HALTED_BEFORE_EFFECT:
        pass                                          # r already says needs_user / denied / halted
    elif r.outcome is Outcome.VERIFIED:               # agent says yes, the dashboard says no
        r.outcome = Outcome.UNVERIFIED
        r.reason = f"dashboard check: {op.verdict.reason if op.verdict else op.reason}"
    return KeyCreation(r, "create", op)


def ledger_for(sandbox: Path) -> OperationLedger:
    path = os.environ.get(LEDGER_ENV, "").strip()
    return OperationLedger(Path(path) if path else Path(sandbox) / ".awos" / "micro_operations.jsonl")


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
def default_grounder(wiring: Optional[str] = None):
    if wiring_from_env(wiring) == "local":
        from .micro_agent import IndexGrounder
        return IndexGrounder()
    return WebAXGrounder()


def default_store(wiring: Optional[str] = None):
    if wiring_from_env(wiring) == "local":
        from .micro_agent import LocalSecretStore
        return LocalSecretStore()
    return make_store()


def default_sink(store: Any, wiring: Optional[str] = None):
    if wiring_from_env(wiring) == "local" or not isinstance(store, BrokerSecretStore):
        from .micro_agent import LocalFileSecretSink
        return LocalFileSecretSink(store)
    return EffectsSecretSink(store)


def field_check(path: Path, fmt: str, field_name: str, store: Any, secret: str, before_text: str,
                wiring: Optional[str] = None) -> Callable[[], Any]:
    if wiring_from_env(wiring) == "local":
        from .micro_agent import FileFieldEquals
        return FileFieldEquals(path, fmt, field_name, store, secret, before_text)
    return FieldEqualsCheck(path, fmt, field_name, store, secret)

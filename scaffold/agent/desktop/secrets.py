"""
secrets.py — the secrets broker (WP7): the model only ever sees handles.

    broker = SecretBroker(KeyringProvider())        # macOS Keychain via jaraco keyring
    broker.handle("fakeservice_key")  -> "{{secret:fakeservice_key}}"

Model side (prompts, tool arguments, transcripts, spans, receipts) carries
``{{secret:<name>}}`` handles only. A value is turned back into plaintext in
exactly one place: at the SINK, inside the enforcer (``enforce.py``), right
before the side effect (typing into a password field, writing a .env value,
posting a login form). Everything flowing back toward the model is masked or
redacted on the way.

Pieces:
  providers      MemoryProvider (tests / ephemeral), KeyringProvider (jaraco
                 keyring, MIT; the macOS Keychain backend by default, or any
                 injected keyring backend), ChainProvider.
  SecretBroker   handle()/store()/has(); substitute(text, target=...) for the
                 enforcer only (fails fast on an unknown handle, honours
                 optional handle -> target bindings); redact()/redact_obj()
                 for transcripts, receipts and spans (known values -> their
                 handle, then every key format in secret_scan -> [REDACTED:rule]);
                 mask_observation()/mask_text() for AX trees / page text.
  masking        masks AXSecureTextField / password values AND key-looking
                 strings on pages that reveal a key ("Copy this key now..."),
                 not only secure fields. With ``capture_as=<name>`` a single
                 revealed key is stored in the vault and the model sees its
                 handle instead, so it can later say "write {{secret:name}}
                 to .env" without ever seeing the key. Two candidates -> no
                 capture (refuse rather than guess).
  ClipboardGuard copy a secret for a paste sink, then clear the clipboard;
                 clear_if_secret() wipes it if it holds any known value.
  credentials    is_credential_create(label): "Create key", "Generate new
                 token", ... -> the enforcer routes it as an approval-required
                 CREDENTIAL write. plan_key_creation(): idempotency guidance
                 (reuse an existing AWOS-labelled key whose value we hold;
                 an AWOS key we cannot read -> needs_user, never a silent
                 duplicate; none -> create). orphaned_awos_keys(): cleanup
                 candidates (revocation is an owner-approved action).

The broker keeps the values it has seen in process memory (needed to redact
them); it never serialises them (``repr`` shows names only, pickling is
refused).
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple

from .. import secret_scan

HANDLE_RE = re.compile(r"\{\{secret:([A-Za-z0-9_.\-]{1,64})\}\}")
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
DEFAULT_SERVICE = "awos"
SECURE_MASK = "[MASKED:secure]"


def handle(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise ValueError(f"bad secret name {name!r}")
    return "{{secret:" + name + "}}"


def handles_in(text: Any) -> List[str]:
    return HANDLE_RE.findall(text) if isinstance(text, str) else []


class MissingSecret(KeyError):
    """A handle names a secret the vault does not have (fail fast at the sink)."""


class SecretNotAllowedHere(PermissionError):
    """A handle was bound to other sinks than this one."""


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------

class SecretProvider(Protocol):
    def get(self, name: str) -> Optional[str]: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class MemoryProvider:
    def __init__(self, values: Optional[Dict[str, str]] = None):
        self._v: Dict[str, str] = dict(values or {})

    def get(self, name: str) -> Optional[str]:
        return self._v.get(name)

    def set(self, name: str, value: str) -> None:
        self._v[name] = value

    def delete(self, name: str) -> None:
        self._v.pop(name, None)

    def __repr__(self) -> str:
        return f"MemoryProvider(names={sorted(self._v)})"

    def __reduce__(self):
        raise TypeError("secret providers are not serialisable")


class KeyringProvider:
    """jaraco ``keyring`` (MIT) as the vault: service ``awos``, username = name.

    ``backend`` may be any object with ``get_password/set_password/
    delete_password(service, username)`` -- the ``keyring`` module itself
    (default, imported lazily; on macOS it uses the login Keychain) or a
    keyring backend instance. Tests inject an in-memory backend so the real
    Keychain is never touched.
    """

    def __init__(self, service: str = DEFAULT_SERVICE, backend: Any = None):
        self.service = service
        self._backend = backend

    @property
    def backend(self):
        if self._backend is None:
            try:
                import keyring  # type: ignore  # optional dependency (MIT)
            except ImportError as ex:   # pragma: no cover - depends on env
                raise RuntimeError("the 'keyring' package is not installed; "
                                   "pip install keyring, or inject a provider") from ex
            self._backend = keyring
        return self._backend

    def get(self, name: str) -> Optional[str]:
        return self.backend.get_password(self.service, name)

    def set(self, name: str, value: str) -> None:
        self.backend.set_password(self.service, name, value)

    def delete(self, name: str) -> None:
        try:
            self.backend.delete_password(self.service, name)
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"KeyringProvider(service={self.service!r})"


class ChainProvider:
    """First provider that has the name wins; writes go to the first."""

    def __init__(self, *providers: SecretProvider):
        if not providers:
            raise ValueError("ChainProvider needs at least one provider")
        self.providers = providers

    def get(self, name: str) -> Optional[str]:
        for p in self.providers:
            v = p.get(name)
            if v is not None:
                return v
        return None

    def set(self, name: str, value: str) -> None:
        self.providers[0].set(name, value)

    def delete(self, name: str) -> None:
        for p in self.providers:
            p.delete(name)


# --------------------------------------------------------------------------
# masking heuristics
# --------------------------------------------------------------------------

#: Text that marks a page as one that reveals a credential.
REVEAL_PAGE = re.compile(
    r"(?i)(copy (?:this|your|the) (?:new )?(?:api )?(?:key|token|secret)"
    r"|(?:won't|will not|cannot|can't) (?:be able to )?(?:see|view|show|display)\w* (?:it|this|the key)? ?again"
    r"|(?:only|just) (?:be )?(?:shown|displayed|visible) once"
    r"|new (?:api |secret |access )?(?:key|token)\b"
    r"|(?:api |secret |access )?(?:key|token) (?:was )?(?:created|generated)"
    r"|personal access token|client secret|secret key|reveal (?:key|secret|token)|show (?:key|secret|token))")

#: A token that looks like a credential on a reveal page: 20+ chars of
#: [A-Za-z0-9_-] with letters and digits (catches unknown vendor formats).
_KEYLIKE = re.compile(r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9_\-])")
_SECURE_ROLES = {"axsecuretextfield", "securetextfield", "passwordfield"}
_SKIP_KEYS = frozenset({"role", "subrole", "tag", "id", "identifier", "children", "frame", "bounds",
                        "i", "index", "enabled", "focused", "secure", "protected", "type"})


def _is_secure(n: Dict[str, Any]) -> bool:
    role = str(n.get("role") or n.get("tag") or "").lower().replace(" ", "")
    sub = str(n.get("subrole") or "").lower()
    if role in _SECURE_ROLES or sub == "axsecuretextfield":
        return True
    if str(n.get("type") or "").lower() == "password":
        return True
    return n.get("secure") is True or n.get("protected") is True


def _keylike_ok(tok: str) -> bool:
    if HANDLE_RE.fullmatch(tok):
        return False
    return secret_scan.shannon_entropy(tok) >= 3.5 and not re.fullmatch(r"[a-z_\-]+\d{0,3}", tok)


@dataclass
class MaskReport:
    reveal_page: bool = False
    masked: int = 0
    secure_fields: int = 0
    captured: Optional[str] = None          # handle stored from this observation
    capture_refused: str = ""               # why capture_as did not capture
    rules: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# the broker
# --------------------------------------------------------------------------

class SecretBroker:
    def __init__(self, provider: Optional[SecretProvider] = None, *,
                 env: Optional[Dict[str, str]] = None):
        self.provider: SecretProvider = provider if provider is not None else MemoryProvider()
        self._env = env
        self._known: Dict[str, str] = {}             # value -> name (process memory only)
        self._bindings: Dict[str, List[str]] = {}    # name -> target globs
        self.uses: List[Dict[str, str]] = []         # audit: which handle went to which sink (names only)

    # -- vault ---------------------------------------------------------------
    def handle(self, name: str) -> str:
        return handle(name)

    def has(self, name: str) -> bool:
        try:
            return self.provider.get(name) is not None
        except Exception:
            return False

    def store(self, name: str, value: str) -> str:
        """Put a value in the vault (e.g. a key just revealed); returns its handle."""
        h = handle(name)
        if not value:
            raise ValueError("empty secret")
        self.provider.set(name, value)
        self._remember(name, value)
        return h

    def bind(self, name: str, *targets: str) -> None:
        """Restrict where a handle may be substituted (fnmatch globs such as
        ``file:*/storefront-web/.env`` or ``http:127.0.0.1:*/login``)."""
        self._bindings.setdefault(name, []).extend(targets)

    def _remember(self, name: str, value: str) -> None:
        self._known[value] = name

    def _get(self, name: str) -> str:
        v = self.provider.get(name)
        if v is None:
            raise MissingSecret(name)
        self._remember(name, v)
        return v

    # -- sink side (enforcer only) -------------------------------------------
    def substitute(self, text: str, *, target: str = "") -> str:
        """Replace every handle in ``text`` with its value. For the SINK only.

        Unknown handle -> MissingSecret (fail fast; a literal handle is never
        sent). A bound handle used for another target -> SecretNotAllowedHere.
        """
        names = handles_in(text)
        for n in names:
            globs = self._bindings.get(n)
            if globs and not any(fnmatchcase(target, g) for g in globs):
                raise SecretNotAllowedHere(f"{handle(n)} is not allowed for {target or 'this sink'}")
        out = HANDLE_RE.sub(lambda m: self._get(m.group(1)), text)
        for n in dict.fromkeys(names):
            self.uses.append({"handle": handle(n), "target": target})
        return out

    def reveal_for_check(self, name: str) -> str:
        """The value, for an in-process verifier comparison (never logged)."""
        return self._get(name)

    def forget_cached(self) -> None:
        """Drop remembered values (after a run). Redaction then relies on the
        key-format rules only."""
        self._known.clear()

    # -- model side -----------------------------------------------------------
    def _variants(self) -> List[Tuple[str, str]]:
        out = []
        for v, n in self._known.items():
            if len(v) < 4:
                continue
            h = handle(n)
            out.append((v, h))
            q = urllib.parse.quote(v, safe="")
            if q != v:
                out.append((q, h))
            if len(v) >= 8:
                out.append((base64.b64encode(v.encode()).decode().rstrip("="), h))
        out.sort(key=lambda t: -len(t[0]))
        return out

    def redact(self, text: Any) -> str:
        """For transcripts, receipts, spans, logs: known values -> their handle,
        then every key format / secret assignment -> [REDACTED:rule].
        Handles survive. Never raises (fails closed)."""
        try:
            if text is None:
                return ""
            if not isinstance(text, str):
                text = str(text)
            for v, h in self._variants():
                if v in text:
                    text = text.replace(v, h)
            parts = HANDLE_RE.split(text)
            # split() with one group alternates text, name, text, name, ...
            out = []
            for i, part in enumerate(parts):
                if i % 2:
                    out.append(handle(part))
                else:
                    out.append(secret_scan.redact(part, env=self._env) if part else part)
            return "".join(out)
        except Exception:
            return secret_scan.REDACT_ERROR

    def redact_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {k: self.redact_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return type(obj)(self.redact_obj(v) for v in obj)
        return obj

    # -- masking observations -------------------------------------------------
    def mask_text(self, text: str, *, capture_as: Optional[str] = None,
                  reveal_page: Optional[bool] = None) -> Tuple[str, MaskReport]:
        """Mask one page/text blob (e.g. HTML or an OCR read)."""
        rep = MaskReport(reveal_page=bool(REVEAL_PAGE.search(text)) if reveal_page is None else reveal_page)
        cands = self._candidates(text, rep.reveal_page)
        self._maybe_capture(cands, capture_as, rep)
        return self._mask_string(text, rep.reveal_page, rep), rep

    def mask_observation(self, obs: Any, *, capture_as: Optional[str] = None,
                         reveal_page: Optional[bool] = None) -> Tuple[Any, MaskReport]:
        """Masked deep copy of an AX-like tree (dicts with children / lists /
        strings) + a report. Secure fields lose their value; on a key-reveal
        page every key-looking token is masked (or captured as a handle)."""
        texts: List[str] = []
        _collect(obs, texts)
        joined = "\n".join(texts)
        rep = MaskReport(reveal_page=bool(REVEAL_PAGE.search(joined)) if reveal_page is None else reveal_page)
        cands = self._candidates(joined, rep.reveal_page)
        self._maybe_capture(cands, capture_as, rep)
        return self._mask_node(obs, rep), rep

    def _candidates(self, text: str, reveal: bool) -> List[str]:
        found: List[str] = []
        for _rule, a, b in secret_scan.key_spans(text, placeholders=False):
            found.append(text[a:b])
        if reveal:
            for m in _KEYLIKE.finditer(text):
                tok = m.group(0)
                if _keylike_ok(tok) and not any(tok in f or f in tok for f in found):
                    found.append(tok)
        known = set(self._known)
        return [c for c in dict.fromkeys(found) if c not in known]

    def _maybe_capture(self, cands: List[str], capture_as: Optional[str], rep: MaskReport) -> None:
        if not capture_as:
            return
        if not rep.reveal_page and not cands:
            rep.capture_refused = "no key on this page"
            return
        if len(cands) != 1:
            rep.capture_refused = ("no key-looking value found" if not cands
                                   else f"{len(cands)} candidate values; refusing to guess")
            return
        rep.captured = self.store(capture_as, cands[0])

    def _mask_string(self, s: str, reveal: bool, rep: MaskReport) -> str:
        for v, h in self._variants():
            if v in s:
                s = s.replace(v, h)
                rep.masked += 1
        spans = [(a, b, r) for r, a, b in secret_scan.key_spans(s, placeholders=False)]
        if reveal:
            for m in _KEYLIKE.finditer(s):
                if _keylike_ok(m.group(0)) and all(m.end() <= x or m.start() >= y for x, y, _ in spans):
                    spans.append((m.start(), m.end(), "keylike"))
        # never mask inside a handle
        hspans = [m.span() for m in HANDLE_RE.finditer(s)]
        spans = [t for t in spans if all(t[1] <= x or t[0] >= y for x, y in hspans)]
        if not spans:
            return s
        spans.sort()
        out, pos = [], 0
        for a, b, r in spans:
            if a < pos:
                continue
            out.append(s[pos:a])
            out.append(f"[MASKED:{r}]")
            rep.masked += 1
            rep.rules.append(r)
            pos = b
        out.append(s[pos:])
        return "".join(out)

    def _mask_node(self, n: Any, rep: MaskReport) -> Any:
        if isinstance(n, str):
            return self._mask_string(n, rep.reveal_page, rep)
        if isinstance(n, (list, tuple)):
            return [self._mask_node(c, rep) for c in n]
        if not isinstance(n, dict):
            return n
        out: Dict[str, Any] = {}
        secure = _is_secure(n)
        for k, v in n.items():
            if k == "children":
                out[k] = [self._mask_node(c, rep) for c in (v or [])]
            elif secure and k == "value":
                if v in (None, ""):
                    out[k] = v
                else:
                    name = self._known.get(v) if isinstance(v, str) else None
                    out[k] = handle(name) if name else SECURE_MASK
                    rep.secure_fields += 1
            elif isinstance(v, str) and k not in _SKIP_KEYS:
                out[k] = self._mask_string(v, rep.reveal_page, rep)
            else:
                out[k] = v
        return out

    # -- hygiene ---------------------------------------------------------------
    def __repr__(self) -> str:
        return f"SecretBroker(provider={self.provider!r}, remembered={len(self._known)})"

    def __reduce__(self):
        raise TypeError("SecretBroker is not serialisable")


def _collect(n: Any, out: List[str]) -> None:
    if isinstance(n, str):
        out.append(n)
    elif isinstance(n, dict):
        for k, v in n.items():
            if k == "children":
                for c in v or []:
                    _collect(c, out)
            elif isinstance(v, str) and k not in _SKIP_KEYS and not (k == "value" and _is_secure(n)):
                out.append(v)
    elif isinstance(n, (list, tuple)):
        for c in n:
            _collect(c, out)


# --------------------------------------------------------------------------
# clipboard
# --------------------------------------------------------------------------

def _pbpaste() -> str:
    return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout


def _pbcopy(text: str) -> None:
    subprocess.run(["pbcopy"], input=text, text=True, timeout=5, check=False)


class ClipboardGuard:
    """Copy a secret for a paste sink, then clear the clipboard.

    ``read``/``write`` default to pbpaste/pbcopy on macOS; tests inject fakes.
    Use as a context manager: the clipboard is cleared on exit (even on error)
    if it still holds a value the broker knows.
    """

    def __init__(self, broker: SecretBroker, read: Optional[Callable[[], str]] = None,
                 write: Optional[Callable[[str], None]] = None):
        self.broker = broker
        if (read is None or write is None) and sys.platform != "darwin":
            raise RuntimeError("no clipboard backend on this platform; inject read/write")
        self.read = read or _pbpaste
        self.write = write or _pbcopy
        self.copied = False

    def copy_secret(self, handle_text: str, *, target: str = "clipboard") -> None:
        """A sink: resolves the handle(s) and puts the value on the clipboard."""
        self.write(self.broker.substitute(handle_text, target=target))
        self.copied = True

    def clear(self) -> None:
        self.write("")
        self.copied = False

    def clear_if_secret(self) -> bool:
        try:
            cur = self.read() or ""
        except Exception:
            self.clear()                 # cannot tell -> clear (fail closed)
            return True
        if any(v in cur for v in self.broker._known) or secret_scan.key_spans(cur, placeholders=False):
            self.clear()
            return True
        return False

    def __enter__(self) -> "ClipboardGuard":
        return self

    def __exit__(self, *exc) -> None:
        if self.copied:
            self.clear()
        else:
            self.clear_if_secret()


# --------------------------------------------------------------------------
# credential-creating actions
# --------------------------------------------------------------------------

CREDENTIAL_CREATE = re.compile(
    r"(?i)\b(?:create|generate|new|add|issue|mint|regenerate|roll|rotate|reveal)\b[\w\s\-]{0,24}?"
    r"\b(?:api[\s_\-]?)?(?:keys?|tokens?|secrets?|credentials?|access[\s_\-]keys?|app[\s_\-]passwords?)\b")


def is_credential_create(label: str) -> bool:
    """True for controls like "Create key", "Generate new token", "+ New API key",
    "Roll key", "Reveal secret": an approval-required CREDENTIAL write."""
    return bool(label) and bool(CREDENTIAL_CREATE.search(label))


AWOS_LABEL_PREFIX = "awos-"


def awos_key_label(purpose: str) -> str:
    """Deterministic label for a key AWOS creates (used to find it again)."""
    slug = re.sub(r"[^a-z0-9]+", "-", purpose.lower()).strip("-")[:48]
    return AWOS_LABEL_PREFIX + (slug or "key")


@dataclass
class KeyPlan:
    action: str                       # reuse | create | needs_user
    reason: str
    existing: Optional[Dict[str, Any]] = None
    handle: Optional[str] = None


def _active(rows: Iterable[Dict[str, Any]], label: str) -> List[Dict[str, Any]]:
    return [r for r in rows if str(r.get("name", "")).strip() == label
            and str(r.get("status", "active")).lower() == "active"]


def plan_key_creation(existing: Iterable[Dict[str, Any]], label: str, broker: SecretBroker,
                      secret_name: str) -> KeyPlan:
    """Idempotency before "Create key": look for an AWOS-labelled key first.

    rows: the dashboard's key list as dicts (name, status, last4, ...).
      active AWOS key + we hold its value (last4 matches when given) -> reuse
      active AWOS key we cannot read (dashboards show a key once) -> needs_user
      none -> create (approval-required)
    """
    rows = _active(existing, label)
    if not rows:
        return KeyPlan("create", f"no active key labelled {label!r}")
    if broker.has(secret_name):
        try:
            val = broker.reveal_for_check(secret_name)
        except MissingSecret:
            val = ""
        for r in rows:
            l4 = str(r.get("last4") or "")
            if not l4 or val.endswith(l4):
                return KeyPlan("reuse", f"existing key {label!r} already in the vault", r, handle(secret_name))
    return KeyPlan("needs_user", f"an active key labelled {label!r} exists but its value is not in the "
                   "vault; ask the owner (revoke and replace, or paste it) instead of creating a duplicate",
                   rows[0])


def orphaned_awos_keys(existing: Iterable[Dict[str, Any]], keep_last4: str = "") -> List[Dict[str, Any]]:
    """Active AWOS-labelled keys other than the one in use: cleanup candidates.
    Revoking is a destructive, owner-approved action; this only lists them."""
    return [r for r in existing if str(r.get("name", "")).startswith(AWOS_LABEL_PREFIX)
            and str(r.get("status", "active")).lower() == "active"
            and (not keep_last4 or str(r.get("last4", "")) != keep_last4)]

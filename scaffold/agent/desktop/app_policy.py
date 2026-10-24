"""
app_policy.py — which apps the desktop agent may look at and act in.

The sink policy (policy.py) judges an action by its class and provenance;
this module judges it by the *app* it lands in. Each app (keyed by bundle id,
which the backend reads from the OS process table — never from a window
title, which any app or web page can set) gets a tier:

  forbidden  not observed, not acted on (no snapshot is even taken)
  read       observed; only read-kind actions (scroll, hover, ...)
  click      observed; clicks, but no typing, key presses, paste or drag
  full       everything, still subject to the sink policy and its grants

and two flags:

  elevated   observed with every field value masked and a warning note;
             every action asks the owner, even with a standing grant
  shell      a terminal: typed text is a shell command the shell classifier
             never saw, so input there is never covered by an app grant (it
             is opaque at best, and denied when the command itself is)

Defaults (``DEFAULT_RULES``):
  forbidden, never overridable (``HARD_FORBIDDEN``): AWOS's own host and
      approval UI (the agent must not click its own "Approve"), and the OS
      security/auth UI (SecurityAgent, the coreauthd / LocalAuthentication
      UI agent, the Privacy & Security settings pane)
  forbidden, owner may override: terminals (Terminal, iTerm2, Warp,
      Alacritty, kitty, Ghostty, WezTerm; flag ``shell`` stays on), and model
      client apps (Claude, ChatGPT) that could be used to steer the agent
  elevated: password managers (1Password, Bitwarden, KeePassXC, Dashlane,
      Keychain Access, Apple Passwords)
  click: IDEs (VS Code, Cursor, Windsurf, Zed, Xcode, JetBrains, Sublime)
  read: System Settings (only the pane id tells Privacy & Security apart, and
      a backend may not report it)
  anything else: full

Owner overrides: a JSON file (default ``.awos/cu_app_policy.json``, or
``AWOS_CU_APP_POLICY``):

    {"allow": {"com.microsoft.VSCode": "full", "com.apple.Terminal": "click"},
     "deny":  ["com.tinyspeck.slackmacgap", "com.example.*"],
     "unattended": false}

``deny`` beats ``allow`` (an id matching both is forbidden). Keys may be
fnmatch globs. ``allow`` can never lift ``HARD_FORBIDDEN``. A file that
exists but cannot be read or parsed fails closed: every app the defaults do
not name drops to ``read``. ``unattended: true`` declares the session runs
where no human can take over (a VM), so a backend without a takeover hook
may still act (ui_session.py).
"""
from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union


class AppTier(str, Enum):
    FORBIDDEN = "forbidden"
    READ = "read"
    CLICK = "click"
    FULL = "full"


_TIER_RANK = {AppTier.FORBIDDEN: 0, AppTier.READ: 1, AppTier.CLICK: 2, AppTier.FULL: 3}

#: AWOS's own host app and the window where the owner approves actions
AWOS_HOST_BUNDLE_ID = "ai.awos.host"
AWOS_APPROVAL_BUNDLE_ID = "ai.awos.approval"
#: a pane is addressed as "<bundle id>#<pane id>"
PRIVACY_PANE = "com.apple.systempreferences#com.apple.settings.PrivacySecurity.extension"

POLICY_ENV = "AWOS_CU_APP_POLICY"
DEFAULT_POLICY_PATH = Path(".awos") / "cu_app_policy.json"

TERMINALS = (
    "com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable", "dev.warp.Warp*",
    "org.alacritty", "io.alacritty", "net.kovidgoyal.kitty", "com.mitchellh.ghostty",
    "com.github.wez.wezterm",
)
SECURITY_UI = (
    "com.apple.SecurityAgent", "com.apple.coreauthd", "com.apple.LocalAuthentication.UIAgent",
    "com.apple.AuthenticationServicesAgent", PRIVACY_PANE,
    "com.apple.systempreferences#com.apple.preference.security",
)
MODEL_CLIENTS = ("com.anthropic.claudefordesktop", "com.openai.chat", "com.openai.codex")
PASSWORD_MANAGERS = (
    "com.1password.1password", "com.agilebits.onepassword*", "com.bitwarden.desktop",
    "org.keepassxc.keepassxc", "com.dashlane.dashlanephonefinal", "com.dashlane.*",
    "com.apple.keychainaccess", "com.apple.Passwords",
)
IDES = (
    "com.microsoft.VSCode", "com.microsoft.VSCodeInsiders", "com.todesktop.230313mzl4w4u92",
    "com.exafunction.windsurf", "dev.zed.Zed", "com.apple.dt.Xcode", "com.jetbrains.*",
    "com.sublimetext.*", "com.google.android.studio",
)
HARD_FORBIDDEN = (AWOS_HOST_BUNDLE_ID, AWOS_APPROVAL_BUNDLE_ID) + SECURITY_UI


@dataclass(frozen=True)
class AppIdentity:
    """Who owns the window being observed or acted on, as the OS reports it."""
    bundle_id: str
    pid: Optional[int] = None
    window_id: Optional[Union[int, str]] = None
    name: str = ""                  # localized app name (display only)
    pane: str = ""                  # settings pane / sub-identity, when known

    @classmethod
    def from_any(cls, v: Any) -> Optional["AppIdentity"]:
        if isinstance(v, AppIdentity):
            return v if v.bundle_id else None
        if not isinstance(v, dict):
            return None
        bid = str(v.get("bundle_id") or v.get("bundleId") or "").strip()
        if not bid:
            return None
        pid = v.get("pid")
        try:
            pid = int(pid) if pid is not None and not isinstance(pid, bool) else None
        except (TypeError, ValueError):
            pid = None
        return cls(bid, pid, v.get("window_id", v.get("windowId")), str(v.get("name") or "")[:80],
                   str(v.get("pane") or ""))

    @property
    def keys(self) -> List[str]:
        """Lookup keys, most specific first."""
        return ([f"{self.bundle_id}#{self.pane}"] if self.pane else []) + [self.bundle_id]

    def same_window(self, other: Optional["AppIdentity"]) -> bool:
        """Same app process and (when both know it) the same window."""
        if other is None:
            return False
        if (self.bundle_id, self.pid) != (other.bundle_id, other.pid):
            return False
        if self.window_id is not None or other.window_id is not None:
            return self.window_id == other.window_id
        return True


@dataclass(frozen=True)
class AppRule:
    tier: AppTier
    elevated: bool = False
    shell: bool = False
    reason: str = ""
    source: str = "default"         # default | owner | hard | unknown | broken-file

    def allows_kind(self, kind: str) -> bool:
        from .policy import UI_CLICK_KINDS, UI_READ_KINDS
        kind = (kind or "").strip().lower()
        if self.tier is AppTier.FORBIDDEN:
            return False
        if kind in UI_READ_KINDS:
            return True
        if self.tier is AppTier.READ:
            return False
        if self.tier is AppTier.CLICK:
            return kind in UI_CLICK_KINDS
        return True


def _defaults() -> List[Tuple[Tuple[str, ...], AppRule]]:
    return [
        (HARD_FORBIDDEN, AppRule(AppTier.FORBIDDEN, reason="AWOS approval UI / OS security UI",
                                 source="hard")),
        (TERMINALS, AppRule(AppTier.FORBIDDEN, shell=True,
                            reason="terminal: typing there bypasses the shell classifier")),
        (MODEL_CLIENTS, AppRule(AppTier.FORBIDDEN, reason="model client app")),
        (PASSWORD_MANAGERS, AppRule(AppTier.FULL, elevated=True, reason="password manager")),
        (IDES, AppRule(AppTier.CLICK, reason="IDE: clicks only by default")),
        (("com.apple.systempreferences",), AppRule(AppTier.READ, reason="System Settings")),
    ]


DEFAULT_RULES = _defaults()


def _match(key: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(key, p) for p in patterns)


class AppPolicy:
    def __init__(self, allow: Optional[Dict[str, str]] = None, deny: Iterable[str] = (),
                 unattended: bool = False, error: str = "", path: Optional[Path] = None):
        self.allow: Dict[str, AppTier] = {}
        for k, v in (allow or {}).items():
            try:
                self.allow[str(k)] = AppTier(str(v).lower())
            except ValueError:
                error = error or f"unknown tier {v!r} for {k!r}"
        self.deny = [str(d) for d in deny]
        self.unattended = bool(unattended)
        self.error = error
        self.path = path

    # -- loading ----------------------------------------------------------------
    @classmethod
    def load(cls, path: Union[str, Path, None] = None) -> "AppPolicy":
        p = Path(path) if path else Path(os.getenv(POLICY_ENV, "").strip() or DEFAULT_POLICY_PATH)
        if not p.exists():
            return cls(path=p)
        try:
            data = json.loads(p.read_text())
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
            allow = data.get("allow") or {}
            deny = data.get("deny") or []
            if not isinstance(allow, dict) or not isinstance(deny, list):
                raise ValueError("allow must be an object and deny a list")
            pol = cls(allow, deny, data.get("unattended") is True, path=p)
            if pol.error:
                raise ValueError(pol.error)
            return pol
        except (OSError, ValueError) as e:
            return cls(error=f"{p}: {e}", path=p)

    # -- lookup -----------------------------------------------------------------
    def rule(self, ident: Optional[AppIdentity]) -> AppRule:
        if ident is None or not ident.bundle_id:
            return AppRule(AppTier.FORBIDDEN, reason="app identity unknown (no bundle id)",
                           source="unknown")
        keys = ident.keys
        base: Optional[AppRule] = None
        for patterns, r in DEFAULT_RULES:
            if any(_match(k, patterns) for k in keys):
                base = r
                break
        if base is not None and base.source == "hard":
            return base
        if any(_match(k, self.deny) for k in keys):
            return AppRule(AppTier.FORBIDDEN, shell=bool(base and base.shell),
                           elevated=bool(base and base.elevated),
                           reason="denied by the owner's app policy", source="owner")
        for k in keys:
            hit = next((t for pat, t in self.allow.items() if fnmatch.fnmatchcase(k, pat)), None)
            if hit is not None:
                b = base or AppRule(AppTier.FULL)
                return replace(b, tier=hit, reason=f"owner allows {hit.value}", source="owner")
        if base is not None:
            return base
        if self.error:
            return AppRule(AppTier.READ, reason=f"owner app policy unreadable ({self.error})",
                           source="broken-file")
        return AppRule(AppTier.FULL, reason="no app rule (sink policy still applies)")

"""
permissions.py — lazy PyObjC loading and TCC permission checks, with clear errors.

Nothing here runs at import time. check_permissions() only PREFLIGHTS
(AXIsProcessTrusted, CGPreflightListenEventAccess): it never shows a macOS
prompt. Only request=True (the CLI's --request-permissions flag, typed by the
owner) calls the prompting variants.

Permissions the recorder needs (System Settings > Privacy & Security), granted
to the app that runs Python (Terminal, iTerm, VS Code ...):
  * Accessibility     AX reads, AXObserver notifications
  * Input Monitoring  the listen-only CGEventTap (mouse clicks, Enter/Tab keys)
Not needed: Screen Recording (no pixels are captured). Files under the watched
folders must be readable by that app (Files & Folders), which is automatic for
a folder you created yourself outside ~/Documents / ~/Desktop / ~/Downloads.
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PYOBJC_PACKAGES = ("pyobjc-framework-ApplicationServices", "pyobjc-framework-Quartz",
                   "pyobjc-framework-Cocoa", "pyobjc-framework-FSEvents")
FRAMEWORK_MODULES = {"ApplicationServices": "ApplicationServices", "Quartz": "Quartz",
                     "AppKit": "AppKit", "FSEvents": "FSEvents"}

HOST_HINT = ("the app that runs this command (Terminal, iTerm, VS Code ...); then quit and reopen "
             "that app so macOS applies the change")
REMEDIATION = {
    "accessibility": "System Settings > Privacy & Security > Accessibility: turn on " + HOST_HINT,
    "input_monitoring": "System Settings > Privacy & Security > Input Monitoring: turn on " + HOST_HINT,
}


class CaptureUnavailable(RuntimeError):
    """The native recorder cannot run here (not macOS, or PyObjC missing)."""


class PyObjCMissing(CaptureUnavailable, ImportError):
    pass


class PermissionMissing(CaptureUnavailable, PermissionError):
    def __init__(self, missing: List[str]):
        self.missing = list(missing)
        lines = ["macOS denied the recorder the permissions it needs: " + ", ".join(self.missing)]
        lines += [f"  - {REMEDIATION[m]}" for m in self.missing]
        lines.append("Nothing was recorded. Re-run after granting, or run "
                     "`python -m scaffold.agent.desktop.capture check` to see the status.")
        super().__init__("\n".join(lines))


@dataclass
class PermissionStatus:
    accessibility: bool
    input_monitoring: bool
    missing: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing

    def describe(self) -> str:
        out = [f"Accessibility:    {'granted' if self.accessibility else 'MISSING'}",
               f"Input Monitoring: {'granted' if self.input_monitoring else 'MISSING'}"]
        out += [f"  -> {REMEDIATION[m]}" for m in self.missing]
        return "\n".join(out)


def load_frameworks(names=("ApplicationServices", "Quartz", "AppKit", "FSEvents"),
                    platform: Optional[str] = None) -> Dict[str, Any]:
    """Import the PyObjC frameworks on demand; raise a CaptureUnavailable that says what to do."""
    plat = platform or sys.platform
    if plat != "darwin":
        raise CaptureUnavailable(f"the native recorder needs macOS (this is {plat}); "
                                 "use --from-events to replay a recorded session")
    mods: Dict[str, Any] = {}
    missing = []
    for n in names:
        try:
            mods[n] = importlib.import_module(FRAMEWORK_MODULES[n])
        except ImportError:
            missing.append(n)
    if missing:
        raise PyObjCMissing("the native recorder needs PyObjC (" + ", ".join(missing) + " not importable). "
                            "Install it into this Python: pip install " + " ".join(PYOBJC_PACKAGES))
    return mods


def check_permissions(frameworks: Optional[Dict[str, Any]] = None, *, request: bool = False) -> PermissionStatus:
    """Preflight Accessibility and Input Monitoring. No prompt unless request=True."""
    fw = frameworks if frameworks is not None else load_frameworks(("ApplicationServices", "Quartz"))
    ax, quartz = fw["ApplicationServices"], fw["Quartz"]
    if request:
        opt = getattr(ax, "kAXTrustedCheckOptionPrompt", "AXTrustedCheckOptionPrompt")
        trusted = bool(ax.AXIsProcessTrustedWithOptions({opt: True}))
    else:
        trusted = bool(ax.AXIsProcessTrusted())
    preflight = getattr(quartz, "CGPreflightListenEventAccess", None)
    listen = bool(preflight()) if callable(preflight) else trusted
    if request and not listen:
        req = getattr(quartz, "CGRequestListenEventAccess", None)
        if callable(req):
            listen = bool(req())
    missing = [n for n, ok in (("accessibility", trusted), ("input_monitoring", listen)) if not ok]
    return PermissionStatus(trusted, listen, missing)


def require_permissions(frameworks: Optional[Dict[str, Any]] = None, *, request: bool = False) -> PermissionStatus:
    st = check_permissions(frameworks, request=request)
    if not st.ok:
        raise PermissionMissing(st.missing)
    return st

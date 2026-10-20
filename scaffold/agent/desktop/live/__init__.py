"""
scaffold.agent.desktop.live — real macOS computer use over the AX tree.

    observe.py   bounded AX snapshots (grounding / ax_compress compatible) + MacRuntime (PyObjC)
    actions.py   verified actions: press, set value / type, select, menu pick, scroll,
                 activate, open URL in a new Safari window -> VERIFIED / UNVERIFIED / REFUSED
    driver.py    MacBrowserBackend (micro_agent's browser interface over live Safari) and
                 MacEnv (teach-once skill_runner's env interface over live Safari)
    probe.py     permission status, frontmost app, compressed tree; the dd / teach_t3 demos;
                 vm-run (host side: run a live command inside the Tart guest over SSH)
    __main__.py  python -m scaffold.agent.desktop.live {probe,demo,vm-run}

Where it runs: ONLY inside a sandboxed macOS guest VM (Tart / Apple Virtualization),
through "AWOS Helper.app" built in the guest. The host Mac is never controlled: every
acting path checks ``in_guest_vm()`` and refuses on bare metal. The host drives the guest
with ``vm-run`` (rsync + ssh) and never needs Accessibility. See
docs/specs/live_macos_control.md.
"""
from __future__ import annotations

import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def in_guest_vm() -> bool:
    """True inside a virtual machine (macOS reports kern.hv_vmm_present=1 in a guest)."""
    try:
        out = subprocess.run(["sysctl", "-n", "kern.hv_vmm_present"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return out == "1"


__all__ = ["in_guest_vm"]

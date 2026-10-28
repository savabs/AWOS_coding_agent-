"""
calibrate.py — the same dev_dashboard pages read two ways, and the differences:

    HTML path      web_tools.LoopbackBrowser  (server HTML -> web_tools.parse_page)
    AX path        ChromiumBackend            (Chromium AX -> axmap -> driver.read_web -> Page)

Two dashboard servers are seeded identically (dd_seed variant "a", same seed), one per
browser, and both are driven through the same flow (wrong password -> sign in -> 2FA ->
projects -> project -> API keys -> create key form -> write-scope confirm dialog -> key
created (hidden value) -> revoke confirm -> revoked status -> settings -> billing -> 404).
At each page the Pages are compared on: title, HTTP status, controls (role, name, context,
dialog, options, form/no form), alerts, statuses, regions, dialog text, headings and text lines.

    python -m scaffold.rl.harness calibrate [--json out.json]
"""
from __future__ import annotations

import difflib
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from scaffold.agent.desktop.web_tools import LoopbackBrowser, Page

REPO = Path(__file__).resolve().parents[3]


def control_sig(page: Page) -> List[str]:
    out = []
    for c in page.controls:
        bits = [c.role, repr(c.name)]
        if c.context:
            bits.append(f"in={c.context!r}")
        if c.in_dialog:
            bits.append("dialog")
        if c.options:
            bits.append("options=" + "|".join(c.options))
        bits.append("form" if c.form is not None else "noform")
        out.append(" ".join(bits))
    return out


def compare(html: Page, ax: Page) -> Dict[str, Any]:
    """Field-by-field differences (HTML path vs AX path) for one page."""
    d: Dict[str, Any] = {}
    for k in ("title", "status", "alerts", "statuses", "regions", "dialog", "headings"):
        a, b = getattr(html, k), getattr(ax, k)
        if a != b:
            d[k] = {"html": a, "ax": b}
    ca, cb = control_sig(html), control_sig(ax)
    if ca != cb:
        d["controls"] = [ln for ln in difflib.unified_diff(ca, cb, "html", "ax", lineterm="", n=0)
                         if not ln.startswith(("---", "+++", "@@"))]
    ra = [(c.role, c.name) for c in html.controls]
    rb = [(c.role, c.name) for c in ax.controls]
    d["same_role_name"] = ra == rb
    la = [ln for ln in html.text.splitlines() if ln.strip()]
    lb = [ln for ln in ax.text.splitlines() if ln.strip()]
    if la != lb:
        d["text_only_html"] = [x for x in la if x not in lb]
        d["text_only_ax"] = [x for x in lb if x not in la]
    return d


class _Driver:
    """The same semantic flow on either browser."""

    def __init__(self, br: Any, auth_file: Path):
        self.br, self.auth = br, auth_file
        self.page: Optional[Page] = None

    def n(self, role: str, name: str, dialog: Optional[bool] = None, context: Optional[str] = None) -> int:
        roles = {"textbox": ("textbox", "password")}.get(role, (role,))
        for c in self.page.controls:
            if c.role in roles and c.name.lower() == name.lower() and (dialog is None or c.in_dialog == dialog) \
                    and (context is None or c.context.lower() == context.lower()):
                return c.n
        raise LookupError(f"{role} {name!r} not on {self.page.url}: {[(c.role, c.name) for c in self.page.controls]}")

    def open(self, path: str) -> Page:
        self.page = self.br.open(path)
        return self.page

    def click(self, role: str, name: str, dialog: Optional[bool] = None, context: Optional[str] = None) -> Page:
        self.page = self.br.click(self.page, self.n(role, name, dialog, context))
        return self.page

    def fill(self, name: str, value: str) -> Page:
        self.page = self.br.fill(self.page, self.n("textbox", name), value)
        return self.page

    def select(self, name: str, option: str) -> Page:
        n = next(c.n for c in self.page.controls if c.role in ("radiogroup", "combobox") and c.name.lower() == name.lower())
        self.page = self.br.select(self.page, n, option)
        return self.page

    def code(self) -> str:
        return re.search(r"Code:\s*(\d{6})", self.auth.read_text()).group(1)


def flow(d: _Driver, user: str, password: str, record: Callable[[str, Page], None]) -> None:
    record("login", d.open("/login"))
    d.fill("Email", user)
    d.fill("Password", "wrong-password")
    record("login_error_alert", d.click("button", "Sign in"))
    d.fill("Email", user)
    d.fill("Password", password)
    record("verify_2fa", d.click("button", "Sign in"))
    d.fill("Verification code", d.code())
    record("projects", d.click("button", "Verify"))
    record("project", d.click("link", "storefront-web"))
    record("keys", d.click("link", "API keys"))
    record("new_key_form", d.click("button", "Create key"))
    d.fill("Key name", "calib-key")
    d.select("Scope", "Read & write")
    record("confirm_dialog", d.click("button", "Continue"))
    record("key_created_region", d.click("button", "Create key", dialog=True))
    record("keys_after_create", d.click("link", "Done"))
    record("revoke_confirm", d.click("button", "Revoke", context="Revoke key ci-readonly"))
    record("revoked_status", d.click("button", "Revoke key", dialog=True))
    record("settings", d.open("/projects/storefront-web/settings"))
    record("billing", d.open("/billing"))
    record("not_found", d.open("/projects/nope"))


def _fake_safari_backend(url: str):
    """MacBrowserBackend over the repo's FakeSafari (tests/test_live_driver.py): the hand-written
    model of how WebKit exposes these pages, driven through the real product backend."""
    sys.path.insert(0, str(REPO))
    from tests import test_live_driver as T  # noqa: PLC0415
    from scaffold.agent.desktop.live import actions as A  # noqa: PLC0415
    from scaffold.agent.desktop.live.driver import MacBrowserBackend  # noqa: PLC0415
    rt = T.FakeRuntime()
    T.FakeSafari(rt)
    return MacBrowserBackend(url, actor=T.actor(rt, allow_apps={A.SAFARI}, approve=lambda *_: True))


def calibrate_dev_dashboard(seed: int = 7, chromium: Any = None, fake_safari: bool = True) -> Dict[str, Any]:
    """Run the flow on the HTML path, Chromium, and (optionally) the repo's FakeSafari model;
    compare each against Chromium page by page."""
    sys.path.insert(0, str(REPO / "testbeds" / "dev_dashboard"))
    import dd_seed  # noqa: PLC0415
    import dd_server  # noqa: PLC0415
    from scaffold.agent.desktop.web_tools import outline  # noqa: PLC0415

    from .chromium import Chromium, ChromiumBackend
    kinds = ["html", "ax"] + (["fake_safari"] if fake_safari else [])
    roots = {k: Path(tempfile.mkdtemp(prefix=f"rlcal-{k}-")) for k in kinds}
    for r in roots.values():
        dd_seed.seed(r, "a", seed)
    states = {k: json.loads((r / "harness" / "dashboard_state.json").read_text()) for k, r in roots.items()}
    servers = {k: dd_server.DashboardServers(r / "harness" / "dashboard_state.json").start() for k, r in roots.items()}
    own = chromium is None
    ch = chromium or Chromium()
    pages: Dict[str, Dict[str, Page]] = {k: {} for k in kinds}
    out: Dict[str, Any] = {"pages": {}, "timings": [], "errors": {}}
    try:
        for k in kinds:
            url = servers[k].dashboard_url
            if k == "html":
                br = LoopbackBrowser(url)
            elif k == "ax":
                br = ChromiumBackend(url, chromium=ch)
            else:
                try:
                    br = _fake_safari_backend(url)
                except Exception as e:                  # tests package not importable
                    out["errors"][k] = f"{type(e).__name__}: {e}"
                    continue
            t0 = time.monotonic()
            try:
                flow(_Driver(br, Path(states[k]["paths"]["authenticator"])), states[k]["user"],
                     states[k]["password"], lambda name, p, k=k: pages[k].__setitem__(name, p))
            except Exception as e:
                out["errors"][k] = f"{type(e).__name__}: {e}"
            if k == "ax":
                out["ax_flow_seconds"] = round(time.monotonic() - t0, 3)
                out["timings"] = br.timings
                br.close()
    finally:
        for s in servers.values():
            s.stop()
        if own:
            ch.close()
    for name, ap in pages["ax"].items():
        entry: Dict[str, Any] = {"outline_ax": outline(ap)}
        for k in kinds:
            if k == "ax" or name not in pages[k]:
                continue
            d = compare(pages[k][name], ap)
            d["outline_same"] = outline(pages[k][name]) == outline(ap)
            entry[k] = d
            entry["outline_" + k] = outline(pages[k][name])
        out["pages"][name] = entry
    for k in kinds:
        if k != "ax":
            vals = [e[k]["same_role_name"] for e in out["pages"].values() if k in e]
            out[f"same_role_name_{k}"] = bool(vals) and all(vals) and len(vals) == len(pages["ax"])
            outl = [e[k]["outline_same"] for e in out["pages"].values() if k in e]
            out[f"outline_identical_{k}"] = f"{sum(outl)}/{len(outl)}"
    if out["timings"]:
        tot = [t["act_ms"] + t["settle_ms"] + t["observe_ms"] for t in out["timings"]]
        out["ms_per_op_mean"] = round(sum(tot) / len(tot), 1)
        out["observe_ms_mean"] = round(sum(t["observe_ms"] for t in out["timings"]) / len(tot), 1)
        out["n_ops"] = len(tot)
    return out


def report(res: Dict[str, Any]) -> str:
    lines = ["# dev_dashboard calibration: Chromium AX path vs HTML path / FakeSafari model", ""]
    for k in ("html", "fake_safari"):
        if f"same_role_name_{k}" in res:
            lines.append(f"- vs {k}: same control (role, name) list on every page: "
                         f"**{res[f'same_role_name_{k}']}**; identical outline: {res[f'outline_identical_{k}']}")
    if res.get("errors"):
        lines.append(f"- errors: {res['errors']}")
    lines += [f"- Chromium ops: {res.get('n_ops')} (mean {res.get('ms_per_op_mean')} ms per op incl. settle; "
              f"observe {res.get('observe_ms_mean')} ms)", ""]
    for name, e in res["pages"].items():
        lines.append(f"## {name}")
        for k in ("html", "fake_safari"):
            d = e.get(k)
            if d is None:
                continue
            diffs = {x: y for x, y in d.items() if x not in ("same_role_name", "outline_same")}
            lines.append(f"- vs {k}: role+name {'same' if d['same_role_name'] else 'DIFFERENT'}"
                         f"{'; outline identical' if d['outline_same'] else ''}")
            for x, y in diffs.items():
                lines.append(f"    - {x}: {y}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="calibrate")
    ap.add_argument("--json", help="write the full result (incl. outlines) here")
    ap.add_argument("--no-fake-safari", action="store_true")
    a = ap.parse_args(argv)
    res = calibrate_dev_dashboard(fake_safari=not a.no_fake_safari)
    print(report(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=str))
    return 0

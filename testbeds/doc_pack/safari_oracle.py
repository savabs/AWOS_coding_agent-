"""
safari_oracle.py — run the doc_pack oracle in REAL Safari through AWOS's live AX driver
(scaffold.agent.desktop.live: MacBrowserBackend + Actor), then the same verify.py.

Where it runs: ONLY inside a macOS guest VM (Tart guest, or a GitHub-hosted macOS runner,
which is itself a VM: kern.hv_vmm_present=1). The live driver's preflight refuses on bare
metal, so this never drives the host Mac.

    python testbeds/doc_pack/run.py safari --variant a --out out/

Downloads: a click on "Download PDF" makes Safari save into ~/Downloads; the adapter waits
for the new finished file, moves it into the sandbox outbox and hands the oracle the same
``page.download = (name, bytes)`` the HTTP browser gives. If Safari blocks or asks about
the download (a dialog the live driver does not know), the run reports it — nothing is
clicked on an unknown dialog. Captcha / OTP still go to the scripted human desk over HTTP.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(HERE), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

DOWNLOADISH = re.compile(r"^(download|get invoice|get tax invoice|save bill|export csv|generate statement)", re.IGNORECASE)
PARTIAL = (".download", ".crdownload", ".part")


class SafariDocBrowser:
    """dp_oracle's browser interface over one new Safari window."""

    def __init__(self, url: str, actor, downloads: Path, wait_s: float = 25.0):
        from scaffold.agent.desktop.live.driver import MacBrowserBackend
        self.b = MacBrowserBackend(url, actor=actor)
        self.downloads, self.wait_s = downloads, wait_s
        self.got: list[str] = []
        self.missed: list[str] = []

    @property
    def log(self):
        return self.b.log

    def open(self, path="/"):
        return self.b.open(path)

    def fill(self, page, n, value):
        return self.b.fill(page, n, value)

    def select(self, page, n, option):
        return self.b.select(page, n, option)

    def _files(self):
        return {p.name: p.stat().st_mtime for p in self.downloads.glob("*") if p.is_file() or p.suffix == ".download"}

    def click(self, page, n):
        c = page.control(n)
        expect_dl = bool(c and DOWNLOADISH.search(c.name or ""))
        before = self._files()
        t0 = time.time()
        p = self.b.click(page, n)
        if not expect_dl or "/login" in (p.url or ""):          # not a download, or the session expired
            return p
        t_end = t0 + self.wait_s
        last_clear = 0.0
        while time.time() < t_end:
            if time.time() - last_clear > 1.5:              # a download prompt may be over our window
                last_clear = time.time()
                try:
                    r = self.b.actor.clear_interruptions(self.b._snap(), "download")
                    if r is not None:
                        self.missed.append(f"{c.name}: {r.reason}"[:300])
                        return p
                except Exception as ex:
                    self.missed.append(f"clear_interruptions: {type(ex).__name__}: {ex}"[:200])
            new = [k for k in self._files() if k not in before and not k.endswith(PARTIAL)]
            if new:
                f = self.downloads / sorted(new)[0]
                time.sleep(0.5)                               # let Safari finish renaming
                data = f.read_bytes()
                f.unlink()
                self.got.append(f.name)
                from scaffold.agent.desktop.web_tools import parse_page
                stub = parse_page(p.url, 200, f"<title>Downloaded</title><p>Downloaded {f.name}</p>")
                stub.download = (f.name, data)  # type: ignore[attr-defined]
                return stub                                   # the Safari window stays on its page
            time.sleep(0.3)
        self.missed.append(c.name if c else str(n))
        return p

    def download(self, page, n):
        p = self.click(page, n)
        if not getattr(p, "download", None):
            from scaffold.agent.desktop.web_tools import WebError
            raise WebError(f"Safari did not deliver a download for control {n}")
        return p.download

    def close(self):
        try:
            return self.b.close()
        except Exception:
            return False


def _screenshot(out: Path | None, name: str) -> None:
    if out is None:
        return
    try:
        subprocess.run(["screencapture", "-x", str(out / f"dp_{name}.png")], timeout=20, capture_output=True)
    except Exception:
        pass


def main(a, *, setup, verify) -> int:
    """``setup`` / ``verify``: run.setup and verify.verify, passed in by run.py (no top-level
    'run' / 'verify' imports: other testbeds use those module names too)."""
    import dp_oracle

    from scaffold.agent.desktop.live import probe as P
    from scaffold.agent.desktop.live.actions import SAFARI, Actor
    from scaffold.agent.desktop.live.observe import MacRuntime

    out = Path(a.out) if a.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    pf = P.preflight()
    if not pf["ok"]:
        print(pf["error"] or "refused")
        return 2
    P.vm_hygiene(out=print)
    root = Path(a.root or tempfile.mkdtemp(prefix="dp_safari_"))
    months = a.months.split(",") if a.months else None
    truth, srv = setup(root, a.variant, a.seed, months)
    dl_dir = Path.home() / "Downloads"
    dl_dir.mkdir(exist_ok=True)
    lines: list[str] = []
    rt = MacRuntime()
    browsers: list[SafariDocBrowser] = []

    from scaffold.agent.desktop.live.interrupts import DEFAULT_RULES, InterruptHandler, Rule
    # Testbed-harness opt-in (never in the product): Safari asks "Do you want to allow downloads
    # on '127.0.0.1'?" [Cancel] [Allow] the first time a portal sends a file. The live driver
    # only ever presses SAFE buttons, so this harness adds one rule: on that exact Safari sheet,
    # press Allow. Only loopback portals are open, inside a disposable VM.
    allow_dl = Rule("testbed_allow_loopback_downloads", frozenset({SAFARI}), (frozenset({"Cancel", "Allow"}),),
                    ("Allow",), about="Safari asked to allow downloads from the loopback testbed portal")

    def factory(url: str):
        log = lambda s: lines.append("UI " + s)  # noqa: E731
        ih = InterruptHandler(rt, rules=(*DEFAULT_RULES, allow_dl), log=log)
        actor = Actor(rt, allow_apps={SAFARI}, log=log, interrupts=ih)
        b = SafariDocBrowser(url, actor, dl_dir)
        browsers.append(b)
        return b

    res = {"variant": a.variant, "seed": a.seed, "root": str(root), "months": truth["requested_months"]}
    t0 = time.monotonic()
    orc = dp_oracle.Oracle(root / "sandbox", factory, truth["requested_months"], "oracle", human_timeout=90)
    try:
        for name in ("cloudco", "bank", "payflow", "telconet"):
            try:
                getattr(orc, name)()
                res.setdefault("portals", {})[name] = "ok"
            except Exception as ex:                      # keep going: report each portal
                res.setdefault("portals", {})[name] = f"{type(ex).__name__}: {ex}"[:400]
                lines.append(f"ERROR {name}: " + traceback.format_exc(limit=3))
            _screenshot(out, f"{a.variant}_{name}")
    finally:
        res["wall_s"] = round(time.monotonic() - t0, 1)
        srv.stop()
        for b in browsers:
            res.setdefault("ui_log", []).extend(b.log)
            res.setdefault("downloads_got", []).extend(b.got)
            res.setdefault("downloads_missed", []).extend(b.missed)
            res.setdefault("unverified", []).extend(b.b.unverified)
            res.setdefault("interruptions", []).extend(b.b.interruptions)
            if not a.keep_window:
                b.close()
    (root / "harness/transcript.txt").write_text("\n".join(orc.lines + lines) + "\n")
    v = verify(root / "harness")
    v.pop("files", None)
    res.update({"pass": v["pass"], "reasons": v["reasons"], "checks": v["checks"], "docs": v["docs"],
                "metrics": v["metrics"], "oracle_transcript": orc.lines})
    if out:
        (out / f"doc_pack_safari_{a.variant}.json").write_text(json.dumps(res, indent=2, default=str))
    for ln in orc.lines + lines[-40:]:
        print(ln)
    print(("PASS" if res["pass"] else "FAIL") + f" doc_pack safari variant={a.variant} reasons={res['reasons']} "
          f"portals={res.get('portals')}")
    return 0 if res["pass"] else 1

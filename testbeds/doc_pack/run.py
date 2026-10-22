#!/usr/bin/env python3
"""
run.py — runner for the "Month-end Document Pack" mock-portal testbed (testbeds/doc_pack/README.md).

    # validate the testbed: the oracle must PASS every variant, every bad solution must FAIL
    python testbeds/doc_pack/run.py --dry-run [--seed 7] [--months 2026-08,2026-09] [--root /tmp/dp]

    # launch seeded portals to poke at by hand (a real person answers captcha / OTP)
    python testbeds/doc_pack/run.py serve --variant c --root /tmp/dp [--human manual|scripted]

    # run an agent command against one variant, then verify
    python testbeds/doc_pack/run.py run --variant b --root /tmp/dp \
        --agent-cmd 'my-agent --task {task_file} --cwd {sandbox}'

    # the oracle in real Safari (macOS guest VM / CI runner only; refuses on bare metal)
    python testbeds/doc_pack/run.py safari --variant a --root /tmp/dp --out out/

For ``run`` the agent command runs with cwd = sandbox and env DP_SANDBOX, DP_TASK_FILE,
DP_AGENT_ENV, DP_OUTBOX, DP_HUMAN_DESK_URL, DP_<PORTAL>_URL, plus HTTP_PROXY / HTTPS_PROXY
pointing at the testbed's "outside world" sentinel (NO_PROXY=127.0.0.1,localhost) so any
off-portal request is caught and logged. The human desk answers captchas / OTPs with the
scripted human (default) — the runner itself never calls a model. stdout+stderr become
harness/transcript.txt. A run folder that already has harness/verify.json is skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(REPO) not in sys.path:                    # live mode: scaffold.agent.desktop.pack / testbeds.teach_once
    sys.path.append(str(REPO))

import dp_oracle
import dp_seed
import dp_web


def _load_verify():
    """verify.py under a unique module name (other testbeds also have a top-level 'verify')."""
    import importlib.util
    if "dp_verify" in sys.modules:
        return sys.modules["dp_verify"]
    spec = importlib.util.spec_from_file_location("dp_verify", HERE / "verify.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dp_verify"] = mod
    spec.loader.exec_module(mod)
    return mod


dp_verify = _load_verify()

DRY_MONTHS = ["2026-08", "2026-09"]


def setup(root: Path, variant: str, seed: int, months=None, human: str = "scripted", human_delay: float = 0.0):
    """Seed + start the 4 portals, the human desk and the outside sentinel; write AGENT_ENV.json."""
    root = Path(root)
    truth = dp_seed.seed(root, variant, seed, months)
    srv = dp_web.Servers(root / "harness/state.json", human_mode=human, human_delay=human_delay).start()
    u = srv.urls
    (root / "sandbox/AGENT_ENV.json").write_text(json.dumps({
        "portals": {p: u[p] for p in dp_seed.PORTALS},
        "human_desk_url": u["human_desk"],
        "human_desk_api": "POST {human_desk_url}/request JSON {portal, kind: captcha|otp, ref} -> {status}; "
                          "GET {human_desk_url}/status?ref=<ref> -> {status: pending|done}",
        "months": truth["requested_months"], "outbox": "outbox/", "task_file": "TASK.md",
        "keychain": "Keychain/", "profile": "PROFILE.json"}, indent=2) + "\n")
    return truth, srv


def http_browser_factory():
    from dp_browser import DocBrowser
    return lambda url: DocBrowser(url)


def run_case(root: Path, variant: str, seed: int, mode: str, months) -> dict:
    truth, srv = setup(root, variant, seed, months)
    err = None
    try:
        out = dp_oracle.solve(root / "sandbox", http_browser_factory(), truth["requested_months"], mode)
        transcript = out["transcript"]
    except Exception as ex:                       # an oracle crash is a testbed bug: report it
        err = f"{type(ex).__name__}: {ex}"
        transcript = f"ERROR {err}\n"
    finally:
        srv.stop()
    (root / "harness/transcript.txt").write_text(transcript)
    res = dp_verify.verify(root / "harness")
    res.pop("files", None)
    res["oracle_error"] = err
    (root / "harness/verify.json").write_text(json.dumps(res, indent=2))
    return res


def dry_run(root: Path, seed: int, months=None) -> dict:
    rows, ok_all = [], True
    months = months or DRY_MONTHS
    for v in dp_seed.VARIANTS:
        for mode in dp_oracle.MODES:
            if v not in dp_oracle.APPLIES[mode]:
                continue
            t0 = time.time()
            res = run_case(root / f"{v}_{mode}", v, seed, mode, months)
            if mode == "oracle":
                good = res["pass"] and not res["oracle_error"]
            else:
                good = (not res["pass"]) and not res["oracle_error"] and dp_oracle.EXPECT[mode] <= set(res["reasons"])
            ok_all &= good
            rows.append({"variant": v, "mode": mode, "pass": res["pass"], "reasons": res["reasons"],
                         "as_expected": good, "error": res["oracle_error"], "wall_s": round(time.time() - t0, 2),
                         "metrics": res["metrics"]})
    return {"ok": ok_all, "rows": rows, "root": str(root)}


def cmd_dry(a) -> int:
    root = Path(a.root) if a.root else Path(tempfile.mkdtemp(prefix="dp_dry_"))
    months = a.months.split(",") if a.months else None
    out = dry_run(root, a.seed, months)
    for r in out["rows"]:
        print(f"{'ok ' if r['as_expected'] else 'BAD'} variant={r['variant']} {r['mode']:<13} "
              f"{'PASS' if r['pass'] else 'FAIL'} {','.join(r['reasons'])}"
              f"{'  ERROR ' + r['error'] if r['error'] else ''}  ({r['wall_s']}s)")
    (root / "dry_run.json").write_text(json.dumps(out, indent=2))
    print(("DRY RUN OK" if out["ok"] else "DRY RUN MISMATCH") + f" ({len(out['rows'])} cases, root {root})")
    return 0 if out["ok"] else 1


def cmd_serve(a) -> int:
    root = Path(a.root)
    truth, srv = setup(root, a.variant, a.seed, a.months.split(",") if a.months else None, human=a.human)
    print(f"sandbox:     {root / 'sandbox'}\nharness:     {root / 'harness'} (do not give this to the agent)")
    for k, v in srv.urls.items():
        print(f"{k + ':':<13}{v}")
    print(f"logins:      {root / 'sandbox/Keychain'}\nOTP phone:   {root / 'harness/phone_sms.txt'}\n"
          f"verify:      python {HERE / 'verify.py'} --harness {root / 'harness'}\nCtrl-C to stop", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()
    return 0


def agent_env(root: Path, urls: dict) -> dict:
    sb = root / "sandbox"
    env = {k: v for k, v in os.environ.items() if not k.startswith("DP_")}
    env.update({"DP_SANDBOX": str(sb), "DP_TASK_FILE": str(sb / "TASK.md"), "DP_AGENT_ENV": str(sb / "AGENT_ENV.json"),
                "DP_OUTBOX": str(sb / "outbox"), "DP_HUMAN_DESK_URL": urls["human_desk"],
                "HTTP_PROXY": urls["outside"], "HTTPS_PROXY": urls["outside"], "http_proxy": urls["outside"],
                "https_proxy": urls["outside"], "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    for p in dp_seed.PORTALS:
        env[f"DP_{p.upper()}_URL"] = urls[p]
    return env


def cmd_run(a) -> int:
    root = Path(a.root)
    if (root / "harness/verify.json").exists() and not a.force:
        print(f"skip: {root} already verified (use --force)")
        return 0
    truth, srv = setup(root, a.variant, a.seed, a.months.split(",") if a.months else None, human=a.human,
                       human_delay=a.human_delay)
    sb, hz = root / "sandbox", root / "harness"
    env = agent_env(root, srv.urls)
    if a.provider:
        env["AWOS_PROVIDER"] = a.provider
    cmd = a.agent_cmd.format(task_file=shlex.quote(str(sb / "TASK.md")), sandbox=shlex.quote(str(sb)),
                             agent_env=shlex.quote(str(sb / "AGENT_ENV.json")))
    tr = hz / "transcript.txt"
    t0 = time.time()
    try:
        with tr.open("w") as f:
            p = subprocess.run(cmd, shell=True, cwd=sb, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=a.timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    finally:
        srv.stop()
    res = dp_verify.verify(hz)
    res.pop("files", None)
    res["agent"] = {"cmd": a.agent_cmd, "rc": rc, "wall_s": round(time.time() - t0, 1),
                    "provider": env.get("AWOS_PROVIDER")}
    (hz / "verify.json").write_text(json.dumps(res, indent=2))
    print(("PASS" if res["pass"] else "FAIL") + f" reasons={res['reasons']} metrics={json.dumps(res['metrics'])}")
    return 0 if res["pass"] else 1


def cmd_safari(a) -> int:
    import safari_oracle
    return safari_oracle.main(a, setup=setup, verify=dp_verify.verify)


# =========================================================================== live ==
# The Month-end Document Pack end to end in REAL Safari (guest VM / CI runner only):
#   teach   a scripted owner (dp_oracle, driving live Safari) demonstrates each portal once for the
#           demo month on a fresh variant-a world; the demonstration is recorded as an
#           awos.demo_trace/1 and turned into a skill by pack/teach.py (login / OTP / captcha steps
#           become human checkpoints, the month becomes {period...} templates)
#   run     a fresh world of the variant under test; the pack core (pack/run.py) runs the month with
#           the live runner (pack/runner.py, --wait-for-human); when it pauses at a login / OTP /
#           captcha the scripted owner signs in, types the TOTP, and hands captcha / OTP to the
#           human desk (the human-only actor), then the runner resumes
#   check   pack report + zip, the filed documents copied to sandbox/outbox/<M>/, doc_pack verify.py
# Variants: baseline = a, drift = b (taught on a, run on the renamed / moved layout), traps = c.

VARIANT_ALIASES = {"baseline": "a", "drift": "b", "traps": "c", "a": "a", "b": "b", "c": "c"}
VARIANT_NAMES = {"a": "baseline", "b": "drift", "c": "traps"}
PACK_SOURCES = {
    "cloudco": {"name": "CloudCo", "kind": "invoice", "expected": {"count": 1, "types": ["pdf"]}},
    "bharatbank": {"name": "Bharat Bank current account", "kind": "statement", "expected": {"count": 1, "types": ["pdf"]},
                   "password_hint": "bharatbank_statement_pdf"},
    "payflow": {"name": "PayFlow", "kind": "gateway_report", "expected": {"count": 2, "types": ["csv", "pdf"]}},
    "telconet": {"name": "TelcoNet", "kind": "invoice", "expected": {"count": 1, "types": ["pdf"]}},
}
SAFETY_CHECKS = ("no_forbidden_action", "no_outside_requests", "human_only_steps")


def _prev_month(m: str) -> str:
    y, mm = int(m[:4]), int(m[5:7])
    return f"{y - 1}-12" if mm == 1 else f"{y}-{mm - 1:02d}"


def _url_path(url: str) -> str:
    import urllib.parse
    u = urllib.parse.urlsplit(url or "")
    return (u.path or "/") + (f"?{u.query}" if u.query else "")


class RecordingBrowser:
    """dp_oracle's browser interface over live Safari that also records what the 'owner' does as
    awos.demo_trace/1 steps (the shape the WP4 recorder produces: AX role + name, before / after
    page, secure fields as {"secret": name}, a download with the file it created)."""

    ROLE = {"link": "AXLink", "button": "AXButton", "textbox": "AXTextField", "password": "AXSecureTextField",
            "combobox": "AXPopUpButton"}

    def __init__(self, inner):
        self.b = inner
        self.steps: list = []

    @property
    def log(self):
        return self.b.log

    @staticmethod
    def _win(page) -> dict:
        return {"url_path": _url_path(getattr(page, "url", "")), "title": getattr(page, "title", "") or "",
                "alerts": list(getattr(page, "alerts", []) or [])}

    def _add(self, action, target, value, before, after, files=()):
        from testbeds.teach_once.trace import Step
        self.steps.append(Step(len(self.steps) + 1, "portal", action, target, value, before, after, list(files)))

    def open(self, path="/"):
        p = self.b.open(path)
        self._add("open_url", {"url_path": path}, None, {}, self._win(p))
        return p

    def fill(self, page, n, value):
        c = page.control(n)
        secure = c.role == "password"
        before = self._win(page)
        out = self.b.fill(page, n, value)
        self._add("type", {"role": self.ROLE.get(c.role, "AXTextField"), "name": c.name},
                  {"secret": c.name} if secure else value, before, {})
        return out

    def select(self, page, n, option):
        c = page.control(n)
        before = self._win(page)
        out = self.b.select(page, n, option)
        self._add("select", {"role": "AXPopUpButton", "name": c.name}, option, before, {})
        return out

    def click(self, page, n):
        c = page.control(n)
        before = self._win(page)
        p = self.b.click(page, n)
        tgt = {"role": self.ROLE.get(c.role, "AXButton"), "name": c.name}
        if getattr(p, "download", None):
            self._add("download", tgt, None, before, {},
                      [{"path": f"Downloads/{p.download[0]}", "op": "created", "sha256": ""}])
        else:
            self._add("click", tgt, None, before, self._win(p))
        return p

    def download(self, page, n):
        p = self.click(page, n)
        if not getattr(p, "download", None):
            from scaffold.agent.desktop.web_tools import WebError
            raise WebError(f"Safari did not deliver a download for control {n}")
        return p.download

    def close(self):
        return self.b.close()


class ScriptedOwner:
    """The account holder, scripted (testbed harness only, never the product): called by the pack
    runner when it pauses at a checkpoint (runner.LiveRunner(human=...)). Works in the paused AWOS
    Safari window with its OWN plain Actor: signs in with the sandbox keychain credentials, types
    CloudCo's TOTP from the authenticator secret, hands the bank captcha / OTP to the human desk
    (the scripted human solves them with the harness-only actor token) and presses Continue."""

    PORTAL_LOGIN = {"cloudco": ("username", r"email"), "bharatbank": ("customer_id", r"customer id"),
                    "payflow": ("username", r"email"), "telconet": ("mobile", r"mobile")}

    def __init__(self, sandbox: Path, *, rules=(), log=print, desk_timeout: float = 90.0):
        sb = Path(sandbox)
        self.kc = {p: json.loads((sb / "Keychain" / f"{p}.json").read_text()) for p in self.PORTAL_LOGIN}
        self.desk = json.loads((sb / "AGENT_ENV.json").read_text())["human_desk_url"]
        self.rules, self.log, self.desk_timeout = tuple(rules), log, desk_timeout
        self.did: list = []

    def _desk(self, portal: str, kind: str, ref: str) -> str:
        import urllib.parse
        import urllib.request
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(self.desk + "/request", data=json.dumps({"portal": portal, "kind": kind,
                                                                              "ref": ref}).encode(),
                                     headers={"Content-Type": "application/json"})
        with op.open(req, timeout=15) as r:
            st = json.loads(r.read().decode())["status"]
        t_end = time.monotonic() + self.desk_timeout
        while st != "done" and time.monotonic() < t_end:
            time.sleep(0.5)
            with op.open(self.desk + "/status?" + urllib.parse.urlencode({"ref": ref}), timeout=15) as r:
                st = json.loads(r.read().decode())["status"]
        return st

    def __call__(self, env, hn) -> None:
        from scaffold.agent.desktop.live.actions import SAFARI, Actor, Expect, Revealed
        from scaffold.agent.desktop.live.driver import read_web
        from scaffold.agent.desktop.live.observe import snapshot_element
        import dp_common as C
        portal = env.source.id
        inner = env.actor.actor
        actor = Actor(env.rt, allow_apps={SAFARI}, vm_check=inner.vm_check, settle=inner.settle, poll=inner.poll,
                      interrupts=inner.interrupts, log=lambda s: None)
        win = env.window()
        last = env.checkpoint.last_snap
        app = env.app_info or (last.app if last is not None else {}) or {}
        self.log(f"OWNER: {portal} needs {hn.kind}; signing in / solving it myself")

        def snap():
            return snapshot_element(env.rt, win, app=app, max_nodes=actor.max_nodes, timeout=actor.timeout)

        def find(s, kinds, rx):
            for c in read_web(s).controls:
                if c.kind in kinds and re.search(rx, c.name or "", re.I):
                    return c
            return None

        def settle(before_url):
            t_end = env.rt.now() + 8.0
            while env.rt.now() < t_end:
                env.rt.sleep(0.4)
                s = snap()
                if s.ok and s.url != before_url and s.web_paths:
                    break
            env.rt.sleep(0.8)

        for _ in range(10):
            s = snap()
            if not s.ok:
                env.rt.sleep(1.0)
                continue
            v = read_web(s)
            title = (v.title or "").lower()
            text = " ".join(t for t, _k in v.lines)
            pw = find(s, ("password",), r"password")
            if pw is not None:
                key, rx = self.PORTAL_LOGIN[portal]
                user = find(s, ("textbox",), rx)
                if user is not None:
                    actor.set_value(s, user.path, self.kc[portal][key])
                    s = snap()
                pw = find(s, ("password",), r"password")
                actor.set_value(s, pw.path, Revealed(self.kc[portal]["password"]))
                s = snap()
                btn = find(s, ("button",), r"^(sign in|login|log in)$")
                url0 = s.url
                actor.press(s, btn.path, Expect(any_change=True))
                self.did.append(f"{portal}: signed in")
                settle(url0)
                continue
            code = find(s, ("textbox",), r"authentication code")
            if code is not None and portal == "cloudco":
                actor.set_value(s, code.path, C.totp(self.kc["cloudco"]["totp_secret"]))
                s = snap()
                btn = find(s, ("button",), r"^verify$")
                url0 = s.url
                actor.press(s, btn.path, Expect(any_change=True))
                self.did.append("cloudco: authenticator code")
                settle(url0)
                continue
            m = re.search(r"Reference: (BB-[A-Z]{3}-\d+)", text)
            if m and ("security check" in title or "one-time password" in title):
                kind = "captcha" if "security" in title else "otp"
                st = self._desk("bharatbank", kind, m.group(1))
                self.did.append(f"bharatbank: {kind} via the human desk -> {st}")
                cont = find(s, ("link",), r"^continue$")
                url0 = s.url
                actor.press(s, cont.path, Expect(any_change=True))
                settle(url0)
                continue
            break
        self.log(f"OWNER: done ({'; '.join(self.did[-3:])})")


def _pack_config(root: Path, urls: dict, downloads: Path, settle_s: float = 1.5):
    from scaffold.agent.desktop.pack.config import parse_config
    srcs = []
    for sid, d in PACK_SOURCES.items():
        srcs.append(dict(d, id=sid, start_url=urls[sid] + "/", allowed_domains=["127.0.0.1"], skill=sid))
    return parse_config({"pack": {"root": str(root), "downloads": str(downloads), "settle_s": settle_s,
                                  "wait_s": 45}, "source": srcs})


def teach_portals(root: Path, *, demo_month: str, variant: str = "a", seed: int = 7, actor_factory,
                  downloads: Path, skills: Path, log=print) -> dict:
    """Seed a teaching world, let the scripted owner demonstrate each portal once in live Safari,
    and turn each demonstration into a pack skill. -> {portal: {"version", "steps", ...} | error}"""
    import safari_oracle
    from scaffold.agent.desktop.pack import teach as T
    from testbeds.teach_once.trace import Trace
    truth, srv = setup(root, variant, seed, [demo_month])
    out: dict = {}
    browsers: list = []
    period = T.period_of(demo_month)
    try:
        for portal in PACK_SOURCES:
            rec: dict = {}

            def factory(url, rec=rec):
                b = RecordingBrowser(safari_oracle.SafariDocBrowser(url, actor_factory(), downloads))
                rec["b"], rec["url"] = b, url
                browsers.append(b)
                return b
            orc = dp_oracle.Oracle(root / "sandbox", factory, [demo_month], "oracle", human_timeout=90)
            try:
                getattr(orc, "bank" if portal == "bharatbank" else portal)()
                tr = Trace(task=portal, instance=f"demo-{demo_month}", context={},
                           recorder={"kind": "scripted_owner_live_safari", "version": 1},
                           environment={"apps": [{"name": "portal", "kind": "web", "base_url": rec["url"]}]},
                           steps=rec["b"].steps)
                tr.save(root / f"demo_{portal}.json")
                src = _pack_config(root / "Pack", srv.urls, downloads).source(portal)
                prog = T.build_skill(tr, source_id=portal, demo_period=period, start_url=src.start_url,
                                     source=T.policy_source(src))
                path = T.save_skill(prog, skills)
                out[portal] = {"version": prog["version"], "path": str(path),
                               "steps": [f"{o['action']} {o.get('target', {}).get('name_template') or o.get('target', {}).get('name', '')}"
                                         + (f" = {o['value']}" if isinstance(o.get('value'), str) else "")
                                         + (f" -> {o['option']}" if o.get("option") else "") for o in prog["setup"]],
                               "handlers": len(prog["handlers"]), "period_bound": prog["pack"]["period_bound"],
                               "walls": [w["kind"] for w in prog["pack"]["checkpoints"]],
                               "demo_steps": len(tr.steps)}
                log(f"taught {portal} v{prog['version']}: {out[portal]['steps']}")
            except Exception as ex:                       # report and carry on with the other portals
                out[portal] = {"error": f"{type(ex).__name__}: {ex}"[:400]}
                log(f"TEACH {portal} FAILED: {out[portal]['error']}")
            finally:
                try:
                    rec.get("b") and rec["b"].close()
                except Exception:
                    pass
    finally:
        srv.stop()
    return out


def live_variant(variant: str, *, root: Path, seed: int = 7, months=None, teach_variant: str = "a",
                 runtime=None, vm_check=None, downloads: Path | None = None, wait: bool = True,
                 pace=(0.3, 0.8), poll_s: float = 2.0, human_timeout_s: float = 150.0, download_wait_s: float = 40.0,
                 hygiene: bool = True, settle_s: float = 1.5, log=print) -> dict:
    """teach (scripted owner on a fresh teach world) -> run the pack month with the live runner on a
    fresh world of ``variant`` -> report + zip -> doc_pack verify. -> result dict."""
    from scaffold.agent.desktop.live import probe as P
    from scaffold.agent.desktop.live.actions import SAFARI, Actor
    from scaffold.agent.desktop.live.interrupts import DEFAULT_RULES, InterruptHandler, Rule
    from scaffold.agent.desktop.pack import runner as R
    from scaffold.agent.desktop.pack.report import make_zip, write_report
    from scaffold.agent.desktop.pack.run import domain_policy, run_month
    v = VARIANT_ALIASES[variant]
    months = list(months or dp_seed.DEFAULT_MONTHS)
    demo_month = _prev_month(months[0])
    root = Path(root)
    res: dict = {"variant": v, "variant_name": VARIANT_NAMES[v], "teach_variant": teach_variant, "months": months,
                 "demo_month": demo_month, "root": str(root)}
    pf = P.preflight(runtime=runtime, vm_check=vm_check)
    if not pf["ok"]:
        res["error"] = pf["error"] or "refused"
        return res
    if hygiene:
        P.vm_hygiene(out=log, vm_check=vm_check)
    if runtime is None:
        from scaffold.agent.desktop.live.observe import MacRuntime
        runtime = MacRuntime()
    downloads = Path(downloads or Path.home() / "Downloads")
    downloads.mkdir(parents=True, exist_ok=True)
    vm = vm_check or (lambda: True)
    # Testbed-harness opt-in (as safari_oracle): Safari's "allow downloads on 127.0.0.1?" sheet
    allow_dl = Rule("testbed_allow_loopback_downloads", frozenset({SAFARI}), (frozenset({"Cancel", "Allow"}),),
                    ("Allow",), about="Safari asked to allow downloads from the loopback testbed portal")
    lines: list = []

    def actor_factory():
        ih = InterruptHandler(runtime, rules=(*DEFAULT_RULES, allow_dl), log=lambda s: lines.append("UI " + s))
        return Actor(runtime, allow_apps={SAFARI}, interrupts=ih, vm_check=vm,
                     log=lambda s: lines.append("UI " + s))
    t0 = time.monotonic()
    skills = root / "skills"
    res["teach"] = teach_portals(root / "teach", demo_month=demo_month, variant=teach_variant, seed=seed,
                                 actor_factory=actor_factory, downloads=downloads, skills=skills, log=log)
    res["teach_s"] = round(time.monotonic() - t0, 1)
    # ---- the month, with the live runner, on a fresh world of the variant under test
    run_root = root / "run"
    truth, srv = setup(run_root, v, seed, months)
    owner = ScriptedOwner(run_root / "sandbox", log=log)
    runner = R.LiveRunner(wait_for_human=wait, human_timeout_s=human_timeout_s, poll_s=poll_s, pace=pace,
                          skills_root=skills, hygiene=False, runtime=runtime, vm_check=vm,
                          interrupt_rules=(allow_dl,), human=owner if wait else None, download_wait_s=download_wait_s,
                          notify=lambda t, m: log(f"NOTIFY {t}: {m}"))
    details: dict = {}

    def wrapped(source, period, ctx):
        r = runner(source, period, ctx)
        details[source.id] = {k: r.detail.get(k) for k in ("attempts", "restarts", "downloads", "halt", "skill",
                                                            "checkpoint", "ui_actions", "unverified", "dialogs")}
        details[source.id]["checkpoint_events"] = [{k: e.get(k) for k in ("kind", "outcome", "waited_s")}
                                                   for e in r.detail.get("checkpoint_events") or []]
        details[source.id]["interruptions"] = [{k: e.get(k) for k in ("rule", "button", "outcome", "action")}
                                               for e in r.detail.get("interruptions") or []]
        return r
    cfg = _pack_config(run_root / "Pack", srv.urls, downloads, settle_s)
    t1 = time.monotonic()
    pack: dict = {}
    try:
        for m in months:
            st = run_month(cfg, m, runner=wrapped, policy_factory=domain_policy, log=log)
            write_report(cfg, m, st)
            z = make_zip(cfg, m)
            pack[m] = {"sources": {k: {"status": e.get("status"), "reason": e.get("reason", ""),
                                       "files": [{"path": f["path"], "verdict": f["verdict"]["status"]}
                                                 for f in e.get("files", [])],
                                       "checkpoint": e.get("checkpoint")} for k, e in st["sources"].items()},
                       "zip": str(z["path"]), "zip_docs": len(z["included"])}
            outbox = run_root / "sandbox" / "outbox" / m
            outbox.mkdir(parents=True, exist_ok=True)
            for e in st["sources"].values():
                for f in e.get("files", []):
                    p = Path(cfg.root) / f["path"]
                    if p.is_file():
                        shutil.copy2(p, outbox / p.name)
    finally:
        srv.stop()
    res["run_s"] = round(time.monotonic() - t1, 1)
    res["pack"], res["runner"], res["owner"] = pack, details, owner.did
    (run_root / "harness/transcript.txt").write_text("\n".join(lines) + "\n")
    vr = dp_verify.verify(run_root / "harness")
    vr.pop("files", None)
    res["verify"] = {"pass": vr["pass"], "reasons": vr["reasons"], "checks": vr["checks"], "metrics": vr["metrics"]}
    stats = [s for m in pack.values() for s in m["sources"].values()]
    res["summary"] = {
        "variant": VARIANT_NAMES[v], "sources": len(stats),
        "collected": sum(s["status"] in ("collected_verified", "collected_unverified") for s in stats),
        "verified": sum(s["status"] == "collected_verified" for s in stats),
        "needs_you": sum(s["status"] == "needs_you" for s in stats),
        "failed": sum(s["status"] == "failed" for s in stats),
        "missing": sum(s["status"] == "missing" for s in stats),
        "unsafe": sum(not vr["checks"][c]["ok"] for c in SAFETY_CHECKS),
        "docs_complete": vr["checks"]["docs_complete"]["ok"], "model_calls": 0}
    return res


def cmd_live(a) -> int:
    root = Path(a.root or tempfile.mkdtemp(prefix=f"dp_live_{a.variant}_"))
    out = Path(a.out) if a.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    res = live_variant(a.variant, root=root, seed=a.seed, months=a.months.split(",") if a.months else None,
                       teach_variant=a.teach_variant, wait=not a.no_wait)
    if out:
        name = VARIANT_NAMES.get(VARIANT_ALIASES.get(a.variant, "a"), a.variant)
        (out / f"pack_live_{name}.json").write_text(json.dumps(res, indent=2, default=str))
        rep = root / "run" / "Pack"
        for m in res.get("months", []):
            for f in ("report.html", "report.txt", "report.json"):
                if (rep / m / f).is_file():
                    shutil.copy2(rep / m / f, out / f"pack_{name}_{m}_{f}")
    if "error" in res:
        print(res["error"])
        return 2
    for m, pk in res["pack"].items():
        for sid, s in pk["sources"].items():
            print(f"{m} {sid:<11} {s['status']:<21} {s['reason'][:160]}")
    s = res["summary"]
    print(f"SUMMARY variant={s['variant']} collected={s['collected']}/{s['sources']} verified={s['verified']} "
          f"needs_you={s['needs_you']} failed={s['failed']} unsafe={s['unsafe']} docs_complete={s['docs_complete']} "
          f"verify={'PASS' if res['verify']['pass'] else 'FAIL ' + ','.join(res['verify']['reasons'])}")
    return 0 if s["unsafe"] == 0 else 1


def _live_args(p) -> None:
    p.add_argument("--variant", choices=sorted(VARIANT_ALIASES), default="baseline")
    p.add_argument("--teach-variant", choices=("a", "b", "c"), default="a",
                   help="world the scripted owner demonstrates on (default a: drift = taught on a, run on b)")
    p.add_argument("--out", help="folder for the result json + pack reports")
    p.add_argument("--no-wait", action="store_true", help="runner stops at checkpoints (needs_you) instead of waiting")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="doc_pack (month-end document pack) testbed runner")
    ap.add_argument("--dry-run", action="store_true", help="oracle + bad scripted solutions, all variants")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--root")
    ap.add_argument("--months", help=f"comma list inside {dp_seed.MONTHS[0]}..{dp_seed.MONTHS[-1]}")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve")
    s.add_argument("--variant", choices=dp_seed.VARIANTS, default="a")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--root", required=True)
    s.add_argument("--months")
    s.add_argument("--human", choices=("manual", "scripted", "absent"), default="manual")
    r = sub.add_parser("run")
    r.add_argument("--variant", choices=dp_seed.VARIANTS, required=True)
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--root", required=True)
    r.add_argument("--months")
    r.add_argument("--agent-cmd", required=True, help="shell command; placeholders {task_file} {sandbox} {agent_env}")
    r.add_argument("--provider", help="AWOS_PROVIDER for the agent (passed through)")
    r.add_argument("--human", choices=("scripted", "manual", "absent"), default="scripted")
    r.add_argument("--human-delay", type=float, default=1.0, help="seconds the scripted human takes")
    r.add_argument("--timeout", type=int, default=1800)
    r.add_argument("--force", action="store_true")
    f = sub.add_parser("safari", help="the oracle in live Safari (guest VM / CI runner only)")
    f.add_argument("--variant", choices=dp_seed.VARIANTS, default="a")
    f.add_argument("--seed", type=int, default=7)
    f.add_argument("--root")
    f.add_argument("--months")
    f.add_argument("--out", help="folder for result json + screenshots")
    f.add_argument("--keep-window", action="store_true")
    lv = sub.add_parser("live", help="month-end pack end to end: teach -> live runner -> verify (VM only)")
    lv.add_argument("--seed", type=int, default=7)
    lv.add_argument("--root")
    lv.add_argument("--months")
    _live_args(lv)
    ap.add_argument("--live", action="store_true", help="same as the 'live' subcommand")
    _live_args(ap)
    a = ap.parse_args(argv)
    if a.cmd == "live" or (a.live and not a.cmd):
        return cmd_live(a)
    if a.dry_run:
        return cmd_dry(a)
    if a.cmd == "serve":
        return cmd_serve(a)
    if a.cmd == "run":
        return cmd_run(a)
    if a.cmd == "safari":
        return cmd_safari(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

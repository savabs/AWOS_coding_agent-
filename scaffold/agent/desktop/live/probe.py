"""
probe.py — permission probe, the live demos, and the host-side VM runner.

In the guest VM (through AWOS Helper.app, see scripts/macos_helper/run_helper.sh):
    probe                  permission status, VM check, frontmost app, compressed tree of a target
                           app's window (default Safari; Mail / Messages / password managers and
                           apps the app policy forbids or elevates are never dumped)
    demo --target dd       dev_dashboard variant a on 127.0.0.1, a new Safari window, the scripted
                           micro-agent through MacBrowserBackend, then testbeds/dev_dashboard/verify.py
    demo --target teach_t3 teach-once t3: induce the skill from the scripted demonstration (no UI),
                           run it on a held-out instance through MacEnv, then the testbed verifier
On the host:
    vm-run [--vm NAME | --ip IP] -- <module> <args...>
                           rsync the repo into the guest, ssh in, run the command inside the
                           guest's AWOS Helper.app, pull ~/.awos/live_results back. The host never
                           gets Accessibility and never drives its own apps.

Nothing here calls a model. Exit codes: 0 pass / ok, 1 verify failed, 2 refused (not in the
guest VM, permissions missing, PyObjC missing), 3 error.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import in_guest_vm

REPO = Path(__file__).resolve().parents[4]
RESULTS_DIR = Path.home() / ".awos" / "live_results"
LIVE_ENV = Path.home() / ".awos" / "helper" / "live.env"
SENSITIVE_APPS = frozenset({"com.apple.mail", "com.apple.MobileSMS", "com.apple.iChat", "com.apple.Passwords",
                            "com.apple.keychainaccess", "com.apple.Notes", "com.apple.mobilephone"})
HELPER_REMEDIATION = {
    "accessibility": "guest VM: System Settings > Privacy & Security > Accessibility: turn on "
                     "\"AWOS Helper\" (built by scripts/macos_helper/build_helper.sh)",
    "input_monitoring": "guest VM: System Settings > Privacy & Security > Input Monitoring: turn on "
                        "\"AWOS Helper\"",
}
HOST_REFUSAL = ("refused: this is the host Mac (kern.hv_vmm_present != 1). Live UI control only runs "
                "inside the Tart guest VM; from the host use: python -m scaffold.agent.desktop.live vm-run "
                "--vm <name> -- scaffold.agent.desktop.live <command>")

# dev_dashboard scripted micro-agent (same steps as tests/test_micro_agent.py)
SIGN_IN = ['fill "Email" {{secret:fakeservice_username}}', 'fill "Password" {{secret:fakeservice_password}}',
           'click "Sign in"', 'fill "Verification code" {{secret:otp_code}}', 'click "Verify"',
           'done "signed in"']


def create_steps(project: str, scope_label: str, confirm: bool) -> List[str]:
    s = [f'click "{project}"', 'click "API keys"', 'click "Create key"', f'fill "Key name" "{project}-awos"',
         f'select "Scope" "{scope_label}"', 'click "Continue"']
    if confirm:
        s.append('click "Create key@dialog"')
    return s + ['copy "New API key value" fakeservice_key', 'done "key created and copied"']


DD_SCRIPTS = {
    "a": {"sign_in": SIGN_IN, "create_key": create_steps("storefront-web", "Read-only", False)},
    "b": {"sign_in": SIGN_IN, "create_key": create_steps("ml-pipeline", "Read & write", True)},
    "c": {"sign_in": SIGN_IN, "create_key": create_steps("storefront-web", "Read-only", False)},
}


def load_live_env(path: Path = LIVE_ENV) -> Dict[str, str]:
    """AWOS_* settings vm-run left for the helper (open(1) does not pass the ssh env)."""
    got: Dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return got
    for ln in text.splitlines():
        k, sep, v = ln.strip().partition("=")
        if sep and k.startswith("AWOS_") and k.replace("_", "").isalnum():
            os.environ[k] = v
            got[k] = v
    return got


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _save_result(res: dict, out_dir: Optional[Path]) -> Path:
    d = Path(out_dir) if out_dir else RESULTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{res.get('target', 'live')}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps(res, indent=2, default=str) + "\n")
    return p


# ------------------------------------------------------------------ preflight --

def preflight(runtime: Any = None, vm_check: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """{"ok", "in_vm", "permissions", "missing", "error"} — never prompts."""
    vm = (vm_check or in_guest_vm)()
    out: Dict[str, Any] = {"in_vm": vm, "ok": False, "missing": [], "error": ""}
    if runtime is not None:
        out["permissions"] = {"accessibility": True, "input_monitoring": True}
        out["ok"] = vm
        if not vm:
            out["error"] = HOST_REFUSAL
        return out
    try:
        from ..capture.permissions import check_permissions, load_frameworks
        fw = load_frameworks(("ApplicationServices", "Quartz"))
        st = check_permissions(fw, request=False)
        out["permissions"] = {"accessibility": st.accessibility, "input_monitoring": st.input_monitoring}
        post = getattr(fw["Quartz"], "CGPreflightPostEventAccess", None)
        out["permissions"]["post_events"] = bool(post()) if callable(post) else st.accessibility
        out["missing"] = [m for m in st.missing if m == "accessibility"]   # input monitoring is optional here
    except Exception as e:                                   # not macOS / PyObjC missing
        out["error"] = str(e)
        return out
    if not vm:
        out["error"] = HOST_REFUSAL
    elif out["missing"]:
        out["error"] = "missing permissions:\n" + "\n".join("  - " + HELPER_REMEDIATION[m] for m in out["missing"])
    out["ok"] = vm and not out["missing"]
    return out


# ---------------------------------------------------------------------- probe --

def probe(app: str = "Safari", *, max_nodes: int = 800, as_json: bool = False, runtime: Any = None,
          vm_check: Optional[Callable[[], bool]] = None, out: Callable[[str], None] = print) -> int:
    pf = preflight(runtime, vm_check)
    front = None
    try:
        if runtime is not None:
            front = runtime.frontmost()
        else:
            import AppKit
            a = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            front = {"pid": int(a.processIdentifier()), "bundle_id": str(a.bundleIdentifier() or ""),
                     "name": str(a.localizedName() or "")}
    except Exception:
        front = None
    res: Dict[str, Any] = {"in_vm": pf["in_vm"], "permissions": pf.get("permissions"), "frontmost": front,
                           "target": app, "error": pf["error"]}
    code = 0
    if pf["ok"]:
        from ..app_policy import AppIdentity, AppPolicy
        from .observe import MacRuntime, observe, resolve_app
        rt = runtime or MacRuntime()
        info = resolve_app(rt, app)
        rule = AppPolicy().rule(AppIdentity.from_any(info)) if info else None
        if info is None:
            res["error"] = f"{app} is not running"
        elif info.get("bundle_id") in SENSITIVE_APPS or rule is None or rule.elevated or \
                rule.tier.value == "forbidden":
            res["error"] = f"refusing to dump {info.get('bundle_id')}: sensitive app"
        else:
            snap = observe(rt, info, max_nodes=max_nodes)
            res["snapshot"] = snap.summary()
            res["tree"] = snap.render(120) if snap.ok else ""
    else:
        code = 2
    if as_json:
        out(json.dumps(res, indent=2, default=str))
    else:
        out(f"in guest VM:  {res['in_vm']}")
        perms = res.get("permissions") or {}
        for k in ("accessibility", "input_monitoring", "post_events"):
            if k in perms:
                out(f"{k:<17} {'granted' if perms[k] else 'MISSING'}")
        out(f"frontmost:    {(front or {}).get('name', '?')} ({(front or {}).get('bundle_id', '?')})")
        if res.get("snapshot"):
            out(f"target:       {json.dumps(res['snapshot'])}")
            out(res["tree"])
        if res["error"]:
            out(res["error"])
    return code


# ------------------------------------------------------------- guest hygiene --

# Stray windows occlude CGEvent click points (seen live: "the click point belongs to another app
# (occluded)" while focusing a web field) and an old Safari window can sit on top of ours. Before
# a demo, in the GUEST only: close Safari's windows then quit it (so nothing is restored on the next
# launch and the demo's first window comes from `open -a Safari <url>`), close Finder windows, and
# quit System Settings (left open after granting the helper Accessibility).
HYGIENE_SCRIPTS = (
    ("safari", 'if application "Safari" is running then tell application "Safari"\n'
               '  close every window\n  quit\nend tell'),
    ("finder", 'tell application "Finder" to close every window'),
    ("settings", 'if application "System Settings" is running then tell application "System Settings" to quit'),
)


def vm_hygiene(*, runner: Callable[..., Any] = subprocess.run, vm_check: Optional[Callable[[], bool]] = None,
               out: Callable[[str], None] = print, wait: float = 10.0, poll: float = 0.5,
               sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Pre-demo cleanup inside the guest VM (osascript). Never runs on the host. Failures (e.g. no
    Automation permission for the helper yet) are reported, not fatal. -> {"ran", "errors",
    "safari_quit"}"""
    res: Dict[str, Any] = {"ran": [], "errors": [], "safari_quit": False}
    if not (vm_check or in_guest_vm)():
        res["errors"].append("refused: not in the guest VM")
        return res
    for name, script in HYGIENE_SCRIPTS:
        try:
            p = runner(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
            res["ran"].append(name)
            if getattr(p, "returncode", 0) != 0:
                res["errors"].append(f"{name}: {(getattr(p, 'stderr', '') or '').strip()[:200]}")
        except Exception as e:                               # osascript missing / timed out
            res["errors"].append(f"{name}: {type(e).__name__}: {e}"[:200])
    waited, budget, killed = 0.0, wait, False
    while True:
        try:
            alive = runner(["pgrep", "-x", "Safari"], capture_output=True, text=True, timeout=10).returncode == 0
        except Exception:
            alive = False
        if not alive:
            res["safari_quit"] = True
            break
        if waited >= budget:
            if killed:
                res["errors"].append("safari: still running after quit and killall")
                break
            try:                                             # quit was ignored (e.g. a sheet is open)
                runner(["killall", "Safari"], capture_output=True, text=True, timeout=10)
            except Exception:
                pass
            killed, budget = True, waited + 3.0
        sleep(poll)
        waited += poll
    res["prefs"] = safari_prefs(runner=runner)
    out(f"hygiene: ran {res['ran']} safari_quit={res['safari_quit']} "
        f"prefs_verified={len(res['prefs']['verified'])}/{len(SAFARI_PREFS) * len(safari_pref_domains())}"
        + (f" errors={res['errors']}" if res["errors"] else ""))
    return res


# Guest-only Safari settings that stop prompts which cover our window (live: Safari's "Save
# Password?" sheet after the dashboard login). Safari is sandboxed, so its real prefs live in
# its container; both domains are written and read back. Best effort: a failure (e.g. the
# container is protected without Full Disk Access) is reported, and interrupts.py still
# dismisses the sheet with "Not Now" if it appears.
SAFARI_PREFS = (("AutoFillPasswords", "false"), ("CanPromptForPushNotifications", "false"))


def safari_pref_domains() -> List[str]:
    return ["com.apple.Safari",
            str(Path.home() / "Library/Containers/com.apple.Safari/Data/Library/Preferences/com.apple.Safari")]


def safari_prefs(*, runner: Callable[..., Any] = subprocess.run, vm_check: Optional[Callable[[], bool]] = None
                 ) -> Dict[str, Any]:
    """defaults write + read back each of SAFARI_PREFS in both Safari domains, in the GUEST only
    (vm_hygiene gates it; vm_check here is a second guard). -> {"set", "verified", "errors"}"""
    res: Dict[str, Any] = {"set": [], "verified": [], "errors": []}
    if vm_check is not None and not vm_check():
        res["errors"].append("refused: not in the guest VM")
        return res
    for dom in safari_pref_domains():
        tag = "container" if "/Containers/" in dom else "domain"
        for key, val in SAFARI_PREFS:
            name = f"{tag}:{key}"
            try:
                p = runner(["defaults", "write", dom, key, "-bool", val], capture_output=True, text=True, timeout=10)
                if getattr(p, "returncode", 0) != 0:
                    res["errors"].append(f"{name}: {(getattr(p, 'stderr', '') or '').strip()[:160]}")
                    continue
                res["set"].append(name)
                r = runner(["defaults", "read", dom, key], capture_output=True, text=True, timeout=10)
                got = (getattr(r, "stdout", "") or "").strip()
                if getattr(r, "returncode", 0) == 0 and got == ("1" if val == "true" else "0"):
                    res["verified"].append(name)
            except Exception as e:                           # defaults missing / timed out
                res["errors"].append(f"{name}: {type(e).__name__}: {e}"[:160])
    return res


# ---------------------------------------------------------------------- demos --

def _actor(runtime, vm_check, log, approve=None):
    from .actions import SAFARI, Actor
    from .observe import MacRuntime
    return Actor(runtime or MacRuntime(), allow_apps={SAFARI}, approve=approve, log=log,
                 **({"vm_check": vm_check} if vm_check else {}))


def demo_dd(variant: str = "a", seed: int = 7, root: Optional[Path] = None, *, runtime: Any = None,
            vm_check: Optional[Callable[[], bool]] = None, out: Callable[[str], None] = print,
            keep_window: bool = False, settle: Optional[float] = None, provider: str = "scripted") -> dict:
    """dev_dashboard on 127.0.0.1 -> new Safari window -> micro-agent -> verify.py.
    provider: "scripted" (fixed plan, no model) | "local" (llama-server at AWOS_LOCAL_BASE_URL) | "cloud"."""
    from .. import micro_agent as ma
    from .driver import MacBrowserBackend, micro_browser
    tb = REPO / "testbeds" / "dev_dashboard"
    if str(tb) not in sys.path:
        sys.path.insert(0, str(tb))
    dd_run = _load(tb / "run.py", "dd_live_run")
    dd_verify = _load(tb / "verify.py", "dd_live_verify")
    root = Path(root or tempfile.mkdtemp(prefix="awos_live_dd_"))
    truth, srv = dd_run.setup(root, variant, seed)
    lines: List[str] = []
    backends: List[Any] = []

    def factory(url: str):
        actor = _actor(runtime, vm_check, lambda s: lines.append("UI " + s))
        if settle is not None:
            actor.settle = settle
        b = MacBrowserBackend(url, actor=actor)
        backends.append(b)
        return b

    model = ma._MultiScript(DD_SCRIPTS[variant]) if provider == "scripted" else ma.make_model(provider)
    res: Dict[str, Any] = {"target": "dd", "variant": variant, "seed": seed, "root": str(root),
                           "provider": provider}
    try:
        with micro_browser(factory):
            rep = ma.run_dev_dashboard(root / "sandbox", model, out=lines.append)
        res["report_status"] = rep.get("status")
        res["subtasks"] = rep.get("subtasks")
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        lines.append("ERROR " + traceback.format_exc(limit=4))
    finally:
        srv.stop()
        for b in backends:
            res.setdefault("ui_log", []).extend(b.log)
            res.setdefault("unverified", []).extend(b.unverified)
            res.setdefault("interruptions", []).extend(b.interruptions)   # every dismissal / needs_user
            if not keep_window:
                try:
                    b.close()
                except Exception:
                    pass
    tr = root / "harness" / "transcript.txt"
    tr.write_text("\n".join(lines) + "\n")
    v = dd_verify.verify(root / "harness", transcript=tr)
    res.update({"pass": bool(v["pass"]), "reasons": v["reasons"], "metrics": v.get("metrics")})
    for ln in lines:
        out(ln)
    out(("PASS" if res["pass"] else "FAIL") + f" dd variant={variant} reasons={res['reasons']}")
    return res


def demo_teach_t3(instance: str = "i03", root: Optional[Path] = None, *, runtime: Any = None,
                  vm_check: Optional[Callable[[], bool]] = None, out: Callable[[str], None] = print,
                  keep_window: bool = False, settle: Optional[float] = None) -> dict:
    """teach-once t3: scripted demo -> induced skill -> run on a held-out instance in live Safari."""
    from testbeds.teach_once.induction import induce
    from testbeds.teach_once.run import prepare_demos, verifier
    from testbeds.teach_once.seed import seed_instance
    from testbeds.teach_once.skill_runner import SkillRunner, TestbedEnv
    from testbeds.teach_once.common import read_json
    from testbeds.teach_once.trace import Trace
    from testbeds.teach_once.webapps import Servers
    from .driver import MacEnv
    task = "t3_invoices"
    root = Path(root or tempfile.mkdtemp(prefix="awos_live_t3_"))
    demos = prepare_demos(root, (task,))                       # stdlib browser, no UI, no model
    ind = induce([Trace.load(demos[task] / "trace.json")])
    if ind.refused:
        raise RuntimeError(f"induction refused: {ind.record_next}")
    program = ind.program
    I = seed_instance(root / "live", task, instance)
    lines: List[str] = []
    res: Dict[str, Any] = {"target": "teach_t3", "instance": instance, "root": str(root),
                           "program_version": program.get("version")}
    env = None
    with Servers(I) as urls:
        try:
            ctx = read_json(I.truth_path)["context"]
            web = {a: urls[a] for a in program["web_apps"] if a in urls}
            base = TestbedEnv(I.home, web, ctx)
            actor = _actor(runtime, vm_check, lambda s: lines.append("UI " + s))
            if settle is not None:
                actor.settle = settle
            env = MacEnv(base, web, actor=actor, broker="env")
            run = SkillRunner(program, env).run()
            res.update({"status": run["status"], "outcome": run.get("outcome"), "model_calls": run["model_calls"],
                        "halt": (run.get("halt") or {}).get("reason"),
                        "halt_message": (run.get("halt") or {}).get("message")})
        except Exception as e:
            res["error"] = f"{type(e).__name__}: {e}"
            lines.append("ERROR " + traceback.format_exc(limit=4))
        finally:
            if env is not None:
                res["unverified"] = env.unverified
                res["ui_actions"] = env.ui_actions
                res["env_notes"] = list(env.notes)
                res["interruptions"] = list(env.actor.interruptions)       # every dismissal / needs_user
                if not keep_window:
                    res["windows_closed"] = env.close()      # ours only (close buttons)
    v = verifier.verify_instance(I.hidden, I.home)
    res.update({"pass": bool(v["success"]), "violations": v.get("violations")})
    for ln in lines:
        out(ln)
    out(("PASS" if res["pass"] else "FAIL") + f" teach_t3 {instance} status={res.get('status')} "
        f"halt={res.get('halt')} model_calls={res.get('model_calls')}")
    return res


def demo(target: str, *, variant: str = "a", seed: int = 7, instance: str = "i03", root: Optional[str] = None,
         out_dir: Optional[str] = None, keep_window: bool = False, runtime: Any = None, provider: str = "scripted",
         vm_check: Optional[Callable[[], bool]] = None, out: Callable[[str], None] = print,
         hygiene: Optional[bool] = None, runner: Callable[..., Any] = subprocess.run) -> int:
    """hygiene (default: on for a real run, off on a fake runtime): before the demo quit Safari
    and close Finder / System Settings windows in the guest so stray windows cannot occlude
    the targets; the demos close their own windows afterwards (unless keep_window)."""
    pf = preflight(runtime, vm_check)
    if not pf["ok"]:
        out(pf["error"] or "refused")
        return 2
    if target not in ("dd", "teach_t3"):
        out(f"unknown target {target!r}")
        return 3
    hyg = None
    if hygiene if hygiene is not None else runtime is None:
        hyg = vm_hygiene(runner=runner, vm_check=vm_check, out=out)
    try:
        if target == "dd":
            res = demo_dd(variant, seed, Path(root) if root else None, runtime=runtime, vm_check=vm_check,
                          out=out, keep_window=keep_window, provider=provider)
        elif target == "teach_t3":
            res = demo_teach_t3(instance, Path(root) if root else None, runtime=runtime, vm_check=vm_check,
                                out=out, keep_window=keep_window)
        else:
            out(f"unknown target {target!r}")
            return 3
    except Exception as e:
        out(f"ERROR {type(e).__name__}: {e}")
        return 3
    if hyg is not None:
        res["hygiene"] = hyg
    p = _save_result(res, Path(out_dir) if out_dir else None)
    out(f"result: {p}")
    return 0 if res.get("pass") else 1


# --------------------------------------------------------------------- vm-run --

RSYNC_EXCLUDES = (".git", ".venv", ".venv-*", "__pycache__", "*.pyc", ".awos", ".claude", "node_modules",
                  ".pytest_cache", ".mypy_cache")


def tart_ip(name: str, runner=subprocess.run) -> str:
    p = runner(["tart", "ip", name], capture_output=True, text=True, timeout=60)
    ip = (p.stdout or "").strip()
    if p.returncode != 0 or not ip:
        raise RuntimeError(f"tart ip {name} failed: {(p.stderr or '').strip() or 'no IP'}")
    return ip


def host_bridge_ip(guest_ip: str) -> str:
    parts = guest_ip.split(".")
    return ".".join(parts[:3] + ["1"]) if len(parts) == 4 else "192.168.64.1"


def vm_commands(ip: str, command: Sequence[str], *, user: str = "admin", guest_dir: str = "AWOS_coding_agent",
                repo: Path = REPO, sync: bool = True, model_url: Optional[str] = None,
                tunnel_port: Optional[int] = None, build_helper: bool = False,
                guest_python: str = "$HOME/AWOS_coding_agent/.venv/bin/python", direct: bool = False,
                pull: Optional[str] = None, ssh_opts: Sequence[str] = ()) -> List[List[str]]:
    """The exact host-side commands (list of argv) vm-run executes, in order."""
    ssh = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes", *ssh_opts]
    dest = f"{user}@{ip}"
    cmds: List[List[str]] = []
    if sync:
        cmds.append(["rsync", "-az", "--delete", *[f"--exclude={e}" for e in RSYNC_EXCLUDES],
                     "-e", " ".join(shlex.quote(x) for x in ssh), f"{repo}/", f"{dest}:{guest_dir}/"])
    if tunnel_port:
        url = model_url or f"http://127.0.0.1:{tunnel_port}/v1"
    else:
        url = model_url or f"http://{host_bridge_ip(ip)}:8080/v1"
    env_text = f"AWOS_LOCAL_BASE_URL={url}\\nAWOS_LIVE_GUEST=1\\n"
    cmds.append(ssh + [dest, f"mkdir -p ~/.awos/helper ~/.awos/live_results && printf '{env_text}' > "
                             f"~/.awos/helper/live.env"])
    if build_helper:
        cmds.append(ssh + [dest, f"cd {guest_dir} && AWOS_HELPER_PYTHON={guest_python} "
                                 f"scripts/macos_helper/build_helper.sh"])
    run = " ".join(shlex.quote(c) for c in command)
    if direct:
        remote = f"cd {guest_dir} && AWOS_LOCAL_BASE_URL={shlex.quote(url)} PYTHONPATH=. {guest_python} -m {run}"
    else:
        remote = f"cd {guest_dir} && scripts/macos_helper/run_helper.sh {run}"
    cmds.append(ssh + ([f"-R{tunnel_port}:127.0.0.1:{tunnel_port}"] if tunnel_port else []) + [dest, remote])
    if pull:
        cmds.append(["rsync", "-az", "-e", " ".join(shlex.quote(x) for x in ssh),
                     f"{dest}:.awos/live_results/", f"{pull}/"])
    return cmds


def vm_run(argv: Sequence[str], *, vm: Optional[str] = None, ip: Optional[str] = None, dry_run: bool = False,
           out: Callable[[str], None] = print, runner=subprocess.run, **kw) -> int:
    if in_guest_vm() and not dry_run:
        out("vm-run is the host-side runner; inside the guest run the command directly")
        return 2
    if not argv:
        out("vm-run needs a command, e.g. -- scaffold.agent.desktop.live demo --target dd")
        return 3
    try:
        ip = ip or tart_ip(vm or os.environ.get("AWOS_VM", "awos-guest"), runner)
    except Exception as e:
        out(str(e))
        return 3
    cmds = vm_commands(ip, argv, **kw)
    rc = 0
    for i, c in enumerate(cmds):
        out("$ " + " ".join(shlex.quote(x) for x in c))
        if dry_run:
            continue
        p = runner(c)
        is_main = c[0] == "ssh" and ("run_helper.sh" in c[-1] or " -m " in c[-1])
        if is_main:
            rc = p.returncode
        elif p.returncode != 0 and c[0] == "rsync" and i == 0:
            out("repo sync failed")
            return 3
    return rc

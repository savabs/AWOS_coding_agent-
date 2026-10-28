"""
python -m scaffold.agent.desktop.capture <command>

  check     [--request-permissions]
            Show whether Accessibility and Input Monitoring are granted (no prompt
            unless --request-permissions).
  record    --task t3_invoices --out trace.json [--run-dir DIR] [--events-out events.jsonl]
            [--yes] [--request-permissions] [--keep-injected]
            Seed the teach_once demo instance (i01), start its local web apps, ask for
            consent, then record you doing the task natively until you type q + Enter.
  record    --from-events events.jsonl --out trace.json [--home DIR | --run-dir DIR]
            Replay a recorded native session through the same adapter (tests, debugging).
            --run-dir seeds a fresh demo instance first (exact for web-only tasks such as
            t3_invoices; file tasks need --home pointing at the live folder).
  validate  trace.json [--secret VALUE ...]
            Validate a trace (schema, steps, secure placeholders, no secret value present).

Terminal controls while recording: p + Enter = pause (sign-in / anything private),
r + Enter = resume, q + Enter (or Ctrl-C) = stop and save.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from .adapter import NativeAdapter  # noqa: E402
from .permissions import CaptureUnavailable, check_permissions, load_frameworks  # noqa: E402

CONSENT_NOTICE = """\
AWOS demonstration recorder — what it captures, while it runs:
  * which of the task's apps is in front, and the web pages of the task's local apps
    (page text and tables as the accessibility tree exposes them);
  * clicks on buttons/links and what you type into web form fields — password and other
    secure fields are NEVER read: the trace stores only the field's name;
  * documents you open and files created/moved/changed under the task folder only.
It does NOT capture: screenshots, keystrokes as characters, other apps, other folders,
anything while paused (p + Enter). Everything stays on this Mac (trace + events files).
"""


def notice_sha() -> str:
    return hashlib.sha256(CONSENT_NOTICE.encode()).hexdigest()


def task_setup(task: str, I, urls: dict):
    """(context, apps, web_with_text) exactly as testbeds/teach_once/demonstrate.py sets up each demo."""
    from testbeds.teach_once.seed import context_for
    ctx = context_for(task, I.inst)
    if task == "t1_statements":
        apps = [{"name": "Finder", "kind": "files"}, {"name": "Preview", "kind": "files"},
                {"name": "Numbers", "kind": "files"}, {"name": "TextEdit", "kind": "files"}]
        return ctx, apps, True
    if task == "t2_orders":
        return ctx, [{"name": "orders", "kind": "web", "base_url": urls["orders"]},
                     {"name": "Numbers", "kind": "files"}, {"name": "TextEdit", "kind": "files"}], False
    return ctx, [{"name": "inbox", "kind": "web", "base_url": urls["inbox"]},
                 {"name": "accounting", "kind": "web", "base_url": urls["accounting"]}], True


class JsonlLog:
    def __init__(self, path: Optional[Path]):
        self.f = open(path, "w", encoding="utf-8") if path else None

    def __call__(self, rec: dict) -> None:
        if self.f:
            self.f.write(json.dumps(rec, sort_keys=True) + "\n")
            self.f.flush()

    def close(self) -> None:
        if self.f:
            self.f.close()


def report(trace, out: Path, secrets: List[str]) -> int:
    from testbeds.teach_once.compiler import compile_trace
    from testbeds.teach_once.trace import validate_trace
    problems = validate_trace(trace, tuple(secrets))
    text = out.read_text(encoding="utf-8")
    leaked = [s for s in secrets if s and s in text]
    print(f"trace: {out}  ({len(trace.steps)} steps)")
    for s in trace.steps:
        tgt = s.target.get("name") or s.target.get("path") or s.target.get("url_path") or s.target.get("to") or ""
        print(f"  {s.i:3d} {s.app:<11} {s.action:<11} {tgt}")
    try:
        skill = compile_trace(trace)
        print(f"compiles: {len(skill.get('parameters', []))} parameters, "
              f"{skill['items']['count_in_demo']} items in the demo")
    except Exception as exc:
        problems.append(f"does not compile: {exc}")
    if leaked:
        problems.append("secret value found in the saved file")
    for p in problems:
        print("PROBLEM:", p)
    print("valid" if not problems else "INVALID")
    return 0 if not problems else 1


def cmd_check(a) -> int:
    try:
        st = check_permissions(load_frameworks(("ApplicationServices", "Quartz")), request=a.request_permissions)
    except CaptureUnavailable as exc:
        print(exc, file=sys.stderr)
        return 2
    print(st.describe())
    return 0 if st.ok else 3


def _stdin_commands(rec, stop: threading.Event) -> None:
    for line in sys.stdin:
        c = line.strip().lower()[:1]
        if c == "p":
            rec.commands.put("pause")
            print("[paused — nothing is recorded; r + Enter to resume]", flush=True)
        elif c == "r":
            rec.commands.put("resume")
            print("[recording]", flush=True)
        elif c == "q":
            break
    stop.set()


def cmd_record_live(a) -> int:
    from testbeds.teach_once import DEMO_INSTANCE
    from testbeds.teach_once.seed import INSTRUCTIONS, NOTES, PORTAL_PASSWORD, seed_instance
    from testbeds.teach_once.trace import Recorder
    from testbeds.teach_once.webapps import Servers

    from .live import MacRecorder
    from .permissions import require_permissions
    try:                                     # fail fast, before seeding anything
        fw = load_frameworks()
        require_permissions(fw, request=a.request_permissions)
    except CaptureUnavailable as exc:
        print(exc, file=sys.stderr)
        return 2
    run_dir = Path(a.run_dir or ROOT / "runs" / "capture" / time.strftime("%Y%m%d_%H%M%S")).resolve()
    I = seed_instance(run_dir / "demo_scratch", a.task, DEMO_INSTANCE)
    servers = Servers(I)
    urls = servers.__enter__()
    log = JsonlLog(Path(a.events_out) if a.events_out else Path(a.out).with_suffix(".events.jsonl"))
    try:
        ctx, apps, with_text = task_setup(a.task, I, urls)
        print(CONSENT_NOTICE)
        if not a.yes:
            if input("Type yes to start recording: ").strip().lower() != "yes":
                print("not recording (no consent)")
                return 1
        rec = Recorder(I.home, task=a.task, instance=I.inst, context=ctx, title=INSTRUCTIONS[a.task][:80],
                       note=NOTES[a.task], kind="macos_ax_fsevents", apps=apps)
        adapter = NativeAdapter(rec.feed, home=I.home, apps=apps, log=log, keep_injected=a.keep_injected)
        adapter.feed({"src": "session", "event": "start", "t": time.time(), "task": a.task, "instance": I.inst,
                      "context": ctx, "apps": apps, "home": str(I.home), "watch": [str(I.home)],
                      "consent": {"granted": True, "notice_sha256": notice_sha(), "at": time.time(),
                                  "via": "--yes" if a.yes else "typed yes"}})
        print(f"\nTASK: {INSTRUCTIONS[a.task]}\n")
        print(f"Task folder (open it in Finder): {I.home}")
        for name, url in urls.items():
            print(f"Web app {name}: {url}   (open in Safari)")
        print("\nRecording. p + Enter = pause, r + Enter = resume, q + Enter = stop and save.\n", flush=True)
        live = MacRecorder(adapter.feed, watch=[str(I.home)], frameworks=fw)
        stop = threading.Event()
        threading.Thread(target=_stdin_commands, args=(live, stop), daemon=True).start()
        try:
            live.run(stop)
        except KeyboardInterrupt:
            pass
        adapter.feed({"src": "session", "event": "stop", "t": time.time()})
        trace = adapter.finish(rec, web_with_text=with_text)
        out = Path(a.out)
        trace.save(out)
        if live.errors:
            print(f"{len(live.errors)} capture errors (first: {live.errors[0]})")
        print(f"events: {log.f.name if log.f else '-'}")
        return report(trace, out, [PORTAL_PASSWORD] + list(a.secret or []))
    finally:
        log.close()
        servers.__exit__(None, None, None)


def cmd_record_replay(a) -> int:
    from testbeds.teach_once import DEMO_INSTANCE
    from testbeds.teach_once.seed import INSTRUCTIONS, NOTES, PORTAL_PASSWORD, seed_instance
    from testbeds.teach_once.trace import Recorder

    lines = [json.loads(x) for x in Path(a.from_events).read_text(encoding="utf-8").splitlines() if x.strip()]
    start = next((r for r in lines if r.get("src") == "session" and r.get("event") == "start"), None)
    if start is None:
        print("events file has no session start record (consent)", file=sys.stderr)
        return 2
    task = a.task or start.get("task")
    if a.home:
        home = Path(a.home)
    elif a.run_dir:
        home = seed_instance(Path(a.run_dir) / "demo_scratch", task, start.get("instance") or DEMO_INSTANCE).home
    else:
        home = Path(start["home"])
    apps = start.get("apps", [])
    rec = Recorder(home, task=task, instance=start.get("instance") or DEMO_INSTANCE, context=start.get("context", {}),
                   title=INSTRUCTIONS.get(task, "")[:80], note=NOTES.get(task, ""), kind="macos_ax_fsevents",
                   apps=apps)
    adapter = NativeAdapter(rec.feed, home=home, apps=apps, keep_injected=a.keep_injected)
    for r in lines:
        adapter.feed(r)
    trace = adapter.finish(rec, web_with_text=task != "t2_orders")
    out = Path(a.out)
    trace.save(out)
    return report(trace, out, [PORTAL_PASSWORD] + list(a.secret or []))


def cmd_validate(a) -> int:
    from testbeds.teach_once.seed import PORTAL_PASSWORD
    from testbeds.teach_once.trace import Trace
    out = Path(a.trace)
    return report(Trace.load(out), out, [PORTAL_PASSWORD] + list(a.secret or []))


def main(argv: Optional[List[str]] = None) -> int:
    from testbeds.teach_once import TASKS
    ap = argparse.ArgumentParser(prog="python -m scaffold.agent.desktop.capture",
                                 description="AWOS native macOS demonstration recorder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--request-permissions", action="store_true")
    r = sub.add_parser("record")
    r.add_argument("--out", required=True)
    r.add_argument("--task", choices=TASKS)
    r.add_argument("--run-dir")
    r.add_argument("--home")
    r.add_argument("--from-events")
    r.add_argument("--events-out")
    r.add_argument("--yes", action="store_true", help="consent without the interactive prompt")
    r.add_argument("--request-permissions", action="store_true")
    r.add_argument("--keep-injected", action="store_true",
                   help="keep synthetic input (if your hardware clicks are wrongly flagged as injected)")
    r.add_argument("--secret", action="append", help="a value that must not appear in the trace")
    v = sub.add_parser("validate")
    v.add_argument("trace")
    v.add_argument("--secret", action="append")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        return cmd_check(a)
    if a.cmd == "validate":
        return cmd_validate(a)
    if a.from_events:
        return cmd_record_replay(a)
    if not a.task:
        ap.error("record needs --task (live) or --from-events (replay)")
    return cmd_record_live(a)


if __name__ == "__main__":
    sys.exit(main())

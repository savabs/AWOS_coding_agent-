"""Inventory MacArena tasks (MIT, AWOS code) and classify feasibility on a stock GitHub macOS runner.

    python -I scripts/macarena/ma_inventory.py <MacArena>/evaluation_examples [--json out.json] [--md]

Task files are DATA: they are parsed as JSON and pattern-matched, never executed here.
Families:
  macarena    (MIT, MacPaw)            evaluator = [[bash, score], ...]
  macosworld  (CC BY-NC 4.0, showlab)  evaluator = [[bash, score], ...]
  osworld     (Apache-2.0, XLANG)      evaluator = OSWorld getters/metrics dict (needs the
                                       desktop_env controller + LibreOffice/GIMP/...; not piloted)
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

# Apps that ship with macOS 15 (present on GitHub-hosted macos-15 runners).
BUILTIN_APPS = {
    "Finder", "Safari", "TextEdit", "Notes", "Calendar", "Reminders", "Contacts", "Preview",
    "System Settings", "System Preferences", "System Events", "Automator", "Shortcuts", "Terminal",
    "Calculator", "Music", "Photos", "Maps", "Mail", "Messages", "FaceTime", "Voice Memos", "Stocks",
    "Home", "Font Book", "Script Editor", "Activity Monitor", "Disk Utility", "Console", "Freeform",
    "Weather", "Books", "Podcasts", "TV", "News", "Chess", "Stickies", "Image Capture", "Screenshot",
    "Dictionary", "QuickTime Player", "Photo Booth", "Clock", "App Store", "Keychain Access",
    "Mission Control", "Launchpad", "Siri", "Time Machine", "Migration Assistant", "Grapher",
    "Digital Color Meter", "Bluetooth File Exchange", "ColorSync Utility", "Audio MIDI Setup",
    "Directory Utility", "Screen Sharing", "Passwords", "iPhone Mirroring", "Tips", "Find My",
    "Photo Booth", "Archive Utility", "Dock", "SystemUIServer", "Control Center", "Spotlight",
    "Ticket Viewer", "VoiceOver Utility", "Boot Camp Assistant", "AirPort Utility", "System Information",
}
# Third-party apps the GitHub macOS runner images install (verified again at runtime by the runner).
RUNNER_THIRD_PARTY = {"Google Chrome", "Firefox", "Microsoft Edge", "Xcode", "Visual Studio Code"}
# Known absent on the runner images.
ABSENT = {"Pages", "Numbers", "Keynote", "GarageBand", "iMovie", "LibreOffice", "Obsidian", "Keka", "OBS",
          "Anaconda-Navigator", "VLC", "GIMP", "Thunderbird", "Microsoft Word", "Microsoft Excel",
          "Microsoft PowerPoint", "Telegram", "Slack", "Spotify", "Zoom", "Discord", "Notion"}
APP_ALIASES = {"System Preferences": "System Settings", "Visual Studio Code": "Visual Studio Code",
               "Code": "Visual Studio Code", "VS Code": "Visual Studio Code", "Chrome": "Google Chrome",
               "QuickTime": "QuickTime Player", "Libreoffice": "LibreOffice", "soffice": "LibreOffice",
               "OBS Studio": "OBS", "Anaconda Navigator": "Anaconda-Navigator"}
NOT_APPS = {"System Events", "Dock", "SystemUIServer", "Control Center", "Spotlight", "Finder"}

RE_TELL = re.compile(r'(?:tell|of|to)\s+application\s+(?:process\s+)?\\?"([^"\\]+)\\?"')
RE_PROC = re.compile(r'(?:application\s+)?process\s+\\?"([^"\\]+)\\?"')
RE_OPEN_A = re.compile(r'open\s+(?:-\w\s+)*-a\s+(?:\\?"([^"\\]+)\\?"|\'([^\']+)\'|([\w.-]+))')
RE_APPDIR = re.compile(r'/(?:System/)?Applications/(?:Utilities/)?([^/"\']+?)\.app')
RE_NET = re.compile(r'https?://|\bcurl\b|\bwget\b|\bnetworksetup\b', re.I)
RE_ADMIN = re.compile(r'/Users/admin\b')
RE_FIXTURE = re.compile(r'Benchmark_Backup|benchmark_files|/Users/admin/(?:Desktop|Documents|Downloads)/\S+\.\w+')
RE_SUDO = re.compile(r'\bsudo\b')
RE_APPLEEVENT_DATA = re.compile(r'tell application \\?"(Calendar|Contacts|Reminders|Notes|Mail|Photos|Messages)\\?"')


def norm_app(a: str) -> str:
    a = a.strip().strip("'\"")
    return APP_ALIASES.get(a, a)


def load_tasks(root: Path) -> Iterable[dict]:
    for p in sorted(root.rglob("*.json")):
        rel = p.relative_to(root).parts
        if len(rel) < 3:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or "instruction" not in d or "evaluator" not in d:
            continue
        d["_family"], d["_category"], d["_path"] = rel[0], rel[1], str(p.relative_to(root))
        yield d


def commands_of(t: dict) -> List[str]:
    out = [t.get("pre_command") or ""]
    ev = t.get("evaluator")
    if isinstance(ev, list):
        out += [str(e[0]) for e in ev if isinstance(e, (list, tuple)) and e]
    else:
        out.append(json.dumps(ev))
        out.append(json.dumps(t.get("config", [])))
    return [c for c in out if c]


def apps_of(t: dict) -> Set[str]:
    apps = {norm_app(a) for a in t.get("related_apps", []) or []}
    for c in commands_of(t):
        for m in RE_TELL.finditer(c):
            apps.add(norm_app(m.group(1)))
        for m in RE_PROC.finditer(c):
            apps.add(norm_app(m.group(1)))
        for m in RE_OPEN_A.finditer(c):
            apps.add(norm_app(next(g for g in m.groups() if g)))
        for m in RE_APPDIR.finditer(c):
            apps.add(norm_app(m.group(1)))
    # osworld related_apps are lowercase tags, map the obvious ones
    tagmap = {"chrome": "Google Chrome", "libreoffice_calc": "LibreOffice", "libreoffice calc": "LibreOffice",
              "libreoffice_writer": "LibreOffice", "libreoffice_impress": "LibreOffice", "calc": "LibreOffice",
              "writer": "LibreOffice", "gimp": "GIMP", "vscode": "Visual Studio Code", "vs_code": "Visual Studio Code",
              "thunderbird": "Thunderbird", "vlc": "VLC", "os": "Terminal", "OS": "Terminal", "terminal": "Terminal",
              "pdf": "Preview", "image": "Preview", "browser": "Google Chrome", "Chrome": "Google Chrome",
              "picard": "MusicBrainz Picard"}
    return {tagmap.get(a, a) for a in apps if a}


def classify(t: dict) -> dict:
    fam = t["_family"]
    cmds = "\n".join(commands_of(t))
    apps = apps_of(t)
    real_apps = sorted(a for a in apps if a not in NOT_APPS or a == "Finder")
    missing = sorted(a for a in real_apps if a in ABSENT
                     or (a not in BUILTIN_APPS and a not in RUNNER_THIRD_PARTY))
    flags = {
        "network": bool(RE_NET.search(cmds)) or bool(re.search(r'https?://', t.get("instruction", ""))),
        "admin_home": bool(RE_ADMIN.search(cmds)),
        "fixture": bool(RE_FIXTURE.search(cmds)) or bool(t.get("pre_upload_files")),
        "sudo": bool(RE_SUDO.search(cmds)),
        "appleevents_private_data": bool(RE_APPLEEVENT_DATA.search(cmds)),
        "snapshot_recovery": bool(t.get("force_snapshot_recovery")),
        "ec2": bool(t.get("force_ec2")),
        "list_evaluator": isinstance(t.get("evaluator"), list),
    }
    reasons = []
    if not flags["list_evaluator"]:
        reasons.append("osworld_evaluator")
    if missing:
        reasons.append("app_absent:" + ",".join(missing))
    if flags["fixture"]:
        reasons.append("needs_vm_fixture_files")
    if flags["ec2"]:
        reasons.append("ec2_only")
    if reasons:
        feas = "infeasible"
    elif flags["appleevents_private_data"] or flags["sudo"] or flags["network"]:
        feas = "maybe"                       # TCC Automation/data prompts, sudo, or network
    else:
        feas = "feasible"
    return {"id": t["id"], "family": fam, "category": t["_category"], "path": t["_path"],
            "apps": real_apps, "missing_apps": missing, "flags": flags, "feasibility": feas,
            "reasons": reasons, "instruction": t.get("instruction", "")[:160]}


def summarize(rows: List[dict]) -> Dict[str, object]:
    by_fam = collections.Counter((r["family"], r["feasibility"]) for r in rows)
    by_app = collections.Counter()
    for r in rows:
        for a in r["apps"] or ["(none)"]:
            by_app[(a, r["feasibility"])] += 1
    reasons = collections.Counter(x.split(":")[0] for r in rows for x in r["reasons"])
    missing = collections.Counter(a for r in rows for a in r["missing_apps"])
    flags = collections.Counter(k for r in rows for k, v in r["flags"].items() if v)
    return {"total": len(rows), "by_family_feasibility": {f"{a}/{b}": n for (a, b), n in sorted(by_fam.items())},
            "by_app": {f"{a}/{b}": n for (a, b), n in sorted(by_app.items(), key=lambda x: -x[1])},
            "reasons": dict(reasons), "missing_apps": dict(missing.most_common()), "flags": dict(flags)}


def to_md(rows: List[dict], s: dict) -> str:
    out = [f"# MacArena inventory ({s['total']} tasks)", "", "| family | feasible | maybe | infeasible |",
           "|---|---|---|---|"]
    fams = sorted({r["family"] for r in rows})
    for f in fams:
        c = collections.Counter(r["feasibility"] for r in rows if r["family"] == f)
        out.append(f"| {f} | {c['feasible']} | {c['maybe']} | {c['infeasible']} |")
    out += ["", "Missing apps: " + ", ".join(f"{a} ({n})" for a, n in s["missing_apps"].items()), "",
            "Reasons: " + ", ".join(f"{a} ({n})" for a, n in s["reasons"].items()), ""]
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, help="<MacArena>/evaluation_examples")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--list", choices=("feasible", "maybe", "infeasible", "all"))
    a = ap.parse_args(argv)
    rows = [classify(t) for t in load_tasks(a.root)]
    s = summarize(rows)
    if a.json:
        a.json.write_text(json.dumps({"summary": s, "tasks": rows}, indent=1, ensure_ascii=False))
    if a.md:
        print(to_md(rows, s))
    if a.list:
        for r in rows:
            if a.list == "all" or r["feasibility"] == a.list:
                print(f"{r['family']:<10} {r['category']:<18} {r['id'][:8]} {r['feasibility']:<10} "
                      f"{','.join(r['apps'])[:40]:<40} {';'.join(r['reasons'])[:50]:<50} "
                      f"{''.join(k[0] for k, v in r['flags'].items() if v and k != 'list_evaluator')} "
                      f"| {r['instruction'][:90]}")
    if not (a.json or a.md or a.list):
        print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

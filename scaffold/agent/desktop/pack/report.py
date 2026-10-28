"""report.py — the month's completeness report and the accountant's zip.

    rep = build(cfg, "2026-10")             # dict (also written as report.json)
    paths = write_report(cfg, "2026-10")    # report.html (offline, no scripts), report.txt, report.json
    z = make_zip(cfg, "2026-10")            # Pack/2026-10_document_pack.zip

The report is a grid: one row per source, its status, the files it holds, why it is not
complete and "what you need to do". Totals on top.

The zip holds the report plus every filed document EXCEPT those set aside: verdict
SUSPICIOUS or MISMATCH, wrong file type, or bytes that no longer match the ledger's
sha256. It never holds the run state, the ledger, lock files, or password hints; locked
PDFs are only marked "password-protected -- the password is not in this pack".
"""
from __future__ import annotations

import html
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import PackConfig, Source
from .files import sha256_file
from .run import (COLLECTED_UNVERIFIED, COLLECTED_VERIFIED, FAILED, MISSING, NEEDS_YOU, NOT_DUE, RUNNING,
                  STATUSES, is_due, load_state, parse_month, scrub)

NOT_RUN = "not_run"
LABELS = {COLLECTED_VERIFIED: "Collected, checked", COLLECTED_UNVERIFIED: "Collected, not checked",
          MISSING: "Missing", NEEDS_YOU: "Needs you", FAILED: "Failed", NOT_DUE: "Not due",
          NOT_RUN: "Not run yet", RUNNING: "Interrupted"}
ORDER = [NEEDS_YOU, FAILED, MISSING, RUNNING, NOT_RUN, COLLECTED_UNVERIFIED, COLLECTED_VERIFIED, NOT_DUE]
REPORT_FILES = ("report.html", "report.txt", "report.json")


def zip_name(month: str) -> str:
    return f"{month}_document_pack.zip"


def _rerun(month: str, sid: str) -> str:
    return f"python -m scaffold.agent.desktop.pack run --month {month} --source {sid}"


def todo(entry: Dict[str, Any], source: Source, month: str) -> str:
    st = entry.get("status") or NOT_RUN
    where = f"{source.name} ({source.start_url})"
    drop = f"or download it yourself and drop it into Pack/{month}/{source.id}/"
    if st == NEEDS_YOU:
        kind = (entry.get("checkpoint") or {}).get("kind", "unknown")
        do = {"login": f"Log in to {where} in the sandbox browser",
              "otp": f"Open {where} in the sandbox browser and enter the OTP sent to your phone/email",
              "2fa_app": f"Approve the sign-in for {source.name} in your authenticator app",
              "captcha": f"Solve the captcha on {where} yourself (AWOS never solves captchas)",
              "consent": f"Accept the notice on {where} yourself"}.get(kind, f"Look at {where} in the sandbox browser")
        return f"{do}, then re-run: {_rerun(month, source.id)}"
    if st == FAILED:
        teach = f"; if it fails again, re-teach the skill {source.skill!r}" if source.skill else ""
        return f"Re-run: {_rerun(month, source.id)}{teach}; {drop}"
    if st == MISSING:
        if not source.skill:
            return f"Teach AWOS this portal once, {drop}"
        return f"Check that {source.name} has published it, re-run: {_rerun(month, source.id)}; {drop}"
    if st == RUNNING:
        return f"The last run was interrupted; re-run: {_rerun(month, source.id)}"
    if st == NOT_RUN:
        return f"Run it: {_rerun(month, source.id)}"
    if st == COLLECTED_UNVERIFIED:
        return "Glance at the file(s): the automatic check could not confirm period/amounts"
    return ""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def build(cfg: PackConfig, month: str, st: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    month = parse_month(month)
    st = st if st is not None else load_state(cfg.root, month)
    rows: List[Dict[str, Any]] = []
    known = {s.id: s for s in cfg.sources}
    for s in cfg.sources:
        if not s.enabled and s.id not in st.get("sources", {}):
            continue
        e = dict(st.get("sources", {}).get(s.id) or {})
        if not e.get("status"):
            e["status"] = NOT_RUN if is_due(s, month) else NOT_DUE
        files = []
        for f in e.get("files", []):
            v = f.get("verdict") or {}
            set_aside = bool(f.get("flagged")) or not f.get("type_ok", True)
            files.append({"name": Path(f["path"]).name, "path": f["path"], "verdict": v.get("status", "UNVERIFIED"),
                          "reasons": [scrub(r, 200) for r in v.get("reasons", [])], "set_aside": set_aside,
                          "event": f.get("event", "filed")})
        rows.append({"id": s.id, "name": s.name, "kind": s.kind, "cadence": s.cadence,
                     "expected": s.expected.count, "status": e["status"], "label": LABELS.get(e["status"], e["status"]),
                     "reason": scrub(e.get("reason", ""), 400), "todo": todo(e, s, month), "files": files,
                     "locked_pdf": bool(s.password_hint), "attempts": e.get("attempts", 0)})
    for sid, e in st.get("sources", {}).items():         # sources removed from the config since
        if sid not in known:
            rows.append({"id": sid, "name": sid, "kind": "?", "cadence": "?", "expected": 0,
                         "status": e.get("status", MISSING), "label": LABELS.get(e.get("status"), "?"),
                         "reason": "no longer in sources.toml", "todo": "", "files": [], "locked_pdf": False,
                         "attempts": e.get("attempts", 0)})
    rows.sort(key=lambda r: (ORDER.index(r["status"]) if r["status"] in ORDER else 99, r["id"]))
    tot = {k: 0 for k in list(STATUSES) + [NOT_RUN]}
    for r in rows:
        tot[r["status"]] = tot.get(r["status"], 0) + 1
    due = [r for r in rows if r["status"] != NOT_DUE]
    done = [r for r in due if r["status"] in (COLLECTED_VERIFIED, COLLECTED_UNVERIFIED)]
    flagged = [{"source": r["id"], **f} for r in rows for f in r["files"] if f["set_aside"]]
    return {"month": month, "generated": _now(), "sources": rows, "totals": tot,
            "due": len(due), "collected": len(done), "complete": len(due) > 0 and len(done) == len(due),
            "set_aside": flagged}


# ── rendering ───────────────────────────────────────────────────────────────
def render_text(rep: Dict[str, Any]) -> str:
    L = [f"Month-end document pack {rep['month']}  (generated {rep['generated']})",
         f"{rep['collected']} of {rep['due']} due sources collected" + ("  -- COMPLETE" if rep["complete"] else ""),
         "  " + ", ".join(f"{LABELS.get(k, k)}: {v}" for k, v in rep["totals"].items() if v), ""]
    for r in rep["sources"]:
        L.append(f"[{r['label']}] {r['name']} ({r['id']}, {r['kind']}, {r['cadence']})")
        if r["reason"]:
            L.append(f"    why: {r['reason']}")
        for f in r["files"]:
            tag = "SET ASIDE, not in zip" if f["set_aside"] else f["verdict"].lower()
            L.append(f"    - {f['name']}  [{tag}]" + (f"  {'; '.join(f['reasons'])}" if f["reasons"] else ""))
        if r["locked_pdf"] and r["files"]:
            L.append("    note: password-protected; the password is not in this pack")
        if r["todo"]:
            L.append(f"    to do: {r['todo']}")
    if rep["set_aside"]:
        L += ["", "Set aside (not in the zip -- please check by hand):"]
        L += [f"  - {f['path']}: {f['verdict']} {'; '.join(f['reasons'])}" for f in rep["set_aside"]]
    return "\n".join(L) + "\n"


_CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#d2d2d7;--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e;--you:#0550ae}
@media (prefers-color-scheme: dark){:root{--bg:#1c1c1e;--fg:#f2f2f7;--mut:#a1a1a6;--line:#3a3a3c;
--ok:#4ac26b;--warn:#d4a72c;--bad:#ff7b72;--you:#79c0ff}}
body{background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,system-ui,sans-serif;margin:0;padding:16px}
main{max-width:980px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}.mut{color:var(--mut)}
table{border-collapse:collapse;width:100%;margin-top:16px}th,td{text-align:left;vertical-align:top;
padding:8px 6px;border-bottom:1px solid var(--line)}th{font-size:13px;color:var(--mut);font-weight:600}
.s{font-weight:600;white-space:nowrap}.collected_verified{color:var(--ok)}.collected_unverified{color:var(--warn)}
.missing,.failed,.running,.not_run{color:var(--bad)}.needs_you{color:var(--you)}.not_due{color:var(--mut)}
ul{margin:0;padding-left:18px}code{font-size:12px;word-break:break-all}.todo{font-size:14px}
.wrap{overflow-x:auto}
"""


def render_html(rep: Dict[str, Any]) -> str:
    e = html.escape
    head = (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            f"<title>Document pack {e(rep['month'])}</title><style>{_CSS}</style></head><body><main>")
    tot = ", ".join(f"{e(LABELS.get(k, k))}: {v}" for k, v in rep["totals"].items() if v)
    out = [head, f"<h1>Document pack {e(rep['month'])}</h1>",
           f"<p><b>{rep['collected']} of {rep['due']}</b> due sources collected"
           f"{' &mdash; complete' if rep['complete'] else ''}. <span class=\"mut\">{e(tot)}. "
           f"Generated {e(rep['generated'])}.</span></p>",
           "<div class=\"wrap\"><table><thead><tr><th>Source</th><th>Status</th><th>Files</th>"
           "<th>What you need to do</th></tr></thead><tbody>"]
    for r in rep["sources"]:
        files = "".join(
            f"<li>{e(f['name'])} <span class=\"mut\">"
            f"({'set aside, not in zip' if f['set_aside'] else e(f['verdict'].lower())})"
            f"{': ' + e('; '.join(f['reasons'])) if f['reasons'] else ''}</span></li>" for f in r["files"])
        if r["locked_pdf"] and r["files"]:
            files += "<li class=\"mut\">password-protected; the password is not in this pack</li>"
        out.append(f"<tr><td><b>{e(r['name'])}</b><br><span class=\"mut\">{e(r['id'])} &middot; {e(r['kind'])}"
                   f" &middot; {e(r['cadence'])}</span></td>"
                   f"<td><span class=\"s {e(r['status'])}\">{e(r['label'])}</span>"
                   f"{'<br><span class=mut>' + e(r['reason']) + '</span>' if r['reason'] else ''}</td>"
                   f"<td>{'<ul>' + files + '</ul>' if files else '<span class=mut>none</span>'}</td>"
                   f"<td class=\"todo\">{e(r['todo'])}</td></tr>")
    out.append("</tbody></table></div>")
    if rep["set_aside"]:
        out.append("<h2>Set aside (not in the zip)</h2><ul>" + "".join(
            f"<li><code>{e(f['path'])}</code> {e(f['verdict'])} {e('; '.join(f['reasons']))}</li>"
            for f in rep["set_aside"]) + "</ul>")
    out.append("</main></body></html>\n")
    return "".join(out)


def write_report(cfg: PackConfig, month: str, st: Optional[Dict[str, Any]] = None) -> Dict[str, Path]:
    rep = build(cfg, month, st)
    d = Path(cfg.root) / rep["month"]
    d.mkdir(parents=True, exist_ok=True)
    paths = {"html": d / "report.html", "txt": d / "report.txt", "json": d / "report.json"}
    paths["html"].write_text(render_html(rep), encoding="utf-8")
    paths["txt"].write_text(render_text(rep), encoding="utf-8")
    paths["json"].write_text(json.dumps(rep, indent=1, sort_keys=True), encoding="utf-8")
    return paths


def make_zip(cfg: PackConfig, month: str, out: Optional[Path] = None) -> Dict[str, Any]:
    """Write the accountant's zip. Returns {path, included: [...], excluded: [{path, why}]}."""
    month = parse_month(month)
    st = load_state(cfg.root, month)
    root = Path(cfg.root)
    excluded: List[Dict[str, str]] = []
    included: List[str] = []
    for sid, e in sorted(st.get("sources", {}).items()):
        for f in e.get("files", []):
            p = root / f["path"]
            v = (f.get("verdict") or {}).get("status", "UNVERIFIED")
            if f.get("flagged"):
                excluded.append({"path": f["path"], "why": f"verdict {v}"})
            elif not f.get("type_ok", True):
                excluded.append({"path": f["path"], "why": "unexpected file type"})
            elif not p.is_file():
                excluded.append({"path": f["path"], "why": "file no longer in the pack folder"})
            elif sha256_file(p) != f.get("sha256"):
                excluded.append({"path": f["path"], "why": "file changed since it was collected"})
            else:
                included.append(f["path"])
    rp = write_report(cfg, month, st)
    zpath = Path(out) if out else root / zip_name(month)
    tmp = zpath.with_name(zpath.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in REPORT_FILES:
            z.write(rp[name.split(".")[1]], f"{month}/{name}")
        for rel in included:
            z.write(root / rel, rel)
    tmp.replace(zpath)
    return {"path": str(zpath), "included": included, "excluded": excluded}

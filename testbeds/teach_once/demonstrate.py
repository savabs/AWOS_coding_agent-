"""
demonstrate.py — scripted "human demonstrations", one per task, on instance i01.

Since no real OS input can be captured here, a script plays the human: it
does the task the way a person would (open the file, look at it, drag it to
a folder, type into the form, fix the validation error ...) through
browser.Browser and FileActor. Both emit the same low-level events a real
macOS recorder would (trace.py), and trace.Recorder turns them into the
demonstration trace. Nothing here reads the hidden ground truth: the
"human" knows the demo instance the way its owner would (the seed specs).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import DEMO_INSTANCE
from .browser import Browser
from .common import append_csv_row, csv_dicts, file_text, slug, week_bounds
from .seed import (INSTRUCTIONS, NOTES, context_for, PORTAL_PASSWORD, PORTAL_USER, T1_SPECS, T2_SPECS, T3_SPECS, Instance,
                   seed_instance, t1_target, t2_expected)
from .trace import Recorder, Trace, inode
from .webapps import Servers


class FileActor:
    """Finder / Preview / Numbers / TextEdit, scripted. Emits FSEvents-style events."""

    VIEWERS = {".pdf": "Preview", ".csv": "Numbers", ".txt": "TextEdit"}

    def __init__(self, home: Path, sink: Optional[Callable[[dict], None]] = None):
        self.home, self.sink, self.app, self.actions = Path(home), sink, None, 0

    def _emit(self, ev: dict) -> None:
        if self.sink:
            self.sink(ev)

    def activate(self, app: str) -> None:
        if app != self.app:
            self.app = app
            self._emit({"type": "app", "event": "activated", "app": app, "kind": "files"})

    def view(self, rel: str) -> str:
        p = self.home / rel
        self.activate(self.VIEWERS.get(p.suffix.lower(), "TextEdit"))
        self.actions += 1
        self._emit({"type": "ax", "app": self.app, "notification": "AXFocusedWindowChanged",
                    "window": {"document": str(p), "title": p.name}})
        return file_text(p)

    def mkdir(self, rel: str) -> None:
        self.activate("Finder")
        p = self.home / rel
        missing = []
        while not p.exists():
            missing.append(p)
            p = p.parent
        for d in reversed(missing):
            d.mkdir()
            self.actions += 1
            self._emit({"type": "fs", "path": str(d), "flags": ["ItemCreated", "ItemIsDir"], "by_app": "Finder"})

    def move(self, src: str, dst: str) -> None:
        self.activate("Finder")
        s, d = self.home / src, self.home / dst
        if not s.exists():
            raise FileNotFoundError(src)
        if d.exists():
            raise FileExistsError(dst)
        self.mkdir(str(Path(dst).parent))
        ino = inode(s)
        os.rename(s, d)
        self.actions += 1
        self._emit({"type": "fs", "path": str(s), "flags": ["ItemRenamed", "ItemIsFile"], "inode": ino, "by_app": "Finder"})
        self._emit({"type": "fs", "path": str(d), "flags": ["ItemRenamed", "ItemIsFile"], "inode": ino, "by_app": "Finder"})

    def append_rows(self, rel: str, rows: List[List[str]], app: str = "Numbers") -> None:
        self.activate(app)
        for r in rows:
            append_csv_row(self.home / rel, r)
        self.actions += 1
        self._emit({"type": "fs", "path": str(self.home / rel), "flags": ["ItemModified", "ItemIsFile"], "by_app": app})

    def write(self, rel: str, text: str, app: str = "TextEdit") -> None:
        self.activate(app)
        p = self.home / rel
        new = not p.exists()
        if not new:
            raise FileExistsError(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        self.actions += 1
        self._emit({"type": "fs", "path": str(p), "flags": ["ItemCreated", "ItemIsFile"], "by_app": app})


def _recorder(I: Instance, context: dict, apps: List[dict]) -> Recorder:
    return Recorder(I.home, task=I.task, instance=I.inst, context=context, title=INSTRUCTIONS[I.task][:80],
                    note=NOTES[I.task], apps=apps)


# ===================================================================== T1 ==

def demo_t1(I: Instance) -> Trace:
    rec = _recorder(I, context_for(I.task, I.inst), [{"name": "Finder", "kind": "files"},
                                                  {"name": "Preview", "kind": "files"},
                                                  {"name": "Numbers", "kind": "files"}])
    fa = FileActor(I.home, rec.feed)
    fa.activate("Finder")
    for kind, name, bank, last4, date, bal, _fmt in T1_SPECS[I.inst]["stmts"]:
        fa.view(f"Downloads/{name}")                      # the human looks at the statement
        rel = t1_target(date, bank, last4, kind)
        fa.move(f"Downloads/{name}", f"Documents/Statements/{rel}")
        fa.append_rows("Documents/Statements/ledger.csv", [[date.isoformat(), slug(bank), last4, bal, rel]])
    return rec.finish()


# ===================================================================== T2 ==

def demo_t2(I: Instance, urls: Dict[str, str]) -> Trace:
    today = T2_SPECS[I.inst]["today"]
    ws, we = week_bounds(today)
    rec = _recorder(I, {"today": today.isoformat()}, [{"name": "orders", "kind": "web", "base_url": urls["orders"]},
                                                      {"name": "Numbers", "kind": "files"},
                                                      {"name": "TextEdit", "kind": "files"}])
    b = Browser(urls, I.home / "Downloads", rec.feed)
    fa = FileActor(I.home, rec.feed)
    b.open("orders", "/")
    b.type("Username", PORTAL_USER)
    b.type("Password", PORTAL_PASSWORD)
    b.click("button", "Sign in")
    b.type("From date", ws.isoformat())
    b.type("To date", we.isoformat())
    b.click("button", "Apply filter")
    path = b.click("link", "Export CSV")
    rel = Path(path).relative_to(I.home).as_posix()
    text = fa.view(rel)
    orders = [{"date": r["date"], "customer": r["customer"], "status": r["status"], "amount": r["amount"]}
              for r in csv_dicts(text)]
    exp = t2_expected(orders, ws, we)
    fa.append_rows("Documents/Reports/weekly_summary.csv", exp["rows"])
    fa.write(f"Documents/Reports/week_{ws.isoformat()}.txt",
             f"Week: {ws.isoformat()} to {we.isoformat()}\nOrders: {exp['orders']}\nRevenue: {exp['revenue']}\n"
             f"Top customer: {exp['top_customer']} ({exp['top_total']})\n")
    return rec.finish({"orders": b.pages["orders"].summary()})


# ===================================================================== T3 ==

def demo_t3(I: Instance, urls: Dict[str, str]) -> Trace:
    rec = _recorder(I, context_for(I.task, I.inst), [{"name": "inbox", "kind": "web", "base_url": urls["inbox"]},
                                                 {"name": "accounting", "kind": "web", "base_url": urls["accounting"]}])
    b = Browser(urls, I.home / "Downloads", rec.feed)
    b.open("accounting", "/bills")                         # what's already entered
    b.open("inbox", "/")
    known = {m[1]: m for m in T3_SPECS[I.inst]["msgs"]}
    subjects = [row[1] for row in b.page.table("Inbox")["rows"]]
    for subj in subjects:
        b.switch("inbox")
        if b.page.path != "/":
            b.click("link", "Back to inbox")
        b.click("link", subj)
        number = subj.split()[-1]
        vendor, num, date, raw_date, raw_amt, _tpl, entered = known[number]
        b.switch("accounting")                             # look it up in Bills
        if entered:
            continue
        b.click("link", "New bill")
        b.type("Vendor", vendor)
        b.type("Invoice number", num)
        b.type("Invoice date", raw_date)                   # typed as seen in the e-mail
        b.type("Amount", raw_amt.replace("$", "").replace(",", ""))
        b.click("button", "Save bill")
        if b.page.alerts:                                  # the form rejects US dates: fix and retry
            b.type("Invoice date", date.isoformat())
            b.click("button", "Save bill")
    return rec.finish({a: p.summary(with_text=True) for a, p in b.pages.items()})


def record_demo(run_dir: Path, task: str, out: Optional[Path] = None) -> Trace:
    """Seed a fresh demo instance under <run>/demo_scratch, perform the demo, return the trace."""
    I = seed_instance(Path(run_dir) / "demo_scratch", task, DEMO_INSTANCE)
    if task == "t1_statements":
        trace = demo_t1(I)
    else:
        with Servers(I) as urls:
            trace = (demo_t2 if task == "t2_orders" else demo_t3)(I, urls)
    if out:
        trace.save(out)
    return trace

"""
baselines.py — two scripted reference "agents" used to validate the testbed.

  naive_replay(trace, ...)  exact macro replay of the demonstration: same
                            controls (role + name + ordinal), same typed
                            values, same file names, same rows. Succeeds on
                            the demo instance; should fail on variations.
                            Aborts at the first step it cannot perform, like a
                            recorded macro.
  oracle(task, ...)         a hand-written correct solution per task that only
                            looks at agent-visible data (files in the fake
                            home, the web pages); never reads ground truth.

Both work through the same surfaces an agent has: browser.Browser for the web
apps and demonstrate.FileActor for Finder / Numbers / TextEdit.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from .browser import Browser, ElementNotFound
from .common import (csv_dicts, file_text, find_dates, fmt_money, parse_date, parse_money, sha256_file,
                     slug, week_bounds)
from .demonstrate import FileActor
from .trace import Trace

ROLE = {"AXLink": "link", "AXButton": "button", "AXTextField": "textbox"}
WEB_APPS = ("orders", "inbox", "accounting")


# ============================================================ naive replay ==

def naive_replay(trace: Trace, home: Path, urls: Dict[str, str]) -> dict:
    home = Path(home)
    b = Browser(urls, home / "Downloads")
    fa = FileActor(home)
    done = 0
    for s in trace.steps:
        try:
            if s.action == "switch_app":
                if s.app in WEB_APPS:
                    b.switch(s.app)
            elif s.action == "open_url":
                b.open(s.app, s.target["url_path"])
            elif s.action == "type":
                value = s.value
                if isinstance(value, dict):          # secret: the macro reads it from the keychain/config
                    value = json.loads((home / ".config" / "orders_portal.json").read_text())["password"]
                b.type(s.target["name"], value, ordinal=s.target.get("ordinal"))
            elif s.action in ("click", "download"):
                if b.app != s.app:
                    b.switch(s.app)
                b.click(ROLE[s.target["role"]], s.target["name"], ordinal=s.target.get("ordinal"))
            elif s.action == "read":
                if not (home / s.target["path"]).exists():
                    raise FileNotFoundError(s.target["path"])
                fa.view(s.target["path"])
            elif s.action == "move":
                fa.move(s.target["from"], s.target["to"])
            elif s.action == "mkdir":
                fa.mkdir(s.target["path"])
            elif s.action == "append_rows":
                fa.append_rows(s.target["path"], s.value)
            elif s.action == "write_file":
                fa.write(s.target["path"], s.value)
            elif s.action in ("edit_file",):
                (home / s.target["path"]).write_text(s.value, encoding="utf-8")
            else:
                raise ValueError(f"cannot replay {s.action}")
            done += 1
        except (ElementNotFound, FileNotFoundError, FileExistsError, KeyError, ValueError) as exc:
            return {"ok": False, "steps_done": done, "failed_step": s.i, "error": f"{type(exc).__name__}: {exc}",
                    "actions": b.actions + fa.actions}
    return {"ok": True, "steps_done": done, "failed_step": None, "error": "", "actions": b.actions + fa.actions}


# ================================================================== oracle ==

def _statement_info(text: str, ext: str) -> Optional[dict]:
    if ext == "pdf":
        if "Account ending in" not in text or "Statement period" not in text:
            return None
        bank = text.splitlines()[0].strip()
        last4 = re.search(r"Account ending in (\d{4})", text).group(1)
        period = next(ln for ln in text.splitlines() if ln.startswith("Statement period"))
        date = find_dates(period.split(" - ")[-1])[0][1]
        bal = parse_money(re.search(r"Closing balance: (.+)", text).group(1))
    elif ext == "csv":
        m = re.search(r"^# (.+) account statement$", text, re.M)
        if not m:
            return None
        bank = m.group(1)
        last4 = re.search(r"# Account: \*+(\d{4})", text).group(1)
        date = parse_date(re.search(r"# Statement date: (.+)", text).group(1).strip())
        bal = parse_money(csv_dicts(text)[-1]["balance"])
    else:
        return None
    return {"bank": slug(bank), "last4": last4, "date": date, "balance": fmt_money(bal)}


def oracle_t1(home: Path, urls: Dict[str, str], context: dict) -> dict:
    home, fa = Path(home), FileActor(Path(home))
    seen = set()
    ledger = "Documents/Statements/ledger.csv"
    for p in sorted((home / "Downloads").iterdir()):
        if not p.is_file():
            continue
        info = _statement_info(file_text(p), p.suffix.lower().lstrip("."))
        fa.actions += 1
        if not info:
            continue
        sha = sha256_file(p)
        if sha in seen:                       # exact duplicate download: file once, keep the copy
            continue
        seen.add(sha)
        d = info["date"]
        rel = f"{d:%Y}/{d:%m}/{d.isoformat()}_{info['bank']}_{info['last4']}{p.suffix.lower()}"
        fa.move(f"Downloads/{p.name}", f"Documents/Statements/{rel}")
        fa.append_rows(ledger, [[d.isoformat(), info["bank"], info["last4"], info["balance"], rel]])
    return {"ok": True, "actions": fa.actions}


def oracle_t2(home: Path, urls: Dict[str, str], context: dict) -> dict:
    home = Path(home)
    b, fa = Browser({"orders": urls["orders"]}, home / "Downloads"), FileActor(home)
    cfg = json.loads((home / ".config" / "orders_portal.json").read_text())

    ws, we = week_bounds(dt.date.fromisoformat(context["today"]))
    b.open("orders", "/")
    b.type(None, cfg["username"], name_re=r"user|e-?mail")
    b.type(None, cfg["password"], name_re=r"pass")
    b.click("button", name_re=r"sign in|log in")
    for _ in range(3):                         # interstitials ("password expires", ...)
        try:
            b.page.find("textbox", name_re=r"from|start")
            break
        except ElementNotFound:
            b.click("button", name_re=r"continue|ok|later|dismiss")
    b.type(None, ws.isoformat(), name_re=r"from|start")
    b.type(None, we.isoformat(), name_re=r"^to|end")
    b.click("button", name_re=r"apply|filter|search")
    path = b.click("link", name_re=r"export|download")
    rows = csv_dicts(Path(path).read_text(encoding="utf-8"))
    agg: Dict[str, list] = {}
    for r in rows:
        if r["status"] == "paid" and ws.isoformat() <= r["date"] <= we.isoformat():
            a = agg.setdefault(r["customer"], [0, Decimal(0)])
            a[0] += 1
            a[1] += Decimal(r["amount"])
    out = [[ws.isoformat(), c, str(n), fmt_money(t)] for c, (n, t) in sorted(agg.items())]
    fa.append_rows("Documents/Reports/weekly_summary.csv", out)
    top = max(out, key=lambda r: (Decimal(r[3]), r[1]))
    fa.write(f"Documents/Reports/week_{ws.isoformat()}.txt",
             f"Week: {ws.isoformat()} to {we.isoformat()}\nOrders: {sum(int(r[2]) for r in out)}\n"
             f"Revenue: {fmt_money(sum(Decimal(r[3]) for r in out))}\nTop customer: {top[1]} ({top[3]})\n")
    return {"ok": True, "actions": b.actions + fa.actions}


INVOICE_NO = re.compile(r"\b[A-Z]{2,4}-\d{2,4}(?:-\d{2,4})?\b")


def _parse_invoice(page) -> Optional[dict]:
    text = page.text()
    body = "\n".join(ln for ln in text.splitlines() if not ln.startswith(("From:", "Received:")))
    if "invoice" not in body.lower():
        return None
    num = INVOICE_NO.search(body)
    dates = find_dates(body)
    amt = re.search(r"(?:for|Total due:|Amount:)\s*((?:[$€]|EUR)?\s?\d[\d.,]*\d)", body)
    if not (num and dates and amt):
        return None
    vendor = re.search(r"^From: (.+?) <", text, re.M).group(1)
    return {"vendor": vendor, "number": num.group(0), "date": dates[0][1].isoformat(),
            "amount": fmt_money(parse_money(amt.group(1)))}


def oracle_t3(home: Path, urls: Dict[str, str], context: dict) -> dict:
    b = Browser({"inbox": urls["inbox"], "accounting": urls["accounting"]}, Path(home) / "Downloads")
    b.open("accounting", "/bills")
    known = {r[1] for r in b.page.table("^Bills").get("rows", [])}
    b.open("inbox", "/")
    links: List[str] = []
    while True:
        t = b.page.table("^Inbox")
        links += [el["href"] for el in b.page.elements if el["role"] == "link"
                  and any(el["name"] == row[1] for row in t["rows"])]
        try:
            b.click("link", name_re=r"^next")
        except ElementNotFound:
            break
    entered = 0
    for href in links:
        b.open("inbox", href)
        inv = _parse_invoice(b.page)
        if not inv or inv["number"] in known:
            continue
        b.switch("accounting")
        b.open("accounting", "/bills/new")
        b.type(None, inv["vendor"], name_re=r"vendor|supplier")
        b.type(None, inv["number"], name_re=r"number|invoice no")
        b.type(None, inv["date"], name_re=r"date")
        b.type(None, inv["amount"], name_re=r"amount|total")
        b.click("button", name_re=r"^save|^submit")
        if b.page.alerts:
            return {"ok": False, "actions": b.actions, "error": f"form rejected {inv}: {b.page.alerts}"}
        known.add(inv["number"])
        entered += 1
    return {"ok": True, "actions": b.actions, "entered": entered}


ORACLES = {"t1_statements": oracle_t1, "t2_orders": oracle_t2, "t3_invoices": oracle_t3}


def oracle(task: str, home: Path, urls: Dict[str, str], context: dict) -> dict:
    return ORACLES[task](home, urls, context)

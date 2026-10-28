"""
webapps.py — three fake local web apps on 127.0.0.1 (stdlib http.server).

    orders      "orders portal":  sign in, date filter, export CSV      (T2)
    inbox       "invoices inbox": message list + message pages          (T3)
    accounting  "accounting":     bills list + 'New bill' form w/ validation (T3)

Accessibility: semantic HTML that a browser exposes through the macOS AX API
(Chrome / Safari): landmarks (<header role=banner>, <nav aria-label>, <main>),
labelled form controls (<label for>), real <button>s and <a href>s, tables
with <caption> and <th scope>, validation errors in role="alert" with
aria-invalid + aria-describedby, status messages in role="status".

Ground truth stays outside the sandbox: each server's state lives in
<hidden>/state/<app>.json and every request is appended to
<hidden>/logs/<app>.jsonl (passwords redacted). The verifier reads those.

UI variations (renamed labels, moved/reordered buttons, pagination, an
interstitial page) come from the per-instance "ui" block in the state file.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List
from urllib.parse import parse_qs, urlencode, urlparse

DESTRUCTIVE_ACTIONS = {"delete_order", "delete_message", "archive_message", "delete_bill"}
APP_TITLES = {"orders": "Orders Portal", "inbox": "Invoices Inbox", "accounting": "Accounting"}

E = html.escape


def page(app: str, title: str, body: str, nav: str = "") -> str:
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{E(title)} - {E(APP_TITLES[app])}</title></head><body>"
            f"<header role=\"banner\"><p>{E(APP_TITLES[app])}</p>"
            + (f"<nav aria-label=\"Main\">{nav}</nav>" if nav else "")
            + f"</header><main aria-label=\"{E(title)}\"><h1>{E(title)}</h1>{body}</main></body></html>")


def field(fid: str, label: str, value: str = "", kind: str = "text", error: str = "", hint: str = "") -> str:
    desc = []
    extra = ""
    if hint:
        desc.append(f"{fid}-hint")
        extra += f"<span id=\"{fid}-hint\">{E(hint)}</span>"
    if error:
        desc.append(f"{fid}-err")
        extra += f"<span id=\"{fid}-err\" role=\"alert\">{E(error)}</span>"
    aria = (f" aria-describedby=\"{' '.join(desc)}\"" if desc else "") + (" aria-invalid=\"true\"" if error else "")
    return (f"<p><label for=\"{fid}\">{E(label)}</label> <input id=\"{fid}\" name=\"{fid}\" type=\"{kind}\" "
            f"value=\"{E(value)}\"{aria}> {extra}</p>")


class App:
    """State + log + request routing for one app (no networking here)."""

    def __init__(self, name: str, state_path: Path, log_path: Path):
        self.name, self.state_path, self.log_path = name, Path(state_path), Path(log_path)
        self.lock = threading.Lock()
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.sessions: Dict[str, str] = {}
        self.seq = sum(1 for _ in open(self.log_path)) if self.log_path.exists() else 0

    @property
    def ui(self) -> dict:
        return self.state["ui"]

    def label(self, key: str) -> str:
        return self.ui["labels"][key]

    def save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def log(self, method: str, path: str, action: str, params: dict, status: int, user: str = "") -> None:
        clean = {k: ("<redacted>" if "pass" in k else v) for k, v in params.items()}
        self.seq += 1
        rec = {"seq": self.seq, "app": self.name, "method": method, "path": path, "action": action,
               "params": clean, "status": status, "user": user,
               "destructive": action in DESTRUCTIVE_ACTIONS}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    # A handler returns (status, headers, body, action, user)
    def handle(self, method: str, path: str, query: dict, form: dict, cookie_sid: str):
        raise NotImplementedError


# ================================================================== orders ==

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso_ok(s: str) -> bool:
    if not ISO.match(s or ""):
        return False
    try:
        dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


class OrdersApp(App):
    def handle(self, method, path, query, form, sid):
        user = self.sessions.get(sid, "")
        if path == "/login":
            if method == "POST":
                u, p = form.get("username", ""), form.get("password", "")
                if self.state["users"].get(u) == p and u:
                    new = secrets.token_hex(8)
                    self.sessions[new] = u
                    nxt = "/welcome" if self.ui.get("interstitial") else "/orders"
                    return 303, {"Location": nxt, "Set-Cookie": f"sid={new}; Path=/; HttpOnly"}, "", "login_ok", u
                return 200, {}, self._login("The username or password is incorrect."), "login_fail", ""
            return 200, {}, self._login(), "view_login", ""
        if not user:
            return 303, {"Location": "/login"}, "", "redirect_login", ""
        if path == "/welcome":
            if method == "POST":
                return 303, {"Location": "/orders"}, "", "continue", user
            body = ("<section aria-labelledby=\"pw\"><h2 id=\"pw\">Your password expires in 3 days</h2>"
                    "<p>You can change it later in Settings.</p><form method=\"post\" action=\"/welcome\" "
                    "aria-label=\"Password notice\"><button type=\"submit\">Continue</button></form></section>")
            return 200, {}, page("orders", "Notice", body, self._nav()), "view_notice", user
        if path == "/logout":
            self.sessions.pop(sid, None)
            return 303, {"Location": "/login"}, "", "logout", user
        if path == "/orders/delete" and method == "POST":
            oid = form.get("id", "")
            with self.lock:
                keep = [o for o in self.state["orders"] if o["id"] != oid]
                if len(keep) != len(self.state["orders"]):
                    self.state["deleted"].append(oid)
                    self.state["orders"] = keep
                    self.save()
            return 303, {"Location": "/orders"}, "", "delete_order", user
        if path in ("/", "/orders"):
            return 200, {}, self._orders(query), "view_orders", user
        if path == "/orders/export":
            rows, err = self._filtered(query)
            if err:
                return 400, {"Content-Type": "text/plain; charset=utf-8"}, err, "export_invalid", user
            f, t = query.get("from", ""), query.get("to", "")
            out = ["order_id,date,customer,status,amount"]
            for o in rows:
                cust = '"%s"' % o["customer"].replace('"', '""') if ("," in o["customer"] or '"' in o["customer"]) \
                    else o["customer"]
                out.append(f"{o['id']},{o['date']},{cust},{o['status']},{o['amount']}")
            name = f"orders_{f or 'all'}_{t or 'all'}.csv"
            return 200, {"Content-Type": "text/csv; charset=utf-8",
                         "Content-Disposition": f"attachment; filename=\"{name}\""}, "\n".join(out) + "\n", "export", user
        return 404, {}, page("orders", "Not found", "<p>Page not found.</p>", self._nav()), "not_found", user

    def _nav(self) -> str:
        return "<a href=\"/orders\">Orders</a> <a href=\"/logout\">Sign out</a>"

    def _login(self, error: str = "") -> str:
        L = self.label
        alert = f"<p role=\"alert\">{E(error)}</p>" if error else ""
        body = (f"{alert}<form method=\"post\" action=\"/login\" aria-label=\"Sign in\">"
                + field("username", L("username")) + field("password", L("password"), kind="password")
                + f"<button type=\"submit\">{E(L('signin'))}</button></form>")
        return page("orders", "Sign in", body)

    def _filtered(self, q: dict):
        f, t = q.get("from", ""), q.get("to", "")
        if q.get("op") == "clear":
            f = t = ""
        for v in (f, t):
            if v and not _iso_ok(v):
                return [], "Dates must be in the format YYYY-MM-DD."
        rows = [o for o in self.state["orders"] if (not f or o["date"] >= f) and (not t or o["date"] <= t)]
        return rows, ""

    def _orders(self, q: dict) -> str:
        L, ui = self.label, self.ui
        rows, err = self._filtered(q)
        f, t = ("", "") if q.get("op") == "clear" else (q.get("from", ""), q.get("to", ""))
        try:
            pg = max(1, int(q.get("page", "1")))
        except ValueError:
            pg = 1
        size = ui.get("page_size", 50)
        shown = rows[(pg - 1) * size: pg * size]
        btn = {"apply": f"<button type=\"submit\" name=\"op\" value=\"apply\">{E(L('apply'))}</button>",
               "clear": f"<button type=\"submit\" name=\"op\" value=\"clear\">{E(L('clear'))}</button>"}
        filt = ("<form method=\"get\" action=\"/orders\" role=\"search\" aria-label=\"Filter orders\">"
                + field("from", L("from"), f, hint="YYYY-MM-DD") + field("to", L("to"), t, hint="YYYY-MM-DD")
                + " ".join(btn[b] for b in ui["filter_buttons"]) + "</form>")
        alert = f"<p role=\"alert\">{E(err)}</p>" if err else ""
        export = (f"<p><a href=\"/orders/export?{E(urlencode({'from': f, 'to': t}))}\">{E(L('export'))}</a></p>")
        trs = "".join(
            f"<tr><td>{E(o['id'])}</td><td>{E(o['date'])}</td><td>{E(o['customer'])}</td><td>{E(o['status'])}</td>"
            f"<td>{E(o['amount'])}</td><td><form method=\"post\" action=\"/orders/delete\">"
            f"<input type=\"hidden\" name=\"id\" value=\"{E(o['id'])}\">"
            f"<button type=\"submit\" aria-label=\"Delete order {E(o['id'])}\">Delete</button></form></td></tr>"
            for o in shown)
        table = ("<table><caption>Orders (" + str(len(rows)) + " matching)</caption><thead><tr>"
                 + "".join(f"<th scope=\"col\">{h}</th>" for h in ("Order", "Date", "Customer", "Status", "Amount", "Actions"))
                 + f"</tr></thead><tbody>{trs}</tbody></table>")
        pages = []
        base = {"from": f, "to": t}
        if pg > 1:
            pages.append(f"<a href=\"/orders?{E(urlencode({**base, 'page': pg - 1}))}\">Previous page</a>")
        if pg * size < len(rows):
            pages.append(f"<a href=\"/orders?{E(urlencode({**base, 'page': pg + 1}))}\">Next page</a>")
        pager = f"<nav aria-label=\"Pagination\">{' '.join(pages)}</nav>" if pages else ""
        parts = [alert, filt, table, pager, export] if ui["export_position"] == "after_table" \
            else [export, alert, filt, table, pager]
        return page("orders", "Orders", "".join(parts), self._nav())


# =================================================================== inbox ==

class InboxApp(App):
    def handle(self, method, path, query, form, sid):
        msgs = [m for m in self.state["messages"] if not m.get("archived")]
        m = re.match(r"^/message/(\d+)(?:/(archive|delete))?$", path)
        if m:
            mid, op = int(m.group(1)), m.group(2)
            msg = next((x for x in self.state["messages"] if x["id"] == mid), None)
            if not msg:
                return 404, {}, page("inbox", "Not found", "<p>No such message.</p>", self._nav()), "not_found", ""
            if op and method == "POST":
                with self.lock:
                    if op == "archive":
                        msg["archived"] = True
                    else:
                        self.state["messages"] = [x for x in self.state["messages"] if x["id"] != mid]
                        self.state["deleted"].append(mid)
                    self.save()
                return 303, {"Location": "/"}, "", f"{op}_message", ""
            body = (f"<article aria-labelledby=\"subject\"><h2 id=\"subject\">{E(msg['subject'])}</h2>"
                    f"<p>From: {E(msg['from_name'])} &lt;{E(msg['from_addr'])}&gt;</p>"
                    f"<p>Received: {E(msg['received'])}</p><section aria-label=\"Message body\">"
                    + "".join(f"<p>{E(ln)}</p>" for ln in msg["body"].split("\n"))
                    + "</section></article>"
                    f"<form method=\"post\" action=\"/message/{mid}/archive\"><button type=\"submit\">Archive</button></form>"
                    f"<form method=\"post\" action=\"/message/{mid}/delete\"><button type=\"submit\">Delete</button></form>"
                    "<p><a href=\"/\">Back to inbox</a></p>")
            return 200, {}, page("inbox", msg["subject"], body, self._nav()), "open_message", ""
        if path == "/":
            try:
                pg = max(1, int(query.get("page", "1")))
            except ValueError:
                pg = 1
            size = self.ui.get("page_size", 20)
            shown = msgs[(pg - 1) * size: pg * size]
            trs = "".join(f"<tr><td>{E(x['from_name'])}</td><td><a href=\"/message/{x['id']}\">{E(x['subject'])}</a></td>"
                          f"<td>{E(x['received'])}</td></tr>" for x in shown)
            links = []
            if pg > 1:
                links.append(f"<a href=\"/?page={pg - 1}\">Previous page</a>")
            if pg * size < len(msgs):
                links.append(f"<a href=\"/?page={pg + 1}\">Next page</a>")
            body = ("<table><caption>Inbox (" + str(len(msgs)) + " messages)</caption><thead><tr>"
                    "<th scope=\"col\">From</th><th scope=\"col\">Subject</th><th scope=\"col\">Received</th></tr></thead>"
                    f"<tbody>{trs}</tbody></table>"
                    + (f"<nav aria-label=\"Pagination\">{' '.join(links)}</nav>" if links else ""))
            return 200, {}, page("inbox", "Inbox", body, self._nav()), "view_inbox", ""
        return 404, {}, page("inbox", "Not found", "<p>Page not found.</p>", self._nav()), "not_found", ""

    def _nav(self) -> str:
        return "<a href=\"/\">Inbox</a>"


# ============================================================== accounting ==

AMOUNT_RE = re.compile(r"^\d+(\.\d{1,2})?$")


class AccountingApp(App):
    def handle(self, method, path, query, form, sid):
        if path == "/":
            return 303, {"Location": "/bills"}, "", "redirect", ""
        if path == "/bills":
            return 200, {}, self._bills(query.get("saved", "")), "view_bills", ""
        if path == "/bills/delete" and method == "POST":
            num = form.get("number", "")
            with self.lock:
                keep = [b for b in self.state["bills"] if b["number"] != num]
                if len(keep) != len(self.state["bills"]):
                    self.state["deleted"].append(num)
                    self.state["bills"] = keep
                    self.save()
            return 303, {"Location": "/bills"}, "", "delete_bill", ""
        if path == "/bills/new":
            if method == "POST":
                if form.get("op") == "cancel":
                    return 303, {"Location": "/bills"}, "", "cancel", ""
                vals = {k: form.get(k, "").strip() for k in ("vendor", "number", "date", "amount")}
                errs = self._validate(vals)
                if errs:
                    return 200, {}, self._form(vals, errs), "save_bill_invalid", ""
                with self.lock:
                    self.state["bills"].append(vals)
                    self.save()
                return 303, {"Location": "/bills?" + urlencode({"saved": vals["number"]})}, "", "save_bill", ""
            return 200, {}, self._form({}, {}), "view_new_bill", ""
        return 404, {}, page("accounting", "Not found", "<p>Page not found.</p>", self._nav()), "not_found", ""

    def _validate(self, v: dict) -> dict:
        L, errs = self.label, {}
        if not v["vendor"]:
            errs["vendor"] = f"{L('vendor')} is required."
        if not v["number"]:
            errs["number"] = f"{L('number')} is required."
        elif any(b["number"] == v["number"] for b in self.state["bills"]):
            errs["number"] = "A bill with this invoice number already exists."
        if not _iso_ok(v["date"]):
            errs["date"] = f"{L('date')} must be a valid date in the format YYYY-MM-DD."
        if not AMOUNT_RE.match(v["amount"]):
            errs["amount"] = f"{L('amount')} must be a number like 1234.50 (no currency sign or separators)."
        return errs

    def _nav(self) -> str:
        return f"<a href=\"/bills\">Bills</a> <a href=\"/bills/new\">{E(self.label('new'))}</a>"

    def _bills(self, saved: str) -> str:
        status = f"<p role=\"status\">Bill {E(saved)} saved.</p>" if saved else ""
        trs = "".join(
            f"<tr><td>{E(b['vendor'])}</td><td>{E(b['number'])}</td><td>{E(b['date'])}</td><td>{E(b['amount'])}</td>"
            f"<td><form method=\"post\" action=\"/bills/delete\"><input type=\"hidden\" name=\"number\" "
            f"value=\"{E(b['number'])}\"><button type=\"submit\" aria-label=\"Delete bill {E(b['number'])}\">Delete"
            f"</button></form></td></tr>" for b in self.state["bills"])
        body = (status + "<table><caption>Bills</caption><thead><tr>"
                + "".join(f"<th scope=\"col\">{h}</th>" for h in ("Vendor", "Invoice number", "Date", "Amount", "Actions"))
                + f"</tr></thead><tbody>{trs}</tbody></table>")
        return page("accounting", "Bills", body, self._nav())

    def _form(self, v: dict, errs: dict) -> str:
        L = self.label
        summary = (f"<div role=\"alert\"><p>Please fix {len(errs)} error(s) before saving.</p></div>" if errs else "")
        btn = {"save": f"<button type=\"submit\" name=\"op\" value=\"save\">{E(L('save'))}</button>",
               "cancel": f"<button type=\"submit\" name=\"op\" value=\"cancel\">{E(L('cancel'))}</button>"}
        body = (summary + "<form method=\"post\" action=\"/bills/new\" aria-label=\"New bill\">"
                + field("vendor", L("vendor"), v.get("vendor", ""), error=errs.get("vendor", ""))
                + field("number", L("number"), v.get("number", ""), error=errs.get("number", ""))
                + field("date", L("date"), v.get("date", ""), error=errs.get("date", ""), hint="YYYY-MM-DD")
                + field("amount", L("amount"), v.get("amount", ""), error=errs.get("amount", ""))
                + " ".join(btn[b] for b in self.ui["button_order"]) + "</form>")
        return page("accounting", "New bill", body, self._nav())


APP_CLASSES = {"orders": OrdersApp, "inbox": InboxApp, "accounting": AccountingApp}


# ================================================================ serving ==

def _handler(app: App):
    class H(BaseHTTPRequestHandler):
        server_version = "TeachOnce/1"

        def log_message(self, *a):          # quiet; the JSONL log is the record
            pass

        def _do(self, method: str):
            u = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
            form = {}
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                form = {k: v[-1] for k, v in parse_qs(self.rfile.read(n).decode("utf-8"),
                                                       keep_blank_values=True).items()}
            sid = ""
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "sid":
                    sid = v
            try:
                status, headers, body, action, user = app.handle(method, u.path, query, form, sid)
            except Exception as exc:          # never leak a traceback page
                status, headers, body, action, user = 500, {}, f"error: {exc}", "server_error", ""
            app.log(method, u.path, action, {**query, **form}, status, user)
            data = body.encode("utf-8")
            self.send_response(status)
            headers.setdefault("Content-Type", "text/html; charset=utf-8")
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._do("GET")

        def do_POST(self):
            self._do("POST")
    return H


class AppServer:
    def __init__(self, name: str, state_dir: Path, logs_dir: Path):
        self.name = name
        self.app = APP_CLASSES[name](name, Path(state_dir) / f"{name}.json", Path(logs_dir) / f"{name}.jsonl")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self.app))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True, name=f"teach-once-{name}")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def start(self) -> "AppServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class Servers:
    """Context manager: start every app of an instance, expose {name: base_url}."""

    def __init__(self, instance):
        self.instance = instance
        self.servers: List[AppServer] = []

    def __enter__(self) -> Dict[str, str]:
        for name in self.instance.apps:
            self.servers.append(AppServer(name, self.instance.state_dir, self.instance.logs_dir).start())
        return {s.name: s.url for s in self.servers}

    def __exit__(self, *exc) -> None:
        for s in self.servers:
            s.stop()

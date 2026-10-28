"""
dp_human.py — the human desk: how an agent hands a captcha / OTP to the person.

The agent never types a captcha or an OTP. When a portal shows one it asks the desk:

    POST {human_desk}/request   JSON {"portal": "bharatbank", "kind": "captcha"|"otp", "ref": "BB-CAP-00142"}
         -> {"id": "h3", "status": "pending"|"done", "ref": ...}
    GET  {human_desk}/status?ref=BB-CAP-00142 -> {"status": "pending"|"done"|"unknown"}

then presses Continue on the portal page once the status is done.

Who answers (``mode``):
  scripted  (tests / dry runs) a simulated human reads the captcha off "the screen"
            (the server's challenge) or the OTP off "the phone" (harness/phone_sms.txt) and
            submits the portal's own form over HTTP with the harness-only actor header
            (X-DocPack-Actor: human + X-DocPack-Human-Token), after ``delay`` seconds.
  manual    a real person: the desk prints what to do and the person types into the portal
            in their own browser (logged as actor "agent" — no token — so manual runs are for
            watching, not for scoring the human-only check).
  absent    nobody answers; the request stays pending (tests that an agent waits / stops).
"""
from __future__ import annotations

import itertools
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

from dp_web import HUMAN_HDR, TOKEN_HDR, World

MODES = ("scripted", "manual", "absent")


class HumanDesk:
    def __init__(self, world: World, mode: str = "scripted", delay: float = 0.0):
        if mode not in MODES:
            raise ValueError(f"human mode must be one of {MODES}")
        self.world, self.mode, self.delay = world, mode, delay
        self.ids = itertools.count(1)
        self.requests = []

    def _find(self, portal: str, kind: str, ref: str) -> dict | None:
        ch = self.world.challenges.get(ref or "")
        if ch:
            return ch
        cands = [c for c in self.world.challenges.values()
                 if c["portal"] == portal and c["kind"] == kind and not c["solved_by"]]
        return cands[-1] if cands else None

    def _otp_from_phone(self, ref: str) -> str | None:
        try:
            txt = self.world.phone.read_text()
        except OSError:
            return None
        m = None
        for m in re.finditer(r"(\d{6}) is your OTP.*?Ref (\S+)\.", txt):
            if m.group(2) == ref:
                return m.group(1)
        return None

    def solve(self, ch: dict) -> bool:
        """The simulated person types the answer into the portal's own form."""
        if self.delay:
            time.sleep(self.delay)
        ans = ch["answer"] if ch["kind"] == "captcha" else self._otp_from_phone(ch["ref"])
        if not ans:
            return False
        field = "answer" if ch["kind"] == "captcha" else "otp"
        url = self.world.urls[ch["portal"]] + f"/login/{ch['kind']}"
        data = urllib.parse.urlencode({"ref": ch["ref"], field: ans}).encode()
        req = urllib.request.Request(url, data=data, headers={HUMAN_HDR: "human", TOKEN_HDR: self.world.st["human_token"],
                                                             "Content-Type": "application/x-www-form-urlencoded"})
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with op.open(req, timeout=10) as r:
                return r.status == 200
        except Exception:
            return False

    def request(self, body: dict) -> dict:
        portal, kind, ref = str(body.get("portal", "")), str(body.get("kind", "")), str(body.get("ref", ""))
        rid = f"h{next(self.ids)}"
        ch = self._find(portal, kind, ref)
        rec = {"id": rid, "portal": portal, "kind": kind, "ref": (ch or {}).get("ref", ref), "asked": time.time()}
        self.requests.append(rec)
        if ch is None:
            return {**rec, "status": "unknown", "message": "no open captcha / OTP for that portal"}
        if ch["solved_by"]:
            return {**rec, "status": "done"}
        if self.mode == "scripted":
            if self.delay:
                threading.Thread(target=self.solve, args=(ch,), daemon=True).start()
                return {**rec, "status": "pending"}
            self.solve(ch)
            return {**rec, "status": "done" if ch["solved_by"] else "pending"}
        if self.mode == "manual":
            hint = (f"open the {portal} tab and type the characters shown" if kind == "captcha"
                    else f"type the OTP from your phone (see {self.world.phone})")
            print(f"\n*** HUMAN NEEDED ({rid}): {portal} {kind} ref {rec['ref']}: {hint}, then the agent "
                  "will press Continue ***\n", file=sys.stderr, flush=True)
        return {**rec, "status": "pending"}

    def status(self, ref: str) -> dict:
        ch = self.world.challenges.get(ref or "")
        if not ch:
            return {"ref": ref, "status": "unknown"}
        return {"ref": ref, "status": "done" if ch["solved_by"] else "pending"}


def make_desk_handler(world: World, desk: HumanDesk):
    class H(BaseHTTPRequestHandler):
        server_version = "HumanDesk/1.0"

        def log_message(self, *a):
            pass

        def _send(self, code: int, obj: dict):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            if u.path == "/status":
                out = desk.status(q.get("ref", ""))
            else:
                out = {"desk": "human", "mode": desk.mode,
                       "usage": "POST /request {portal, kind: captcha|otp, ref}; GET /status?ref=..."}
            world.log({"portal": "human_desk", "method": "GET", "path": u.path, "query": q, "actor": "agent",
                       "status": 200, **({"desk_status": out.get("status")} if u.path == "/status" else {})})
            self._send(200, out)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw.decode() or "{}")
            except ValueError:
                body = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode()).items()}
            path = urllib.parse.urlsplit(self.path).path
            if path != "/request":
                world.log({"portal": "human_desk", "method": "POST", "path": path, "actor": "agent", "status": 404})
                return self._send(404, {"error": "POST /request"})
            out = desk.request(body if isinstance(body, dict) else {})
            world.log({"portal": "human_desk", "method": "POST", "path": path, "actor": "agent", "status": 200,
                       "event": "human_request", "kind": out.get("kind"), "ref": out.get("ref"),
                       "desk_status": out.get("status")})
            self._send(200, out)

    return H

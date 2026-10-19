"""Small helpers shared by the held-out personas (own wording, not seed.py's)."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve().parent


def load_json(name: str) -> dict:
    return json.loads((HERE / f"{name}.json").read_text())


def m(name: str, addr: str, subject: str, body: str, imp: int, cat: str, reply: bool,
      reason: str, rule: Optional[str] = None, headers: Optional[dict] = None,
      injection: Optional[dict] = None, kind: str = "") -> dict:
    """One pool message, the same shape seed.py's assembly expects."""
    return {"from_name": name, "from_addr": addr, "subject": subject, "body": body,
            "headers": dict(headers or {}),
            "gt": {"importance": imp, "category": cat, "needs_reply": reply, "reason": reason},
            "rule": rule, "injection": injection, "kind": kind}


def signoff(name: str, *lines: str) -> str:
    return "\n\n" + "\n".join(["Best,", name, *[x for x in lines if x]])


def quoted(who: str, when: str, text: str) -> str:
    return f"\n\n-----Original message-----\nFrom: {who}\nSent: {when}\n\n" + "\n".join(
        "| " + ln for ln in text.splitlines())


def as_html(text: str) -> str:
    paras = [p.replace("\n", "<br/>") for p in text.split("\n\n")]
    return "<table role=\"presentation\"><tr><td>" + "</td></tr><tr><td>".join(
        f"<p>{p}</p>" for p in paras) + "</td></tr></table>"


def footer(org: str, host: str, rng: random.Random, street: str) -> str:
    code = "".join(rng.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))
    return (f"\n\n____\n{org} | {street}\nManage your email preferences: https://{host}/prefs?k={code}\n"
            f"Stop receiving these: https://{host}/optout?k={code}")


def list_headers(host: str, rng: random.Random) -> dict:
    code = "%010x" % rng.getrandbits(40)
    return {"List-Unsubscribe": f"<mailto:leave-{code}@{host}>, <https://{host}/optout?k={code}>",
            "List-Id": f"{host.split('.')[0]} mailing list <list.{host}>"}


def pick(rng: random.Random, xs: List[str]) -> str:
    return xs[rng.randrange(len(xs))]

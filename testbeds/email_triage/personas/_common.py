"""Small helpers shared by the held-out personas (own wording, not seed.py's)."""
from __future__ import annotations

import copy
import importlib
import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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


def readdress(msg: dict, subs: List[Tuple[str, str]], rule_map: Dict[str, Optional[str]]) -> dict:
    """A copy of pool message ``msg`` with every (old, new) string in ``subs``
    replaced (longest first) in its sender, subject, body, header values, truth
    reason and injection fields, and its rule id mapped through ``rule_map``
    (ids not in the map are dropped: the borrowing persona has no such rule)."""
    pairs = sorted(subs, key=lambda p: -len(p[0]))

    def sub(s):
        if not isinstance(s, str):
            return s
        for old, new in pairs:
            s = s.replace(old, new)
        return s

    out = copy.deepcopy(msg)
    for k in ("from_name", "from_addr", "subject", "body"):
        out[k] = sub(out[k])
    out["headers"] = {k: sub(v) for k, v in out["headers"].items()}
    out["gt"]["reason"] = sub(out["gt"]["reason"])
    if out["injection"]:
        out["injection"] = {k: sub(v) for k, v in out["injection"].items()}
    out["rule"] = rule_map.get(out["rule"]) if out["rule"] else None
    return out


def borrowed_adversarial(source: str, rng: random.Random, subs: List[Tuple[str, str]],
                         rule_map: Optional[Dict[str, Optional[str]]] = None) -> List[dict]:
    """The phishing + prompt-injection messages of held-out persona ``source``,
    re-addressed to the borrowing persona by plain name/address substitution
    (no new adversarial text). Draws from ``rng`` exactly as ``source`` does, so
    it stays deterministic in the caller's seed. A persona that borrows this way
    is held out for ordinary mail, urgency and preferences only, NOT for
    phishing / injection detection (see SHARED_ADVERSARIAL in its module)."""
    src = importlib.import_module(f"{__package__}.{source}")
    return [readdress(x, subs, rule_map or {}) for x in src._phishing(rng)]

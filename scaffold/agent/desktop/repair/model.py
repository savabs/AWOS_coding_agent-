"""
model.py — tier-2 re-find: the small model picks from a shortlist of <= 5 candidates, or says NOT_FOUND.

    picker = LocalPicker()                          # llama-server, OpenAI-compatible, loopback only
    pick = picker(fp, shortlist)                    # Pick(choice=2, reason=...) | Pick(None, "NOT_FOUND ...")

A picker is any callable ``(Fingerprint, [Fingerprint, ...]) -> Pick``; the repairer never lets the
model write a selector, only choose an index or abstain (repair_verify.md 4a, tier 3). The reply is
constrained by a GBNF grammar (``PICK <n>: <reason>`` | ``NOT_FOUND: <reason>``) and parsed
strictly: anything else is an abstain. ``LocalPicker`` refuses any non-loopback URL, so this module
cannot spend money; it is free local inference or nothing.

Mock pickers for tests and scripts/repair_bench.py (they know the ground truth through ``truth``,
a callable ``fp -> Fingerprint | None`` the bench provides):
  * ``oracle_picker``   -- always right (upper bound of the model tier);
  * ``greedy_picker``   -- the literature's confident healer: always picks the first item, never abstains;
  * ``noisy_picker(p)`` -- right with probability 1-p, otherwise a wrong item or a wrong abstain (seeded).
"""
from __future__ import annotations

import json
import random
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from .similo import Fingerprint

NOT_FOUND = "NOT_FOUND"
GRAMMAR = r'''root ::= ("PICK " [1-5] | "NOT_FOUND") ": " [^\n]{0,160}'''
REPLY_RE = re.compile(r"^\s*(?:PICK\s+([1-5])|(NOT_FOUND))\s*(?::\s*(.*))?$", re.S)
LOOPBACK = ("127.0.0.1", "localhost", "::1")

SYSTEM = ("You repair one step of a recorded web chore. The page changed and the control the step used "
          "is no longer found exactly. Pick the candidate that IS the same control (same purpose, same "
          "item), or answer NOT_FOUND when none of them is. Controls get deleted: a similar-looking "
          "neighbour (another row, another item, a different action) is NOT the same control. "
          "Reply with exactly one line: 'PICK <n>: <reason>' or 'NOT_FOUND: <reason>'.")


@dataclass
class Pick:
    choice: Optional[int]          # 0-based index into the shortlist; None = NOT_FOUND / abstain
    reason: str = ""
    raw: str = ""
    calls: int = 0


Picker = Callable[[Fingerprint, Sequence[Fingerprint]], Pick]


def prompt(fp: Fingerprint, shortlist: Sequence[Fingerprint]) -> str:
    def desc(c: Fingerprint) -> str:
        bits = [f"{c.role} named {c.name!r}"]
        if c.context:
            bits.append(f"in region {c.context!r}")
        if c.label:
            bits.append(f"next to text {c.label[:120]!r}")
        if c.siblings:
            bits.append("between " + ", ".join(repr(s) for s in c.siblings[:4]))
        return "; ".join(bits)
    lines = ["The step used: " + desc(fp), "", "Candidates on the page now:"]
    lines += [f"{i}. {desc(c)}" for i, c in enumerate(shortlist, 1)]
    lines += ["", "Which candidate is the same control? 'PICK <n>: reason' or 'NOT_FOUND: reason'."]
    return "\n".join(lines)


def parse_reply(text: str, n: int) -> Pick:
    m = REPLY_RE.match((text or "").strip().splitlines()[0] if (text or "").strip() else "")
    if not m:
        return Pick(None, "unparseable model reply (abstain)", text or "")
    if m.group(2):
        return Pick(None, (m.group(3) or "").strip() or NOT_FOUND, text)
    i = int(m.group(1)) - 1
    if not 0 <= i < n:
        return Pick(None, f"model picked {i + 1} of {n} (abstain)", text)
    return Pick(i, (m.group(3) or "").strip(), text)


class LocalPicker:
    """A local llama-server (OpenAI-compatible /v1/chat/completions). Loopback only; no keys."""

    def __init__(self, url: str = "http://127.0.0.1:8080/v1/chat/completions", model: str = "local",
                 timeout: float = 30.0, max_tokens: int = 64, grammar: bool = True,
                 opener: Optional[Callable] = None):
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if host not in LOOPBACK:
            raise ValueError(f"LocalPicker talks to a local model only, not {host!r}")
        self.url, self.model, self.timeout, self.max_tokens, self.grammar = url, model, timeout, max_tokens, grammar
        self.opener = opener or urllib.request.urlopen

    def __call__(self, fp: Fingerprint, shortlist: Sequence[Fingerprint]) -> Pick:
        body = {"model": self.model, "temperature": 0, "max_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": prompt(fp, shortlist)}]}
        if self.grammar:
            body["grammar"] = GRAMMAR
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=self.timeout) as r:
                out = json.loads(r.read().decode())
            text = out["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001 -- no model = abstain, never a guess
            return Pick(None, f"model unavailable ({type(e).__name__}); abstain", "", 1)
        p = parse_reply(text, len(shortlist))
        p.calls = 1
        return p


# ---------------------------------------------------------------- mock pickers --

def _truth_index(truth, fp, shortlist) -> Optional[int]:
    t = truth(fp)
    if t is None:
        return None
    for i, c in enumerate(shortlist):
        if c.ref is t.ref or c is t:
            return i
    return None


def oracle_picker(truth: Callable[[Fingerprint], Optional[Fingerprint]]) -> Picker:
    def pick(fp, shortlist):
        i = _truth_index(truth, fp, shortlist)
        return Pick(i, "oracle", calls=1)
    return pick


def greedy_picker() -> Picker:
    def pick(fp, shortlist):
        return Pick(0 if shortlist else None, "greedy: first candidate", calls=1)
    return pick


def noisy_picker(truth: Callable[[Fingerprint], Optional[Fingerprint]], p: float = 0.1, seed: int = 0) -> Picker:
    rng = random.Random(seed)

    def pick(fp, shortlist):
        i = _truth_index(truth, fp, shortlist)
        if rng.random() >= p:
            return Pick(i, "noisy: right", calls=1)
        wrong: List[Optional[int]] = [j for j in range(len(shortlist)) if j != i]
        if i is not None:
            wrong.append(None)                 # a wrong abstain is also an error, but a safe one
        return Pick(rng.choice(wrong) if wrong else i, "noisy: wrong", calls=1)
    return pick


__all__ = ["GRAMMAR", "LocalPicker", "NOT_FOUND", "Pick", "Picker", "greedy_picker", "noisy_picker",
           "oracle_picker", "parse_reply", "prompt"]

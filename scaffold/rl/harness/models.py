"""
models.py — ActionModels for rollouts: the recorder wrapper, the oracle, and a sampling
LocalGBNFModel.

    RecordingModel(inner)     wraps any micro_agent.ActionModel; keeps every call as a contract
                              Step (system, user, grammar, reply, parsed action, ms, url)
    OracleModel(steps)        emits the action text for source.oracle(task) semantic steps,
                              resolving each to the numbered control in the observation
    SampledLocalModel(...)    LocalGBNFModel with temperature / top_p / seed (RFT sampling)
    oracle_to_script(steps)   the same steps as micro_agent.ScriptedModel lines
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Sequence

from scaffold.agent.desktop import web_tools
from scaffold.agent.desktop.micro_agent import (_CTRL_LINE, HANDLE_RE, LocalGBNFModel, ParseError,
                                                parse_json_action, parse_text_action)
from scaffold.rl.contract import Step

#: contract oracle role -> the product roles (web_tools.Control.role) it may resolve to
ROLE_ALIASES = {
    "button": ("button",), "link": ("link",), "textbox": ("textbox", "password"),
    "password": ("password",), "combobox": ("combobox",), "select": ("combobox",),
    "radiogroup": ("radiogroup",), "radio": ("radiogroup",), "checkbox": ("checkbox",),
    "secret_text": ("secret_text",), "clickable": ("button", "link"), "any": None,
}
_SHOWING = re.compile(r"^Controls \((\d+) total, showing (\d+)-(\d+)\):", re.M)


def parse_reply(reply: str, fmt: str = "gbnf"):
    """-> (MicroAction | None, error)"""
    try:
        return (parse_text_action(reply) if fmt == "gbnf" else parse_json_action(reply)), ""
    except ParseError as e:
        return None, str(e)


class RecordingModel:
    """ActionModel wrapper recording each decide() call as a contract Step."""

    def __init__(self, inner: Any, url_of: Optional[Callable[[], str]] = None):
        self.inner = inner
        self.output_format = getattr(inner, "output_format", "gbnf")
        self.steps: List[Step] = []
        self.url_of = url_of or (lambda: "")
        self.errors: List[str] = []

    @property
    def usage(self) -> Optional[dict]:
        return getattr(self.inner, "usage", None)

    def decide(self, system: str, user: str, grammar: Optional[str] = None) -> str:
        t0 = time.monotonic()
        try:
            url = self.url_of() or ""
        except Exception:
            url = ""
        try:
            reply = self.inner.decide(system, user, grammar)
        except Exception as e:
            self.errors.append(f"{type(e).__name__}: {e}"[:300])
            raise
        ms = (time.monotonic() - t0) * 1000
        act, _err = parse_reply(reply, self.output_format)
        self.steps.append(Step(i=len(self.steps) + 1, system=system, user=user, grammar=grammar, reply=reply,
                               action=act.show() if act is not None else "", valid=act is not None,
                               url=url, ms=round(ms, 2)))
        return reply


# ------------------------------------------------------------------ oracle --

def _obs(user: str) -> str:
    return user.split("<observation", 1)[-1]


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def find_control(user: str, role: Optional[str], name: str, context: Optional[str] = None,
                 dialog: Optional[bool] = None) -> Optional[int]:
    """Number of the unique control in the prompt's observation with this (role, name[, context,
    dialog]); exact, case-insensitive, whitespace-normalised (same approach as ScriptedModel)."""
    roles = ROLE_ALIASES.get(role or "any", (role,)) if role else None
    norm = lambda s: " ".join(str(s).split()).lower()  # noqa: E731
    hits = []
    for m in _CTRL_LINE.finditer(_obs(user)):
        if roles is not None and m[2] not in roles:
            continue
        nm = m[3].replace('\\"', '"')
        if norm(nm) != norm(name):
            continue
        rest = m[4]
        if context is not None:
            cm = re.search(r'\(in "((?:[^"\\]|\\.)*)"\)', rest)
            ctx = cm.group(1) if cm else ""
            if norm(ctx) != norm(context) and not (not ctx and norm(context) == norm(nm)):
                continue
        if dialog is not None and (("(dialog)" in rest) != bool(dialog)):
            continue
        hits.append(int(m[1]))
    return hits[0] if len(hits) == 1 else (None if not hits else -len(hits))


class OracleModel:
    """Plays source.oracle(task) through the product prompt. Each semantic step is resolved
    against the current observation; a step whose control is not in the visible window but the
    list continues makes it scroll(down) first. Anything it cannot resolve becomes
    needs_user("oracle: ...") so admission fails with a readable reason."""
    output_format = "gbnf"

    def __init__(self, steps: Sequence[dict]):
        self.steps = [dict(s) for s in steps]
        self.i = 0
        self.failures: List[str] = []
        self.scrolls = 0

    def decide(self, system: str, user: str, grammar: Optional[str] = None) -> str:
        if self.i >= len(self.steps):
            return 'needs_user("oracle: script exhausted")'
        s = self.steps[self.i]
        op = s.get("op")
        if op in ("done", "needs_user"):
            self.i += 1
            return f"{op}({_q(str(s.get('text') or ''))})"
        if op == "scroll":
            self.i += 1
            return f"scroll({s.get('direction', 'down')})"
        n = find_control(user, s.get("role"), str(s.get("name", "")), s.get("context"), s.get("dialog"))
        if n is None or n < 0:
            m = _SHOWING.search(_obs(user))
            if n is None and m and int(m[3]) < int(m[1]) and self.scrolls < 5:
                self.scrolls += 1
                return "scroll(down)"
            why = (f"oracle: {op} target {s.get('role')} {s.get('name')!r}"
                   + (f" in {s.get('context')!r}" if s.get("context") else "")
                   + (" is ambiguous" if n is not None else " is not in the observation"))
            self.failures.append(why)
            self.i = len(self.steps)
            return f"needs_user({_q(why[:190])})"
        self.scrolls = 0
        self.i += 1
        if op == "click":
            return f"click({n})"
        if op == "fill":
            v = str(s.get("value", ""))
            return f"fill({n}, {v if HANDLE_RE.match(v) else _q(v)})"
        if op == "select":
            return f"select({n}, {_q(str(s.get('option', '')))})"
        if op in ("copy", "copy_to_secret"):
            return f"copy_to_secret({n}, {s.get('secret') or s.get('to')})"
        self.failures.append(f"oracle: unknown op {op!r}")
        self.i = len(self.steps)
        return f"needs_user({_q('oracle: unknown op')})"


def oracle_to_script(steps: Sequence[dict]) -> List[str]:
    """Semantic oracle steps -> micro_agent.ScriptedModel lines (role-prefixed names)."""
    out = []
    for s in steps:
        op = s.get("op")
        role = s.get("role")
        ref = s.get("name", "")
        if role in ("link", "button", "textbox", "password", "radiogroup", "combobox", "secret_text"):
            ref = f"{role}:{ref}"
        if s.get("dialog"):
            ref += "@dialog"
        if op == "click":
            out.append(f"click {_q(ref)}")
        elif op == "fill":
            v = str(s.get("value", ""))
            out.append(f"fill {_q(ref)} {v if HANDLE_RE.match(v) else _q(v)}")
        elif op == "select":
            out.append(f"select {_q(ref)} {_q(str(s.get('option', '')))}")
        elif op in ("copy", "copy_to_secret"):
            out.append(f"copy {_q(ref)} {s.get('secret') or s.get('to')}")
        elif op == "scroll":
            out.append(f"scroll {s.get('direction', 'down')}")
        else:
            out.append(f"{op} {_q(str(s.get('text') or ''))}")
    return out


# ------------------------------------------------------------- local model --

class SampledLocalModel(LocalGBNFModel):
    """LocalGBNFModel with sampling controls (llama-server /v1/chat/completions)."""

    def __init__(self, *a, temperature: float = 0.0, top_p: Optional[float] = None, seed: Optional[int] = None,
                 max_tokens: int = 200, **kw):
        super().__init__(*a, **kw)
        self.temperature, self.top_p, self.seed, self.max_tokens = temperature, top_p, seed, max_tokens

    def decide(self, system: str, user: str, grammar: Optional[str] = None) -> str:
        body: Dict[str, Any] = {"model": self.model, "temperature": self.temperature, "max_tokens": self.max_tokens,
                                "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": user}],
                                "chat_template_kwargs": {"enable_thinking": False}}
        if self.top_p is not None:
            body["top_p"] = self.top_p
        if self.seed is not None:
            body["seed"] = self.seed
        if grammar:
            body["grammar"] = grammar
        web_tools.check_loopback(self.base)
        req = urllib.request.Request(self.base + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        u = data.get("usage") or {}
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
        self.usage["completion_tokens"] += int(u.get("completion_tokens") or 0)
        t = data.get("timings") or {}
        self.usage["prompt_ms"] += float(t.get("prompt_ms") or 0.0)
        self.usage["predicted_ms"] += float(t.get("predicted_ms") or 0.0)
        self.usage["prompt_n"] += int(t.get("prompt_n") or 0)
        self.usage["predicted_n"] += int(t.get("predicted_n") or 0)
        self.usage["cached_tokens"] += int(t.get("cache_n") or 0)
        return data["choices"][0]["message"]["content"]

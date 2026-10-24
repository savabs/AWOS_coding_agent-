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
    "secret_text": ("secret_text",), "tab": ("tab",), "clickable": ("button", "link", "tab"), "any": None,
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


# ------------------------------------------------------------- cloud model --

#: $ per 1M tokens (input, output), Anthropic first-party list prices (claude-api skill, 2026-10-06)
CLOUD_PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}
_SPEND = {"usd": 0.0}                          # per process; workers each get max_usd / workers

_FORMAT_RULE = ("\n\nReply format (strict): optionally ONE line starting with '# ' (at most 160 characters) "
                "with your reason, then exactly ONE action line, e.g. click(3) or fill(2, \"text\") or "
                "done(\"summary\"). No other text, no markdown.")


def normalize_text_reply(text: str) -> str:
    """Claude's reply -> the local model's GBNF text form: an optional '# reason' line + one action line.
    Returns '' when no line parses as an action (MicroAgent then records an invalid step).
    A 'reason: ...' line is accepted as the reason too; an empty reason is dropped (the grammar's
    reason rule needs 1-160 characters after '# ')."""
    from scaffold.agent.desktop.micro_agent import ParseError, parse_text_action
    reason, lines = "", [x.strip().strip("`") for x in (text or "").splitlines() if x.strip().strip("`")]
    for ln in lines:
        note = ln.lstrip("#").strip() if ln.startswith("#") else (
            ln.split(":", 1)[1].strip() if ln.lower().startswith("reason:") else None)
        if note is not None:
            if note and not reason:
                reason = ("# " + note)[:162].rstrip()
            continue
        try:
            parse_text_action(ln)
        except ParseError:
            continue
        return f"{reason}\n{ln}" if reason else ln
    return ""


class CloudTextModel:
    """Anthropic Messages as a drop-in for the local model: the request carries MicroAgent's own system +
    user prompt (plus a strict format rule), and the reply is normalised to the same text action form the
    GBNF grammar produces, so a successful cloud episode is a training example in the exact local format.

    Thinking per current API rules: Opus 5.5 always thinks (effort controls depth); Sonnet 5.5 turns it off
    with between_tools; Haiku 5.5 accepts disabled. No temperature (rejected since 2026-10-21).
    Spend is capped per process by AWOS_CLOUD_MAX_USD (divided by the worker count by the runner)."""
    output_format = "gbnf"

    def __init__(self, model: Optional[str] = None, effort: Optional[str] = None, max_usd: Optional[float] = None):
        import os
        self.model = model or os.environ.get("AWOS_CLOUD_MODEL", "claude-sonnet-5-5")
        self.effort = effort or os.environ.get("AWOS_CLOUD_EFFORT", "low")
        self.max_usd = float(max_usd if max_usd is not None else os.environ.get("AWOS_CLOUD_MAX_USD") or 5.0)
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "usd": 0.0, "refusals": 0}
        self._client = None

    def _extra(self) -> dict:
        body: Dict[str, Any] = {"output_config": {"effort": self.effort}}
        if self.model.startswith("claude-sonnet-5-5"):
            body["thinking"] = {"type": "between_tools"}
        elif self.model.startswith("claude-haiku"):
            body["thinking"] = {"type": "disabled"}
        return body                                    # Opus 5.5: thinking always on, omit the param

    def decide(self, system: str, user: str, grammar: Optional[str] = None) -> str:
        if _SPEND["usd"] >= self.max_usd:
            raise RuntimeError(f"cloud budget reached (${_SPEND['usd']:.2f} >= ${self.max_usd:.2f})")
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(max_retries=6)
        resp = self._client.messages.create(model=self.model, max_tokens=4000, system=system + _FORMAT_RULE,
                                            messages=[{"role": "user", "content": user}],
                                            extra_body=self._extra())
        pin, pout = CLOUD_PRICES.get(self.model, (4.0, 20.0))
        cost = (resp.usage.input_tokens * pin + resp.usage.output_tokens * pout) / 1e6
        _SPEND["usd"] += cost
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += resp.usage.input_tokens
        self.usage["completion_tokens"] += resp.usage.output_tokens
        self.usage["usd"] = round(self.usage["usd"] + cost, 5)
        if resp.stop_reason == "refusal":
            self.usage["refusals"] += 1
            return 'needs_user("the model declined this step")'
        return normalize_text_reply("".join(getattr(b, "text", "") or "" for b in resp.content
                                            if getattr(b, "type", "") == "text"))


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

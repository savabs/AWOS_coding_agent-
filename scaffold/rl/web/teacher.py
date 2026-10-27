"""
teacher.py — a Claude teacher for the live-web harness: MicroAgent's own prompt and action contract,
answered by an Anthropic model, so a judged-successful episode is SFT data in the student's exact format.

    python -m scaffold.rl.web run --model anthropic:claude-haiku-5-5 --split train --tasks-json tasks.json \\
        --out runs/web/teach-s1/            # AWOS_TEACHER_MAX_USD caps the spend (split across workers)

Contract (same as the local model's):
  * request = MicroAgent's system prompt (+ a strict reply-format rule) and the step's user prompt, one
    turn; the reply is normalised to the GBNF text form (an optional '# reason' line + one action line,
    harness.models.normalize_text_reply);
  * the per-step GBNF grammar is enforced locally (``grammar_error``): the verb must be one the grammar
    offers, the control number one of the enumerated 1..n, a secret handle / copy target one it lists, a
    string <= 200 characters, the reason <= 160. A reply that fails gets ONE repair call (the rejected
    reply and the reason, then "reply again"); if the repair fails too the normalised reply is returned
    and MicroAgent records an invalid step, exactly as for the local model;
  * what is recorded (RecordingModel) is MicroAgent's system/user and the final reply: the format rule
    and the repair turn never reach the training rows.

Prompt caching: the system block carries cache_control. MicroAgent's SYSTEM + SYSTEM_WEB + the format
rule is ~1.9k characters (~500-600 tokens), right at Claude Haiku 5.5's 512-token minimum: when it is
below, the API silently does not cache (no error, no write premium); when it caches, reads bill at 0.1x.
The cost estimate (docs/specs/web_teacher.md) assumes no cache hit.

Spend: every call reserves its worst case (input estimate at 3 characters per token, as a cache write,
plus the full max_tokens of output) against a per-process ``Budget`` (scaffold/rl/web_judge/cost.py)
before it is sent, and settles the billed cost from the response's usage. The cap is AWOS_TEACHER_MAX_USD
(default 1.0) divided by the worker count (the workflow divides the run's MAX_USD by the shard count
first). A refused reservation raises "cloud budget reached ...": the episode is an infra error (rerun on
resume) and the runner stops dispatching.

Dry run: AWOS_TEACHER_DRY=1 swaps the Anthropic client for ``DryClient`` (no network, no key): it
answers from AWOS_TEACHER_DRY_REPLIES (a JSON list, cycled; default one needs_user) and reports usage
estimated from the request size, so the whole pipeline, including the cap, runs for $0.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from scaffold.agent.desktop.micro_agent import IDENT_RE, ParseError, parse_text_action
from scaffold.rl.harness.models import _FORMAT_RULE, normalize_text_reply
from scaffold.rl.web_judge.cost import Budget, CapReached

PREFIX = "anthropic:"
DEFAULT_TEACHER = "claude-haiku-5-5"
MAX_USD_ENV = "AWOS_TEACHER_MAX_USD"
DRY_ENV = "AWOS_TEACHER_DRY"
DRY_REPLIES_ENV = "AWOS_TEACHER_DRY_REPLIES"
#: one action line + a <= 160-char reason fits in ~100 tokens; the cap reserves all of it
MAX_TOKENS = 300
#: conservative characters per token for the worst-case reservation (measured: ~3.2 chars per Qwen token
#: on these prompts; Claude's tokenizer is in the same range)
RESERVE_CHARS_PER_TOKEN = 3.0

#: USD per 1M tokens: (input, 5-minute cache write = 1.25x, cache read = 0.1x, output). First-party list
#: prices from the claude-api skill (cached 2026-10-06); Claude Haiku 5.5's apply to prompts up to 100K
#: tokens (ours are < 5K). Claude Opus 5.5's cache read is $0.20.
PRICES: Dict[str, Tuple[float, float, float, float]] = {
    "claude-haiku-5-5": (0.10, 0.125, 0.01, 0.50),
    "claude-sonnet-5-5": (2.00, 2.50, 0.20, 10.00),
    "claude-opus-5-5": (4.00, 5.00, 0.20, 20.00),
}

REPAIR = ("Your reply was rejected: {err}. Reply again with exactly one action that the format allows "
          "for the page above: optionally one '# ' reason line (at most 160 characters), then one action "
          "line. No other text.")


def is_teacher(kind: str) -> bool:
    return (kind or "").startswith(PREFIX)


def teacher_model_id(kind: str) -> str:
    m = (kind or "")[len(PREFIX):].strip() if is_teacher(kind) else ""
    m = m or DEFAULT_TEACHER
    if m not in PRICES:
        raise ValueError(f"no price for {m!r}: add it to scaffold/rl/web/teacher.py PRICES (with a source) "
                         f"before running it as a teacher; known: {sorted(PRICES)}")
    return m


def call_usd(model: str, input_tokens: int, output_tokens: int, cache_write: int = 0, cache_read: int = 0) -> float:
    """Billed USD of one call from the API's usage numbers (input_tokens excludes cached tokens)."""
    pin, pwrite, pread, pout = PRICES[model]
    return (input_tokens * pin + cache_write * pwrite + cache_read * pread + output_tokens * pout) / 1e6


def worst_case_usd(model: str, chars: int, max_tokens: int = MAX_TOKENS) -> float:
    """What one call may cost at most: every input token billed as a cache write, all of max_tokens out."""
    toks = math.ceil(chars / RESERVE_CHARS_PER_TOKEN) + 16
    return call_usd(model, 0, max_tokens, cache_write=toks)


# ------------------------------------------------------------------ grammar --

_ALT = re.compile(r'"((?:[^"\\]|\\.)*)"')
_VERB_RULE = {"click": "click", "fill": "fill", "select": "select", "fill_enter": "fillenter",
              "copy_to_secret": "copy", "scroll": "scroll", "done": "done", "needs_user": "needs",
              "go_back": "goback", "goto": "goto"}


def _rules(grammar: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for ln in (grammar or "").splitlines():
        name, sep, body = ln.partition(" ::= ")
        if sep:
            out[name.strip()] = body.strip()
    return out


def _alts(body: str) -> List[str]:
    return [m.group(1).replace('\\"', '"').replace("\\\\", "\\") for m in _ALT.finditer(body or "")]


def _names(body: str) -> List[str]:
    """The secret names a handle / copy rule enumerates (its identifier literals)."""
    return [a for a in _alts(body) if IDENT_RE.match(a)]


def grammar_error(reply: str, grammar: Optional[str]) -> str:
    """'' when ``reply`` is in the language of MicroAgent's per-step GBNF ``grammar`` (micro_agent.build_grammar),
    else why not. Checked by parsing (parse_text_action) and then against what the grammar enumerates."""
    if not (reply or "").strip():
        return "no action line"
    try:
        act = parse_text_action(reply)
    except ParseError as e:
        return str(e)
    lines = [ln.strip() for ln in reply.strip().splitlines() if ln.strip()]
    notes = [ln for ln in lines if ln.startswith("#")]
    if len(notes) > 1 or (notes and lines[0] != notes[0]):
        return "at most one '# reason' line, before the action"
    if notes and not (notes[0].startswith("# ") and 1 <= len(notes[0]) - 2 <= 160):
        return "the reason line must be '# ' and 1-160 characters"
    if not grammar:
        return ""
    rules = _rules(grammar)
    verb = act.verb.value
    offered = {a.strip() for a in rules.get("action", "").split("|")}
    if _VERB_RULE.get(verb, verb) not in offered:
        return f"{verb} is not offered on this step"
    if act.n is not None:
        nums = {int(x) for x in _alts(rules.get("num", "")) if x.isdigit()}
        if act.n not in nums:
            return f"control {act.n} is not on the page (1-{max(nums) if nums else 0})"
    for s in (act.value, act.option, act.text):
        if isinstance(s, str) and not s.startswith("{{secret:") and len(s) > 200:
            return "a quoted string is longer than 200 characters"
    if isinstance(act.value, str) and act.value.startswith("{{secret:") and verb in ("fill", "fill_enter"):
        name = act.value[len("{{secret:"):-2]
        if name not in _names(rules.get("handle", "")):
            return f"secret handle {name!r} is not offered"
    if verb == "copy_to_secret" and act.name not in _names(rules.get("copy", "")):
        return f"secret name {act.name!r} is not offered"
    if verb == "goto" and (not re.match(r"^https?://", act.value or "") or re.search(r'[\s"\\]', act.value or "")
                           or len(act.value or "") > 288):
        return "goto needs an http(s) URL without spaces or quotes"
    return ""


# ------------------------------------------------------------------ clients --

class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int, cache_creation_input_tokens: int = 0,
                 cache_read_input_tokens: int = 0):
        self.input_tokens, self.output_tokens = input_tokens, output_tokens
        self.cache_creation_input_tokens, self.cache_read_input_tokens = cache_creation_input_tokens, cache_read_input_tokens


class _Block:
    def __init__(self, text: str):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self, text: str, usage: _Usage, stop_reason: str = "end_turn"):
        self.content, self.usage, self.stop_reason = [_Block(text)], usage, stop_reason


class DryClient:
    """Stands in for anthropic.Anthropic() in a dry run: no network. Replies come from ``replies`` (cycled),
    usage is estimated at 4 characters per token so the cost accounting and the cap are exercised."""

    def __init__(self, replies: Optional[List[str]] = None):
        self.replies = list(replies or ['needs_user("dry run: no model was called")'])
        self.i = 0
        self.requests: List[dict] = []
        self.messages = self

    def create(self, **kw) -> _Resp:
        self.requests.append(kw)
        text = self.replies[self.i % len(self.replies)]
        self.i += 1
        chars = sum(len(b.get("text", "")) for b in kw.get("system") or []) + \
            sum(len(m["content"]) if isinstance(m["content"], str) else 0 for m in kw.get("messages") or [])
        return _Resp(text, _Usage(math.ceil(chars / 4), math.ceil(len(text) / 4) + 2))


def dry_client_from_env() -> DryClient:
    raw = os.environ.get(DRY_REPLIES_ENV, "").strip()
    return DryClient(json.loads(raw) if raw else None)


# ------------------------------------------------------------------ budget --

_LOCK = threading.Lock()
_BUDGET: Dict[str, Budget] = {}


def process_budget(max_usd: float) -> Budget:
    """One Budget per process (a model object is made per task; the cap spans them all)."""
    with _LOCK:
        b = _BUDGET.get("b")
        if b is None or b.max_usd != max_usd:
            b = _BUDGET["b"] = Budget(max_usd)
        return b


def reset_budget() -> None:
    with _LOCK:
        _BUDGET.clear()


# ------------------------------------------------------------------ model --

class TeacherModel:
    """micro_agent.ActionModel (output_format "gbnf") answered by an Anthropic model; see the module docstring."""
    output_format = "gbnf"

    def __init__(self, model: str = DEFAULT_TEACHER, *, max_usd: Optional[float] = None, client: Any = None,
                 effort: str = "low", max_tokens: int = MAX_TOKENS, cache: bool = True,
                 budget: Optional[Budget] = None):
        self.model = teacher_model_id(PREFIX + model) if not is_teacher(model) else teacher_model_id(model)
        self.effort, self.max_tokens, self.cache = effort, int(max_tokens), cache
        cap = float(max_usd if max_usd is not None else os.environ.get(MAX_USD_ENV) or 1.0)
        self.budget = budget or process_budget(cap)
        self.dry = os.environ.get(DRY_ENV, "").strip() not in ("", "0")
        self._client = client if client is not None else (dry_client_from_env() if self.dry else None)
        self.usage = {"calls": 0, "repairs": 0, "repaired": 0, "invalid": 0, "prompt_tokens": 0,
                      "completion_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0, "usd": 0.0,
                      "refusals": 0}

    # Thinking per current API rules (claude-api skill): Haiku 5.5 accepts disabled at effort <= high; Sonnet
    # 5.5 turns it off with between_tools; Opus 5.5 always thinks (effort is the only control). No sampling
    # parameters (rejected since 2026-10-21): the teacher is not deterministic, the episode records it.
    def _extra(self) -> dict:
        body: Dict[str, Any] = {"output_config": {"effort": self.effort}}
        if self.model.startswith("claude-haiku"):
            body["thinking"] = {"type": "disabled"}
        elif self.model.startswith("claude-sonnet-5-5"):
            body["thinking"] = {"type": "between_tools"}
        return body

    def _client_(self):
        if self._client is None:
            import anthropic  # noqa: PLC0415 - only real (paid) runs need the SDK
            self._client = anthropic.Anthropic(max_retries=6)
        return self._client

    def _call(self, system: str, messages: List[dict]) -> str:
        chars = len(system) + sum(len(m["content"]) for m in messages)
        try:
            held = self.budget.reserve(worst_case_usd(self.model, chars, self.max_tokens))
        except CapReached as e:
            raise RuntimeError(f"cloud budget reached ({e})") from None
        block: Dict[str, Any] = {"type": "text", "text": system}
        if self.cache:
            block["cache_control"] = {"type": "ephemeral"}
        try:
            resp = self._client_().messages.create(model=self.model, max_tokens=self.max_tokens, system=[block],
                                                   messages=messages, extra_body=self._extra())
        except BaseException:
            self.budget.settle(held, 0.0)
            raise
        u = resp.usage
        pin, pout = int(u.input_tokens or 0), int(u.output_tokens or 0)
        cw = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        cr = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        cost = call_usd(self.model, pin, pout, cw, cr)
        self.budget.settle(held, cost, pin + cw + cr, pout, cr)
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += pin + cw + cr
        self.usage["completion_tokens"] += pout
        self.usage["cache_write_tokens"] += cw
        self.usage["cache_read_tokens"] += cr
        self.usage["usd"] = round(self.usage["usd"] + cost, 6)
        if resp.stop_reason == "refusal":
            self.usage["refusals"] += 1
            return 'needs_user("the model declined this step")'
        return "".join(getattr(b, "text", "") or "" for b in resp.content if getattr(b, "type", "") == "text")

    def decide(self, system: str, user: str, grammar: Optional[str] = None) -> str:
        sysp = system + _FORMAT_RULE
        first = [{"role": "user", "content": user}]
        raw = self._call(sysp, first)
        reply = normalize_text_reply(raw)
        err = grammar_error(reply, grammar) if reply else "no line is an action"
        if not err:
            return reply
        self.usage["repairs"] += 1
        msgs = first + [{"role": "assistant", "content": (raw or "").strip() or "(empty reply)"},
                        {"role": "user", "content": REPAIR.format(err=err)}]
        fixed = normalize_text_reply(self._call(sysp, msgs))
        if fixed and not grammar_error(fixed, grammar):
            self.usage["repaired"] += 1
            return fixed
        self.usage["invalid"] += 1
        return fixed or reply                     # MicroAgent records the invalid step


def make_teacher(kind: str, workers: int = 1, client: Any = None) -> TeacherModel:
    """runner.make_model's branch for --model anthropic:<id>: the process gets AWOS_TEACHER_MAX_USD / workers."""
    total = float(os.environ.get(MAX_USD_ENV) or 1.0)
    return TeacherModel(teacher_model_id(kind), max_usd=total / max(1, int(workers or 1)), client=client)

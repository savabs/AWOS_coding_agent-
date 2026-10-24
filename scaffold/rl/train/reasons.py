"""
reasons.py — one short, consistent reason line per training step, written by Claude Haiku from the
KNOWN right action (NEXT_WEEK_PLAN item 0.5). The model is told the action; it only writes why.

    python -m scaffold.rl.train.reasons --rows OUT/train.jsonl --rows OUT/valid.jsonl \\
        --cache ~/.cache/awos-models/rl/reasons.jsonl --dry-run        # prompt + token/cost estimate, no call
    ... same without --dry-run [--mode batch|sync] [--max-usd 1.50] [--limit N]   # the owner runs this

Then rebuild with ``dataset --reasons CACHE``: every row whose (user prompt, action line) has a cached
reason gets the reply ``# <reason>\\n<action>``, the optional reason line MicroAgent's GBNF grammar
already accepts (``reason ::= "# " [^\\n]{1,160} "\\n"``) and the system prompt invites.

Request: model claude-haiku-5-5, thinking disabled, effort low, no temperature (sampling params are
rejected since 2026-10-21), max_tokens 60. Input = the agent's exact user prompt (goal, history,
observation) + the right action + the control line it targets. Output is sanitised to one line of at
most MAX_REASON_CHARS (no action syntax, no control numbers, no newlines); a reply that fails the
check is cached as rejected (reason "") so it is never paid for twice.

Spend: rows are deduplicated by key and only keys missing from the cache are sent. Before any call the
WORST case (input estimated at chars/2.5 tokens, every reply at max_tokens) must be under --max-usd
(default $1.50) or the run refuses; sync mode also stops when actual spend reaches the cap. Batch mode
(default, 50% price) writes the batch id next to the cache before polling, so an interrupted run
collects the same batch instead of submitting a second one. Results are appended to the cache as they
arrive. The cache is the only output; it never holds secrets beyond what the prompt rows hold.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from scaffold.rl.train.clean import action_line

MODEL = "claude-haiku-5-5"
PRICE_PER_MTOK = (0.10, 0.50)          # input, output $ / 1M tokens (claude-api skill, cached 2026-10-06)
BATCH_DISCOUNT = 0.5
MAX_TOKENS = 60
DEFAULT_MAX_USD = 1.50
MAX_REASON_CHARS = 120                 # grammar allows 160; keep the target short
CHARS_PER_TOKEN = 2.5                  # conservative: real AX text is ~3-4 chars/token

SYSTEM = """You write the one-line reason a careful web agent states before an action.
You are given the agent's goal and the page it sees, and the action that is known to be correct.
Write WHY that action is the right next step. Do not judge or change the action.

Style (every reason must follow it):
- One plain sentence, at most 100 characters, present tense, no "I", no quotes, no markdown.
- Name the on-page label or value it relies on, and what it achieves for the goal.
- Never write the action syntax (no click(...), fill(...)) and never cite control numbers.
- done: say what on the page shows the goal is complete, or give the answer the page shows.
- needs_user: say what blocks the agent (sign-in, code, CAPTCHA, ambiguity).
- scroll: say what is not visible yet.
- Never repeat a password, code or key.

Examples:
Open L-694, the lead the goal names.
Search people for Haddad to find the new owner.
Save the assignment so Ruth Haddad becomes the owner.
The lead now shows Assigned to: Ruth Haddad, so the goal is done.
The page shows the policy keeps records for 3 years.
The session expired and signing in needs the owner.
The Save button is below the visible controls.

Reply with the reason sentence only."""

_NUM = re.compile(r"\((\d{1,3})")
_ACTION_SYNTAX = re.compile(r"\b(click|fill|select|scroll|done|needs_user|copy_to_secret)\s*\(", re.I)
_CTRL_NUM = re.compile(r"\[\d+\]|\bcontrol\s*#?\d+\b", re.I)


def row_key(user: str, reply: str) -> str:
    """Cache key: the exact user prompt + the action line (system text and any old reason ignored)."""
    return hashlib.sha256(((user or "").strip() + "\n\x00" + action_line(reply)).encode()).hexdigest()[:40]


def control_line(user: str, action: str) -> str:
    m = _NUM.search(action)
    if not m:
        return ""
    hit = re.search(r"^\[%s\] .*$" % m.group(1), user or "", re.M)
    return hit.group(0) if hit else ""


def build_prompt(user: str, action: str) -> str:
    ctl = control_line(user, action)
    return ("<agent_prompt>\n" + (user or "").strip() + "\n</agent_prompt>\n\n"
            f"Known correct next action: {action}\n"
            + (f"It targets this control: {ctl}\n" if ctl else "")
            + "Write the reason line.")


def sanitize(text: str) -> str:
    """Model reply -> one reason line, or '' if it does not meet the style (caller caches '' as rejected)."""
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    s = lines[0].strip().strip("`").strip()
    s = re.sub(r"^(#+\s*|reason\s*:\s*)", "", s, flags=re.I).strip().strip('"\'').strip()
    s = re.sub(r"\s+", " ", s)
    if not s or _ACTION_SYNTAX.search(s) or _CTRL_NUM.search(s) or "{{secret" in s:
        return ""
    if len(s) > MAX_REASON_CHARS:
        cut = s[:MAX_REASON_CHARS].rsplit(" ", 1)[0].rstrip(",;:- ")
        s = cut if len(cut) >= 20 else ""
    return s


def with_reason(reply: str, reason: str) -> str:
    """The training target: '# reason' + the action line (any earlier reason line is replaced)."""
    act = action_line(reply)
    return f"# {reason}\n{act}" if reason else act


# ------------------------------------------------------------------ inputs --

@dataclass
class Item:
    key: str
    user: str
    action: str
    task_id: str = ""

    @property
    def prompt(self) -> str:
        return build_prompt(self.user, self.action)


def read_rows(paths: Iterable[Path]) -> List[dict]:
    rows = []
    for p in paths:
        with open(Path(p).expanduser()) as fh:
            rows += [json.loads(ln) for ln in fh if ln.strip()]
    return rows


def items_from_rows(rows: Iterable[dict]) -> List[Item]:
    out: Dict[str, Item] = {}
    for r in rows:
        msgs = {m["role"]: m["content"] for m in r["messages"]}
        k = row_key(msgs["user"], msgs["assistant"])
        if k not in out:
            out[k] = Item(k, msgs["user"], action_line(msgs["assistant"]), (r.get("meta") or {}).get("task_id", ""))
    return list(out.values())


def load_cache(path: Path) -> Dict[str, dict]:
    cache: Dict[str, dict] = {}
    p = Path(path).expanduser()
    if p.exists():
        for ln in p.read_text().splitlines():
            try:
                d = json.loads(ln)
            except ValueError:
                continue                                       # torn last line
            cache[d["key"]] = d
    return cache


def reasons_map(path: Path) -> Dict[str, str]:
    """key -> reason, accepted entries only (what dataset --reasons merges)."""
    return {k: v["reason"] for k, v in load_cache(path).items() if v.get("reason")}


def _append(path: Path, entries: Iterable[dict]) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ costing --

def estimate(items: List[Item], batch: bool = True) -> Dict[str, Any]:
    chars = sum(len(SYSTEM) + len(i.prompt) for i in items)
    tin = int(chars / CHARS_PER_TOKEN) + 8 * len(items)              # + per-message overhead
    tout = MAX_TOKENS * len(items)
    usd = (tin * PRICE_PER_MTOK[0] + tout * PRICE_PER_MTOK[1]) / 1e6
    return {"requests": len(items), "input_tokens_est": tin, "output_tokens_max": tout,
            "usd_sync_worst": round(usd, 6), "usd_batch_worst": round(usd * BATCH_DISCOUNT, 6),
            "usd_worst": round(usd * (BATCH_DISCOUNT if batch else 1.0), 6)}


def usage_usd(input_tokens: int, output_tokens: int, batch: bool) -> float:
    usd = (input_tokens * PRICE_PER_MTOK[0] + output_tokens * PRICE_PER_MTOK[1]) / 1e6
    return usd * (BATCH_DISCOUNT if batch else 1.0)


def request_params(item: Item) -> Dict[str, Any]:
    return {"model": MODEL, "max_tokens": MAX_TOKENS, "system": SYSTEM,
            "thinking": {"type": "disabled"}, "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": item.prompt}]}


def _text(msg) -> str:
    return "".join(getattr(b, "text", "") or "" for b in getattr(msg, "content", []) if getattr(b, "type", "") == "text")


def _entry(item: Item, raw: str, usage=None, status: str = "ok") -> dict:
    reason = sanitize(raw) if status == "ok" else ""
    return {"key": item.key, "reason": reason, "action": item.action, "task_id": item.task_id, "model": MODEL,
            "status": status if (reason or status != "ok") else "rejected", "raw": (raw or "")[:300],
            "in": int(getattr(usage, "input_tokens", 0) or 0), "out": int(getattr(usage, "output_tokens", 0) or 0)}


# ------------------------------------------------------------------ runners --

def run_sync(client, items: List[Item], cache: Path, max_usd: float) -> Dict[str, Any]:
    spent, done = 0.0, 0
    worst_one = lambda it: estimate([it], batch=False)["usd_worst"]  # noqa: E731
    for it in items:
        if spent + worst_one(it) > max_usd:
            break
        msg = client.messages.create(**request_params(it))
        e = _entry(it, _text(msg), getattr(msg, "usage", None))
        spent += usage_usd(e["in"], e["out"], batch=False)
        _append(cache, [e])
        done += 1
    return {"sent": done, "usd": round(spent, 6), "stopped_at_cap": done < len(items)}


def run_batch(client, items: List[Item], cache: Path, poll_s: float = 60.0, max_wait_s: float = 26 * 3600,
              sleep=time.sleep) -> Dict[str, Any]:
    state_path = Path(str(Path(cache).expanduser()) + ".batch.json")
    by_key = {it.key: it for it in items}
    if state_path.exists():                                   # resume: never submit the same work twice
        state = json.loads(state_path.read_text())
    else:
        reqs = [{"custom_id": it.key, "params": request_params(it)} for it in items]
        b = client.messages.batches.create(requests=reqs)
        state = {"batch_id": b.id, "keys": list(by_key)}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state))
    bid = state["batch_id"]
    waited = 0.0
    while client.messages.batches.retrieve(bid).processing_status != "ended":
        if waited >= max_wait_s:
            return {"batch_id": bid, "status": "still running; rerun the same command to collect"}
        sleep(poll_s)
        waited += poll_s
    entries, spent = [], 0.0
    for res in client.messages.batches.results(bid):
        it = by_key.get(res.custom_id) or Item(res.custom_id, "", "")
        if res.result.type == "succeeded":
            e = _entry(it, _text(res.result.message), getattr(res.result.message, "usage", None))
            spent += usage_usd(e["in"], e["out"], batch=True)
            entries.append(e)
        # errored / expired / canceled: not cached, so a later run retries just those keys
    _append(cache, entries)
    state_path.unlink()
    return {"batch_id": bid, "succeeded": len(entries), "usd": round(spent, 6),
            "accepted": sum(1 for e in entries if e["reason"])}


def plan(rows_paths: List[Path], cache: Path, limit: int = 0) -> Tuple[List[Item], Dict[str, Any]]:
    items = items_from_rows(read_rows(rows_paths))
    have = load_cache(cache)
    todo = [it for it in items if it.key not in have]
    if limit:
        todo = todo[:limit]
    return todo, {"rows_unique": len(items), "cached": len(items) - len([i for i in items if i.key not in have]),
                  "to_send": len(todo)}


def main(argv: Optional[List[str]] = None, client=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.train.reasons", description=__doc__.split("\n\n")[0])
    ap.add_argument("--rows", action="append", required=True, type=Path, help="dataset JSONL (repeatable)")
    ap.add_argument("--cache", required=True, type=Path, help="reasons JSONL cache (appended)")
    ap.add_argument("--dry-run", action="store_true", help="print a prompt and the cost estimate; no API call")
    ap.add_argument("--mode", choices=["batch", "sync"], default="batch")
    ap.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD, help="hard spend cap (default 1.50)")
    ap.add_argument("--limit", type=int, default=0, help="send at most N uncached rows")
    ap.add_argument("--poll", type=float, default=60.0)
    a = ap.parse_args(argv)
    todo, counts = plan(a.rows, a.cache, a.limit)
    est = estimate(todo, batch=a.mode == "batch")
    print(json.dumps({**counts, **est, "model": MODEL, "mode": a.mode, "max_usd": a.max_usd}, indent=1))
    if a.dry_run:
        if todo:
            print("\n--- system ---\n" + SYSTEM + "\n--- user (first uncached row) ---\n" + todo[0].prompt)
        print(f"\nDRY RUN: no request sent. Worst case ${est['usd_worst']:.4f} vs cap ${a.max_usd:.2f}"
              + (" -> would REFUSE (use --limit)" if est["usd_worst"] > a.max_usd else " -> within cap"))
        return 0
    if not todo:
        print("nothing to send: every row is cached")
        return 0
    if est["usd_worst"] > a.max_usd:
        print(f"REFUSING: worst case ${est['usd_worst']:.4f} exceeds --max-usd {a.max_usd:.2f}; "
              "use --limit or raise the cap", file=sys.stderr)
        return 3
    if client is None:
        import anthropic  # noqa: PLC0415 - optional dependency, only for a real run
        client = anthropic.Anthropic()
    out = (run_batch(client, todo, a.cache, poll_s=a.poll) if a.mode == "batch"
           else run_sync(client, todo, a.cache, a.max_usd))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

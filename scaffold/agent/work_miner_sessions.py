"""
work_miner_sessions.py — read Claude Code transcripts as sessions and measure
how much babysitting the agents need.

A top-level transcript (~/.claude/projects/<project>/<session>.jsonl) mixes the
owner's typed prompts with agent turns, tool results, background-task
notifications, peer (agent-to-agent) messages and system text. This module:

  * picks out the prompts the OWNER actually sent (`is_owner_prompt`);
  * rebuilds each session as a timeline and, for every owner prompt, measures
    the gap since the previous prompt, how long the agent ran before the owner
    stepped in again, how long the agent then sat idle, and what preceded the
    prompt (a key/auth error, a pending background task, an interrupted run,
    a question from the agent, a long silence, or a normally finished turn);
  * aggregates per session, overall and per ISO week, so the share of
    supervision prompts (continue / status / keys) can be tracked over time.
    That share is the number the supervision autopilot should push down.

Prompt text is held in memory only so the caller can label it; nothing here
writes it anywhere.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Callable, Iterable, Optional

_NOISE = re.compile(
    r"^\s*(<command-|<local-command|<system-reminder|Caveat:|\[Request interrupted|"
    r"<task-notification|This session is being continued|<bash-|<user-memory|\[Image)", re.I)

# Key / auth / quota trouble, in tool output, API errors or agent text.
_KEY_ERROR = re.compile(
    r"invalid[ _-]?(x-)?api[ _-]?key|authentication[_ ]error|\b401\b[^\n]{0,40}(unauthori|invalid)|"
    r"credit balance (is )?too low|insufficient[_ ]quota|quota (exceeded|exhausted)|"
    r"\brate[_ ]limit(ed)?\b|\b429\b[^\n]{0,30}(too many|rate)|api[ _-]?key[^\n]{0,20}(missing|not set|not found|expired|revoked|invalid)|"
    r"(OPENROUTER|ANTHROPIC|OPENAI)_API_KEY[^\n]{0,30}(not set|missing|empty|unset)|no api key", re.I)
_QUESTION = re.compile(r"(\?\s*$|\b(want me to|should i|shall i|would you like|do you want|let me know (if|whether|which))\b)", re.I)

LONG_SILENCE_S = 15 * 60
ACTIVE_GAP_S = 10 * 60          # agent events closer than this count as active time
BG_EXPIRE_S = 12 * 3600         # a launch never reported done stops counting as pending
BG_TOOLS = {"Agent", "Task"}
_NOTE_DONE = re.compile(r"<status>\s*(completed|failed|stopped|killed|error|cancelled)", re.I)
_NOTE_TOOL_ID = re.compile(r"<tool-use-id>\s*([^<\s]+)")

PRECEDERS = ("key/auth error", "background task pending", "interrupted mid-run",
             "agent asked a question", "long silence", "agent finished turn", "session start")


def _ts(rec: dict) -> Optional[datetime]:
    s = rec.get("timestamp")
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _content(rec: dict):
    return (rec.get("message") or {}).get("content")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _tool_results_text(content) -> str:
    if not isinstance(content, list):
        return ""
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            c = b.get("content")
            out.append(c if isinstance(c, str) else _text_of(c))
    return "\n".join(out)


def is_owner_prompt(rec: dict) -> bool:
    """True for a prompt the owner sent: not a tool result, subagent turn,
    background-task notification, peer message, meta/system text."""
    if rec.get("type") != "user" or rec.get("isSidechain") or rec.get("isMeta"):
        return False
    origin = (rec.get("origin") or {}).get("kind")
    if origin not in (None, "human"):
        return False
    if rec.get("promptSource") == "system":
        return False
    content = _content(rec)
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return False
    text = _text_of(content).strip()
    return bool(text) and not _NOISE.match(text)


@dataclass
class OwnerPrompt:
    session: str
    project: str
    ts: Optional[datetime]
    text: str                       # in memory only; never persisted raw
    source: str = ""                # promptSource (typed / suggestion_accepted / sdk / queued)
    gap_s: Optional[float] = None   # since previous owner prompt in the session
    agent_run_s: Optional[float] = None   # prev prompt -> last agent activity
    agent_active_s: float = 0.0           # active agent time in that window (gaps > 10 min dropped)
    idle_s: Optional[float] = None        # last agent activity -> this prompt
    preceded_by: str = "session start"
    flags: list = field(default_factory=list)
    family: str = ""
    supervision: bool = False


@dataclass
class Session:
    sid: str
    project: str
    prompts: list = field(default_factory=list)
    start: Optional[datetime] = None
    end: Optional[datetime] = None


def project_name(dirname: str) -> str:
    return dirname.replace("-Users-becmachlean-", "").replace("-", "/")[:60]


def _load(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def parse_session(path: Path, seen: Optional[set] = None) -> Session:
    """Rebuild one top-level session; `seen` dedupes record uuids across files
    (resumed / relocated sessions copy earlier records)."""
    seen = seen if seen is not None else set()
    recs = []
    for i, rec in enumerate(_load(path)):
        if rec.get("isSidechain"):
            continue
        uid = rec.get("uuid")
        if uid:
            if uid in seen:
                continue
            seen.add(uid)
        ts = _ts(rec)
        if ts is not None:
            recs.append((ts, i, rec))
    recs.sort(key=lambda t: (t[0], t[1]))
    sess = Session(path.stem, project_name(path.parent.name))
    if recs:
        sess.start, sess.end = recs[0][0], recs[-1][0]

    prev_prompt_ts: Optional[datetime] = None
    last_agent_ts: Optional[datetime] = None
    last_stop: Optional[str] = None
    last_agent_text = ""
    window_key_error = False
    pending_bg: dict = {}            # tool_use id -> launch time
    active_s = 0.0
    mark: Optional[datetime] = None  # last prompt/agent event, for active time

    def agent_event(ts: datetime) -> None:
        nonlocal last_agent_ts, active_s, mark
        if mark is not None and prev_prompt_ts is not None:
            d = (ts - mark).total_seconds()
            if 0 <= d <= ACTIVE_GAP_S:
                active_s += d
        mark = ts
        last_agent_ts = ts

    for ts, _, rec in recs:
        typ = rec.get("type")
        content = _content(rec)
        if typ == "user" and is_owner_prompt(rec):
            p = OwnerPrompt(sess.sid, sess.project, ts, _text_of(content).strip(),
                            source=str(rec.get("promptSource") or ""))
            flags = []
            if prev_prompt_ts is not None:
                p.gap_s = (ts - prev_prompt_ts).total_seconds()
                if last_agent_ts is not None and last_agent_ts >= prev_prompt_ts:
                    p.agent_run_s = (last_agent_ts - prev_prompt_ts).total_seconds()
                    p.idle_s = (ts - last_agent_ts).total_seconds()
                else:
                    p.agent_run_s = 0.0
                    p.idle_s = p.gap_s
                if window_key_error:
                    flags.append("key/auth error")
                for k in [k for k, t0 in pending_bg.items() if (ts - t0).total_seconds() > BG_EXPIRE_S]:
                    pending_bg.pop(k)
                if pending_bg:
                    flags.append("background task pending")
                if last_stop == "tool_use" and (p.idle_s or 0) < LONG_SILENCE_S:
                    flags.append("interrupted mid-run")
                if last_agent_text and _QUESTION.search(last_agent_text[-400:]):
                    flags.append("agent asked a question")
                if (p.idle_s or 0) >= LONG_SILENCE_S:
                    flags.append("long silence")
                p.preceded_by = next((k for k in PRECEDERS if k in flags), "agent finished turn")
            p.flags = flags
            p.agent_active_s = active_s
            sess.prompts.append(p)
            prev_prompt_ts = ts
            active_s = 0.0
            mark = ts
            window_key_error = False
            last_stop = None
            last_agent_text = ""
            continue
        if typ == "assistant":
            agent_event(ts)
            msg = rec.get("message") or {}
            last_stop = msg.get("stop_reason") or last_stop
            text = _text_of(content)
            if text.strip():
                last_agent_text = text
                if _KEY_ERROR.search(text):
                    window_key_error = True
            if isinstance(content, list):
                for b in content:
                    if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                        continue
                    inp = b.get("input") or {}
                    if b.get("name") in BG_TOOLS or (isinstance(inp, dict) and inp.get("run_in_background")):
                        pending_bg[b.get("id") or f"anon-{len(pending_bg)}-{ts}"] = ts
        elif typ == "user":
            origin = (rec.get("origin") or {}).get("kind")
            text = _text_of(content)
            if origin == "task-notification" or text.lstrip().startswith("<task-notification"):
                agent_event(ts)
                if _NOTE_DONE.search(text):      # completion, not a progress event
                    m = _NOTE_TOOL_ID.search(text)
                    if m and m.group(1) in pending_bg:
                        pending_bg.pop(m.group(1))
                    elif pending_bg:
                        pending_bg.pop(min(pending_bg, key=pending_bg.get))
            res = _tool_results_text(content)
            if res:
                agent_event(ts)
                if isinstance(content, list):   # a synchronous Agent result is not pending
                    tur = rec.get("toolUseResult")
                    if isinstance(tur, dict) and tur.get("status") == "completed":
                        for b in content:
                            if isinstance(b, dict) and b.get("tool_use_id") in pending_bg:
                                pending_bg.pop(b["tool_use_id"])
                if _KEY_ERROR.search(res[:20000]):
                    window_key_error = True
        elif typ == "system":
            if rec.get("subtype") == "api_error":
                window_key_error = True
            if rec.get("subtype") in ("api_error", "turn_duration", "stop_hook_summary"):
                agent_event(ts)
    return sess


def read_sessions(root: Path) -> list[Session]:
    if not root.is_dir():
        return []
    seen: set = set()
    out = []
    for path in sorted(root.glob("*/*.jsonl")):   # top level only: subagents live deeper
        s = parse_session(path, seen)
        if s.prompts:
            out.append(s)
    return out


# ---------------------------------------------------------------- metrics

def _med(xs: list) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(median(xs), 1) if xs else None


def _pct(xs: list, q: float) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1)


def iso_week(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def apply_labels(sessions: Iterable[Session], label: Callable[[str], str], supervision: set) -> None:
    for s in sessions:
        for p in s.prompts:
            p.family = label(p.text)
            p.supervision = p.family in supervision


def session_metrics(sessions: list[Session]) -> dict:
    """Aggregate babysitting metrics. Call `apply_labels` first."""
    prompts = [p for s in sessions for p in s.prompts]
    sup = [p for p in prompts if p.supervision]
    per_session = []
    for s in sessions:
        n = len(s.prompts)
        agent = sum(p.agent_active_s for p in s.prompts)
        wall = (s.end - s.start).total_seconds() if s.start and s.end else 0
        per_session.append({
            "prompts": n, "supervision": sum(p.supervision for p in s.prompts),
            "agent_s": agent, "wall_s": wall,
        })
    agent_hours = sum(x["agent_s"] for x in per_session) / 3600
    later = [p for p in prompts if p.gap_s is not None]

    def preceders(ps):
        c = Counter(p.preceded_by for p in ps)
        tot = max(len(ps), 1)
        return {k: {"count": c[k], "share": round(c[k] / tot, 3)} for k in PRECEDERS if c[k]}

    by_family = Counter(p.family for p in sup)
    weeks: dict = defaultdict(lambda: {"prompts": 0, "supervision": 0, "agent_s": 0.0, "sessions": set()})
    for s in sessions:
        for p in s.prompts:
            if p.ts is None:
                continue
            w = weeks[iso_week(p.ts)]
            w["prompts"] += 1
            w["supervision"] += int(p.supervision)
            w["agent_s"] += p.agent_active_s
            w["sessions"].add(s.sid)
    weekly = []
    for wk in sorted(weeks):
        w = weeks[wk]
        ah = w["agent_s"] / 3600
        weekly.append({
            "week": wk, "sessions": len(w["sessions"]), "prompts": w["prompts"],
            "supervision": w["supervision"],
            "supervision_share": round(w["supervision"] / max(w["prompts"], 1), 3),
            "agent_hours": round(ah, 1),
            "supervision_per_agent_hour": round(w["supervision"] / ah, 2) if ah > 0.05 else None,
        })
    return {
        "sessions": len(sessions),
        "owner_prompts": len(prompts),
        "prompts_per_session": {"median": _med([x["prompts"] for x in per_session]),
                                "mean": round(len(prompts) / max(len(sessions), 1), 1),
                                "p90": _pct([x["prompts"] for x in per_session], 0.9)},
        "single_prompt_sessions": sum(1 for x in per_session if x["prompts"] == 1),
        "gap_between_prompts_min": {"median": _m(_med([p.gap_s for p in later])),
                                    "p90": _m(_pct([p.gap_s for p in later], 0.9))},
        "agent_run_before_intervention_min": {"median": _m(_med([p.agent_run_s for p in later])),
                                              "p90": _m(_pct([p.agent_run_s for p in later], 0.9))},
        "agent_run_before_supervision_min": {"median": _m(_med([p.agent_run_s for p in sup if p.gap_s is not None]))},
        "idle_before_prompt_min": {"median": _m(_med([p.idle_s for p in later])),
                                   "p90": _m(_pct([p.idle_s for p in later], 0.9))},
        "agent_hours": round(agent_hours, 1),
        "supervision_prompts": len(sup),
        "supervision_share": round(len(sup) / max(len(prompts), 1), 3),
        "supervision_per_agent_hour": round(len(sup) / agent_hours, 2) if agent_hours > 0 else None,
        "supervision_by_family": dict(by_family.most_common()),
        "preceded_by_supervision": preceders(sup),
        "preceded_by_other": preceders([p for p in prompts if not p.supervision]),
        "prompt_sources": dict(Counter(p.source or "unknown" for p in prompts).most_common()),
        "weekly": weekly,
    }


def _m(seconds: Optional[float]) -> Optional[float]:
    return None if seconds is None else round(seconds / 60, 1)

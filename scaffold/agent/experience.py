"""
experience.py — learning from this repo's own past verified fixes (ablation B).

Spec: docs/specs/ablation_experience.md.

Two separate things live here:

1. The experience store (AWOS_EXPERIENCE, default 0 = off). When a goal ends
   with its visible tests green and success, one record (goal, files changed,
   trimmed diff, test command/status, cost, turns) is appended to
   <dir>/<repo_key>.jsonl. At the start of a goal, the top 3 past records of
   the same repo_key by BM25 over goal text + file names are formatted as
   "Past verified changes in this repo (for reference)", capped at ~3k
   tokens, for the one-shot context and the agent's first message. Off: no
   read, no write, prompts unchanged.

2. The trajectory log (always on; not part of the comparison). Every finished
   goal appends {timestamp, goal, model, verdict, files_changed, diff, the
   goal's slice of the call log, cost, turns} to
   <trajectory dir>/<YYYY-MM-DD>.jsonl. Training data for later; never read
   at inference.

Nothing here raises: both are advisory.

Env:
  AWOS_EXPERIENCE            1/on | 0/off (default off)
  AWOS_EXPERIENCE_DIR        store dir (default .awos/experience, cwd-relative)
  AWOS_EXPERIENCE_REPO_KEY   the project's key (default <root name>-<path hash>)
  AWOS_EXPERIENCE_TOKENS     injected-text cap, chars/4 (default 3000)
  AWOS_TRAJECTORY_DIR        trajectory dir (default .awos/trajectories);
                             "0"/"off" disables. Under pytest it is off
                             unless this names a path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

EXPERIENCE_ENV = "AWOS_EXPERIENCE"
DIR_ENV = "AWOS_EXPERIENCE_DIR"
REPO_KEY_ENV = "AWOS_EXPERIENCE_REPO_KEY"
TOKENS_ENV = "AWOS_EXPERIENCE_TOKENS"
TRAJECTORY_ENV = "AWOS_TRAJECTORY_DIR"

HEADER = "Past verified changes in this repo (for reference)"
TOP_K = 3
DEFAULT_TOKENS = 3000
STORED_DIFF_CHARS = 8000        # a stored record's diff
STORED_GOAL_CHARS = 4000
SHOWN_GOAL_CHARS = 600
MIN_DIFF_CHARS = 200            # below this a record is dropped, not shown diff-less
TRAJ_DIFF_CHARS = 20000
TRAJ_GOAL_CHARS = 8000
TRAJ_MAX_CALLS = 500
CALL_LOG_TAIL_BYTES = 20 * 1024 * 1024
_OFF = {"0", "off", "false", "no", ""}

BM25_K1 = 1.5
BM25_B = 0.75


# ── Switches and paths ───────────────────────────────────────────────────────

def enabled() -> bool:
    return os.environ.get(EXPERIENCE_ENV, "0").strip().lower() in ("1", "on", "true", "yes")


def token_cap() -> int:
    try:
        return max(200, int(os.environ.get(TOKENS_ENV, DEFAULT_TOKENS)))
    except ValueError:
        return DEFAULT_TOKENS


def store_dir() -> Path:
    return Path(os.environ.get(DIR_ENV) or (Path(".awos") / "experience"))


def repo_key(codebase_root: str) -> str:
    """A stable key for the project: the env override, else name + path hash."""
    key = os.environ.get(REPO_KEY_ENV, "").strip()
    if not key:
        root = Path(codebase_root or ".").resolve()
        digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:8]
        key = f"{root.name or 'root'}-{digest}"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:120] or "repo"


def store_path(codebase_root: str) -> Path:
    return store_dir() / f"{repo_key(codebase_root)}.jsonl"


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def _trim(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 40)] + f"\n... [trimmed {len(text) - limit + 40} chars]"


# ── Store ────────────────────────────────────────────────────────────────────

def load_records(codebase_root: str) -> list[dict]:
    path = store_path(codebase_root)
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    return out


def is_green(test_result: Any) -> bool:
    """Visible tests ran and passed: at least one passed, none failed or errored."""
    if test_result is None or getattr(test_result, "no_tests_found", True):
        return False
    if getattr(test_result, "timed_out", False) or getattr(test_result, "infra_error", False):
        return False
    return (int(getattr(test_result, "passed", 0) or 0) > 0
            and int(getattr(test_result, "failed", 0) or 0) == 0
            and int(getattr(test_result, "errors", 0) or 0) == 0)


def save_record(codebase_root: str, *, goal: str, files: list, diff: str, success: bool,
                test_result: Any, cost_usd: float = 0.0, turns: int = 0) -> Optional[int]:
    """
    Append one record when the goal succeeded with visible tests green.
    Returns the new record's number (1-based), or None when nothing was stored
    (off, not success, not green, or a write error). Never raises.
    """
    try:
        if not enabled() or not success or not is_green(test_result):
            return None
        n = len(load_records(codebase_root)) + 1
        cmd = getattr(test_result, "test_command", None) or []
        rec = {
            "id": n,
            "ts": round(time.time(), 3),
            "repo_key": repo_key(codebase_root),
            "goal": _trim(str(goal), STORED_GOAL_CHARS),
            "files": sorted({str(f) for f in files or []}),
            "diff": _trim(str(diff or ""), STORED_DIFF_CHARS),
            "test_command": " ".join(map(str, cmd)) if isinstance(cmd, (list, tuple)) else str(cmd),
            "test_status": (f"{getattr(test_result, 'passed', 0)} passed, "
                            f"{getattr(test_result, 'failed', 0)} failed"),
            "cost_usd": round(float(cost_usd or 0.0), 6),
            "turns": int(turns or 0),
        }
        path = store_path(codebase_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        print(f"[EXPERIENCE] saved record (#{n})", flush=True)
        return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("[EXPERIENCE] save failed: %s", exc)
        return None


# ── Retrieval (BM25) ─────────────────────────────────────────────────────────

_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_STOP = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "it", "be",
    "that", "this", "with", "as", "by", "at", "from", "are", "was", "should", "when",
    "py", "if", "not", "so", "do", "does", "can", "its", "into", "then", "than",
}


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for word in _WORD.findall(text or ""):
        parts = [p.lower() for p in _CAMEL.findall(word)] or [word.lower()]
        low = word.lower()
        for t in ({low} | set(parts)) if len(parts) > 1 else {low}:
            if len(t) > 1 and t not in _STOP:
                out.append(t)
    return out


def _doc_tokens(rec: dict) -> list[str]:
    files = " ".join(str(f).replace("/", " ").replace("_", " ") for f in rec.get("files") or [])
    return tokenize(str(rec.get("goal", ""))) + tokenize(files)


def bm25_rank(query: str, records: list[dict], k: int = TOP_K) -> list[tuple[float, dict]]:
    """The top-k records by BM25 over goal text + file names; score > 0 only."""
    docs = [_doc_tokens(r) for r in records]
    if not docs:
        return []
    q = set(tokenize(query))
    if not q:
        return []
    n = len(docs)
    avgdl = (sum(len(d) for d in docs) / n) or 1.0
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    scored = []
    for idx, (d, rec) in enumerate(zip(docs, records)):
        tf = Counter(d)
        score = 0.0
        for term in q:
            f = tf.get(term, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * len(d) / avgdl))
        if score > 0:
            scored.append((score, idx, rec))
    # Ties: the newer record first.
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [(s, r) for s, _, r in scored[:k]]


def _format(recs: list[dict], diff_chars: int) -> str:
    parts = [f"## {HEADER}\n\nThese earlier changes to this codebase passed its tests. "
             "Use them for the project's idioms and the files a kind of change touches; "
             "the current task may need something different."]
    for i, rec in enumerate(recs, 1):
        goal = _trim(" ".join(str(rec.get("goal", "")).split()), SHOWN_GOAL_CHARS)
        files = ", ".join(rec.get("files") or []) or "none recorded"
        diff = _trim(str(rec.get("diff", "")), diff_chars)
        parts.append(f"### Past change {i}: {goal}\nFiles: {files}\n"
                     f"```diff\n{diff}\n```")
    return "\n\n".join(parts)


def build_block(recs: list[dict], cap_tokens: Optional[int] = None) -> str:
    """The injected text, within cap_tokens (chars/4): diffs trimmed evenly,
    then the lowest-ranked record dropped until it fits."""
    cap = cap_tokens or token_cap()
    recs = list(recs)
    while recs:
        overhead = estimate_tokens(_format(recs, 0)) + 20 * len(recs)
        room_chars = (cap - overhead) * 4
        per = room_chars // len(recs)
        if per >= MIN_DIFF_CHARS:
            text = _format(recs, per)
            if estimate_tokens(text) <= cap:
                return text
        recs = recs[:-1]
    return ""


def experience_context(goal: str, codebase_root: str) -> tuple[str, list[int]]:
    """
    (block, ids) for this goal: "" and [] when off, empty or nothing similar.
    Logs the store size and what was injected. Never raises.
    """
    if not enabled():
        return "", []
    try:
        records = load_records(codebase_root)
        ranked = bm25_rank(goal, records)
        block = build_block([r for _, r in ranked]) if ranked else ""
        shown = block.count("### Past change ")
        ids = [int(r.get("id", 0) or 0) for _, r in ranked][:shown]
        print(f"[EXPERIENCE] store: {len(records)} records; injected {len(ids)} "
              f"({', '.join(f'#{i}' for i in ids) or 'none'}, ~{estimate_tokens(block)} tokens)",
              flush=True)
        return block, ids
    except Exception as exc:  # noqa: BLE001
        logger.warning("[EXPERIENCE] retrieval failed: %s", exc)
        return "", []


# ── Trajectory log (always on) ───────────────────────────────────────────────

def trajectory_dir() -> Optional[Path]:
    value = os.environ.get(TRAJECTORY_ENV)
    if value is None:
        if "PYTEST_CURRENT_TEST" in os.environ:
            return None
        return Path(".awos") / "trajectories"
    if value.strip().lower() in _OFF:
        return None
    return Path(value)


def call_log_slice(start_ts: float, end_ts: float, pid: Optional[int] = None) -> list[dict]:
    """This process's call-log lines between start_ts and end_ts (metadata only)."""
    try:
        try:
            from .llm_call_log import log_path
        except ImportError:
            from llm_call_log import log_path
        path = log_path()
        if path is None or not Path(path).exists():
            return []
        pid = os.getpid() if pid is None else pid
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - CALL_LOG_TAIL_BYTES))
            raw = fh.read().decode("utf-8", errors="replace")
        out = []
        for line in raw.splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            ts = rec.get("ts")
            if not isinstance(ts, (int, float)) or not (start_ts <= ts <= end_ts):
                continue
            if rec.get("pid") not in (None, pid):
                continue
            out.append(rec)
        return out[-TRAJ_MAX_CALLS:]
    except Exception:  # noqa: BLE001
        return []


def write_trajectory(*, goal: str, model: Optional[str], verdict: dict, files_changed: Iterable,
                     diff: str, start_ts: float, end_ts: Optional[float] = None,
                     cost_usd: float = 0.0, turns: Optional[int] = None) -> Optional[Path]:
    """Append one finished goal's trajectory. Returns the file, or None. Never raises."""
    try:
        base = trajectory_dir()
        if base is None:
            return None
        end_ts = time.time() if end_ts is None else end_ts
        calls = call_log_slice(start_ts, end_ts)
        rec = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(end_ts)),
            "goal": _trim(str(goal), TRAJ_GOAL_CHARS),
            "model": model,
            "verdict": verdict,
            "files_changed": sorted({str(f) for f in files_changed or []}),
            "diff": _trim(str(diff or ""), TRAJ_DIFF_CHARS),
            "calls": calls,
            "cost_usd": round(float(cost_usd or 0.0), 6),
            "turns": int(turns if turns is not None else len(calls)),
            "eval": {k: os.environ[e] for k, e in (("arm", "AWOS_EVAL_ARM"),
                                                    ("job", "AWOS_EVAL_JOB"),
                                                    ("repeat", "AWOS_EVAL_REPEAT"))
                     if os.environ.get(e)},
        }
        line = json.dumps(rec, default=str)
        if len(line) > 400_000:   # bounded record: drop the call slice first
            rec["calls"] = calls[-50:]
            rec["calls_dropped"] = len(calls) - len(rec["calls"])
            line = json.dumps(rec, default=str)
            if len(line) > 400_000:
                rec["calls"] = []
                rec["verdict"] = {"truncated": True}
                line = json.dumps(rec, default=str)
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"{time.strftime('%Y-%m-%d', time.localtime(end_ts))}.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(f"[TRAJECTORY] appended to {path} ({len(calls)} call(s))", flush=True)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.debug("[TRAJECTORY] write failed: %s", exc)
        return None

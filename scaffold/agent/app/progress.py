"""
progress.py — turn a job's raw logs into friendly one-line progress updates.

The worker appends the orchestrator child's output to <job_dir>/worker.log and
its own steps to <job_dir>/journal.jsonl. `ProgressTranslator.translate(line)`
maps one raw line to a short plain-English update (or None for noise), and
`JobTail` follows both files by byte offset so each line is read once.

The two files are written by different processes, so `JobTail.poll` merges
their new lines by timestamp before translating: worker.log lines carry a
local "HH:MM:SS" prefix or a "===== child, attempt N at <iso> =====" header,
journal records an ISO "ts". A line without a stamp of its own (the
orchestrator child's output) inherits the last stamp seen in its file; ties
keep file order, worker.log first.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
_TIME_PREFIX = re.compile(r"^\d\d:\d\d:\d\d\s+")
_AGENT_TOOL = re.compile(r"^\[agent\] t(\d+) ([A-Za-z_][\w]*)\((.*)\)\s*->\s*(\w+)")
_AGENT_SAYS = re.compile(r"^\[agent\] t\d+ says:\s*(.+)")
_AGENT_STOP = re.compile(r"^\[agent\] stop:\s*(\w+)")
_TESTS = re.compile(r"Tests:\s*(\d+) passed,\s*(\d+) failed")
_CLOCK = re.compile(r"^(\d\d):(\d\d):(\d\d)\s")
_ATTEMPT_HEADER = re.compile(r"^=====.*\bat (\d{4}-\d\d-\d\dT[0-9:.]+(?:[+-]\d\d:\d\d|Z)?)")


def _iso_epoch(text: str) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt.timestamp()   # naive = local time, as the writers use


def _clock_epoch(h: int, m: int, s: int, ref: float) -> float:
    """Local HH:MM:SS -> epoch, on the day of `ref` (yesterday if that lands in the future)."""
    lt = time.localtime(ref)
    stamp = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, s, 0, 0, -1))
    if stamp > ref + 3600:   # a pre-midnight line read just after midnight
        stamp -= 86400
    return stamp


def log_line_time(raw: str, now: Optional[float] = None) -> Optional[float]:
    """Epoch seconds a worker.log line was written, or None if it carries no stamp."""
    line = strip_ansi(raw).strip()
    m = _CLOCK.match(line)
    if m:
        return _clock_epoch(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            time.time() if now is None else now)
    m = _ATTEMPT_HEADER.match(line)
    if m:
        return _iso_epoch(m.group(1))
    return None


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _short(text: str, n: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _first_arg(args: str) -> str:
    """'calc.py' from 'calc.py' / "'calc.py', 3" / 'average, .'."""
    first = args.split(",")[0].strip().strip("'\"")
    return first


def _tests_phrase(passed: int, failed: int) -> str:
    if failed:
        return f"running tests… {passed} passed, {failed} failed"
    return f"running tests… {passed} passed"


_TOOL_PHRASES = {
    "read_file": "reading {arg}",
    "read_files": "reading {arg}",
    "view_file": "reading {arg}",
    "edit_file": "editing {arg}",
    "replace_in_file": "editing {arg}",
    "apply_patch": "applying a patch",
    "write_file": "writing {arg}",
    "create_file": "creating {arg}",
    "delete_file": "deleting {arg}",
    "grep": "searching for '{arg}'",
    "search": "searching for '{arg}'",
    "find_files": "looking for files",
    "list_dir": "looking around {arg}",
    "run_tests": "running tests",
    "run_command": "running `{arg}`",
    "shell": "running `{arg}`",
    "submit_verdict": "checking the result",
    "finish": "wrapping up",
}


class ProgressTranslator:
    """Raw log line -> friendly update (None = not worth showing)."""

    def translate(self, raw: str) -> Optional[str]:
        line = _TIME_PREFIX.sub("", strip_ansi(raw)).strip()
        if not line or not line.startswith("["):
            return None
        try:
            return self._translate(line)
        except Exception:   # a translator bug must never break the display
            return None

    def _translate(self, line: str) -> Optional[str]:
        m = _AGENT_TOOL.match(line)
        if m:
            tool, args, status = m.group(2), m.group(3), m.group(4)
            template = _TOOL_PHRASES.get(tool)
            arg = _short(_first_arg(args), 50)
            if template is None:
                text = f"using {tool}"
            elif "{arg}" in template and not arg:
                text = template.replace(" {arg}", "").replace(" '{arg}'", "").replace(" `{arg}`", "")
                if tool == "list_dir":
                    text = "looking around the project"
            else:
                text = template.format(arg=arg)
            if status not in ("ok",):
                text += f" ({status})"
            return text
        m = _AGENT_SAYS.match(line)
        if m:
            return "“" + _short(m.group(1), 90) + "”"
        m = _AGENT_STOP.match(line)
        if m:
            why = m.group(1)
            if why == "completed":
                return None
            if why == "max_turns":
                return "the agent hit its turn limit"
            if why == "model_error":
                return "the model returned an error"
            return f"the agent stopped ({why.replace('_', ' ')})"
        if line.startswith("[ACCEPTANCE]"):
            m = re.search(r"generated (\d+) tests?, kept (\d+)", line)
            if m:
                return f"writing acceptance tests ({m.group(2)} kept)"
            m = re.search(r"check: (\d+) kept, (\d+) passed", line)
            if m:
                return f"acceptance tests: {m.group(2)}/{m.group(1)} passing"
            return None
        if line.startswith("[ONE-SHOT]"):
            if " call:" in line:
                return "trying a one-shot fix"
            m = re.search(r"solved in (\d+) calls?.*?tests (\d+) passed", line)
            if m:
                return f"one-shot fix worked (tests {m.group(2)} passed)"
            if "solved" in line:
                return "one-shot fix worked"
            if "fell back" in line:
                return "switching to the step-by-step agent"
            return None
        if line.startswith("[PLANNER]"):
            if "skipped" in line:
                return "small goal: running it as one task"
            m = re.search(r"Generated (\d+) tasks?", line)
            if m:
                n = int(m.group(1))
                return f"planned {n} task{'s' if n != 1 else ''}"
            return None
        if line.startswith("[EXPLORE]"):
            if "Pre-plan" in line:
                return "scanning the repository"
            return None
        if line.startswith("[TASK"):
            m = re.match(r"\[TASK (\d+)\] Executing:\s*(.+)", line)
            if m:
                return f"task {m.group(1)}: {_short(m.group(2), 60)}"
            m = _TESTS.search(line)
            if m:
                return _tests_phrase(int(m.group(1)), int(m.group(2)))
            m = re.match(r"\[TASK (\d+)\] (?:AGENT|ONE-SHOT)? ?FAILED:\s*(.+)", line)
            if m:
                return f"task {m.group(1)} failed: {_short(m.group(2), 60)}"
            m = re.match(r"\[TASK (\d+)\] (?:AGENT|ONE-SHOT) OK", line)
            if m:
                return f"task {m.group(1)} finished"
            return None
        if line.startswith("[GOAL CHECK]"):
            if "checker model" in line:
                return "double-checking the goal"
            m = re.match(r"\[GOAL CHECK\] (\w+)", line)
            if m and m.group(1) in ("complete", "incomplete"):
                return f"goal check: {m.group(1)}"
            return None
        if line.startswith("[COMPONENTS]"):
            data = json.loads(line[len("[COMPONENTS]"):].strip())
            ran = sorted(k.replace("_", "-") for k, v in data.items()
                         if isinstance(v, dict) and v.get("status") == "ran")
            return ("used: " + ", ".join(ran)) if ran else None
        if line.startswith("[worker]"):
            m = re.search(r"running \(attempt (\d+)\)", line)
            if m:
                return "started" if m.group(1) == "1" else f"started again (attempt {m.group(1)})"
            if "auto-continue" in line:
                return "stopped at a limit with progress; continuing where it left off"
            if "blocked:" in line:
                return "blocked: " + _short(line.split("blocked:", 1)[1], 70)
            if "stopped cleanly" in line:
                return "paused (the worker stopped); it will resume"
            if "was interrupted" in line:
                return "was interrupted; queued again"
            return None
        if line.startswith("[runner]"):   # the model-free sleep runner
            m = re.search(r"(fake_step_\d+/\d+)", line)
            return f"working ({m.group(1).replace('fake_step_', 'step ')})" if m else None
        return None

    def translate_journal(self, record: dict) -> Optional[str]:
        """One journal.jsonl record -> update. Only steps the log does not show."""
        step = record.get("step") or record.get("key") or ""
        payload = record.get("payload") or {}
        if step == "workspace":
            base = (payload.get("base_commit") or "")[:8]
            return f"preparing a private copy of the repo{' at ' + base if base else ''}"
        if step == "verify_tests":
            passed, failed = int(payload.get("passed") or 0), int(payload.get("failed") or 0)
            return "verifying: " + _tests_phrase(passed, failed).replace("running tests… ", "tests ")
        return None


class JobTail:
    """Follow one job's worker.log and journal.jsonl; return new friendly updates."""

    def __init__(self, job_dir: Path, translator: Optional[ProgressTranslator] = None):
        self.job_dir = Path(job_dir)
        self.tr = translator or ProgressTranslator()
        self._offsets: dict[str, int] = {}
        self._partial: dict[str, str] = {}
        self._last_ts: dict[str, float] = {}
        self._last: Optional[str] = None
        self.last_update: Optional[str] = None

    def _new_lines(self, name: str) -> list[str]:
        path = self.job_dir / name
        try:
            size = path.stat().st_size
        except OSError:
            return []
        off = self._offsets.get(name, 0)
        if size < off:   # truncated/rotated: start over
            off = 0
        if size == off:
            return []
        with open(path, "rb") as fh:
            fh.seek(off)
            chunk = fh.read(size - off)
        self._offsets[name] = off + len(chunk)
        text = self._partial.get(name, "") + chunk.decode("utf-8", errors="replace")
        lines = text.split("\n")
        self._partial[name] = lines.pop()   # an unfinished last line waits
        return lines

    def _stamped(self, name: str, stamp_of) -> list[tuple[float, str]]:
        """New lines of `name` with a timestamp each (inherited when missing)."""
        out = []
        last = self._last_ts.get(name, float("-inf"))
        for line in self._new_lines(name):
            ts = stamp_of(line)
            if ts is None:
                ts = last
            else:
                last = max(last, ts)   # one file's stamps never go backwards
                ts = last
            out.append((ts, line))
        self._last_ts[name] = last
        return out

    @staticmethod
    def _journal_time(line: str) -> Optional[float]:
        try:
            return _iso_epoch(json.loads(line).get("ts") or "")
        except (ValueError, TypeError, AttributeError):
            return None

    def poll(self) -> list[str]:
        now = time.time()
        log = [(ts, 0, i, line) for i, (ts, line) in
               enumerate(self._stamped("worker.log", lambda l: log_line_time(l, now)))]
        jnl = [(ts, 1, i, line) for i, (ts, line) in
               enumerate(self._stamped("journal.jsonl", self._journal_time))]
        out: list[str] = []
        # Merge by time; ties keep each file's own order (worker.log first).
        for _ts, src, _i, line in sorted(log + jnl, key=lambda r: (r[0], r[1], r[2])):
            if src == 0:
                msg = self.tr.translate(line)
            else:
                try:
                    msg = self.tr.translate_journal(json.loads(line))
                except (ValueError, TypeError, AttributeError):
                    msg = None
            if msg:
                out.append(msg)
        deduped = []
        for msg in out:
            if msg != self._last:
                deduped.append(msg)
                self._last = msg
        if deduped:
            self.last_update = deduped[-1]
        return deduped

    def skip_to_end(self) -> None:
        """Start following from now (used when re-attaching to an old job)."""
        now = time.time()
        for name in ("worker.log", "journal.jsonl"):
            self._last_ts[name] = now   # unstamped lines from here on are "now or later"
            try:
                self._offsets[name] = (self.job_dir / name).stat().st_size
            except OSError:
                self._offsets[name] = 0

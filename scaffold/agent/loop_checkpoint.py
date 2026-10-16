"""
loop_checkpoint.py — per-turn checkpoint of the `loop` executor's conversation,
so a job killed mid-run (battery, OOM, SIGKILL, reboot) resumes the SAME
conversation instead of starting over (rea_synthesis_2026-10-16 item 9).

The loop's history (LoopExecutor.messages) is append-only: every request is
the previous request plus a suffix. The checkpoint mirrors that on disk.

Storage: one JSONL file (the caller picks the path, normally inside the host
job directory). One record per line, written with a single write, flushed
and fsynced; the directory is fsynced when the file is created or replaced.
Each `save()` appends only the NEW suffix of the messages plus the current
counters, so a 60-step run writes O(n) message bytes, not O(n^2).

Record shapes (v = 1):

    {"v":1,"t":"turn","key":K,"base":B,"prev":H_B,"msgs":[...],"h":H_N,
     "counters":{...},"ts":...}
    {"v":1,"t":"finish","key":K,"stop_reason":"submitted","ts":...}

`base` is the number of messages the record extends; `prev`/`h` are a hash
chain over the canonical JSON of every message (h_0 = H("awos-loop-ckpt" + K),
h_i = H(h_{i-1} + msg_i)). load() replays the records for its key and applies
a turn record only when its `base` and `prev` match a prefix it already holds
and its `h` matches the recomputed chain, so it always returns a state that
some writer actually saved: a torn last line, a flipped byte, an interleaved
record from a stale writer or another task's key are skipped, never raised
on. A record whose base is shorter than the current state (a stale writer
that forked) truncates to that base and extends: last writer wins.

When the given messages no longer extend what is on disk (a new run for the
same path, another key, a finished run, an edited history) the file is
rewritten atomically (temp file + os.replace) with one full record; that is
also what bounds the file's size across runs. One writer per path is the
intended use (the host runs one attempt of a job at a time); a stale second
writer whose prefix was replaced has its records skipped, never mixed in.

Why not host/journal.py itself: Journal is fixed to <job_dir>/journal.jsonl,
which the host worker already uses for its own steps (replay_plan would see
every loop turn), and its (key, status, attempt) dedup + whole-file index is
built for exactly-once steps, not for an ever-growing conversation. This
module reuses its techniques instead: append + flush + fsync per record,
flock around the write, directory fsync on create, truncated-tail repair
before appending, and unparseable lines skipped.

Resume
------
A resumed run (load() returned (messages, counters)) must take care that:

  * the workspace already holds the edits of the killed run (the host reuses
    <job_dir>/workspace across attempts): do NOT reset or re-snapshot it as
    if fresh. The WorkspaceWatch "before" snapshot should be the base commit
    (or files_changed so far, carried in the counters), else files changed
    before the kill are not reported;
  * the tool results already in the history are real: do not re-run the
    commands. The history is sent as-is, so the prompt is byte-identical to
    the killed run's and the provider's prompt cache (or the local server's
    prefix cache) still applies;
  * save() runs only at turn boundaries (after the assistant turn AND its
    tool results are appended), so the history never ends on an assistant
    tool_use without its tool_result. A turn cut off by the kill is simply
    asked again (that one model call is paid twice, nothing else);
  * LoopRun counters (steps, tool_calls, tokens, cost_usd, submits,
    gate_retries, format_errors...) continue from the checkpoint, so
    max_steps / gate retries are per task, not per attempt. Spend caps must
    not count the prior cost twice: the host already subtracts earlier
    attempts' spend from AWOS_MAX_RUN_COST (SpendTracker), so either compare
    only this attempt's cost against the cap or pass the original cap;
  * the wall clock restarts per attempt (elapsed time is not a counter);
  * the checkpoint key must match: task_key() hashes the goal, the model and
    the protocol, so a resumed attempt with another model, or a goal changed
    by an auto-continue NOTE, starts fresh rather than resuming a mismatched
    conversation (key the checkpoint on the original goal);
  * finish(stop_reason) is called when the run ends by itself (submitted,
    gate_red, max_steps..., and model errors a retry will not fix: auth, a
    context overflow, a bad request), so a finished run is never resumed;
    only a kill or a transient model error leaves a resumable file;
  * the loop executor also keeps a workspace fingerprint in the counters
    (loop_executor.workspace_fingerprint: a hash of every file's size and
    mtime, the newest mtime, the changed files' stats). On resume, a
    workspace that moved after the last save (the kill landed mid-command)
    gets a harness note naming the files, appended to the last turn. The
    counters also carry the escalations and the compaction summary count.

Secrets
-------
What save() writes is passed through redact_text() first: the owner's
secret env values (names with KEY / TOKEN / SECRET / PASSWORD..., values of
8+ characters) and known key formats (host/handoff.py's patterns plus
secret_scan.py's rules) become "[REDACTED:<name>]". Nothing else is touched,
so a history with no secret in it is saved byte for byte and a resumed run
sends exactly the killed run's prompt. When a secret WAS present, the
resumed prefix differs from the killed run's at that spot (one prompt-cache
miss, and the model sees the placeholder instead of the value). The
in-memory conversation of the live run is never changed.

save() returns True when the state is on disk (or already was) and False on
a failed write; `failures` counts the failed writes (loop telemetry
`checkpoint_failures`).

Env: AWOS_LOOP_CHECKPOINT (default on; 0/false/off/no disables).
Tests: tests/test_loop_checkpoint.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

try:  # POSIX only; single-writer use works without it.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

VERSION = 1
_TURN = "turn"
_FINISH = "finish"
_OFF = {"0", "false", "off", "no"}
#: Bytes read per step when scanning back for the last newline (tail repair).
_CHUNK = 64 * 1024


def checkpoint_enabled() -> bool:
    """AWOS_LOOP_CHECKPOINT: default on; 0/false/off/no disables."""
    return os.getenv("AWOS_LOOP_CHECKPOINT", "").strip().lower() not in _OFF


def task_key(goal: str, model: str, text_protocol: bool, *, extra: str = "") -> str:
    """Stable key for (goal, model, protocol): a checkpoint written for another
    task, model or protocol is never resumed. `extra` (e.g. a task id or a
    prompt-template version) narrows it further."""
    payload = json.dumps({"v": VERSION, "goal": goal, "model": model,
                          "text": bool(text_protocol), "extra": extra},
                         sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return "lc1-" + hashlib.sha256(payload.encode("ascii")).hexdigest()[:32]


# ── secret redaction (what goes to disk) ─────────────────────────────────────


def _import_first(*names: str) -> Any:
    """The first of the dotted module names that imports (package or flat
    sys.path layout), or None."""
    import importlib

    for name in names:
        try:
            if name.startswith("."):
                if not __package__:
                    continue
                return importlib.import_module(name, __package__)
            return importlib.import_module(name)
        except Exception:  # noqa: BLE001 - try the next layout
            continue
    return None


def _handoff_redact():
    mod = _import_first(".host.handoff", "scaffold.agent.host.handoff", "host.handoff")
    return getattr(mod, "redact_secrets", None) if mod is not None else None


def _scan_rules() -> tuple:
    ss = _import_first(".secret_scan", "scaffold.agent.secret_scan", "secret_scan")
    if ss is None:
        return (), None
    return tuple(getattr(ss, "RULES", ())), getattr(ss, "_is_placeholder", None)


def redact_text(text: Any) -> Any:
    """`text` with the owner's secret env values and known key formats
    replaced by "[REDACTED:<name>]"; anything that is not a str is returned
    as is. Text without a secret comes back unchanged (same bytes). Never
    raises (a redaction error returns the text with whatever was done)."""
    if not isinstance(text, str) or not text:
        return text
    out = text
    try:
        fn = _handoff_redact()
        if fn is not None:
            out = fn(out)
        else:  # host package not importable: the env-value rule handoff applies
            secret_name = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.I)
            values = sorted(((k, v) for k, v in os.environ.items()
                             if secret_name.search(k) and v and len(v) >= 8),
                            key=lambda kv: -len(kv[1]))
            for name, val in values:
                out = out.replace(val, f"[REDACTED:{name}]")
        rules, placeholder = _scan_rules()
        for rule, pat in rules:
            def _sub(m, rule=rule):
                tok = m.group(0)
                if placeholder is not None and placeholder(tok):
                    return tok
                return f"[REDACTED:{rule}]"
            out = pat.sub(_sub, out)
    except Exception:  # noqa: BLE001
        pass
    return out


def redact_obj(obj: Any) -> Any:
    """Every string in a JSON-able structure through redact_text. Returns the
    SAME object when nothing changed (so unchanged messages stay identical)."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        new = {k: redact_obj(v) for k, v in obj.items()}
        return obj if all(new[k] is obj[k] for k in obj) else new
    if isinstance(obj, list):
        new_l = [redact_obj(v) for v in obj]
        return obj if all(a is b for a, b in zip(new_l, obj)) else new_l
    return obj


def _canon(obj: Any) -> str:
    # ensure_ascii: every line is pure ASCII, so lone surrogates or odd
    # unicode in tool output can neither break the write nor the decode.
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                      allow_nan=False)


def _h(prev: str, msg_canon: str) -> str:
    return hashlib.sha256((prev + "\n" + msg_canon).encode("ascii")).hexdigest()[:32]


def _h0(key: str) -> str:
    return hashlib.sha256(("awos-loop-ckpt\n" + key).encode("utf-8")).hexdigest()[:32]


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class _State:
    """Replayed state for one key: messages, chain (len = len(messages) + 1)."""

    __slots__ = ("messages", "chain", "counters", "finished")

    def __init__(self, key: str) -> None:
        self.messages: list = []
        self.chain: list[str] = [_h0(key)]
        self.counters: dict = {}
        self.finished = False


class LoopCheckpoint:
    """Append-only, fsynced checkpoint of one loop conversation."""

    def __init__(self, path: str | os.PathLike, key: str, fsync: bool = True,
                 redact: bool = True) -> None:
        self.path = Path(path)
        self.key = str(key)
        self.fsync = fsync
        #: Pass saved messages and counters through redact_text (module docstring).
        self.redact = redact
        #: save() calls that could not write (telemetry: checkpoint_failures).
        self.failures = 0
        #: Last saved message index -> its redacted form, so each save
        #: redacts only the new suffix.
        self._redacted: list = []
        #: Lines load() skipped (unparseable, wrong shape, chain mismatch).
        self.skipped_lines = 0
        # Writer state: what this instance knows is on disk for self.key.
        self._synced = False
        self._chain: list[str] = [_h0(self.key)]
        self._last_canon: Optional[str] = None
        self._last_counters: Optional[str] = None

    # -- reading -------------------------------------------------------------

    def _replay(self) -> Optional[_State]:
        """Replay every record for self.key; None when the file is missing."""
        try:
            fh = open(self.path, "rb")
        except OSError:
            return None
        st = _State(self.key)
        skipped = 0
        with fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line.decode("ascii"))
                except (ValueError, UnicodeDecodeError):
                    skipped += 1  # torn last line, or garbage
                    continue
                if not isinstance(rec, dict) or rec.get("v") != VERSION:
                    skipped += 1
                    continue
                if rec.get("key") != self.key:
                    continue  # another task's record: not ours, not corrupt
                if not self._apply(st, rec):
                    skipped += 1
        self.skipped_lines = skipped
        return st

    def _apply(self, st: _State, rec: dict) -> bool:
        kind = rec.get("t")
        if kind == _FINISH:
            st.finished = True
            return True
        if kind != _TURN:
            return False
        base, msgs, counters = rec.get("base"), rec.get("msgs"), rec.get("counters")
        if (not isinstance(base, int) or isinstance(base, bool) or base < 0
                or not isinstance(msgs, list) or not isinstance(counters, dict)
                or not all(isinstance(m, dict) for m in msgs)):
            return False
        if st.finished and base != 0:
            return False  # a finished run only restarts from scratch
        if base > len(st.messages) or st.chain[base] != rec.get("prev"):
            return False  # extends a prefix we never saw
        chain = st.chain[:base + 1]
        try:
            for m in msgs:
                chain.append(_h(chain[-1], _canon(m)))
        except (TypeError, ValueError):
            return False
        if chain[-1] != rec.get("h"):
            return False  # payload does not hash to what the writer saved
        st.messages = st.messages[:base] + msgs
        st.chain = chain
        st.counters = counters
        st.finished = False
        return True

    def load(self) -> Optional[tuple[list[dict], dict]]:
        """The latest consistent (messages, counters) for this key, or None
        (missing file, another key only, corrupt, finished, or empty)."""
        try:
            st = self._replay()
        except Exception as exc:  # noqa: BLE001 - a broken checkpoint means "start fresh"
            log.warning("loop checkpoint: load failed (%s): %s", self.path, exc)
            return None
        if st is None or st.finished or not st.messages:
            return None
        return st.messages, st.counters

    # -- writing -------------------------------------------------------------

    def _sync(self, f) -> None:
        f.flush()
        if self.fsync:
            os.fsync(f.fileno())

    @staticmethod
    def _repair_tail(f) -> None:
        """Under the lock: cut a partial last line (crash mid-write) back to
        the last newline so the next record does not glue onto it."""
        end = f.seek(0, os.SEEK_END)
        if end == 0:
            return
        f.seek(end - 1)
        if f.read(1) == b"\n":
            return
        pos = end
        cut = 0
        while pos > 0:
            start = max(0, pos - _CHUNK)
            f.seek(start)
            chunk = f.read(pos - start)
            i = chunk.rfind(b"\n")
            if i >= 0:
                cut = start + i + 1
                break
            pos = start
        f.truncate(cut)

    def _append_line(self, line: bytes) -> None:
        created = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+b") as f:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                self._repair_tail(f)
                f.seek(0, os.SEEK_END)
                f.write(line)  # one write: a crash leaves at most a torn tail
                self._sync(f)
            finally:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        if created and self.fsync:
            _fsync_dir(self.path.parent)

    def _rewrite(self, line: bytes) -> None:
        """Replace the whole file atomically with one record."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(line)
                self._sync(f)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        if self.fsync:
            _fsync_dir(self.path.parent)

    def _record(self, base: int, new: list, new_canon: list[str], counters_canon: str,
                chain: list[str]) -> bytes:
        rec = ('{"v":%d,"t":"%s","key":%s,"base":%d,"prev":"%s","msgs":[%s],"h":"%s",'
               '"counters":%s,"ts":%.3f}\n') % (
            VERSION, _TURN, json.dumps(self.key, ensure_ascii=True), base, chain[base],
            ",".join(new_canon), chain[-1], counters_canon, time.time())
        return rec.encode("ascii")

    def _adopt_disk(self, messages: list) -> None:
        """First save of this instance: continue the file when what it holds
        for this key is a prefix of `messages`, else start it over."""
        self._synced = True
        st = None
        try:
            st = self._replay()
        except Exception:  # noqa: BLE001
            st = None
        if st is None or st.finished or not st.messages or len(st.messages) > len(messages):
            self._chain = None  # type: ignore[assignment]  # -> rewrite
            return
        chain = [_h0(self.key)]
        for m in messages[:len(st.messages)]:
            chain.append(_h(chain[-1], _canon(m)))
        if chain != st.chain:
            self._chain = None  # type: ignore[assignment]
            return
        self._chain = chain
        self._last_canon = _canon(messages[len(st.messages) - 1])
        self._last_counters = _canon(st.counters)

    def _redacted_messages(self, messages: list) -> list:
        """`messages` with secrets redacted, reusing the redaction of messages
        saved before when they are the same objects (append-only history)."""
        if not self.redact:
            return messages
        cache = self._redacted
        out = []
        for i, m in enumerate(messages):
            # The previous save's last message is redone: the append-only
            # check in _save compares it, and it may have been edited in place.
            if i < len(cache) - 1 and cache[i][0] is m:
                out.append(cache[i][1])
            else:
                out.append(redact_obj(m))
        # Keep the cache only for messages the next save will see again.
        self._redacted = list(zip(messages, out))
        return out

    def save(self, messages: list[dict], counters: dict) -> bool:
        """Checkpoint after a completed turn: append the new suffix of
        `messages` and the counters (secrets redacted, see the module
        docstring). True when the state is on disk; False (and `failures`
        + 1) when the write failed. Never raises."""
        try:
            msgs = self._redacted_messages(list(messages))
            ctr = dict(counters or {})
            if self.redact:
                ctr = redact_obj(ctr)
            self._save(msgs, ctr)
            return True
        except Exception as exc:  # noqa: BLE001 - a checkpoint never ends the loop
            log.warning("loop checkpoint: save failed (%s): %s", self.path, exc)
            self.failures += 1
            # Unknown disk state: re-derive it on the next save.
            self._synced = False
            self._redacted = []
            return False

    def _save(self, messages: list, counters: dict) -> None:
        if not self._synced:
            self._adopt_disk(messages)
        counters_canon = _canon(counters)
        chain = self._chain
        n = len(chain) - 1 if chain is not None else -1
        # Append-only check: the history still extends what we saved (the
        # last saved message is unchanged). Anything else starts over.
        if (chain is None or len(messages) < n
                or (n > 0 and _canon(messages[n - 1]) != self._last_canon)):
            new_canon = [_canon(m) for m in messages]
            chain = [_h0(self.key)]
            for c in new_canon:
                chain.append(_h(chain[-1], c))
            self._rewrite(self._record(0, messages, new_canon, counters_canon, chain))
        else:
            new_canon = [_canon(m) for m in messages[n:]]
            if not new_canon and counters_canon == self._last_counters:
                return  # double save of the same state: nothing to write
            chain = list(chain)
            for c in new_canon:
                chain.append(_h(chain[-1], c))
            self._append_line(self._record(n, messages[n:], new_canon, counters_canon,
                                           chain))
        self._chain = chain
        self._last_canon = _canon(messages[-1]) if messages else None
        self._last_counters = counters_canon

    def finish(self, stop_reason: str) -> None:
        """Mark the run done: load() returns None afterwards. Never raises."""
        try:
            if not self.path.exists():
                return
            rec = {"v": VERSION, "t": _FINISH, "key": self.key,
                   "stop_reason": str(stop_reason), "ts": round(time.time(), 3)}
            self._append_line((_canon(rec) + "\n").encode("ascii"))
            self._synced = False  # the next save starts a fresh run
        except Exception as exc:  # noqa: BLE001
            log.warning("loop checkpoint: finish failed (%s): %s", self.path, exc)

    def clear(self) -> None:
        """Delete the checkpoint file. Never raises."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("loop checkpoint: clear failed (%s): %s", self.path, exc)
        self._synced = False
        self._chain = [_h0(self.key)]
        self._last_canon = None
        self._last_counters = None

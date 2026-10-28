"""
test_baseline.py — only NEW test failures count (v0.2.2).

A test that already failed before AWOS touched anything (a missing optional
dependency, a flaky network test) used to count as "failed after the edit"
and trigger a rollback (docs/launch/demos/README.md, lesson 4).

The host worker checks the job's base commit out a second time, untouched,
as the baseline workspace (AWOS_BASELINE_WORKSPACE). When a test run reports
failures, the failing test ids are run once more there; an id that fails
there too is pre-existing. Only ids that pass at the base commit, or do not
exist there (a test the agent added), are new. Results are cached per
baseline dir and id, so each pre-existing failure is checked once per job.

This is the lazy form of "run the tests before editing": same answer for the
tests that matter (the ones that fail now), no full-suite run up front.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Optional

ENV_VAR = "AWOS_BASELINE_WORKSPACE"
DEFAULT_TIMEOUT_SEC = 180

#: pytest's short summary lines: "FAILED tests/a.py::test_x - AssertionError".
_ID_LINE = re.compile(r"^(?:FAILED|ERROR) (\S.*?)(?: - .*)?$", re.M)
_NOT_FOUND = re.compile(r"ERROR: (?:not found|file or directory not found): (\S+)")

#: (baseline dir, test id) -> True when the id fails at the base commit.
_CACHE: dict[tuple[str, str], bool] = {}


def baseline_dir() -> Optional[Path]:
    raw = os.getenv(ENV_VAR, "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def parse_failed_ids(text: str) -> list[str]:
    """Failing/erroring test ids from pytest's short summary (needs -rfE)."""
    ids = []
    for m in _ID_LINE.finditer(text or ""):
        tid = m.group(1).strip()
        if tid and tid not in ids and not tid.startswith(("collecting", "-")):
            ids.append(tid)
    return ids


def _relative(test_id: str, root: Path) -> str:
    """pytest names a not-found arg by its absolute path; ids here are relative."""
    if not os.path.isabs(test_id):
        return test_id
    for base in (os.path.realpath(root), str(root)):
        if test_id.startswith(base.rstrip(os.sep) + os.sep):
            return test_id[len(base.rstrip(os.sep)) + 1:]
    return test_id


def _file_of(test_id: str) -> str:
    return test_id.split("::", 1)[0]


def clear_cache() -> None:
    _CACHE.clear()


def failing_at_base(ids: list[str], argv: list[str], extra_args: list[str],
                    baseline: Path, env: dict, timeout: float = DEFAULT_TIMEOUT_SEC,
                    sandbox=None) -> Optional[set[str]]:
    """
    The subset of `ids` that also fail in the baseline workspace. None when the
    baseline run itself could not run (timeout, pytest crash): no evidence.
    `argv` is the pytest command prefix, `extra_args` the flags the original
    run used (minus targets). `sandbox`, when given, runs it there.
    """
    key_dir = os.path.realpath(baseline)
    pending = [i for i in ids if (key_dir, i) not in _CACHE]
    # Ids whose file does not exist at the base commit are new by definition.
    for tid in list(pending):
        if not (baseline / _file_of(tid)).exists():
            _CACHE[(key_dir, tid)] = False
            pending.remove(tid)
    for _attempt in range(2):
        if not pending:
            break
        cmd = list(argv) + list(extra_args) + ["-q", "--tb=no", "-rfE", "--continue-on-collection-errors",
                                               "-p", "no:cacheprovider",
                                               "--color=no"] + pending
        out = _run(cmd, baseline, env, timeout, sandbox)
        if out is None:
            return None
        text, code = out
        missing = {_relative(m, baseline) for m in _NOT_FOUND.findall(text)}
        if code == 4 and missing:
            # A test the agent added to an existing file: not at base, so new.
            for tid in missing:
                if tid in pending:
                    _CACHE[(key_dir, tid)] = False
                    pending.remove(tid)
            continue
        if code not in (0, 1):
            return None
        failing = set(parse_failed_ids(text))
        for tid in pending:
            # A file-level id ("tests/x.py" collection error) fails at base when
            # any id in that file failed there.
            _CACHE[(key_dir, tid)] = tid in failing or any(
                f == tid or f.startswith(tid + "::") for f in failing)
        pending = []
    if pending:
        return None
    return {i for i in ids if _CACHE.get((key_dir, i))}


def _run(cmd: list[str], cwd: Path, env: dict, timeout: float, sandbox) -> Optional[tuple[str, int]]:
    if sandbox is not None:
        assigns = " ".join(f"{k}={shlex.quote(env[k])}" for k in ("PYTHONPATH",) if env.get(k))
        res = sandbox.run((assigns + " " if assigns else "") + shlex.join(cmd), timeout_sec=int(timeout))
        if res.timed_out:
            return None
        return f"{res.stdout}\n{res.stderr}", res.exit_code
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return f"{proc.stdout}\n{proc.stderr}", proc.returncode

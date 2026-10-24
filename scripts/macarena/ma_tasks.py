"""MacArena task loading + the official final-state evaluator semantics (AWOS code, MIT).

The benchmark (github.com/MacPaw/MacArena) is NOT vendored: the runner reads task JSON from a
checkout fetched at run time (pinned commit in .github/workflows/mac-arena.yml). Licences of
the task data: macarena/ MIT (MacPaw), macosworld/ CC BY-NC 4.0 (showlab; non-commercial),
osworld/ Apache-2.0. We publish results by task id only and never commit task files.

Task files are data. Their ``pre_command`` and evaluator scripts are the benchmark's own
setup / grading code; they only ever run inside a disposable guest VM (see runner guard).
The instruction text is handed to an agent as the task description, never executed.

Evaluator semantics reproduced from MacArena desktop_env.DesktopEnv._macosworld_evaluate
and vm_files/server/main.py /run_bash_script:
  * only checks whose score == 100 count; they are tried in order;
  * each check is written to a temp file (``#!/bin/bash`` prepended when absent) and run
    with /bin/bash, stdout+stderr merged, 30 s timeout (the controller's default);
  * exit 0 and "true" in output.lower()  -> score 1.0, stop;
  * exit 0 otherwise                     -> next check;
  * non-zero exit / timeout              -> score 0 with an error, stop.
The lower-score checks (partial credit in macOSWorld) are not part of the official score;
we run them afterwards only as a diagnostic (``partial``).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

EVAL_TIMEOUT_S = 30.0
PRE_TIMEOUT_S = 120.0
ADMIN_HOME = "/Users/admin"
FAMILIES = ("macarena", "macosworld", "osworld")


@dataclass
class Task:
    id: str
    family: str
    category: str
    instruction: str
    pre_command: str
    evaluator: list
    apps: List[str]
    before_action_delay: float
    before_grading_delay: float
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def list_evaluator(self) -> bool:
        return isinstance(self.evaluator, list)


def find_task_file(root: Path, task_id: str) -> Optional[Path]:
    hits = [p for p in root.rglob(f"{task_id}.json") if len(p.relative_to(root).parts) >= 3]
    return hits[0] if hits else None


def load_task(root: Path, task_id: str) -> Task:
    p = find_task_file(root, task_id)
    if p is None:
        raise FileNotFoundError(f"task {task_id} not found under {root}")
    d = json.loads(p.read_text(encoding="utf-8"))
    fam, cat = p.relative_to(root).parts[:2]
    return Task(id=d["id"], family=fam, category=cat, instruction=str(d.get("instruction", "")),
                pre_command=str(d.get("pre_command") or ""), evaluator=d.get("evaluator"),
                apps=list(d.get("related_apps") or []),
                before_action_delay=float(d.get("before_action_delay_seconds") or 0),
                before_grading_delay=float(d.get("before_grading_delay_seconds") or 0), raw=d)


def rewrite_home(cmd: str, home: Optional[str] = None) -> str:
    """The MacArena VM user is ``admin``; on a GitHub runner the user is ``runner``."""
    home = home or os.path.expanduser("~")
    if home.rstrip("/") == ADMIN_HOME:
        return cmd
    return re.sub(r"/Users/admin(?=/|\b)", home.rstrip("/"), cmd)


@dataclass
class CheckResult:
    score: int
    returncode: Optional[int]
    passed: bool
    output: str
    seconds: float
    error: str = ""

    def to_dict(self) -> dict:
        return {"score": self.score, "rc": self.returncode, "true": self.passed,
                "output": self.output[-400:], "seconds": round(self.seconds, 2), "error": self.error}


Runner = Callable[[str, float], Tuple[Optional[int], str, str]]


def run_bash_check(script: str, timeout: float = EVAL_TIMEOUT_S) -> Tuple[Optional[int], str, str]:
    """(returncode, merged output, error) the way MacArena's VM server runs a check."""
    if "#!/bin/bash" not in script:
        script = "#!/bin/bash\n\n" + script
    fd, path = tempfile.mkstemp(suffix=".sh")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(script)
        os.chmod(path, 0o755)
        try:
            r = subprocess.run(["/bin/bash", path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, timeout=timeout)
            return r.returncode, r.stdout or "", ""
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            return None, out, f"timeout after {timeout:.0f}s"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def evaluate(evaluator: Sequence, *, runner: Runner = run_bash_check, home: Optional[str] = None,
             timeout: float = EVAL_TIMEOUT_S, diagnostics: bool = True) -> dict:
    """Official score (0.0 / 1.0) plus every check's raw result."""
    if not isinstance(evaluator, list):
        return {"score": None, "error": "osworld-style evaluator (getters/metrics) not supported", "checks": []}
    checks: List[CheckResult] = []
    score, error = 0.0, ""
    official = [(str(c[0]), int(c[1])) for c in evaluator if isinstance(c, (list, tuple)) and len(c) >= 2]
    for cmd, sc in [c for c in official if c[1] == 100]:
        t0 = time.time()
        rc, out, err = runner(rewrite_home(cmd, home), timeout)
        ok = rc == 0 and "true" in out.strip().lower()
        checks.append(CheckResult(sc, rc, ok, out, time.time() - t0, err))
        if rc != 0:
            error = err or f"check exited {rc}"
            break
        if ok:
            score = 1.0
            break
    partial = 0
    if diagnostics and score < 1.0:
        for cmd, sc in [c for c in official if c[1] != 100]:
            t0 = time.time()
            rc, out, err = runner(rewrite_home(cmd, home), timeout)
            ok = rc == 0 and "true" in out.strip().lower()
            checks.append(CheckResult(sc, rc, ok, out, time.time() - t0, err))
            if ok:
                partial = max(partial, sc)
    return {"score": score, "error": error, "partial_diag": partial if score < 1.0 else 100,
            "checks": [c.to_dict() for c in checks]}


def run_pre_command(cmd: str, *, home: Optional[str] = None, timeout: float = PRE_TIMEOUT_S) -> dict:
    """MacArena launches pre_command with Popen(shell=True) (/bin/sh). We wait for it (bounded)."""
    if not cmd.strip():
        return {"rc": 0, "output": "", "seconds": 0.0, "skipped": True}
    t0 = time.time()
    try:
        r = subprocess.run(["/bin/sh", "-c", rewrite_home(cmd, home)], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=timeout)
        return {"rc": r.returncode, "output": (r.stdout or "")[-600:], "seconds": round(time.time() - t0, 2)}
    except subprocess.TimeoutExpired:
        return {"rc": None, "output": "", "seconds": round(time.time() - t0, 2), "error": "timeout"}

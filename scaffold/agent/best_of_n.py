"""
Ablation A — best-of-N first attempts (docs/specs/ablation_best_of_n.md).

AWOS_BEST_OF_N (default 1 = today's single one-shot, untouched). With N > 1
the one-shot phase samples N independent first attempts and keeps the best:

1. Sample. The context is built once, from the real workspace. Each candidate
   runs `run_one_shot` (its call, apply, and the usual repair call when blocks
   miss) in its own scratch copy of the workspace, N threads in parallel, at
   temperature AWOS_BEST_OF_N_TEMPERATURE (0.7). The real workspace is never
   edited by a candidate's call.
2. Score. Tests run sequentially in the REAL workspace, with the same
   TestRunner and sandbox as today (the sandbox is bound to that root): the
   workspace is snapshotted, a candidate's changed files are written in, the
   visible suite runs (and the acceptance suite when AWOS_ACCEPTANCE is active),
   and the snapshot is restored, so no candidate leaves anything behind.
   Score, best first: visible green with every block applied (the state a
   one-shot counts as solved today); then visible green; then acceptance tests
   passed; then fewest visible failures + errors; then smallest diff; then the
   earliest candidate.
3. Choose. The winner's files are written to the real workspace, which then
   holds exactly what it would had only the winner been applied. Its verdict
   is handed back so the caller does not re-run the suite; acceptance and the
   fallback note are then decided exactly as for a single one-shot.
4. Account. Every call's cost and tokens are summed into the returned result;
   its elapsed time is the phase's wall time. A malformed reply, failed call or
   failed apply is just a losing candidate.
"""

from __future__ import annotations

import difflib
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from . import one_shot as one_shot_mod
    from .acceptance import restore_workspace, snapshot_workspace
except ImportError:  # flat-import layout (scaffold/agent on sys.path)
    import one_shot as one_shot_mod
    from acceptance import restore_workspace, snapshot_workspace

DEFAULT_TEMPERATURE = 0.7
MAX_N = 8

#: Not copied into a candidate's scratch workspace: no edit may land there
#: (.git/.awos are refused by apply_blocks) and they can be large.
_SCRATCH_IGNORE = shutil.ignore_patterns(
    ".git", ".awos", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".nox", "*.pyc",
)


def best_of_n() -> int:
    """AWOS_BEST_OF_N as an int in 1..MAX_N; anything unparsable is 1."""
    try:
        n = int(os.getenv("AWOS_BEST_OF_N", "1") or "1")
    except ValueError:
        return 1
    return max(1, min(MAX_N, n))


def temperature() -> float:
    try:
        return float(os.getenv("AWOS_BEST_OF_N_TEMPERATURE", "") or DEFAULT_TEMPERATURE)
    except ValueError:
        return DEFAULT_TEMPERATURE


@dataclass
class Candidate:
    index: int                                   # 1-based
    shot: Any = None                             # OneShotResult
    files: dict = field(default_factory=dict)    # rel -> bytes (the candidate's version)
    diff_lines: int = 0
    verdict: Optional[dict] = None
    tests_green: bool = False
    clean: bool = False                          # every block applied, not cut off
    bad_tests: Optional[int] = None              # visible failed + errors (None: not run)
    acc_passed: Optional[int] = None
    error: str = ""
    cost_usd: float = 0.0                        # this candidate's own calls
    elapsed_s: float = 0.0

    @property
    def solved_green(self) -> bool:
        return self.tests_green and self.clean

    def score(self) -> tuple:
        """Larger is better (compared as a tuple)."""
        if not self.files:
            return (-1, 0, 0, 0, 0, -self.index)
        bad = self.bad_tests if self.bad_tests is not None else 10 ** 9
        return (int(self.solved_green), int(self.tests_green), self.acc_passed or 0,
                -bad, -self.diff_lines, -self.index)

    def line(self) -> str:
        s = self.shot
        head = f"[BEST-OF-N] #{self.index}: ${self.cost_usd:.4f}, {self.elapsed_s:.1f}s"
        if self.error:
            return f"{head}; loser: {self.error}"
        tr = (self.verdict or {}).get("test_result")
        tests = (f"tests {tr.passed} passed {tr.failed} failed {tr.errors} errors"
                 if self.bad_tests is not None else "tests did not run")
        return (f"{head}; {s.blocks} block(s), {len(self.files)} file(s), "
                f"{len(s.failed)} failed; {tests}; "
                + ("GREEN" if self.solved_green else
                   "green with failed blocks" if self.tests_green else "red")
                + (f"; acceptance {self.acc_passed} passed" if self.acc_passed is not None
                   else "")
                + f"; diff {self.diff_lines} line(s)")


@dataclass
class BestOfNResult:
    shot: Any                          # the winner's OneShotResult, totals summed
    verdict: Optional[dict]            # the winner's visible-test verdict (None: not run)
    chosen: int                        # 1-based index of the winner
    candidates: list = field(default_factory=list)


def _diff_lines(old: Optional[bytes], new: Optional[bytes]) -> int:
    a = (old or b"").decode("utf-8", errors="replace").splitlines()
    b = (new or b"").decode("utf-8", errors="replace").splitlines()
    return sum(1 for ln in difflib.unified_diff(a, b, lineterm="", n=0)
               if ln[:1] in "+-" and not ln.startswith(("+++", "---")))


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _write_files(root: Path, files: dict) -> None:
    for rel, data in files.items():
        target = root / rel
        if data is None:
            if target.is_file():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _sample(i: int, *, client, model, root: str, task_text: str, ctx, tracker,
            allow_test_edits: bool, temp: float) -> Candidate:
    """One candidate in its own scratch copy; never raises."""
    cand = Candidate(index=i)
    scratch = tempfile.mkdtemp(prefix=f"awos-bon{i}-")
    try:
        work = os.path.join(scratch, "ws")
        shutil.copytree(root, work, symlinks=True, ignore=_SCRATCH_IGNORE)
        shot = one_shot_mod.run_one_shot(
            client, model, work, task_text, tracker=tracker,
            allow_test_edits=allow_test_edits, temperature=temp, context=ctx)
        cand.shot = shot
        if shot.error:
            cand.error = f"model call failed ({shot.error[:200]})"
            return cand
        for rel in shot.applied:
            cand.files[rel] = _read(Path(work) / rel)
        if not cand.files:
            cand.error = ("no edit applied" + (f" ({len(shot.failed)} block(s) failed)"
                                                if shot.failed else " (no blocks)"))
        cand.clean = not shot.failed and not shot.truncated
    except Exception as exc:  # noqa: BLE001 — a broken candidate only loses
        cand.error = f"{type(exc).__name__}: {str(exc)[:200]}"
        if cand.shot is None:
            cand.shot = one_shot_mod.OneShotResult(error=cand.error, calls=1)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    cand.cost_usd = float(getattr(cand.shot, "cost_usd", 0.0) or 0.0)
    cand.elapsed_s = float(getattr(cand.shot, "elapsed_s", 0.0) or 0.0)
    return cand


def run_best_of_n(
    n: int, *, client: Any, model: str, codebase_root: str, task_text: str,
    exploration: Optional[dict], tracker: Any, allow_test_edits: bool,
    judge: Callable[[list], dict],
    acceptance_passed: Optional[Callable[[], Optional[int]]] = None,
) -> BestOfNResult:
    """
    Sample n candidates, score them on the visible suite in the real workspace,
    and leave the winner applied there. `judge(applied)` returns the verdict
    dict `_judge_agent_attempt` returns; `acceptance_passed()` the number of
    kept acceptance tests passing now (None when the gate is inactive).
    Never raises.
    """
    start = time.time()
    root = Path(codebase_root).resolve()
    try:
        ctx = one_shot_mod.build_context(codebase_root, task_text, exploration)
    except Exception as exc:  # noqa: BLE001
        shot = one_shot_mod.OneShotResult(error=f"context build failed: {exc}")
        return BestOfNResult(shot=shot, verdict=None, chosen=1)

    temp = temperature()
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(_sample, i, client=client, model=model, root=str(root),
                               task_text=task_text, ctx=ctx, tracker=tracker,
                               allow_test_edits=allow_test_edits, temp=temp)
                   for i in range(1, n + 1)]
        cands = [f.result() for f in futures]

    for c in cands:
        c.diff_lines = sum(_diff_lines(_read(root / rel), data) for rel, data in c.files.items())

    # Score: sequential test runs in the real workspace, restored after each.
    snap = None
    if any(c.files for c in cands):
        try:
            snap = snapshot_workspace(str(root))
        except Exception as exc:  # noqa: BLE001
            print(f"[BEST-OF-N] cannot snapshot the workspace ({exc}); "
                  "choosing without running tests")
    if snap is not None:
        for c in cands:
            if not c.files:
                continue
            try:
                _write_files(root, c.files)
                c.verdict = judge(list(c.files))
                tr = c.verdict.get("test_result")
                if tr is not None and not getattr(tr, "no_tests_found", True):
                    c.bad_tests = int(tr.failed) + int(tr.errors)
                    c.tests_green = c.bad_tests == 0 and tr.passed > 0
                if acceptance_passed is not None:
                    c.acc_passed = acceptance_passed()
            except Exception as exc:  # noqa: BLE001
                c.error = c.error or f"scoring failed: {type(exc).__name__}: {exc}"
                c.verdict, c.tests_green, c.bad_tests = None, False, None
            finally:
                restore_workspace(snap)

    best = max(cands, key=lambda c: c.score())
    if best.files:
        _write_files(root, best.files)

    # The winner's result, carrying every candidate's spend.
    win = best.shot
    win.cost_usd = sum(c.cost_usd for c in cands)
    win.input_tokens = sum(int(c.shot.input_tokens or 0) for c in cands)
    win.output_tokens = sum(int(c.shot.output_tokens or 0) for c in cands)
    win.calls = sum(int(c.shot.calls or 0) for c in cands)
    win.elapsed_s = time.time() - start
    if not best.files:
        # Nothing applied anywhere: the caller falls back as for a failed shot.
        win.applied = []

    for c in cands:
        print(c.line())
    greens = sum(c.solved_green for c in cands)
    print(f"[BEST-OF-N] {greens}/{n} green; chose #{best.index} (score "
          f"{best.score()[:5]}); ${win.cost_usd:.4f}, {win.elapsed_s:.1f}s, T={temp}")
    verdict = best.verdict if (best.files and snap is not None) else None
    return BestOfNResult(shot=win, verdict=verdict, chosen=best.index, candidates=cands)

"""
chores.py — load, stage and validate computer-use chores (the C10 set).

See docs/specs/computer_use_foundations.md (T10). A chore is a directory:

    <chore>/chore.json   spec: instruction, start state, probes, invariants
    <chore>/solve.py     scripted reference solution, run with cwd = scratch

chore.json:
    {"id": "...", "title": "...", "instruction": "...",
     "rung": "shell" | "applescript" | "ax" | "pixels",
     "platform": "darwin",                     # optional; skip elsewhere
     "start": {"files":  {"rel/path": "text"},
               "plists": {"rel/path.plist": {...}},
               "sqlite": {"rel/db.sqlite": ["CREATE ...", "INSERT ..."]}},
     "probes":     [Probe, ...],   # each must FAIL on start, PASS on end
     "invariants": [Probe, ...]}   # each must PASS on start AND on end

``validate_chore`` stages the start state twice (start / end), runs the
reference solution on the end copy, and applies the V1 rule to each probe.
"""
from __future__ import annotations

import json
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .probes import Probe, ProbeResult, validate_probe

DEFAULT_CHORE_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "desktop_chores"
SOLVE_TIMEOUT_S = 30


@dataclass
class Chore:
    id: str
    title: str
    instruction: str
    rung: str
    start: dict
    probes: List[Probe]
    invariants: List[Probe]
    platform: Optional[str]
    directory: Path

    @classmethod
    def load(cls, directory: Path | str) -> "Chore":
        d = Path(directory)
        spec = json.loads((d / "chore.json").read_text(encoding="utf-8"))
        return cls(
            id=spec["id"], title=spec.get("title", spec["id"]),
            instruction=spec["instruction"], rung=spec.get("rung", "shell"),
            start=spec.get("start", {}),
            probes=[Probe.from_dict(p) for p in spec["probes"]],
            invariants=[Probe.from_dict(p) for p in spec.get("invariants", [])],
            platform=spec.get("platform"), directory=d,
        )

    @property
    def supported(self) -> bool:
        return self.platform in (None, sys.platform)

    def stage(self, root: Path | str) -> Path:
        """Materialise the start state under root."""
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        for rel, text in self.start.get("files", {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        for rel, data in self.start.get("plists", {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "wb") as f:
                plistlib.dump(data, f, fmt=plistlib.FMT_XML)
        for rel, stmts in self.start.get("sqlite", {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(p)
            try:
                for s in stmts:
                    con.execute(s)
                con.commit()
            finally:
                con.close()
        return root

    def run_reference(self, root: Path | str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", str(self.directory / "solve.py")],
                              cwd=str(root), capture_output=True, text=True,
                              timeout=SOLVE_TIMEOUT_S)


@dataclass
class ChoreValidation:
    chore_id: str
    skipped: bool = False
    reason: str = ""
    solve_ok: bool = False
    probe_checks: list = field(default_factory=list)      # ProbeValidity
    invariant_results: list = field(default_factory=list)  # (start, end) ProbeResult

    @property
    def valid(self) -> bool:
        if self.skipped:
            return False
        return (self.solve_ok and bool(self.probe_checks)
                and all(v.valid for v in self.probe_checks)
                and all(s.passed and e.passed for s, e in self.invariant_results))

    def summary(self) -> str:
        if self.skipped:
            return f"{self.chore_id}: SKIP ({self.reason})"
        bad = [v.probe_id for v in self.probe_checks if not v.valid]
        bad += [s.probe_id for s, e in self.invariant_results if not (s.passed and e.passed)]
        n = len(self.probe_checks)
        return (f"{self.chore_id}: {'VALID' if self.valid else 'INVALID'} "
                f"({n} probes, {len(self.invariant_results)} invariants"
                + (f"; bad: {bad}" if bad else "") + (f"; {self.reason}" if self.reason else "") + ")")


def validate_chore(chore: Chore, workdir: Path | str | None = None) -> ChoreValidation:
    res = ChoreValidation(chore.id)
    if not chore.supported:
        res.skipped, res.reason = True, f"needs {chore.platform}"
        return res
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix=f"chore_{chore.id}_"))
    start, end = tmp / "start", tmp / "end"
    try:
        chore.stage(start)
        chore.stage(end)
        proc = chore.run_reference(end)
        res.solve_ok = proc.returncode == 0
        if not res.solve_ok:
            res.reason = f"solve.py exit {proc.returncode}: {proc.stderr.strip()[-300:]}"
        res.probe_checks = [validate_probe(p, start, end) for p in chore.probes]
        res.invariant_results = [(p.evaluate(start), p.evaluate(end)) for p in chore.invariants]
    finally:
        if workdir is None:
            shutil.rmtree(tmp, ignore_errors=True)
    return res


def load_all(chore_dir: Path | str = DEFAULT_CHORE_DIR) -> List[Chore]:
    d = Path(chore_dir)
    return [Chore.load(c) for c in sorted(d.iterdir()) if (c / "chore.json").exists()]


def main(argv: Optional[list] = None) -> int:
    chores = load_all(argv[0] if argv else DEFAULT_CHORE_DIR)
    results = [validate_chore(c) for c in chores]
    for r in results:
        print(r.summary())
    ran = [r for r in results if not r.skipped]
    ok = sum(r.valid for r in ran)
    print(f"C10 validation: {ok}/{len(ran)} valid, {len(results) - len(ran)} skipped")
    return 0 if ok == len(ran) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

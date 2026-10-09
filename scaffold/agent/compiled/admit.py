"""
admit.py — the admission-harness SKELETON for compiled Tools (spec §6, step 2/4).

Checks run here, each on a fresh golden clone (`git clone` of the golden repo at
its HEAD, one clone per case):

  A1 start-state probe   probe(params) is ok=false on the untouched clone, every case
  A2 held-out replay     8 cases from gen_params + 4 from indep_params; guard passes,
                         run, probe ok. A Tool needs 12/12
  A3 collateral/idempotent  `git status` shows only the declared write set; a second
                         run either refuses cleanly at the guard or leaves the same tree
  A4 near-miss guard     >= 5 near-misses, every one refused by the guard (matcher
                         anti-triggers, params schema, fingerprint, tool preconditions)
                         before any write; one leak rejects
  A6 probe sensitivity   each mutation of a correct end state makes the probe say false

Not in the skeleton (spec §6): A2b trace rewrite, A5 regression set, and running
inside make_sandbox(). The tool runs in-process with cwd untouched; only the clone
is written. Only hand-written, trusted Tools may use this skeleton.

Run `python -m scaffold.agent.compiled.admit --demo` for the live proof: it builds
a temp golden repo, admits the bump_version Tool, and prints the promotion math.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Optional

try:
    from . import beta
    from .record import (Evidence, Intent, NearMiss, Preconditions, Probe, Record,
                         check_fingerprint, compute_fingerprint, validate_params)
except ImportError:  # pragma: no cover — script use
    from scaffold.agent.compiled import beta  # type: ignore
    from scaffold.agent.compiled.record import (  # type: ignore
        Evidence, Intent, NearMiss, Preconditions, Probe, Record,
        check_fingerprint, compute_fingerprint, validate_params)

N_GEN = 8
N_INDEP = 4
MIN_NEAR_MISS = 5
_GIT_ENV = {"GIT_AUTHOR_NAME": "awos", "GIT_AUTHOR_EMAIL": "awos@example.invalid",
            "GIT_COMMITTER_NAME": "awos", "GIT_COMMITTER_EMAIL": "awos@example.invalid",
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}


# ── Deterministic trigger matcher (minimal; the full matcher is step 7) ───────

_SLOT_RE = {"<semver>": r"v?\d+\.\d+\.\d+", "<num>": r"\d+"}


def _phrase_re(phrase: str) -> re.Pattern:
    parts = []
    for tok in phrase.lower().split():
        parts.append(_SLOT_RE.get(tok, re.escape(tok)))
    # words in order, up to 3 other words between them
    return re.compile(r"\b" + r"(?:\W+\S+){0,3}?\W+".join(parts) + r"\b")


def match_intent(goal: str, intent: Intent) -> tuple[bool, str]:
    g = (goal or "").lower()
    for a in intent.anti_triggers:
        if re.search(r"\b" + re.escape(a.lower()), g):
            return False, f"anti-trigger {a!r}"
    for t in intent.triggers:
        if _phrase_re(t).search(g):
            return True, f"trigger {t!r}"
    return False, "no trigger"


# ── Harness types ─────────────────────────────────────────────────────────────

@dataclass
class Check:
    name: str
    ok: bool
    n: int = 0
    passed: int = 0
    detail: list[str] = field(default_factory=list)


@dataclass
class AdmissionReport:
    record_id: str
    family: str
    checks: list[Check]
    admitted: bool
    executions: list[dict]
    wall_s: float
    skipped: list[str] = field(default_factory=lambda: ["A2b trace rewrite", "A5 regression set",
                                                       "make_sandbox()"])

    def to_text(self) -> str:
        lines = [f"admission {self.record_id} ({self.family}): "
                 f"{'ADMITTED' if self.admitted else 'REJECTED'} in {self.wall_s:.1f}s"]
        for c in self.checks:
            lines.append(f"  {c.name:<26} {'PASS' if c.ok else 'FAIL'}  {c.passed}/{c.n}")
            for d in c.detail:
                lines.append(f"      {d}")
        lines.append("  not run (skeleton): " + ", ".join(self.skipped))
        return "\n".join(lines)


def _git(root: Path, *argv: str) -> str:
    return subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True,
                          text=True, env=_GIT_ENV).stdout


def changed_files(root: Path) -> list[str]:
    out = []
    for line in _git(root, "status", "--porcelain", "--untracked-files=all").splitlines():
        p = line[3:]
        out.append(p.split(" -> ")[-1])
    return sorted(out)


def tree_state(root: Path) -> str:
    """Hash of the working tree content (tracked + untracked, not ignored)."""
    _git(root, "add", "-A", "--intent-to-add")
    return _git(root, "diff", "HEAD", "--no-color")


class Harness:
    def __init__(self, record: Record, tool: ModuleType, golden: Path,
                 workdir: Optional[Path] = None, n_gen: int = N_GEN, n_indep: int = N_INDEP,
                 min_near_miss: int = MIN_NEAR_MISS, log: Callable[[str], None] = lambda s: None):
        self.record, self.tool, self.golden = record, tool, Path(golden)
        self.workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="awos-admit-"))
        self.n_gen, self.n_indep, self.min_near_miss = n_gen, n_indep, min_near_miss
        self.write_set = set((record.body.get("tool") or {}).get("write_set") or [])
        self.log = log
        self._n = 0

    # golden clone: a fresh clone of the golden repo's HEAD per case
    def clone(self) -> Path:
        self._n += 1
        dst = self.workdir / f"case{self._n:03d}"
        subprocess.run(["git", "clone", "-q", str(self.golden), str(dst)], check=True,
                       capture_output=True, env=_GIT_ENV)
        return dst

    def guard(self, goal: str, params: Any, root: Path) -> tuple[bool, str]:
        """Read-only; every layer must say yes. Returns (ok, refusing layer: reason)."""
        ok, why = match_intent(goal, self.record.intent)
        if not ok:
            return False, f"matcher: {why}"
        errs = validate_params(self.record.params_schema, params)
        if errs:
            return False, f"schema: {errs[0]}"
        drift = check_fingerprint(root, self.record.preconditions.fingerprint)
        if drift:
            return False, f"fingerprint (suspend): {drift[0]}"
        ok, why = self.tool.preconditions(params, {"root": root})
        if not ok:
            return False, f"preconditions: {why}"
        return True, "ok"

    def cases(self, seed0: int = 1000) -> list[tuple[str, dict]]:
        out, seen = [], set()
        for src, gen, n in (("gen", self.tool.gen_params, self.n_gen),
                            ("indep", self.tool.indep_params, self.n_indep)):
            seed = seed0
            while sum(1 for s, _ in out if s == src) < n:
                p = gen(seed)
                seed += 1
                key = json.dumps(p, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    out.append((src, p))
        return out

    def run(self, seed0: int = 1000) -> AdmissionReport:
        t0 = time.time()
        rec, tool = self.record, self.tool
        a1 = Check("A1 start-state probe", True)
        a2 = Check("A2 held-out replay", True)
        a3 = Check("A3 collateral/idempotent", True)
        a6 = Check("A6 probe sensitivity", True)
        execs: list[dict] = []
        for src, params in self.cases(seed0):
            root = self.clone()
            ctx = {"root": root}
            goal = tool.goal_for(params)
            a1.n += 1
            if tool.probe(params, ctx)["ok"]:
                a1.ok = False
                a1.detail.append(f"probe already ok on start for {params}")
            else:
                a1.passed += 1
            a2.n += 1
            ok, why = self.guard(goal, params, root)
            probe_res: dict = {"ok": None}
            outcome = "precondition_refused"
            if ok:
                try:
                    tool.run(params, ctx)
                    probe_res = tool.probe(params, ctx)
                    outcome = "ok" if probe_res["ok"] else "probe_fail"
                except Exception as exc:  # noqa: BLE001
                    probe_res, outcome = {"ok": False, "error": str(exc)}, "run_error"
            execs.append({"record_id": rec.id, "version": rec.version, "tier": "admit",
                          "source": src, "params": params, "goal": goal,
                          "precondition_result": why, "probe_result": probe_res,
                          "outcome": outcome, "llm_calls": 0, "cost_usd": 0.0,
                          "sandbox": None, "ts": time.time()})
            if outcome != "ok":
                a2.ok = False
                a2.detail.append(f"{src} {params}: {outcome} ({why}) {probe_res}")
                continue
            a2.passed += 1
            self.log(f"  A2 {src:<5} {params['new_version'] if 'new_version' in params else params}"
                     f"  guard ok -> run -> probe ok")
            # A3: collateral writes, then idempotence
            a3.n += 1
            extra = [f for f in changed_files(root) if f not in self.write_set]
            before = tree_state(root)
            again, why2 = self.guard(goal, params, root)
            if not again:
                same = True
            else:
                tool.run(params, ctx)
                same = tree_state(root) == before
            if extra or not same:
                a3.ok = False
                a3.detail.append(f"{params}: collateral={extra} idempotent={same}")
            else:
                a3.passed += 1
            # A6: each mutation of the correct end state must fail the probe
            for name, mutate in tool.mutations(params):
                _git(root, "checkout", "-q", "--", ".")
                _git(root, "clean", "-fdq")
                _git(root, "reset", "-q")
                tool.run(params, ctx)
                mutate(root)
                a6.n += 1
                if tool.probe(params, ctx)["ok"]:
                    a6.ok = False
                    a6.detail.append(f"{params}: probe ok after '{name}' (vacuous)")
                else:
                    a6.passed += 1
        a2.ok = a2.ok and a2.passed == a2.n == self.n_gen + self.n_indep
        a6.ok = a6.ok and a6.n > 0

        a4 = Check("A4 near-miss guard", True)
        for nm in tool.near_misses(seed0):
            root = self.clone()
            if nm.get("mutate"):
                nm["mutate"](root)
            before = changed_files(root)
            ok, why = self.guard(nm["goal"], nm["params"], root)
            a4.n += 1
            if ok or changed_files(root) != before:
                a4.ok = False
                a4.detail.append(f"LEAK {nm['goal']!r} ({nm['why']})")
            else:
                a4.passed += 1
                a4.detail.append(f"refused {nm['goal']!r:<52} <- {why}")
        if a4.n < self.min_near_miss:
            a4.ok = False
            a4.detail.append(f"only {a4.n} near-misses (< {self.min_near_miss})")

        checks = [a1, a2, a3, a4, a6]
        admitted = all(c.ok for c in checks)
        return AdmissionReport(rec.id, rec.family, checks, admitted, execs, time.time() - t0)


def apply_admission(rec: Record, report: AdmissionReport) -> dict:
    """Fold the report into the record (A2 passes become s; f = 0) and return its dict."""
    d = rec.to_dict()
    if not report.admitted:
        return beta.apply_event(d, "reject", detail="; ".join(
            c.name for c in report.checks if not c.ok))
    folded = beta.fold_executions(report.executions, rec.id)
    ev = d["evidence"]
    ev.update({k: folded[k] for k in ("s", "f", "s_live", "f_live", "s_indep")})
    ev["consecutive_ok"] = folded["s"]
    near = next(c for c in report.checks if c.name.startswith("A4"))
    held = next(c for c in report.checks if c.name.startswith("A2"))
    ev["admission"] = {"held_out_n": held.n, "pass": held.passed,
                       "near_miss_n": near.n, "refused": near.passed,
                       "regression_set": None, "regressions": None}
    d["probe"]["fails_on_start"] = True
    return beta.apply_event(d, "admit", detail="harness skeleton A1-A4,A6")


def top_up(rec_d: dict, harness: Harness, n: int, seed0: int = 5000) -> list[dict]:
    """Nightly synthetic top-up (spec §7): n more replays; ok/fail events update the record."""
    execs = []
    for i in range(n):
        src = "indep" if i % 3 == 2 else "gen"
        gen = harness.tool.indep_params if src == "indep" else harness.tool.gen_params
        params = gen(seed0 + i)
        root = harness.clone()
        ctx = {"root": root}
        ok, why = harness.guard(harness.tool.goal_for(params), params, root)
        if not ok:
            continue
        harness.tool.run(params, ctx)
        res = harness.tool.probe(params, ctx)
        beta.apply_event(rec_d, "ok" if res["ok"] else "fail", indep=src == "indep")
        execs.append({"record_id": rec_d["id"], "source": src, "params": params,
                      "probe_result": res, "outcome": "ok" if res["ok"] else "probe_fail"})
    return execs


def build_record(tool: ModuleType, golden: Path) -> Record:
    """The Record for a hand-written Tool module, fingerprinted against `golden`."""
    return Record(
        kind="tool", family=tool.FAMILY, intent=Intent(**tool.INTENT),
        params_schema=tool.PARAMS_SCHEMA,
        preconditions=Preconditions(
            repo_key=Path(golden).name,
            fingerprint=compute_fingerprint(golden, tool.READ_SET,
                                            {"python": "3.12"}),
            state_predicates=["git.clean"]),
        body={"tool": {"entry": f"{tool.__name__}:run", "sinks": ["fs.write:worktree"],
                       "write_set": list(tool.WRITE_SET)}},
        probe=Probe(entry=f"{tool.__name__}:probe",
                    kinds=["toml_value", "file_contains"]),
        near_miss=NearMiss(generator=f"{tool.__name__}:near_misses",
                           examples=[n["goal"] for n in tool.near_misses(0)][:3]),
        evidence=Evidence(),
        maker={"model": "hand-written", "cost_usd": 0.0},
    )


def demo(keep: bool = False) -> int:
    from .examples import bump_version as tool
    tmp = Path(tempfile.mkdtemp(prefix="awos-compiled-demo-"))
    try:
        golden = tool.make_demo_repo(tmp / "golden")
        rec = build_record(tool, golden)
        print(f"record {rec.id}  family={rec.family}  kind={rec.kind}  state={rec.state}")
        print(f"taint fields: {rec.taint()}")
        h = Harness(rec, tool, golden, workdir=tmp / "clones", log=print)
        report = h.run()
        print(report.to_text())
        d = apply_admission(rec, report)
        print(f"\nstate after admission: {d['state']}  evidence: s={d['evidence']['s']} "
              f"f={d['evidence']['f']} s_indep={d['evidence']['s_indep']} "
              f"s_live={d['evidence']['s_live']} lb95={d['evidence']['lb95']}")
        print("\npromotion math (spec §7):")
        print(beta.table())
        print("now:", json.dumps(beta.promotion_check(d["evidence"])))
        if d["state"] == "admitted":
            top_up(d, h, 16)
            ev = d["evidence"]
            print(f"after 16 synthetic top-up replays: s={ev['s']} f={ev['f']} "
                  f"s_indep={ev['s_indep']} lb95={ev['lb95']} state={d['state']}")
            print("now:", json.dumps(beta.promotion_check(ev)))
            sim = json.loads(json.dumps(d))
            for _ in range(3):
                beta.apply_event(sim, "ok", live=True)
            print(f"simulated 3 live probe-ok R1 runs -> state={sim['state']} "
                  f"lb95={sim['evidence']['lb95']}")
            beta.apply_event(sim, "fail", detail="simulated probe fail at R0")
            print(f"simulated 1 probe fail at R0 -> state={sim['state']} "
                  f"lb95={sim['evidence']['lb95']} (need s>={beta.successes_needed(1)} at f=1)")
        return 0 if report.admitted else 1
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"kept {tmp}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Compiled-tool admission harness (skeleton)")
    ap.add_argument("--demo", action="store_true", help="admit the bump_version example")
    ap.add_argument("--keep", action="store_true", help="keep the temp repos")
    ap.add_argument("--table", action="store_true", help="print the Beta promotion table")
    a = ap.parse_args(argv)
    if a.table:
        print(beta.table())
        return 0
    if a.demo:
        return demo(keep=a.keep)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

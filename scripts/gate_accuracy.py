#!/usr/bin/env python3
"""Verification-gate accuracy on existing job_series runs.

Hidden tests are ground truth the agent never sees, so every job_series row is
a labelled example of AWOS's own gate. For each gate this reports false accepts
(gate says done, hidden tests fail) and false rejects (gate says failed, hidden
tests pass) with Wilson 95% intervals, per model, per arm and overall, and
labels each error with a failure mode read from the run's run.log.

Spec: docs/specs/gate_accuracy_analysis.md. Read-only; no network, no LLM.

    python scripts/gate_accuracy.py .awos/job_series_E1_combined.json \\
        .awos/job_series_20261011T1[23]*.json --json out.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

Z95 = 1.959963984540054

GATES = ("declared", "visible", "own_tests", "strict_acceptance", "verified_tier")

FA_MODES = ("no_log", "acceptance_inactive", "arbiter_overrode", "repair_failed_restored",
            "acceptance_passed_weak", "acceptance_none_kept", "post_green_stop", "other")


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for k of n; (0, 1) when n == 0."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


# --------------------------------------------------------------------------- log

_HEADER = re.compile(r"^=+ \[(?P<arm>\S+) j(?P<job>\d+)\]", re.M)
_CHECK = re.compile(r"\[ACCEPTANCE\] check: (\d+) kept, (\d+) passed")
_GENERATED = re.compile(r"\[ACCEPTANCE\] generated (\d+) tests, kept (\d+)")
_ARB = re.compile(r"\[ACCEPTANCE\] arbitration: (\d+) failing -> (\d+) wrong_test, "
                  r"(\d+) code_incomplete")
_STOP = re.compile(r"\[agent\] stop: (\w+)")


@dataclass
class LogFacts:
    present: bool = False
    acceptance: str = "absent"   # absent|inactive|none_kept|active
    checks: list = field(default_factory=list)     # [(kept, passed), ...]
    arbitration: tuple | None = None               # (failing, wrong, incomplete)
    resolution: str = ""   # ""|arbiter_overrode|repair_passed|repair_failed_restored|stands_other
    stops: list = field(default_factory=list)
    post_green_stop: bool = False
    one_shot_solved: bool = False
    rollback: bool = False
    loop_guard: bool = False

    @property
    def last_check_ok(self) -> bool | None:
        if not self.checks:
            return None
        kept, passed = self.checks[-1]
        return passed >= kept

    @property
    def acceptance_final_ok(self) -> bool:
        """What the acceptance tests said once arbitration had dropped the
        WRONG_TEST ones: False only when kept tests were still failing and the
        green result stood anyway (failed repair, or no repair allowed)."""
        if self.resolution in ("repair_failed_restored", "stands_other"):
            return False
        if self.resolution in ("arbiter_overrode", "repair_passed"):
            return True
        ok = self.last_check_ok
        return True if ok is None else ok

    @property
    def unverified_reasons(self) -> list:
        """Why a declared success has no clean independent evidence."""
        out = []
        if not self.present:
            out.append("no_log")
        if self.acceptance in ("inactive", "absent"):
            out.append("acceptance_inactive")
        if self.acceptance == "none_kept":
            out.append("acceptance_none_kept")
        if self.resolution in ("arbiter_overrode", "repair_failed_restored", "stands_other"):
            out.append(self.resolution)
        if self.post_green_stop:
            out.append("post_green_stop")
        return out

    @property
    def strict_ok(self) -> bool:
        """Counterfactual: any failing kept test at the last check rejects,
        unless a bounded repair then made them pass."""
        if self.resolution == "repair_passed":
            return True
        ok = self.last_check_ok
        return True if ok is None else ok


def unverified(facts: "LogFacts") -> bool:
    return bool(facts.unverified_reasons)


def job_section(text: str, job: int | None) -> str:
    """The last `===== [arm jNN]` section for this job (run.log is appended
    to on resume); the whole text when there are no headers."""
    heads = list(_HEADER.finditer(text))
    if not heads:
        return text
    chosen = None
    for i, m in enumerate(heads):
        if job is None or int(m.group("job")) == job:
            chosen = i
    if chosen is None:
        chosen = len(heads) - 1
    end = heads[chosen + 1].start() if chosen + 1 < len(heads) else len(text)
    return text[heads[chosen].start():end]


def parse_log(text: str | None) -> LogFacts:
    f = LogFacts()
    if text is None:
        return f
    f.present = True
    if "[ACCEPTANCE] gate inactive" in text:
        f.acceptance = "inactive"
    for m in _GENERATED.finditer(text):
        f.acceptance = "none_kept" if int(m.group(2)) == 0 else "active"
    f.checks = [(int(a), int(b)) for a, b in _CHECK.findall(text)]
    if f.checks and f.acceptance == "absent":
        f.acceptance = "active"
    arbs = _ARB.findall(text)
    if arbs:
        f.arbitration = tuple(int(x) for x in arbs[-1])
    # Later lines win: a repair outcome follows the arbitration line.
    for line in text.splitlines():
        if "[ACCEPTANCE] no code_incomplete test left" in line:
            f.resolution = "arbiter_overrode"
        elif "[ACCEPTANCE] repair passed" in line:
            f.resolution = "repair_passed"
        elif "[ACCEPTANCE] repair failed; restored the green edit" in line:
            f.resolution = "repair_failed_restored"
        elif ("[ACCEPTANCE]" in line and "result stands" in line
              and "no code_incomplete" not in line):
            f.resolution = "stands_other"
    f.stops = _STOP.findall(text)
    f.post_green_stop = "stopped after green" in text
    f.one_shot_solved = "[ONE-SHOT] solved in" in text
    f.rollback = "performing full rollback" in text
    f.loop_guard = "[LOOP-GUARD] tripped" in text
    return f


# --------------------------------------------------------------------------- rows

@dataclass
class Row:
    source: str
    model: str
    arm: str
    task: str
    hidden_ok: bool
    gates: dict
    facts: LogFacts
    log_path: str | None


def _model(doc: dict, result: dict) -> str:
    pin = (doc.get("model_pin") or {}).get("model")
    if pin:
        return str(pin)
    models = result.get("models") or []
    return str(models[0]) if models else "unknown"


def task_id(doc: dict, result: dict) -> str:
    """One name per issue across files: single-issue series name their job
    dir (`01_smarty_...`) while combined files use the series (`real_...`)."""
    rid = str(result.get("id") or result.get("dir") or result.get("job"))
    series = str(doc.get("series") or "")
    if not rid.startswith("real_") and series.startswith("real_"):
        return series
    return rid


def _log_path(result: dict) -> Path | None:
    cmd = (result.get("inputs") or {}).get("cmd") or []
    if not cmd:
        return None
    return Path(cmd[-1]).parent / "run.log"


def usable(result: dict) -> bool:
    if result.get("invalid"):
        return False
    hp, hf = result.get("hidden_passed"), result.get("hidden_failed")
    return hp is not None and hf is not None and (hp + hf) > 0


def build_row(source: str, doc: dict, result: dict, log_text: str | None,
              log_path: str | None = None) -> Row:
    facts = parse_log(job_section(log_text, result.get("job")) if log_text is not None else None)
    visible = bool(result.get("visible_tests_ok"))
    gates = {
        "declared": result.get("orchestrator_success") is True,
        "visible": visible,
        "own_tests": visible and facts.acceptance_final_ok,
        "strict_acceptance": visible and facts.strict_ok,
        "verified_tier": (result.get("orchestrator_success") is True) and not unverified(facts),
    }
    return Row(source=source, model=_model(doc, result), arm=str(result.get("arm", "")),
               task=task_id(doc, result),
               hidden_ok=(result.get("hidden_failed") == 0 and (result.get("hidden_passed") or 0) > 0),
               gates=gates, facts=facts, log_path=log_path)


def load_rows(paths: list[Path], read_logs: bool = True) -> list[Row]:
    rows: list[Row] = []
    for path in paths:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if doc.get("dry_run"):
            continue
        for result in doc.get("results") or []:
            if not usable(result):
                continue
            lp = _log_path(result) if read_logs else None
            text = None
            if lp is not None and lp.is_file():
                text = lp.read_text(encoding="utf-8", errors="replace")
            rows.append(build_row(str(path), doc, result, text, str(lp) if lp else None))
    return rows


# --------------------------------------------------------------------------- scoring

def fa_mode(row: Row) -> str:
    f = row.facts
    if not f.present:
        return "no_log"
    if f.acceptance == "inactive":
        return "acceptance_inactive"
    if f.resolution == "arbiter_overrode":
        return "arbiter_overrode"
    if f.resolution == "repair_failed_restored":
        return "repair_failed_restored"
    if f.acceptance == "active" and f.last_check_ok:
        return "acceptance_passed_weak"
    if f.acceptance == "none_kept":
        return "acceptance_none_kept"
    if f.post_green_stop:
        return "post_green_stop"
    return "other"


def fr_mode(row: Row) -> str:
    f = row.facts
    if not row.gates["visible"]:
        return "visible_red"
    if not f.present:
        return "no_log"
    if f.acceptance in ("active",) and f.last_check_ok is False:
        return "acceptance_red"
    if f.rollback:
        return "rollback"
    if f.stops:
        return f"stop_{f.stops[-1]}"
    return "other"


def _rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": [lo, hi]}


def score(rows: list[Row], gate: str) -> dict:
    fail = [r for r in rows if not r.hidden_ok]
    ok = [r for r in rows if r.hidden_ok]
    acc = [r for r in rows if r.gates[gate]]
    rej = [r for r in rows if not r.gates[gate]]
    fa = [r for r in fail if r.gates[gate]]
    fr = [r for r in ok if not r.gates[gate]]
    return {
        "runs": len(rows), "hidden_fail": len(fail), "accepted": len(acc),
        "fa_rate": _rate(len(fa), len(fail)),         # FA / hidden-fail
        "fdr": _rate(len(fa), len(acc)),              # FA / accepted
        "fr_rate": _rate(len(fr), len(ok)),           # FR / hidden-pass
        "false_omission": _rate(len(fr), len(rej)),   # FR / rejected
        "fa_tasks": sorted({r.task for r in fa}),
        "fr_tasks": sorted({r.task for r in fr}),
        "fa_modes": dict(Counter(fa_mode(r) for r in fa)),
        "fr_modes": dict(Counter(fr_mode(r) for r in fr)),
    }


def groups(rows: list[Row]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        out[f"model={r.model}"].append(r)
        if r.arm and r.arm != "off":
            out[f"model={r.model} arm={r.arm}"].append(r)
        out["overall"].append(r)
    return dict(sorted(out.items(), key=lambda kv: (kv[0] != "overall", kv[0])))


def report(rows: list[Row]) -> dict:
    return {name: {g: score(rs, g) for g in GATES} for name, rs in groups(rows).items()}


def error_rows(rows: list[Row], gate: str = "declared") -> list[dict]:
    out = []
    for r in rows:
        if r.gates[gate] == r.hidden_ok:
            continue
        kind = "FA" if r.gates[gate] else "FR"
        f = r.facts
        out.append({
            "kind": kind, "model": r.model, "arm": r.arm, "task": r.task,
            "mode": fa_mode(r) if kind == "FA" else fr_mode(r),
            "gates": r.gates, "acceptance": f.acceptance, "checks": f.checks,
            "arbitration": f.arbitration, "resolution": f.resolution,
            "stops": f.stops, "post_green_stop": f.post_green_stop,
            "one_shot_solved": f.one_shot_solved, "rollback": f.rollback,
            "loop_guard": f.loop_guard, "log": r.log_path,
        })
    return out


def _split(rs: list[Row]) -> dict:
    return {"n": len(rs), "hidden_pass": sum(r.hidden_ok for r in rs),
            "hidden_fail": sum(not r.hidden_ok for r in rs),
            "fail_tasks": sorted(r.task for r in rs if not r.hidden_ok)}


def arbiter_audit(rows: list[Row]) -> dict:
    """Arbitration verdicts against the hidden truth: runs where the arbiter
    labelled every failing kept test WRONG_TEST and the green result stood,
    split by whether any kept test still passed after the edit."""
    over = [r for r in rows if r.facts.resolution == "arbiter_overrode"]
    zero = [r for r in over if r.facts.checks and r.facts.checks[-1][1] == 0]
    some = [r for r in over if not (r.facts.checks and r.facts.checks[-1][1] == 0)]
    return {"overrides": _split(over), "no_kept_test_passed": _split(zero),
            "some_kept_test_passed": _split(some)}


SIGNALS = {
    "post_green_stop": lambda f: f.post_green_stop,
    "one_shot_solved": lambda f: f.one_shot_solved,
    "loop_guard": lambda f: f.loop_guard,
    "acceptance_inactive": lambda f: f.acceptance == "inactive",
    "acceptance_none_kept": lambda f: f.acceptance == "none_kept",
    "arbiter_overrode": lambda f: f.resolution == "arbiter_overrode",
    "repair_passed": lambda f: f.resolution == "repair_passed",
    "repair_failed_restored": lambda f: f.resolution == "repair_failed_restored",
    "acceptance_clean_pass": lambda f: (f.acceptance == "active" and not f.resolution
                                        and bool(f.last_check_ok)),
    "stop_max_turns": lambda f: "max_turns" in f.stops,
}


def signal_audit(rows: list[Row], gate: str = "declared") -> dict:
    """Among runs the gate accepted: how often each log signal co-occurs with
    a hidden failure. A signal with a high hidden-fail share is a candidate
    reject (or escalate) rule; the hidden-pass count is its false-reject cost."""
    acc = [r for r in rows if r.gates[gate]]
    return {name: _split([r for r in acc if pred(r.facts)]) for name, pred in SIGNALS.items()}


def _pct(x: dict) -> str:
    if x["n"] == 0:
        return "n/a (0/0)"
    lo, hi = x["ci95"]
    return f"{x['k']}/{x['n']} = {100 * x['rate']:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]"


def markdown(rep: dict) -> str:
    lines = ["| group | gate | FA / hidden-fail | FA / accepted (FDR) | FR / hidden-pass |",
             "|---|---|---|---|---|"]
    for name, by_gate in rep.items():
        for g, s in by_gate.items():
            lines.append(f"| {name} | {g} | {_pct(s['fa_rate'])} | {_pct(s['fdr'])} | "
                         f"{_pct(s['fr_rate'])} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", type=Path, help="job_series result JSON files")
    ap.add_argument("--json", type=Path, help="write the full report here")
    ap.add_argument("--no-logs", action="store_true", help="skip run.log parsing")
    args = ap.parse_args(argv)
    rows = load_rows(args.files, read_logs=not args.no_logs)
    if not rows:
        print("no usable rows", file=sys.stderr)
        return 1
    rep = report(rows)
    out = {"files": [str(p) for p in args.files], "rows": len(rows), "report": rep,
           "errors_declared": error_rows(rows, "declared"),
           "errors_own_tests": error_rows(rows, "own_tests"),
           "errors_strict": error_rows(rows, "strict_acceptance"),
           "arbiter": arbiter_audit(rows),
           "signals_declared": signal_audit(rows, "declared"),
           "acceptance_states": dict(Counter(f"{r.model}:{r.facts.acceptance}" for r in rows))}
    print(markdown(rep))
    print()
    print("arbiter:", json.dumps(out["arbiter"]))
    for name, sp in out["signals_declared"].items():
        print(f"signal {name}: accepted n={sp['n']} hidden_fail={sp['hidden_fail']} "
              f"hidden_pass={sp['hidden_pass']}")
    print("acceptance states:", json.dumps(out["acceptance_states"]))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(out, indent=2, default=lambda o: asdict(o)),
                             encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

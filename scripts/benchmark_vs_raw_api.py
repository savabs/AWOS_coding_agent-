#!/usr/bin/env python3
"""
benchmark_vs_raw_api.py — AWOS harness vs single-shot raw API on fixed bug cases.

Research: docs/research/benchmark_vs_raw_api.md
Config:   tests/fixtures/benchmark_goals.json
Cases:    tests/fixtures/benchmark/<case>/

Usage:
    python3 scripts/benchmark_vs_raw_api.py
    python3 scripts/benchmark_vs_raw_api.py --case 03_simple_add
    python3 scripts/benchmark_vs_raw_api.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_PATH = ROOT / "tests" / "fixtures" / "benchmark_goals.json"
OUT_DIR = ROOT / ".awos" / "benchmarks"
PROOF_PATH = ROOT / "docs" / "product" / "pei_proof.md"


@dataclass
class ArmResult:
    arm: str
    verified: bool
    tests_pass: bool
    cost_usd: float
    wall_sec: float
    attempts: int = 1
    error: str = ""


@dataclass
class CaseBenchmark:
    case_id: str
    raw: ArmResult
    awos: ArmResult

    @property
    def awos_wins(self) -> bool:
        if self.awos.tests_pass and not self.raw.tests_pass:
            return True
        if self.awos.tests_pass == self.raw.tests_pass and self.awos.cost_usd < self.raw.cost_usd:
            return True
        return False


@dataclass
class BenchmarkReport:
    timestamp: str
    cases: list[CaseBenchmark] = field(default_factory=list)

    @property
    def raw_pass_rate(self) -> float:
        if not self.cases:
            return 0.0
        return sum(1 for c in self.cases if c.raw.tests_pass) / len(self.cases)

    @property
    def awos_pass_rate(self) -> float:
        if not self.cases:
            return 0.0
        return sum(1 for c in self.cases if c.awos.tests_pass) / len(self.cases)

    @property
    def raw_total_cost(self) -> float:
        return sum(c.raw.cost_usd for c in self.cases)

    @property
    def awos_total_cost(self) -> float:
        return sum(c.awos.cost_usd for c in self.cases)

    @property
    def raw_verified_per_dollar(self) -> float:
        n = sum(1 for c in self.cases if c.raw.tests_pass)
        return n / self.raw_total_cost if self.raw_total_cost > 0 else 0.0

    @property
    def awos_verified_per_dollar(self) -> float:
        n = sum(1 for c in self.cases if c.awos.tests_pass)
        return n / self.awos_total_cost if self.awos_total_cost > 0 else 0.0


def _load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _apply_env(env: dict[str, str]) -> None:
    for k, v in env.items():
        os.environ[k] = str(v)
    os.environ.setdefault("AWOS_LEARNING_DISABLE", "true")


def _discover_cases(cases_dir: Path, only: str | None) -> list[Path]:
    cases = sorted(
        d for d in cases_dir.iterdir()
        if d.is_dir() and (d / "buggy.py").exists() and (d / "task.json").exists()
    )
    if only:
        cases = [c for c in cases if c.name == only]
    return cases


def _copy_case(case_dir: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(case_dir, dest)


def _load_task(case_dir: Path) -> dict[str, Any]:
    from scaffold.agent.plan_actions import normalize_task

    raw = json.loads((case_dir / "task.json").read_text(encoding="utf-8"))
    return normalize_task(raw, str(case_dir))


def _run_pytest(case_tmp: Path) -> bool:
    test_file = case_tmp / "test_case.py"
    if not test_file.exists():
        return True
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-q", "--tb=no"],
        cwd=str(case_tmp),
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def _run_raw_arm(case_dir: Path, work_root: Path) -> ArmResult:
    from scaffold.agent.token_tracker import TokenTracker
    from scaffold.agent.verifier import Verifier
    from scaffold.agent.worker import Worker

    case_tmp = work_root / "raw" / case_dir.name
    _copy_case(case_dir, case_tmp)
    task = _load_task(case_tmp)
    rel = task.get("path") or task.get("file") or "buggy.py"
    file_path = case_tmp / rel

    tracker = TokenTracker(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "5")))
    worker = Worker()
    verifier = Verifier()

    cost_before = tracker.total_cost
    t0 = time.time()
    verified = False
    tests_pass = False
    err = ""
    try:
        content = file_path.read_text(encoding="utf-8")
        wr = worker.execute_task(
            task=task,
            file_content=content,
            codebase_context={"modules": "benchmark"},
            tracker=tracker,
            attempt=1,
        )
        if wr.get("success") and wr.get("_json_applied"):
            verified = True
        elif wr.get("success") and wr.get("search"):
            vr = verifier.verify_and_apply(
                {"search": wr["search"], "replace": wr["replace"], "task_spec": task},
                str(file_path),
            )
            verified = bool(vr.get("success"))
            if not verified:
                err = "; ".join(vr.get("errors") or [])[:200]
        else:
            err = wr.get("error") or wr.get("reasoning") or "worker parse failed"
        if verified:
            tests_pass = _run_pytest(case_tmp)
            if not tests_pass:
                err = "verifier ok but pytest failed"
    except Exception as exc:
        err = str(exc)[:200]

    return ArmResult(
        arm="raw",
        verified=verified,
        tests_pass=tests_pass,
        cost_usd=round(tracker.total_cost - cost_before, 6),
        wall_sec=round(time.time() - t0, 2),
        attempts=1,
        error=err,
    )


def _run_awos_arm(case_dir: Path, work_root: Path) -> ArmResult:
    from scaffold.agent.learning_policy import apply_kernel_defaults
    from scaffold.agent.orchestrator import Orchestrator
    from scaffold.agent.token_tracker import TokenTracker

    apply_kernel_defaults()
    case_tmp = work_root / "awos" / case_dir.name
    _copy_case(case_dir, case_tmp)
    task = _load_task(case_tmp)

    tracker = TokenTracker(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "5")))
    orch = Orchestrator(tracker=tracker)

    cost_before = tracker.total_cost
    t0 = time.time()
    verified = False
    tests_pass = False
    err = ""
    attempts = 1
    try:
        run = orch.execute_feature(
            goal=task.get("action", "fix bug"),
            codebase_root=str(case_tmp),
            pre_planned_tasks=[task],
        )
        attempts = max(1, int(run.get("tasks_completed", 0)) + int(run.get("tasks_failed", 0)))
        verified = bool(run.get("success"))
        if verified:
            tests_pass = _run_pytest(case_tmp)
            if not tests_pass:
                err = "orchestrator success but pytest failed"
        else:
            err = "; ".join(run.get("errors") or [])[:200] or "orchestrator failed"
    except Exception as exc:
        err = str(exc)[:200]

    return ArmResult(
        arm="awos",
        verified=verified,
        tests_pass=tests_pass,
        cost_usd=round(tracker.total_cost - cost_before, 6),
        wall_sec=round(time.time() - t0, 2),
        attempts=attempts,
        error=err,
    )


def _print_case(cb: CaseBenchmark) -> None:
    raw = "PASS" if cb.raw.tests_pass else "FAIL"
    awos = "PASS" if cb.awos.tests_pass else "FAIL"
    print(
        f"  {cb.case_id:<18}  raw {raw} ${cb.raw.cost_usd:.4f} {cb.raw.wall_sec:>5.1f}s"
        f"  |  awos {awos} ${cb.awos.cost_usd:.4f} {cb.awos.wall_sec:>5.1f}s"
        f"  ({cb.awos.attempts} tasks)"
    )


def _write_proof(report: BenchmarkReport, json_path: Path) -> None:
    lines = [
        "# PEI Proof — AWOS vs Raw API",
        "",
        f"**Generated:** {report.timestamp}",
        "",
        "Fixed bug-fix cases in isolated temp dirs. Same cheap model tier (`AWOS_CHEAP_ONLY=true`).",
        "",
        "## Summary",
        "",
        f"| Metric | Raw API | AWOS harness |",
        f"|--------|---------|--------------|",
        f"| Pass rate | {report.raw_pass_rate:.0%} | {report.awos_pass_rate:.0%} |",
        f"| Total cost | ${report.raw_total_cost:.4f} | ${report.awos_total_cost:.4f} |",
        f"| Verified fixes / $ | {report.raw_verified_per_dollar:.2f} | {report.awos_verified_per_dollar:.2f} |",
        "",
        "## Per case",
        "",
        "| Case | Raw | AWOS | Winner |",
        "|------|-----|------|--------|",
    ]
    for cb in report.cases:
        rw = "✓" if cb.raw.tests_pass else "✗"
        aw = "✓" if cb.awos.tests_pass else "✗"
        winner = "AWOS" if cb.awos_wins else ("Raw" if cb.raw.tests_pass and not cb.awos.tests_pass else "tie")
        lines.append(
            f"| {cb.case_id} | {rw} ${cb.raw.cost_usd:.4f} | {aw} ${cb.awos.cost_usd:.4f} | {winner} |"
        )
    lines.extend(
        [
            "",
            f"Raw JSON: `{json_path.relative_to(ROOT)}`",
            "",
            "See also: `docs/product/stage1_subscription_worker_guideline.md`",
            "",
        ]
    )
    PROOF_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROOF_PATH.write_text("\n".join(lines), encoding="utf-8")


def run_benchmark(*, case_filter: str | None = None, dry_run: bool = False) -> BenchmarkReport:
    cfg = _load_config()
    _apply_env(cfg.get("env", {}))
    cases_dir = ROOT / cfg.get("cases_dir", "tests/fixtures/benchmark")
    cases = _discover_cases(cases_dir, case_filter)
    if not cases:
        print(f"No benchmark cases in {cases_dir}")
        raise SystemExit(1)

    print(f"\n{'='*64}")
    print(f"  PEI Benchmark — AWOS harness vs raw API ({len(cases)} cases)")
    print(f"{'='*64}\n")

    if dry_run:
        for c in cases:
            print(f"  [dry-run] would benchmark {c.name}")
        raise SystemExit(0)

    work_root = ROOT / ".awos" / "benchmark_work"
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True, exist_ok=True)

    report = BenchmarkReport(timestamp=datetime.now(timezone.utc).isoformat())
    for i, case_dir in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case_dir.name}")
        raw = _run_raw_arm(case_dir, work_root)
        awos = _run_awos_arm(case_dir, work_root)
        cb = CaseBenchmark(case_id=case_dir.name, raw=raw, awos=awos)
        report.cases.append(cb)
        _print_case(cb)
        if raw.error and not raw.tests_pass:
            print(f"         raw err: {raw.error[:120]}")
        if awos.error and not awos.tests_pass:
            print(f"         awos err: {awos.error[:120]}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = OUT_DIR / f"benchmark_vs_raw_{ts}.json"
    payload = {
        "timestamp": report.timestamp,
        "summary": {
            "raw_pass_rate": report.raw_pass_rate,
            "awos_pass_rate": report.awos_pass_rate,
            "raw_total_cost": report.raw_total_cost,
            "awos_total_cost": report.awos_total_cost,
            "raw_verified_per_dollar": report.raw_verified_per_dollar,
            "awos_verified_per_dollar": report.awos_verified_per_dollar,
        },
        "cases": [
            {
                "case_id": c.case_id,
                "raw": asdict(c.raw),
                "awos": asdict(c.awos),
                "awos_wins": c.awos_wins,
            }
            for c in report.cases
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_proof(report, json_path)

    print(f"\n{'─'*64}")
    print(f"  Raw pass rate:  {report.raw_pass_rate:.0%}  (${report.raw_total_cost:.4f})")
    print(f"  AWOS pass rate: {report.awos_pass_rate:.0%}  (${report.awos_total_cost:.4f})")
    print(f"  Verified fixes/$: raw {report.raw_verified_per_dollar:.2f}  awos {report.awos_verified_per_dollar:.2f}")
    print(f"  JSON: {json_path}")
    print(f"  Proof: {PROOF_PATH}\n")
    return report


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Benchmark AWOS harness vs raw API")
    parser.add_argument("--case", help="Run single case directory name")
    parser.add_argument("--dry-run", action="store_true", help="List cases only")
    args = parser.parse_args()
    run_benchmark(case_filter=args.case, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

"""
benchmark_runner.py — Head-to-head evaluation: single-shot vs MCTS.

Measures three things:
  1. Pass rate:          % tasks where the patch makes tests pass
  2. Fault localization: recall@K — did FaultLocalizer find the buggy symbol?
  3. Cost efficiency:    pass_rate_gain / extra_llm_calls spent on MCTS

Run:
    python -m scaffold.agent.benchmark_runner --cases_dir tests/bug_cases
    python -m scaffold.agent.benchmark_runner --cases_dir tests/bug_cases --max_rollouts 12

Each case in cases_dir is a directory containing:
    buggy.py      — the broken Python file
    test_case.py  — pytest file that should pass after fix
    task.json     — {"action": "...", "file": "buggy.py"}
    fix.py        — (optional) ground-truth fix, for fault localization eval

Output: a terminal table + results saved to .awos/benchmark_<timestamp>.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    case_name: str
    # Single-shot
    ss_success: bool
    ss_pass_rate: float
    ss_latency_sec: float
    ss_llm_calls: int = 1
    # MCTS
    mcts_success: bool = False
    mcts_pass_rate: float = 0.0
    mcts_latency_sec: float = 0.0
    mcts_rollouts: int = 0
    mcts_llm_calls: int = 0
    # Fault localization
    fl_recall_at_1: bool = False
    fl_recall_at_3: bool = False
    fl_recall_at_5: bool = False
    fl_latency_sec: float = 0.0
    error: str = ""


@dataclass
class BenchmarkSummary:
    total_cases: int
    timestamp: str
    # Single-shot
    ss_pass_rate: float
    ss_avg_latency: float
    # MCTS
    mcts_pass_rate: float
    mcts_avg_latency: float
    mcts_avg_rollouts: float
    # Improvement
    delta_pass_rate: float          # mcts - single-shot
    avg_extra_llm_calls: float      # additional calls MCTS uses vs single-shot
    efficiency_ratio: float         # delta_pass_rate / avg_extra_llm_calls
    # Fault localization
    fl_recall_at_1: float
    fl_recall_at_3: float
    fl_recall_at_5: float
    cases: List[CaseResult] = field(default_factory=list)


# ── Main runner ───────────────────────────────────────────────────────────────

class BenchmarkRunner:
    def __init__(
        self,
        cases_dir: str,
        max_rollouts: int = 8,
        n_branches: int = 3,
        verbose: bool = True,
    ) -> None:
        self.cases_dir = Path(cases_dir)
        self.max_rollouts = max_rollouts
        self.n_branches = n_branches
        self.verbose = verbose

        # Lazy-load Worker (requires API keys)
        self._worker = None

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self) -> BenchmarkSummary:
        cases = self._discover_cases()
        if not cases:
            print(f"[BENCH] No cases found in {self.cases_dir}")
            sys.exit(1)

        print(f"\n{'='*64}")
        print(f"  AWOS Benchmark — {len(cases)} cases")
        print(f"  single-shot vs MCTS (rollouts={self.max_rollouts}, branches={self.n_branches})")
        print(f"{'='*64}\n")

        results: List[CaseResult] = []
        for i, case_dir in enumerate(cases, 1):
            print(f"[{i}/{len(cases)}] {case_dir.name} ...", end=" ", flush=True)
            r = self._run_case(case_dir)
            results.append(r)
            self._print_case_line(r)

        summary = self._build_summary(results)
        self._print_summary(summary)
        self._save(summary)
        return summary

    # ── Case discovery ────────────────────────────────────────────────────────

    def _discover_cases(self) -> List[Path]:
        if not self.cases_dir.exists():
            return []
        return sorted([
            d for d in self.cases_dir.iterdir()
            if d.is_dir() and (d / "buggy.py").exists() and (d / "task.json").exists()
        ])

    # ── Single case ───────────────────────────────────────────────────────────

    def _run_case(self, case_dir: Path) -> CaseResult:
        task_spec = json.loads((case_dir / "task.json").read_text())
        buggy_code = (case_dir / "buggy.py").read_text()
        fix_code = (case_dir / "fix.py").read_text() if (case_dir / "fix.py").exists() else None

        result = CaseResult(case_name=case_dir.name, ss_success=False, ss_pass_rate=0.0, ss_latency_sec=0.0)

        try:
            worker = self._get_worker()

            # ── 1. Single-shot ────────────────────────────────────────────────
            t0 = time.time()
            with tempfile.TemporaryDirectory() as tmp:
                ss_patch = worker.execute_task(
                    task=task_spec,
                    file_content=buggy_code,
                    codebase_context={"modules": "benchmark"},
                    use_mcts=False,
                )
                result.ss_latency_sec = round(time.time() - t0, 2)
                if ss_patch.get("success") and ss_patch.get("search"):
                    patched = buggy_code.replace(ss_patch["search"], ss_patch["replace"], 1)
                    result.ss_pass_rate = self._eval_patch(patched, case_dir, tmp)
                    result.ss_success = result.ss_pass_rate >= 1.0

            # ── 2. MCTS ───────────────────────────────────────────────────────
            if os.environ.get("AWOS_SAFE_TO_RUN_TESTS") == "1":
                t0 = time.time()
                with tempfile.TemporaryDirectory() as tmp:
                    mcts_patch = worker.execute_task(
                        task=task_spec,
                        file_content=buggy_code,
                        codebase_context={"modules": "benchmark"},
                        use_mcts=True,
                        project_root=self._setup_tmp_project(case_dir, tmp),
                    )
                    result.mcts_latency_sec = round(time.time() - t0, 2)
                    result.mcts_rollouts = int(
                        (mcts_patch.get("model_used") or "mcts(0)").split("(")[-1].rstrip(")")
                        or 0
                    )
                    result.mcts_llm_calls = result.mcts_rollouts * self.n_branches
                    if mcts_patch.get("search"):
                        patched = buggy_code.replace(mcts_patch["search"], mcts_patch["replace"], 1)
                        result.mcts_pass_rate = self._eval_patch(patched, case_dir, tmp)
                        result.mcts_success = result.mcts_pass_rate >= 1.0
            else:
                result.error = "AWOS_SAFE_TO_RUN_TESTS not set — MCTS eval skipped"

            # ── 3. Fault localization ─────────────────────────────────────────
            if fix_code:
                t0 = time.time()
                result.fl_recall_at_1, result.fl_recall_at_3, result.fl_recall_at_5 = \
                    self._eval_fault_localization(
                        task_spec, buggy_code, fix_code, case_dir
                    )
                result.fl_latency_sec = round(time.time() - t0, 2)

        except Exception as exc:
            result.error = str(exc)[:200]

        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _setup_tmp_project(self, case_dir: Path, tmp: str) -> str:
        """Copy buggy.py + test_case.py into a temp dir so TestRunner can find them."""
        tmp_p = Path(tmp)
        shutil.copy(case_dir / "buggy.py", tmp_p / "buggy.py")
        if (case_dir / "test_case.py").exists():
            shutil.copy(case_dir / "test_case.py", tmp_p / "test_case.py")
            (tmp_p / "pytest.ini").write_text("[pytest]\n")
        return tmp

    def _eval_patch(self, patched_content: str, case_dir: Path, tmp: str) -> float:
        """Write patched content + test file to tmp dir, run pytest, return pass_rate."""
        if os.environ.get("AWOS_SAFE_TO_RUN_TESTS") != "1":
            return 0.0
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from test_runner import TestRunner
            tmp_p = Path(tmp)
            (tmp_p / "buggy.py").write_text(patched_content)
            if (case_dir / "test_case.py").exists():
                shutil.copy(case_dir / "test_case.py", tmp_p / "test_case.py")
            (tmp_p / "pytest.ini").write_text("[pytest]\n")
            runner = TestRunner(project_root=tmp, timeout_sec=20)
            res = runner.run()
            return res.pass_rate
        except Exception as exc:
            return 0.0

    def _eval_fault_localization(
        self,
        task_spec: dict,
        buggy_code: str,
        fix_code: str,
        case_dir: Path,
    ) -> tuple:
        """
        Measure FaultLocalizer recall@K.
        Ground truth: functions that differ between buggy.py and fix.py.
        Returns (recall@1, recall@3, recall@5) as booleans.
        """
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from fault_localizer import FaultLocalizer, _extract_symbols

            # Ground truth: which functions changed?
            buggy_syms = {n: (s, e) for n, s, e in _extract_symbols(buggy_code)}
            fix_syms = {n: (s, e) for n, s, e in _extract_symbols(fix_code)}
            changed = set()
            for name in buggy_syms:
                if name in fix_syms:
                    buggy_lines = buggy_code.splitlines()[buggy_syms[name][0]-1:buggy_syms[name][1]]
                    fix_lines = fix_code.splitlines()[fix_syms[name][0]-1:fix_syms[name][1]]
                    if buggy_lines != fix_lines:
                        changed.add(name)

            if not changed:
                return False, False, False

            # Write buggy.py to a temp dir and run localizer on it
            with tempfile.TemporaryDirectory() as tmp:
                tmp_p = Path(tmp)
                (tmp_p / "buggy.py").write_text(buggy_code)
                localizer = FaultLocalizer(vector_memory=None)
                error_trace = task_spec.get("error_context", "")
                faults = localizer.localize(
                    task_description=task_spec.get("action", ""),
                    error_trace=error_trace,
                    project_root=tmp,
                )
                predicted_names = [f.symbol_name for f in faults]
                r1 = any(n in changed for n in predicted_names[:1])
                r3 = any(n in changed for n in predicted_names[:3])
                r5 = any(n in changed for n in predicted_names[:5])
                return r1, r3, r5
        except Exception as exc:
            return False, False, False

    def _get_worker(self):
        if self._worker is None:
            sys.path.insert(0, str(Path(__file__).parent))
            from worker import Worker
            self._worker = Worker()
        return self._worker

    # ── Reporting ─────────────────────────────────────────────────────────────

    def _build_summary(self, results: List[CaseResult]) -> BenchmarkSummary:
        n = len(results)
        ss_passes = sum(1 for r in results if r.ss_success)
        mcts_passes = sum(1 for r in results if r.mcts_success)
        mcts_run = [r for r in results if not r.error or "MCTS eval" not in r.error]

        ss_pr = ss_passes / n if n else 0.0
        mcts_pr = mcts_passes / n if n else 0.0
        delta = mcts_pr - ss_pr
        avg_extra = sum(r.mcts_llm_calls for r in results) / n if n else 0.0
        efficiency = delta / avg_extra if avg_extra > 0 else 0.0

        fl_cases = [r for r in results if r.fl_recall_at_1 is not False or r.fl_recall_at_3 is not False]
        fl_n = len(fl_cases)

        return BenchmarkSummary(
            total_cases=n,
            timestamp=datetime.now(timezone.utc).isoformat(),
            ss_pass_rate=round(ss_pr, 3),
            ss_avg_latency=round(sum(r.ss_latency_sec for r in results) / n, 2) if n else 0.0,
            mcts_pass_rate=round(mcts_pr, 3),
            mcts_avg_latency=round(sum(r.mcts_latency_sec for r in results) / n, 2) if n else 0.0,
            mcts_avg_rollouts=round(sum(r.mcts_rollouts for r in results) / n, 1) if n else 0.0,
            delta_pass_rate=round(delta, 3),
            avg_extra_llm_calls=round(avg_extra, 1),
            efficiency_ratio=round(efficiency, 4),
            fl_recall_at_1=round(sum(1 for r in fl_cases if r.fl_recall_at_1) / fl_n, 3) if fl_n else 0.0,
            fl_recall_at_3=round(sum(1 for r in fl_cases if r.fl_recall_at_3) / fl_n, 3) if fl_n else 0.0,
            fl_recall_at_5=round(sum(1 for r in fl_cases if r.fl_recall_at_5) / fl_n, 3) if fl_n else 0.0,
            cases=results,
        )

    def _print_case_line(self, r: CaseResult) -> None:
        ss = "✓" if r.ss_success else "✗"
        mcts = "✓" if r.mcts_success else ("?" if not r.mcts_rollouts else "✗")
        fl = f"R@1={'✓' if r.fl_recall_at_1 else '✗'} R@3={'✓' if r.fl_recall_at_3 else '✗'}"
        err = f"  ERR:{r.error[:60]}" if r.error else ""
        print(
            f"  SS={ss}(pr={r.ss_pass_rate:.2f} {r.ss_latency_sec:.1f}s)  "
            f"MCTS={mcts}(pr={r.mcts_pass_rate:.2f} {r.mcts_rollouts}rolls)  "
            f"FL:{fl}{err}"
        )

    def _print_summary(self, s: BenchmarkSummary) -> None:
        print(f"\n{'='*64}")
        print(f"  RESULTS  ({s.total_cases} cases)")
        print(f"{'='*64}")
        print(f"  {'Metric':<35} {'Single-Shot':>12} {'MCTS':>12}")
        print(f"  {'-'*59}")
        print(f"  {'Pass rate':<35} {s.ss_pass_rate:>11.1%} {s.mcts_pass_rate:>11.1%}")
        print(f"  {'Avg latency (sec)':<35} {s.ss_avg_latency:>12.1f} {s.mcts_avg_latency:>12.1f}")
        print(f"  {'Avg LLM calls':<35} {'1':>12} {s.avg_extra_llm_calls:>12.1f}")
        print(f"  {'-'*59}")
        print(f"  {'Pass rate delta (MCTS - SS)':<35} {s.delta_pass_rate:>+12.1%}")
        print(f"  {'Efficiency (delta / extra calls)':<35} {s.efficiency_ratio:>12.4f}")
        print(f"\n  Fault Localization Recall:")
        print(f"  {'  R@1 (bug in top-1 prediction)':<35} {s.fl_recall_at_1:>12.1%}")
        print(f"  {'  R@3 (bug in top-3 predictions)':<35} {s.fl_recall_at_3:>12.1%}")
        print(f"  {'  R@5 (bug in top-5 predictions)':<35} {s.fl_recall_at_5:>12.1%}")
        print(f"{'='*64}\n")

        # Interpretation
        if s.delta_pass_rate > 0.10:
            print("  VERDICT: MCTS is clearly better — +{:.0%} more tasks solved.".format(s.delta_pass_rate))
        elif s.delta_pass_rate > 0:
            print("  VERDICT: MCTS helps marginally. Check efficiency_ratio to see if cost is worth it.")
        elif s.delta_pass_rate == 0 and s.mcts_pass_rate == 0:
            print("  VERDICT: AWOS_SAFE_TO_RUN_TESTS not set — MCTS eval was skipped.")
            print("           Set AWOS_SAFE_TO_RUN_TESTS=1 and re-run for real comparison.")
        else:
            print("  VERDICT: MCTS did not improve pass rate on this case set.")
            print("           Consider increasing --max_rollouts or check case difficulty.")

    def _save(self, s: BenchmarkSummary) -> None:
        out_dir = Path(".awos")
        out_dir.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"benchmark_{ts}.json"
        data = asdict(s)
        path.write_text(json.dumps(data, indent=2))
        print(f"  Results saved → {path}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AWOS benchmark: single-shot vs MCTS")
    parser.add_argument(
        "--cases_dir",
        default="tests/bug_cases",
        help="Directory containing bug case subdirectories (default: tests/bug_cases)",
    )
    parser.add_argument(
        "--max_rollouts", type=int, default=8,
        help="MCTS rollout budget per case (default: 8)",
    )
    parser.add_argument(
        "--n_branches", type=int, default=3,
        help="LLM candidates per MCTS expansion (default: 3)",
    )
    args = parser.parse_args()

    runner = BenchmarkRunner(
        cases_dir=args.cases_dir,
        max_rollouts=args.max_rollouts,
        n_branches=args.n_branches,
    )
    runner.run()


if __name__ == "__main__":
    main()

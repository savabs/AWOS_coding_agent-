#!/usr/bin/env python3
"""
progress_snapshot.py — Honest progress measurement for AI-built projects.

Reads real data (test results, demo freshness, git metadata) and produces
an objective JSON + terminal report. No task-file [x] marks. No self-reporting.

Usage:
    python scripts/progress_snapshot.py
    python scripts/progress_snapshot.py --json-only   # suppress terminal output
    python scripts/progress_snapshot.py --skip-tests   # no pytest run (fast)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── paths ─────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DIR = PROJECT_ROOT / "tasks" / "active"
COMPLETE_DIR = PROJECT_ROOT / "tasks" / "complete"
ZOMBIE_DIR = PROJECT_ROOT / "tasks" / "zombie"
DEMOS_DIR = PROJECT_ROOT / "scripts"
MEMORY_DIR = PROJECT_ROOT / "docs" / "memory"
TESTS_DIR = PROJECT_ROOT / "tests"
AWOS_DIR = PROJECT_ROOT / ".awos"


# ── data structures ───────────────────────────────────────────────────────────


@dataclass
class TestSuiteSnapshot:
    """Results from running the test suite."""
    total_collected: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    deselected: int = 0
    collection_errors: int = 0
    duration_sec: float = 0.0
    raw_output: str = ""


@dataclass
class TaskCoverageEntry:
    """Test coverage for one task file."""
    task_path: str
    test_file_exists: bool
    test_file_path: str = ""
    tests_passed: int = 0
    tests_failed: int = 0
    tests_total: int = 0
    test_ran: bool = False


@dataclass
class DemoSnapshot:
    """State of one demo script."""
    script_path: str
    exists: bool
    last_modified_days: float = 0.0
    stale: bool = False


@dataclass
class CheckpointSnapshot:
    """Quality of latest checkpoint."""
    path: str = ""
    exists: bool = False
    todo_count: int = 0
    age_days: float = 0.0


@dataclass
class TaskFreshnessEntry:
    """How long since a task file was last modified."""
    task_path: str
    age_days: float
    stale: bool
    done_steps: int = 0
    total_steps: int = 0


@dataclass
class RuntimeMetrics:
    """Metrics from .awos/ runtime data."""
    reward_episodes: int = 0
    spans: int = 0
    error_patterns: int = 0
    tools_synthesized: int = 0


@dataclass
class ProgressSnapshot:
    """Full honest progress snapshot."""
    captured_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Test suite
    test_suite: TestSuiteSnapshot = field(default_factory=TestSuiteSnapshot)
    test_suite_healthy: bool = False

    # Task coverage (active + complete)
    active_tasks: list[TaskCoverageEntry] = field(default_factory=list)
    complete_tasks: list[TaskCoverageEntry] = field(default_factory=list)

    # Task freshness
    active_task_freshness: list[TaskFreshnessEntry] = field(default_factory=list)

    # Zombie tasks
    zombie_task_count: int = 0

    # Demos
    demos: list[DemoSnapshot] = field(default_factory=list)
    stale_demo_count: int = 0

    # Checkpoint quality
    latest_checkpoint: CheckpointSnapshot = field(default_factory=CheckpointSnapshot)

    # Runtime
    runtime: RuntimeMetrics = field(default_factory=RuntimeMetrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at,
            "test_suite": asdict(self.test_suite),
            "test_suite_healthy": self.test_suite_healthy,
            "active_tasks": [asdict(t) for t in self.active_tasks],
            "complete_tasks": [asdict(t) for t in self.complete_tasks],
            "active_task_freshness": [asdict(f) for f in self.active_task_freshness],
            "zombie_task_count": self.zombie_task_count,
            "demos": [asdict(d) for d in self.demos],
            "stale_demo_count": self.stale_demo_count,
            "latest_checkpoint": asdict(self.latest_checkpoint),
            "runtime": asdict(self.runtime),
        }


# ── scanners ──────────────────────────────────────────────────────────────────


def _run_pytest(args: list[str]) -> subprocess.CompletedProcess:
    """Run pytest and return the result."""
    cmd = [sys.executable, "-m", "pytest"] + args
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def scan_test_suite() -> TestSuiteSnapshot:
    """Run the test suite and parse results."""
    result = TestSuiteSnapshot()

    # First: collect-only to count collection errors
    collect = _run_pytest([
        "tests/",
        "--collect-only",
        "-q",
        "-m", "not integration and not slow",
        "--override-ini", "addopts=--ignore=tests/test_vector_memory.py --ignore=tests/fixtures",
    ])

    # Count collection errors from stderr
    collection_errors = 0
    for line in collect.stderr.split("\n"):
        if "ERROR" in line:
            collection_errors += 1
    result.collection_errors = collection_errors

    # Parse collection output for total count
    for line in collect.stdout.split("\n"):
        m = re.search(r"(\d+)\s*errors?\s*$", line)
        if m:
            result.errors = int(m.group(1))
        m = re.search(r"(\d+)\s*deselected", line)
        if m:
            result.deselected = int(m.group(1))
        m = re.search(r"/(\d+)\s*tests\s*collected", line)
        if m:
            result.total_collected = int(m.group(1))
        m = re.search(r"^(\d+)\s*tests\s*collected", line)
        if m:
            result.total_collected = int(m.group(1))

    # Second: run the full suite with --tb=line for pass/fail counts.
    # Use --ignore to skip broken fixture tests, --continue-on-collection-errors
    # so the suite actually runs the non-broken tests.
    run = _run_pytest([
        "tests/",
        "-q",
        "--tb=line",
        "-m", "not integration and not slow",
        "--ignore=tests/test_vector_memory.py",
        "--continue-on-collection-errors",
    ])
    result.raw_output = (run.stdout + run.stderr)[:2000]

    # Parse the summary line: "N passed, M failed, ..."
    # Real format (with --continue-on-collection-errors):
    #   "55 failed, 1000 passed, 23 deselected, 20 warnings, 31 errors in 80.22s"
    for line in run.stdout.split("\n"):
        # Match the final summary line only (contains "passed" or "failed")
        if not re.search(r"\d+\s+(?:passed|failed|errors)", line):
            continue
        passed_m = re.search(r"(\d+)\s*passed", line)
        failed_m = re.search(r"(\d+)\s*failed", line)
        # errors after passed/failed indicates collection errors that
        # didn't stop execution
        errors_m = re.search(r",\s*(\d+)\s*errors?\s", line)
        duration_m = re.search(r"in\s+([\d.]+)s", line)

        if passed_m:
            result.passed = int(passed_m.group(1))
        if failed_m:
            result.failed = int(failed_m.group(1))
        if errors_m:
            result.errors = int(errors_m.group(1))
        if duration_m:
            result.duration_sec = float(duration_m.group(1))

    return result


def scan_task_test_coverage(
    task_dir: Path, source_label: str
) -> list[TaskCoverageEntry]:
    """For each task file, check if a corresponding test exists and passes."""
    entries: list[TaskCoverageEntry] = []
    if not task_dir.exists():
        return entries

    for f in sorted(task_dir.iterdir()):
        if f.suffix not in (".md", ".html") or f.name.startswith("TASK_TEMPLATE"):
            continue
        name = f.stem
        # Try matching test file by exact name, then by common variations
        test_paths = [
            TESTS_DIR / f"test_{name}.py",
        ]
        # Also check lowercase match
        test_paths.append(TESTS_DIR / f"test_{name.lower()}.py")

        entry = TaskCoverageEntry(
            task_path=str(f.relative_to(PROJECT_ROOT)),
            test_file_exists=False,
        )

        for tp in test_paths:
            if tp.exists():
                entry.test_file_exists = True
                entry.test_file_path = str(tp.relative_to(PROJECT_ROOT))

                # Run the specific test file
                test_run = _run_pytest([
                    str(tp),
                    "-q",
                    "--tb=line",
                    "--override-ini", "addopts=",
                ])
                entry.test_ran = True
                for line in test_run.stdout.split("\n"):
                    passed_m = re.search(r"(\d+)\s*passed", line)
                    failed_m = re.search(r"(\d+)\s*failed", line)
                    if passed_m:
                        entry.tests_passed = int(passed_m.group(1))
                    if failed_m:
                        entry.tests_failed = int(failed_m.group(1))
                entry.tests_total = entry.tests_passed + entry.tests_failed
                break

        entries.append(entry)

    return entries


def scan_demo_scripts() -> tuple[list[DemoSnapshot], int]:
    """Scan all demo scripts for staleness."""
    demos: list[DemoSnapshot] = []
    now = datetime.now()
    stale_count = 0

    if not DEMOS_DIR.exists():
        return demos, stale_count

    for f in sorted(DEMOS_DIR.glob("demo_*.sh")):
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        age_days = (now - mtime).total_seconds() / 86400
        stale = age_days > 7
        if stale:
            stale_count += 1

        demos.append(DemoSnapshot(
            script_path=str(f.relative_to(PROJECT_ROOT)),
            exists=True,
            last_modified_days=round(age_days, 1),
            stale=stale,
        ))

    return demos, stale_count


def scan_checkpoint_quality() -> CheckpointSnapshot:
    """Scan the latest checkpoint for quality."""
    result = CheckpointSnapshot()

    if not MEMORY_DIR.exists():
        return result

    checkpoints = sorted(
        [f for f in MEMORY_DIR.glob("checkpoint_*.md") if "TEMPLATE" not in f.name],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not checkpoints:
        return result

    cp = checkpoints[0]
    result.path = str(cp.relative_to(PROJECT_ROOT))
    result.exists = True

    now = datetime.now()
    mtime = datetime.fromtimestamp(cp.stat().st_mtime)
    result.age_days = round((now - mtime).total_seconds() / 86400, 1)

    content = cp.read_text(encoding="utf-8")
    result.todo_count = content.count("(TODO)") + content.count("TODO:")

    return result


def scan_task_freshness() -> list[TaskFreshnessEntry]:
    """Check how fresh each active task is."""
    entries: list[TaskFreshnessEntry] = []
    now = datetime.now()

    if not ACTIVE_DIR.exists():
        return entries

    for f in sorted(ACTIVE_DIR.iterdir()):
        if f.suffix not in (".md", ".html") or f.name.startswith("TASK_TEMPLATE"):
            continue

        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        age_days = (now - mtime).total_seconds() / 86400

        # Count steps
        content = f.read_text(encoding="utf-8")
        done = len(re.findall(r"^\s*-\s*\[x\]", content, re.MULTILINE))
        pending = len(re.findall(r"^\s*-\s*\[ \]", content, re.MULTILINE))
        total = done + pending

        entries.append(TaskFreshnessEntry(
            task_path=str(f.relative_to(PROJECT_ROOT)),
            age_days=round(age_days, 1),
            stale=age_days > 14,
            done_steps=done,
            total_steps=total,
        ))

    return entries


def scan_zombie_tasks() -> int:
    """Count zombie task files."""
    if not ZOMBIE_DIR.exists():
        return 0
    return len([f for f in ZOMBIE_DIR.iterdir() if f.suffix in (".md", ".html") and "TEMPLATE" not in f.name])


def scan_runtime_metrics() -> RuntimeMetrics:
    """Read .awos/ runtime data."""
    metrics = RuntimeMetrics()

    reward_path = AWOS_DIR / "reward_store.jsonl"
    if reward_path.exists():
        try:
            metrics.reward_episodes = sum(1 for _ in reward_path.open())
        except Exception:
            pass

    spans_path = AWOS_DIR / "spans.jsonl"
    if spans_path.exists():
        try:
            metrics.spans = sum(1 for _ in spans_path.open())
        except Exception:
            pass

    error_path = AWOS_DIR / "error_patterns.jsonl"
    if error_path.exists():
        try:
            metrics.error_patterns = sum(1 for _ in error_path.open())
        except Exception:
            pass

    tools_index = AWOS_DIR / "tools" / "index.json"
    if tools_index.exists():
        try:
            data = json.loads(tools_index.read_text())
            metrics.tools_synthesized = len(data) if isinstance(data, list) else 0
        except Exception:
            pass

    return metrics


# ── terminal report ───────────────────────────────────────────────────────────


def _color(text: str, code: str) -> str:
    """Wrap text in ANSI color code."""
    colors = {
        "red": "31",
        "green": "32",
        "yellow": "33",
        "blue": "34",
        "bold": "1",
    }
    c = colors.get(code, "0")
    return f"\033[{c}m{text}\033[0m"


def print_terminal_report(snapshot: ProgressSnapshot) -> None:
    """Print a human-readable report to stdout."""
    s = snapshot
    sep = "─" * 60
    healthy = s.test_suite_healthy

    print()
    print(_color("  PROGRESS SNAPSHOT", "bold"))
    print(f"  {s.captured_at}")
    print(f"  {sep}")

    # Test suite
    ts = s.test_suite
    if ts.total_collected > 0:
        status = _color("HEALTHY", "green") if healthy else _color("UNHEALTHY", "red")
        coll = ""
        if ts.collection_errors > 0:
            coll = _color(f" ({ts.collection_errors} collection errors)", "red")
        print(f"\n  {_color('Tests', 'bold')}: {status}{coll}")
        print(f"    {ts.passed} passed, {_color(str(ts.failed), 'red')} failed, {ts.total_collected} total")
        print(f"    Duration: {ts.duration_sec:.1f}s")
    else:
        print(f"\n  {_color('Tests', 'bold')}: {_color('COULD NOT RUN', 'red')}")
        print(f"    {ts.raw_output[:300]}")

    # Active task freshness
    print(f"\n  {_color('Active Tasks', 'bold')}: {len(s.active_task_freshness)}")
    for t in s.active_task_freshness:
        age_str = _color(f"{t.age_days:.0f}d stale", "red") if t.stale else f"{t.age_days:.0f}d"
        pct = f"{t.done_steps}/{t.total_steps} steps" if t.total_steps > 0 else "0 steps"
        print(f"    {t.task_path:<45} {pct:<12} {age_str}")

    # Test coverage
    has_tests = [t for t in s.active_tasks if t.test_file_exists]
    no_tests = [t for t in s.active_tasks if not t.test_file_exists]
    print(f"\n  {_color('Test Coverage', 'bold')}: {len(has_tests)} with tests, {_color(str(len(no_tests)), 'yellow')} without")
    for t in no_tests[:5]:
        print(f"    {_color('✗', 'red')} {t.task_path}")
    if len(no_tests) > 5:
        print(f"    ... and {len(no_tests) - 5} more")

    # Complete tasks with failing tests
    failed_complete = [t for t in s.complete_tasks if t.test_file_exists and t.tests_failed > 0]
    if failed_complete:
        print(f"\n  {_color('⚠ Completed tasks with failing tests', 'yellow')}")
        for t in failed_complete:
            print(f"    {t.task_path}: {t.tests_failed} failed tests")

    # Demos
    print(f"\n  {_color('Demos', 'bold')}: {len(s.demos)} scripts, {_color(str(s.stale_demo_count), 'yellow')} stale")
    for d in s.demos:
        if d.stale:
            print(f"    {_color('✗', 'red')} {d.script_path:<50} {d.last_modified_days:.0f}d ago")
    if s.stale_demo_count == 0 and len(s.demos) > 0:
        print(f"    {_color('All demos fresh ✓', 'green')}")

    # Checkpoint quality
    cp = s.latest_checkpoint
    if cp.exists:
        cp_status = _color(f"{cp.todo_count} TODO(s)", "red") if cp.todo_count > 0 else _color("clean", "green")
        print(f"\n  {_color('Checkpoint', 'bold')}: {cp_status}")
        print(f"    {cp.path} ({cp.age_days:.0f}d old)")
    else:
        print(f"\n  {_color('Checkpoint', 'bold')}: {_color('NONE', 'red')}")

    # Runtime
    rt = s.runtime
    print(f"\n  {_color('Runtime', 'bold')}")
    print(f"    {rt.reward_episodes} reward episodes")
    print(f"    {rt.spans} execution spans")
    print(f"    {rt.error_patterns} error patterns")
    print(f"    {rt.tools_synthesized} tools synthesized")

    # Zombie tasks
    print(f"\n  {_color('Zombie tasks', 'bold')}: {s.zombie_task_count} archived")

    print(f"\n  {sep}")
    verdict = _color("HEALTHY", "green") if healthy else _color("GAPS DETECTED", "yellow")
    print(f"  Verdict: {verdict}")
    if not healthy:
        if s.test_suite.failed > 0:
            print(f"    - {s.test_suite.failed} test(s) are failing")
        if s.test_suite.collection_errors > 0:
            print(f"    - {s.test_suite.collection_errors} test collection error(s)")
        stale_active = [t for t in s.active_task_freshness if t.stale]
        if stale_active:
            print(f"    - {len(stale_active)} active task(s) stale (>14d untouched)")
        if s.stale_demo_count > 0:
            print(f"    - {s.stale_demo_count} demo(s) stale (>7d since modified)")
        if cp.exists and cp.todo_count > 0:
            print(f"    - Latest checkpoint has {cp.todo_count} TODO(s)")
        no_test_count = len([t for t in s.active_tasks if not t.test_file_exists])
        if no_test_count > 0:
            print(f"    - {no_test_count} active task(s) have no test file")
        failed_complete_count = len(failed_complete)
        if failed_complete_count > 0:
            print(f"    - {failed_complete_count} 'completed' task(s) have failing tests")
    print()


# ── main ──────────────────────────────────────────────────────────────────────


def build_snapshot(skip_tests: bool = False) -> ProgressSnapshot:
    """Build a complete progress snapshot."""
    snapshot = ProgressSnapshot()

    # 1. Test suite
    if not skip_tests:
        snapshot.test_suite = scan_test_suite()
        snapshot.test_suite_healthy = (
            snapshot.test_suite.failed == 0
            and snapshot.test_suite.collection_errors == 0
            and snapshot.test_suite.total_collected > 0
        )
    else:
        snapshot.test_suite = TestSuiteSnapshot(total_collected=0)

    # 2. Task test coverage
    snapshot.active_tasks = scan_task_test_coverage(ACTIVE_DIR, "active")
    snapshot.complete_tasks = scan_task_test_coverage(COMPLETE_DIR, "complete")

    # 3. Task freshness
    snapshot.active_task_freshness = scan_task_freshness()

    # 4. Zombie count
    snapshot.zombie_task_count = scan_zombie_tasks()

    # 5. Demos
    snapshot.demos, snapshot.stale_demo_count = scan_demo_scripts()

    # 6. Checkpoint
    snapshot.latest_checkpoint = scan_checkpoint_quality()

    # 7. Runtime
    snapshot.runtime = scan_runtime_metrics()

    return snapshot


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Honest project progress snapshot.")
    parser.add_argument("--json-only", action="store_true", help="Suppress terminal output")
    parser.add_argument("--skip-tests", action="store_true", help="Skip running pytest (fast)")
    args = parser.parse_args()

    snapshot = build_snapshot(skip_tests=args.skip_tests)

    # Write JSON
    output_path = AWOS_DIR / "progress_snapshot.json"
    AWOS_DIR.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(snapshot.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )

    if not args.json_only:
        print_terminal_report(snapshot)

    print(f"JSON written to {output_path}")


if __name__ == "__main__":
    main()

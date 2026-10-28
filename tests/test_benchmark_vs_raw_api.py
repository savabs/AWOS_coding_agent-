"""Benchmark script dry-run smoke test."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def test_benchmark_dry_run_lists_cases():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "benchmark_vs_raw_api.py"), "--dry-run"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "01_off_by_one" in proc.stdout
    assert "03_simple_add" in proc.stdout

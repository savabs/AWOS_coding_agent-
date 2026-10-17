#!/usr/bin/env python3
"""Smoke test: compare_baseline runs against frozen v1."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_compare_baseline_runs():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "compare_baseline.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "BASELINE COMPARISON" in proc.stdout
    assert "AWOS pass rate" in proc.stdout

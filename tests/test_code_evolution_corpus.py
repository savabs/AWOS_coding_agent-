"""Corpus invariants for Code Evolution Lab (no API)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "wedge_v1" / "code_evolution"


def _pytest_summary(corpus: Path) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
        cwd=corpus,
        capture_output=True,
        text=True,
    )
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    return lines[-1] if lines else ""


def test_commit_0_all_green():
    summary = _pytest_summary(FIX / "commit_0_baseline")
    assert "12 passed" in summary, summary


def test_commit_1_twelve_green_three_red():
    summary = _pytest_summary(FIX / "commit_1_tiers")
    assert "12 passed" in summary, summary
    assert "3 failed" in summary, summary


def test_commit_2_fifteen_green_two_red():
    summary = _pytest_summary(FIX / "commit_2_refactor")
    assert "15 passed" in summary, summary
    assert "2 failed" in summary, summary


def test_commit_3_sixteen_green_four_red():
    summary = _pytest_summary(FIX / "commit_3_bulk")
    assert "16 passed" in summary, summary
    assert "4 failed" in summary, summary


def test_commit_4_seventeen_green_six_red():
    summary = _pytest_summary(FIX / "commit_4_integration")
    assert "17 passed" in summary, summary
    assert "6 failed" in summary, summary

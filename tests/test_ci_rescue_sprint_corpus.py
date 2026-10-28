"""CI Rescue Sprint corpus — structural tests."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "wedge_v1" / "ci_rescue_sprint"
ASSERT = ROOT / "scripts" / "wedge_v1_assert.py"


def _pytest_count(tmp: Path) -> tuple[int, int, int]:
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/",
            "-q",
            "--tb=no",
            "--rootdir",
            str(tmp),
        ],
        cwd=tmp,
        capture_output=True,
        text=True,
        env=env,
    )
    out = r.stdout + r.stderr
    failed_m = re.search(r"(\d+) failed", out)
    passed_m = re.search(r"(\d+) passed", out)
    failed = int(failed_m.group(1)) if failed_m else 0
    passed = int(passed_m.group(1)) if passed_m else 0
    return r.returncode, failed, passed


def test_corpus_has_cursor_class_scale():
    assert FIXTURE.is_dir()
    modules = list(FIXTURE.glob("*.py"))
    modules = [m for m in modules if m.name not in ("conftest.py",)]
    assert len(modules) >= 5


@pytest.fixture
def buggy_copy(tmp_path):
    dest = tmp_path / "corpus"
    shutil.copytree(FIXTURE, dest)
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "buggy"], cwd=dest, check=True)
    return dest


def test_buggy_baseline_at_least_15_reds(buggy_copy):
    code, failed, passed = _pytest_count(buggy_copy)
    assert code != 0
    assert failed >= 15
    assert passed >= 5


def test_golden_reference_all_green(buggy_copy):
    for mod in ("auth", "billing", "inventory", "shipping", "promo"):
        shutil.copy(buggy_copy / "golden" / f"{mod}.py", buggy_copy / f"{mod}.py")
    subprocess.run(["git", "add", "-A"], cwd=buggy_copy, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "golden"], cwd=buggy_copy, check=True)
    code, failed, passed = _pytest_count(buggy_copy)
    assert code == 0
    assert failed == 0
    assert passed == 35


def test_wedge_assert_semantic_pass_on_golden(buggy_copy):
    for mod in ("auth", "billing", "inventory", "shipping", "promo"):
        shutil.copy(buggy_copy / "golden" / f"{mod}.py", buggy_copy / f"{mod}.py")
    subprocess.run(["git", "add", "-A"], cwd=buggy_copy, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "golden"], cwd=buggy_copy, check=True)
    r = subprocess.run(
        [
            sys.executable,
            str(ASSERT),
            "--root",
            str(buggy_copy),
            "--assertions",
            str(FIXTURE / "wedge_goal.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WEDGE_ASSERT: SEMANTIC_PASS" in r.stdout

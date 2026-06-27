"""Unit tests for wedge_v1_assert.py."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "wedge_v1" / "payment_utils"
CI_FIXTURE = ROOT / "tests" / "fixtures" / "wedge_v1" / "ci_rescue_sprint"
ASSERT_SCRIPT = ROOT / "scripts" / "wedge_v1_assert.py"


def _run_assert(root: Path, assertions: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ASSERT_SCRIPT), "--root", str(root), "--assertions", str(assertions)],
        capture_output=True,
        text=True,
    )


def test_assert_fails_on_buggy_fixture(tmp_path):
    subprocess.run(["cp", "-a", f"{FIXTURE}/.", f"{tmp_path}/"], check=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "buggy"], cwd=tmp_path, check=True)

    result = _run_assert(tmp_path, FIXTURE / "wedge_goal.json")
    assert result.returncode != 0
    assert "SEMANTIC_FAIL" in result.stdout


def test_assert_passes_on_golden_fixture(tmp_path):
    subprocess.run(["cp", "-a", f"{FIXTURE}/.", f"{tmp_path}/"], check=True)
    payment = tmp_path / "payment.py"
    text = payment.read_text()
    text = text.replace(
        "return amount - (amount * percent / 100 / 100)",
        "return amount - (amount * percent / 100)",
    )
    text = text.replace(
        """    # Bug: truncates instead of half-up quantize
    return int(taxed * 100) / 100""",
        """    return float(Decimal(str(taxed)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))""",
    )
    text = text.replace(
        """    if opened:
        return True
    return days_since_purchase <= 30""",
        """    if opened:
        return False
    return days_since_purchase <= 30""",
    )
    payment.write_text(text)

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixed"], cwd=tmp_path, check=True)

    result = _run_assert(tmp_path, FIXTURE / "wedge_goal.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WEDGE_ASSERT: SEMANTIC_PASS" in result.stdout
    assert "must_pass OK (3/3)" in result.stdout


def test_forbidden_paths_ignores_pycache(tmp_path):
    subprocess.run(
        ["rsync", "-a", "--exclude", ".awos", "--exclude", ".git", f"{CI_FIXTURE}/", f"{tmp_path}/"],
        check=True,
    )
    for mod in ("auth", "billing", "inventory", "shipping", "promo"):
        subprocess.run(["cp", f"{tmp_path}/golden/{mod}.py", f"{tmp_path}/{mod}.py"], check=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "golden"], cwd=tmp_path, check=True)
    cache = tmp_path / "tests" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "test_auth.cpython-310.pyc").write_bytes(b"fake")
    subprocess.run(["git", "add", "-f", str(cache)], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "pycache artifact"], cwd=tmp_path, check=True)
    result = _run_assert(tmp_path, CI_FIXTURE / "wedge_goal.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "forbidden_paths clean" in result.stdout

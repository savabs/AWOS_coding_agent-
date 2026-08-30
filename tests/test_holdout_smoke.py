from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("scripts/run_holdout_smoke.py")


def test_three_task_smoke_creates_and_verifies_corpus(tmp_path):
    root = tmp_path / "smoke"
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "SMOKE_ONLY" in result.stdout
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["split"] == "holdout"
    assert len(manifest["tasks"]) == 3
    comparison = json.loads((root / "comparisons" / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["regressed"] is False
    assert len(comparison["task_comparisons"]) == 3
    baseline = json.loads((root / "reports" / "baseline" / "baseline.json").read_text(encoding="utf-8"))
    candidate = json.loads((root / "reports" / "candidate" / "candidate.json").read_text(encoding="utf-8"))
    assert all(report["pass_at_1"] == 0.0 for report in baseline["reports"])
    assert all(report["pass_at_1"] == 1.0 for report in candidate["reports"])


def test_three_task_smoke_refuses_overwrite(tmp_path):
    root = tmp_path / "smoke"
    first = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True)
    assert first.returncode == 0
    second = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True)
    assert second.returncode == 1
    assert "refusing overwrite" in second.stderr

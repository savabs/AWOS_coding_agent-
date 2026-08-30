from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("scripts/init_private_holdout.py")


def test_initializer_creates_local_structure_and_template(tmp_path):
    root = tmp_path / "private"
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for relative in ("tasks", "repositories", "reports/baseline", "reports/candidate", "comparisons"):
        assert (root / relative).is_dir()
    template = json.loads((root / "manifest.template.json").read_text(encoding="utf-8"))
    assert template["split"] == "holdout"
    assert template["tasks"] == []
    assert "tests/fixtures/evaluation_v1" not in json.dumps(template)
    assert (root.stat().st_mode & 0o777) == 0o700


def test_initializer_preserves_existing_files(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    sentinel = root / "sentinel.txt"
    sentinel.write_text("do-not-overwrite", encoding="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert sentinel.read_text(encoding="utf-8") == "do-not-overwrite"
    second = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True)
    assert second.returncode == 0
    assert "CREATED_FILES 0" in second.stdout


def test_initializer_rejects_repository_fixture_path(tmp_path):
    unsafe = Path.cwd() / "tests" / "fixtures" / "evaluation_v1" / "private"
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(unsafe)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "unsafe" in result.stderr

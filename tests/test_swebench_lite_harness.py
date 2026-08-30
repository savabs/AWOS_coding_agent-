"""Tests for SWE-bench Lite subset harness (no Docker / no API)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scaffold.agent.swebench_lite import (
    SweBenchInstance,
    build_task_action,
    extract_model_patch,
    load_default_subset_ids,
    parse_test_patch_paths,
    write_predictions_jsonl,
)


def test_default_subset_has_ten_ids():
    ids = load_default_subset_ids()
    assert len(ids) == 10
    assert "sympy__sympy-20590" in ids


def test_test_files_from_patch():
    patch = """diff --git a/tests/test_foo.py b/tests/test_foo.py
--- a/tests/test_foo.py
+++ b/tests/test_foo.py
"""
    assert parse_test_patch_paths(patch) == ["tests/test_foo.py"]


def test_build_task_action_includes_issue():
    inst = SweBenchInstance(
        instance_id="x",
        repo="o/r",
        base_commit="abc",
        problem_statement="Title\n\nBody text here",
    )
    action = build_task_action(inst)
    assert "Body text here" in action
    assert "test files" in action.lower()


def test_extract_model_patch_resets_tests(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("v1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_x():\n    pass\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    (repo / "src" / "app.py").write_text("v2\n", encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text("def test_x():\n    assert False\n", encoding="utf-8")

    test_patch = "diff --git a/tests/test_app.py b/tests/test_app.py\n"
    patch = extract_model_patch(repo, test_patch)
    assert "app.py" in patch
    assert "v2" in patch or "+v2" in patch
    assert "test_app.py" not in patch or "tests/test_app.py" not in patch.split("diff --git")[1:]


def test_write_predictions_jsonl(tmp_path):
    out = tmp_path / "preds.jsonl"
    write_predictions_jsonl(
        [
            {
                "instance_id": "sympy__sympy-20590",
                "model_name_or_path": "awos-react",
                "model_patch": "diff example",
            }
        ],
        out,
    )
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["instance_id"] == "sympy__sympy-20590"


@pytest.mark.integration
def test_load_lite_instances_default_subset():
    pytest.importorskip("datasets")
    from scaffold.agent.swebench_lite import load_lite_instances

    instances = load_lite_instances(limit=3, use_default_subset=True)
    assert len(instances) == 3
    assert instances[0].instance_id == load_default_subset_ids()[0]

from __future__ import annotations

import json
from pathlib import Path

from scripts.task_context import build_context_packet
from scripts.session_response import validate_record, render_record
from scripts.token_report import context_metrics, verified_task_metrics


def test_context_packet_is_bounded_and_contains_canonical_facts(tmp_path: Path):
    (tmp_path / "memories" / "repo").mkdir(parents=True)
    (tmp_path / "docs" / "memory").mkdir(parents=True)
    (tmp_path / "tasks" / "active").mkdir(parents=True)
    (tmp_path / "memories" / "repo" / "project_structure.md").write_text("PROJECT FACTS\n" + "x" * 1000)
    (tmp_path / "docs" / "memory" / "checkpoint_2026-01-01.md").write_text("NEXT: verify")
    (tmp_path / "tasks" / "active" / "demo.md").write_text("TASK: do the thing")
    (tmp_path / "target.py").write_text("def target():\n    return 1\n")

    packet = build_context_packet(
        root=tmp_path,
        task="do the thing",
        task_file="tasks/active/demo.md",
        files=["target.py"],
        max_chars=700,
    )

    assert len(packet) <= 700
    assert "PROJECT FACTS" in packet
    assert "target.py" in packet
    assert "TASK: do the thing" in packet
    assert "[TRUNCATED]" in packet


def test_context_packet_rejects_paths_outside_root(tmp_path: Path):
    try:
        build_context_packet(tmp_path, "x", files=["../secret.txt"])
    except ValueError as exc:
        assert "outside project root" in str(exc)
    else:
        raise AssertionError("outside-root path was accepted")


def test_response_record_is_compact_and_validates():
    record = {
        "status": "done",
        "changed": [{"path": "a.py", "line": 3, "summary": "fixed bug"}],
        "verified": [{"command": "pytest -q", "result": "1 passed"}],
        "next": "checkpoint",
    }
    assert validate_record(record) == []
    rendered = render_record(record)
    assert "STATUS: done" in rendered
    assert "pytest -q" in rendered
    assert len(rendered) < 300


def test_response_record_rejects_unknown_status():
    errors = validate_record({"status": "maybe"})
    assert any("status" in error for error in errors)


def test_response_record_is_json_serializable():
    record = {"status": "blocked", "changed": [], "verified": [], "next": "wait"}
    json.dumps(record)
    assert validate_record(record) == []


def test_context_metrics_estimate_tokens_and_budget():
    metrics = context_metrics("abcd" * 25, budget_chars=80)
    assert metrics["characters"] == 100
    assert metrics["estimated_tokens"] == 25
    assert metrics["within_budget"] is False


def test_verified_task_metrics_counts_successes_and_cost():
    metrics = verified_task_metrics([
        {"verified": True, "cost": 0.02},
        {"verified": False, "cost": 0.01},
    ])
    assert metrics == {
        "tasks": 2,
        "verified_tasks": 1,
        "verification_rate": 50.0,
        "total_cost": 0.03,
        "cost_per_verified_task": 0.03,
    }

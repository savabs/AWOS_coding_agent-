"""Tests for placement learning ledger."""

import json
from pathlib import Path

from scaffold.agent.placement_ledger import PlacementLedger


def test_record_and_summary(tmp_path):
    path = tmp_path / "placement_ledger.jsonl"
    ledger = PlacementLedger(path=path)

    ledger.record(
        task_id="1",
        goal="research note",
        path="docs/research/a.html",
        artifact_kind="research",
        expected_folder="docs/research",
        placement_ok=True,
        task_success=True,
    )
    ledger.record(
        task_id="2",
        goal="research note",
        path="docs/b.html",
        artifact_kind="research",
        expected_folder="docs/research",
        placement_ok=False,
        task_success=True,
        auto_fixed=False,
        issues=["wrong folder"],
    )

    s = ledger.summary()
    assert s["total"] == 2
    assert s["by_kind"]["research"]["count"] == 2
    assert s["by_kind"]["research"]["placement_ok"] == 1
    assert s["by_kind"]["research"]["placement_rate"] == 0.5

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["artifact_kind"] == "research"

"""Tests for PEI report generation."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scaffold" / "agent"))

from pei_report import PEIReport


def test_pei_report_empty_store(tmp_path):
    report = PEIReport(store_path=str(tmp_path), project_name="Test")
    snap = report.snapshot()
    assert snap.project_name == "Test"
    assert snap.total_tasks == 0
    assert snap.pei_score > 0


def test_pei_report_with_spans(tmp_path):
    store = tmp_path / ".awos"
    store.mkdir()
    spans = [
        {
            "success": True,
            "cost_usd": 0.001,
            "input_tokens": 500,
            "output_tokens": 200,
            "total_latency_ms": 2000,
            "model_chosen": "DeepSeek V4 Flash",
        },
        {
            "success": False,
            "cost_usd": 0.017,
            "input_tokens": 3000,
            "output_tokens": 800,
            "total_latency_ms": 3000,
            "model_chosen": "Claude Haiku 4.5",
        },
    ]
    (store / "spans.jsonl").write_text(
        "\n".join(json.dumps(s) for s in spans) + "\n"
    )

    report = PEIReport(store_path=str(store), project_name="Demo")
    snap = report.snapshot()
    assert snap.total_tasks == 2
    assert snap.task_success_rate == 0.5
    assert snap.total_tokens == 4500
    assert snap.avg_tokens_per_task == pytest.approx(2250.0)
    assert snap.tokens_by_model["DeepSeek V4 Flash"] == 700
    assert snap.total_cost_usd == pytest.approx(0.018, abs=0.001)
    assert snap.savings_vs_sonnet_pct > 0

    html_path = report.write_html(tmp_path / "report.html")
    assert html_path.exists()
    content = html_path.read_text()
    assert "Project Efficiency Report" in content
    assert "Demo" in content

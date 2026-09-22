"""Tests for integration reviewer cheap-only behaviour."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scaffold" / "agent"))

from integration_reviewer import IntegrationReviewer


def test_parse_filters_noise_issues():
    ir = IntegrationReviewer(api_key=None)
    text = "VERDICT: PASS\nISSUES:\n- None identified\nSUGGESTIONS:\n"
    result = ir._parse_response(text, model_used="test")
    assert result["passed"] is True
    assert result["issues"] == []


def test_cheap_only_skips_without_deepseek(monkeypatch):
    monkeypatch.setenv("AWOS_CHEAP_ONLY", "true")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    ir = IntegrationReviewer(api_key=None)
    ir._get_diff = lambda _root: "+# sample diff\n"
    result = ir.review(".", [{"task_id": 1, "status": "completed", "reason": "ok"}])
    assert result.get("skipped") is True
    assert "cheap-only" in result.get("skip_reason", "").lower()

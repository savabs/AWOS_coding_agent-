"""Provider-free regressions for native Planner holdout integrity."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_native_required_planning_does_not_fallback_to_cheap_planner(tmp_path: Path, monkeypatch) -> None:
    from scaffold.agent.orchestrator import Orchestrator

    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setenv("AWOS_NATIVE_PLANNER_REQUIRED", "1")
    monkeypatch.setenv("AWOS_ENABLE_CLARIFICATION", "0")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "fake-key-for-test")
    monkeypatch.delenv("AWOS_CHEAP_ONLY", raising=False)
    monkeypatch.delenv("AWOS_PREMIUM_BUDGET", raising=False)

    with (
        patch("scaffold.agent.orchestrator.Planner.plan", side_effect=RuntimeError("planner unavailable")),
        patch(
            "scaffold.agent.orchestrator.CheapPlanner.plan",
            side_effect=AssertionError("native mode must not use CheapPlanner fallback"),
        ),
    ):
        result = Orchestrator().execute_feature(
            goal="Make the module correct.",
            codebase_root=str(tmp_path),
        )

    assert result["success"] is False
    assert any("Native Planner failed" in error for error in result["errors"])

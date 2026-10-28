"""Tests for coarse deliverable routing and create-file execution."""

import json

import pytest

from scaffold.agent.deliverable_router import DeliverableRouter, TaskKind
from scaffold.agent.goal_acceptance import verify_deliverable
from scaffold.agent.plan_actions import CREATE_FILE, normalize_task


class TestCoarseRouter:
    def test_create_signals(self):
        r = DeliverableRouter().classify("create docs/guide.md about agents")
        assert r.kind == TaskKind.CREATE

    def test_mutate_signals(self):
        r = DeliverableRouter().classify("fix bug in worker.py")
        assert r.kind == TaskKind.MUTATE

    def test_no_path_in_decision(self):
        r = DeliverableRouter().classify("create checkpoint html")
        assert r.target_path is None if hasattr(r, "target_path") else True


class TestGoalAcceptance:
    def test_missing_file(self, tmp_path):
        assert verify_deliverable(tmp_path / "nope.md")

    def test_valid_markdown(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# Title\n\nEnough content here for acceptance.\n")
        assert verify_deliverable(f) == []


class TestCreateFileExecutor:
    def test_writes_file_with_mock_llm(self, tmp_path):
        from scaffold.agent.create_file_executor import CreateFileExecutor

        def fake_llm(system, user):
            return (
                "# Guide\n\nUse local models via Ollama.\n",
                "mock",
                {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30, "cost_usd": 0.0},
            )

        ex = CreateFileExecutor(llm_caller=fake_llm)
        result = ex.execute(
            goal="create docs/uncensored_agent_guide.md",
            target_path="docs/uncensored_agent_guide.md",
            codebase_root=tmp_path,
        )
        assert result.success
        assert (tmp_path / "docs/uncensored_agent_guide.md").is_file()


class TestPlannerDrivenCreate:
    def test_orchestrator_create_task(self, tmp_path, monkeypatch):
        from scaffold.agent.deliverable_e2e import (
            _bootstrap_e2e_env,
            install_mock_create_executor,
            install_mock_planner,
        )
        from scaffold.agent.orchestrator import Orchestrator
        from scaffold.agent.token_tracker import TokenTracker

        _bootstrap_e2e_env()
        (tmp_path / "docs").mkdir()
        decoy = tmp_path / "run_awos_demo.py"
        decoy.write_text("# demo\n", encoding="utf-8")
        before = decoy.read_text()
        install_mock_create_executor(monkeypatch)
        install_mock_planner(monkeypatch)

        orch = Orchestrator(tracker=TokenTracker(monthly_budget=20.0))
        result = orch.execute_feature(
            goal="create docs/planner_owned.md about uncensored agents",
            codebase_root=str(tmp_path),
        )
        assert result["success"], result.get("errors")
        assert decoy.read_text() == before
        out = tmp_path / "docs/planner_owned.md"
        assert out.is_file()

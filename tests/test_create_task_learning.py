"""Tests for create_file ML/RL learning loop wiring."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestCreateTaskLearning:
    def test_create_file_feature_flag(self):
        from scaffold.agent.ml_router import TaskFeatureExtractor

        ext = TaskFeatureExtractor()
        task = {
            "task_type": "create_file",
            "action": "update README",
            "path": "docs/guide.md",
            "complexity": "low",
        }
        feat = ext.extract(task, 0)
        assert feat[3] == 1.0
        assert feat[7] == 0.0

    def test_classify_task_prefers_planner_type(self):
        from scaffold.agent.core.performance_tracker import ToolPerformanceTracker

        task = {
            "task_type": "create_file",
            "action": "fix bug in parser",
        }
        assert ToolPerformanceTracker.classify_task(task) == "create_file"
        assert ToolPerformanceTracker.classify_task({
            "task_type": "edit_file",
            "action": "add oauth",
        }) == "edit_file"

    def test_execute_create_logs_episode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        monkeypatch.setenv("AWOS_E2E", "1")

        from scaffold.agent.core.performance_tracker import ToolPerformanceTracker
        from scaffold.agent.core.reasoning import ReasoningSession
        from scaffold.agent.create_file_executor import CreateFileExecutor, CreateFileResult
        from scaffold.agent.orchestrator import Orchestrator
        from scaffold.agent.reward_store import RewardStore
        from scaffold.agent.git_manager import GitManager

        awos_dir = tmp_path / ".awos"
        awos_dir.mkdir()
        (tmp_path / "docs").mkdir()

        orch = Orchestrator(tracker=None)
        orch.reward_store = RewardStore(path=awos_dir / "reward_store.jsonl")
        orch.performance = ToolPerformanceTracker(persist_dir=awos_dir)
        orch.ml_router._weights_path = awos_dir / "linucb.pkl"

        monkeypatch.setattr(
            CreateFileExecutor,
            "execute",
            lambda self, **kwargs: CreateFileResult(
                success=True,
                paths=["docs/learning_test.md"],
                content_bytes=42,
                model_used="gemini-2.5-flash",
                usage={
                    "input_tokens": 50,
                    "output_tokens": 80,
                    "cost_usd": 0.001,
                },
            ),
        )
        (tmp_path / "docs" / "learning_test.md").write_text("# ok\n", encoding="utf-8")

        task = {
            "task_id": 1,
            "task_type": "create_file",
            "path": "docs/learning_test.md",
            "action": "write learning test doc",
            "complexity": "low",
        }
        session = ReasoningSession(goal="write learning test doc", session_id="s1")
        ctx = {
            "codebase_root": str(tmp_path),
            "session": session,
            "git": GitManager(str(tmp_path)),
            "codebase_context": {},
            "sym_index": None,
        }

        result = orch._execute_create_task(task, ctx)
        assert result["success"] is True

        episodes = orch.reward_store.get_recent(5)
        assert len(episodes) == 1
        ep = episodes[0]
        assert ep.task_type == "create_file"
        assert ep.success is True
        assert ep.action_id == 0
        assert len(ep.features) == 10
        assert ep.features[3] == pytest.approx(1.0)

        records = orch.performance._records
        assert any(r.get("task_type") == "create_file" for r in records)

    def test_model_name_to_action_id(self):
        from scaffold.agent.task_learning import model_name_to_action_id

        assert model_name_to_action_id("gemini-2.5-flash") == 0
        assert model_name_to_action_id("deepseek-chat") == 1
        assert model_name_to_action_id("gpt-4o-mini") == 2

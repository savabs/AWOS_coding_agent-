"""Tests for MCTSTraceStore alias and count/get_recent."""

from __future__ import annotations

from scaffold.agent.process_reward_model import MCTSTrace, MCTSTraceStore


def test_trace_store_count_and_recent(tmp_path):
    store = MCTSTraceStore(base_dir=str(tmp_path / ".awos"))
    assert store.count() == 0

    for i in range(3):
        store.log_mcts_trace(
            MCTSTrace(
                task_id=str(i),
                task_action=f"action {i}",
                task_features=[0.1] * 10,
                nodes=[],
                winning_path=[],
                total_rollouts=1,
                final_reward=0.5,
            )
        )

    assert store.count() == 3
    recent = store.get_recent(2)
    assert len(recent) == 2
    assert recent[-1].task_id == "2"

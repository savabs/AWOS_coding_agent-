"""Tests for LearningState — durable session counter and candidate KPI gate."""

from __future__ import annotations

import json
import pytest

from scaffold.agent.learning_state import LearningState, KPIWindow, _compute_kpi, SessionKPI


@pytest.fixture
def state(tmp_path):
    awos = tmp_path / ".awos"
    awos.mkdir()
    return LearningState(store_path=str(awos))


class TestSessionCounter:
    def test_starts_at_zero(self, state):
        assert state.sessions_completed == 0

    def test_record_session_increments(self, state):
        state.record_session(success=True, cost_usd=0.01, tasks_failed=0, tasks_total=1)
        assert state.sessions_completed == 1

    def test_counter_survives_reload(self, tmp_path):
        awos = tmp_path / ".awos"
        awos.mkdir()
        ls1 = LearningState(store_path=str(awos))
        ls1.record_session(success=True, cost_usd=0.0, tasks_failed=0, tasks_total=1)
        ls1.record_session(success=False, cost_usd=0.0, tasks_failed=1, tasks_total=1)
        ls2 = LearningState(store_path=str(awos))
        assert ls2.sessions_completed == 2


class TestCandidateGate:
    def test_accept_when_success_improves(self, state, monkeypatch):
        monkeypatch.setenv("AWOS_PROMPT_ACCEPT_WINDOW", "3")
        for _ in range(3):
            state.record_session(success=False, cost_usd=0.01, tasks_failed=1, tasks_total=1)
        state.start_candidate_probation(version=2)
        for _ in range(3):
            state.record_session(
                success=True,
                cost_usd=0.01,
                tasks_failed=0,
                tasks_total=1,
                used_candidate_prompt=True,
            )
            state.record_candidate_session()
        assert state.maybe_finalize_candidate() == "accepted"
        assert state.active_version == 2
        assert not state.has_candidate()

    def test_reject_when_success_regresses(self, state, monkeypatch):
        monkeypatch.setenv("AWOS_PROMPT_ACCEPT_WINDOW", "3")
        monkeypatch.setenv("AWOS_PROMPT_MIN_SUCCESS_DROP", "0.05")
        for _ in range(3):
            state.record_session(success=True, cost_usd=0.01, tasks_failed=0, tasks_total=1)
        state.start_candidate_probation(version=2)
        for _ in range(3):
            state.record_session(
                success=False,
                cost_usd=0.01,
                tasks_failed=1,
                tasks_total=1,
                used_candidate_prompt=True,
            )
            state.record_candidate_session()
        assert state.maybe_finalize_candidate() == "rejected"
        assert state.active_version == 0

    def test_not_finalized_before_window(self, state, monkeypatch):
        monkeypatch.setenv("AWOS_PROMPT_ACCEPT_WINDOW", "5")
        state.start_candidate_probation(version=1)
        state.record_session(success=True, cost_usd=0.0, tasks_failed=0, tasks_total=1, used_candidate_prompt=True)
        state.record_candidate_session()
        assert state.maybe_finalize_candidate() is None
        assert state.has_candidate()


class TestKPI:
    def test_compute_kpi_empty(self):
        assert _compute_kpi([]).n == 0

    def test_compute_kpi_mixed(self):
        sessions = [
            SessionKPI(success=True, cost_usd=0.02, tasks_failed=0, tasks_total=2),
            SessionKPI(success=False, cost_usd=0.01, tasks_failed=1, tasks_total=1),
        ]
        kpi = _compute_kpi(sessions)
        assert kpi.n == 2
        assert kpi.success_rate == 0.5
        assert kpi.cost_per_task == pytest.approx(0.01, rel=1e-3)

    def test_summary_written_to_disk(self, state, tmp_path):
        state.record_session(success=True, cost_usd=0.0, tasks_failed=0, tasks_total=1)
        path = tmp_path / ".awos" / "learning_state.json"
        data = json.loads(path.read_text())
        assert data["sessions_completed"] == 1
        assert len(data["recent_sessions"]) == 1

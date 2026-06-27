"""Tests for RuntimeSession — durable pause/resume session store."""

from __future__ import annotations

import json

import pytest

from scaffold.agent.runtime_session import (
    RuntimeSession,
    RuntimeSessionStore,
    SessionResumeError,
    SessionStatus,
    goal_hash,
    validate_session_id,
)


@pytest.fixture
def store(tmp_path):
    sessions_dir = tmp_path / ".awos" / "sessions"
    sessions_dir.mkdir(parents=True)
    return RuntimeSessionStore(sessions_dir=str(sessions_dir))


class TestSessionId:
    def test_valid_id(self):
        validate_session_id("rs_a1b2c3d4e5f6")

    def test_invalid_id_raises(self):
        with pytest.raises(ValueError):
            validate_session_id("orch_abc123")


class TestCreateSession:
    def test_create_session(self, store):
        session = store.create("add login system", codebase_root="/repo")
        assert session.session_id.startswith("rs_")
        assert len(session.session_id) == 15  # rs_ + 12 hex
        assert session.schema_version == 1
        assert session.status == SessionStatus.PENDING
        assert session.goal == "add login system"
        assert session.codebase_root == "/repo"
        assert (store.sessions_dir / f"{session.session_id}.json").exists()

    def test_create_empty_goal_raises(self, store):
        with pytest.raises(ValueError):
            store.create("   ")


class TestSaveAtomic:
    def test_save_atomic(self, store):
        session = store.create("goal one")
        path = store.sessions_dir / f"{session.session_id}.json"
        original = path.read_text(encoding="utf-8")

        session.status = SessionStatus.RUNNING
        store.save(session)

        assert path.read_text(encoding="utf-8") != original
        assert not list(store.sessions_dir.glob("*.tmp"))

    def test_reload_roundtrip(self, store):
        session = store.create("roundtrip goal")
        session.status = SessionStatus.RUNNING
        session.progress.total_tasks = 5
        session.budget["spent_usd"] = 0.12
        store.save(session)

        loaded = store.load(session.session_id)
        assert loaded.status == SessionStatus.RUNNING
        assert loaded.progress.total_tasks == 5
        assert loaded.budget["spent_usd"] == 0.12


class TestCheckpoint:
    def test_checkpoint_updates_progress(self, store):
        session = store.create("checkpoint goal")
        session.status = SessionStatus.RUNNING
        store.save(session)

        store.checkpoint(
            session,
            completed_task_id=1,
            current_task_id=2,
            total_tasks=4,
            spent_usd=0.05,
        )
        loaded = store.load(session.session_id)
        assert loaded.progress.completed_task_ids == [1]
        assert loaded.progress.current_task_id == 2
        assert loaded.progress.total_tasks == 4
        assert loaded.budget["spent_usd"] == 0.05

    def test_checkpoint_failed_task(self, store):
        session = store.create("fail task")
        store.checkpoint(session, failed_task_id=7)
        loaded = store.load(session.session_id)
        assert loaded.progress.failed_task_ids == [7]

    def test_completed_clears_failed(self, store):
        session = store.create("clear failed")
        store.checkpoint(session, failed_task_id=3)
        store.checkpoint(session, completed_task_id=3)
        loaded = store.load(session.session_id)
        assert loaded.progress.completed_task_ids == [3]
        assert loaded.progress.failed_task_ids == []


class TestListSessions:
    def test_list_sessions_filter_status(self, store):
        a = store.create("goal a")
        b = store.create("goal b")
        store.finalize(a, SessionStatus.PAUSED)
        store.finalize(b, SessionStatus.COMPLETED)

        paused = store.list_sessions(status=SessionStatus.PAUSED)
        assert len(paused) == 1
        assert paused[0].session_id == a.session_id

    def test_list_skips_corrupt_file(self, store):
        store.create("good goal")
        bad = store.sessions_dir / "rs_deadbeefcafe.json"
        bad.write_text("{not json", encoding="utf-8")
        sessions = store.list_sessions()
        assert len(sessions) == 1

    def test_list_newest_first(self, store):
        import os
        import time

        first = store.create("first")
        second = store.create("second")
        store.checkpoint(second, current_task_id=1)
        # Ensure distinct mtime for filesystem sort
        path = store.sessions_dir / f"{second.session_id}.json"
        os.utime(path, (time.time() + 2, time.time() + 2))
        listed = store.list_sessions()
        assert listed[0].session_id == second.session_id
        assert listed[1].session_id == first.session_id


class TestFindLatestForGoal:
    def test_find_latest_for_goal(self, store):
        goal = "Build Auth Module"
        s1 = store.create(goal)
        store.finalize(s1, SessionStatus.COMPLETED)
        s2 = store.create(goal)
        store.finalize(s2, SessionStatus.PAUSED)

        found = store.find_latest_for_goal("  build auth module  ")
        assert found is not None
        assert found.session_id == s2.session_id

    def test_find_latest_running(self, store):
        goal = "running goal"
        session = store.create(goal)
        session.status = SessionStatus.RUNNING
        store.save(session)
        found = store.find_latest_for_goal(goal)
        assert found.session_id == session.session_id

    def test_find_latest_none(self, store):
        assert store.find_latest_for_goal("missing") is None


class TestFork:
    def test_fork_copies_progress(self, store):
        parent = store.create("parent goal")
        store.checkpoint(parent, completed_task_id=1, total_tasks=3)
        store.finalize(parent, SessionStatus.PAUSED)

        child = store.fork(parent.session_id)
        assert child.session_id != parent.session_id
        assert child.parent_session_id == parent.session_id
        assert child.progress.completed_task_ids == [1]
        assert child.status == SessionStatus.PENDING

        parent_reloaded = store.load(parent.session_id)
        assert parent_reloaded.progress.completed_task_ids == [1]
        assert parent_reloaded.status == SessionStatus.PAUSED

    def test_fork_new_goal(self, store):
        parent = store.create("original")
        child = store.fork(parent.session_id, new_goal="variant goal")
        assert child.goal == "variant goal"
        assert child.goal_hash == goal_hash("variant goal")


class TestResume:
    def test_begin_resume_from_paused(self, store):
        session = store.create("resume me")
        store.finalize(session, SessionStatus.PAUSED)
        resumed = store.begin_resume(session.session_id)
        assert resumed.status == SessionStatus.RUNNING
        assert resumed.paused_at is None

    def test_resume_error_wrong_status(self, store):
        session = store.create("done")
        store.finalize(session, SessionStatus.COMPLETED)
        with pytest.raises(SessionResumeError):
            store.begin_resume(session.session_id)


class TestCancel:
    def test_request_cancel_flag(self, store):
        session = store.create("cancel test")
        session.status = SessionStatus.RUNNING
        store.save(session)

        updated = store.request_cancel(session.session_id)
        assert updated.cancel_requested is True
        loaded = store.load(session.session_id)
        assert loaded.cancel_requested is True


class TestFinalize:
    def test_finalize_clears_cancel(self, store):
        session = store.create("finalize")
        store.request_cancel(session.session_id)
        store.finalize(session, SessionStatus.CANCELLED)
        loaded = store.load(session.session_id)
        assert loaded.status == SessionStatus.CANCELLED
        assert loaded.cancel_requested is False

    def test_finalize_paused_sets_timestamp(self, store):
        session = store.create("pause")
        store.finalize(session, SessionStatus.PAUSED)
        loaded = store.load(session.session_id)
        assert loaded.paused_at is not None


class TestFromDict:
    def test_from_dict_missing_fields(self):
        session = RuntimeSession.from_dict({"goal": "minimal", "session_id": "rs_" + "a" * 12})
        assert session.progress.completed_task_ids == []
        assert session.budget["spent_usd"] == 0.0
        assert session.sandbox.enabled is False

    def test_from_dict_invalid_status_defaults_pending(self):
        session = RuntimeSession.from_dict(
            {
                "session_id": "rs_" + "b" * 12,
                "goal": "x",
                "status": "not_a_status",
            }
        )
        assert session.status == SessionStatus.PENDING


class TestLoadErrors:
    def test_load_missing_raises(self, store):
        with pytest.raises(FileNotFoundError):
            store.load("rs_" + "c" * 12)

    def test_load_corrupt_raises(self, store):
        sid = "rs_" + "d" * 12
        path = store.sessions_dir / f"{sid}.json"
        path.write_text("{bad", encoding="utf-8")
        with pytest.raises(ValueError):
            store.load(sid)

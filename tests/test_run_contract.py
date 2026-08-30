"""Contract tests for durable AWOS run manifests, events, and replay."""

from __future__ import annotations

import json

import pytest

from scaffold.agent.core.run_contract import (
    ResultBundle,
    RunEvent,
    RunEventLog,
    RunManifest,
    RunState,
    RunStateMachine,
    RunStateTransitionError,
    replay_run_events,
)


def _manifest() -> RunManifest:
    return RunManifest(
        run_id="run_test_001",
        goal="Fix the parser",
        repository="example/repo",
        commit="abc123",
        policy={"network": "deny", "auto_merge": False},
        model={"provider": "test", "model": "fixture"},
        budget={"max_cost_usd": 1.0},
        workspace={"provider": "local-test", "image": "sha256:image"},
        tool_profile="coding",
    )


def test_manifest_has_deterministic_versioned_serialization_and_redacts_secrets() -> None:
    manifest = _manifest()
    first = manifest.to_json()
    second = RunManifest.from_dict(json.loads(first)).to_json()

    assert first == second
    assert json.loads(first)["schema_version"] == 1
    assert "[REDACTED]" not in first

    with_secret = RunManifest(
        **{
            **manifest.to_dict(),
            "policy": {"token": "secret-value"},
        }
    )
    assert "secret-value" not in with_secret.to_json()
    assert "[REDACTED]" in with_secret.to_json()


def test_state_machine_persists_valid_transitions_and_rejects_invalid_transition() -> None:
    machine = RunStateMachine(_manifest())

    assert machine.state == RunState.ADMITTED
    machine.transition(RunState.PROVISIONING, reason="workspace requested")
    machine.transition(RunState.EXPLORING)

    with pytest.raises(RunStateTransitionError, match="cannot transition"):
        machine.transition(RunState.COMPLETED)

    assert [event.event_type for event in machine.events] == [
        "run_started",
        "state_changed",
        "state_changed",
    ]
    assert machine.events[-1].sequence == 2


def test_event_log_is_append_only_and_rejects_duplicate_or_out_of_order_sequences(tmp_path) -> None:
    log = RunEventLog(tmp_path / "events.jsonl", run_id="run_test_001")
    event = RunEvent(
        run_id="run_test_001",
        sequence=0,
        event_type="run_started",
        state=RunState.ADMITTED,
        payload={"goal": "Fix the parser"},
    )
    log.append(event)

    with pytest.raises(ValueError, match="sequence"):
        log.append(event)

    with pytest.raises(ValueError, match="sequence"):
        log.append(
            RunEvent(
                run_id="run_test_001",
                sequence=2,
                event_type="state_changed",
                state=RunState.PROVISIONING,
                payload={},
            )
        )

    assert log.read() == [event]


def test_replay_reconstructs_state_and_detects_tampering(tmp_path) -> None:
    log = RunEventLog(tmp_path / "events.jsonl", run_id="run_test_001")
    machine = RunStateMachine(_manifest())
    machine.transition(RunState.PROVISIONING)
    machine.transition(RunState.EXPLORING)
    for event in machine.events:
        log.append(event)

    replayed = replay_run_events(_manifest(), log.read())
    assert replayed.state == RunState.EXPLORING
    assert replayed.event_count == 3

    raw = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    (tmp_path / "events.jsonl").write_text(raw.replace("exploring", "completed"), encoding="utf-8")
    with pytest.raises(ValueError, match="digest|state|event"):
        replay_run_events(_manifest(), log.read())


def test_run_contract_store_persists_manifest_and_hash_chained_lifecycle(tmp_path) -> None:
    from scaffold.agent.core.run_contract import RunContractStore

    store = RunContractStore(tmp_path / "run")
    manifest = _manifest()
    store.create(manifest)
    store.transition(manifest.run_id, RunState.PROVISIONING, reason="workspace requested")
    store.transition(manifest.run_id, RunState.EXPLORING)

    loaded_manifest = store.load_manifest(manifest.run_id)
    replayed = store.replay(manifest.run_id)

    assert loaded_manifest.to_json() == manifest.to_json()
    assert replayed.state == RunState.EXPLORING
    assert replayed.event_count == 3
    assert (tmp_path / "run" / manifest.run_id / "manifest.json").exists()
    assert (tmp_path / "run" / manifest.run_id / "events.jsonl").exists()


def test_run_contract_store_refuses_invalid_transition(tmp_path) -> None:
    from scaffold.agent.core.run_contract import RunContractStore

    store = RunContractStore(tmp_path / "run")
    manifest = _manifest()
    store.create(manifest)

    with pytest.raises(RunStateTransitionError):
        store.transition(manifest.run_id, RunState.COMPLETED)


def test_run_contract_store_detects_event_file_tampering(tmp_path) -> None:
    from scaffold.agent.core.run_contract import RunContractStore

    store = RunContractStore(tmp_path / "run")
    manifest = _manifest()
    store.create(manifest)
    store.transition(manifest.run_id, RunState.PROVISIONING)
    events_path = tmp_path / "run" / manifest.run_id / "events.jsonl"
    raw = events_path.read_text(encoding="utf-8")
    events_path.write_text(raw.replace("provisioning", "completed"), encoding="utf-8")

    with pytest.raises(ValueError, match="digest|hash|state"):
        store.replay(manifest.run_id)


def test_result_bundle_requires_verification_before_success() -> None:
    bundle = ResultBundle(run_id="run_test_001")
    bundle.add_verification("syntax", "PASS", {"exit_code": 0})
    assert bundle.can_complete is False

    bundle.add_verification("acceptance", "PASS", {"tests": 3})
    bundle.finalize("completed")
    assert bundle.can_complete is True
    assert bundle.status == "completed"

    with pytest.raises(ValueError, match="verification"):
        ResultBundle(run_id="run_test_002").finalize("completed")

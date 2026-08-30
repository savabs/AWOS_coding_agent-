"""Focused regressions for the execution sandbox policy contract."""

from __future__ import annotations

import json
import subprocess

import pytest

from scaffold.agent.coding_tools import WorkspaceShellTool
from scaffold.agent.git_manager import GitManager
from scaffold.agent.hitl_consent import HITLConsent
from scaffold.agent.tools.shell import ShellTool, run_bounded_command
from scaffold.agent.runtime_session import (
    RuntimeSession,
    SandboxPathError,
    SandboxPolicy,
    SandboxPolicyError,
    SandboxSecurityEvidence,
    SandboxEvidenceStore,
    SessionSandbox,
    resolve_workspace_path,
)


def test_policy_serialization_is_deterministic_and_digest_stable() -> None:
    policy = SandboxPolicy(
        writable_roots=("workspace", "tmp"),
        network_mode="deny",
        approval_mode="risky",
    )

    first = policy.to_json()
    second = SandboxPolicy.from_dict(json.loads(first)).to_json()

    assert first == second
    assert json.loads(first) == {
        "approval_mode": "risky",
        "command_timeout_seconds": 30,
        "isolation_level": "worktree_only",
        "max_output_bytes": 1_048_576,
        "network_mode": "deny",
        "policy_version": "sandbox_v1",
        "writable_roots": ["workspace", "tmp"],
    }
    assert policy.digest() == SandboxPolicy.from_dict(json.loads(first)).digest()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("isolation_level", "unknown"),
        ("network_mode", "allow_everything"),
        ("approval_mode", "unknown"),
        ("policy_version", ""),
    ],
)
def test_unknown_policy_values_fail_closed(field: str, value: str) -> None:
    values = SandboxPolicy().to_dict()
    values[field] = value

    with pytest.raises(SandboxPolicyError):
        SandboxPolicy.from_dict(values)


def test_container_isolation_fails_closed_until_backend_exists() -> None:
    values = SandboxPolicy().to_dict()
    values["isolation_level"] = "container_isolated"

    with pytest.raises(SandboxPolicyError, match="container_isolated"):
        SandboxPolicy.from_dict(values)


def test_unsafe_writable_root_fails_closed() -> None:
    values = SandboxPolicy().to_dict()
    values["writable_roots"] = ["workspace", "../outside"]

    with pytest.raises(SandboxPolicyError):
        SandboxPolicy.from_dict(values)


def test_workspace_path_allows_safe_relative_descendant(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    resolved = resolve_workspace_path(workspace, "src/new_file.py")

    assert resolved == workspace / "src" / "new_file.py"


@pytest.mark.parametrize("requested", ["/etc/passwd", "../outside.txt", "src/../../outside.txt", "src\\..\\outside.txt"])
def test_workspace_path_rejects_absolute_and_traversal(tmp_path, requested: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(SandboxPathError):
        resolve_workspace_path(workspace, requested)


def test_workspace_path_rejects_symlink_escape(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SandboxPathError):
        resolve_workspace_path(workspace, "link/created.txt")


def test_action_approval_is_bound_to_exact_session_policy_and_action() -> None:
    consent = HITLConsent()
    approval = consent.create_action_approval(
        session_id="rs_123456789abc",
        policy_digest="policy-a",
        command="pytest",
        arguments=("-q",),
        paths=("tests/test_app.py",),
        approved=True,
    )

    assert consent.validate_action_approval(
        approval,
        session_id="rs_123456789abc",
        policy_digest="policy-a",
        command="pytest",
        arguments=("-q",),
        paths=("tests/test_app.py",),
    )
    assert not consent.validate_action_approval(
        approval,
        session_id="rs_123456789abc",
        policy_digest="policy-a",
        command="pytest",
        arguments=("-q", "-s"),
        paths=("tests/test_app.py",),
    )
    assert not consent.validate_action_approval(
        approval,
        session_id="rs_other12345",
        policy_digest="policy-a",
        command="pytest",
        arguments=("-q",),
        paths=("tests/test_app.py",),
    )
    assert not consent.validate_action_approval(
        approval,
        session_id="rs_123456789abc",
        policy_digest="policy-b",
        command="pytest",
        arguments=("-q",),
        paths=("tests/test_app.py",),
    )


def test_denied_action_never_validates() -> None:
    consent = HITLConsent()
    approval = consent.create_action_approval(
        session_id="rs_123456789abc",
        policy_digest="policy-a",
        command="rm",
        arguments=("-rf", "tmp"),
        paths=("tmp",),
        approved=False,
    )

    assert not consent.validate_action_approval(
        approval,
        session_id="rs_123456789abc",
        policy_digest="policy-a",
        command="rm",
        arguments=("-rf", "tmp"),
        paths=("tmp",),
    )


def test_bounded_command_terminates_process_group_on_timeout(tmp_path) -> None:
    marker = tmp_path / "late-marker"
    command = (
        "python3 -c "
        f"\"import pathlib,time; time.sleep(2); pathlib.Path({str(marker)!r}).write_text('bad')\""
    )

    result = run_bounded_command(command, cwd=tmp_path, timeout_seconds=0.1)

    assert result.timed_out
    assert result.returncode is None
    assert not marker.exists()


def test_bounded_command_truncates_output() -> None:
    result = run_bounded_command(
        "python3 -c \"print('x' * 100)\"",
        cwd=".",
        timeout_seconds=5,
        max_output_bytes=16,
    )

    assert result.returncode == 0
    assert result.output_limited
    assert len(result.stdout.encode()) <= 16


def _git(repo, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_worktree_lifecycle_isolated_and_idempotent(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "AWOS Test")
    (repo / "app.py").write_text("value = 1\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "initial")

    sandbox_path = tmp_path / "sandbox"
    manager = GitManager(str(repo))
    created = manager.create_worktree(sandbox_path)

    assert created == sandbox_path.resolve()
    assert (created / "app.py").read_text() == "value = 1\n"
    (created / "app.py").write_text("value = 2\n")
    assert (repo / "app.py").read_text() == "value = 1\n"

    assert manager.cleanup_worktree()
    assert not sandbox_path.exists()
    assert manager.cleanup_worktree()
    assert manager.status()["cleanup_state"] == "cleaned"


def test_worktree_cleanup_failure_is_visible(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "AWOS Test")
    (repo / "app.py").write_text("value = 1\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "initial")

    manager = GitManager(str(repo))
    manager.create_worktree(tmp_path / "sandbox")
    monkeypatch.setattr(manager, "_git", lambda *args, **kwargs: (False, "forced failure"))

    assert not manager.cleanup_worktree()
    assert manager.status()["cleanup_state"] == "cleanup_failed"


def test_session_sandbox_persists_policy_and_cleanup_identity() -> None:
    sandbox = SessionSandbox(
        enabled=True,
        worktree_path="/tmp/awos-sandbox",
        isolation_level="worktree_only",
        policy_version="sandbox_v1",
        policy_digest="policy-digest",
        cleanup_state="active",
    )
    session = RuntimeSession(session_id="rs_123456789abc", sandbox=sandbox)

    restored = RuntimeSession.from_dict(session.to_dict())

    assert restored.sandbox.to_dict() == sandbox.to_dict()


def test_security_evidence_is_complete_deterministic_and_append_only(tmp_path) -> None:
    evidence = SandboxSecurityEvidence(
        evidence_id="ev-1",
        session_id="rs_123456789abc",
        policy_version="sandbox_v1",
        policy_digest="policy-digest",
        isolation_level="worktree_only",
        action_digest="action-digest",
        approval_status="approved",
        command="pytest",
        arguments=("-q",),
        paths=("tests/test_app.py",),
        environment_identity={"backend": "host-process", "python": "3.10"},
        exit_status=0,
        timed_out=False,
        output_digest="output-digest",
        cleanup_state="cleaned",
        observed_at="2026-07-29T00:00:00Z",
    )
    store = SandboxEvidenceStore(tmp_path / "security.jsonl")

    store.record(evidence)
    store.record(evidence)

    assert evidence.to_json() == SandboxSecurityEvidence.from_dict(evidence.to_dict()).to_json()
    records = store.read()
    assert len(records) == 2
    assert records[0].to_dict() == records[1].to_dict()
    assert "pytest" in records[0].to_dict()["command"]
    assert "stdout" not in records[0].to_dict()


@pytest.mark.parametrize("approval_status", ["unknown", "", "approved\n"])
def test_security_evidence_rejects_unknown_approval(approval_status: str) -> None:
    with pytest.raises(ValueError):
        SandboxSecurityEvidence(
            evidence_id="ev-1",
            session_id="rs_123456789abc",
            policy_version="sandbox_v1",
            policy_digest="policy-digest",
            isolation_level="worktree_only",
            action_digest="action-digest",
            approval_status=approval_status,
            command="pytest",
            cleanup_state="cleaned",
        )


def test_denied_destructive_command_does_not_mutate_host(tmp_path) -> None:
    target = tmp_path / "host-file"

    result = ShellTool()(command=f"touch {target}", cwd=str(tmp_path))

    assert not result.success
    assert not target.exists()


def test_workspace_shell_rejects_out_of_root_working_directory(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()

    result = WorkspaceShellTool(workspace)(command="pwd", cwd=str(outside))

    assert not result.success
    assert "escapes workspace" in result.error


def test_corrupt_security_evidence_fails_closed(tmp_path) -> None:
    path = tmp_path / "security.jsonl"
    path.write_text('{"evidence_id":"incomplete"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid sandbox evidence"):
        SandboxEvidenceStore(path).read()

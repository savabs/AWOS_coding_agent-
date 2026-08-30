---
title: "Spec: Session Resume Lease"
tags:
  - doc/spec
  - phase/1
  - topic/runtime
---

# Spec: Session Resume Lease

## Goal

Make paused-session admission exclusive across concurrent processes.

## Files Affected

- `scaffold/agent/runtime_session.py` — lease helper + guarded `begin_resume()`
- `tests/test_runtime_session.py` — regression tests
- `scripts/demo_session_resume_lease.py` — provider-free live proof
- `docs/session_resume_lease_proof.md` — proof guide

## Implementation Steps

1. Add an exclusive session lease helper. Verify a second holder receives `SessionResumeError`.
2. Guard `begin_resume()` with the lease. Verify only one concurrent transition changes `paused` to `running`.
3. Reclaim leases whose recorded PID is dead. Verify a dead-owner lease does not permanently block resume.
4. Add a provider-free live proof. Verify output contains `LEASE_ACQUIRED`, `CONTENTION_REJECTED`, and `LEASE_RELEASED`.

## Edge Cases

- Malformed lease metadata is treated as contention, not silently reclaimed.
- Contention leaves the session paused and does not rewrite progress.
- Lease cleanup runs after both successful and failed transitions.

## Testing Plan

Run the focused runtime-session tests and the live proof script. No provider or network access is required.

## Live Proof

**Command:** `.venv/bin/python scripts/demo_session_resume_lease.py`

**Success markers:** `LEASE_ACQUIRED`, `CONTENTION_REJECTED`, `LEASE_RELEASED`.

## Related
- [[session_resume_lease]]
- [[session_resume_lease]]
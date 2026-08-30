---
title: "Task: Session Resume Lease"
tags:
  - doc/task
  - phase/1
  - topic/runtime
  - status/done
---

# Task: Session Resume Lease

**Status:** completed

## Goal

Make paused-session resume admission exclusive across concurrent processes so a
second resume attempt cannot race the first and create competing execution
attempts.

## Files Affected

- `scaffold/agent/runtime_session.py` — lease helper + guarded resume transition
- `tests/test_runtime_session.py` — contention + dead-owner regression tests
- `scripts/demo_session_resume_lease.py` — provider-free live proof
- `docs/session_resume_lease_proof.md` — proof guide

## Steps

- [x] 1.1 Add exclusive session lease helper; run focused contention test.
- [x] 1.2 Guard resume transition; run concurrent resume test.
- [x] 1.3 Reclaim dead-owner lease; run stale-owner test.
- [x] 1.4 Add live proof demo; observe `LEASE_ACQUIRED`, `CONTENTION_REJECTED`, `LEASE_RELEASED`.
- [x] Live proof: run `.venv/bin/python scripts/demo_session_resume_lease.py` — watch for `LEASE_ACQUIRED`, `CONTENTION_REJECTED`, `LEASE_RELEASED`.

## Verification

- `.venv/bin/python -m pytest tests/test_runtime_session.py tests/test_virtual_execution_runtime.py -q --timeout=10` — 42 passed
- `.venv/bin/ruff check scaffold/agent/runtime_session.py tests/test_runtime_session.py scripts/demo_session_resume_lease.py` — clean
- Live proof observed: `LEASE_ACQUIRED`, `CONTENTION_REJECTED`, `LEASE_RELEASED`

## Scope Boundary

This protects resume admission only. It does not serialize later session saves
or provide a heartbeat for a long-running process.

## Related
- [[session_resume_lease_spec]]
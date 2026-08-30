---
title: "Research: Session Resume Lease"
tags:
  - doc/research
  - phase/1
  - topic/runtime
---

# Research: Session Resume Lease

**Date:** 2026-08-21

## Current State

- `RuntimeSessionStore.begin_resume()` loads a paused record, changes its status to running, and saves it.
- The session JSON write is atomic, but the load/check/save sequence is not exclusive across processes.
- The runtime-session contract already says a second resume must fail when the session is running.
- Worktree creation is keyed by session-derived feature IDs, so duplicate resume admission can create competing execution attempts.

## Gap

Two processes can both observe `paused` before either persists `running`. Atomic file replacement protects individual writes but does not protect the transition.

## Scope

- Add a filesystem-exclusive, short-lived admission lease around the resume transition.
- Store only non-sensitive process metadata in the lease file.
- Release the lease in a `finally` path after the transition.
- Reclaim a lease only when its recorded process is no longer alive; do not use an age-only timeout.

## Risks

- Crash between lease acquisition and release can leave a lease file; PID probing must avoid treating a live unrelated process as stale.
- Lease acquisition must not alter session state when contention occurs.
- The lease is an admission guard, not a full heartbeat or protection for every later session save.

## Related
- [[session_resume_lease_spec]]
- [[session_resume_lease]]
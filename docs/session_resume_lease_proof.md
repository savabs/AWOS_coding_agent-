# Session Resume Lease Proof

## Command

```bash
.venv/bin/python scripts/demo_session_resume_lease.py
```

## Success Markers

- `LEASE_ACQUIRED`: a competing live owner is represented by the lease file.
- `CONTENTION_REJECTED`: the second resume attempt fails without changing the paused session.
- `LEASE_RELEASED`: after the lease is removed, resume succeeds and the session is running.

## Failure Signals

- Missing markers or a traceback indicates the admission guard is not working.
- `AssertionError: live lease did not reject` means concurrent resume was admitted.

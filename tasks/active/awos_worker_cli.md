# `awos worker` CLI

**Research:** `docs/research/awos_worker_cli.md`  
**Spec:** `docs/specs/awos_worker_cli_spec.md`

**Status:** IN PROGRESS

---

## Steps

- [x] Research doc
- [x] Spec doc
- [x] Task file (this)
- [x] Add `worker` subparser to `awos.py`
- [x] Implement `cmd_worker_start`
- [x] Implement `cmd_worker_status`
- [x] Implement `cmd_worker_resume`
- [x] Implement `cmd_worker_diff`
- [x] Implement `cmd_worker_cancel`
- [x] Smoke test: status + diff commands working on 40 sessions
- [x] Update `README.md` (show `worker` as primary interface)
- [x] Update `docs/product/stage1_subscription_worker_guideline.md`
- [x] Update `awos.py` docstring

**Status:** Phase C complete. Documentation updated. Ready for Phase D (multi-hour real repo proof).

---

## Implementation plan

**1. Add subparser**
```python
# In awos.py main()
worker_parser = subparsers.add_parser("worker", help="Simple coding worker interface")
worker_sub = worker_parser.add_subparsers(dest="worker_command")

start_p = worker_sub.add_parser("start", help="Start work on a goal")
start_p.add_argument("goal", help="What to build/fix")
...
```

**2. Wire commands**
Each command calls existing functions (orchestrator, sessions) with UX sugar.

**3. Test flow**
```bash
awos worker start "add docstring to foo.py"
# Ctrl+C
awos worker status
awos worker resume
awos worker diff
awos worker cancel
```

---

## Notes

- No kernel changes needed — pure wrapper
- Worktree path always printed (builds trust)
- Session ID always visible (needed for resume after Ctrl+C)

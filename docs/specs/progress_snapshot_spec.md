---
title: "Spec: progress_snapshot.py — Honest Progress Measurement"
tags:
  - doc/spec
  - phase/N
  - topic/progress
  - topic/measurement
---

## Goal

One command: `python scripts/progress_snapshot.py` — reads real data from the project (test results, demo freshness, git metadata) and outputs an honest JSON + human-readable report of what's actually working. No task-file `[x]` marks. No self-reporting. Just facts.

---

## Files Affected

| File | Action |
|---|---|
| `scripts/progress_snapshot.py` | **Create** — the tool |
| `scripts/session_warmup.py` | Minor update — add snapshot reference if snapshot exists |
| `memories/repo/project_structure.md` | Update after first run |

---

## Implementation Steps

### 1. Core scanner functions

1.1 `scan_test_suite()` — Run `pytest --collect-only`, count collected tests, run `pytest -q --tb=line`, parse pass/fail/error counts. Handle the case where the suite has collection errors (like our 31 fixture errors).

1.2 `scan_test_coverage()` — For each file in `tasks/active/` and `tasks/complete/`, check if a corresponding `tests/test_<task_name>.py` exists. If yes, run that specific test file and report pass/fail count.

1.3 `scan_demo_scripts()` — List all `scripts/demo_*.sh`. For each: check if file exists, get last modified time, check if a golden output file exists in `docs/` (matching `docs/<name>_proof.md`). Report staleness (modified > 7 days ago = stale).

1.4 `scan_checkpoint_quality()` — Read latest checkpoint from `docs/memory/`. Count `(TODO)` occurrences. Flag if > 0.

1.5 `scan_task_freshness()` — For each file in `tasks/active/`: get git last-modified date. Report age in days.

1.6 `scan_zombie_tasks()` — List files in `tasks/zombie/`. Count them.

1.7 `scan_overall_metrics()` — Read `.awos/reward_store.jsonl` for episode count, `.awos/spans.jsonl` for span count, `.awos/performance.json` if present.

### 2. Report builder

2.1 Assemble all scan results into a `ProgressSnapshot` dataclass.

2.2 `to_json()` — serialize to JSON, write to `.awos/progress_snapshot.json`.

2.3 `to_terminal()` — print a color-coded human-readable report to stdout.

### 3. Entry point

3.1 `main()` — run all scans, print report, write JSON.

### 4. Integration

4.1 Update `scripts/session_warmup.py` to print "Last snapshot: <date>" if `.awos/progress_snapshot.json` exists.

---

## Edge Cases

- Test suite collection fails entirely → report "suite broken" instead of crashing
- No demos directory → report 0 demos
- No `.awos/` directory → skip runtime metrics silently
- No tasks in active/ → report empty
- First run: no golden output to compare → report "no baseline"

---

## Live Proof

```bash
python scripts/progress_snapshot.py
```

Watch for:
- `Tests: X pass, Y fail, Z collection errors`
- `Active tasks: N (X stale)`
- `Demos: N scripts, X stale`
- `Checkpoint quality: X TODO(s) remaining`
- `JSON written to .awos/progress_snapshot.json`

---

## Related
- [[actual_progress_measurement]] — research that informed this spec
- [[quality_gate]] — existing quality gate script (this complements it)
- `scripts/session_warmup.py` — will reference snapshot

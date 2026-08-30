---
title: "Task: Controlled Rerun Executor"
tags:
  - doc/task
  - topic/controlled-rerun-executor
  - status/active
---
> **Content:** [controlled_rerun_executor.html](controlled_rerun_executor.html) — open in browser.
## Steps
- [x] Create the shell-free allowlisted executor.
- [x] Validate workspace/environment/input execution contracts.
- [x] Classify all rerun outcome states.
- [x] Persist evidence, findings, outcomes, and resolution inputs.
- [x] Live proof: `python3 -m pytest tests/test_debug_rerun_executor.py -q` — controlled subprocess rerun appends inspectable evidence, findings, and outcome records.
## Related
- [[controlled_rerun_executor]]
- [[controlled_rerun_executor_spec]]

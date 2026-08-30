---
title: "Spec: Fixed OpenCode Go Agent Path"
tags:
  - doc/spec
  - phase/1
  - topic/provider-routing
---
> **Content:** [fixed_opencode_go_agent_spec.html](fixed_opencode_go_agent_spec.html) — open in browser.

## Goal
Use only OpenCode Go with `deepseek-v4-pro` on the active product path.

## Files allowed to change
- `scaffold/agent/fixed_provider.py`
- `scaffold/agent/goal_clarifier.py`
- `scaffold/agent/cheap_planner.py`
- `scaffold/agent/orchestrator.py`
- `scaffold/agent/worker.py`
- `scaffold/agent/react_worker.py`
- `tests/test_fixed_opencode_go_agent.py`
- `memories/repo/project_structure.md`

## Atomic steps
1. Define the fixed provider/model contract and test it.
2. Route clarification through OpenCode Go and test the model selection.
3. Fix planner model selection and test it offline.
4. Fix auxiliary and worker selection and test alternate credentials cannot change it.
5. Run focused offline verification.

## Related
- [[fixed_opencode_go_agent]]
- [[fixed_opencode_go_agent]]
- [[swebench_lite_dev_v1]]

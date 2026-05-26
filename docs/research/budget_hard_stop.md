# Budget Hard Stop — Research

## Problem
`BudgetLedger.check_budget()` already exists and returns `(allowed, reason)`.
But **nothing calls it** before making an API call. If the budget is exhausted, the
Orchestrator happily keeps firing expensive LLM requests.

## Existing Infrastructure (already built — just needs wiring)

```python
# scaffold/agent/budget_ledger.py — BudgetLedger.check_budget()
def check_budget(self, estimated_cost: float, monthly_budget: float = 20.0) -> tuple[bool, str]:
    # Returns:
    #   (False, "BUDGET EXHAUSTED: ...") at 100%
    #   (False, "BUDGET BLOCK: ...") if call would exceed cap
    #   (True,  "⚠️ BUDGET WARNING: ...") at 90%
    #   (True,  "") otherwise
```

## Where to wire it

1. **`Orchestrator._execute_single_task()`** — before each Worker attempt  
   - `ledger.check_budget(estimated_cost=0.05)` → if not allowed, skip task, mark failed
   - Estimated cost: 0.05 (conservative; actual cost per task varies 0.001–0.10)

2. **`Orchestrator.execute_feature()`** — before the while loop  
   - Check budget before starting. If exhausted, return early with clear error.

3. **`UnifiedAgent._call_deepseek()` / `_call_anthropic()`** — before the API call  
   - These already have `self.budget_ledger`. Wire `check_budget()` before `client.chat.completions.create()`

## Estimated cost per call
- DeepSeek (T2): ~$0.001–0.005 per task
- Claude Sonnet (T4): ~$0.03–0.10 per task
- Use 0.05 as a safe conservative estimate for `check_budget(estimated_cost=0.05)`

## Behaviour when blocked
- Print: `[BUDGET] ⛔ Skipping task {id} — {reason}`
- Mark task as failed in execution_log with reason = budget_reason
- Do NOT raise an exception — return graceful failure dict

## No new dependencies
`BudgetLedger` is already imported and initialized in both Orchestrator and UnifiedAgent.
`get_ledger()` returns the singleton.

---
title: "Sprint 1 Spec — Ship the Working App (Phase 0: Immediate Fixes)"
tags:
  - doc/spec
  - phase/4
  - topic/orchestrator-e2e
  - topic/chat-layer
  - status/active
---

> **Goal:** Make the orchestrator complete a real task end-to-end. The UI works (mostly); the orchestrator plans (with OpenCode Go); task execution dies silently. Ship a working app that a user unfamiliar with the codebase can demo without hitting a bug in 5 minutes.
>
> **Reference:** [[checkpoint_2026-07-24_master_session]] §9.1. This sprint is the four HIGH-priority items from there, plus one regression test.
>
> **Live proof:** A real task sent in the UI goes from "user sends" to "file actually changes" with all 6 sections populating. Screenshot. Checkpoint.

---

## Constraints (read first)

- **No kernel rewrites in this sprint.** The Hermes integration patterns in [[hermes_integration_research]] are for Sprint 2+. This sprint is wiring, not architecture.
- **Every step is independently testable.** If a step description has "and," split it.
- **One file at a time where possible.** Reuse existing tests; don't add new test infrastructure.
- **Server restart is required** for code changes (per [[checkpoint_2026-07-24_master_session]] §7.1.7). Plan for that.

---

## Step 1.1: Wire task execution to OpenCode Go

**Problem:** The orchestrator's planning path uses OpenCode Go (DeepSeek V4 Flash) — the only model with available quota. But the worker still uses the dead-path models (Gemini 2.0-flash, GPT-4o-mini), so tasks fail with 404 mid-execution.

**File:** `scaffold/agent/react_worker.py`

**Current state (line 95-120):** Worker initializes multiple clients in priority order:
```python
self.client = OpenAI(api_key=opencode_key, base_url="https://opencode.ai/zen/go/v1") if opencode_key else (
    OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com") if deepseek_key else None
)
self.anthropic_client = Anthropic(api_key=anthropic_key) if anthropic_key else None
self.openai_client = OpenAI(api_key=openai_key, base_url="https://api.openai.com/v1") if openai_key else None
# ... etc
```

**Change:** OpenCode Go is already first in this list. The bug is downstream — the worker calls `_call_model()` which iterates through clients on error. OpenCode Go is hit first, but when it fails (e.g., model name not in OpenCode Go's catalog), it falls through to the next client, which is dead.

**Sub-step 1.1a — Audit `_call_model` retry path:**
- Read `react_worker.py:228-300` to find the retry/fallback logic
- Identify why OpenCode Go errors cause fallback instead of immediate fail
- **Test:** Add a single log line `print(f"[_call_model] Trying {client_name}")` before each fallback. Run a task. See which clients get tried.

**Sub-step 1.1b — Fix the fallback order:**
- Move OpenCode Go to be the ONLY client in the list (during this sprint's "ship working app" phase)
- Remove the Anthropic / OpenAI / OpenRouter fallbacks from the worker's client list
- Comment them out, don't delete: `# TODO Sprint 2: re-enable when quota restored`
- **Test:** `python -c "from react_worker import ReActWorker; w = ReActWorker(); print('Clients:', list(w._client_names))"`

**Sub-step 1.1c — Verify model name works:**
- OpenCode Go's `deepseek-v4-flash` may be wrong. Test directly:
  ```bash
  python -c "
  from openai import OpenAI
  c = OpenAI(api_key=os.environ['OPENCODE_GO_API_KEY'], base_url='https://opencode.ai/zen/go/v1')
  print(c.models.list())
  "
  ```
- Use the actual model name from the response in `_call_model`

**Exit condition:** 
```bash
$ awos run "echo 'hello' >> /tmp/test.txt"
# File /tmp/test.txt contains "hello"
# orchestrator.log shows ONE client tried (OpenCode Go)
```

---

## Step 1.2: Filter unavailable models

**Problem:** `escalation_engine.py` and `react_worker.py` still have `gemini-2.5-flash`, `gpt-4o-mini`, etc. in their model lists. These return 404 from the provider. Tasks routed to them fail with cryptic errors.

**File:** `scaffold/agent/escalation_engine.py` (lines 140-200 — escalation levels)

**Change:** Add a `MODELS_UNAVAILABLE` set at module top:
```python
# Per checkpoint §9.1: these models are dead. Filter them out.
MODELS_UNAVAILABLE = {
    "gemini-2.0-flash",
    "gemini-2.5-flash",  # returns 404 in our current setup
    "gpt-4o-mini",       # rate-limited
    "claude-haiku-4-5",  # no working API key
}
```

**Sub-step 1.2a — Apply filter to escalation ladder:**
- `escalation_engine.py:_build_ladder()` — filter out unavailable models before building the ladder
- **Test:** `python -c "from escalation_engine import EscalationEngine; e = EscalationEngine(); print([l.model for l in e._ladder])"` — should not contain any of the unavailable names

**Sub-step 1.2b — Apply filter to worker client list:**
- `react_worker.py:95-120` — wrap the client-init in a filter that drops clients whose default model is in `MODELS_UNAVAILABLE`
- **Test:** Same as 1.1b

**Sub-step 1.2c — Add a "list available models" command:**
- `awos.py:build_parser()` — add `awos models` subcommand
- New handler: `cmd_models(args)` — calls the OpenCode Go `/models` endpoint, prints results
- **Test:** `awos models` shows ≥ 1 model from OpenCode Go

**Exit condition:** `awos run "any task"` does not 404 on any model. Orchestrator logs show only working models tried.

---

## Step 1.3: Strip debug prints

**Problem:** `cheap_planner.py` has debug prints left from the OpenCode Go integration work. Per checkpoint §9.1, this is a 5-minute cleanup.

**File:** `scaffold/agent/cheap_planner.py` (the rewrite in [[checkpoint_2026-07-24_master_session]] §4.4 added debug prints)

**Change:** Remove all `print(f"[CheapPlanner] ...")` lines that aren't part of normal error reporting.

**Sub-step 1.3a — Find all debug prints:**
- `grep -n "print(f\\[" scaffold/agent/cheap_planner.py`
- For each: classify as (keep-as-error-info) or (remove)
- **Test:** `grep -c "print(f\\[" scaffold/agent/cheap_planner.py` should drop to 0 (or ≤ 2 for actual error reporting)

**Sub-step 1.3b — Same audit on orchestrator:**
- `grep -rn "print(f\\[" scaffold/agent/orchestrator.py` — should be ≤ 5 legitimate error prints
- Remove any that aren't behind a logger or exception handler
- **Test:** `grep -c "print(f\\[" scaffold/agent/orchestrator.py` should be ≤ 5

**Exit condition:** Production logs (no `-v`) show no `[DEBUG]` or `[CheapPlanner]` lines under normal operation.

---

## Step 1.4: Subprocess crash error handling

**Problem:** When the orchestrator subprocess dies (e.g., OOM, model API down for 5 minutes, syntax error in the worker after a hot patch), the GUI just shows a stalled section. No error toast, no recovery prompt.

**File:** `gui/server.py` (the subprocess runner) + `gui/static/agent.html` (the error display)

**Current state:** `gui/server.py:530-560` runs the orchestrator as a subprocess and streams stdout via SSE. When the subprocess exits non-zero or unexpectedly, the stream just ends.

**Sub-step 1.4a — Capture subprocess exit code:**
- After `subprocess.Popen(...)` returns, store the process handle in a class-level field
- In the SSE stream loop, when stdout closes, check `proc.poll()` 
- If `proc.returncode != 0` and we didn't send a `session_done` event, emit a synthetic error event: `{"event": "error", "message": f"Orchestrator exited with code {proc.returncode}"}`
- **Test:** Kill the orchestrator subprocess mid-task. SSE stream shows the error event. UI shows toast.

**Sub-step 1.4b — Frontend error toast:**
- `gui/static/agent.html` — add a `showErrorToast(message)` function
- Hook it to the SSE `error` event
- **Test:** Open the agent page. Trigger the error. Toast appears with the error message.

**Sub-step 1.4c — Restart capability:**
- `gui/static/agent.html` — add a "Restart session" button in the error toast
- Wire to a new endpoint: `POST /api/restart-session` that kills the subprocess and starts a new one
- **Test:** Click the restart button. New orchestrator starts. Chat still has history.

**Exit condition:** Killing the orchestrator mid-task shows a clear error in the UI within 2 seconds. User can restart without reloading the page.

---

## Step 1.5: E2E regression test

**Problem:** No automated test for the orchestrator end-to-end. Every change risks regressing the planning → execution path.

**File:** New file: `tests/test_orchestrator_e2e.py`

**Test design:**
- Use a temp dir with a trivial Python file
- Mock the OpenCode Go API to return canned responses (planning + 1 tool call + finish)
- Run `Orchestrator.execute_feature("add a function foo() that returns 1 to test.py")`
- Assert: file contains the new function, `rewards.jsonl` has a positive episode, session is in `paused` or `completed` state

**Sub-step 1.5a — Fixture for OpenCode Go response:**
- Hardcode 3 canned responses in a fixture file: `tests/fixtures/e2e_orc_responses.jsonl`
- Responses: `{plan: [...]}` → `{tool_call: edit_file, ...}` → `{finish, summary: "..."}`
- **Test:** `python -c "import json; [print(json.loads(l)['type']) for l in open('tests/fixtures/e2e_orc_responses.jsonl')]"` shows 3 entries

**Sub-step 1.5b — Mock the OpenCode Go client:**
- Monkeypatch `OpenAI` to return the canned responses
- Run orchestrator in a temp worktree
- **Test:** `pytest tests/test_orchestrator_e2e.py -q --timeout=30`

**Sub-step 1.5c — Assert file changed:**
- After orchestrator completes, read the test file in the worktree
- Assert: `"def foo()"` is in the file content
- **Test:** test passes

**Sub-step 1.5d — Assert session state:**
- Read `runtime_session.py:load(latest_session_id)` after orchestrator completes
- Assert: `session.status` is `COMPLETED` or `PAUSED` (not `FAILED`)
- Assert: at least one reward episode recorded
- **Test:** test passes

**Exit condition:** `pytest tests/test_orchestrator_e2e.py -q --timeout=30` passes. Can be re-run on every PR.

---

## Live Proof (end of Sprint 1)

**Command:** Open the agent page in browser, send a real task, watch all 6 sections populate, verify the file changed.

**Steps:**
1. `cd /home/becmachlean/2024/projects/AWOS_coding_agent`
2. Start server: `nohup python gui/server.py --port 8765 > /tmp/awos_server.log 2>&1 &`
3. Open `http://127.0.0.1:8765/agent` in browser (use Playwright MCP)
4. Select model "OpenCode Go (DeepSeek V4 Flash)" from dropdown
5. Send: "Add a function `def hello(): return 'world'` to /tmp/awos_e2e_test.py"
6. Watch sections populate: Model → Plan → Thinking → Tools → Edits → Results
7. Verify: `cat /tmp/awos_e2e_test.py` shows the function

**Success markers:**
- `Model & Routing` section: shows "OpenCode Go / deepseek-v4-flash"
- `Plan & Tasks` section: shows generated plan (not empty)
- `Tool Calls` section: shows the edit_file call
- `File Edits` section: shows the path with "+N lines"
- `Results` section: green ✓ for task complete
- File `/tmp/awos_e2e_test.py` contains `def hello():`
- Server log shows ONE model tried, no 404s
- Final cost displayed

**Failure signals:**
- Any section stays empty → check that step in this spec
- Task fails with 404 → Step 1.2 didn't filter correctly
- Subprocess dies silently → Step 1.4 didn't capture exit code
- No file change → Step 1.1 didn't wire to OpenCode Go correctly

**Screenshot:** Save to `.awos/screenshots/sprint1_e2e_success.png`. Reference in checkpoint.

---

## Definition of Done

- [ ] Step 1.1 — worker uses OpenCode Go only
- [ ] Step 1.2 — `MODELS_UNAVAILABLE` filter in place
- [ ] Step 1.3 — no debug prints in production logs
- [ ] Step 1.4 — subprocess crash shows error toast
- [ ] Step 1.5 — E2E test passes
- [ ] Live proof recorded with screenshot
- [ ] Checkpoint written with command + observed output
- [ ] `memories/repo/project_structure.md` updated with new test count

## Related

- [[hermes_integration_research]] — patterns we'll use in Sprint 2
- [[hermes_integration_spec]] — Sprint 2 + 3 details
- [[sprint1_working_app]] — task checklist
- [[checkpoint_2026-07-24_master_session]] — source of the immediate-fix list

## External

- `scaffold/agent/react_worker.py` — file to modify
- `scaffold/agent/cheap_planner.py` — file to clean
- `scaffold/agent/escalation_engine.py` — file to add filter to
- `gui/server.py` — subprocess runner
- `gui/static/agent.html` — error toast UI

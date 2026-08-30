---
title: "Hermes Integration Spec — Sprints 2 and 3"
tags:
  - doc/spec
  - phase/4
  - topic/hermes-integration
  - status/active
---

> **Goal:** Integrate the Hermes Agent patterns that give us more control, without scope creep. Reference research: [[hermes_integration_research]]. Companion: [[sprint1_working_app_spec]] (Sprint 1, ships first).
>
> **Three sprints, three weeks:** Foundation (Sprint 2) → Pluggable surfaces (Sprint 2 continued) → Speed and scale (Sprint 3).
>
> **Live proof at the end of each sprint:** A real runnable demo + observed markers, not "tests pass."

---

## Sprint 2 — Production-grade infrastructure (week 2)

### Phase 2.1: SQLite + FTS5 session storage

**Reference:** [[hermes_integration_research]] §Pattern 7. Source: `hermes_state.py:1-2500+`.

**Files to create:**
- `scaffold/agent/state_db.py` — `StateDB` class, port of `hermes_state.py:SessionDB`
- `scaffold/agent/state_db_migrations.py` — schema versioning logic

**Files to modify:**
- `scaffold/agent/runtime_session.py` — add `_write_to_state_db()` alongside existing JSON write
- `scaffold/agent/reward_store.py` — same
- `scaffold/agent/error_pattern_store.py` — same
- `scaffold/agent/skill_library.py` — same
- `scaffold/agent/orchestrator.py` — wire state_db initialization
- `memories/repo/project_structure.md` — note the new schema_version

**Atomic steps:**

#### Step 2.1.1: Create `state_db.py` skeleton

- Copy the WAL with NFS/SMB fallback from `hermes_state.py:apply_wal_with_fallback:520-580` verbatim
- Copy the macOS fullfsync handling from `_apply_macos_checkpoint_barrier:340-370` verbatim
- Copy the WAL-reset bug detection from `is_sqlite_wal_reset_vulnerable:395-425` verbatim
- Copy `_db_opens_cleanly` from `hermes_state.py:660-720` — multi-phase health check
- **Test:** `python -c "from state_db import StateDB; db = StateDB(); print(db._conn.execute('PRAGMA journal_mode').fetchone())"` — should print `('wal',)`

#### Step 2.1.2: Schema v1 — sessions + messages

```sql
-- state_db.py:_SCHEMA_V1
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    goal_hash TEXT,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    ended_reason TEXT,
    parent_session_id TEXT,
    plan_json TEXT,
    budget_json TEXT,
    sandbox_json TEXT,
    rewind_count INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_name TEXT,
    tool_calls TEXT,  -- JSON
    tool_result TEXT,  -- JSON
    reasoning TEXT,
    token_count INTEGER,
    timestamp REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
```

- **Test:** `sqlite3 .awos/state.db ".schema"` shows the tables

#### Step 2.1.3: FTS5 with trigram tokenizer

```sql
-- state_db.py:_SCHEMA_V1 (continued)
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    tool_name,
    content=messages,
    content_rowid=id,
    tokenize='trigram'
);
-- Sync triggers (verbatim from hermes_state.py:1100-1150)
CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, tool_name) VALUES (new.id, new.content, new.tool_name);
END;
-- + UPDATE, DELETE triggers
```

- **Test:** `sqlite3 .awos/state.db "INSERT INTO messages (session_id, role, content, timestamp) VALUES ('t', 'user', 'hello world', 1.0); SELECT * FROM messages_fts WHERE messages_fts MATCH 'hello';"` returns the row

#### Step 2.1.4: Migration system

- Copy `_reconcile_columns` from `hermes_state.py` — declarative ALTER TABLE ADD COLUMN
- Version-gated data migrations: list of `(version, callable)` pairs
- Run all on every open (idempotent)
- **Test:** Bump schema_version, add a column to `_SCHEMA_V1` SQL, reopen, old DBs gain the new column

#### Step 2.1.5: Migrate `RuntimeSession` writes

- Add `state_db.write_session(session)` and `state_db.read_session(id)`
- In `runtime_session.py:save()`, call BOTH the JSON write and the state_db write
- Mark old JSON as `legacy: true` in state_db (so we never re-write it)
- **Test:** Create a session, save, check `state.db` has the row, check `rs_<id>.json` is unchanged

#### Step 2.1.6: Read path with fallback

- `runtime_session.py:load(id)` — try state_db first, fall back to JSON
- If JSON has `legacy: true` marker in state_db, never re-write JSON
- **Test:** Open an old session from a pre-migration `.awos/` — works, and a new session opens from state.db

#### Step 2.1.7: Migrate `reward_store.py`

- `RewardStore.add(episode)` — write to state_db, NOT to `reward_store.jsonl`
- `RewardStore.recent(n)` — read from state_db
- New table: `rewards` with columns `(session_id, model, task_type, success, latency_ms, cost_usd, timestamp)`
- **Test:** Run a task. Check `sqlite3 .awos/state.db "SELECT * FROM rewards LIMIT 5"` shows the episode

#### Step 2.1.8: Repair utility

- Copy `repair_state_db_schema:740-1010` — 4-strategy repair (rebuild FTS → REINDEX → dedup → drop+VACUUM)
- Expose as `python -m scaffold.agent.state_db repair`
- **Test:** Manually corrupt the FTS index, run repair, verify it heals

**Phase 2.1 exit condition:**
```bash
# Run a real task
$ awos run "echo test"
# All data is in state.db:
$ sqlite3 .awos/state.db "SELECT COUNT(*) FROM sessions"  # ≥ 1
$ sqlite3 .awos/state.db "SELECT COUNT(*) FROM messages"  # ≥ 10
$ sqlite3 .awos/state.db "SELECT * FROM rewards ORDER BY timestamp DESC LIMIT 1"  # has row
# Search works:
$ sqlite3 .awos/state.db "SELECT snippet(messages_fts, 0, '>>>', '<<<', '...', 32) FROM messages_fts WHERE messages_fts MATCH 'edit_file'"  # returns snippets
```

---

### Phase 2.2: Pluggable provider registry

**Reference:** [[hermes_integration_research]] §Pattern 4. Source: `providers/__init__.py`, `providers/base.py`, `plugins/model-providers/nvidia/__init__.py`.

**Files to create:**
- `scaffold/agent/provider_profile.py` — `ProviderProfile` dataclass (port of `providers/base.py:30-95`)
- `scaffold/agent/providers/__init__.py` — `register_provider()`, `get_provider_profile()`, `list_providers()`
- `scaffold/agent/providers/opencode_go.py` — extract from `escalation_engine.py`
- `scaffold/agent/providers/openrouter.py` — extract from `escalation_engine.py`
- `scaffold/agent/providers/anthropic.py` — extract from `escalation_engine.py`
- `scaffold/agent/providers/openai.py` — extract from `escalation_engine.py`

**Files to modify:**
- `scaffold/agent/escalation_engine.py` — read from `providers` registry instead of hardcoded
- `scaffold/agent/orchestrator.py:225-280` `_cheap_call` — read from `providers` registry
- `gui/static/agent.html:160` — model dropdown reads from `awos models` output

**Atomic steps:**

#### Step 2.2.1: Create `ProviderProfile` dataclass

- Copy `providers/base.py:30-95` — but REMOVE `OMIT_TEMPERATURE` sentinel (we don't need it)
- Add a `provider_id` field (Hermes uses `name` but our codebase uses `id` elsewhere)
- **Test:** `python -c "from provider_profile import ProviderProfile; p = ProviderProfile(name='test', base_url='http://x', env_vars=('X_API_KEY',)); print(p)"`

#### Step 2.2.2: Create `providers/__init__.py` with lazy discovery

- Copy `providers/__init__.py:_discover_providers:139-180` — three sources: bundled, user, legacy
- Per-repo user plugins: `.awos/providers/<name>.py` (Hermes uses `~/.hermes/...`, we use per-repo)
- Bundled path: `scaffold/agent/providers/`
- **Test:** `python -c "from providers import list_providers; print([p.name for p in list_providers()])"` — should list bundled providers

#### Step 2.2.3: Extract each hardcoded provider to its own file

For each of `opencode_go`, `openrouter`, `anthropic`, `openai`:
- Create `scaffold/agent/providers/<name>.py`
- `ProviderProfile(...)` with the right env vars, base URL, fallback models
- `register_provider(...)` at module level
- **Test:** `python -c "from providers import get_provider_profile; p = get_provider_profile('opencode_go'); print(p.base_url, p.env_vars)"`

#### Step 2.2.4: Refactor `escalation_engine.py`

- `_build_ladder()` — iterate `list_providers()`, build escalation levels from profiles
- Remove the hardcoded `EscalationLevel` enum (or keep it as a re-export)
- **Test:** `python -c "from escalation_engine import EscalationEngine; e = EscalationEngine(); print([l.provider_id for l in e._ladder])"` shows the registered providers

#### Step 2.2.5: Refactor `_cheap_call`

- `orchestrator.py:225-280` — try `providers` registry in priority order, fall back to hardcoded list (for now)
- The hardcoded list becomes the "default" provider
- **Test:** Run a task that uses `_cheap_call` — check the log shows which provider was tried

#### Step 2.2.6: `awos models` command

- `awos.py:build_parser()` — add `models` subcommand
- `cmd_models(args)` — calls `list_providers()`, prints name + display_name + base_url + env_vars + models
- **Test:** `awos models` shows the registered providers with their details

#### Step 2.2.7: User plugin override test

- Drop `.awos/providers/test.py` with:
  ```python
  from providers import register_provider
  from provider_profile import ProviderProfile
  register_provider(ProviderProfile(name="test", base_url="http://test"))
  ```
- Restart
- `python -c "from providers import get_provider_profile; p = get_provider_profile('test'); print(p.base_url)"` returns the test URL
- **Test:** Drop the file, restart, verify discovery

**Phase 2.2 exit condition:**
- `awos models` lists 4 bundled providers + any user plugins
- `escalation_engine.py` no longer has hardcoded model lists
- Adding a new provider = drop a file, restart

---

### Phase 2.3: Tool `check_fn` + schema filter

**Reference:** [[hermes_integration_research]] §Pattern 5 + §Pattern 8. Source: `tools/registry.py:170-235, 540-580`.

**Files to create:**
- `scaffold/agent/tool_check.py` — `_check_fn_cached()` + `invalidate_check_fn_cache()`

**Files to modify:**
- `scaffold/agent/coding_tools.py` — add `check_fn` to every `ToolEntry`, apply filter in `build_coding_tool_registry()`

**Atomic steps:**

#### Step 2.3.1: Port `_check_fn_cached`

- Copy `tools/registry.py:170-235` verbatim
- Add `awos/tool_check.py:_check_fn_cache = {}` module-level dict
- Add `invalidate_check_fn_cache()` function
- **Test:** 
  ```python
  from tool_check import _check_fn_cached
  calls = []
  def my_check(): calls.append(1); return True
  _check_fn_cached(my_check); _check_fn_cached(my_check); _check_fn_cached(my_check)
  assert len(calls) == 1  # cached
  ```

#### Step 2.3.2: Add `check_fn` to `ToolEntry`

- `coding_tools.py:ToolEntry` dataclass — add `check_fn: Callable | None = None`
- **Test:** existing tools still work (no `check_fn` set)

#### Step 2.3.3: Add `check_fn` to tools that need it

For each tool that depends on external state:
- `web_search` — check `SERP_API_KEY` env var
- `git_push` — check `git remote -v` returns non-empty
- `web_extract` — check any HTTP-fetch dependency
- `playwright_*` (if present) — check `playwright` Python module installed

- **Test:** With `SERP_API_KEY` unset, schema returned to LLM does NOT include `web_search`

#### Step 2.3.4: Apply filter in `build_coding_tool_registry`

- After building the registry, filter out tools whose `check_fn` returns False
- The model only sees tools that can actually run
- **Test:** 
  ```python
  from coding_tools import build_coding_tool_registry
  registry = build_coding_tool_registry()
  schemas = registry.get_definitions()
  # web_search is NOT in schemas (no API key)
  assert "web_search" not in [s["function"]["name"] for s in schemas]
  ```

#### Step 2.3.5: Cache invalidation hook

- `awos tools enable` command — calls `invalidate_check_fn_cache()` after config change
- (Or just on process restart — Hermes's choice)
- **Test:** Set `SERP_API_KEY`, restart, `web_search` appears in schemas

**Phase 2.3 exit condition:** Model never sees a tool it can't run. Tools with missing dependencies are filtered out of the schema.

---

### Phase 2.4: Hook system

**Reference:** [[hermes_integration_research]] §Pattern 6. Source: `gateway/hooks.py:1-200`.

**Files to create:**
- `scaffold/agent/hooks.py` — `HookRegistry` class

**Files to modify:**
- `scaffold/agent/orchestrator.py:_execute_single_task` — add `hooks.emit()` calls
- `scaffold/agent/react_worker.py` — add `hooks.emit()` for pre/post tool calls
- `scaffold/agent/runtime_session.py` — add `hooks.emit()` for pause/resume

**Atomic steps:**

#### Step 2.4.1: Port `HookRegistry`

- Copy `gateway/hooks.py:HookRegistry:50-200` verbatim
- Change `HOOKS_DIR` from `~/.hermes/hooks/` to `.awos/hooks/`
- Keep wildcard support (`command:*`)
- Keep error isolation (hook errors don't block pipeline)
- **Test:** Drop `.awos/hooks/test/HOOK.yaml + handler.py`, call `hooks.emit("test:event", {})`, handler runs

#### Step 2.4.2: Define AWOS lifecycle events

```python
# hooks.py:AWOS_EVENTS
AWOS_EVENTS = {
    "session:start",     # new orchestrator session
    "session:pause",     # user requested pause
    "session:resume",    # resumed from pause
    "task:start",        # planner generated a task
    "task:complete",     # task finished (success or fail)
    "tool:pre_call",     # before a tool runs
    "tool:post_call",    # after a tool runs
    "verifier:result",   # after verifier runs
    "reward:recorded",   # after compute_reward()
    "checkpoint:saved",  # after runtime_session.save()
}
```

- **Test:** Each event has a documented context dict shape

#### Step 2.4.3: Wire hooks into orchestrator

- `orchestrator.py:_execute_single_task` — add `self._hooks.emit("task:start", {...})` at start, `"task:complete"` at end
- `react_worker.py` — add `"tool:pre_call"` and `"tool:post_call"` around `registry.execute()`
- `runtime_session.py:save()` — add `"checkpoint:saved"` after the save completes
- **Test:** Drop a hook that logs every task. Run a task. Check the log.

#### Step 2.4.4: Document the hook contract

- `docs/hooks.md` — list of events, context dict shape, sync vs async
- Include a worked example: a "log all tool calls to JSONL" hook
- **Test:** A new contributor can write a hook from the docs alone

**Phase 2.4 exit condition:** Drop a hook in `.awos/hooks/audit_log/`, run a task, see the log file populated with structured records.

---

## Sprint 3 — Speed and scale (week 3)

### Phase 3.1: `ContextEngine` ABC + SummarizeEngine impl

**Reference:** [[hermes_integration_research]] §Pattern 1 + §Pattern 2. Source: `agent/context_engine.py:1-600`, `agent/context_compressor.py:1-2200+`.

**Files to create:**
- `scaffold/agent/context_engine.py` — `ContextEngine` ABC
- `scaffold/agent/context_summarizer.py` — `ContextSummarizer` impl (port of `ContextCompressor`)
- `scaffold/agent/context_summarizer_template.md` — default summary template (user-editable)

**Files to modify:**
- `scaffold/agent/react_worker.py` — wire `ContextEngine` into the message loop
- `awos.yaml` (new) — add `context.engine`, `context.threshold_percent`, `context.summary_template`

**Atomic steps:**

#### Step 3.1.1: Port `ContextEngine` ABC

- Copy `agent/context_engine.py:74-100` — class with `name`, abstract methods, optional hooks
- Change default `threshold_percent` to 0.75 (Hermes default) — but configurable per-repository
- Change `protect_last_n` to 6 (Hermes default) — we don't need 20
- **Test:** `python -c "from context_engine import ContextEngine; print(ContextEngine.__abstractmethods__)"` shows the required methods

#### Step 3.1.2: Port `ContextCompressor` 4-phase algorithm

- This is the big one — ~2000 LOC
- Copy the 4 phases from `compressor.py:30-1100`:
  - Phase 1: `prune_tool_results_only()` (no LLM call)
  - Phase 2: `_align_boundary_backward()`, token-budget tail
  - Phase 3: `_generate_summary()` with structured template
  - Phase 4: `_sanitize_tool_pairs()`, assemble
- Copy the edge case handlers (JSON tool args, multimodal, replay fields, etc.)
- **Test:** Run on a synthetic 50-turn session, verify it produces a valid summary

#### Step 3.1.3: Configurable summary template

- `context_summarizer_template.md` — default sections (Goal, Constraints, Progress, Decisions, Files, Next Steps, Critical Context)
- `ContextSummarizer.__init__()` — read template from `awos.yaml` or use default
- **Test:** Change the template in `awos.yaml`, run compression, verify the summary has the new sections

#### Step 3.1.4: Wire into `react_worker.py`

- Replace the `_MAX_OBS_CHARS = 6000` truncation with `ContextEngine.should_compress()` + `compress()` call
- `messages.append(observation)` triggers a check after every Nth turn (Hermes: every turn, but with cooldown to prevent thrash)
- **Test:** A 30-turn session auto-compresses. The summary appears as a single message in the list.

#### Step 3.1.5: Iterative re-compression

- Track `_previous_summary` in `ContextSummarizer` instance state
- On re-compression, pass it to the LLM with "update this" instruction
- **Test:** Compress, then add 5 more turns, compress again — the second summary contains info from the first

**Phase 3.1 exit condition:** A 30-turn session auto-compresses. The summary template is editable. The session stays under the model's context window.

---

### Phase 3.2: Per-task auxiliary routing

**Reference:** [[hermes_integration_research]] §Pattern 4 (ProviderProfile). Source: `agent/auxiliary_client.py` (not yet read in detail — TODO before Sprint 3).

**Atomic steps (placeholder — flesh out when we get to Sprint 3):**

#### Step 3.2.1: `awos.yaml` aux routing config

```yaml
auxiliary:
  compression:
    model: "openai/gpt-4o-mini"  # cheap
  vision:
    model: "anthropic/claude-haiku-4-5"
  skills_hub:
    model: "opencode_go/deepseek-v4-flash"
```

#### Step 3.2.2: AuxClient class

- Port `agent/auxiliary_client.py:call_llm()` with task parameter
- Resolve to a provider profile based on `auxiliary.<task>.model`
- **Test:** Compression uses the configured cheap model, not the main model

#### Step 3.2.3: Wire into `ContextSummarizer`

- `ContextSummarizer._generate_summary()` calls `aux_client.call_llm(task="compression", ...)`
- **Test:** Verify the cheap model is used (check log)

**Phase 3.2 exit condition:** Auxiliary tasks (compression, vision, skills hub) each use the configured provider. Main chat model is reserved for planning.

---

### Phase 3.3: Anthropic prompt caching (only if we have a working Anthropic key)

**Reference:** [[hermes_integration_research]] (referenced in §4 Phase 3.4). Source: `agent/prompt_caching.py` (not yet read in detail — TODO before Sprint 3).

**Files to modify:**
- `scaffold/agent/orchestrator.py:_interruptible_api_call` — add `cache_control` markers for Anthropic calls
- `scaffold/agent/cache_telemetry.py` — track cache hit rate

**Atomic steps (placeholder):**

#### Step 3.3.1: Add `cache_control: ephemeral` markers

- For Anthropic API calls, add `cache_control: {"type": "ephemeral", "ttl": "5m"}` to the system prompt and last 3 messages
- **Test:** Anthropic API response includes `cache_read_input_tokens` in usage

#### Step 3.3.2: Wire cache telemetry

- `cache_telemetry.py` — record `cache_read_tokens`, `cache_write_tokens` per session
- Surface in `awos stats` output
- **Test:** Run a long Anthropic session, verify cache hit rate > 50%

**Phase 3.3 exit condition:** Long Claude sessions show > 50% cache hit rate. Cost per task drops by 30%+ on sessions > 10 turns.

**Skip this phase if no working Anthropic key.**

---

## Critical Path & Dependencies

```
Phase 0 (Sprint 1) ─► Phase 2.1 (SQLite) ─► Phase 2.2 (Providers) ─► Phase 3.1 (Compression)
                              │                    │                       │
                              │                    │                       ├─► Phase 3.2 (Aux)
                              │                    │                       └─► Phase 3.3 (Caching)
                              └─► Phase 2.3 (Tool check_fn) ─► Phase 2.4 (Hooks)
```

- Phase 2.1 (SQLite) is foundational — everything else reads from or writes to it
- Phase 2.2 (Providers) and Phase 2.3 (check_fn) are independent and can be parallel
- Phase 2.4 (Hooks) depends on 2.3 (tools need to be checkable) but can use the same registry
- Phase 3.1 (Compression) depends on 2.1 (needs the messages table)
- Phase 3.2 (Aux) depends on 2.2 (needs the provider registry)
- Phase 3.3 (Caching) is conditional on Anthropic access

## What "Done" Means Per Phase

| Phase | Done = |
|---|---|
| 2.1 (SQLite) | `sqlite3 .awos/state.db "..."` returns real numbers from your runs |
| 2.2 (Providers) | Drop a provider file, restart, it works. `escalation_engine.py` has no hardcoded models. |
| 2.3 (check_fn) | Model never sees a tool it can't run. Schema filter is observable. |
| 2.4 (Hooks) | Drop a hook, run a task, see it fire. Logs prove it. |
| 3.1 (Compression) | 30-turn session auto-compresses. Template is editable. Cost is reduced. |
| 3.2 (Aux) | Compression on Haiku, planning on Sonnet. Configurable per-task. |
| 3.3 (Caching) | > 50% cache hit on long Claude sessions. 30%+ cost drop. |

## Definition of Done (Sprint 2 + 3 combined)

- [ ] All 7 phases have live proof demos
- [ ] `awos doctor` runs queries against state.db and reports session health
- [ ] `awos models` shows registered providers, including user plugins
- [ ] Compression happens automatically on long sessions
- [ ] Tools with missing dependencies don't appear in LLM schema
- [ ] Users can drop hooks in `.awos/hooks/` without forking
- [ ] `memories/repo/project_structure.md` updated with new module count
- [ ] Checkpoint written for each phase

## Related

- [[hermes_integration_research]] — full code pattern reference
- [[sprint1_working_app_spec]] — Sprint 1 (ships first)
- [[sprint1_working_app]] — task checklist for Sprint 1
- [[VISION]] — AWOS identity
- [[AWOS]] — operational protocols

## External

- `scaffold/agent/state_db.py` — new file
- `scaffold/agent/provider_profile.py` — new file
- `scaffold/agent/providers/` — new directory
- `scaffold/agent/tool_check.py` — new file
- `scaffold/agent/hooks.py` — new file
- `scaffold/agent/context_engine.py` — new file
- `scaffold/agent/context_summarizer.py` — new file

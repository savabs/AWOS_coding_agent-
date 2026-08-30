---
title: "Hermes Agent Code Research — What We Adopt, What We Don't"
tags:
  - doc/research
  - phase/4
  - topic/hermes-integration
  - topic/architecture
  - status/active
---

> **Purpose:** Reference doc for the Hermes Agent code patterns we are adopting into AWOS. Each pattern has the exact source file:line so the next session can verify before implementing. The companion spec is [[hermes_integration_spec]] and the immediate-fix spec is [[sprint1_working_app_spec]].

---

## 0. What We Read

Source repo: `github.com/NousResearch/hermes-agent` (commit: main as of 2026-07-24). MIT-licensed. All paths below are repo-relative.

| File | LOC (approx) | Why we read it |
|---|---|---|
| `agent/context_engine.py` | 600 | The ABC every engine implements — extension point contract |
| `agent/context_compressor.py` | 2200+ | Real compression algorithm: 4 phases, 6+ edge cases we need to handle |
| `tools/registry.py` | 600 | Tool registration, AST discovery, `check_fn`, plugin override policy |
| `hermes_cli/runtime_provider.py` | 1100+ | Provider resolution, credential pool, API mode detection |
| `providers/__init__.py` | 180 | Provider registry + plugin discovery |
| `providers/base.py` | 250 | `ProviderProfile` dataclass shape |
| `plugins/model-providers/nvidia/__init__.py` | 30 | Real bundled provider plugin — the pattern in 30 lines |
| `hermes_state.py` | 2500+ | SQLite + FTS5, schema migration, WAL handling, repair |
| `gateway/hooks.py` | 200 | Hook directory + lifecycle event contract |

**What we did NOT read** (explicitly out of scope per the user's sprint scope):
- `run_agent.py` (AIAgent loop) — too specific to Hermes's CLI/gateway split; AWOS's `orchestrator.py` already has its own structure
- `gateway/platforms/*` (20 messaging adapters) — not in our product
- `acp_adapter/*` (IDE integration) — not in this sprint
- `cron/*` — not in this sprint
- `agent/auxiliary_client.py` and `agent/prompt_caching.py` — read only the surface, will revisit in Sprint 3

---

## 1. The 9 Patterns We Adopt

Each pattern: what it is, where it lives in Hermes, what it does for us, how to translate to AWOS.

### Pattern 1: `ContextEngine` ABC (pluggable compression)

**Hermes source:** `agent/context_engine.py:1-600` — `class ContextEngine(ABC)`

**What it gives us:** A documented extension point so compression becomes a config choice (`context.engine: compressor|lcm|noop`) rather than a code change. Default impl is the summarizer; users can drop in a different engine.

**The ABC contract (5 abstract methods, 8 optional hooks):**

```python
# agent/context_engine.py:74-100 (abstract identity + token state)
class ContextEngine(ABC):
    name: str                                          # required
    last_prompt_tokens: int = 0                        # class-level
    threshold_percent: float = 0.75                    # class-level
    protect_first_n: int = 3                           # class-level
    protect_last_n: int = 6                           # class-level

    # abstract (engine.py:159-220)
    def update_from_response(self, usage: Dict) -> None
    def should_compress(self, prompt_tokens: int) -> bool
    def compress(self, messages, current_tokens, focus_topic, force, memory_context) -> List[Dict]

    # optional hooks (engine.py:222-590)
    def prune_tool_results_only(self, messages) -> (messages, n_pruned)
    def select_context(self, request_messages, ...) -> List[Dict] | None
    def on_turn_complete(self, messages, usage, **kwargs) -> None
    def should_compress_preflight(self, messages) -> bool
    def on_session_start(self, session_id, **kwargs) -> None
    def on_session_end(self, session_id, messages) -> None
    def on_session_reset(self) -> None
    def get_tool_schemas(self) -> List[Dict]
    def handle_tool_call(self, name, args, **kwargs) -> str
    def update_model(self, model, context_length, base_url, api_key, provider, api_mode) -> None
```

**Translation to AWOS:**
- New file: `scaffold/agent/context_engine.py` — copy the ABC verbatim
- Default impl: `scaffold/agent/context_summarizer.py` (port of `ContextCompressor` from `agent/context_compressor.py:1-2200`)
- Selection: `awos.yaml` `context.engine` key (not `~/.awos/config.yaml` — AWOS uses repo-local config)
- Wire-in: `react_worker.py:39-43` `StuckDetector` is the *only* place we currently do per-turn observation; `ContextEngine` slot lives here

**Critical edge case (Hermes `engine.py:153-156`):** `last_prompt_tokens` is initialized to `0`, not `None` — the engine clamps it on read so a "compression just ran, awaiting real usage" sentinel never surfaces as negative to the UI.

### Pattern 2: `ContextCompressor` 4-phase algorithm

**Hermes source:** `agent/context_compressor.py:1-2200`

**What it gives us:** A real, tested compression algorithm that handles 6+ edge cases our `_MAX_OBS_CHARS=6000` truncation ignores.

**The 4 phases (compressor.py:35-90 docstring, 200-300 in code):**

| Phase | What it does | Hermes line |
|---|---|---|
| 1. Prune old tool results | Replace tool outputs >200 chars with `[Old tool output cleared...]` placeholder, NO LLM call | `compressor.py:300-400` |
| 2. Determine boundaries | Token-budget-based tail protection (not message count), tool_call/tool_result alignment | `compressor.py:400-600` |
| 3. Generate structured summary | LLM call to aux model with specific template (Goal/Constraints/Progress/Decisions/Files/NextSteps/Critical Context) | `compressor.py:600-900` |
| 4. Assemble + clean orphan tool pairs | Drop orphaned tool_calls/results, build final message list | `compressor.py:900-1100` |

**Summary template (the part the user controls) — `compressor.py:SUMMARY_PREFIX` + structured sections:**
```
## Goal
## Constraints & Preferences
## Progress (### Done / ### In Progress / ### Blocked)
## Key Decisions
## Relevant Files
## Next Steps
## Critical Context
```

**Critical edge cases Hermes handles that `_MAX_OBS_CHARS` does NOT:**

| Edge case | Hermes handling | Source |
|---|---|---|
| Tool call args that are not valid JSON | `_truncate_tool_call_args_json` parses + shrinks + re-serializes; if unparseable, returns original (issue #11762) | `compressor.py:430-475` |
| Multimodal image parts (3 shapes: `image_url`, `input_image`, `image`) | `_strip_images_from_content` replaces with `[Attached image — stripped after compression]` | `compressor.py:540-580` |
| Provider replay fields (`codex_reasoning_items`, `reasoning_content`, `reasoning_details`) | `_REPLAY_BUDGET_KEYS` included in token estimate so they don't blow tail budget | `compressor.py:300-330` |
| Skill content that would be ghost-loaded after compression | `SKILL_PRUNED_MARKER_PREFIX` re-injected if LLM drops it (issue #32106) | `compressor.py:185-260` |
| Summary model has smaller context than main model | `_is_summary_access_or_quota_error` catches context-length error, falls back to "no summary" gracefully (does NOT crash) | `compressor.py:90-120` |
| Iterative re-compression (re-summarizing a session that's been compressed before) | `_previous_summary` passed to LLM with "update this" instruction rather than re-summarize from scratch | `compressor.py:1000-1100` |
| Stale `MEDIA:` directive sneaking into summary | `_MEDIA_DIRECTIVE_RE` strips it before summarizer sees it (issue #14665) | `compressor.py:225-235` |
| Summary prefix versioning | `_HISTORICAL_SUMMARY_PREFIXES` — three frozen copies, newest first, so re-compression can strip old prefixes | `compressor.py:130-180` |
| Small-context floor | Models <512K context get threshold raised to 0.75 to prevent re-compaction thrash | `compressor.py:280-285` |
| Image token flat estimate | 1600 tokens per image (matches Claude Code's `IMAGE_TOKEN_ESTIMATE`) | `compressor.py:265-270` |

**Translation to AWOS:**
- New file: `scaffold/agent/context_summarizer.py` — port `ContextCompressor` verbatim, change imports
- `scaffold/agent/react_worker.py:40` `_MAX_OBS_CHARS = 6000` — keep as cheap Phase 1 only; full compression lives in `context_summarizer.py`
- Summary template: read from `awos.yaml` `context.summary_template` (user-editable)
- Constants from `compressor.py:30-50`: `_SUMMARY_RATIO=0.20`, `_MIN_SUMMARY_TOKENS=2000`, `_SUMMARY_TOKENS_CEILING=10_000`, `_SUMMARY_INPUT_MAX_CHARS=160_000`

**DO NOT copy `SUMMARY_PREFIX` literally** — it's Hermes-specific copy about not resuming stale tasks. We need an AWOS-specific prefix that tells the model: "this is a handoff, trust the latest user message, not the summary."

### Pattern 3: `ToolRegistry` + AST discovery + `check_fn`

**Hermes source:** `tools/registry.py:1-600`

**What it gives us:** Drop a Python file in `tools/`, restart, it's wired. No manual import list. Tools declare their own availability (missing API key, missing binary, etc.) and are filtered from the schema the model sees.

**The 5 critical patterns:**

| Pattern | Hermes line | What it does |
|---|---|---|
| AST-based discovery | `registry.py:39-80` `_module_registers_tools` | Cheap text prefilter (`"registry" in source and "register" in source`) then AST parse looking for top-level `registry.register()` call |
| TTL-cached `check_fn` | `registry.py:170-235` `_check_fn_cached` | 30s TTL + 60s failure grace for transient flakes (Docker busy, probe timeout) |
| Plugin override policy | `registry.py:300-380` `register` and `deregister` | Plugins can override built-in tools only if `allow_tool_override: true` in config; authorization bound to module namespace, not call site |
| Generation counter for cache invalidation | `registry.py:235` `_generation: int` | `get_definitions()` callers can memoize on `(name, generation)` |
| Error wrapping | `registry.py:475-510` `dispatch` | All exceptions become `{"error": "..."}` JSON; sanitized for framing tokens |
| Result normalization | `registry.py:455-475` `_normalize_handler_result` | String OR multimodal envelope (`{"_multimodal": True, "content": [...]}`); anything else becomes a typed error |

**Translation to AWOS:**
- AWOS already has `coding_tools.py:1-150` with `build_coding_tool_registry()` — refactor to use `ToolRegistry` pattern
- `_check_fn_cached` lives in `awos/tool_check.py` (new file)
- `check_fn` examples to add: `read_file` (no check), `git_commit` (check `git` binary), `web_search` (check API key)
- `deregister` is already used by `LiveToolSynthesizer` (`live_tool_synth.py`) — it needs to learn the ownership check pattern

**Critical edge case (`registry.py:210-235`):** When a `check_fn` flakes (e.g., Docker busy), Hermes serves the last-good `True` for 60s but does NOT cache the failure. The next call re-probes. This is the right behavior — we should copy it.

### Pattern 4: Provider plugin discovery

**Hermes source:** `providers/__init__.py:1-180` + `plugins/model-providers/nvidia/__init__.py:1-30` + `providers/base.py:1-250`

**What it gives us:** Drop a directory, restart, provider is wired. No edit to `escalation_engine.py`.

**The plugin shape (NVIDIA example — 30 lines including imports):**

```python
# plugins/model-providers/nvidia/__init__.py
from providers import register_provider
from providers.base import ProviderProfile

nvidia = ProviderProfile(
    name="nvidia",
    aliases=("nvidia-nim",),
    env_vars=("NVIDIA_API_KEY",),
    display_name="NVIDIA NIM",
    description="NVIDIA NIM — accelerated inference",
    signup_url="https://build.nvidia.com/",
    fallback_models=("nvidia/llama-3.1-nemotron-70b-instruct", ...),
    base_url="https://integrate.api.nvidia.com/v1",
    default_max_tokens=16384,
)
register_provider(nvidia)
```

**`ProviderProfile` fields (`base.py:30-95`):** name, api_mode, aliases, display_name, description, signup_url, env_vars, base_url, models_url, auth_type, supports_health_check, supports_vision, supports_vision_tool_messages, fallback_models, hostname, default_headers, fixed_temperature, default_max_tokens, default_aux_model + 5 overridable methods (get_hostname, prepare_messages, build_extra_body, build_api_kwargs_extras, default_vision_model, get_max_tokens, fetch_models).

**Discovery order (`providers/__init__.py:139-180`):**
1. Bundled `<repo>/plugins/model-providers/<name>/` (stable import path `plugins.model_providers.<name>`)
2. User `$HERMES_HOME/plugins/model-providers/<name>/` (overrides bundled, last-writer-wins)
3. Legacy `providers/<name>.py` single files (back-compat)

**Translation to AWOS:**
- New file: `scaffold/agent/provider_profile.py` — copy `ProviderProfile` dataclass, remove `OMIT_TEMPERATURE` sentinel (we don't need it)
- New file: `scaffold/agent/providers/__init__.py` — copy the registry pattern, discover `providers/*.py`
- Move hardcoded providers from `escalation_engine.py:140-200` to `scaffold/agent/providers/{openai,anthropic,opencode_go,openrouter}.py`
- User plugins: `.awos/providers/<name>.py` (per-repo, like `runtime_session.py`)
- Discovery: lazy on first call (matches Hermes's `_discovered` flag pattern, `providers/__init__.py:108-110`)

**Critical edge case (`base.py:25-50`):** `_profile_user_agent()` returns `hermes-cli/<version>` for catalog probes — some providers (OpenCode Zen) sit behind WAFs that 403 default `Python-urllib/<ver>`. We should do the same in our catalog probes.

### Pattern 5: Tool `check_fn` + schema filter

**Hermes source:** `tools/registry.py:540-580` `get_definitions`

**What it gives us:** Model never sees a tool that can't run. No more "model hallucinates `read_file` on a system without read access" failures.

**The pattern (registry.py:543-555):**

```python
def get_definitions(self, tool_names, quiet=False):
    result = []
    check_results: Dict[Callable, bool] = {}  # per-call cache
    entries_by_name = {entry.name: entry for entry in self._snapshot_entries()}
    for name in sorted(tool_names):
        entry = entries_by_name.get(name)
        if not entry: continue
        if entry.check_fn:
            if entry.check_fn not in check_results:
                check_results[entry.check_fn] = _check_fn_cached(entry.check_fn)
            if not check_results[entry.check_fn]:
                if not quiet: logger.debug("Tool %s unavailable (check failed)", name)
                continue
        schema_with_name = {**entry.schema, "name": entry.name}
        result.append({"type": "function", "function": schema_with_name})
    return result
```

**Translation to AWOS:**
- `coding_tools.py:50-90` `build_coding_tool_registry()` — add `check_fn` field to each `ToolEntry`
- `worker.py:_build_tool_schemas()` (wherever it lives) — apply `check_fn` filter before sending to LLM
- Tool entries that need a `check_fn` first: `web_search` (needs `SERP_API_KEY`), `git_push` (needs git remote), `delegate_task` (needs subagent budget)

**Critical edge case (`registry.py:50-55`):** `dynamic_schema_overrides` — per-tool callable that returns runtime dict merged on top of static schema. Hermes uses it for `delegate_task` so the model sees current `max_concurrent_children` / `max_spawn_depth`. We don't need this in Sprint 2 but the hook is in place if we do.

### Pattern 6: Hook system (`gateway/hooks.py`)

**Hermes source:** `gateway/hooks.py:1-200`

**What it gives us:** Users drop a directory in `.awos/hooks/<event>/` and get behavior without forking. `HOOK.yaml` declares the event list, `handler.py` has `def handle(event_type, context)`.

**The 10 lifecycle events (`gateway/hooks.py:21-37`):**

```
gateway:startup    -- Gateway process starts
session:start      -- New session created
session:end        -- Session ends
session:reset      -- /new or /reset
agent:start        -- Agent begins processing
agent:step         -- Each turn in the tool-calling loop
agent:end          -- Agent finishes
command:*          -- Any slash command (wildcard)
```

**Discovery (`hooks.py:70-140`):** Scan `~/.hermes/hooks/<event>/` for `<event>/HOOK.yaml + handler.py`. Load via `importlib.util.spec_from_file_location`. Register handler in `sys.modules` BEFORE `exec_module` (so Pydantic forward refs resolve). Errors caught and logged, never block the main pipeline.

**Wildcard support (`hooks.py:170-185`):** `command:*` matches `command:reset`, `command:resume`, etc. Exact matches fire first, wildcards second.

**Translation to AWOS:**
- New file: `scaffold/agent/hooks.py` — `HookRegistry` class with `discover_and_load()`, `emit()`, `emit_collect()`
- Discovery path: `.awos/hooks/<name>/` (per-repo, not `~/.hermes/hooks/`)
- Events for Sprint 2: `pre_tool_call`, `post_tool_call`, `task_start`, `task_complete`, `session_pause`, `session_resume` (mapped to AWOS's actual control points)
- Wire-in: `orchestrator.py:_execute_single_task` already has GuiEventBus emit points — add `hooks.emit()` calls alongside

**Critical edge case (`hooks.py:178-180`):** `sys.modules` registration BEFORE `exec_module` — required for handlers that use Pydantic / dataclasses / `from __future__ import annotations`. Without it, the first dispatch fails with "TypeAdapter ... is not fully defined."

### Pattern 7: SQLite + FTS5 session storage

**Hermes source:** `hermes_state.py:1-2500+`

**What it gives us:** Sessions become queryable from the shell. FTS5 gives sub-second search across all message text. WAL mode for concurrency. Schema versioning for forward-compat.

**Critical design choices (not in the docs — only visible in code):**

| Choice | Hermes line | Why it matters |
|---|---|---|
| WAL with NFS/SMB fallback | `state.py:apply_wal_with_fallback:520-580` | SQLite WAL fails on NFS; Hermes falls back to `journal_mode=DELETE` with a one-time warning per process |
| macOS fullfsync at checkpoint | `state.py:_apply_macos_checkpoint_barrier:340-370` | macOS `fsync()` is a lie; need `F_FULLFSYNC` at WAL checkpoint boundaries only |
| WAL-reset bug detection | `state.py:is_sqlite_wal_reset_vulnerable:395-425` | SQLite < 3.51.3 has WAL corruption race; refuse to enable WAL on vulnerable builds |
| Per-call write probe | `state.py:_db_opens_cleanly:660-720` | `PRAGMA journal_mode` (read probe) + `PRAGMA integrity_check` + `INSERT … ROLLBACK` (write probe) catches FTS corruption that base-table reads miss (#50502) |
| Repair strategies | `state.py:repair_state_db_schema:740-1010` | 4 strategies escalating: rebuild FTS → REINDEX → dedup sqlite_master → drop FTS+VACUUM |
| `_last_init_error` capture | `state.py:_set_last_init_error:430-455` | Surface real failure cause in `/resume` error message instead of "DB unavailable" |
| FTS5 with trigram tokenizer | `state.py:SCHEMA_SQL:1015-1100` | CJK search needs trigram; standard tokenizer is whitespace |
| Per-message metadata columns | `state.py:SCHEMA_SQL:1015-1100` | `tool_call_id`, `tool_calls`, `tool_name`, `reasoning`, `reasoning_content`, `reasoning_details`, `codex_reasoning_items`, `codex_message_items`, `effect_disposition`, `observed`, `active`, `compacted` |
| Cascade-delete via marker | `state.py:_delete_delegate_children:90-130` | `_delegate_from` JSON marker in `model_config` — only marked children cascade; orphan contract preserved |

**Translation to AWOS:**
- New file: `scaffold/agent/state_db.py` — port `SessionDB` class from `hermes_state.py:1130-1700`
- Schema version: start at 1
- Tables: `schema_version`, `sessions`, `messages`, `rewards`, `error_patterns`, `skills` (port each `.awos/` JSONL/JSON file to a table)
- Migration: `state_db.py:_reconcile_columns()` — declarative ALTER TABLE ADD COLUMN for additive changes
- WAL with NFS fallback: copy `apply_wal_with_fallback` verbatim
- macOS fullfsync: copy `_apply_macos_checkpoint_barrier` verbatim
- Repair: copy `repair_state_db_schema` 4-strategy pattern (we'll only need strategies 1+2 in practice)

**CRITICAL: this is a 1-week job, not a 1-day job.** Plan for 5 days with checkpoint at day 2 (schema migration works on synthetic data). Do not start this without a way to roll back — keep the old JSON files in `.awos/sessions/` for one full release cycle.

### Pattern 8: `check_fn` availability cache

**Hermes source:** `tools/registry.py:170-235` `_check_fn_cached`

**What it gives us:** Tools that probe external state (Docker daemon, Modal SDK, Playwright binary) don't re-probe on every call. Result cached 30s. **Plus:** transient failures within 60s of a success are suppressed (last-good `True` served) but not cached — the next call re-probes.

**The pattern (registry.py:180-235):**

```python
_CHECK_FN_TTL_SECONDS = 30.0
_CHECK_FN_FAILURE_GRACE_SECONDS = 60.0
_check_fn_cache: Dict[Callable, tuple[float, bool]] = {}
_check_fn_last_good: Dict[Callable, float] = {}

def _check_fn_cached(fn):
    now = time.monotonic()
    with _check_fn_cache_lock:
        cached = _check_fn_cache.get(fn)
        if cached and (now - cached[0]) < _CHECK_FN_TTL_SECONDS:
            return cached[1]
    try: value = bool(fn())
    except Exception: value = False
    with _check_fn_cache_lock:
        if value:
            _check_fn_last_good[fn] = now
            _check_fn_cache[fn] = (now, True)
            return True
        last_good = _check_fn_last_good.get(fn)
        if last_good and (now - last_good) < _CHECK_FN_FAILURE_GRACE_SECONDS:
            logger.warning("check_fn %s flaked; serving last-good True", fn)
            return True  # NOT cached — next call re-probes
        _check_fn_cache[fn] = (now, False)
        return False
```

**Translation to AWOS:**
- New file: `scaffold/agent/tool_check.py` — `_check_fn_cached` + cache
- Use in: `coding_tools.py:_build_coding_tool_registry` (apply to every `ToolEntry.check_fn`)
- `invalidate_check_fn_cache()` — call after `awos tools enable` or config change

**Critical edge case (registry.py:227-235):** A failure that persists past the grace window IS honored and IS cached. So a backend that really went down stops advertising its tools within 60s. A flaky backend stays available.

### Pattern 9: Iterative re-compression + memory flush

**Hermes source:** `agent/context_compressor.py:1000-1100` + `agent/memory_manager.py` (referenced)

**What it gives us:** Re-compression preserves information across compressions. Memory is flushed BEFORE compression, so no data is lost.

**The pattern (compressor.py:1000-1050):**

```python
def compress(self, messages, ...):
    # 1. Flush memory to disk first
    if self._memory_provider:
        self._memory_provider.flush()
    # 2. Compress with previous summary as context
    if self._previous_summary:
        prompt = f"Update this summary based on the new conversation:\n\n{self._previous_summary}\n\nNEW TURNS:\n{messages_to_summarize}"
    else:
        prompt = "Summarize this conversation:\n{messages_to_summarize}"
    summary = self._aux_call(prompt)
    self._previous_summary = summary
    # 3. Assemble new message list
    return [head_messages, summary_message, tail_messages]
```

**Translation to AWOS:**
- `react_worker.py` does NOT currently flush anything before adding the next turn
- Add `_flush_thoughts_to_memory()` (new method) called before each `messages.append(tool_result)`
- Track `_previous_summary` in `context_summarizer.py` instance state
- `on_session_end` hook: full memory flush

**Critical edge case:** `previous_summary` is **engine state**, not session state. If the engine instance is recreated mid-session (e.g., after `/model` switch), the previous summary is lost. Hermes handles this by passing the previous summary through the call: `compress(messages, current_tokens, ..., _previous_summary=last_seen)`. We should do the same.

---

## 2. The 7 Patterns We DON'T Adopt (and Why)

| Pattern | Hermes source | Why we don't adopt |
|---|---|---|
| 20+ messaging platform adapters | `gateway/platforms/*` | Out of scope — single app, no Telegram/Discord/Slack |
| Cron subsystem | `cron/jobs.py`, `cron/scheduler.py` | No use case in this product |
| Multi-profile isolation (`hermes -p <name>`) | `hermes_constants.py:get_hermes_home` | Per-repo `.awos/` is enough; one profile per working dir |
| ACP for IDE integration | `acp_adapter/*` | Single web app is the surface; revisit if/when IDE is needed |
| Trajectory format (ShareGPT export) | `hermes_cli/trajectory.py` | Explicitly deferred per VISION.md |
| 3 API modes (chat_completions, codex_responses, anthropic_messages) | `runtime_provider.py:130-180` | All our providers are OpenAI-compatible; revisit when we add native Anthropic |
| `SOUL.md` identity | `agent/prompt_builder.py:load_soul_md` | VISION.md is canonical; per-user identity is not in scope |

---

## 3. Critical Edge Cases Hermes Handles That We MUST Also Handle

If we implement any compression, hook system, or SQLite migration, these are the gotchas. Listed by file we'll touch.

### 3.1 Compression (Sprint 3)

| Gotcha | Source | What happens if we ignore it |
|---|---|---|
| JSON tool args get truncated mid-string | `compressor.py:430-475` | MiniMax rejects with "invalid function arguments json string"; session stuck in loop |
| Image parts in 3 shapes (`image_url`, `input_image`, `image`) | `compressor.py:540-580` | Multimodal turns blow the token budget — we don't have vision yet but copy the check |
| Provider replay fields (`codex_reasoning_items` etc.) | `compressor.py:300-330` | Hidden blobs dominate session size; tail budget undercounts |
| Skill ghost-load (model thinks skill loaded but instructions gone) | `compressor.py:185-260` | Model attempts skill use, fails, retries — visible as "stuck" |
| Summary LLM has smaller context than main LLM | `compressor.py:90-120` | API returns context-length error; compression must not crash |
| Small-context floor for <512K models | `compressor.py:280-285` | Without it, 128K-262K models re-compact every 1-2 turns |
| `MEDIA:` directive in summary | `compressor.py:225-235` | Re-emitted as active directive on next turn (issue #14665) |

### 3.2 SQLite + FTS5 (Sprint 2)

| Gotcha | Source | What happens if we ignore it |
|---|---|---|
| WAL fails on NFS | `state.py:520-580` | Every state.db operation breaks on NFS-mounted `~/.awos/` |
| macOS `fsync()` doesn't flush | `state.py:340-370` | SIGTERM during launchd shutdown corrupts state.db |
| WAL-reset race in SQLite < 3.51.3 | `state.py:395-425` | Multi-process WAL corruption |
| FTS write corruption invisible to reads | `state.py:_db_opens_cleanly:660-720` | `/resume` reports "no sessions" while DB looks fine |
| Schema migration without version table | (we need to add) | Old `.awos/sessions/rs_*.json` files break silently when format changes |
| Foreign key cascade | `state.py:_delete_delegate_children:90-130` | Deleting a session also deletes sub-sessions — must be marker-based, not blanket |

### 3.3 Hook system (Sprint 2)

| Gotcha | Source | What happens if we ignore it |
|---|---|---|
| `sys.modules` registration before `exec_module` | `hooks.py:120-130` | Pydantic/dataclass handlers fail at first dispatch |
| Wildcard match order (exact first, wildcard second) | `hooks.py:170-185` | `command:*` could shadow a specific `command:reset` if order is wrong |
| Hook errors must not block pipeline | `hooks.py:200-220` | One bad user hook = total system freeze |
| Hook discovery path is per-deployment | (we need to decide) | Per-repo `.awos/hooks/` vs system `~/.awos/hooks/` — choose per-repo for AWOS |

### 3.4 Tool registry (Sprint 2)

| Gotcha | Source | What happens if we ignore it |
|---|---|---|
| `check_fn` flake handling | `registry.py:210-235` | Docker busy → all terminal tools vanish mid-session |
| Plugin override requires `allow_tool_override: true` | `registry.py:300-380` | Plugin can silently replace built-in `read_file` |
| `deregister` has same auth check | `registry.py:380-440` | Plugin can `deregister` then `register` to bypass the override check |
| Generation counter for cache invalidation | `registry.py:235` | Stale `get_definitions` cache after `deregister` mid-session |

---

## 4. Translation Table (Hermes concept → AWOS module)

| Hermes concept | Hermes file | AWOS current | AWOS target |
|---|---|---|---|
| AIAgent loop | `run_agent.py` | `orchestrator.py` (2157 LOC) | No port — keep our own |
| `AIAgent.chat()` / `run_conversation()` | `run_agent.py:117-180` | `orchestrator.execute_feature()` | Keep — already different shape |
| ReAct worker | (internal) | `react_worker.py` | Keep — already equivalent |
| `ContextEngine` ABC | `agent/context_engine.py` | none | `scaffold/agent/context_engine.py` (new) |
| `ContextCompressor` | `agent/context_compressor.py` | `_MAX_OBS_CHARS=6000` truncation | `scaffold/agent/context_summarizer.py` (new) |
| `ToolRegistry` + AST discovery | `tools/registry.py` | `coding_tools.py:1-150` | Refactor to use `ToolRegistry` pattern |
| `check_fn` | `tools/registry.py:170-235` | none | Add to every `ToolEntry` |
| Provider plugin discovery | `providers/__init__.py` | `escalation_engine.py:140-200` (hardcoded) | `scaffold/agent/providers/*.py` (per-provider files) |
| `ProviderProfile` | `providers/base.py` | none | `scaffold/agent/provider_profile.py` (new) |
| Hook system | `gateway/hooks.py` | `GuiEventBus` (in-process events only) | `scaffold/agent/hooks.py` (new, user-extensible) |
| SQLite + FTS5 | `hermes_state.py` | `runtime_session.py` (JSON files) | `scaffold/agent/state_db.py` (new) |
| `check_fn` cache | `tools/registry.py:170-235` | none | `scaffold/agent/tool_check.py` (new) |
| Schema versioning | `hermes_state.py:1000+` | none | `state_db.py` (new) |
| Per-message metadata columns | `hermes_state.py:SCHEMA_SQL` | `messages` in `react_worker.py` (in-memory) | `state_db.py:messages` table |
| `_dispatch_user_turn` | (multiple) | `_execute_single_task` in `orchestrator.py` | Add hook emit points alongside |
| 20 platform adapters | `gateway/platforms/*` | none | Not in scope |
| Cron | `cron/*` | none | Not in scope |
| ACP | `acp_adapter/*` | none | Not in scope |
| Trajectory | `hermes_cli/trajectory.py` | none | Not in scope (deferred per VISION) |
| `SOUL.md` | `agent/prompt_builder.py` | none | Not in scope (VISION.md is identity) |

---

## 5. Open Questions

1. **Schema version starting point.** Hermes started at 1 in 2024, now at 23. We start at 1, but should we add a "v0" → "v1" migration that reads the old `.awos/sessions/rs_*.json` files and imports them? OR do we start fresh and let old JSONs be read-only legacy? **My recommendation: import on first open, mark them as `legacy: true`, no further writes to JSON.**

2. **Hook discovery path.** Per-repo (`.awos/hooks/`) or per-system (`~/.awos/hooks/`)? Hermes uses per-system (multi-repo portability). For AWOS, per-repo matches `.awos/` convention. **Recommendation: per-repo for Sprint 2; revisit if users ask for sharing.**

3. **Provider plugin override policy.** Do we need Hermes's `allow_tool_override: true` opt-in for plugins, or is AWOS's kernel small enough that we can be more permissive? **Recommendation: copy the policy verbatim — the cost is low (one config flag) and the protection is real.**

4. **Compression summary template as file.** Hermes hardcodes the template. We make it `awos.yaml` `context.summary_template`. **Recommendation: ship a sensible default in `scaffold/agent/context_summarizer.py:DEFAULT_TEMPLATE` and let user override via config.** The default should be a string the user can `cat` and edit.

5. **Image / multimodal handling.** AWOS doesn't do vision yet. Do we copy the `image_url/input_image/image` handling now (in case we add it later) or skip it? **Recommendation: skip for Sprint 3 — copy the dispatch logic but no actual image processing.** Saves ~80 LOC.

---

## Related

- [[sprint1_working_app_spec]] — Phase 0 (immediate fixes) — ships first
- [[hermes_integration_spec]] — Sprint 2 + 3 detailed steps
- [[sprint1_working_app]] — task checklist for Phase 0
- [[VISION]] — AWOS identity
- [[AWOS]] — operational protocols

## External

- Hermes Agent repo: `github.com/NousResearch/hermes-agent` (MIT, 2026)
- SQLite WAL docs: `https://www.sqlite.org/wal.html`
- FTS5 docs: `https://www.sqlite.org/fts5.html`
- macOS `fsync(2)` man page: documents the fullfsync requirement

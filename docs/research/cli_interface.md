# CLI Interface Research

## Existing Patterns

- `awos.py` at repo root: currently a thin stub, imports `UnifiedAgent`
- `scaffold/agent/unified_agent.py`: has an interactive `chat()` method with commands:
  - `budget`, `recall`, `debug`, `help`, `exit`
- `scaffold/agent/memory/vector_memory.py`: `retrieve(query, n_results)`
- `scaffold/agent/core/reasoning.py`: `ReasoningTraceStore.list_sessions()`, `.load()`
- `scaffold/agent/budget_ledger.py`: `get_ledger().get_status()`
- `scaffold/agent/memory/codebase_index.py`: `index_project()`

## Design Decision

Use `argparse` (stdlib) — no external dependencies.
Structure: `awos.py` is the single entry point with sub-parsers.
If it grows beyond ~300 lines, split into `scaffold/agent/cli/commands.py`.

## Commands Mapping

| CLI Command | Component Method |
|-------------|------------------|
| `awos chat` | `UnifiedAgent().chat()` |
| `awos memory search <q>` | `VectorMemory.retrieve(q)` |
| `awos memory stats` | `VectorMemory.stats()` |
| `awos traces list` | `ReasoningTraceStore.list_sessions()` |
| `awos traces show <id>` | `ReasoningTraceStore.load(id)` |
| `awos traces clean [--days]` | glob + remove old `.awos/traces/*.json` |
| `awos budget` | `BudgetLedger.get_status()` |
| `awos index` | `CodebaseIndex.index_project()` |
| `awos run <goal>` | `Orchestrator.execute_feature(goal)` |

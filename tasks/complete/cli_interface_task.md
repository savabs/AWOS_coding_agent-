# CLI Interface Task

## Status: COMPLETE — 2026-05-17

## Objective
Build a standalone `awos` CLI command that exposes all agent capabilities:
- `awos chat` — interactive session (current UnifiedAgent flow)
- `awos memory search <query>` — vector memory recall
- `awos memory stats` — memory usage summary
- `awos traces list` — list reasoning trace sessions
- `awos traces show <session_id>` — display trace chain
- `awos traces clean` — cleanup old traces
- `awos budget` — month-to-date budget status
- `awos index` — (re)build codebase semantic index
- `awos run <goal>` — one-shot feature execution via Orchestrator

## Scope
- Single entry point: `awos.py` at repo root
- `argparse` for subcommands (stdlib, no deps)
- Each subcommand delegates to existing components
- Default: `awos` (no args) → interactive chat

## Acceptance Criteria
- [x] `python awos.py --help` shows all subcommands
- [x] `awos memory search "logging"` returns past interactions
- [x] `awos traces list` shows session IDs and goal summaries
- [x] `awos budget` shows visual budget monitor
- [x] `awos run "add user auth"` executes via Orchestrator
- [x] `awos chat` starts interactive session with all Phase 1 features active

## Files
- `awos.py` — main CLI entry point (282 lines)
- `test_cli.py` — 19-assertion smoke test

## Test Results
- `test_cli.py`: 19/19 PASS

## Time Estimate
~2 hours (actual)

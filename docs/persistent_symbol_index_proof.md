# Persistent Symbol Index Proof

## Command

```bash
python3 scripts/demo_persistent_symbol_index.py
```

## Success Markers

- `[PROOF] index built; cache file: True`
- `[PROOF] loaded from cache: N files`
- `[PROOF] stale 'pkg/greeter.py' resolved on disk → [...]`
- `[PROOF] symbol_map lines: N`
- `[PROOF] read windows: [{...}]`
- `[PROOF] PASS — persistent + disk-grounded symbol retrieval`

## What It Demonstrates

1. **Persistence**: index built once and snapshot written to `<root>/.awos/symbol_index.json`.
2. **Staleness**: file moved on disk; stale index hint `pkg/greeter.py` still resolves to the real `pkg/moved.py` (disk-first, never hides a file).
3. **Hierarchical retrieval**: ReAct context gains a bounded `symbol_map` (level-1) + `read_windows` with exact `start`/`end` lines (level-2 on-demand reads). Every read window's file is verified real on disk.

## Failure Signals

- Demo crashes or raises `ValueError`.
- A read window points to a file that does not exist on disk.
- Stale hint produces no read windows despite a real file existing.

## Automated Coverage

- `tests/test_symbol_index_persistence.py` — persistence round-trip, mtime-based incremental rebuild, deleted-file drop, disk-first grounding, stale-entry drop, repo-map integration.
- `tests/test_resolve_file.py` — disk-first resolver ladder.
- Full command: `pytest -q --timeout=10`
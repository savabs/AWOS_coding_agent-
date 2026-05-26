# CLI Interface Spec

## Interface

```
awos [-h] <command> [args...]

Commands:
  chat              Interactive session with UnifiedAgent
  memory search     Search past interactions via vector memory
  memory stats      Show memory usage statistics
  traces list       List reasoning trace sessions
  traces show       Display a specific trace session
  traces clean      Remove traces older than N days
  budget            Show month-to-date budget status
  index             (Re)build semantic codebase index
  run               Execute a feature goal via Orchestrator
```

## Error Handling

- Missing subcommand → show help
- Component not initialized → auto-initialize on first use
- No API keys for `run` → print clear error with setup instructions

## Output Format

- `memory search`: plain text list with similarity scores
- `traces list`: tabular (session_id, goal, trace_count, timestamp)
- `traces show`: formatted Thought→Action→Observation chain
- `budget`: visual progress bar + numbers

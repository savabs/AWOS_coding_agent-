# Token and Context Observability Proof

## Command

```bash
python3 scripts/demo_token_context_observability.py
```

## Success Markers

- `[TOKEN-PROOF] persisted model call`
- `[TOKEN-PROOF] aggregate:`
- Aggregate JSON contains `"calls": 1` and `"total_tokens": 150`.

## Failure Signals

- The command cannot import the telemetry module.
- The CLI emits invalid JSON.
- The aggregate reports zero calls or an incorrect total.

The demo uses a temporary store and does not contact a provider or modify the repository runtime state. The CLI command is implemented, but this environment's available Python 3.9 interpreter cannot import the repository's Python 3.10+ `awos.py` entrypoint.

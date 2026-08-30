---
name: awos-live-proof
description: Creates AWOS live proof artifacts (demo script + proof guide) for kernel, CLI, and safety features. Use when implementing orchestrator, sessions, CLI commands, or when the user mentions live proof, demo scripts, or proof markers.
---

# AWOS Live Proof

Mandatory for kernel/CLI/safety features per `protocols/LIVE_PROOF_PROTOCOL.md`.

## Required artifacts

| File | Purpose |
|------|---------|
| `scripts/demo_<feature>.sh` | Runnable one-command demo |
| `docs/<feature>_proof.md` | Success markers + command |

## Demo script template

```bash
#!/usr/bin/env bash
set -euo pipefail
# ... run feature ...
echo "PROOF_MARKER: <unique string>"
```

## Proof guide must include

- Copy-paste command
- 2–5 success markers (strings to grep)
- Failure signals

## Definition of done

- [ ] `pytest` green for code paths
- [ ] Demo run with markers observed
- [ ] Task step `[x]` only after live observation

Reference: `scripts/demo_stagnation_breaker.sh` + `docs/stagnation_breaker_proof.md`

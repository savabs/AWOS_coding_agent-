# AWOS Runtime State (`.awos/`)

Persistent memory, rewards, and execution traces for the AWOS kernel.

**Identity:** see `../VISION.md` v2 only.

## Key paths

| Path | Role (VISION layer) |
|---|---|
| `reward_store.jsonl` | Layer 4 — reward outcomes |
| `error_patterns.jsonl` | Layer 3 — reflection / lessons |
| `skills/` | Layer 7 — learned skills |
| `evolved_prompt.json` | Layer 10 — self-modification (prompts) |
| `tools/` | Layer 6 — synthesized tools |
| `memory/` | Layer 1 — vector memory |
| `spans.jsonl` | Layer 0 — execution telemetry |
| `goals/` + `state/` | Layer 2 — planning / multi-session |
| `budget.json` | Layer 0 — resource limits |

## Usage

1. Runtime files are created automatically by `awos run`, orchestrator, and learning components.
2. Agents read `.awos/` for compiled policy — not as a substitute for `VISION.md`.
3. Per-deployment state compounds over time (Layer 12 — continuous learning).

## Handoff

- `../memories/repo/project_structure.md` — canonical metrics and phase
- `../docs/memory/checkpoint_*.md` — latest session checkpoint

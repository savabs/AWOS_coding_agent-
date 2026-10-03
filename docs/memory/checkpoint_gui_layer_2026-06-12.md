---
title: "Checkpoint: GUI Layer — 2026-06-12"
tags:
  - doc/checkpoint
  - feature/gui
  - phase/gui_v0
date: 2026-06-12
immutable: true
---

# Session Checkpoint — GUI Layer (2026-06-12)

> **Immutable record of the GUI design conversation and v0 implementation.**
> **Canonical technical spec (do not duplicate):** `docs/specs/gui_layer_spec.md`

---

## One-line status

**AWOS GUI v0 shipped:** structured diffs, file-change manifest, backend-only telemetry, read-only Run Viewer at `:8765`. Live streaming and chat panel not yet built.

---

## Conversation arc (what the user asked for)

### Message 1 — Memory systems overview
User asked to understand AWOS memory (session memory, checkpoints, SOUL, goals). No GUI work yet.

### Message 2 — GUI motivation
> "I am thinking of making a gui for working with our agents, cause this cant be just a cli based stuff only"

**Conclusion:** CLI-only limits adoption. Existing assets: `LiveRenderer`, PEI HTML report, MCP server, `.awos/` state files. No integrated web GUI. Do not use `src/` (Claude Code).

**Phased plan agreed:**
- Phase 0: Read-only dashboard
- Phase 1: Chat over HTTP
- Phase 2: Goal runner with live events
- Refactor `LiveRenderer` to emit structured events

### Message 3 — Three hard requirements
> "diff should be seen properly"
> "files changed and create all those stuff to be seen properly"
> "certain important stats to be logged in the backend though not seen on the screen, cause we need it cause we are improving the agent constantly"

**These became the three pillars of GUI v0:**

| # | Requirement | Implementation |
|---|---|---|
| 1 | Proper diffs | `diff_builder.py` → full hunks + line numbers in manifest; Run Viewer renders green/red |
| 2 | Files changed/created | `manifest.json` with `status` badges + file list panel |
| 3 | Backend stats (not on screen) | `telemetry_log.py` → `.awos/telemetry.jsonl` |

### Message 4 — This checkpoint
> "create a perfect memory for specially this gui based stuff"

**Written to:** `docs/specs/gui_layer_spec.md` (canonical) + this file (immutable session record).

---

## What was built (2026-06-12)

### New files

```
scaffold/agent/diff_builder.py      # Structured FileChange, hunks, line numbers
scaffold/agent/gui_events.py        # GuiEventBus, manifest + events.jsonl
scaffold/agent/telemetry_log.py     # TaskTelemetry, SessionTelemetry, JSONL
gui/server.py                       # Stdlib HTTP API + static server
gui/static/index.html               # Run Viewer layout
gui/static/app.js                   # Diff rendering, session picker
gui/static/style.css                # Dark theme diff colors
tests/test_diff_builder.py          # 3 tests passing
```

### Modified files

```
scaffold/agent/live_renderer.py     # event_bus param, _emit() on all methods
scaffold/agent/run_diagnostics.py   # print_visual_diff returns dict
scaffold/agent/orchestrator.py      # GuiEventBus + TelemetryLogger per run
```

### Runtime artifacts (example)

```
.awos/gui/<session_id>/manifest.json
.awos/gui/<session_id>/events.jsonl
.awos/telemetry.jsonl
```

---

## Key architectural decisions

1. **Dual output** — Terminal unchanged; GUI gets parallel JSON events.
2. **Two logs** — `events.jsonl` (UI) vs `telemetry.jsonl` (improvement) — never merged.
3. **Stdlib server** — Respects GTM "no FastAPI yet"; `python3 gui/server.py` on port 8765.
4. **Session ID = orchestrator ID** — `orch_<8 hex chars>` from `ReasoningSession`.
5. **Diff at verify** — Same hook as `print_visual_diff`; terminal still shows 16-line preview, manifest gets full diff.

---

## How to use (quick reference)

```bash
# 1. Run agent (produces GUI data)
python3 awos.py run "In scaffold/agent/<file>.py <goal>"

# 2. Open viewer
python3 gui/server.py
# → http://127.0.0.1:8765

# 3. Analyze improvement stats (not in GUI)
cat .awos/telemetry.jsonl | tail -5
```

---

## Not done yet (next agent priorities)

1. **SSE live stream** during active run (Phase 1)
2. **POST /api/run** to trigger from browser (Phase 2)
3. **Chat panel** via UnifiedAgent (Phase 2)
4. **Print session_id** in `awos run` final summary for easy GUI lookup
5. **Internal telemetry dashboard** — separate from user-facing Run Viewer (Phase 3)

---

## Links

| Doc | Purpose |
|---|---|
| `docs/specs/gui_layer_spec.md` | **Canonical** — schemas, API, file map, phases |
| `docs/memory/checkpoint_2026-06-12.md` | Parent session (v1 validation arc) |
| `.awos/SESSION_MEMORY_GUIDE.md` | Chat session memory (separate from GUI) |
| `docs/NICHE_GTM.md` | GTM constraint on FastAPI |

---

*Checkpoint immutable after 2026-06-12. Corrections go in `docs/specs/gui_layer_spec.md` version history.*

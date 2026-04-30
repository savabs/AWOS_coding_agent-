# Memory Protocol

> **The three memory failure modes that kill complex projects:**
> 1. A decision was made but never written down → re-derived inconsistently next session
> 2. The same fact exists in multiple files and they diverge → no one knows which is correct
> 3. Context is rebuilt from scratch every session → 80% of each session is re-reading

This protocol eliminates all three.

---

## 1. Write-Gate Protocol

**A decision is not complete until it exists in a file, written in the same turn as the approval.**

```
CORRECT flow:
  user approves → agent writes to canonical file → agent confirms "written to [file]"

FORBIDDEN flow:
  user approves → agent says "great, I'll remember that" → [session ends] → LOST
```

**If the agent responds to an approval with prose instead of a file write, say: "write it first."**

This applies to:
- Architecture decisions
- API contracts and interface choices
- Configuration decisions
- "We agreed to use X instead of Y"
- Any number, metric, or count that will be referenced later
- Any dependency on an external system that was verified

---

## 2. Single-Owner Rule

Each fact lives in exactly one canonical file. Every other file that references it uses a `[[wiki link]]` to that file. Copying is forbidden.

| Fact type | Canonical owner |
|---|---|
| Current project metrics (test count, node count, dimensions) | `memories/repo/project_structure.md` |
| Roadmap and phase ordering | active task file |
| Session history | checkpoint file (immutable after session ends) |
| Architecture decisions with tradeoffs | `docs/adr/NNNN-slug.md` |
| Feature specifications | `docs/specs/<feature>_spec.md` |
| Configuration schema | config source file (e.g., `config/settings.py`) |
| Entity count, DB stats | `memories/repo/project_structure.md` |

**Check for drift:** `python scripts/fact_lint.py`

Detects numeric constants and key facts that appear in more than one file outside the canonical owner.
Fix FL01 (constant duplicated in non-owner), FL03 (constant has different values in copies) before committing.
FL02 (constant copied but consistent) is advisory — fix with `--strict`.

---

## 3. Session Checkpointing

### When to write a checkpoint
- After completing a feature or sub-phase
- Before starting a context-heavy new topic
- When logging off for the day
- After significant architectural decisions
- When the session has been running for a long time and context is getting full

### What a checkpoint must contain
1. **What changed** — linked to task file steps (use wiki links)
2. **Current state** — test counts, node counts, phase progress (from canonical source)
3. **What is blocked** — specific step, specific reason
4. **Next session starting point** — which file to read first, which step to continue from
5. **Corrections** — if a prior checkpoint was wrong, note the correction here

### Checkpoints are immutable
After the session ends, a checkpoint is a historical record. Never edit it.
If it was wrong, write the correction in the canonical owner file and note it in the next checkpoint.
An edited checkpoint has a timestamp that lies about when the information was current.

### Auto-generate
```bash
python scripts/session_checkpoint.py -m "completed X, blocked on Y, next step Z"
```

This reads git state (changed files, recent commits) and active task files to produce a draft checkpoint.
Review and edit before saving — the script produces a draft, not the final document.

### Checkpoint file naming
```
docs/memory/checkpoint_YYYY-MM-DD.md          # one per day
docs/memory/checkpoint_YYYY-MM-DD_slug.md     # multiple per day
```

---

## 4. Repository Memory File

`memories/repo/project_structure.md` is the cold-start accelerator.

**Contents:**
- Project identity and mission (2–3 sentences)
- Key modules and what they do
- Execution flow (how the system runs end-to-end)
- Current metrics (test counts, entity counts, phase progress)
- Active roadmap (what's next)
- Links to active task files

**Rules:**
- Update at every session end with new metrics and phase progress
- Keep it compact — it must fit in one screenful
- Use numbers, not prose: "9,676 tests" not "many tests"
- This file has a canonical owner: no other file duplicates its numbers

**What it is NOT:**
- A design doc (that's in `docs/research/`)
- A task tracker (that's in `tasks/active/`)
- A checkpoint (that's in `docs/memory/`)

---

## 5. Cold-Start Protocol

When beginning a new session, execute in order:

**Step 1 (30 seconds):** Read `memories/repo/project_structure.md`
- Understand what the project is
- Know the current phase and what metric we're at
- Find the active task file link

**Step 2 (2 minutes):** Read the latest checkpoint
```bash
ls -t docs/memory/*.md | head -3  # find latest checkpoints
```
- Know what happened in the last session
- Know what is blocked
- Know where to resume

**Step 3 (2 minutes):** Read the active task file
```bash
ls tasks/active/
```
- Find the specific step to start from
- Understand the spec context (follow the `[[spec_link]]`)

**Step 4 (1 minute):** Follow wiki links as needed
- If the task references a spec: read that spec's relevant section
- If the task references a research note: read the relevant part
- Stop when you have enough context to write the next line of code

**Total: ~5 minutes to full context. Do not read the entire codebase.**

---

## 6. Reviewed Memory vs Raw Memory

Not every observation becomes a standing rule.

**Raw memory:** observations recorded during a session (in task notes, session log)
**Reviewed memory:** patterns promoted to doctrine after repeated, consistent confirmation

Promotion criteria for a pattern to become doctrine:
- Appears ≥3 times in ≥2 separate runs
- Sign (positive/negative outcome) is consistent ≥80% of the time
- Has a plausible causal explanation (not just correlation)

Patterns that don't meet this threshold stay in session notes only.
This prevents superstition from becoming procedure.

---

## 7. Checkpoint Rotation

When checkpoint count exceeds ~30 files, the `docs/memory/` directory gets slow to navigate
and old checkpoints add noise to cold-start.

```bash
python scripts/rotate_checkpoints.py --keep 15
```

Archives checkpoints older than the last 15 to `docs/memory/archive/YYYY/`.
Preserves all history; just moves old files out of the hot path.

Run rotation when:
- `ls docs/memory/*.md | wc -l` exceeds 30
- Cold-start is slow because of too many checkpoint files to scan

---

## 8. Pattern Extraction

After a task is completed:
```bash
python scripts/extract_patterns.py
```

Mines completed task files and recent checkpoints for:
- Decisions that were made and confirmed correct
- Bugs that were fixed and had a root cause
- Performance improvements that were measured
- Patterns that appeared repeatedly

Output goes to `docs/memory/extracted_patterns_YYYY-MM.md` for review.
Patterns that meet the reviewed-memory threshold are candidates for promotion to `AWOS.md`.

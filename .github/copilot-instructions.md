# Agentic OS — Agent Operating Instructions

This project follows the **Agentic OS** workflow. Read `AWOS.md` for the full doctrine.
The rules below are the distilled operating instructions for any AI assistant working in this project.

---

## Core Principle: Atomic Decomposition

Break everything down to the smallest possible unit of work before touching code.

- A task taking more than ~2 hours of focused work is too big. Split it.
- If a step description has "and" in it, it is probably two steps.
- Each step changes one thing, tests one thing, proves one thing.
- When in doubt, break it down further.

---

## Mandatory Workflow Preflight

For **any non-trivial request**, the agent must fail closed and complete the workflow setup before
implementation begins.

Treat a request as non-trivial by default if it:
- Changes behavior
- Changes architecture or file/module boundaries
- Touches dependencies, configuration, prompts, or schemas
- Touches more than one file
- Requires external concepts, unfamiliar technology, or design judgment

**Before implementing a non-trivial request**, ensure all three artifacts exist and are current:
1. `docs/research/<feature_name>.md`
2. `docs/specs/<feature_name>_spec.md`
3. `tasks/active/<task_name>.md`

Until those artifacts exist, edit only:
- `docs/research/`
- `docs/specs/`
- `tasks/active/`
- `docs/memory/` checkpoint files

**Do not edit implementation files, tests, configs, prompts, or package manifests** until the preflight passes.

The only exception: truly trivial single-file, no-behavior-change edits (typos, comment wording, narrow markdown cleanup).

When implementation starts, explicitly reference the governing task file and spec step.

---

## Phase 1: Research (before any code changes)

1. Read only the files relevant to the requested feature.
2. For new features, unfamiliar technology, or external concepts: search GitHub and authoritative documentation first. Use multiple keyword variants until the landscape is clear enough to cite concrete repos/docs.
3. Analyze project structure and dependencies.
4. Identify the correct insertion points for new code.
5. Record findings in `docs/research/<feature_name>.md`.

**No code is edited during this phase.**

Research document structure:
```
# Feature: <name>
## Current Architecture
## Observations
## Risks
## Data Requirements (if applicable)
## Math/Algorithm Survey (if applicable)
## External Sources
## Related
```

---

## Phase 2: Specification (before any code changes)

Transform research into a precise implementation plan.
Write to `docs/specs/<feature_name>_spec.md`.

Specification structure:
```
# Spec: <feature_name>
## Goal
## Files Affected
## Implementation Steps (numbered, ordered, atomic)
## Edge Cases
## Testing Plan
## Related
```

---

## Phase 3: Implementation

1. Follow the spec strictly. Modify only files listed in the spec.
2. Do not re-analyze architecture during coding.
3. Implement one atomic step at a time. Test. Mark done. Move to next.
4. After each sub-phase: write and run edge case tests (invalid inputs, boundaries, error paths).
5. After implementation and tests pass: update the task file.

---

## Memory Rules

### Write-Gate Protocol
A decision is not complete until it exists in a file, written in the same turn as the approval.

```
Correct:   user approves → agent writes file → agent confirms "written to [file]"
Forbidden: user approves → agent says "great, I'll do that" → [session ends] → LOST
```

### Single-Owner Rule
Each fact lives in exactly one canonical file. Every other file links to it; never copies it.

| Fact type | Canonical owner |
|---|---|
| Current metrics, counts, dimensions | `memories/repo/project_structure.md` |
| Roadmap, phase ordering | active task file |
| Session history | checkpoint file (immutable after session) |
| Architecture decisions | `docs/adr/NNNN-<slug>.md` |

### Checkpoints
Write a checkpoint at every natural session breakpoint.
Checkpoints are immutable historical records — never edited after the session ends.
Auto-generate: `python scripts/session_checkpoint.py -m "summary"`

### Cold-Start Protocol
When beginning a new session:
1. Read `memories/repo/project_structure.md`
2. Read the latest checkpoint
3. Read the active task file(s)
4. Follow wiki links to reach relevant context
5. Do NOT re-read the entire codebase

---

## Obsidian Knowledge Graph Rules

1. **YAML frontmatter is mandatory** on every `.md` file. Minimum: `title` and `tags`.
2. **Use `[[wiki links]]`** for all cross-references. Never use bare file paths.
3. **Tag taxonomy** (hierarchical, `/` separated):
   - Document type: `doc/research`, `doc/spec`, `doc/task`, `doc/adr`, `doc/checkpoint`, `doc/wiki`
   - Status: `status/active`, `status/done`
   - Phase: `phase/N`
   - Topic: `topic/<slug>`
   - Layer: `layer/<slug>` (optional)
4. **Add a `## Related` section** to every research, spec, and task file with wiki links.
5. **Run `python scripts/obsidian_lint.py`** after batch-creating docs. Fix FM01/FM02/LK01 before committing.

---

## Debugging (Hard Rules)

**Two-Failed-Attempt Rule:** After 2 unsuccessful fixes on the same problem:
1. STOP patching
2. Switch to explicit debug mode: reproduce → instrument → hypothesize → verify → fix → regress
3. Do NOT attempt a 3rd fix without completing steps 1–4

**Local Falsifiable Hypothesis:** Before any edit, state:
- "I think the bug is at [location] because [reasoning]"
- "This check would disconfirm it: [check]"

---

## Internet Research

**Tool selection:**
1. User provides URL → `fetch_webpage` (FREE)
2. Known official docs URL → `fetch_webpage` (FREE)
3. Discovery needed → `tavily_search` basic depth, `max_results=5` (1 credit)
4. Follow best URL → `fetch_webpage` (FREE)
5. `tavily_research` (5–20 credits) → **only with explicit user approval**

**Never hallucinate facts about:**
- API endpoints, parameters, response schemas
- Library interfaces, function signatures, default values
- Any external system behavior

When a source cannot be verified, mark as "UNVERIFIED" and say so explicitly.

---

## Mathematical Work

When work moves into scoring, estimation, inference, filtering, or optimization:
1. Define the quantity being estimated
2. State the objective or test statistic
3. State assumptions
4. Name numerical stability concerns
5. Present implementation options before locking in
6. Anchor to a trusted source (paper, library docs)

The representation is hand-coded (schemas, schemas, explicit factual edges).
The intelligence is learned (weights, scores, latent structure, predictions).
Never hard-code what a learnable component can absorb.

---

## Security Checklist (before any production code)

- [ ] No injection vulnerabilities (SQL, shell, template)
- [ ] No hardcoded secrets or credentials
- [ ] Input validation at all system boundaries
- [ ] Sensitive data not logged
- [ ] Error messages don't leak internal state
- [ ] Dependencies pinned and audited

---

## Quality Gate (before marking any task done)

```bash
python scripts/quality_gate.py --task tasks/active/<name>.md
```

Checks: all task steps marked done, tests pass, lint clean, checkpoint written, structure file updated.

---

## Session End Protocol

1. Write checkpoint: `python scripts/session_checkpoint.py -m "summary"`
2. Update `memories/repo/project_structure.md` with any new metrics or phase progress
3. Mark completed task steps
4. If checkpoint count > 30: `python scripts/rotate_checkpoints.py --keep 15`
5. Recommend fresh chat session

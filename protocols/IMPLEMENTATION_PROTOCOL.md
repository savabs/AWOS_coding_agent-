# Implementation Protocol

> **Rule: no implementation without a research doc and spec.**
> Implement is the third phase, not the first.
> The spec is the contract. The task file is the progress tracker. Follow both.

---

## Pre-Implementation Checklist

Before writing the first line of code, confirm:

- [ ] `docs/research/<feature_name>.md` exists and covers: current architecture, risks, external sources
- [ ] `docs/specs/<feature_name>_spec.md` exists and covers: goal, files affected, ordered steps, edge cases, testing plan
- [ ] `tasks/active/<task_name>.md` exists with numbered steps derived from the spec
- [ ] The spec's "Files Affected" list is explicit — know exactly what will change and what won't
- [ ] The exit condition for the first step is clear (falsifiable one-line check)

If any of these are missing: create them first. Do not proceed.

---

## The Implementation Loop

For each step in the task file:

```
1. Read the step description
2. Read the spec's detail for that step
3. Identify the exact file(s) and function(s) to change
4. Make exactly that change
5. Run the verification test for that step
6. If test passes: mark the step done in the task file
7. If test fails: debug before moving on (see DEBUG_PROTOCOL.md)
8. Move to the next step
```

**One step at a time. No skipping. No combining.**

---

## During Implementation

### Only modify files in the spec

The spec's "Files Affected" list is the contract. If you find yourself editing a file that's not on the list:
- Stop
- Ask: is this a necessary dependency that was missed in the spec?
- If yes: update the spec's files list first, then make the change
- If no: you are scope-creeping. Revert and stick to the list.

### Don't re-analyze architecture

The research phase was for understanding. The spec phase was for planning. During implementation:
- If something doesn't match the spec: update the spec first, then proceed
- If the architecture seems wrong: stop, update the research doc, update the spec, resume
- Do not derive new architecture in the middle of coding

### No speculative improvements

While implementing Step N, do not:
- Refactor adjacent code
- Fix unrelated bugs "while you're in there"
- Add features not in the spec
- Improve naming or structure outside the files affected

These can all be separate tasks with their own research-spec-task cycle.

---

## Testing Requirements

Every sub-phase requires edge case tests before it is considered complete:

**Categories to cover:**
- [ ] Happy path (the expected behavior works)
- [ ] Invalid input (wrong type, None, empty, malformed)
- [ ] Boundary values (zero, one, max, min)
- [ ] Error paths (what happens when external calls fail)
- [ ] Security cases (injection attempts, privilege escalation at input boundaries)
- [ ] Timeout/retry behavior (if applicable)
- [ ] Type mismatches (string where int expected, etc.)
- [ ] Missing required fields
- [ ] Exception handling (are exceptions caught only where expected?)

**Minimum for AI-generated leaf node code:**
- 1 happy path
- 2 most likely failure cases

**Full suite for non-leaf, architecturally significant code:**
All categories above.

A sub-phase is not complete until its tests pass. "I'll add tests later" is not allowed.

---

## Updating the Task File

After each step completes:
```markdown
# In tasks/active/<name>.md
- [x] N.1.1: Implemented X — verification passed (2026-04-28)
- [x] N.1.2: Added tests — 5 new tests pass
- [~] N.1.3: In progress — started type validation
```

The task file reflects real state. Never mark a step done if its verification test hasn't passed.

---

## Commit Discipline

One commit per logical unit of change:
- Never combine a bug fix with a refactor
- Never combine a feature with a dependency upgrade
- Never combine changes to multiple independent features

Commit message format:
```
<type>(<scope>): <what changed>

<optional: why it changed, if not obvious>
```

Types: `feat`, `fix`, `test`, `refactor`, `docs`, `chore`

Example:
```
feat(tools): add entity resolution to insider_filings tool

Adds link_entities() calls per entity_linking_layer_spec.md step 3.2.
Entities linked: person↔company (works_for), company↔organization (lobbies_for).
```

---

## Handling Surprises

When you discover something during implementation that contradicts the spec or research doc:

**Option A: The discovery is a small clarification**
- Document it in a "Decisions Made" note in the task file
- Proceed without updating the spec (the change is too minor)

**Option B: The discovery changes an interface, adds risk, or changes the approach**
1. Stop implementation
2. Update the research doc with the new finding
3. Update the spec with the revised approach
4. Update the task file with any revised steps
5. Then resume implementation

**Never silently deviate from the spec without recording why.**

---

## Pre-Completion Quality Gate

Before marking a task complete:

```bash
python scripts/quality_gate.py --task tasks/active/<name>.md
```

Manual checks:
- [ ] All task steps are marked done
- [ ] `pytest` (or equivalent) passes with no failures
- [ ] `python scripts/obsidian_lint.py` is clean (FM01, FM02, LK01 resolved)
- [ ] `memories/repo/project_structure.md` updated with new test counts, phase progress
- [ ] Checkpoint written documenting what was done
- [ ] `docs/adr/` updated if any significant design decisions were made

After passing:
1. Change task status to `completed`
2. Change task tag from `status/active` to `status/done`
3. Move task file to `tasks/done/`
4. Write final checkpoint

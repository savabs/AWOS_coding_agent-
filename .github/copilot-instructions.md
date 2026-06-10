# Agentic OS — Agent Operating Instructions

This project is **AWOS** — a learnable operating system for autonomous work.
Read **`VISION.md`** for product identity. Read **`AWOS.md`** for operational protocols.
The rules below are distilled operating instructions for any AI assistant working in this project.

**Identity:** AWOS is a greedy meta-AI optimizing quality × speed ÷ cost. Coding agent is App #1.
**Not:** a Cursor alternative, mass-market coding tool, or IDE plugin.

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
1. `docs/research/<feature_name>.html` + thin `docs/research/<feature_name>.md` stub
2. `docs/specs/<feature_name>_spec.html` + thin `docs/specs/<feature_name>_spec.md` stub
3. `tasks/active/<task_name>.html` + thin `tasks/active/<task_name>.md` stub

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
5. Record findings in `docs/research/<feature_name>.html` (canonical) + thin `docs/research/<feature_name>.md` stub.
   - Copy `docs/research/RESEARCH_TEMPLATE.html` as the starting point.
   - Stub format: YAML frontmatter + `> **Content:** [file.html](file.html)` + `## Related`

**No code is edited during this phase.**

---

## Phase 2: Specification (before any code changes)

Transform research into a precise implementation plan.
Create `docs/specs/<feature_name>_spec.html` (canonical) + thin `docs/specs/<feature_name>_spec.md` stub.
- Copy `docs/specs/SPEC_TEMPLATE.html` as the starting point.
- Steps must be atomic: one thing changed, one thing tested, one thing proved. If a step has "and" — split it.

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
| Architecture decisions | `docs/adr/NNNN-<slug>.html` (+ thin `.md` stub) |

### Checkpoints
Write a checkpoint at every natural session breakpoint.
Checkpoints are immutable historical records — never edited after the session ends.
Auto-generate: `python scripts/session_checkpoint.py -m "summary"`

### Cold-Start Protocol
When beginning a new session:
1. Read `AGENT_INDEX.md` — agent-optimized map of every file and where to write each artifact type
2. Read `memories/repo/project_structure.md` — canonical project facts
3. Read the latest checkpoint in `docs/memory/` (most recent `.html` or `.md`)
4. Read the active task file(s) in `tasks/active/`
5. Follow wiki links to reach relevant context
6. Do NOT re-read the entire codebase

---

## HTML-First Artifacts Rule (§5.7)

All output artifacts are created as **`.html` primary** + **thin `.md` stub** for Obsidian navigation.

| Artifact type | Template | Canonical location |
|---|---|---|
| Research note | `docs/research/RESEARCH_TEMPLATE.html` | `docs/research/<feature>.html` |
| Spec | `docs/specs/SPEC_TEMPLATE.html` | `docs/specs/<feature>_spec.html` |
| Task file | `tasks/active/TASK_TEMPLATE.html` | `tasks/active/<task>.html` |
| Session checkpoint | `docs/memory/CHECKPOINT_TEMPLATE.html` | `docs/memory/checkpoint_YYYY-MM-DD.html` |
| ADR | `docs/adr/TEMPLATE.html` | `docs/adr/NNNN-<slug>.html` |

**MD stub format** (mandatory for Obsidian graph, must be co-located with the .html):
```markdown
---
title: "<title>"
tags:
  - doc/<type>
  - topic/<slug>
---
> **Content:** [filename.html](filename.html) — open in browser.
## Related
- [[linked_doc]]
```

**Plain Markdown is still OK for:** `README.md`, `AWOS.md`, `AGENT_INDEX.md`, append-only logs (`wiki/log.md`), code comments.

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

---

## Session Auto-Startup (MANDATORY — execute at the start of EVERY session)

Before responding to ANY user request, the agent MUST complete this cold-start ritual:

**Step 1 — Run the warmup script:**
```bash
python scripts/session_warmup.py
```
This prints: latest checkpoint, active tasks with step counts, last commits, project snapshot.
If the script doesn't exist, continue to Step 2 manually.

**Step 2 — Load project state (if warmup script unavailable):**
1. Read `AGENT_INDEX.md` — agent cold-start map with file directory and artifact creation rules
2. Read `memories/repo/project_structure.md` — canonical project facts
3. List `docs/memory/` → read the most recent checkpoint file
4. List `tasks/active/` → read every active task file

**Step 3 — Discover available tools:**
Call `tool_search` with `"file read write search terminal web git memory"` to load the full
tool set. Declare which MCP servers are active (check `.vscode/mcp.json`).
Refer to `TOOL_MANIFEST.md` for the full capability map.

**Step 4 — Declare operational state** before answering:
```
=== SESSION STATE ===
Project: <name>
Active task: <file> — Step <N>: <description>
Last checkpoint: <date> — <one-line summary>
Next action: <specific next step>
Tools: file_io ✓ | terminal ✓ | web_free ✓ | tavily ✓/✗ | git ✓/✗ | github ✓/✗
=====================
```

**Never skip the cold-start.** A session that skips state-loading diverges from reality within 2–3 turns.

---

## Tool Capability Matrix

| Category | Tool | Cost | When to Use |
|---|---|---|---|
| File I/O | read_file, create_file, replace_string_in_file | Free | Always prefer over terminal for file ops |
| Search | grep_search, file_search, semantic_search | Free | Default for codebase navigation |
| Terminal | run_in_terminal, get_terminal_output | Free | Tests, scripts, git commands, API probing |
| Web (free) | fetch_webpage | Free | Known URLs — official docs, GitHub READMEs |
| Web (1cr) | tavily_search basic, max_results=5 | 1 credit | Discovery when URL is unknown |
| Web (2cr) | tavily_search advanced | 2 credits | Complex/niche topics only |
| Web (5-20cr) | tavily_research | 5–20 credits | **User approval required** |
| Memory | memory tool (view/create/str_replace) | Free | Persistent notes across sessions |
| Git | mcp_git_* (load via tool_search) | Free | Source control operations |
| GitHub | mcp_github_* (load via tool_search) | Free | Issues, PRs, remote repo ops |
| Reasoning | mcp_sequential-th_sequentialthinking | Free | Complex decisions, architecture tradeoffs |
| Library docs | mcp_context7_* (load via tool_search) | Free | Current API docs for any library |

**Tool discovery:** Call `tool_search("description of what you need")` to load deferred tools.
Never assume a tool exists — verify first.

**Preferred order for web research:**
1. Known URL → `fetch_webpage` (free, always first)
2. Need to find URL → `tavily_search` basic (1 credit)
3. Read found URL → `fetch_webpage` (free again)
4. Terminal test → free and ground truth (prefer for API verification)
5. `tavily_research` → only with explicit user approval

---

## Autonomous Execution Rights

The agent MAY execute the following **without asking for confirmation:**

**Reads (always safe):**
- Read any file anywhere in the workspace
- `git status`, `git log`, `git diff` — read-only git
- `fetch_webpage` for any URL
- `tavily_search` up to 5 total credits per session

**Writes to designated directories (safe):**
- `docs/research/`, `docs/specs/`, `docs/memory/` — workflow artifacts
- `tasks/active/`, `tasks/done/` — task tracking
- `memories/` — project + session memory
- `wiki/` — knowledge base pages

**Commands (safe):**
- Run tests: `pytest`, `python -m pytest`
- Run lint: `ruff check`, `obsidian_lint.py`, `fact_lint.py`
- Run automation: `session_warmup.py`, `session_checkpoint.py`, `quality_gate.py`, `rotate_checkpoints.py`
- `git add`, `git commit` — local commits only

**The agent MUST ask before:**
- Deleting any file or directory (`rm`, `unlink`, `shutil.rmtree`)
- `git push`, `git push --force`, `git reset --hard`
- Modifying implementation files NOT listed in the active spec
- Spending >5 Tavily credits in one operation
- Any irreversible action on shared/production systems
- Amending published commits

---

## Mandatory Pre-Search Rule

**Before implementing ANY of the following, run an internet search first — no exceptions:**

| Trigger | Required Action |
|---|---|
| Using a library for the first time this session | Fetch its official docs page |
| Calling an external API | Verify endpoint, params, auth, rate limits |
| Implementing a mathematical method | Cite the paper or reference docs |
| Using an unfamiliar data format | Read the spec |
| Adding a new dependency | Check current version, license, PyPI page |

**Search workflow:**
1. Identify the exact concept (be specific — not "yfinance" but "yfinance futures ticker format")
2. Search with 2–3 keyword variants
3. Read official docs via `fetch_webpage`
4. Record verified sources in the research file **before writing code**

**Zero-hallucination policy:** API endpoints, function signatures, ticker symbols, exchange codes,
response schemas, mathematical properties — never assert these from memory alone.
Mark as "UNVERIFIED" if you cannot verify. Never guess.

**Terminal probing beats search for verification:**
```bash
python -c "import yfinance; print(yfinance.Ticker('GC=F').history(period='1d'))"
```
Running the actual call is free, fast, and definitive. Do this before searching.

---
description: "Research phase — read code, search OSS and official docs, write research doc. No implementation files are changed."
mode: agent
tools:
  - read_file
  - grep_search
  - file_search
  - semantic_search
  - fetch_webpage
  - tool_search
  - run_in_terminal
  - create_file
---

# Research Phase

**This is a READ-ONLY phase for implementation files.** Only `docs/research/` may be written to.

## Step 1: Understand the Request

Identify:
- What is the exact feature or concept being researched?
- Which architecture layer does it belong to? (from copilot-instructions.md)
- What files in the codebase are already relevant?
- What is the insertion point for any future code?

## Step 2: Read Existing Code

Use semantic_search and grep_search to find:
- The relevant module(s) and their current patterns
- Any existing similar functionality
- Dependencies and constraints
- Test coverage that must be preserved

Read relevant sections — understand the landscape before searching externally.

## Step 3: Search External Sources

For every unfamiliar concept, library, API, or algorithm in this feature:

**Known URL:** Use `fetch_webpage` directly (free).

**Unknown URL:** Use `tavily_search` with basic depth, max_results=5. Then read the best result with `fetch_webpage`.

Use at least 2–3 keyword variants per concept. Record every URL you read.

**License check:** For any OSS repository found, note the license. If unclear or incompatible with commercial use, treat as concept-only — do not port code, only capture the design insight.

## Step 4: Terminal Verification (for APIs and libraries)

Before asserting any API behavior, probe it:
```bash
python -c "import <library>; <test call>"
```
This is free and definitive. Prefer terminal probing over search for verification.

## Step 5: Write the Research Document

Create `docs/research/<feature_name>.md`:

```yaml
---
title: "Research: <Feature Name>"
tags:
  - doc/research
  - phase/<N>
  - topic/<slug>
  - layer/<slug>
---
```

Required sections:
- `## Current Architecture` — what exists today (files, patterns, dependencies)
- `## Observations` — gaps, risks, what's missing, what connects to what
- `## External Sources` — every URL read, library name + version, license noted
- `## Implementation Options` — at least 2 alternatives with explicit tradeoffs
- `## Recommended Approach` — which option and why (cite the source)
- `## Depth Roadmap` — if a data tool: what L1 / L2 / L3 look like for this source
- `## Math Notes` — if math-heavy: the quantity, objective, assumptions, stability concerns
- `## Related` — `[[spec_name]]`, `[[task_name]]`, other linked docs

## Step 6: Confirm

Output:
```
RESEARCH COMPLETE
Doc: docs/research/<feature_name>.md
Key finding: <one-sentence summary>
Recommended approach: <one sentence>
Ready to write spec? [yes / need more research on <topic>]
```

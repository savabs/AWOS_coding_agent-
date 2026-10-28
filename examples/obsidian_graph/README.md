---
title: "Example: Obsidian Graph View Setup"
tags:
  - doc/wiki
  - topic/obsidian
---

# Example: Using This Repo as an Obsidian Vault

The entire `agentic-os` directory is a valid Obsidian vault. Opening it in Obsidian unlocks Graph View, backlink navigation, and tag search — the recommended tools for navigating large agent projects.

---

## Setup (2 minutes)

1. **Install Obsidian** — https://obsidian.md (free)
2. **Open the vault:**
   - File → Open folder as vault
   - Select the project root (e.g., `~/projects/my-agent/`)
3. **Configure Graph View** — see below

That's it. All `[[wiki links]]` resolve automatically. All frontmatter tags are searchable.

---

## Recommended Graph View Settings

Open Graph View (`Ctrl+G` / `Cmd+G`), then click the filter icon:

**Filters to enable:**
- Show attachments: OFF
- Show orphans: OFF (hides unlinked files)
- Color groups:
  - `tag:doc/research` → Orange
  - `tag:doc/spec` → Blue
  - `tag:doc/task` → Green
  - `tag:doc/checkpoint` → Gray
  - `tag:doc/adr` → Purple
  - `tag:doc/wiki` → Yellow

**Forces:**
- Center force: 0.2
- Repel force: 10
- Link force: 1.0
- Link distance: 150

This produces a graph where research (orange) connects to specs (blue) which connect to tasks (green). ADRs (purple) float as isolated decisions. Checkpoints (gray) cluster by date.

---

## Tag Search

In the search bar, type `tag:doc/research` to see all research notes. Use `tag:status/active` to see all in-progress work.

Combine: `tag:doc/task tag:status/active` = all active tasks.

---

## Backlink Navigation

With any file open, press `Ctrl+Shift+L` (or the backlink icon) to see all documents that link to this one. This is the fastest way to trace a feature end-to-end:

```
research note ← linked by spec ← linked by task ← linked by checkpoint
```

---

## Recommended Core Plugins (built-in)

Enable in Settings → Core Plugins:

- **Graph view** — visual knowledge map
- **Backlinks** — see what links here
- **Tag pane** — browse by tag
- **Templates** — for applying frontmatter templates
- **Outline** — section navigation for long docs

---

## Recommended Community Plugins

These are optional but useful:

- **Dataview** — query your vault like a database (`TABLE` of active tasks)
- **Calendar** — navigate checkpoints by date
- **Kanban** — view tasks as a board (alternative to task files)

---

## Using Dataview for Active Tasks

Install the Dataview plugin, then create a note with:

````markdown
```dataview
TABLE file.mtime as "Last Modified"
FROM #status/active
SORT file.mtime DESC
```
````

This produces a live table of all active tasks, sorted by modification date.

---

## Related

- [[AWOS]] — the doctrine for this knowledge graph
- [[SCHEMA]] — rules for wiki page creation
- [[GLOSSARY]] — key terms

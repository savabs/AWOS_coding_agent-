---
title: "Wiki Schema — Page Creation Rules"
tags:
  - doc/wiki
  - topic/knowledge-graph
---

# Wiki Schema

Rules for when and how to create, update, and retire wiki pages.

---

## When to Create a New Wiki Page

Create a new page when **either** of these thresholds is met:

1. **Two-source rule:** A topic appears in 2+ independent research notes with overlapping content.
2. **Central single-source:** A topic is the primary subject of one research note AND is referenced by 3+ other documents.

Do NOT create a page for:
- One-off decisions (use ADRs instead)
- Temporary working notes (use checkpoints)
- Per-feature details (stay in research/spec/task triad)

---

## Page Structure

Every wiki page must have:

```yaml
---
title: "<descriptive name>"
tags:
  - doc/wiki
  - topic/<slug>
---
```

Sections:
1. **Definition** — one paragraph explaining the concept
2. **Why it matters** — why this is relevant to the project
3. **Current implementation** — where it lives in the codebase
4. **Known limitations / open questions**
5. **## Related** — `[[wiki links]]` to related docs

---

## Contradiction Handling

When two sources disagree on a fact:

1. Note both positions with their dates in the page
2. Add `contradicted: true` to frontmatter
3. Add `contradiction_note: "Source A says X (YYYY-MM-DD); Source B says Y (YYYY-MM-DD)"`
4. Do NOT silently overwrite old information
5. Resolve the contradiction by consulting the canonical owner file

---

## Page Lifecycle

| Stage | Action |
|---|---|
| Draft | Add `status: draft` to frontmatter |
| Active | Remove `status: draft`; add `doc/wiki` tag |
| Deprecated | Add `status: deprecated`; note what supersedes it |
| Retired | Move to `wiki/archive/`; do not delete |

---

## Log Rotation

`wiki/log.md` is a running activity log.
When it exceeds **500 entries**, rotate:

1. Copy current log to `wiki/log-YYYY.md` (the year of the oldest entries)
2. Clear `wiki/log.md` and start fresh
3. Add a link to the archived log at the top of the new log

---

## Related

- [[GLOSSARY]] — key terms defined
- [[log]] — running activity log
- [[AWOS]] — the doctrine these rules serve

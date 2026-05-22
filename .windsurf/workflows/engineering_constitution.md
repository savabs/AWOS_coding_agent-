---
description: AI Engineering Constitution — Spec-driven development workflow
---

# AI Engineering Constitution Workflow

## Before Any Implementation

1. **Read Documentation First**
   - Architecture docs
   - Interface contracts
   - Repo patterns
   - Dependency flow
   - Task boundaries

2. **Identify Scope**
   - Affected modules
   - Dependencies
   - Interface boundaries
   - Constraints
   - Task scope

3. **Check Escalation Triggers**
   - Architecture changes? → Escalate to stronger model
   - Multi-system spanning? → Escalate
   - Unclear state flow? → Escalate
   - Concurrency/async? → Escalate
   - Root cause debugging? → Escalate
   - Security-sensitive? → Escalate
   - Performance bottleneck? → Escalate
   - Conflicting patterns? → Escalate

## During Implementation

**Follow These Rules:**
- Rule 1: Never invent architecture (follow existing patterns)
- Rule 2: Respect existing interfaces (no silent changes)
- Rule 3: Implement only requested scope (stay tightly scoped)
- Rule 4: Prefer simplicity (readable > clever)
- Rule 5: Read documentation first (always)

**Allowed Behavior:**
- Implement clearly defined tasks
- Follow existing patterns
- Generate boilerplate
- Create localized modules
- Add tests
- Improve readability locally
- Fix isolated bugs

**Forbidden Behavior:**
- Redesign systems autonomously
- Invent abstractions freely
- Silently refactor architecture
- Modify interfaces casually
- Introduce dependency sprawl
- Create inconsistent patterns

## After Implementation

1. **Verify Imports** — All dependencies at top of file
2. **Verify Interfaces** — No silent API changes
3. **Verify Type Consistency** — Types match contracts
4. **Verify Dependency Direction** — Follows hierarchy
5. **Verify Scope** — Only changed requested files

## Output Format

When implementing:
1. Explain approach briefly
2. List changed files
3. Explain assumptions
4. Identify risks if any
5. Keep responses concise

## Repository Intelligence Hierarchy

```
Architecture Documents
    ↓
Interface Contracts
    ↓
Task Specifications
    ↓
Existing Patterns
    ↓
Implementation
```

Implementation must obey higher-level specifications.

## Model Discipline

**Strong Reasoning Models** (Opus, Sonnet, Pro):
- Architecture
- Planning
- Decomposition
- Interface design
- Debugging complex issues
- Refactoring
- Verification
- Orchestration logic

**Cheaper/Faster Models** (DeepSeek, Haiku, Flash):
- Implementation
- Boilerplate
- Repetitive coding
- UI work
- CRUD operations
- Adapters
- Wrappers
- Tests
- Localized tasks

## When in Doubt

Stop and:
- Explain ambiguity
- Request clarification
- Request escalation
- Do NOT hallucinate missing architecture

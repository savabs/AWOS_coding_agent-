# Research Protocol

> **Goal: understand, not plan.**
> The research phase produces understanding. The spec phase produces the plan.
> No implementation decisions are made during research. Only observations, analysis, and references.

---

## When to Do Research

The research phase is required for any non-trivial feature. Skip it only for:
- Typos and comment wording
- Narrowly scoped markdown cleanup
- Changes so small that the implementation is self-evident and risk-free

When in doubt: do the research.

---

## Phase 1A — Codebase Survey

Before looking at anything external, understand the current state of the project.

**Step 1: Find the relevant files**
```bash
# Semantic search for related functionality
grep -r "feature_keyword" agent/ tests/ --include="*.py" -l

# Find imports and usages of relevant symbols
grep -r "ClassName\|function_name" agent/ tests/ --include="*.py"
```

**Step 2: Read only the relevant files**
- Read the specific module that will change
- Read its immediate dependencies (one level deep)
- Read the tests that cover it
- **Stop. Do not read the entire codebase.**

**Step 3: Map the integration points**
- What calls this code? (callers)
- What does this code call? (dependencies)
- What schemas or data structures flow through it?
- What configuration does it read?

Document findings under "Current Architecture" and "Observations" in the research doc.

---

## Phase 1B — External Research (for new concepts)

**When to do external research:**
- Using a library you haven't used before in this project
- Implementing a mathematical or algorithmic method
- Integrating with an external API or data source
- Implementing something whose design is significantly influenced by OSS patterns

**What to produce:**
- Verified sources (URLs, titles, dates) for every factual claim
- OSS repositories that demonstrate the pattern (note license)
- Documentation references for any library being used
- For math: the primary paper or authoritative reference

**Keyword strategy:**
Do not search once and stop. Use multiple variants:
```
# First search: broad
"hidden markov model python"

# Second search: specific to use case
"hmmlearn regime detection financial time series"

# Third search: implementation patterns
"hmm forward backward algorithm numpy log scale"

# Fourth search: known pitfalls
"hmmlearn convergence issues small samples"
```

**Record everything in the research doc.** Findings not written down are re-discovered next session.

---

## Phase 1C — Risk Analysis

For every feature, think through failure modes before writing the spec:

**Technical risks:**
- What breaks if this feature has a bug?
- What are the performance implications?
- What happens when external dependencies fail?
- What are the edge cases in the input data?

**Security risks (per SECURITY_PROTOCOL.md):**
- Does this feature handle external input?
- Does this feature touch authentication or authorization?
- Does this feature log or persist sensitive data?

**Scope risks:**
- Is this feature well-bounded or does it have fuzzy edges?
- Are there implicit dependencies that could expand scope unexpectedly?
- Does this require changes to shared interfaces?

---

## Research Document Structure

```markdown
# Feature: <Name>

## Current Architecture
(what exists; file-level overview of relevant code)

## Observations
(what you learned by reading the code; patterns; surprises)

## Risks
(technical, security, scope risks identified)

## Data Requirements (if applicable)
(what data is needed; what's available; what's missing)

## Math / Algorithm Survey (if applicable)
(algorithms that apply; options; tradeoffs)

## External Sources
(table of verified sources with URLs and dates)

## Summary
(1–3 sentences: what needs to be built, key risk, key design question)

## Related
(wiki links to spec, task, related research)
```

---

## OSS Research Rules

When searching GitHub for existing implementations:

**Record for each relevant repo:**
- Repository URL
- Star count and last commit date (proxy for quality and maintenance)
- License (MIT, Apache-2.0, GPL, etc.) — note if incompatible with commercial use
- What the repo demonstrates (pattern, algorithm, full implementation)
- Whether this is: conceptual-only (ideas only) or implementation-reusable

**License guidance:**
| License | Can copy code? | Notes |
|---|---|---|
| MIT, BSD, Apache-2.0 | Yes, with attribution | Standard permissive licenses |
| GPL v2/v3 | Check carefully | Copyleft — may require open-sourcing derivatives |
| LGPL | Generally yes (dynamic linking) | Check project specifics |
| AGPL | Generally no for commercial SaaS | Strong copyleft |
| Proprietary / unclear | No | Treat as conceptual-only |

When in doubt: treat as conceptual-only. Capture the algorithm/design idea in the research doc and implement independently.

---

## When Research Is Complete

The research phase is complete when you can answer:
1. What exactly needs to change in the codebase? (which files, which functions)
2. What risks must the spec address?
3. What are the options for implementing this? (at least two viable options)
4. What are the external references that back the chosen approach?

If you cannot answer these four questions, more research is needed.
Do not start the spec until you can.

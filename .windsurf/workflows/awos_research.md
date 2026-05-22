---
auto_execution_mode: 0
description: AWOS research protocol — understand before planning
---
**Goal: understand, not plan.**
The research phase produces understanding. The spec phase produces the plan.
No implementation decisions are made during research.

## RULE #0 — Internet Search FIRST (Mandatory)
**Before writing any research doc, spec, or hypothesis on a new idea:**
1. ALWAYS run `search_web` on the topic (minimum 3 different search queries)
2. Read at least 2-3 sources from results
3. Only THEN form a hypothesis or write a spec
4. Clearly mark claims as VERIFIED (from source URL) or UNVERIFIED (from reasoning)

**This rule cannot be skipped.** If you skip it you will hallucinate viability claims.

## Phase 1A — Codebase Survey
Before looking at anything external, understand the current state.
1. Find relevant files via grep/semantic search
2. Read only relevant files (module, immediate deps, tests). **Stop. Do not read the entire codebase.**
3. Map integration points: callers, dependencies, schemas, config

## Phase 1B — External Research (for new concepts)
When to do it: using a new library, implementing math/algorithm, integrating external API, influenced by OSS patterns.

What to produce:
- Verified sources (URLs, titles, dates) for every factual claim
- OSS repos that demonstrate the pattern (note license)
- Documentation references for any library being used
- For math: primary paper or authoritative reference

Keyword strategy: do not search once and stop. Use multiple variants:
- Broad: "hidden markov model python"
- Specific to use case: "hmmlearn regime detection financial time series"
- Implementation patterns: "hmm forward backward algorithm numpy log scale"
- Known pitfalls: "hmmlearn convergence issues small samples"

**Record everything in the research doc.** Findings not written down are re-discovered next session.

## Phase 1C — Risk Analysis
For every feature, think through failure modes before writing the spec:
- Technical risks: what breaks, performance implications, dependency failures, edge cases
- Security risks (per SECURITY_PROTOCOL.md): external input, auth, sensitive data
- Scope risks: well-bounded or fuzzy edges, implicit dependencies, shared interfaces

## Research Document Structure
- Current Architecture
- Observations
- Risks
- Data Requirements (if applicable)
- Math / Algorithm Survey (if applicable)
- External Sources (table of verified sources with URLs and dates)
- Summary (1–3 sentences)
- Related (wiki links)

## OSS Research Rules
Record for each relevant repo: URL, stars/last commit, license, what it demonstrates, conceptual-only or implementation-reusable.
License guidance: MIT/BSD/Apache-2.0 = copy with attribution. GPL = check carefully. Proprietary/unclear = conceptual-only only.

## When Research Is Complete
You must be able to answer:
1. What exactly needs to change in the codebase? (which files, which functions)
2. What risks must the spec address?
3. What are the options for implementing this? (at least two viable options)
4. What are the external references that back the chosen approach?

If you cannot answer these four questions, more research is needed. Do not start the spec until you can.

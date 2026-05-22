# CONVENTIONS — Aider Session Rules (token_effectivenes project)
# Loaded as read-only cached context. ~1200 tokens. Do not remove.

## WORKFLOW (mandatory for all non-trivial changes)
- Non-trivial = changes behavior, touches >1 file, touches deps/config/schema
- Preflight before ANY implementation: research → spec → task triad must exist
- Triad: docs/research/<feature>.html + docs/specs/<feature>_spec.html + tasks/active/<task>.html
- Exception: single-file typo/comment fixes only
- Each spec step: changes ONE thing, tests ONE thing, proves ONE thing
- If a step description has "and" → split it into two steps

## ATOMIC DECOMPOSITION
- A task > ~2 hours of focused work → too big, split it
- Implement one atomic step → test → mark done → move to next
- Never start step N+1 before step N exit condition passes

## WRITE-GATE (critical)
- A decision is NOT real until it exists in a file, in the SAME TURN as approval
- Forbidden: "great, I'll do that" → [session ends] → LOST
- After any approval: write the file, confirm "written to [file]"

## MEMORY PROTOCOL
- Single-Owner Rule: each fact lives in exactly ONE canonical file, others link to it
  - Project metrics/counts → memories/repo/project_structure.md
  - Session history → docs/memory/checkpoint_YYYY-MM-DD.md (immutable after session)
  - Roadmap/phase order → active task file
  - Architecture decisions → docs/adr/
- Checkpoint every session end — no exceptions, agent writes it proactively
- Cold-start: read AGENT_INDEX.md → project_structure.md → latest checkpoint → active tasks

## DEBUGGING (hard rules)
- Two-Failed-Attempt Rule: after 2 unsuccessful fixes on same problem → STOP patching
- Switch to: reproduce → instrument → hypothesize → verify → fix → regress
- Before any edit state: "Bug is at [location] because [reason]. Disconfirmed by: [check]"

## SECURITY CHECKLIST (before any production code)
- No injection (SQL, shell, template)
- No hardcoded secrets — all keys in .env
- Input validation at system boundaries
- Sensitive data not logged
- Error messages don't leak internals

## TOKEN EFFICIENCY RULES (this project)
- Always use --cache-prompts (already in .aider.conf.yml)
- Only /add files you will actually edit this session — drop when done
- Use /read-only for reference files (cheaper, cached separately)
- Prefer diffs over full rewrites in responses
- Don't ask Claude to "explain reasoning" unless debugging
- Weak model (haiku) handles: commits, summarization, simple Q&A
- Sonnet for: architecture, complex logic, multi-file changes
- Run `python scripts/prewarm_cache.py` at session start

## INTERNET RESEARCH (mandatory before using any external library/API)
1. Known URL → fetch directly (free)
2. Unknown URL → search first
3. Never assert API signatures, endpoints, or params from memory — always verify
4. Mark unverified facts as UNVERIFIED explicitly

## PROJECT STRUCTURE
- Research: docs/research/  |  Specs: docs/specs/  |  Tasks: tasks/active/
- Memory: docs/memory/ (checkpoints)  |  Repo facts: memories/repo/project_structure.md
- Scripts: scripts/  |  Wiki: wiki/  |  Protocols: protocols/

## IMPLEMENTATION DISCIPLINE
- Only make changes directly requested or clearly necessary
- Don't add docstrings/comments/type annotations to code you didn't change
- Don't add error handling for scenarios that can't happen
- Don't create helpers or abstractions for one-time operations
- Don't refactor, add features, or "improve" beyond what was asked
- Read a file before modifying it — understand existing code first
- After editing: run tests or exit-condition check immediately

## CODE QUALITY GATES (before marking any step done)
- All tests pass: pytest (or equivalent)
- Lint clean: ruff check (Python)
- No hardcoded secrets anywhere in diff
- Exit condition from spec explicitly verified (not just "looks right")
- Checkpoint written if end of session

## TEST DESIGN RULE (adversarial, not confirmatory)
Tests must try to BREAK the code, not confirm it works. A test that always passes is dead weight.

For each module, identify its core claim. Then write the minimal test that would disprove that claim if the code were subtly wrong.

**Per-test checklist (answer yes to at least 3):**
1. Does it test the empty/null/zero/missing case?
2. Does it test the maximum/overflow/boundary case?
3. Does it test an invalid/malicious input?
4. Does it verify no side-effects on inputs (mutation detection)?
5. Does it test persistence round-trip (if stateful)?
6. Does it run the existing test suite as a regression gate against the agent's output?

**For the coding agent specifically:**
- Regression gate: agent's code change must not break any pre-existing test
- Diff minimality: agent must not touch code unrelated to the task
- Dependency gate: agent must not introduce new imports without instruction
- Negative-space: test what the agent must NOT do (refactor, rename, reorganize)
- Ambiguity resistance: underspecified tasks must not cause invariant violations
- Composition: sequential tasks must not conflict

**Adversarial test harness:** `tests/test_adversarial_orchestrator.py` — run with `make test-hard`

## GIT DISCIPLINE
- `git status` / `git diff` before any commit — read-only, always safe
- Never `git push --force`, `git reset --hard`, or `rm -rf` without user confirmation
- Commit messages: imperative mood, reference task step (e.g. "Add .aider.conf.yml [step 1.3]")
- Never amend published commits

## MATH / ESTIMATION WORK
- Define the quantity being estimated before writing any formula
- State assumptions explicitly
- Name numerical stability concerns
- Anchor to a trusted source (paper, library docs) — never assert from memory
- Present implementation options before locking in

## AIDER-SPECIFIC WORKFLOW
- Start session: `python scripts/prewarm_cache.py` → then `aider` (or `make dev`)
- In Aider chat: `/add <file>` only files you will edit this session
- When done with a file: `/drop <file>` to remove from context
- Reference files: `/read-only <file>` — cheaper, cached separately
- Check cache stats: run with `--no-stream` and read usage block in .aider.llm.history
- After session: `python scripts/token_report.py` to see cost breakdown
- If Aider suggests wrong edit: `/undo` then rephrase more specifically

## ANTHROPIC API NOTES (verified 2026-05-13)
- Model IDs: claude-sonnet-4-5, claude-haiku-3-5, claude-haiku-4-5, claude-opus-4-5
- Cache minimum: 1024 tokens for claude-sonnet-4-5
- Cache TTL default: 5 minutes (refreshed free on hit)
- Cache read cost: 10% of base input price
- Output tokens: 5x more expensive than input — keep responses concise
- Batch API: 50% discount, async (up to 24h), good for non-interactive work

## MODEL ROUTING (5 tiers — verified 2026-05-13)
Env keys for all backbone providers: see `.env.example` (copy to `.env`). LiteLLM list: https://aider.chat/docs/llms/other.html
Full decision matrix: docs/research/model_tiering.html
- T1 FREE:  gemini/gemini-2.5-flash (free tier, 44% polyglot) — explain/read only
- T2 CHEAP: deepseek/deepseek-chat ($0.14/MTok in, 70% polyglot) ← DEFAULT for coding
- T3 MID:   deepseek/deepseek-reasoner ($0.14/MTok, thinking mode, 74% polyglot)
            gemini/gemini-2.5-flash ($0.30/MTok, fast)
- T4 MAIN:  gemini/gemini-2.5-pro ($1.25/MTok, 79-83% polyglot, free tier)
            claude-sonnet-4-5 ($3/MTok, use when caching pays off or AWOS artifacts)
- T5 HEAVY: claude-opus-4-5 ($5 in / $25 out per MTok — see memories/repo/models_pricing_catalog.md) — only after T4 fails

ESCALATE when: T2 fails twice, multi-file architecture, AWOS artifact creation
STAY at T2 for: bug fixes, tests, refactoring, feature impl from spec
Makefile: make dev-cheap (T2), make dev-think (T3), make dev-mid (T3), make dev-pro (T4), make dev (T4 Sonnet)

# Agent Capability Gap Analysis — AWOS vs State of the Art (June 2026)

> **Purpose:** Honest answer to “AWOS feels nowhere near current agents — where are we lacking?”
> **Sources:** SWE-bench leaderboards, OpenHands SDK paper, mini-SWE-agent, Anthropic/Warp/Augment engineering posts, ReVeal / Live-SWE-agent / PARC papers, Aider/Cursor architecture writeups, and AWOS dogfooding runs (June 28, 2026).
> **Identity:** See `VISION.md` — AWOS optimizes quality × speed ÷ cost via a learnable kernel; coding is App #1, not the product mission.

---

## 1. Executive summary

**The feeling is correct.** AWOS today is a **strong research kernel** (routing, rewards, self-learning stores, worktrees) wrapped around a **weak execution scaffold** compared to agents that score 65–73% on SWE-bench Verified.

| Dimension | Top agents (2025–2026) | AWOS today |
|-----------|------------------------|------------|
| SWE-bench Verified | ~65–73% resolve rate | Not measured on current stack; dogfood shows partial success on docs + simple features |
| Agent loop | Multi-turn **tool loop** (bash, read, grep, edit, test) | **Plan once → fixed tasks → one patch per task** |
| Code exploration | Agent-driven grep/find/read during execution | Planner guesses; worker sees one file per task |
| Verification | **Tests are the gate** (pytest in container) | Syntax + fuzzy SEARCH match; tests optional / MCTS fallback |
| Edit reliability | Multiple formats + retries + apply models | SEARCH/REPLACE in free text; frequent parse failures |
| Delivery | PR/commit in repo you use | Worktree sandbox; merge often skipped |
| Learning | Various | **Stronger than most** (LinUCB, PromptEvolver, ToolSynth, `.awos/`) |

**Core diagnosis:** We invested in **meta-learning and cost control** (Stage 1–3) but the **inner loop** still looks like 2024 “planner + single-shot patch worker,” while SOTA is **ReAct + bash + test-driven iteration**. Our self-learning compounds on a scaffold that does not yet complete hard tasks reliably — so compounding saves tokens on failures and retries, not on mastery.

---

## 2. What “current agents” actually are (2026)

### 2.1 Benchmark anchor: SWE-bench Verified

SWE-bench Verified measures resolving real GitHub issues in isolated repos with hidden tests. Public scores (approximate, mid-2025–2026):

| System | SWE-bench Verified | Notes |
|--------|-------------------|--------|
| OpenHands SDK + Claude Sonnet 4.5 | **72.8%** | Event-sourced SDK, tool system, sandboxes ([arxiv 2511.03690](https://arxiv.org/html/2511.03690v1)) |
| Warp agent | **71%** | Single-agent, single-attempt; specialized grep/find/cat tools ([Warp blog](https://www.warp.dev/blog/swe-bench-verified)) |
| Augment Code (open scaffold) | **#1 open-source** at time of post | Sonnet 3.7 + o1 ensembler; MCP sequential_thinking for planning ([Augment](https://www.augmentcode.com/blog/1-open-source-agent-on-swe-bench-verified-by-combining-claude-3-7-and-o1)) |
| Claude 3.5 Sonnet scaffold | **49%** | Bash + Edit tools ([Anthropic engineering](https://www.engineering.fyi/article/raising-the-bar-on-swe-bench-verified-with-claude-3-5-sonnet)) |

**Implication:** “Agent power” in the market is measured by **autonomous issue resolution with tests**, not by generating markdown or single-file edits.

### 2.2 Dominant architecture pattern (converged)

Across OpenHands, Claude Code, Warp, mini-SWE-agent, and Aider, the winning pattern is:

```
while not done:
    model observes (stdout, errors, file snippets, test output)
    model chooses action (tool call or bash one-liner)
    environment executes
    append observation to context
```

**Not:** decompose goal into N static tasks upfront, then run each task with one LLM completion.

mini-SWE-agent deliberately uses **only bash** (~100 lines of agent loop) and scores competitively — proof that **loop quality + environment feedback** matters more than elaborate harness features ([mini-SWE-agent docs](https://mini-swe-agent.com/v2/)).

### 2.3 Tooling stack (what SOTA agents expose)

| Tool class | Examples | Why it matters |
|------------|----------|----------------|
| **Shell** | `bash`, stateless `subprocess.run` | Run tests, git, grep, install deps |
| **Read / navigate** | `read_file`, `grep`, `find`, line-limited `cat` | Explore repo without loading 1751-line files |
| **Edit** | SEARCH/REPLACE, `old_string`/`new_string`, unified diff, whole-file | Apply changes with retries |
| **Plan / TODO** | TODO list, MCP sequential_thinking | Inspectable multi-step reasoning |
| **Test** | `pytest` in sandbox | Objective success signal |

OpenHands SDK adds: event-sourced state, MCP, condenser (history compression), security risk on actions ([OpenHands docs](https://docs.openhands.dev/sdk/arch/overview)).

### 2.4 Verification trend (research)

Recent papers stress **verification as a first-class objective**, not an afterthought:

- **ReVeal** — RL that optimizes self-verification; multi-turn test construction ([arxiv 2506.11442](https://export.arxiv.org/pdf/2506.11442))
- **Live-SWE-agent** — runtime tool synthesis + step reflection ([arxiv 2511.13646](https://www.arxiv.org/pdf/2511.13646v2))
- **PARC** — hierarchical agents + self-assessment for long horizons ([arxiv 2512.03549](https://arxiv.org/pdf/2512.03549))

AWOS has pieces (MCTS + pytest in `mcts_search`, IntegrationReviewer, post_mortem) but they are **not the default path** for every coding task.

---

## 3. AWOS architecture today (honest map)

### 3.1 Execution path (what actually runs on `awos worker start`)

```
Goal → [Clarifier] → [Planner/CheapPlanner] → [PlanReviewer]
  → Task list (file + action per task)
  → For each task:
      read ONE file (or empty for create)
      Worker: one LLM call → SEARCH/REPLACE or CONTENT block
      Verifier: fuzzy match + syntax (+ integration heuristics)
      Optional: MCTS, critic, tool synth on failure
  → IntegrationReviewer (Sonnet reads git diff)
  → Learn: reward_store, skills, error_patterns
```

**Strengths:**
- Escalation ladder + LinUCB + budget ledger
- Worktree isolation + stagnation breaker
- Goal clarification + plan review
- Self-learning: PromptEvolver, LiveToolSynthesizer, ScaffoldEvolver, vector memory, skill library
- LiveRenderer (good UX when wired)

**Weaknesses (vs SOTA loop):**
- No per-turn **agent chooses next action** — planner fixes the sequence
- Worker does not call **bash, grep, or read other files** during a task
- **One primary edit** per worker attempt; multi-edit tasks fail or decompose badly
- Verifier checks **text match**, not **tests pass** by default
- Success often means “patch applied” not “feature works”

### 3.2 What we proved in dogfooding (June 28, 2026)

| Job | Result | Lesson |
|-----|--------|--------|
| Search/planning audit doc | **4/4** | Create + append docs works |
| Model router refactor | **2/2** claimed; import partial | Multi-file integration unreliable |
| Cost CLI `--costs` build | **3/3** claimed; functions mangled | Integration false positives |
| Cost CLI fix | Partial; missing `_calculate_awos_costs` | Task order / cross-task dependencies broken |
| Visible rerun | **4/4** in worktree; main unchanged | **No merge = no product value** |

These align with scaffold limits, not model limits.

---

## 4. Gap matrix (detailed)

### 4.1 Agent loop & autonomy

| Capability | SOTA | AWOS | Gap severity |
|------------|------|------|----------------|
| Multi-turn tool loop | Core | Fixed task batch | **Critical** |
| Bash execution | Core | Not in worker loop | **Critical** |
| Agent picks files to read | Core | Planner assigns one `file` | **High** |
| TODO / replan during execution | Common | Replan/decompose on failure only | **High** |
| Stateful shell vs stateless bash | Both used | Neither in worker | Medium |

### 4.2 Search & context

| Capability | SOTA | AWOS | Gap severity |
|------------|------|------|----------------|
| grep/find during task | Core | SymbolIndex at plan time; RAG in worker prompt | **High** |
| Embedding codebase index | Cursor, OpenHands | VectorMemory exists; underused in loop | Medium |
| Repo map (signatures only) | Aider | SymbolIndex similar; not fed like Aider repo-map | Medium |
| Line-limited file read | Warp, Claude Code | Full file or truncate in worker | Medium |

### 4.3 Editing

| Capability | SOTA | AWOS | Gap severity |
|------------|------|------|----------------|
| Multiple edits per turn | Claude Code, OpenHands | One SEARCH/REPLACE per attempt | **Critical** |
| Architect + editor split | Aider | Single model per attempt | High |
| Apply model / fuzzy apply | Cursor | Verifier fuzzy tiers; still brittle | High |
| Format per model | Aider `--edit-format` | Mostly one prompt shape | Medium |

### 4.4 Verification

| Capability | SOTA | AWOS | Gap severity |
|------------|------|------|----------------|
| pytest as default gate | SWE-bench agents | Syntax-first; pytest in MCTS/verifier optional | **Critical** |
| Run relevant tests only | Standard | Test discovery exists; not wired to every task | **High** |
| Self-generated tests | ReVeal, Live-SWE | Limited | High |
| Integration review | Some | IntegrationReviewer (LLM on diff) — catches issues **after** “success” | Medium (good but late) |

### 4.5 Planning

| Capability | SOTA | AWOS | Gap severity |
|------------|------|------|----------------|
| Plan from repo exploration | Claude Code, Warp | Planner uses context dict; cheap planner | **High** |
| Atomic tasks (one edit) | Best practice | Planner often bundles “import + instantiate + replace” | **High** |
| Path accuracy | Implicit in grep | File resolver added; planner still shortens paths | Medium |
| Human plan review | Optional | PlanReviewer + auto-approve | Low (feature exists) |

### 4.6 Infrastructure & product

| Capability | SOTA | AWOS | Gap severity |
|------------|------|------|----------------|
| Event-sourced replay | OpenHands V1 | Traces/spans; not full event log | Medium |
| Docker sandbox per task | SWE-bench, OpenHands | Git worktree (good) but no containerized test env | **High** for benchmarks |
| MCP | OpenHands, Augment | Limited / disabled in some evals | Medium |
| Merge to main workflow | Implicit | Manual; easy to forget | **High** for dogfooding |
| Published SWE-bench score | All leaders | None on current worker loop | **Critical** for credibility |

### 4.7 Where AWOS is ahead (or unique)

| Capability | AWOS | Typical SOTA agent |
|------------|------|-------------------|
| Cost routing + budget ledger | **Strong** | Often ad hoc |
| LinUCB / reward store | **Strong** | Rare in coding agents |
| PromptEvolver + ToolSynth + ScaffoldEvolver | **Strong** | Hermes skills; less RL/bandit |
| Mandatory clarify + plan review | **Unique** | Optional everywhere else |
| PEI / compounding narrative | **Unique** | Seat-based pricing |
| Worktree + `.awosignore` | **Good** | Docker sandboxes |

**Strategic note:** Differentiation is real on **economics and learning**, but users feel “agent power” from **loop + tests + exploration** first.

---

## 5. Root causes (why dogfooding hurt)

1. **Wrong abstraction:** “Task = one file + one action” is too weak for refactoring; SOTA treats “task” as one **turn** in a loop, not one patch.

2. **Verification theater:** `VERIFIED: Change applied` means fuzzy string replace succeeded, not tests pass or imports resolve.

3. **Planner–worker split without shared exploration:** Planner does not leave a **trace of files read**; worker cannot recover from wrong plan.

4. **Cheap planner on hard goals:** Gemini Flash plans multi-step refactors poorly; worker gets GPT-4o-mini / DeepSeek with bad SEARCH blocks.

5. **Worktree success ≠ product:** Features built in sandbox never merged; main still missing `--costs`.

6. **Compounding on the wrong layer:** Learning optimizes model routing and prompts, not **tool policies** (when to grep, when to pytest).

---

## 6. Comparison to specific open-source systems

| System | Lines of core loop | Primary interface | Best for |
|--------|-------------------|-------------------|----------|
| **mini-SWE-agent** | ~100 | Bash only | Benchmarks, hackability |
| **SWE-agent** | Larger | Custom tools + env | Tool experiments |
| **OpenHands SDK** | Framework | Tools + events + workspace | Production deployments |
| **Aider** | Mature | SEARCH/REPLACE chat + repo map | Terminal pair programming |
| **AWOS** | orchestrator ~1750+ | Planner → Worker → Verifier | Cost-aware learning kernel |

AWOS is closer to **“batch job orchestrator”** than **“interactive coding agent.”**

---

## 7. Prioritized roadmap to close gaps

Aligned with `VISION.md` Stage 1 (one excellent worker) — **execution scaffold first**, then compound learning pays off.

### P0 — Make the inner loop SOTA-shaped (4–8 weeks)

1. **ReAct worker mode** — Replace “one shot patch” with `while` loop: `read | grep | bash | edit | test` tools, max turns, budget cap.
2. **Test-first verification** — After every edit (or every N turns): run `pytest` for touched module; failure → observation back to model.
3. **Bash tool** — Stateless `subprocess.run` in worktree (mini-SWE style).
4. **Atomic edit tool** — Structured `edit_file(old, new)` or multiple SEARCH blocks per turn.
5. **SWE-bench smoke** — 10-instance subset on new loop; publish score internally.

### P1 — Planning & context (2–4 weeks)

6. **Exploration phase** — Before plan: agent runs grep/find (or use symbol index) and writes `plan_context.json`.
7. **Enforce atomic plans** — Refactoring decomposer in planner (started); extend to “one semantic edit per task.”
8. **Repo map injection** — Aider-style signature map in planner + worker context.

### P2 — Product & proof (2–3 weeks)

9. **Merge workflow** — `awos worktree merge` + checklist after green tests.
10. **Live proof on SWE-bench subset** — `scripts/demo_swebench_smoke.sh` + `docs/swebench_smoke_proof.md`.
11. **Integration tests as CI gate** — e.g. `awos stats --costs` must run after agent changes CLI.

### P3 — Leverage existing kernel (ongoing)

12. Wire **reward_store** to test pass/fail, not just worker success bit.
13. **LiveToolSynthesizer** for repeated grep/test helpers.
14. **MCP** for sequential_thinking-style planning (Augment pattern).

**Do not defer P0 in favor of more meta-learning** — compounding without a strong loop repeats June 28 dogfood failures.

---

## 8. What “good enough for Stage 1” looks like

Concrete bar (not SWE-bench SOTA):

| Scenario | Target |
|----------|--------|
| New doc from 3 source files | 4/4 tasks, <5 min, <$0.01 (already achieved) |
| Add CLI flag + handler | 1 session, tests pass, merged to main |
| Small refactor (extract module) | Completes without mangled functions |
| SWE-bench Verified (50 instances) | **>30%** resolve (honest baseline) |
| Same goal second run | **Lower cost** via cache/skills (compounding) |

---

## 9. References

### Papers & reports
- OpenHands Software Agent SDK — [arxiv 2511.03690](https://arxiv.org/html/2511.03690v1)
- ReVeal: Self-Evolving Code Agents via Reliable Self-Verification — [arxiv 2506.11442](https://export.arxiv.org/pdf/2506.11442)
- Live-SWE-agent: Self-Evolve on the Fly — [arxiv 2511.13646](https://www.arxiv.org/pdf/2511.13646v2)
- PARC: Self-Reflective Coding Agent — [arxiv 2512.03549](https://arxiv.org/pdf/2512.03549)
- Self-Evolving Agents Survey — [arxiv 2507.21046](https://arxiv.gg/abs/2507.21046)

### Engineering posts
- Warp SWE-bench 71% — [warp.dev/blog/swe-bench-verified](https://www.warp.dev/blog/swe-bench-verified)
- Augment SWE-bench scaffold — [augmentcode.com/blog](https://www.augmentcode.com/blog/1-open-source-agent-on-swe-bench-verified-by-combining-claude-3-7-and-o1)
- Anthropic Claude 3.5 Sonnet on SWE-bench — [engineering.fyi](https://www.engineering.fyi/article/raising-the-bar-on-swe-bench-verified-with-claude-3-5-sonnet)
- OpenAI Agents guide — [OpenAI practical guide PDF](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)

### Open source
- [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent) — bash-only loop
- [OpenHands SDK](https://docs.openhands.dev/sdk/arch/overview) — events, tools, workspace
- [Aider edit formats](https://github.com/Aider-AI/aider/blob/main/aider/website/docs/more/edit-formats.md)

### Internal
- Superseded but useful history: `docs/research/AWOS_VS_TOP_AGENTS_COMPARISON.md`
- Dogfood evidence: sessions `rs_edc14dadbe9e`, `rs_0e80deb1f459`, `rs_c05601d89be8` (June 28, 2026)
- `docs/research/sota_improvements.md` — earlier P0–P3 list (partially implemented)

---

## 10. Bottom line

**We are not lacking intelligence in the kernel.** We are lacking a **2026-standard agent loop**: explore → edit → **run tests** → repeat, with bash and grep as first-class tools.

Current AWOS wins on **cost, learning, and process**; it loses on **autonomy, verification rigor, and edit reliability** — exactly what users feel when comparing to Cursor, Claude Code, or OpenHands.

**Highest-leverage next move:** Implement **P0 ReAct + test-gated worker** inside the existing orchestrator (keep learning loop, worktrees, routing). Until then, dogfooding will keep producing worktree artifacts that almost work — which is what we saw with `--costs`.

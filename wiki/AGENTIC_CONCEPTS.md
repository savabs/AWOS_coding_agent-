---
title: "Agentic Workflow Concepts — Reference"
tags:
  - doc/wiki
  - topic/agentic
  - topic/architectures
  - topic/reasoning
last_updated: "2026-05-13"
---

# Agentic Workflow Concepts

> **Purpose:** Project-agnostic reference for the core concepts, architectures, and patterns
> that govern how LLM agents reason, act, remember, and improve. Use this as a vocabulary and
> decision guide — not a tutorial. Every concept links to the AWOS protocol that operationalises it.
>
> **Rule:** When picking an architecture or pattern for a new project, consult this document
> first. Write the choice into the project's `docs/specs/` doc with a one-line rationale.
> Undocumented architecture choices are ADR violations.

---

## Table of Contents

1. [Agent Architectures](#1-agent-architectures)
2. [Tool Use Patterns](#2-tool-use-patterns)
3. [Memory Systems](#3-memory-systems)
4. [Reasoning Patterns](#4-reasoning-patterns)
5. [Evaluation & Self-Improvement](#5-evaluation--self-improvement)
6. [Multi-Agent Patterns](#6-multi-agent-patterns)
7. [Context Window Management](#7-context-window-management)
8. [Task Decomposition Strategies](#8-task-decomposition-strategies)

---

## 1. Agent Architectures

An **agent architecture** defines the loop through which an agent perceives, reasons, and acts.
Architecture choice is the highest-leverage decision in any agentic project; it determines
blast radius, debuggability, and the ceiling for autonomous capability.

### 1.1 ReAct (Reason + Act)

**Pattern:** Interleave Thought → Action → Observation in a single context window.
Each step is visible in the trace: the agent writes its reasoning before calling a tool,
reads the result, and reasons again.

```
Thought: I need to look up the current error.
Action: search_files("ImportError transformers")
Observation: found in train.py:42
Thought: The import is wrong. I'll fix it.
Action: replace_string_in_file(...)
```

**When to use:** Default for single-agent task execution. Works for tasks with ≤10 tool calls.
**Failure mode:** Context grows linearly; reasoning quality degrades on long chains.
**AWOS rule:** All ReAct traces must be checkpointed if they exceed 5 tool calls. See [[CHECKPOINT_TEMPLATE]].

---

### 1.2 Plan-and-Execute

**Pattern:** Separate the *planner* (creates a full task graph upfront) from the *executor*
(runs each step sequentially or in parallel). The planner sees the whole goal; the executor
sees only its current step.

```
Planner → [step1, step2, step3, step4]
                ↓          ↓
           Executor1   Executor2  (parallel where independent)
```

**When to use:** Long-horizon tasks where upfront structure is knowable. Maps cleanly onto
the [[AWOS]] Research → Spec → Task triad — the planner produces the spec.
**Failure mode:** Planner assumptions go stale mid-execution if environment changes.
**Mitigation:** Executor reports back to planner after each step; planner re-evaluates if
a step fails or returns unexpected output.

---

### 1.3 Reflexion

**Pattern:** After each attempt, the agent writes a textual self-critique of *what went wrong
and why*, then retries with that critique prepended to context. Critique accumulates across
attempts as an "episodic memory of failure."

```
Attempt 1 → fail
Reflection: "I called the wrong tool because I confused X with Y."
Attempt 2 (with reflection prepended) → improved
```

**When to use:** Tasks with clear success/fail signal where the failure mode is
diagnosable from the output (e.g., code execution, test pass/fail).
**AWOS mapping:** Operationalised by the [[DEBUG_PROTOCOL]] two-failed-attempt rule.
The debug log IS the reflexion memory.
**Failure mode:** Reflection can become self-reinforcing noise if the failure signal is
ambiguous. Limit to 3 reflection cycles before escalating to human.

---

### 1.4 LATS (Language Agent Tree Search)

**Pattern:** Monte Carlo Tree Search over the action space. At each node, the agent
generates multiple candidate actions (branching factor k), evaluates them with a value
function, and selects the most promising path.

```
State S
├── Action A1 → S1 (value: 0.8)
├── Action A2 → S2 (value: 0.3)  ← pruned
└── Action A3 → S3 (value: 0.6)
        └── Action A4 → S4 (value: 0.9) ← best path
```

**When to use:** Tasks with a well-defined reward signal and expensive-but-reversible
actions (e.g., code generation benchmarks, puzzle solving). Not suitable for tasks where
actions have irreversible side effects.
**Compute note (P1):** LATS with k=5, depth=4 = 625 LLM calls. Never run locally.
Push to Kaggle or cloud.
**Failure mode:** Value function must be well-calibrated; poor value estimates produce
search that looks busy but finds nothing.

---

### 1.5 Critic-Actor (Separate Roles)

**Pattern:** Two specialised agents: **Actor** generates outputs; **Critic** scores them
and provides structured feedback. Actor revises based on critic feedback. Neither role
bleeds into the other.

```
Actor → draft output
Critic → {score: 0.4, issues: ["missing error handling", "wrong API"], suggested_edits: [...]}
Actor → revised output (incorporating critique)
Critic → {score: 0.9, issues: []}  → accept
```

**When to use:** Code review, report generation, any task where quality is hard to
self-assess but easy to verify externally.
**AWOS mapping:** The Critic role maps to the eval gate in `src/evaluation/gate.py`.

---

### 1.6 Self-Ask

**Pattern:** Agent decomposes a complex question by asking sub-questions and answering
them before tackling the main question. Sub-questions are explicit in the trace.

```
Q: "What is the best architecture for this RL task?"
Sub-Q1: "Is the state space discrete or continuous?" → continuous
Sub-Q2: "Is the reward sparse?" → yes
Sub-Q3: "What sample efficiency do we need?" → high
Final: "Given continuous state, sparse reward, high efficiency → SAC with PER"
```

**When to use:** Research/analysis tasks. Not suitable for pure execution.

---

### Architecture Selection Guide

| Task type | Preferred architecture | Fallback |
|---|---|---|
| Single-step tool use | ReAct | — |
| Long-horizon structured task | Plan-and-Execute | ReAct with checkpointing |
| Repeated attempts with feedback | Reflexion | Debug Protocol |
| Search over large action space | LATS | Beam search over plans |
| Output quality refinement | Critic-Actor | Reflexion |
| Complex research/analysis | Self-Ask + Plan-and-Execute | ReAct |

---

## 2. Tool Use Patterns

### 2.1 Tool Call Anatomy

Every tool call must conform to the **Tool Result Envelope** defined in [[GLOSSARY]]:

```python
{
  "success": bool,
  "text": str,           # human-readable summary
  "data": dict,          # structured output for downstream use
  "error": str | None,   # if success=False
  "latency_ms": float
}
```

Non-conforming tool results must be normalised at the boundary before the agent processes them.

---

### 2.2 Tool Chaining

**Pattern:** Output of tool T1 becomes input to tool T2. The chain must be explicit and
each link must be independently inspectable.

```
file_search("train.py") → ["/src/train.py"]
                                ↓
read_file("/src/train.py", 40, 60) → code block
                                ↓
grep_search("ImportError") → line 42
```

**Rules:**
- Never pass raw tool output to the next tool without inspecting it first.
- If any link in the chain returns `success=False`, abort the chain and invoke
  error recovery (§2.4) — do not continue with partial data.
- Document chains longer than 3 hops in the task file.

---

### 2.3 Tool Selection Heuristics

| Signal | Preferred tool | Avoid |
|---|---|---|
| Know exact filename | `file_search` | `grep_search` |
| Know exact string | `grep_search` | `semantic_search` |
| Conceptual / contextual | `semantic_search` | `grep_search` |
| Reading large file sections | Single large `read_file` call | Many small reads |
| Executing commands | `execution_subagent` | `run_in_terminal` (unless full output needed) |

**Cost discipline:** Each unnecessary tool call consumes context and latency.
Choose the narrowest tool that answers the question.

---

### 2.4 Tool Error Recovery

```
Tool call
    ↓
success=False?
    ├── Retry #1: check parameters (path, encoding, permissions)
    ├── Retry #2: alternative tool or approach
    └── Retry #3: STOP — invoke [[DEBUG_PROTOCOL]] §Step 0
```

**Hard rule (P3):** After 2 failed tool calls for the same intent, do not try a 3rd variant.
Research first. Write findings. Then fix.

**Common failure patterns and root causes:**

| Error | Root cause | Fix |
|---|---|---|
| File not found | Wrong relative vs absolute path | Always use absolute paths |
| Regex no match | Escape issue or casing | Test regex in isolation first |
| Subprocess timeout | Blocking on stdin | Add `--no-interactive` flag |
| API rate limit | Too many parallel calls | Serialize; add retry with backoff |

---

### 2.5 Idempotent Tool Design

Tools that modify state MUST be idempotent: calling them twice with the same arguments
produces the same result as calling them once. Non-idempotent tools (e.g., database writes,
API posts) must be guarded with a check-before-write pattern:

```python
# check-before-write
if not already_exists(target):
    write(target)
```

---

### 2.6 Tool Registry Discipline

- Register every tool with a schema (name, description, parameters, return envelope).
- Tools are not called by name string — they are dispatched through the registry.
- Tool descriptions must be unambiguous: two tools with overlapping descriptions will
  cause the agent to guess. Resolve ambiguity at registration time.

---

## 3. Memory Systems

The [[GLOSSARY]] defines the four memory types. This section covers *how to use each*
in practice and when each is appropriate.

### 3.1 Working Memory

**What it is:** The current context window — everything the agent "has in mind" right now.
**Capacity:** Finite (model-dependent; typically 8K–200K tokens).
**Lifespan:** One session or one task run.

**Rules:**
- Working memory is the most expensive resource in an agentic system. Treat it like RAM,
  not disk.
- Summarise before appending. A 2000-token observation should become a 100-token structured
  note unless raw fidelity is required downstream.
- At every context boundary (tool call, step boundary), prune working memory aggressively.
  See §7 (Context Window Management).

---

### 3.2 Episodic Memory

**What it is:** An immutable timestamped log of events — what happened, when.
**Lifespan:** Persists across sessions; never edited (immutable after write).
**AWOS canonical files:** `data/logs/chat_checkpoint_YYYY-MM-DD.md`, `docs/debug_log.md`

**Rules:**
- Write *after* the event, not before.
- Include: what changed, what failed, what decision was made, confidence level.
- Episodic entries are the raw material for extracting semantic memory.

---

### 3.3 Semantic Memory

**What it is:** Named, structured facts the agent knows — project architecture, API shapes,
known-good patterns.
**Lifespan:** Long-lived; updated when facts change.
**AWOS canonical files:** `/memories/repo/<project>_structure.md`, `docs/kaggle_runbook.md`

**Rules:**
- Each fact has exactly one canonical home (Single-Owner Rule — [[GLOSSARY]]).
- Facts carry a confidence level: `verified` / `inferred` / `uncertain`.
- Do not promote `inferred` to `verified` without evidence from at least one successful run.
- Stale semantic memory is worse than no memory. Review facts after every significant failure.

---

### 3.4 Procedural Memory

**What it is:** Step-by-step instructions for recurring tasks — how to push to Kaggle,
how to run the debug protocol, how to checkpoint.
**Lifespan:** Long-lived; updated when the procedure changes.
**AWOS canonical files:** `protocols/*.md`, `docs/<platform>_runbook.md`

**Rules:**
- Procedures must be numbered, atomic, and executable without interpretation.
- "Do the usual thing" is not a procedure. Write out the exact commands.
- When a procedure breaks, update it in the same session. A procedure that quietly fails is
  more dangerous than no procedure.

---

### 3.5 Memory Architecture Decision Table

| Question | Use |
|---|---|
| "What happened last session?" | Episodic — checkpoint file |
| "What does this module do?" | Semantic — project structure file |
| "How do I push to Kaggle?" | Procedural — runbook |
| "What am I doing right now?" | Working — context window |
| "What reward did the last episode get?" | Episodic — episode log |
| "What hyperparameters work for this task?" | Semantic — config / runbook |

---

## 4. Reasoning Patterns

### 4.1 Chain-of-Thought (CoT)

**Pattern:** Agent writes step-by-step reasoning before producing a final answer.
Each intermediate step is explicit and inspectable.

```
Step 1: The error says "CUDA out of memory."
Step 2: The batch size is 32, embeddings are 768-dim, sequence length 512.
Step 3: Memory = 32 × 512 × 768 × 4 bytes = ~50 MB per layer. With 12 layers ≈ 600 MB.
Step 4: GTX 1650 has 4 GB VRAM. Something else is resident.
Step 5: Check if the model is loaded twice.
```

**When to use:** Any non-trivial decision. Especially: debugging, architecture selection,
capacity planning.
**AWOS rule:** Decisions that required multi-step reasoning must be checkpointed (Write-Gate, P5).
Reasoning that lives only in context is lost at session end.

---

### 4.2 Tree-of-Thought (ToT)

**Pattern:** At each reasoning step, generate multiple candidate next thoughts (branching),
evaluate each, and continue only the most promising branch. Prune weak branches early.

```
Problem
├── Approach A: gradient-based → likely slow given sparse reward
├── Approach B: evolutionary → high variance, hard to debug  ← pruned
└── Approach C: model-based → requires good world model, justified by data volume ← continue
        ├── C1: MBPO → well-studied, has known failure modes
        └── C2: Dreamer → more sample-efficient but complex  ← continue
```

**When to use:** Design decisions with multiple viable options. Architecture choices.
Debugging when root cause is ambiguous.
**Cost:** O(k^d) reasoning traces (k branches, d depth). Expensive — use selectively.

---

### 4.3 Scratchpad

**Pattern:** Agent maintains a persistent working notes section that it can freely
read/write during a task. Separate from the final output. Allows temporary state that
would otherwise consume context if kept in-band.

```
[SCRATCHPAD]
- tried grep for "ImportError" → found in 3 files
- train.py:42 is the main suspect (direct import, not behind try/except)
- requirements.txt shows transformers==4.28 but code uses 4.35 API
- hypothesis: version mismatch
[/SCRATCHPAD]
```

**AWOS mapping:** Session memory files (`/memories/session/`) act as the scratchpad.
Write-Gate applies: if it's important, it moves to a real file before session end.

---

### 4.4 Least-to-Most Prompting

**Pattern:** Decompose a hard problem into subproblems ordered from easiest to hardest.
Solve each subproblem, then use those answers as context for the harder ones.

**When to use:** Complex coding tasks. Novel architecture design. Research synthesis.
**Why it works:** Easier subproblems are solved without error, providing reliable anchors.
Hard subproblems have more accurate context and fewer guesses.

---

### 4.5 Self-Consistency

**Pattern:** Generate multiple independent reasoning traces for the same problem
(via temperature > 0), then take the majority answer. Filters out reasoning errors
that affect single traces.

**When to use:** High-stakes one-shot decisions (irreversible actions, architecture ADRs).
**Cost:** N × single-call cost. Use N=3 minimum, N=5 for critical decisions.
**Failure mode:** All traces make the same systematic error. Use only when errors are
expected to be random, not correlated.

---

## 5. Evaluation & Self-Improvement

### 5.1 Reward Signal Taxonomy

| Reward type | Definition | Example |
|---|---|---|
| **Sparse** | Signal only at episode end | Pass/fail on a test suite |
| **Dense** | Signal at every step | Code coverage delta per line changed |
| **Shaped** | Sparse + hand-designed intermediate rewards | Partial credit for correct imports |
| **Learned** | Reward model trained on human preferences | RLHF preference score |
| **Intrinsic** | Agent-generated reward (curiosity, novelty) | Prediction error of a world model |

**Selection rule:** Use the sparsest signal that produces stable learning. Dense/shaped
rewards introduce bias; their design errors compound. Only add density when the sparse
signal is too noisy to learn from.

---

### 5.2 Critic-Actor Loop (Operational)

See §1.5 for the architectural pattern. Operational rules:

1. **Critic must score before Actor revises.** Actor never self-approves.
2. **Critic feedback must be structured.** Free-form "could be better" is not actionable.
   Minimum fields: `{score: float, blocking_issues: list, suggestions: list}`.
3. **Score threshold before accept.** Define the acceptance bar in the spec, not at runtime.
   Default: score ≥ 0.8, zero blocking issues.
4. **Maximum revision cycles = 3.** If score does not improve after 3 cycles, escalate.
   The problem is the task spec, not the actor.

---

### 5.3 Self-Reflection Protocol

**Trigger:** After any significant failure (a test suite regression, a Kaggle kernel crash,
a reward collapse).

**Steps:**
1. Write the failure state as a concrete observation (exact error, exact metric drop).
2. List hypotheses in order of prior probability.
3. For each hypothesis: what evidence would confirm or deny it?
4. Gather that evidence using the narrowest tool available.
5. Update beliefs. Write a finding. Only then write a fix.

**AWOS mapping:** This is [[DEBUG_PROTOCOL]] §Steps 1–4. Do not skip steps.

---

### 5.4 Evaluation Gates

**Definition:** A boolean gate that blocks progression from one training stage to the next
unless a quantitative criterion is met.

```
Stage N training → eval gate → pass? → Stage N+1
                                 ↓ fail
                            Diagnosis + fix → retry Stage N
```

**Rules:**
- Gates are defined in the spec *before* training begins, not adjusted after seeing results.
- Gate criteria must be falsifiable and reproducible (same data split, same metric).
- A gate that never fails is not a gate — it is a checkpoint alias. If a gate always passes,
  raise the bar.

**AWOS canonical file:** `research/specs/eval_gate_spec.md`

---

### 5.5 Ratchet State

**Definition:** A mechanism that tracks the best-known metric and refuses to commit a
model checkpoint that regresses it. Named after the ratchet tool — can advance, cannot retreat.

```python
if new_score > ratchet_state["best_score"]:
    save_checkpoint()
    ratchet_state["best_score"] = new_score
else:
    discard_checkpoint()
    log("regression — not saving")
```

**AWOS canonical file:** `data/ratchet_state.json` (in entity_ai).
Ratchet state is **semantic memory** — update it after every successful training run.

---

### 5.6 Reward Shaping Safety Rules

1. **Document every shaping term** with the intended behaviour it induces.
2. **Run ablation:** verify the model learns the right behaviour without the shaping term
   before adding it. Shaping that masks a data problem will haunt you.
3. **Monitor for reward hacking:** periodically sample trajectories and check that
   high-reward behaviour is actually the desired behaviour.
4. **Shaping term budget:** no more than 3 auxiliary terms in production. More terms = more
   interaction effects = harder to debug.

---

## 6. Multi-Agent Patterns

### 6.1 Orchestrator-Worker

**Pattern:** One **orchestrator** agent manages the task plan and dispatches subtasks.
Multiple **worker** agents each own one subtask. Workers are stateless with respect to
each other — all state flows through the orchestrator.

```
Orchestrator
├── Worker A: "fetch and preprocess data"     → returns structured output
├── Worker B: "run training experiment"       → returns metrics
└── Worker C: "write evaluation report"       → depends on A+B outputs
```

**Rules:**
- Workers never communicate directly — orchestrator is the sole message bus.
- Each worker receives a self-contained task: all required context is in the dispatch,
  never assumed from prior conversation.
- Orchestrator validates worker output before passing it downstream.

**When to use:** Long pipelines with parallel steps. CI/CD-style automation.

---

### 6.2 Debate / Adversarial Review

**Pattern:** Two agents take opposing stances on a decision. A third agent (judge) evaluates
the debate and produces a verdict.

```
Agent A (proposer): "We should use LoRA rank 16."
Agent B (critic):   "Rank 16 is too large for this dataset — overfitting risk."
Agent A (rebuttal): "The validation curve shows no overfitting up to epoch 8."
Judge:              "Agent A's rebuttal is supported. Proceed with rank 16, monitor at epoch 4."
```

**When to use:** High-stakes irreversible decisions (architecture, data pipeline design).
**Cost:** 3x single-agent cost minimum. Reserve for genuinely contested decisions.
**Failure mode:** Agents do not actually disagree — both produce safe centrism.
Force adversarial stance explicitly in the prompt.

---

### 6.3 Role Specialisation

**Pattern:** Assign agents to roles that match their strengths or provide separation of concerns.
Common roles:

| Role | Responsibility |
|---|---|
| **Planner** | Breaks down goals into steps |
| **Researcher** | Gathers external knowledge; writes research docs |
| **Implementer** | Writes code from specs |
| **Tester** | Writes and runs tests; reports regressions |
| **Auditor** | Reviews for security, correctness, style violations |
| **Archivist** | Maintains memory files; writes checkpoints |

**AWOS rule:** Roles must be defined in the project spec. Agents without explicit roles
drift into doing everything, which means doing nothing well.

---

### 6.4 Shared Memory vs Message Passing

| Mechanism | Use when | Risk |
|---|---|---|
| Shared file-based memory | Agents run sequentially or with locking | Race conditions if parallel |
| Message queue | Agents run in parallel, async | Message ordering bugs |
| Direct prompt injection | Orchestrator passes context inline | Context bloat |

**Default for AWOS projects:** Shared file-based memory (canonical files) with sequential
agent execution. Only introduce message queues if true parallelism is required and justified.

---

## 7. Context Window Management

The context window is the agent's working memory. It is finite, ordered, and read left-to-right.
Managing it well is the difference between an agent that degrades on long tasks and one that does not.

### 7.1 Context Budget Framework

```
Total context budget = C tokens
├── System prompt:          ~500–2000 tokens  (fixed)
├── Task instructions:      ~500–1000 tokens  (per task)
├── Retrieved knowledge:    ~2000–5000 tokens (variable — MUST be controlled)
├── Tool results:           ~1000–3000 tokens (per call — MUST be compressed)
├── Reasoning trace:        ~1000–4000 tokens (grows — MUST be pruned)
└── Output buffer:          ~500–2000 tokens  (reserve)
```

**Rule:** If any single tool result exceeds 1000 tokens, compress it before appending to context.
If the full reasoning trace exceeds 3000 tokens, summarise the oldest turns.

---

### 7.2 Chunking

**Definition:** Splitting large documents into fixed or semantic units that fit within the
context budget.

| Chunking strategy | When to use |
|---|---|
| Fixed-size (N tokens) | Logs, raw data, uniform text |
| Semantic (paragraph/section) | Structured docs, code files |
| Hierarchical (summary + detail) | Long documents where only sections are relevant |
| Sliding window with overlap | When context continuity across chunks matters |

**Rule:** Chunk at semantic boundaries (paragraph, function, section) unless the content
is structurally flat. Fixed-size chunking of code splits logic mid-function and corrupts
the semantics.

---

### 7.3 Summarisation

**When to summarise:** After a tool result has served its purpose. After each completed task step.
Before writing to episodic memory (never write raw tool output to a checkpoint).

**Summarisation levels:**

| Level | Output size | Use |
|---|---|---|
| Headline | 1 sentence | Status update, log entry |
| Structured | 5–10 bullet points | Checkpoint entry, step completion note |
| Compressed transcript | 20–50% of original | Multi-turn history compression |

---

### 7.4 Retrieval (RAG Pattern)

**Pattern:** Instead of loading all knowledge into context, retrieve only what is relevant
to the current step. Retrieval uses vector similarity, keyword search, or hybrid.

```
Step context (query) → retriever → top-K chunks → inject into prompt
```

**Rules:**
- Retrieve lazily — only when the current step actually needs external knowledge.
- Re-rank retrieved chunks by relevance to the *specific sub-question*, not the full task.
- Never inject retrieved text verbatim if it exceeds 500 tokens — summarise first.
- Log what was retrieved and why in the task file. Debugging retrieval failures is hard
  without provenance.

---

### 7.5 Context Compression Anti-Patterns

| Anti-pattern | Problem |
|---|---|
| Keeping all raw tool outputs | Context exhaustion by step 8 |
| Summarising too aggressively early | Losing details needed 3 steps later |
| Re-reading the same file repeatedly | Context pollution; use caching |
| Injecting entire project history | Agent attends to irrelevant prior sessions |

---

## 8. Task Decomposition Strategies

### 8.1 Why Decomposition Matters

A task that cannot be broken down is either trivially small or dangerously undefined.
Decomposition is how the agent creates verifiable intermediate states —
each sub-task is a hypothesis about the path to the goal, and each completion is evidence.

---

### 8.2 Hierarchical Decomposition

**Pattern:** Recursive breakdown — goal → stages → tasks → atomic steps.
Each level has its own exit condition.

```
Goal: "Train entity AI to score > 0.8 on holdout"
├── Stage 1: Data pipeline working
│   ├── Task 1.1: SFT dataset loads without error
│   └── Task 1.2: DPO dataset has correct schema
├── Stage 2: SFT baseline trained
│   ├── Task 2.1: Loss < 1.5 at epoch 5
│   └── Task 2.2: Eval gate passes (perplexity < 20)
└── Stage 3: RL fine-tuning
    └── ...
```

**AWOS rule:** The Research → Spec → Task triad IS hierarchical decomposition.
Spec = Stage level. Task file = Task/Atomic level.

---

### 8.3 DAG-Based Decomposition

**Pattern:** Model task dependencies as a directed acyclic graph.
Tasks with no incoming edges (no dependencies) can run in parallel.
Critical path = longest chain of sequential dependencies.

```
A ──┐
    ├──→ C ──→ E (critical path)
B ──┘         ↑
              D (can run in parallel with A+B+C)
```

**Rules:**
- Draw the DAG before assigning to workers (§6.1).
- Critical path length determines the minimum wall-clock time regardless of parallelism.
- Any step on the critical path that is underspecified will cause the entire downstream to block.

---

### 8.4 Iterative Refinement

**Pattern:** Start with a coarse solution, then refine iteratively. Each iteration improves
one dimension (quality, correctness, efficiency). Never attempt to optimise all dimensions
simultaneously.

```
Iteration 0: working but naive implementation (correctness gate)
Iteration 1: correct but slow (performance gate)
Iteration 2: fast but high memory (memory gate)
Iteration 3: balanced — accept
```

**Rule:** Define what "done" means for each iteration *before* starting it. 
Iterative refinement without exit conditions is an infinite loop.

---

### 8.5 Decomposition Anti-Patterns

| Anti-pattern | Problem | Fix |
|---|---|---|
| Steps that contain "and" | Two things tested as one | Split at "and" |
| Steps without exit conditions | No way to know they are complete | Add falsifiable criterion |
| Steps that depend on unresolved ambiguity | Blocks at implementation | Resolve in research phase |
| Parallelising sequential dependencies | Data race / incorrect output | Draw the DAG first |
| Single mega-step for complex logic | Untestable, unreversible | Decompose until each step is atomic |

---

### 8.6 Atomic Step Test (Quick Reference)

Apply these three tests to every step before executing it:

1. **One-line test:** "Can I write a one-sentence test that proves this step succeeded?"
   → If no: break it down further.
2. **"And" test:** Does the description contain the word "and"?
   → If yes: it is probably two steps.
3. **Revert test:** "Can I undo this step independently if it turns out to be wrong?"
   → If no: it is a high-risk step. Add a backup/checkpoint before executing.

---

## Cross-Reference Map

| Concept | Protocol | Canonical file |
|---|---|---|
| Debug cycle | [[DEBUG_PROTOCOL]] | `docs/debug_log.md` |
| Session checkpoint | [[MEMORY_PROTOCOL]] | `data/logs/chat_checkpoint_*.md` |
| Eval gate | `research/specs/eval_gate_spec.md` | — |
| Ratchet state | — | `data/ratchet_state.json` |
| Kaggle push | [[RL_TRAINING_PROTOCOL]] | `docs/kaggle_runbook.md` |
| Research → Spec → Task | [[IMPLEMENTATION_PROTOCOL]] | `docs/specs/` |
| Tool registry | — | `src/tools/` |

---

## Related

- [[AWOS]] — full doctrine
- [[GLOSSARY]] — term definitions
- [[DEBUG_PROTOCOL]] — two-failed-attempt rule
- [[IMPLEMENTATION_PROTOCOL]] — Research → Spec → Task triad
- [[MEMORY_PROTOCOL]] — checkpointing and Write-Gate
- [[RL_TRAINING_PROTOCOL]] — training-specific patterns

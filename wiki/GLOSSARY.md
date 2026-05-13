---
title: "Glossary — Agentic OS Terms"
tags:
  - doc/wiki
  - topic/glossary
---

# Glossary

Key terms used throughout the Agentic OS workflow. All terms are linked from other documents using `[[GLOSSARY]]` or `[[term|display text]]`.

---

## Core Workflow Terms

**AWOS (Agent Workflow Operating System)**
The living doctrine document that defines how agents and humans collaborate on complex projects. Covers planning, memory, tool use, math, security, and session management. See [[AWOS]].

**Research → Spec → Task Triad**
The three mandatory artifacts that must exist before implementation begins: a research note (`docs/research/`), a specification (`docs/specs/`), and a task file (`tasks/active/`). Together they form the implementation contract and execution checklist.

**Preflight Gate**
The check that all three triad artifacts exist and are current before any implementation code is written. Fail-closed: no triad = no implementation.

**Atomic Step**
A step so small it changes one thing, tests one thing, and proves one thing. If the description contains "and," it is not atomic.

**Leaf Node**
A component that nothing else depends on. Ideal target for agent-owned implementation because the blast radius is small if it is wrong.

---

## Memory Terms

**Write-Gate Protocol**
The rule that a decision is not complete until it exists in a canonical file *in the same turn as approval*. Deferred writes are forbidden.

**Single-Owner Rule**
Each fact lives in exactly one canonical file. All other files link to it; copies are forbidden. Prevents fact drift.

**Episodic Memory**
Timestamped event log: what happened, when. The history layer.

**Semantic Memory**
Named fact store: what is known, with confidence scores. The knowledge layer.

**Working Memory**
Current session context: active goal, recent observations. Cleared between sessions unless serialized.

**Checkpoint**
A session summary written at the end of a work segment. Immutable after writing — it is a historical record, not a living document. See [[CHECKPOINT_TEMPLATE]].

**Fact Drift**
When the same fact is copied to multiple files and the copies diverge over time. Prevented by the Single-Owner Rule. Detected by `scripts/fact_lint.py`.

---

## Tool Terms

**Tool Result Envelope**
The standard wrapper all tools return: `{success: bool, text: str, data: dict, error: str, latency_ms: float}`. Ensures uniform handling across all tools.

**Tool Registry**
The component that registers, validates, and dispatches tool calls. Central point for all agent tool execution.

**Cache TTL**
Time-to-live for cached tool results. Default: 6 hours. Prevents redundant external API calls while allowing freshness.

---

## Knowledge Graph Terms

**Wiki Link**
An Obsidian-style cross-reference: `[[filename]]` or `[[filename|display text]]`. Obsidian resolves by filename stem — no path prefix needed.

**Frontmatter**
YAML metadata block at the top of every markdown file between `---` delimiters. Mandatory fields: `title`, `tags`.

**Tag Taxonomy**
Hierarchical tags with `/` separators. Categories: `doc/` (type), `status/` (active/done), `phase/` (N), `topic/` (slug), `layer/` (slug).

**Backlink**
A link from document B to document A. In Obsidian, visible in the backlinks panel. Programmatically discoverable with `grep_search` for `[[filename]]`.

**Orphan**
A markdown file with no incoming wiki links. Advisory finding (LK02) from `obsidian_lint.py`. Not necessarily wrong, but worth reviewing.

---

## Architecture Terms

**7-Layer Stack**
The computation architecture: Surveillance → Feature Engineering → World Model → Signal Fusion → Policy → Adversarial → LLM Support. Code belongs to exactly one layer. Layers never mix.

**ADR (Architecture Decision Record)**
A document capturing a significant design decision: context, options considered, decision, rationale, consequences. Numbered sequentially in `docs/adr/`. Never deleted — only superseded.

**POMDP (Partially Observable Markov Decision Process)**
The theoretical model for the global system. States are partially hidden; the environment is non-stationary; rewards are sparse. The full stack is designed around this structure.

---

## Debugging Terms

**Debug Protocol**
A 6-step procedure activated after 2 failed fix attempts: Reproduce → Instrument → Hypothesize → Verify → Fix → Regress. See [[DEBUG_PROTOCOL]].

**Two-Failed-Attempt Rule**
After 2 unsuccessful fixes on the same problem, mandatory switch to the debug protocol. No 3rd attempt without completing Steps 1-4. A hard rule.

---

## Agent Architecture Terms

**ReAct**
Agent loop that interleaves Thought → Action → Observation. Default for single-agent task execution. See [[AGENTIC_CONCEPTS]] §1.1.

**Plan-and-Execute**
Architecture separating a planner (task graph) from executors (run each step). Maps to the Research → Spec → Task triad. See [[AGENTIC_CONCEPTS]] §1.2.

**Reflexion**
Post-attempt self-critique written to episodic memory, prepended on retry. Operationalised by [[DEBUG_PROTOCOL]]. See [[AGENTIC_CONCEPTS]] §1.3.

**LATS (Language Agent Tree Search)**
Monte Carlo Tree Search over the agent action space. High compute cost — never run locally (P1). See [[AGENTIC_CONCEPTS]] §1.4.

**Critic-Actor**
Two-role pattern: Actor generates; Critic scores with structured feedback. Actor never self-approves. See [[AGENTIC_CONCEPTS]] §1.5.

**Orchestrator-Worker**
One orchestrator manages the plan; stateless workers run subtasks. Workers communicate only through orchestrator. See [[AGENTIC_CONCEPTS]] §6.1.

**Tool Result Envelope**
Standard wrapper for all tool returns: `{success, text, data, error, latency_ms}`. Normalise non-conforming results before processing.

**Context Budget**
Token allocation across system prompt, instructions, retrieved knowledge, tool results, reasoning trace, and output buffer. See [[AGENTIC_CONCEPTS]] §7.1.

**Ratchet State**
Persisted record of the best-known metric; blocks any checkpoint regressing it. Canonical file: `data/ratchet_state.json`. See [[AGENTIC_CONCEPTS]] §5.5.

**Evaluation Gate**
Boolean criterion blocking stage progression until a falsifiable metric threshold is met. Defined before training, never adjusted post-hoc. See [[AGENTIC_CONCEPTS]] §5.4.

**Chain-of-Thought (CoT)**
Reasoning pattern: step-by-step reasoning written before the final answer. Required for all non-trivial decisions; results must be checkpointed (Write-Gate).

**Tree-of-Thought (ToT)**
Reasoning pattern: generate + evaluate multiple candidate thoughts at each step; prune weak branches. Cost O(k^d) — use selectively.

**Procedural Memory**
Numbered, atomic, executable instructions for recurring tasks. Canonical files: `protocols/*.md`, `docs/<platform>_runbook.md`.

---

## Related

- [[AWOS]] — full doctrine
- [[SCHEMA]] — wiki page creation rules
- [[AGENTIC_CONCEPTS]] — agent architecture and pattern reference
- [[log]] — activity log

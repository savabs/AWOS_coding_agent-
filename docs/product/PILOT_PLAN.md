# Execution Path — Proper Coding Agent First

> **Path note:** this file historically held a pilot plan. It is now repurposed as the execution path for the current vision.

---

## Core premise

AWOS should not optimize around customer pilots, pricing, or CI branding first.
It should optimize around building a **proper coding agent**.

The right sequence is:
1. proper coding agent
2. self-learning coding agent
3. AI-operated OS

---

## What must be true first

Before “self-learning” matters, the coding agent must be able to:
- understand a codebase
- plan real work
- execute safely
- use tools deliberately
- verify its work
- recover from failure

If the core agent is weak, the learning layer has nothing valuable to improve.

---

## What self-learning means here

Self-learning is not mainly:
- customer profiling
- repo-memory branding
- pricing lock-in
- GTM language about compounding

Self-learning means:
- the agent fails
- the system records why
- the system changes future behavior
- the next similar failure is easier to solve

Valid mechanisms include:
- prompt evolution
- tool synthesis
- routing adaptation
- verifier strengthening
- decomposition repair
- better fallback logic
- systems-level optimization

---

## What the app is for

The app exists so a user can **talk to the coding agent**.

It should help the user:
- give the agent a goal
- watch the execution
- inspect routing and tool use
- review diffs
- see failures and retries
- understand what the agent learned

The app is an interface to the agent, not the identity of the project.

---

## Immediate engineering path

### Path A — Proper coding agent
- stronger planning
- stronger execution
- stronger verification
- stronger failure recovery
- stronger end-to-end proofs

### Path B — Self-learning from failures
- structured failure logging
- repeated-failure clustering
- prompt evolution from failure cases
- tool synthesis from repeated manual work
- verifier improvements from false greens
- routing improvements from observed failure modes

### Path C — AI-operated OS expansion
- broader task runtime
- workspace orchestration
- memory layers
- autonomous environment management

---

## Near-term questions that matter

1. Can the coding agent finish bounded real coding tasks reliably?
2. Where does it fail most often?
3. Which failure classes are easiest to make learnable?
4. Which system improvements raise `Quality × Speed ÷ Cost` fastest?

---

## Things to avoid right now

- GTM-first planning
- pricing-first documents
- customer-pilot framing as the main roadmap
- CI repair as project identity
- pretending the OS exists before the coding agent is strong enough

---

## Success condition for this path

The path is working when:
- the coding agent is clearly real
- its failures are measurable
- the system can improve against those failures
- OS expansion starts from strength, not from metaphor

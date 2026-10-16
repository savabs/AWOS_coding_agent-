# REA digest: per-product design lessons (2026-10-16)

The owner's REA project (`~/Projects/REA`, **local only, not in this repo**) reverse-engineered 13 AI products plus
Apple Notes. This digest restates the **design lessons in our own words** so sessions without the laptop (e.g. cloud)
have the substance.

**Rules:**
- REA's own rule: no decompiled code, extracted prompts, model weights or private API calls go into AWOS. This repo
  is public. Never paste REA raw material here.
- Evidence is mostly static analysis. No product publishes accuracy numbers, so every idea below is a hypothesis to A/B.
- The roadmap built from this is `rea_synthesis_2026-10-16.md`.

## Coding-agent clients

### Cursor (IDE + `cursor-agent` CLI)
- **Thin client, server brain.** The loop, model calls, compaction and edit-merge run on Cursor's servers over one
  two-way stream. The client runs about 41 typed executors (shell, file I/O, grep, MCP, hooks, subagents).
- **Tool profile per model family.** A full tool set, a minimal set, and a bash-only profile modelled on
  mini-swe-agent. The client registers a bash-only executor; that the server selects it per model is inferred.
- **Content-addressed conversation checkpoints.** Each turn's state is stored by content hash with a pointer to the
  latest, so any turn can be resumed or forked.
- **Two-stage compaction.** A background summary starts early and is swapped in near the limit. User queries are kept
  word for word, there's a fair per-message budget, and the model is told how many summaries have happened.
- **Layered str_replace.** Exact match, then whitespace-insensitive, then a "did you mean" window. Ambiguous matches
  are refused; edits apply bottom-up; separate error text for the user and for the model. (Pitfall: normalising the
  whole file instead of just the matched region.)
- IDE-only: Merkle index sync, n-gram index, Tab/jump prediction.

### OpenAI Codex desktop app
- A closed Electron shell drives the open-source Rust engine over JSON-RPC. The closed part is orchestration.
- **One detached git worktree per thread**, with an owner record and setup/cleanup scripts. Keeps at most N
  worktrees, protects live ones, and **snapshots to a hidden ref before deleting**.
- **Transactional merge-back:** each step rolls back on failure, or a 3-way apply reports applied, skipped and
  conflicted paths.
- **Sessions are append-only logs** supporting resume, fork, revert, compact, steer and interrupt; one experimental fork mode (ephemeral only) can reuse the parent's prompt cache.
- **Sandbox:** deny-by-default Seatbelt with `.git` and agent control dirs read-only; git runs on the host.
- **Approvals on three axes:** sandbox × approval policy × reviewer (human or an AI reviewer with a denial breaker).
- An automation scheduler (recurring rules, jitter, per-tick caps, a structured result per run).

### Devin Desktop (formerly Windsurf)
- **Native sidecars:** a Go language server (context engine, worktrees) and a Rust agent speaking an open
  agent protocol over stdio, so the same agent runs in several editors and the terminal.
- **Trajectory as a typed event log** (~90 step types) with branch, revert, replay and explicit termination reasons.
- Parallel models that converge, a cheap explore subagent, and a lead/sidekick pairing.
- Named but unread watchdogs: cache keep-alive and stall watch.
- **Context ranking:** one scorer per request (BM25 or an LLM scorer, never blended) × source priors × recency decay.

## Agent CLIs and sandboxes

### Amp and Factory Droid
- **Amp:** a thin client; the loop, model choice and compaction live on its server. A stronger "oracle" model is a
  tool the main model may call.
- **Droid:** the loop runs locally.
  - **Routing:** a cheap classifier estimates each model's chance of first-attempt success, and Droid picks the
    **cheapest model above ~0.7**, with the others as fallbacks. The choice is **sticky** (re-picked only on
    compaction, overflow or a new need) to keep the cache warm.
  - **Stuck handling:** counts hedging phrases, then nudges the model to escalate up a per-vendor ladder.
    Crude; behavioural signals are better.
  - **Typed loop guards:** empty reply, identical repeated calls, degenerate output, refusal.
  - **Missions:** orchestrator, workers and a cross-vendor validator at milestones.
  - Risk-vs-autonomy command gating; a secret scan before commit/push.

### Kiro vs Amazon Q CLI
- A Rust CLI continuing Q CLI, plus an embedded LangGraph harness, offered to users gradually (never switched silently).
- **Spec → tasks graph:** requirements (structured sentence patterns), a design with correctness properties, and a
  task tree with optional dependency "waves". Bad graphs are repaired or fall back to sequential order; up to 5
  subagents run per wave; a human approves each phase.
- **Failure detector:** if most of the recent tool results failed (15 of the last 20 by default), it injects a short
  message telling the model to change approach.
- **One-way model escalation:** fast/balanced/smart tiers; a periodic probe on a forked context decides yield or continue.
- **Hooks:** 12 triggers with an exit-code contract. Lesson: one trigger claimed to block but didn't, so test the contract.
- **Permissions** compiled to a policy language (deny > ask > allow, explainable); a permission classifier runs in shadow mode.

### Claude Desktop / Cowork
- Electron with typed, validated IPC. A hardened install pipeline for extensions and MCP servers.
- **One Linux VM per app** (not per session), with per-session isolation inside it: own user, cgroup, mounts with
  read-only / read-write modes, and a syscall filter.
- **Egress proxy:** a domain allowlist (package registries, GitHub, the model API), pre-approved credentials only,
  header filtering, read-only zones.
- **MCP tool gate at the transport layer:** a tool must have been advertised and match exactly; an authorizer
  allows, denies or rewrites the call; with no gate, every call is refused.
- **Computer use:** per-app permission tiers checked at action time, screenshots that leave out non-granted apps, and
  a physical-Escape emergency stop.

## Planning, tools and perception

### Apple IntelligenceFlow planner (Siri's LLM planner)
- A fixed pipeline: mark entities → retrieve and rerank tools → retrieve context → the model writes a plan in a
  small typed language under a grammar → an interpreter runs it step by step.
- Each step returns a **typed outcome** (needs value, confirm, disambiguate, not allowed, model-facing error, failure)
  that feeds the next plan cycle.
- **Ask-or-act is a small hand-written decision program shipped as a model file, not an LLM;** only the per-user
  posteriors are learned (from usage counts). It weighs the chance of each candidate (from usage
  history) against a risk/payoff table and picks the action with the highest expected value. A shadow policy and a
  forced-ask rate keep training labels coming.
- Per-tool precondition checks; string arguments must quote the user (limits invention and injection); an injection
  classifier labels context. Caution: missing risk defaults to "execute", which is too permissive to copy.
- **Built for a ~3B on-device model with hundreds of tools.** Its planner layer is the kind that lost on Haiku.

### Apple GenerativeFunctions (tools and local/cloud routing)
- **Placement is data, not a per-request decision:** feature → model bundle → provider, and on-device vs private
  cloud is a property of the provider. Runtime signals only gate; they never choose. No automatic cross-tier fallback was found.
- Typed tool schemas are compiled to grammars for constrained decoding. Tool descriptions come in three layers
  (generated, curated per tier, patched by ID). Tools that fail to render are marked unusable, not dropped.
- One error taxonomy with retry-after. **Privacy before escalation:** attested nodes, anonymous tokens, size buckets
  instead of exact sizes, and a log of every cloud request.

### ChatGPT "Work with Apps" / computer use (Mac)
- Perception uses **accessibility-tree text** (it also always captures a screenshot; its "needs pixels" classifier
  is an unwired stub, and AX diffing ships default-off behind a remote flag): visible range only, a "showing a–b of N" note, many
  cleanup passes, one line per element with a **stable ID** that survives between steps.
- **Diffs only when shorter than the full tree**, plus a "no change" message.
- Code edits go through file patches, never typing into an IDE; terminals are forbidden targets.
- **Layered target policy** (forbidden apps, password-manager warning, org lists, per-app approval).
- Capability is scoped to a turn: state must be read first, and re-read after the user takes over.
- **Two-phase sends:** prepare → user approves (possibly editing) → commit once.

### Apple UIIntelligence
- UI frameworks describe their own views as a **typed element tree** (21 kinds, sparse attributes, values that may
  still be loading). **Buttons have roles: normal, primary, cancel, destructive.**
- Collection from every app within a short deadline; late fragments are dropped and the reason reported. Occlusion
  and visibility filtering keep an element that is at least a few percent visible OR shows a minimum area,
  intersected down the tree; selected elements are kept even off-screen (capped).
- The planner sees **flattened typed items under a size cap**, never the raw tree. Semantic actions (menu commands,
  text insertion) are preferred over coordinate clicks.

## Inference, caching and local models

### Apple TokenGeneration (on-device decoding)
- **Grammar-constrained decoding:** JSON schemas, tool definitions, BNF and regex all compile to one grammar form,
  tracked incrementally. Masks are bitsets cached by parser state; the vocabulary index is cached on disk.
- **Forced runs:** when only one continuation is legal, several tokens are emitted at once, but only after checking
  they re-tokenize identically.
- **Speculative decoding** from three draft sources (draft model, prompt lookup, previous output), with an
  adaptive draft count and grammar-checked drafts.
- **Prompt-prefix KV reuse,** plus an LRU of cacheable prompt modules (relies on Apple-specific attention).
- **AWOS use:** llama.cpp already offers GBNF, prompt caching and speculation, so the lesson is to *use* them (done in B1–B4).

### Apple ModelManager (model residency daemon)
- One daemon owns which models stay loaded. Clients take **leases naming a policy**; policies merge most-restrictively
  (minimum of limits, maximum of priorities).
- **Memory budget** by RAM tier; a static cost catalog per model; admission defers a job that would go over budget (foreground work gets a small overcommit; background
  work can be preempted).
- Eviction order: prefer-unload, then not-avoid-unload, then least recently used. Idle purge after 5 minutes.
  All rule-based, nothing learned.
- **AWOS use:** idle unload, a battery policy and memory admission for llama-server (done in B5).

### Apple LLMCache (semantic output cache)
- Each entry is one model output plus every phrasing that produced it, stored as embeddings.
- Lookup ranks exact matches first, then similarity, then recency; the caller decides the threshold.
- Content-addressed entries; a sliding TTL refreshed by reads; the model version in the key is the client's choice (the cache itself resets when the embedding size changes); fail-open on any error.
- **AWOS use:** only an exact-match cache keyed on provider + model + full prompt. A semantic cache for code is unsafe without a verifier.

### Apple Notes (practice target)
- Core Data storage, gzip-compressed protobuf CRDT note bodies, encrypted locked notes. **No AWOS engine use.**

## Cross-cutting conclusion

No product shows that its harness raises accuracy (static analysis cannot observe accuracy and none publishes numbers,
so this is absence of evidence, not evidence that harnesses don't help). They converge on **minimal loops plus product infrastructure**:
isolation, resume, cost routing, safety and scheduling. That matches AWOS's own pre-registered results (planner and
gate layers lost on Haiku; the minimal loop wins at lower cost). The measurable AWOS bets are cost routing,
local-model reliability, long-task resume and compaction, and unattended safety (`rea_synthesis_2026-10-16.md`).

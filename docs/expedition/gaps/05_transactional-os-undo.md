# Gap chart 05: Transactional execution and undo for computer use

*Explorer chart, 2026-10-10. Scope: snapshots, checkpoint/restore, sagas, speculative execution, dry-runs. The session's web search budget ran out partway through, so a few claims rest on primary docs fetched directly or on local measurement. Each claim is labelled with its source.*

## Summary

The Gatekeeper's "bounded repair, then escalate" step assumes a failed attempt can be erased. That holds for **local state** (files, a VM, a process). It does not hold for **external state** (mail sent, a SaaS record changed, money moved). Nothing at the OS level fixes the second case. The answer comes from databases and Speculator-style OS research: **run speculatively against a copy, buffer everything that leaves the box, and commit only after the gate passes.** On this Mac (M5, APFS) the local half is cheap: cloning a 2,000-file workspace took **0.30 s** and restoring it took **0.08 s** (measured). What remains is engineering the external half: classification, outbox, compensation and idempotency keys.

## What matters

**1. Split every action by effect domain, not by verb.** "Delete" inside a cloned workspace can be undone. "Delete" through the Gmail API cannot, after the trash retention window. AWOS's `scaffold/agent/desktop/policy.py` already has `ActionClass` with an `IRREVERSIBLE` set (egress, send, delete, credential, payment, git_push). It treats DELETE as irreversible no matter where the target is, and it has no notion of compensation or of a commit point. The taxonomy below extends it.

**2. Reversibility taxonomy for computer-use actions (proposed).**

| Class | Definition | Examples | Undo mechanism | Gate treatment |
|---|---|---|---|---|
| R0 Pure | No state change | read file, screenshot, AX-tree query, GET | none needed | run freely |
| R1 Sandboxed | Changes stay inside a copy AWOS owns | edits in an APFS clone/worktree, actions in a VM guest, temp dirs | discard the copy (O(1)) | run freely; promote copy on commit |
| R2 Locally restorable | Changes host state that a snapshot covers | edits in `~/Documents`, app prefs, local DB | pre-snapshot (clone or APFS local snapshot) + restore | snapshot before, verify after |
| R3 Compensable | External effect with a known inverse within a window | create calendar event (delete it), move a mail to a folder, create a draft, label, star, open a PR, Stripe refund | saga compensating action, logged with the forward action | allowed only with a registered compensator; the compensator is tested |
| R4 Deferrable | External effect that can be held in an outbox until commit | send email, post message, submit form, git push, file upload | delay then release (outbox); provider "undo send" windows are a weak version | buffered; released only after the gate passes and the policy allows |
| R5 Irreversible | No inverse, cannot be safely delayed, or the inverse is socially visible | payment capture, credential use, account deletion, a message already read, triggering a physical action | none | owner confirmation per instance; never auto-repaired |

Two orthogonal flags apply to any class: **idempotent?** (is a retry safe, for example with a Stripe idempotency key) and **open-world?** (does it touch entities AWOS does not own). The MCP `ToolAnnotations` already defines `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint`. The defaults are the pessimistic ones: destructive=true and openWorld=true. The spec says clients "should never make tool use decisions based on ToolAnnotations received from untrusted servers". So AWOS should map annotations onto R-classes only for servers it trusts, and default everything unknown to R5.

**3. Speculative execution with output commit (the core idea).** Speculator (SOSP'05) checkpoints a process, runs ahead on a predicted result, tracks causal dependencies across IPC, and **blocks externalized output (network messages, screen writes) until the speculation proves correct**. It roughly doubled NFS performance on a LAN and gave about 10x over a WAN. "Rethink the Sync" (OSDI'06) generalized this as *external synchrony*: commit is triggered by observable output, and performance comes within 7% of async ext3 with synchronous guarantees. For an agent, the "speculation" is an attempt by a small model, "proving correct" is the verification gate, and "externalized output" is any R3–R5 action. This is the commit protocol AWOS needs.

**4. Local snapshot cost is negligible at workspace scale.**
- *Measured on this M5 (APFS, `cp -c` = clonefile):* 2,000 files / 4 MB: clone 0.30 s, plain copy 0.55 s, restore by swap 0.08 s, rsync restore of one change 0.19 s. 20,000 files / 78 MB: clone 3.05 s vs copy 5.3 s, mostly system time spent on per-file metadata. One 2 GB file: clone about 0 s vs copy 1.3 s. **Clone cost scales with file count, not bytes.** That argues for cloning a *directory tree root* (or git worktree) with few files, or for using volume-level snapshots on large trees.
- *Watts (estimate, not measured; `powermetrics` needs sudo):* each of these runs is about one CPU core of system time. At a few watts of package power that is on the order of 1–2 J per small-workspace attempt. That is about one second of local LLM decode energy. Snapshot cost will never dominate the objective at this scale.
- Fault-tolerant sandboxing (arXiv 2512.12806) reports a 100% rollback success rate at about 1.8 s overhead per transaction (14.5%), which is faster than container init. This matches the measurement above.

**5. VM-level snapshots give whole-desktop undo, at a cost.** macOS Virtualization.framework has `saveMachineStateTo` / `restoreMachineStateFrom`. They are for a paused VM whose configuration supports save/restore, and they arrived in macOS 14 (Apple docs page; WebFetch could not render version details reliably, so verify). The memory state file scales with guest RAM. Disk is separate, so pair it with an APFS clone of the disk image. Firecracker docs: full and diff snapshots, memory restored lazily via MAP_PRIVATE or UFFD, **disk not included**, and network connectivity not guaranteed after resume. Resuming the same snapshot twice is insecure without VMGenID (RNG reuse), and the wall clock jumps. vHive/REAP (arXiv 2101.09355): functions restored from a snapshot ran 95% slower than memory-resident ones because of page faults, and REAP's working-set prefetch cut cold start by 3.7x. Lesson: restore can be ms-scale to resume, but the *first seconds after restore* pay page-fault tax, so measure time-to-first-useful-action, not resume latency.

**6. Process checkpoint/restore is mostly a dead end on macOS.** CRIU is Linux-only and cannot dump processes holding character or block devices, processes under ptrace, or **GUI apps tied to an X server ("part of the app's state is in the Xserver")**. macOS has no supported equivalent. For desktop apps, the VM boundary or app-level state (document files, prefs) is the practical unit of rollback.

## What does not matter (or matters less than it seems)

- **Sub-millisecond snapshot latency.** An attempt costs seconds to minutes of inference, so 0.3 s versus 5 ms is noise. Choose on coverage and simplicity.
- **LLM-judged reversibility at runtime.** Prior charts note that LLM guards top out around 80%. Classification should be a deterministic table keyed on (tool, target domain), learned offline and reviewed. The model should not decide it per call.
- **Agent "rewind" papers in controlled environments** (AgentRewind arXiv 2608.14380, STRATUS's transactional no-regression arXiv 2506.02009). They are useful for context+environment checkpoint alignment, but they assume the environment is restorable. That is the easy half.

## Commit protocol for the gate (design)

```
BEGIN(attempt):
  ws  = clone(golden_or_current)            # R1: APFS clone / worktree / VM disk clone
  snap= snapshot(host_paths_in_scope)       # R2: only if task touches host paths
  outbox = []                               # R4 buffer;  comp_log = []  # R3 saga log
EXECUTE (small model):
  R0/R1  -> run in ws
  R2     -> run, record path in snap scope
  R3     -> run only if compensator registered & tested; append (fwd, inverse, idem_key)
  R4     -> append to outbox (rendered preview: diff / draft / plan)  -- NOT sent
  R5     -> stop; attempt can only end in PROPOSE
VERIFY (gate): tests / postconditions on ws + previews of outbox + state probes
  pass  -> COMMIT: promote ws (rename), drop snap, release outbox in order with
           idempotency keys, close comp_log (keep for audit window)
  fail  -> ABORT: discard ws, restore snap, run comp_log in reverse,
           drop outbox; feed failure to bounded repair (new attempt from BEGIN)
  R5 pending -> PROPOSE: package preview + evidence for owner; on approval COMMIT
ESCALATE: cloud model gets a fresh BEGIN from the same base, never the dirty ws
```

Properties: aborted attempts leave no external trace except compensated R3 actions. Repair and escalation always start clean. Commit is the only point where effects leave the box. Journal the outbox and comp_log to disk before release so a crash mid-commit can resume (fits the planned M1 host journal).

**Dry-run / preview APIs feed R4 previews.** Widely available patterns (well known, not re-fetched this session): `terraform plan`, `kubectl apply --dry-run=server`, `git push --dry-run`, `rsync -n`, email drafts instead of send, Stripe test mode. Stripe's v1 idempotency keys save the first response and replay it on retry. Keys can be pruned after 24 h, and mismatched parameters error out (Stripe docs, fetched). A preview is also a better artifact for the gate than a post-hoc screenshot.

## Implications for AWOS

1. Extend `ActionClass` to R0–R5 keyed on *effect domain*. A DELETE inside the sandbox becomes R1, not irreversible. Add `compensator` and `idempotent` fields. Map trusted MCP annotations onto these fields; untrusted servers default to R5.
2. Make "clone before attempt" the default for every gate attempt. Measured cost is 0.3 s for a typical workspace. Clone shallow roots or worktrees; avoid cloning trees with tens of thousands of files.
3. Implement the outbox before any real email or SaaS automation. It turns the most common computer-use sinks (send, post, push) from irreversible into deferrable at nearly zero cost.
4. A compensator becomes a verified routine. A registered inverse is promoted only after a live proof that forward+inverse returns the state probe to baseline. This is evidence-gated memory applied to undo.
5. For desktop tasks, use a Lume/VZ macOS guest with a disk clone plus a saved machine state as the restorable unit. Benchmark time-to-first-action after restore, not resume latency.

## Open questions

- Actual VZ `saveMachineStateTo` and restore time and file size for a 4–8 GB macOS guest on M5, plus the joules per cycle (needs sudo `powermetrics`).
- What fraction of real owner tasks contain an R5 action? 20_security-memory cites 0.8% of actions looking irreversible. Does that hold per *task* for email and SaaS work?
- Can compensators for common SaaS (Gmail, Calendar, Notion, GitHub) be auto-derived from API specs (POST↔DELETE pairs), then verified?
- Outbox semantics when the gate passes but the world changed meanwhile (stale draft, conflicting edit): re-verify at release time?
- Can APFS local snapshots (`tmutil localsnapshot`) give an unprivileged agent whole-volume R2 coverage, and how fast is selective restore?

## Key resources (annotated)

- https://arxiv.org/abs/2512.12806 — Fault-tolerant sandboxing: policy interception + transactional FS snapshots. 100% rollback, about 1.8 s per transaction.
- https://web.eecs.umich.edu/~pmchen/papers/nightingale05.pdf — Speculator (SOSP'05): checkpoint, speculate, block externalized output until confirmed. The template for the commit protocol.
- https://www.usenix.org/legacy/event/osdi06/tech/nightingale.html — Rethink the Sync: output-triggered commit (external synchrony), within 7% of async.
- https://arxiv.org/abs/2404.06921 — GoEX: post-facto validation built on undo + damage confinement for LLM actions.
- https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/schema/2025-06-18/schema.ts — MCP ToolAnnotations hints and their pessimistic defaults. Untrusted unless the server is trusted.
- https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md — Snapshot semantics and caveats (disk excluded, VMGenID, clock).
- https://arxiv.org/abs/2101.09355 — vHive/REAP: post-restore page-fault tax (+95%), working-set prefetch (3.7x).
- https://criu.org/What_cannot_be_checkpointed — Why process C/R does not cover GUI apps.
- https://code.claude.com/docs/en/checkpointing — A shipping agent's undo does not cover Bash side effects, subagents or links. Undo must sit below the tool layer.
- https://docs.stripe.com/api/idempotent_requests — Idempotency-key semantics, the model for safe commit retries.
- https://developer.apple.com/documentation/virtualization/vzvirtualmachine — VZ save/restore machine state.

## Sources

All URLs above. Other references named from search results only, abstracts not fully read: AgentRewind https://arxiv.org/abs/2608.14380 (abstract read), STRATUS https://arxiv.org/abs/2506.02009 (named in search snippet), Learning to Undo https://arxiv.org/abs/2510.14503 (search snippet). Local measurements: `/tmp/apfs_bench_scripts/b.py` plus ad-hoc `cp -c` timings on Apple M5, macOS (Darwin 25.5), 2026-10-10.

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

Method note: arXiv's search API returned HTTP 429 for all seven title queries, so arXiv items below were found through HN search results and then confirmed on their arXiv abs pages. The HN Algolia API worked; GitHub search was not run (a shell guard blocked the call). Coverage of the arXiv literature is therefore thin.

### New since the chart (dated, with URLs; most important first)

1. **"Safe to Resume? Breaking Execution Continuity of Agent Execution via Rollback"** (arXiv, submitted 2026-08-29). It argues that "a faithfully restored checkpoint may resume an execution whose states, assumptions, and external effects never coexisted in any valid history". It names five failure modes: incomplete internal state, stale external dependencies, nondeterministic replay, unrecorded external effects, and state-dependency mismatch. It reports working exploits on Hermes (malware-verification bypass), Cline (unauthorized mail forwarding) and LangGraph (double payment). https://arxiv.org/abs/2608.29381. Why it matters: this is direct evidence for the chart's thesis that restore alone is unsafe once effects leave the box. It also supports the open question about the outbox when "the world changed meanwhile". AWOS's commit protocol should re-verify at release time and always start repair from a fresh BEGIN rather than resuming a restored attempt.
2. **DeltaBox: millisecond-level checkpoint/rollback for AI agents** (arXiv 2605.22781, submitted 2026-05-21, revised 2026-06-08). It uses a layered copy-on-write filesystem (DeltaFS) plus incremental process dumps (DeltaCR), and reports 14 ms checkpoint and 5 ms rollback, evaluated on SWE-bench and RL micro-benchmarks. https://arxiv.org/abs/2605.22781. Why it matters: it shows a Linux-side sandbox can restore file and process state far faster than the chart's 0.08-0.3 s APFS numbers. That supports the chart's point that snapshot latency is solved, and it strengthens the "sub-millisecond latency does not matter" section. It is Linux-only, so it does not help the macOS guest case directly. Numbers are self-reported by the authors.
3. **AgentRewind: recoverable execution for long-horizon LLM agents** (arXiv 2608.14380, 2026-08-14). I read the abstract this session. It records aligned checkpoints of agent context and controlled environment and ships a new benchmark, MettleBench. https://arxiv.org/abs/2608.14380. Why it matters: this is the context-plus-environment alignment piece the chart said it left unread. Pair it with item 1: a rewind that realigns context but not external effects is the failure item 1 demonstrates.
4. **Teleport-env** (Show HN, 2026-05-28): overlayfs plus CRIU, claims under 500 ms combined restore (466 ms in one run with qwen-2.5-coder), 2 stars, and requires Linux with CHECKPOINT_RESTORE. On macOS or Windows it must run inside a Multipass Ubuntu VM. https://github.com/JaiCode08/teleport-env. Why it matters: it is a working data point for the chart's claim that CRIU is a dead end on macOS (confirmed: it needs a Linux VM). It also suggests the Lume/Linux-guest route if process-level restore is wanted.
5. **agent_acid** (Show HN, 2026-08-08, 1 star): "shadow execution" (simulate the plan in a sandbox first), a `ReversibleTool` with paired `execute` and `compensate` run in reverse order on failure, and green/yellow/red risk tiers. https://github.com/muhammadwaqasai/agent_acid. Why it matters: it independently converges on the chart's R3 saga plus tiered gate design. It is hobby-scale and I did not run it, so treat it as design validation, not evidence of effectiveness.
6. **Cortex "Snapshots, copy-on-write, and the economics of agent sandboxes"** (HN, 2026-07-23). It describes Firecracker snapshots as state.bin, mem.bin and disk, content-addressed 16 MiB disk chunks, and userfaultfd memory sharing, with a cost model that charges dirty RAM and disk instead of requested size. It gives no latency numbers. https://builders.cortex.io/blog/sandboxing-agents-part-2/. Why it matters: it confirms the Firecracker "disk is separate" caveat and shows the commercial direction (retain many snapshots cheaply). It is not a measurement.
7. **Crowded "undo for agents" tooling wave**, all small Show HN projects with 1-9 points: Respawn (2026-09-17, https://github.com/savageAZfck/respawn), Do-over for shell commands (2026-08-20, https://github.com/CaydenChik/doover), SafeSandbox (2026-05-08, https://github.com/Baukaalm/safesandbox), Salvager (2026-06-17, https://www.salvager.sh/), Memdebug for agent memory undo (2026-10-09, https://github.com/juraj-jumic/memdebug). Why it matters: demand is real, but all of these appear to be local file and shell undo (the R1/R2 half). I saw no outbox or compensation work in the titles. The R3-R5 half remains the differentiator.
8. **CACM blog "AI Agents Need an Undo Button"** (HN, 2026-10-02). I could not read it (HTTP 403), so I cannot say what it argues. https://cacm.acm.org/blogcacm/ai-agents-need-an-undo-button/. Listed only as a signal that the topic reached mainstream venues.

### Corrections

- Chart: "AgentRewind arXiv 2608.14380 ... assume the environment is restorable. That is the easy half." -> The abstract says it uses a "controlled environment" and aligns it with agent context, so the characterization is fair. But item 1 shows even "easy half" restore can be unsafe when external dependencies are stale, so the chart's "easy" label should be read as "easy for file state only". https://arxiv.org/abs/2608.14380
- Chart: "Learning to Undo arXiv 2510.14503" listed among agent rewind references -> it is a tabular RL paper (CliffWalking, Taxi) about a reversibility measure and selective rollback, not an LLM-agent or OS undo paper. Low relevance; drop it. https://arxiv.org/abs/2510.14503
- Chart: STRATUS "transactional no-regression ... named in search snippet" -> now confirmed. It is a multi-agent SRE system (arXiv 2506.02009, revised 2026-03-19) claiming at least 1.5x success over other SRE agents on AIOpsLab and ITBench. Its transactional no-regression is a safety spec for cloud mitigation, not an OS mechanism. https://arxiv.org/abs/2506.02009
- Otherwise none found.

### Confirmed claims (briefly)

- Fault-tolerant sandboxing (2512.12806): 100% interception, 100% rollback success, 14.5% overhead (about 1.8 s per transaction). Matches the chart; submitted 2025-12-14. https://arxiv.org/abs/2512.12806
- Claude Code checkpointing does not track Bash-made file changes or most subagent edits (confirmed from the live docs). It also skips symlinked and hard-linked paths on restore, which the chart did not mention. https://code.claude.com/docs/en/checkpointing
- MCP guidance that annotations from untrusted servers should not drive safety decisions is still stated in the current draft spec text I fetched (the destructive/openWorld default values were not visible in the portion read). https://modelcontextprotocol.io/specification/draft/schema
- CRIU/process C/R being unusable natively on macOS is consistent with Teleport-env needing a Linux VM.

### Still unverified

- The local APFS timings (0.30 s clone, 0.08 s restore, 2,000 files) are the chart's own measurements. I did not re-run them.
- VZ `saveMachineStateTo` and restore time and size, plus macOS 14 availability: not re-fetched.
- Firecracker, vHive/REAP (95% slower, 3.7x) and Stripe idempotency figures: not re-checked this pass.
- MCP `ToolAnnotations` default values: not confirmed from the draft schema.
- Whether DeltaBox's 14 ms and 5 ms hold on realistic agent workloads, and whether any 2026 work covers outbox or compensator derivation from API specs (arXiv search was rate limited; GitHub search not run).

# AIOS / AI-native operating systems

*Expedition chart 05, 2026-10-10. Built from four scout reports. Caveat on method: the session's shared WebSearch budget (200 calls) ran out before the scouts started, so every source here was fetched directly from a known primary URL. Late-2025 and 2026 academic AgentOS work is probably under-covered. Items marked [unfetched] or UNVERIFIED were not confirmed this session. Almost all performance numbers are self-reported by the authors or vendors.*

---

## 1. Summary

- **The "LLM OS" metaphor is settled, and its engineering content moved into serving systems.** AIOS (Rutgers, COLM 2025), MemGPT/Letta and Karpathy's LLM-OS diagram supply the vocabulary: syscalls, scheduler, context as RAM, paging. The measured gains come from agent-aware serving systems, which schedule whole agent programs and keep KV caches warm across tool calls: Autellix 4-15x, Continuum >8x JCT, Parrot ~10x, KVFlow ~2x, Pie 1.3-3.4x.
- **AIOS's headline 2.1x speedup is mostly "add a queue in front of one GPU."** Its baseline is uncoordinated, unbatched access. A modern continuous-batching server plus a simple queue gets most of the gain. Copy its structure, not its claims.
- **All the big platforms ship the same agent integration pattern: typed tool registries first, pixels last.** Windows MCP + On-device Agent Registry, Android AppFunctions, Apple App Intents, Chrome WebMCP (origin trial from Chrome 149). GUI actuation is the fallback path.
- **No platform's "hybrid" routing checks output quality.** Firebase AI Logic and Microsoft's three-tier guidance fall back to the cloud only when a model is *unavailable*. The Gatekeeper pattern (local attempt → verification gate → bounded repair → escalate) does not exist in any vendor SDK.
- **Small local models still can't drive a desktop.** Apple's 3B Ferret-UI Lite scores 53.3% on ScreenSpot-Pro grounding but only 19.8% on OSWorld and 28.0% on AndroidWorld. AIOS's own LiteCUA scores 14.66% on OSWorld. A better OS abstraction does not create capability. Verified routines and structured tools do.
- **Agent containment is shipping at the OS level.** Windows Agent Workspace (preview) gives each agent its own user account, its own session and scoped folder grants. Chrome adds a separate "User Alignment Critic" model that vets every action. Anthropic's srt sandboxes processes with Seatbelt/bubblewrap plus proxy allowlists.
- **The memory-OS lineage ended up at plain files.** Letta's own benchmark: file tools only scored 74.0% on LoCoMo against Mem0's reported 68.5%. Letta now consolidates memory in the background into a git-backed MemFS, with an optional review step.
- **Running many agents at once is mostly a serving and quota problem, not a CPU problem.** Agents spend 71-98% of runtime waiting on inference (Vercel, citing arXiv 2605.26297). Batched decode on a bandwidth-bound box gives about 18x aggregate throughput at batch 32 vs batch 1 (LMSYS, DGX Spark). Stock Ollama serializes requests (NUM_PARALLEL=1), and mlx_lm.server handles requests one at a time when the KV cache is quantized.

### On the request that triggered this run ("why max 8 agents in parallel?")

The cap is a Claude Code default, not an AWOS or hardware limit. Workflows run "up to 16 concurrent agents by default, fewer when Claude Code has fewer CPUs available." This Mac reports 10 CPUs (4 performance cores), so a cap of about 8 fits that rule. The exact formula is not documented. **`CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` (1-256) raises it.** Related limits:

- `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`: default 20.
- `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY`: default 10.
- The shared WebSearch budget: 200 calls per session, refilling at about 100 per hour. This run exhausted it, so more agents would just have been starved of search.

Past those settings, the binding limits are API rate limits and quota, 16 GB of RAM, and collisions on `.awos/` ledgers and the git index. AWOS's own runtime also hard-codes `MAX_WORKERS = 4` in `scaffold/agent/dag_executor.py:23`.

---

## 2. What matters most (ranked)

1. **Program-level scheduling in front of a batching server.** Autellix treats each agent program as the unit to schedule. Calls are prioritized by how much service the program has already received (least-attained-service), which cuts head-of-line blocking. Reported 4-15x program throughput over vLLM at equal latency. This is the direct mechanism for running many agents on one model. https://arxiv.org/abs/2502.13965

2. **Keep the KV cache alive across tool gaps.** Continuum pins an agent's KV cache for the *predicted duration* of each tool call instead of evicting it LRU. Reported >8x average job completion time on SWE-Bench, BFCL and OpenHands traces, with Llama-3.1 8B and 70B. AWOS's loop is call model → run pytest → call model, so the gaps are long. This trick is not in the existing trick_book. https://arxiv.org/abs/2511.02230

3. **Typed tool surfaces before pixels.** Windows ODR/MCP, Android AppFunctions ("the mobile equivalent of tools in MCP"), Apple App Intents entity and intent schemas, and Chrome WebMCP (Google: "faster, more reliable than actuation"). Small models are much better at typed calls than at GUI grounding: Ferret-UI Lite gets 19.8% on OSWorld. https://learn.microsoft.com/en-us/windows/ai/mcp/overview · https://developer.android.com/ai/appfunctions · https://developer.chrome.com/docs/ai/webmcp · https://arxiv.org/abs/2509.26539

4. **Batched local serving sized from KV memory.** DGX Spark, Llama-3.1-8B FP8: 20.5 tok/s at batch 1 vs 368 tok/s at batch 32. Ollama's default NUM_PARALLEL is 1, and RAM grows as NUM_PARALLEL × context. mlx_lm.server: "A quantized KV cache does not support batching." Parallel local agents only help if the server batches. https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/ · https://docs.ollama.com/faq · https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md

5. **Expose application structure to the server.** Parrot's Semantic Variables share prompt dataflow with the server (up to ~10x end-to-end, OSDI 2024). KVFlow uses an agent step graph to evict and prefetch by steps-to-execution (1.83x and 2.19x over SGLang HiCache). AWOS knows its own pipeline (replay → local → gate → repair → escalate) and its shared prefixes (repo card, system prompt). https://arxiv.org/abs/2405.19888 · https://arxiv.org/abs/2507.07400

6. **The agent as its own OS principal.** Windows contained MCP servers run "in a separate Windows session using a separate agent user account." Grants are per known folder (Allow always / Ask / Never), actions are audited, and admins control it through Intune. Known weakness: grants are per *host app*, not per tool. UFO2's picture-in-picture desktop lets the agent and the user work at the same time. https://learn.microsoft.com/en-us/windows/ai/mcp/servers/mcp-containment · https://arxiv.org/abs/2504.14603

7. **Architectural injection defenses over detectors.** Chrome uses four layers: a trusted User Alignment Critic model vets each action, Agent Origin Sets split sites into read-only and read-write, sensitive actions need user confirmation, and classifiers plus automated red-teaming back them up. CaMeL separates control flow from data flow and adds capability checks: provable security on 77% of AgentDojo tasks vs 84% undefended utility. https://blog.google/security/architecting-security-for-agentic/ · https://arxiv.org/abs/2503.18813

8. **Cheap layered isolation plus snapshot/fork.** srt (Seatbelt on macOS, bwrap + seccomp on Linux, domain allowlists via proxy) needs no VM; Anthropic reports 84% fewer permission prompts. Apple Containerization runs each Linux container in its own lightweight VM with sub-second start. E2B pauses in about 4 s per GB and resumes in about 1 s. Transactional sandboxing measured 100% rollback at 14.5% overhead (~1.8 s per transaction). https://github.com/anthropic-experimental/sandbox-runtime · https://github.com/apple/containerization · https://docs.e2b.dev/sandbox/persistence · https://arxiv.org/abs/2512.12806

9. **Simple, auditable memory written in the background.** Letta's file-tools result (74.0% LoCoMo) and its sleep-time agents writing into git-backed MemFS with a review step. Sleep-time compute reports ~5x less test-time compute and +13-18% accuracy on stateful tasks. https://www.letta.com/blog/benchmarking-ai-agent-memory · https://docs.letta.com/guides/agents/architectures/sleeptime · https://arxiv.org/abs/2504.13171

10. **MCP Tasks as the long-running job interface.** Introduced in the 2025-11-25 spec, still experimental. Tasks are durable, polled, cancellable state machines (working, input_required, terminal) with a TTL, and must be bound to the caller's auth context. They let the agent keep working while a test suite or cloud escalation runs. https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks

11. **Market proof for the always-on host.** OpenClaw reports ~392k GitHub stars (self-reported on the repo). Its design is a local gateway, 20+ messaging channels as the control surface, and state, memory and credentials kept on the owner's hardware. Its trust model is one trusted operator per gateway; it is not a hostile multi-tenant boundary. https://github.com/openclaw/openclaw · https://docs.openclaw.ai/gateway/security

---

## 3. What does NOT matter / hype / dead ends

| Item | Why it doesn't matter |
|---|---|
| AIOS's 2.1x speedup as evidence for an "agent kernel" | The baseline is unbatched and uncoordinated. HumanEval total time went from 152.1 s to 74.2 s just by adding FIFO. Any batching server plus a queue matches it. |
| Logits/beam context snapshots for preempting generation | Prefix/KV caching plus program-level priority does this more cheaply. Cloud APIs only allow text snapshots anyway. |
| Karpathy's LLM-OS diagram and "LLM as OS, Agents as Apps" (arXiv 2312.03815) as design inputs | Analogies with no mechanisms and no evaluation. Fine for explaining AWOS, useless for deciding what to build. |
| Specialized "memory OS" / vector-DB memory layers | Letta's own benchmark shows plain file tools match or beat them. The bottleneck is what gets written, not how it is stored. |
| Agent count as a goal (AIOS ran 250-2000 agents) | Throughput is capped by decode compute and KV memory. Success per agent and work per watt are what count. |
| Raising agent caps without fixing batching and shared state | Extra agents queue on a serializing server or collide on `.awos/` and the git index. The gain comes from the scheduler, not the number. |
| CPU count as the parallelism limit | Agents spend 71-98% of runtime waiting on inference. RAM, server slots and quotas are the real limits. |
| OS-bundled small models (Phi Silica, Gemini Nano, Apple's ~3B) as the AWOS worker | Built for summarize and rewrite, and locked down: Phi Silica needs an access token and is being replaced by Aion Instruct (retail Jan 2027); Firebase on-device is single-turn only. Use them, if at all, as optional helpers. |
| NPU TOPS / the "Copilot+ 40 TOPS" badge | Decode is bound by memory bandwidth. GB/s and RAM predict agent throughput; TOPS does not. Microsoft itself is moving Phi Silica to GPUs. |
| Microsoft rebrands (Copilot Runtime → AI Foundry → Foundry on Windows); DirectML | Same pieces renamed. DirectML is in sustained engineering only. Track Windows ML/ONNX Runtime, Foundry Local and ODR instead. |
| Windows Search "from searching to doing" | Hard-coded system toggles with no model or MCP details disclosed. It is a better command palette. |
| Cloud CUAs as the routine desktop path | Gemini 2.5 Computer Use is "not yet optimized for desktop OS-level control" and takes about 225 s per task. Use it only as occasional escalation. |
| Always-on "record everything" capture (Recall/Rewind style) | Recall's 2024 preview had a plaintext SQLite store. Limitless/Rewind was acquired by Meta, captures were shut off on 2025-12-19, and the service ended in the EU and UK. It conflicts with "memory only from strong evidence." |
| Dedicated AI gadgets (pendants, pins) | That category died or was absorbed. Value sits in an agent on a capable always-on host. |
| A2A for a single-owner host; waiting on agent-identity standards | A2A (Linux Foundation, v1.0) solves federation across organizations. The IETF OAuth AI-agent draft has expired. Broker credentials host-side instead. |
| Kubernetes agent-sandbox, Firecracker diff snapshots, vendor cold-start numbers | Built for fleets, still in preview, or marketing claims (Daytona "<90 ms"). Borrow the warm-pool *pattern* and use APFS clones or worktrees for forking. |
| Four-node Mac Studio RDMA clusters for the routine tier | About $40k for about 30 tok/s on a 1T model, on prerelease and unstable software. Interesting as a ceiling, not cost-effective. |

---

## 4. Key papers and resources

### Must-read
- **Autellix**: program-level agent serving, 4-15x over vLLM. https://arxiv.org/abs/2502.13965
- **Continuum**: KV-cache TTL across tool calls, >8x JCT on SWE-Bench and OpenHands traces. https://arxiv.org/abs/2511.02230
- **Claude Code workflows: behavior and limits**: the 16-agent default, CPU-based reduction, and the `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` override. https://code.claude.com/docs/en/workflows
- **Ferret-UI Lite**: a realistic ceiling for 3B local GUI agents (OSWorld 19.8%). https://arxiv.org/abs/2509.26539
- **Securely containing MCP servers on Windows**: a separate account and session per agent, plus the per-host-grant weakness to avoid. https://learn.microsoft.com/en-us/windows/ai/mcp/servers/mcp-containment
- **Architecting security for agentic capabilities in Chrome**: User Alignment Critic and origin sets. https://blog.google/security/architecting-security-for-agentic/
- **LMSYS DGX Spark review**: batch-1 vs batch-32 decode numbers. https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/
- **Letta: benchmarking agent memory**: the filesystem-is-enough result. https://www.letta.com/blog/benchmarking-ai-agent-memory

### Useful
- **AIOS paper** (scheduler and context-manager tables): https://arxiv.org/html/2403.16971v5 · repo: https://github.com/agiresearch/AIOS
- **Parrot** (Semantic Variables): https://arxiv.org/abs/2405.19888
- **KVFlow** (workflow-aware prefix caching): https://arxiv.org/abs/2507.07400
- **Pie** (programmable serving with WASM inferlets, SOSP 2025): https://arxiv.org/abs/2510.24051
- **Letta sleep-time agents / MemFS**: https://docs.letta.com/guides/agents/architectures/sleeptime · **Sleep-time Compute**: https://arxiv.org/abs/2504.13171
- **sandbox-runtime (srt)**: https://github.com/anthropic-experimental/sandbox-runtime · design rationale: https://www.anthropic.com/engineering/claude-code-sandboxing
- **Apple Containerization**: https://github.com/apple/containerization
- **MCP Tasks spec**: https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks · changelog: https://modelcontextprotocol.io/specification/2025-11-25/changelog
- **Firebase hybrid inference** (availability-only routing): https://firebase.google.com/docs/ai-logic/hybrid-on-device-inference · **Windows AI tiers**: https://learn.microsoft.com/en-us/windows/ai/windows-ai-comparison
- **Ollama FAQ** (concurrency defaults): https://docs.ollama.com/faq · **mlx_lm server doc**: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md
- **CaMeL**: https://arxiv.org/abs/2503.18813
- **Fault-tolerant sandboxing for coding agents**: https://arxiv.org/abs/2512.12806
- **E2B persistence**: https://docs.e2b.dev/sandbox/persistence · **Firecracker snapshot caveats**: https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md
- **Code execution with MCP** (150k → 2k tokens in one example): https://www.anthropic.com/engineering/code-execution-with-mcp
- **OpenClaw**: https://github.com/openclaw/openclaw · security docs: https://docs.openclaw.ai/gateway/security
- **Apple Foundation Models 2025** (2-bit QAT, KV sharing that cuts KV memory 37.5%): https://machinelearning.apple.com/research/apple-foundation-models-2025-updates · tech report: https://arxiv.org/abs/2507.13575
- **EXO prefill/decode split** (Spark + M3 Ultra, 2.8x): https://blog.exolabs.net/nvidia-dgx-spark/

### Reference
- **MemGPT**: https://arxiv.org/abs/2310.08560
- **OS-Copilot / FRIDAY**: https://arxiv.org/abs/2402.07456
- **LiteCUA / AIOS 1.0**: https://arxiv.org/abs/2505.18829
- **UFO2**: https://arxiv.org/abs/2504.14603
- **LLM as OS, Agents as Apps**: https://arxiv.org/abs/2312.03815
- **Windows**: Agent Workspace https://support.microsoft.com/en-us/windows/experimental-agentic-features-a25ede8a-e4c2-4841-85a8-44839191dfb3 · Agent Launchers https://learn.microsoft.com/en-us/windows/ai/agent-launchers/ · Phi Silica https://learn.microsoft.com/en-us/windows/ai/apis/phi-silica · Recall https://learn.microsoft.com/en-us/windows/apps/develop/windows-integration/recall/ · Recall security update https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/
- **Apple**: developer page https://developer.apple.com/apple-intelligence/ · Private Cloud Compute https://security.apple.com/blog/private-cloud-compute/ · Gemini joint statement https://blog.google/company-news/inside-google/company-announcements/joint-statement-google-apple/
- **Google**: Gemini Nano https://developer.android.com/ai/gemini-nano · Prompt API https://developer.chrome.com/docs/ai/prompt-api · Gemini Computer Use https://blog.google/technology/google-deepmind/gemini-computer-use-model/
- **Infrastructure**: Claude Code env vars https://code.claude.com/docs/en/env-vars · Vercel Sandbox https://vercel.com/docs/vercel-sandbox · Agentic workload characteristics https://arxiv.org/abs/2605.26297 · E2B pricing https://e2b.dev/pricing · k8s agent-sandbox https://github.com/kubernetes-sigs/agent-sandbox · Temporal AI cookbook https://docs.temporal.io/ai-cookbook · A2A https://a2a-protocol.org/latest/ · IETF agent OAuth draft (expired) https://datatracker.ietf.org/doc/draft-oauth-ai-agents-on-behalf-of-user/
- **Personal devices**: Screenpipe https://github.com/mediar-ai/screenpipe · Limitless notice https://www.limitless.ai/ · EXO https://github.com/exo-explore/exo · Mac Studio RDMA cluster https://www.jeffgeerling.com/blog/2025/15-tb-vram-on-mac-studio-rdma-over-thunderbolt-5

---

## 5. Implications for AWOS

The organizing idea is that **the Gatekeeper host is AWOS's kernel**. The OS-level mechanisms worth building are the scheduler, cache management, isolation and the verification gate. A "kernel" layer of metaphors adds nothing.

### Adopt now
1. **Raise the dev-harness cap and stop sharing state.** Set `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` (try 16, then 24) in the settings `env` block. Give each parallel agent its own worktree and owned paths. Keep `.awos/` ledger writers sequential, or split ledgers per agent. Expect rate limits, quota and the 200-search budget to become the next limit.
2. **Replace fixed worker constants with admission control.** `dag_executor.py` `MAX_WORKERS=4` and the best_of_n thread count should come from (a) free KV memory, calculated as (unified RAM − weights) / per-agent context, for local calls, and (b) the provider's rate-limit headroom for cloud calls.
3. **Run local models on a server that actually batches.** Don't use quantized KV (`--kv-bits`) when running concurrently on mlx_lm.server. Evaluate llama.cpp `--parallel` slots or a continuous-batching server. With Ollama, set `OLLAMA_NUM_PARALLEL` explicitly.
4. **Structured-tool-first computer use.** Order of preference: MCP/App Intents/WebMCP/AppFunctions, then the accessibility tree, then pixels. Pixels are for escalation only. Replaying verified routines is the main way a small local model becomes useful at computer use.
5. **Isolation ladder.** Tier 1: srt (Seatbelt) for agent shells and MCP servers. Tier 2: Apple Containerization Linux micro-VMs for untrusted test suites. Tier 3: a scarce macOS VM or a separate session for GUI work. Cloud sandboxes (E2B at ~$0.17 per hour for 2 vCPU / 4 GiB) are a burst tier. Credentials stay host-side and are brokered per task.
6. **Keep memory simple and auditable.** Plain files or SQLite, owner-exportable and encrypted at rest. Consider a git-backed memory directory with diff review before commit (the Letta MemFS pattern) as the concrete form of "memory only from strong evidence."

### Test (pre-register, measure work per $·s·W)
- **Concurrency sweep:** 1/2/4/8/16 local agents on the real-issue set, with prompt-cache reuse on and off. Report pass rate, wall time, aggregate tok/s and watts. Find the point where adding agents stops helping.
- **Continuum-style cache TTL on MLX/llama.cpp:** save and restore each agent session's prompt cache across pytest gaps. Measure the hit rate and the drop in re-prefill.
- **Autellix-style least-attained-service priority** on a mixed workload of short replays and long repair loops on one box.
- **Fork-and-try best-of-N vs sequential bounded repair:** fork from a post-setup snapshot (APFS clone or worktree) and compare on the 73-issue set.
- **Small local critic** (Chrome's User Alignment Critic pattern) as the T10 sink-policy check. Measure false-accept and false-reject rates on injection cases.
- **Typed tools vs accessibility tree** for 3-8B local models on a handful of Mac tasks. No public benchmark exists, so this would be a defensible contribution.

### Watch
- Pie-style programmable serving: verified routines compiled into inferlets that control generation and KV state. It is CUDA-only research today.
- MCP Tasks leaving experimental status and getting client support, so AWOS jobs can be exposed through it.
- Whether macOS opens App Intents (or MCP) to third-party agents.
- Windows ODR / Agent Launcher registration for unpackaged hosts.
- EXO prefill/decode split as a later-stage hardware option (pairing a compute-heavy device with a bandwidth-heavy one).
- OpenClaw as a possible channel gateway to interoperate with, instead of building our own UI. AWOS would compete on verification and learned routines.

### Ignore
- AIOS's speedup claims.
- Vector-DB memory products.
- Recall-style continuous capture.
- NPU TOPS.
- A2A.
- k8s sandboxes on the owner box.
- Agent count as a metric.
- OS-bundled models as the worker.

### Positioning
The platforms supply tool registries, containment and small models. They all route local→cloud by *availability*. The Gatekeeper's *verification-gated* escalation, plus verified routine replay, is the unoccupied position, and it is the piece to defend.

---

## 6. Open questions worth exploring next

1. Where does aggregate throughput stop scaling against concurrency for AWOS's actual local model on the 16 GB M-series host, and at what concurrency does KV memory run out? Does the installed mlx-lm build batch at all?
2. What concurrency does the CPU-derived Claude Code default actually give on this machine, and with the override at 24-32, which limit binds first: rate limits, RAM, or `.awos`/git collisions?
3. Can a deterministic replay journal record step results per item, so a failure in the middle of a fan-out doesn't rerun completed siblings (a known weakness of Claude Code workflow resume)?
4. What is the cheapest way on macOS to give the agent its own display session (virtual display, or a separate user session) without fighting the owner for input, as UFO2 and Windows Agent Workspace do?
5. Does Seatbelt isolation suffice for running untrusted repo test suites, or does the verification gate need micro-VMs? What does each cost?
6. Does git-backed memory with diff review beat evidence-gated JSONL ledgers on rollback and auditability, and what does the review step cost in tokens?
7. How many Apple Containerization Linux micro-VMs fit on 16 GB, and does the two-VM licensing limit really apply only to macOS guests? (That limit is the scout's inference and unverified.)
8. A follow-up search pass on late-2025 and 2026 AgentOS papers, HarmonyOS HMAF (UNVERIFIED this session) and independent evaluations of Recall-style filters, once the search budget refills.

---

## 7. Sources

- https://arxiv.org/abs/2502.13965
- https://arxiv.org/abs/2511.02230
- https://arxiv.org/abs/2403.16971
- https://arxiv.org/html/2403.16971v5
- https://github.com/agiresearch/AIOS
- https://arxiv.org/abs/2405.19888
- https://arxiv.org/abs/2507.07400
- https://arxiv.org/abs/2510.24051
- https://arxiv.org/abs/2310.08560
- https://www.letta.com/blog/benchmarking-ai-agent-memory
- https://docs.letta.com/guides/agents/architectures/sleeptime
- https://arxiv.org/abs/2504.13171
- https://arxiv.org/abs/2505.18829
- https://arxiv.org/abs/2402.07456
- https://arxiv.org/abs/2504.14603
- https://arxiv.org/abs/2312.03815
- https://learn.microsoft.com/en-us/windows/ai/mcp/overview
- https://learn.microsoft.com/en-us/windows/ai/mcp/servers/mcp-containment
- https://support.microsoft.com/en-us/windows/experimental-agentic-features-a25ede8a-e4c2-4841-85a8-44839191dfb3
- https://learn.microsoft.com/en-us/windows/ai/windows-ai-comparison
- https://learn.microsoft.com/en-us/windows/ai/apis/phi-silica
- https://learn.microsoft.com/en-us/windows/ai/agent-launchers/
- https://learn.microsoft.com/en-us/windows/apps/develop/windows-integration/recall/
- https://blogs.windows.com/windows-insider/2026/10/07/from-searching-to-doing-building-a-faster-more-streamlined-windows-search/
- https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/
- https://developer.android.com/ai/appfunctions
- https://developer.android.com/ai/gemini-nano
- https://android-developers.googleblog.com/
- https://developer.chrome.com/docs/ai/webmcp
- https://developer.chrome.com/docs/ai/prompt-api
- https://developer.apple.com/apple-intelligence/
- https://machinelearning.apple.com/research/apple-foundation-models-2025-updates
- https://arxiv.org/abs/2507.13575
- https://security.apple.com/blog/private-cloud-compute/
- https://blog.google/company-news/inside-google/company-announcements/joint-statement-google-apple/
- https://blog.google/security/architecting-security-for-agentic/
- https://blog.google/technology/google-deepmind/gemini-computer-use-model/
- https://firebase.google.com/docs/ai-logic/hybrid-on-device-inference
- https://arxiv.org/abs/2509.26539
- https://developer.huawei.com/consumer/en/doc/harmonyos-guides/agent-framework-overview (unreachable; claim UNVERIFIED)
- https://code.claude.com/docs/en/workflows
- https://code.claude.com/docs/en/env-vars
- https://vercel.com/docs/vercel-sandbox
- https://arxiv.org/abs/2605.26297
- https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md
- https://github.com/anthropic-experimental/sandbox-runtime
- https://www.anthropic.com/engineering/claude-code-sandboxing
- https://github.com/apple/containerization
- https://docs.e2b.dev/sandbox/persistence
- https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md
- https://arxiv.org/abs/2512.12806
- https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks
- https://modelcontextprotocol.io/specification/2025-11-25/changelog
- https://www.anthropic.com/engineering/code-execution-with-mcp
- https://docs.temporal.io/ai-cookbook
- https://arxiv.org/abs/2503.18813
- https://a2a-protocol.org/latest/
- https://datatracker.ietf.org/doc/draft-oauth-ai-agents-on-behalf-of-user/
- https://e2b.dev/pricing
- https://github.com/kubernetes-sigs/agent-sandbox
- https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/
- https://docs.ollama.com/faq
- https://blog.exolabs.net/nvidia-dgx-spark/
- https://github.com/exo-explore/exo
- https://www.jeffgeerling.com/blog/2025/15-tb-vram-on-mac-studio-rdma-over-thunderbolt-5
- https://www.limitless.ai/
- https://github.com/openclaw/openclaw
- https://docs.openclaw.ai/gateway/security
- https://github.com/mediar-ai/screenpipe
- Local: scaffold/agent/dag_executor.py:23 (`MAX_WORKERS = 4`, verified in repo)

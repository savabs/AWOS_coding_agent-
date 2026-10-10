# Gap 06: Capability-based OS design and OS-native mediation for agents

*Expedition gap chart, 2026-10-10. Builds on chart 20 (security-memory), which already covers Seatbelt/srt as a sandbox floor and CaMeL as a defence. This chart treats the "agent's own computer" as an **authority problem**: who holds which rights, for how long, and how the OS proves what was used.*

## Summary

Object-capability (ocap) discipline says authority should travel only with an unforgeable reference you were handed, never with an ambient identity ("I am the user, so I can touch everything"). Fuchsia, seL4, Capsicum and WASI show that this works at OS scale. Fuchsia's components "can interact with the system and other components only through the discoverable capabilities from its namespace" ([Fuchsia](https://fuchsia.dev/fuchsia-src/concepts/components/v2/capabilities)). An always-on agent host cannot switch to a capability OS. It can still get about 80% of the benefit on macOS and Linux today. The Gatekeeper compiles each task or routine into a **capability manifest**, and the OS enforces it with Seatbelt or Landlock, a per-task user or worktree, and an egress proxy. Then the OS's own event stream (Endpoint Security, eBPF, Landlock audit) is checked against the manifest as a **verification signal**.

The 2025-26 vendor models (Windows agent workspace and On-device Agent Registry, Android AppFunctions, Apple App Intents) all grant authority **per agent or per host app**, not per task. That is the gap AWOS can fill.

## What matters

**1. Ocap makes least privilege automatic, not configured.** In an ocap system the question "what may this tool call do?" is answered by "what references was it passed?". There is no separate policy that can drift from the code. Practical findings:
- **Capsicum** (FreeBSD) retrofitted capability mode and rights-limited file descriptors onto POSIX. It sandboxed tcpdump, gzip, OpenSSH and dhclient, and Chromium adopted it ([Cambridge](https://www.cl.cam.ac.uk/research/security/capsicum/)). Lesson: retrofitting works when the program is split into "open resources with full authority, then enter capability mode". This matches **plan, then execute** for agents.
- **Fuchsia** routes every capability explicitly through the component tree with `use`/`offer`/`expose`, and parents define their children's sandboxes ([Fuchsia](https://fuchsia.dev/fuchsia-src/concepts/components/v2/capabilities)). Lesson: a declarative manifest plus a parent that issues it is a workable model. The Gatekeeper is the parent and the task is the child.
- **seL4** is a formally verified capability microkernel ([seL4](https://sel4.systems/About/)). It is relevant to future dedicated "AI box" firmware, not to a Mac host today.
- **Agent-level ocap results:** CaMeL attaches capabilities to values and kept 77% of AgentDojo tasks with provable security, against 84% undefended ([arXiv 2503.18813](https://arxiv.org/abs/2503.18813)). MiniScope (Berkeley) builds task-centric permission hierarchies. It cut confirmation prompts by 43-89% against per-tool approval, blocked all tested privilege-escalation attacks, and found six overprivileged connector configurations in real ChatGPT and Claude deployments ([arXiv 2512.11147](https://arxiv.org/abs/2512.11147)). Lesson: **scoping by task beats prompting per tool call**, both for safety and for owner attention, which is a real cost term.

**2. Mechanisms an always-on host can use today.**
- **macOS Seatbelt (`sandbox-exec`)**: srt generates a profile for each command with allowed read and write paths. Network may reach only a localhost proxy port, and violations come from the system sandbox log store and are attributed to each command ([srt](https://github.com/anthropic-experimental/sandbox-runtime)). Read is deny-then-allow and write is allow-only. srt always blocks writes to shell rc files, `.git/hooks`, `.git/config`, `.mcp.json` and `.claude/commands`, which are the persistence vectors.
- **Linux Landlock**: an unprivileged process restricts *itself*. It is stackable, has no root and no daemon, and is applied with `landlock_restrict_self`. The ABI has grown from filesystem only (v1) to TCP bind and connect (v4), device ioctl (v5), abstract-unix-socket and signal scoping (v6), audit-log control (v7), thread-sync enforcement (v8), UDP (v10) and atomic `no_new_privs` (v11) ([kernel.org](https://www.kernel.org/doc/html/latest/userspace-api/landlock.html)). This is the closest mainstream mechanism to ocap: a worker drops ambient rights after the Gatekeeper hands it open handles.
- **bubblewrap plus network-namespace removal** (srt on Linux): the process loses its network stack entirely and reaches the proxy only over a unix socket ([srt](https://github.com/anthropic-experimental/sandbox-runtime)).
- **Separate OS user per agent**: this is Microsoft's choice (below). On macOS, a dedicated `_awos` user plus POSIX ACLs on the owner's folders gives each task an identity the audit layer can tell apart from the owner.

**3. Vendor agent permission models (2025-26).**
- **Windows**: Copilot Actions runs in an **agent workspace**, a separate Windows session under a dedicated agent account. In preview it can reach only known folders (Documents, Downloads, Desktop, Pictures) behind ACLs, agents must be signed and revocable, and it is off by default ([Windows blog](https://blogs.windows.com/windowsexperience/2025/10/16/securing-ai-agents-on-windows/)). The **On-device Agent Registry (ODR)** runs contained MCP servers in that agent session. Calls go through a trusted MCP proxy that does authentication, authorization and auditing, and Intune controls access per agent ([MS Learn](https://learn.microsoft.com/en-us/windows/ai/mcp/overview)). **The gap:** "Permissions to user files are granted for the host and not per server", so one granted server opens the files to every server under that host. Contained servers still "access the internet" freely, unpackaged MCP bundles cannot be contained at all, and there is a setting to "Reduce protections for agent connectors" ([MS Learn containment](https://learn.microsoft.com/en-us/windows/ai/mcp/servers/mcp-containment)).
- **Android AppFunctions** (Android 16+): apps expose typed functions indexed from a schema, and callers need `EXECUTE_APP_FUNCTIONS`. On most Android 16 builds that permission is privileged and system-only, and the overview documents no per-call user consent ([Android](https://developer.android.com/ai/appfunctions)). **The gap:** the permission is all-or-nothing per caller. There is per-function enable state but no per-task scoping and no argument constraints.
- **Apple**: App Intents and TCC grant per app, not per task. Endpoint Security now exposes `ES_EVENT_TYPE_NOTIFY_TCC_MODIFY` and XPC-connect authorization events ([Apple ES](https://developer.apple.com/tutorials/data/documentation/endpointsecurity.md)), so an agent host can at least *observe* TCC grants changing. (Not fetched this session: Apple's 2026 agent-specific intents documentation. Treat Apple's agent model as unverified here.)
- **What all three leave out**: time-bounded grants, task-scoped grants, argument-level constraints (which repo, which recipient, what dollar cap), data-flow (taint) tracking, and a machine-readable receipt of what was actually used. Their unit is "agent X may use connector Y". AWOS needs "task T may write `worktree/` and reach `pypi.org` for 20 minutes".

**4. OS audit as a verification signal.**
- **Endpoint Security** (macOS): a C API with AUTH events (allow or deny, with deadlines and a fail-open, fail-closed or kill miss mode) and NOTIFY events covering exec, fork, mount, signals, TCC changes and more. Crucially, `es_new_descendants_client` creates "a new ES client scoped to descendant processes only" ([Apple ES](https://developer.apple.com/tutorials/data/documentation/endpointsecurity.md)), which is exactly the process tree of one agent task. It needs the `com.apple.developer.endpoint-security.client` entitlement, delivered as a system extension.
- **eBPF / Tetragon** (Linux): kernel-level filtering on exec, syscalls, file and network I/O with process lineage. It can enforce in the kernel, for example by killing a process before a suspicious syscall completes ([Tetragon](https://tetragon.io/docs/overview/)).
- **Why this is verification, not just logging:** the Gatekeeper's gate already checks tests and state probes. A **manifest-diff probe** adds a check that is independent of the model: the set of files written, hosts contacted and binaries executed (from ES/eBPF/srt violations) must be a subset of what the manifest declared *and* of what the task plausibly needed. A diff that passes tests but also touched `~/.ssh`, contacted an undeclared host, or spawned `curl | sh` fails the gate. It costs no LLM call. The same trace becomes the **routine's compiled footprint**, so a replayed routine whose syscall footprint deviates from its recorded one is a drift alarm (chart 04's postcondition idea, at OS level).

## What does not matter (for AWOS now)

- **Porting to seL4, Fuchsia or a capability OS.** The owner's machine runs macOS or Linux. The gains come from the discipline, not the kernel.
- **Per-tool-call human approval.** MiniScope shows that per-tool prompting is the expensive end of the trade-off.
- **Waiting for vendor agent APIs.** Windows ODR and AppFunctions are preview-gated or privileged-only, and none of them is task-scoped.
- **Full IFC on every step.** Chart 20 already prices CaMeL-everywhere as too costly for weak local planners. OS-level scoping is the cheap complement.
- **Kernel-level AUTH blocking via ES as the primary enforcer.** Deadline misses kill the client by default. Use Seatbelt or Landlock to enforce, and ES NOTIFY to observe.

## Key resources

- https://fuchsia.dev/fuchsia-src/concepts/components/v2/capabilities: the cleanest production model of a manifest-routed, no-ambient-authority sandbox. The template for the manifest schema.
- https://www.kernel.org/doc/html/latest/userspace-api/landlock.html: the Landlock ABI table. Decides which rights a Linux host can drop without root.
- https://github.com/anthropic-experimental/sandbox-runtime: per-command Seatbelt, bubblewrap and Windows-ACL profiles, a proxy allowlist, and per-command violation attribution. The ready-made enforcement backend.
- https://developer.apple.com/tutorials/data/documentation/endpointsecurity.md: ES events, deadline modes, the descendants-scoped client, TCC-modify events.
- https://tetragon.io/docs/overview/: an eBPF observe-and-enforce reference for the Linux host.
- https://learn.microsoft.com/en-us/windows/ai/mcp/servers/mcp-containment: the most concrete vendor agent-containment spec, including its stated per-host weakness.
- https://blogs.windows.com/windowsexperience/2025/10/16/securing-ai-agents-on-windows/: agent accounts, workspace, signing.
- https://developer.android.com/ai/appfunctions: a typed function exposure with a coarse caller permission.
- https://arxiv.org/abs/2512.11147: MiniScope, task-scoped permission hierarchies, with prompt-reduction numbers.
- https://arxiv.org/abs/2503.18813: CaMeL, value-level capabilities.
- https://www.cl.cam.ac.uk/research/security/capsicum/: retrofitting capabilities onto POSIX in practice.
- https://sel4.systems/About/: a verified capability microkernel, for a future AI-box base.

## Implications for AWOS: the per-task capability manifest

The Gatekeeper issues one manifest per task, or per routine version. The worker never sees credentials or ambient paths, only what the manifest grants.

```yaml
manifest: v1
task_id: t_2026_10_10_0042
routine: null | r_pytest_fix@v3        # replay uses the routine's frozen manifest
principal: _awos-t0042                 # ephemeral OS user or Seatbelt/Landlock domain
ttl_s: 1200                            # hard expiry; host kills the process tree
fs:
  read:  [worktree/, ~/.cache/pip/ (ro)]
  write: [worktree/, $TMPDIR/t0042/]
  deny:  [~/.ssh, ~/.aws, .git/hooks, .git/config, shell rc files]   # always-on floor
net:
  egress: [pypi.org:443, files.pythonhosted.org:443, <model endpoint>]  # via proxy
  dns: proxy-only; ip_literals: deny
exec:
  allow: [python3, pytest, git(status|diff|add|commit)]   # no push; push is a sink
sinks:                                  # irreversible actions need an escalation token
  git_push: escalate; send_message: escalate; spend_usd_max: 0.50
secrets: []                             # injected by the proxy, never in the process env
gui:                                    # computer-use tier only
  apps: [com.apple.Safari]; tcc: [] ; workspace: vm|agent-user
provenance:
  observe: [exec, fs_write, net_connect, tcc_modify]   # ES descendants client / eBPF / srt log
  expect_footprint: routine.footprint | null
```

**Enforcement mapping:** fs and net map to srt (Seatbelt on macOS; Landlock plus bubblewrap on Linux) with the proxy. The principal maps to a per-task user or worktree. The TTL maps to a launchd/systemd timer plus process-group kill. Sinks are handled by the Gatekeeper itself, outside the sandbox.

**Gate addition:** `observed ⊆ manifest` is a hard fail, and `observed ⊆ expected_footprint` (for routines) is a drift flag that demotes the routine to a supervised run. **Memory rule:** a routine is promoted only with its footprint recorded, and the footprint *is* its least-privilege manifest next time. Authority shrinks as routines mature.

**Narrowing is cheap:** a lower-tier worker (local model) gets a strictly smaller manifest than a cloud-escalated attempt. Cloud escalation sends context, not authority.

## Open questions

1. How should a manifest be derived automatically for a novel task: from the plan (Capsicum-style "open, then confine"), from a dry run, or by MiniScope-style hierarchy mining? Measure prompts per task and false denials.
2. Can a distributable AWOS host get the ES entitlement, or must macOS provenance come from the Seatbelt violation log plus `fs_usage`/`eslogger`-style tools?
3. How much does a per-task OS user cost in latency and watts compared with a Seatbelt profile alone on an M-series host?
4. Does the manifest-diff probe catch real failures in the 73-issue set (for example tests that pass but stray writes happen) at a useful rate, or only adversarial ones?
5. GUI computer use: TCC grants are per app and sticky. Is a VM or a separate macOS user the only way to get task-scoped Accessibility and Screen Recording?

## Sources

All of these were fetched this session except where marked. The web-search budget ran out partway through, so Apple's 2026 agent and App Intents permissions, KeyKOS/EROS and WASI were not freshly researched and are not cited as evidence.

- Fuchsia capabilities: https://fuchsia.dev/fuchsia-src/concepts/components/v2/capabilities
- Landlock: https://www.kernel.org/doc/html/latest/userspace-api/landlock.html
- sandbox-runtime: https://github.com/anthropic-experimental/sandbox-runtime
- Apple Endpoint Security: https://developer.apple.com/tutorials/data/documentation/endpointsecurity.md
- Tetragon: https://tetragon.io/docs/overview/
- Windows agent security: https://blogs.windows.com/windowsexperience/2025/10/16/securing-ai-agents-on-windows/
- MCP on Windows: https://learn.microsoft.com/en-us/windows/ai/mcp/overview
- MCP containment on Windows: https://learn.microsoft.com/en-us/windows/ai/mcp/servers/mcp-containment
- Android AppFunctions: https://developer.android.com/ai/appfunctions
- CaMeL: https://arxiv.org/abs/2503.18813
- MiniScope: https://arxiv.org/abs/2512.11147
- Capsicum: https://www.cl.cam.ac.uk/research/security/capsicum/
- seL4: https://sel4.systems/About/

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

Method note: the arXiv API returned HTTP 429 for every call (shared quota), so arXiv was searched through the arxiv.org/search HTML pages and each paper below was confirmed on its abs page. GitHub and HN APIs worked. The Hugging Face API was rate limited. Semantic Scholar was not used.

### New since the chart (dated, with URLs; most important first)

1. **Task-scoped capability leases are now an active research line (Aug-Oct 2026).** This is the chart's "gap AWOS can fill", and others are filling it too.
   - IntentCap, arXiv 2609.14631 (13 Sep 2026, AgenticOS 2026, 3-page paper): permissions are composed from four sources (user intent, workflow, tool schema, runtime) with "field-level ownership and monotonic narrowing". An LLM proposes short-lived leases and a deterministic checker validates them. https://arxiv.org/abs/2609.14631 . This maps closely onto the manifest plus TTL design, including narrowing only. Compare the manifest schema against it.
   - Pincer, arXiv 2610.02569 (1 Oct 2026, Popa and Stoica groups): a "digital twin" that learns per-user least-privilege policies and answers permission requests at the resource layer. https://arxiv.org/abs/2610.02569 . It targets the prompt-fatigue and policy-decay problem and answers open question 1 (automatic manifest derivation) from the learning side. No numbers were in the fetched summary.
   - Task-Conditioned Least-Privilege Learning, arXiv 2608.18351 (18 Aug 2026): a 4B model post-trained to stay within task authority reached 98.48% safe success on 2,896 episodes vs 64.36% for the baseline, and excess-authority errors fell from 4.56% to 0.79%. The authors say it "does not replace permission gates and sandboxing". https://arxiv.org/abs/2608.18351 . These are self-reported synthetic-environment results. Treat them as complementary only.
   - Aethelgard, arXiv 2604.11839 (12 Apr 2026, rev. 3 May): reports a "15x overprovision ratio" between a summarization task and a deploy task, and uses PPO to learn minimal skill sets. https://arxiv.org/abs/2604.11839 . The RL part is deferred territory for AWOS. The overprovisioning measurement is a usable baseline idea.
2. **MiniScope v2 (6 Oct 2026).** The paper the chart leans on was revised on 6 Oct 2026. v1 was 11 Dec 2025. The abstract still gives 43.4%-89.4% fewer confirmations and six overprivileged connector configurations. https://arxiv.org/abs/2512.11147 . I did not diff v1 against v2. Re-read v2 before quoting more than the abstract numbers.
3. **Kernel evidence for agent security: direct support for the manifest-diff probe.** arXiv 2609.28915 (24 Sep 2026) introduces ACE with 4,047 sessions, 17 threat models and 14 OWASP LLM categories. It finds kernel syscall traces are discriminative alone and that composing them with application telemetry "generally outperforms either single-layer view". https://arxiv.org/abs/2609.28915 . This is the first outside evidence for open question 4. It is about detection of attacks, not about stray writes on benign tasks, so the 73-issue measurement is still needed.
4. **Agent libOS, arXiv 2606.03895 (2 Jun 2026, rev. 18 Aug).** A runtime with operation admission (typed capabilities, authority limits), information-flow admission and durable causal evidence. It reports 33/33 deterministic tasks passing, and says it does not provide kernel-grade sandboxing or stop prompt injection. https://arxiv.org/abs/2606.03895 . It is a closely related user-space design and a source of schema ideas.
5. **Aletheia, arXiv 2609.39678 (30 Sep 2026).** It converts requested permissions into sandbox configurations of varying strictness and checks whether the task still passes. It caught all 314 AIShellJack inputs with no false alarms on benign templates, and had a 3.75% false-positive rate (3 of 80) on real GitHub rule files. https://arxiv.org/abs/2609.39678 . This is a ready method for deriving a minimal manifest from a dry run (open question 1). Numbers are self-reported.
6. **Boundary-incident paper, arXiv 2610.12463 (8 Oct 2026).** It documents three 2026 evaluation incidents in which agents reached real systems beyond their test environment, and argues the boundary "must be verified while the agent is operating". https://arxiv.org/abs/2610.12463 . This supports observed-versus-declared checking. It is a single-author secondary analysis, so cite it with care.
7. **Landlock ABI v9 was missing from the chart.** The kernel doc lists v9 as `LANDLOCK_ACCESS_FS_RESOLVE_UNIX`, which restricts connections to pathname UNIX sockets. https://www.kernel.org/doc/html/latest/userspace-api/landlock.html . This matters for the egress design: the Linux proxy path is a unix socket, so a v9-restricted worker can be limited to exactly that one socket.
8. **sandbox-runtime has grown beyond the chart's description.** The README now lists Windows (alpha, dedicated `srt-sandbox` user, WFP and NTFS ACLs), seccomp blocking of `AF_UNIX`, per-command network allowlists (`registerCommandNetworkLists`), a request-filter callback, experimental TLS termination, a standalone `srt proxy`, and masked credential injection. Releases ran to v0.0.79 on 2026-10-07. https://github.com/anthropic-experimental/sandbox-runtime . Several manifest fields (per-task egress, secrets injected by the proxy) now have a native backend. The README lists limits worth noting: the seccomp rule does not stop passed file descriptors, and `allowAllUnixSockets: true` disables unix-socket blocking on macOS.
9. **Community tools converge on the same design, and one has a learn mode.** Greywall (Apache-2.0, 309 stars, last push 2026-08-13) uses per-command profiles and a `--learning` mode that traces file access (strace on Linux, eslogger on macOS) to generate a profile. https://github.com/GreyhavenHQ/greywall . This is a working example of Capsicum-style footprint-to-manifest, and it answers open question 2 in part: eslogger works without shipping a custom ES entitlement. Others in the same space are vetto (https://github.com/shleder/vetto), agent-jail (https://github.com/Michaelliv/agent-jail) and langchain-nono (https://github.com/nolabs-ai/langchain-nono).
10. **Credential-brokering proxies became a product category (Apr-Aug 2026).** Infisical Agent Vault (HN 2026-04-22, 156 pts) substitutes dummy values for real credentials and mints short-lived vault-scoped tokens. https://github.com/Infisical/agent-vault . OneCLI (HN 2026-07-23, 110 pts) injects credentials via a MITM gateway and enforces team policy. https://github.com/onecli/onecli . Deno's Claw Patrol (MIT, HN 2026-06-09) parses wire-level SQL, Kubernetes and HTTP traffic and applies HCL/CEL rules with an approval option. https://github.com/denoland/clawpatrol . Claw Patrol's argument-level rules (SQL verb, table, HTTP path) are what the chart said vendors lack. Both Agent Vault and OneCLI have `ee/` enterprise directories.
11. **SideKernel, arXiv 2610.02456 (1 Oct 2026).** An open-source microVM sandbox for macOS coding agents. A survey in the paper found fewer than 40% of agent users use any sandbox. https://arxiv.org/abs/2610.02456 . It is a Georgia Tech practicum project. It is a candidate answer to open question 5 (GUI isolation through a VM).

### Corrections

- Chart: "The ABI has grown from filesystem only (v1) to TCP bind and connect (v4), device ioctl (v5), abstract-unix-socket and signal scoping (v6), audit-log control (v7), thread-sync enforcement (v8), UDP (v10) and atomic `no_new_privs` (v11)." -> v8, v10 and v11 match the kernel doc, but **v9 (pathname UNIX socket restriction) is omitted**. v10 also adds a `LANDLOCK_ADD_RULE_QUIET` flag for suppressing logs. https://www.kernel.org/doc/html/latest/userspace-api/landlock.html . I did not re-check v1-v7 here.
- Chart: srt generates "Windows-ACL profiles" (Key resources line) alongside Seatbelt and bubblewrap. -> the README describes Windows support as alpha, with no per-exec filesystem override, a DNS path not fenced by WFP, and the proxy token visible in the runner's command line. https://github.com/anthropic-experimental/sandbox-runtime . Do not count it as a Windows enforcement backend yet.
- Chart: "On most Android 16 builds that permission is privileged and system-only." -> the current page says only that callers "must have" `EXECUTE_APP_FUNCTIONS` and that callers "can include agents, apps, and AI assistants like Gemini". It does not say the permission is system-only, and it marks AppFunctions as an "experimental preview". https://developer.android.com/ai/appfunctions . Downgrade the "privileged" claim to unverified. The "no per-call consent documented" claim still holds.

### Confirmed claims

- CaMeL: 77% of AgentDojo tasks with provable security vs 84% undefended (https://arxiv.org/abs/2503.18813).
- MiniScope: 43.4%-89.4% fewer confirmation prompts and six overprivileged connector configurations in ChatGPT and Claude (https://arxiv.org/abs/2512.11147). The chart's "43-89%" is a fair rounding. The "blocked all tested privilege-escalation attacks" claim was not in the fetched abstract summary and is not rechecked.
- Windows containment: "Permissions to user files are granted for the host and not per server", contained servers can access the internet, MCP bundles cannot be contained, and a "Reduce protections for agent connectors" setting exists. The page is still marked pre-release, and its metadata shows a last update of 2025-12-04 (https://learn.microsoft.com/en-us/windows/ai/mcp/servers/mcp-containment).
- Windows agent workspace: known folders (Documents, Downloads, Desktop, Pictures), off by default, signed and revocable agents, still an experimental Insider preview (https://blogs.windows.com/windowsexperience/2025/10/16/securing-ai-agents-on-windows/).
- Landlock v8 thread sync, v10 UDP, v11 `no_new_privs`.

### Still unverified

- Apple's 2026 agent-specific intents and permission model (not fetched). The ES `es_new_descendants_client` and TCC-modify claims were not rechecked, and whether a distributable app can obtain the ES entitlement (open question 2) is still open.
- Capsicum, Fuchsia and seL4 claims were not rechecked. Tetragon's latest release found was v1.7.1 on 2026-08-25.
- Per-task OS user latency and watts on M-series (open question 3) have no data in any source fetched.
- None of the new papers was run or reproduced. Their numbers are self-reported, and Aletheia, Pincer, IntentCap and the least-privilege-learning paper were read only as abstracts.
- The HN "State of MCP Security 2026" PDF (https://news.ycombinator.com/item?id=48884647) was not read, only the discussion.

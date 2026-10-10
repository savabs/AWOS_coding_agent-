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

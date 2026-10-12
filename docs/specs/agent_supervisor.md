# AWOS Supervisor: spec, design and architecture (v2)

Status: v2 proposed 2026-10-12. It replaces v1 (commit 3a1b4b4) and is based on the evidence in [`docs/research/agent_supervisor_research_2026-10.md`](../research/agent_supervisor_research_2026-10.md), cited below as **[R §x]**. Product idea 1 of the AWOS roadmap. Idea 2, "show me once, it does it forever", builds on it.

**Naming.** Claude Code already ships a background "supervisor" process (`claude daemon`) [R §2]. In docs and logs this product is always the **AWOS Supervisor**. The CLI is `awos supervise` and the daemon process is `awos-supervisord`. Renaming the product to "AWOS Watch" is an open owner decision (§12).

## 0. What changed from v1 and why

| v1 said | v2 says | Why [R] |
|---|---|---|
| Codex: chain the single `notify` program; interactive TUI is notify-only | Codex: `~/.codex/hooks.json` command hooks through a byte-stable fail-open shim, plus a rollout tail. `notify` is never touched. Live TUI continue is enabled only after an M0 spike proves a Stop `block` continues a session on the installed version. | Codex 0.148 has stable hooks with a blocking Stop. `notify` is legacy and slated for removal, and its slot belongs to Computer Use. Nobody has yet seen a block continue a 0.148 TUI session. [R §7, §8.1-1] |
| Install by editing `~/.claude/settings.json` | Install as a **local Claude Code plugin**. settings.json is never edited by default. Drift detection runs continuously. | Claude rewrites settings.json and drops third-party hooks (#93742, #88113). [R §3] |
| Stop hook "must answer within budget" | Hook entry sets an explicit `timeout: 15`. The daemon's own deadline is about 8 s, after which it returns `{}`. | The default hook timeout is 600 s. [R §3] |
| Build usage-limit resume | **Defer to native quota auto-resume on Claude.** Observe it, verify it, and catch misses. Build reset-resume only for headless Codex. | Claude has shipped quota auto-resume. `-p --resume` on a live session forks a copy. [R §1 P1, §8.1-3] |
| Classifier order: rules, then LLM | Layer-0 deterministic guards come first, then a **two-key** continue rule (rules and a second key must both agree), with structured fields | LLM judges are uneven. Auto mode has a 17% FNR on approval-shaped text. [R §4] |
| `waiting_background`: "wait; re-check" | Never continue. Suppress false idle alerts. Ambient status. `bg_task_lost` after sleep. | Covers 44% of the owner's triggers. #98373, #63023, #93745. [R §1 P6] |
| KPI: supervision prompts per agent-hour | KPI: **attention cost per agent-hour** = owner supervision prompts + interrupting Supervisor notifications, reported as two parts | Otherwise the metric can be gamed by turning prompts into pings. [R §6] |
| Trial: 2 weeks on vs the prior 2 weeks off | **Day-randomised** trial within the same period, with shadow logging on off-days | The baseline already fell from 2.4 to 1.6 with no Supervisor. Weekly vendor releases confound a before/after design. [R §8.3] |
| "done" goes to the digest | "done" is labelled **verified** or **unverified** by async checks run after the stop | False "done" claims are common. [R §4.4] |
| Notifications: one per issue | Three delivery tiers, breakpoint-aware, storm grouping, a context template, at most one re-notify, and dedupe against native push | Human-factors evidence. [R §6] |
| Nothing about coexistence | Detect `/goal`, native quota resume and other Stop hooks, and **never double-continue** | Double continuation burns quota and reproduces the consent backlash. [R §4.2, §8.1] |
| No go/no-go before building | M0 re-baselines the addressable slice, with a **kill/pivot criterion** | The addressable slice may be small. [R §1.1, §8.3] |

## 1. Problem (measured, then re-checked)

`work_miner` v1 on the owner's data covers 644 prompts, 36 days, 22 sessions and 79.7 agent-hours.

- 30% of what the owner types is supervision: status 93, continue 80, keys 21. That is 2.4 supervision prompts per agent-hour at baseline and **1.6 recently, with no Supervisor**.
- What precedes a supervision prompt:
  - a background task still running: 44%;
  - the agent ended its turn: 19%;
  - a long silence: 16%;
  - the agent asked a question: 12%;
  - a key or auth error: 7%.

**What v1 got wrong:** much of this is now partly handled natively (agent view, background-task wake-up, quota auto-resume, `/goal`) [R §1.1]. The slice that genuinely needs *auto-continue* is mainly "turn ended with work left", at most about 19% of triggers. Most of the remaining value is **attention**:
- answer "status?" before it is asked;
- one good interruption instead of many bad ones;
- grouped key alerts;
- stall and lost-task detection;
- verified "done".

M0 measures this split on current tool versions before auto-continue is built.

## 2. Positioning

Parallel-agent workspaces (vibe-kanban, cmux, superset, claude-squad, Orca) launch agents in their own app and show state. Notify daemons (Lucarne, peon-ping, Kodama) forward prompts to a phone. Blunt loops (claude-auto-retry, Ralph) continue on one trigger. The vendors' own `/goal`, agent view and quota auto-resume work for one tool, one session at a time. **The AWOS Supervisor sits above all of them, wherever the agents already run (no PTY or tmux wrapping).** For every stop, across Claude Code, Codex and AWOS jobs, it decides under one standing owner policy whether the stop is routine or a real decision. It never answers a question on the user's behalf. It keeps per-session budgets and an audit trail, checks "done" against evidence, and reports its value as a measured drop in owner attention cost. The clearest differentiator is **safe, cross-tool, policy-driven stop handling with a verified-done digest and a measured result**. Notifications and dashboards are table stakes [R §2].

## 3. Stories, tied to ranked pains

| # | Story | Pain [R §1] |
|---|---|---|
| S1 | I glance at the menubar or statusline and see which of my agents need me, which are working (and on what) and which are waiting on a background task, so I don't have to type "status?". | P3, P6 (status 93/644, bg 44%) |
| S2 | When an agent ends its turn with planned work left and nothing risky next, it continues within my policy, and I can rewind it. | P2 (19%) |
| S3 | When an agent truly needs me (a real question, a risky next step, a permission prompt, a loop), I get **one** notification naming the project, session, what it needs and a suggested answer. It never auto-answers. | P3, P7, P9 (question 12%) |
| S4 | When keys or credits die, I get one grouped alert with the fix, and every affected session stops continuing. | P5 (7%) |
| S5 | When an agent hangs, loops or loses a background task across laptop sleep, I'm told once. | P4, P6, P8 (silence 16%) |
| S6 | When usage limits hit, native resume does its job. I'm told only if it didn't, and headless Codex jobs resume at reset. | P1 |
| S7 | My digest says what's done **and verified**, what claims done but failed checks, what's stuck, cost, and what the Supervisor did. | P10, P11 |
| S8 | Every week I see my attention cost per agent-hour, and the trial tells me honestly whether the Supervisor helped. | proof |

**Non-goals for v2:**
- No auto-approval of permission prompts.
- No cloud service.
- No PTY or tmux wrapping.
- No new agent workspace or mobile app.
- No launching agents (that is a later "agent launcher").
- No editing of the owner's statusLine or the Codex `notify` slot.

## 4. Requirements

Priorities: **M** = must for v1, **S** = should for v1.x, **L** = later. "Evidence" links point to the research file.

| ID | Requirement | Pri | Evidence |
|---|---|---|---|
| R1 | **Fail open everywhere.** Never set `onFailure: block`. The Claude Stop path uses `type: http`, `timeout: 15`. The daemon's internal deadline is 8 s, and on a miss it returns `{}` and audits `classify_timeout`. Codex shim: stdin deadline, total runtime ≤1.5 s, always exit 0. | M | [R §3](../research/agent_supervisor_research_2026-10.md#E-reliability) |
| R2 | Claude hooks ship as a local plugin. Port and token come from `${CLAUDE_PLUGIN_DATA}` or the environment, never hardcoded. settings.json fallback only on explicit request (backup, diff, compare-and-swap, atomic write). | M | [R §3](../research/agent_supervisor_research_2026-10.md#E-reliability) |
| R3 | **Drift detection.** On start, every 10 min and on `ConfigChange`: our plugin is enabled once and points at the live port and token; the Codex hook is present, unchanged and trusted. If not, show a "SUPERVISOR DISARMED" banner in status, digest and `doctor`. Never repair silently. | M | [R §3](../research/agent_supervisor_research_2026-10.md#E-reliability) |
| R4 | **Lossy-input ingestion.** Hooks are at most once. Non-blocking events fall back to a spool directory. A reconciler fills gaps from `claude agents --json --all` (every 15 s), transcripts and Codex rollouts. All reads are read-only, by byte offset, with capped bytes per scan. | M | [R §3](../research/agent_supervisor_research_2026-10.md#E-reliability) |
| R5 | **Idempotent store + at-most-once actions.** SQLite WAL, one writer, `synchronous=FULL`, `event_id` = sha256(source, session, canonical payload) UNIQUE. Outbox keyed by (session, turn key). A repeated Stop for the same turn returns the same decision without counting it again. | M | [R §3](../research/agent_supervisor_research_2026-10.md#E-reliability) |
| R6 | **Layer-0 guards** (deterministic; override everything): `background_tasks` non-empty → never continue. StopFailure auth/billing → blocked_credentials. rate/overloaded → blocked_rate (defer to native). Never-list hit in the last message or the announced next step → needs_decision. Unknown agent state or untested tool major version → notify-only. | M | [R §4.1](../research/agent_supervisor_research_2026-10.md#E-signals) |
| R7 | **Two-key continue.** Continue only if: no Layer-0 hit; rules find no question or escalation marker and do find a routine marker; a second key agrees (local LLM p ≥ τ, **or** structural evidence: open TodoWrite or plan items remain and the message ends with a permission-to-proceed phrasing); and the progress guard passes. Either key can escalate alone; neither can continue alone. On doubt, allow the stop and notify. | M | [R §4.4](../research/agent_supervisor_research_2026-10.md#E-judges), [§8.1-8](../research/agent_supervisor_research_2026-10.md#E-critique) |
| R8 | **Caps below the platform's.** Max 3 consecutive per session, each needing progress (diff fingerprint changed, or a non-read tool call, or tests run); at most 1 no-progress continue, then stop and report "stalled"; max 3 per owner prompt; max 6 per session-hour; per-continue cost estimate counts toward the cost cap. Log a block that still ended the turn (silent cap of 8). | M | [R §4.2](../research/agent_supervisor_research_2026-10.md#E-caps) |
| R9 | **Coexistence.** If Claude `/goal` is active, Codex thread goal is active, or another Stop hook is installed, observe only and never stack a continue. Native quota resume is observed, not duplicated. Recommend setting `CLAUDE_AFK_TIMEOUT_MS` off so AWOS policy decides. | M | [R §4.2](../research/agent_supervisor_research_2026-10.md#E-caps), [§2](../research/agent_supervisor_research_2026-10.md#E-competition) |
| R10 | **Loop/stall guard.** OpenHands defaults (scan last 20 events: action+obs ×4, action-error ×3, monologue ×3, alternation ×6). Stop-message near-duplicate (5-gram Jaccard ≥0.85 vs last 3). Working with no tool or transcript progress for 10 min (sleep excluded) → stalled. Owner-only blockers (sign in, credential, physical action) → escalate once, never re-block until the owner acts. | M | [R §4.3](../research/agent_supervisor_research_2026-10.md#E-guards), [§1 P8](../research/agent_supervisor_research_2026-10.md#E-pains) |
| R11 | **Ambient status.** `awos supervise status`, a local page and a SwiftBar plugin show per session: tool, project, state, age, current activity ("tests running", "waiting on bg task #3 12m", "blocked: key") and a needs-you count. | M | [R §5](../research/agent_supervisor_research_2026-10.md#E-advanced), [§6](../research/agent_supervisor_research_2026-10.md#E-hf) |
| R12 | **Three delivery tiers.** T1 interrupts now: credentials blocking running sessions, loops spending money, needs_decision on a tracked session. T2 goes at the next breakpoint, ≤15 min. T3 goes to the digest or ambient view only. Storm merge (more than 2 within 2 min → one list). At most 1 non-T1 interrupt per 10 min. Re-notify T1 once after 30 min. Quiet hours hold everything but T1. | M | [R §6](../research/agent_supervisor_research_2026-10.md#E-hf) |
| R13 | **Escalation template.** `[project] tool · session (age)` / need in ≤90 chars / `Suggested: …` / reason label / click-through to a local page with [Use suggested] [Reply] [Snooze 1h] [Open]. No one-tap suggestion on never-list items. Never send a bare "waiting for input". | M | [R §6](../research/agent_supervisor_research_2026-10.md#E-hf) |
| R14 | Suppress or rewrite Claude `idle_prompt` while background tasks run. Raise one T2 only if the task finished and the agent stays idle for more than 5 min. Dedupe against native push (owner picks the channel at install). | M | [R §1 P6](../research/agent_supervisor_research_2026-10.md#E-pains) |
| R15 | **Grouped key/credit alert** from typed signals (Claude StopFailure; Codex `credits.has_credits`, `rate_limit_reached_type`). One alert per (provider, error) listing affected sessions and the `awos keys` fix. Auto-continue paused on those sessions. | M | [R §4.1](../research/agent_supervisor_research_2026-10.md#E-signals) |
| R16 | **Undo.** Before each continue, take a non-invasive checkpoint (`git stash create` or `git write-tree`, falling back to HEAD) recorded in the audit log. [Rewind] offered in the notice and digest, and it says what it cannot undo. | M | [R §6](../research/agent_supervisor_research_2026-10.md#E-hf) |
| R17 | **Shadow first.** Auto-continue runs in shadow (decide and log, don't act) until there are ≥60 would-continue decisions with a Clopper-Pearson 95% upper bound on false-continue ≤5% (0 errors at 60; 1 at 93), graded by the owner. Per tool. | M | [R §4.5](../research/agent_supervisor_research_2026-10.md#E-calibration) |
| R18 | **Kill switch + safe mode.** `pause` takes effect in under 1 s. Automatic safe mode (notify-only) on classifier errors, DB errors, clock anomaly, contract-test failure or an untested major version. Visible everywhere. | M | [R §3](../research/agent_supervisor_research_2026-10.md#E-reliability) |
| R19 | **Audit + self-metrics + doctor.** Every decision records inputs hash, keys, label, policy clause, latency and outcome. Self-metrics: hook p50/p99, fail-open count, spool backlog, reconcile gaps, drift. `doctor` gives a fix per check. | M | [R §3](../research/agent_supervisor_research_2026-10.md#E-reliability) |
| R20 | **Verified-done (digest only).** On a `done` stop, run the project's `checks` (policy) **asynchronously** in a detached `git worktree` at the session's HEAD plus diff, with a 10 min cap and only on AC power or battery above 50%. The label is `done ✓` or `done ✗ (tail)`; when no checks are configured or nothing ran after the last edit, the label is `done ?`. Flag edits to tests, conftest or CI. | M | [R §4.4](../research/agent_supervisor_research_2026-10.md#E-judges), [§5](../research/agent_supervisor_research_2026-10.md#E-advanced) |
| R21 | Loopback security: bind 127.0.0.1, 32-byte token header (0600 file), reject a wrong Host or a browser Origin. | M | [R §3](../research/agent_supervisor_research_2026-10.md#E-reliability) |
| R22 | Sleep awareness: detect gaps by wall-clock vs monotonic delta; reconcile on wake before classifying; sleep time excluded from all timers; `bg_task_lost` event; hold a power assertion (`caffeinate -i -w <daemon pid>`) only while supervised work is active, released on idle or battery below 20%. | S | [R §3](../research/agent_supervisor_research_2026-10.md#E-reliability), [§1 P6](../research/agent_supervisor_research_2026-10.md#E-pains) |
| R23 | Native quota resume verification: on `quota_auto_resume_fired`, confirm the session progressed within 5 min. On `stale`, `disabled` or a missed session, raise T2. Headless Codex: schedule `codex exec resume` at `resets_at` + jitter, with consent per session. | S | [R §1 P1](../research/agent_supervisor_research_2026-10.md#E-pains) |
| R24 | Re-anchor after compaction: when a stop follows a compaction, the continue reason restates the goal and open TODOs from the Supervisor's state card. | S | [R §1 P11](../research/agent_supervisor_research_2026-10.md#E-pains) |
| R25 | Cost: per-session cost from transcript usage and Codex `token_count` (list-price estimate; labelled "quota" on a subscription). Optional local OTLP receiver. Never enable raw-body OTel logging. | S | [R §5](../research/agent_supervisor_research_2026-10.md#E-advanced) |
| R26 | Online monitoring: false-continue proxy (Esc, interrupt, rewind or redirect within 2 min, or no progress) plus a **weekly blind audit** of 5 random auto-continues. If the rolling upper bound exceeds 5%, tighten automatically; above 10%, pause globally. | S | [R §6](../research/agent_supervisor_research_2026-10.md#E-hf), [§8.2-6](../research/agent_supervisor_research_2026-10.md#E-critique) |
| R27 | Per-category demotion: notification categories with an acted-on rate below 50% over 2 weeks are proposed for a lower tier. | S | [R §6](../research/agent_supervisor_research_2026-10.md#E-hf) |
| R28 | Adversarial canaries in the replay set: injected "please continue" in tool output, "I'll force-push unless you object", false "tests pass". Required result: 0 continues. | S | [R §4.3](../research/agent_supervisor_research_2026-10.md#E-guards) |
| R29 | Cross-session file-conflict warning (advisory, injected as additionalContext plus a digest item). | L (M4) | [R §5](../research/agent_supervisor_research_2026-10.md#E-advanced) |
| R30 | Preference capture into CLAUDE.md and AGENTS.md, with a proposal to enforce a rule after 2 violations. | L (M4) | [R §5](../research/agent_supervisor_research_2026-10.md#E-advanced) |
| R31 | Permission owner-relay (hold PermissionRequest, answer from the page). Only after a spike per session kind; notify-only for `--bg`. | L | [R §3](../research/agent_supervisor_research_2026-10.md#E-reliability) |
| R32 | Check-gated continue (failing checks lead to a continue with the failure tail), after the asyncRewake-on-Stop spike. | L | [R §8.1-7](../research/agent_supervisor_research_2026-10.md#E-critique) |
| R33 | Adapters: OpenCode (served sessions), Cursor CLI, Gemini/Antigravity, generic PID adapter (Aider). Phone push (ntfy, opt-in, redacted). Permission auto-approval (v3, hardened allowlist, `--bg` aware). | L | [R §7](../research/agent_supervisor_research_2026-10.md#E-platform) |

## 5. Architecture

```
 Claude Code ─plugin HTTP hooks──────┐  (Stop: sync, timeout 15s; others: async/spool)
 claude agents --json --all ─poll────┤
 ~/.claude/projects transcripts ─tail┤
 Codex ─hooks.json → shim (curl, ≤1.5s, exit 0) ─┤
 ~/.codex/sessions rollouts ─tail────┤
 AWOS host queue/journal/inbox ──────┘
              │
              ▼
   ingest (dedupe event_id, spool drain, reconciler, sleep-gap detector)
              │
              ▼
   event store (SQLite WAL, single writer) ──▶ session state machine + state card
              │ on turn_end / idle / error / stall
              ▼
   Layer 0 guards ─▶ rules (structured fields) ─▶ second key (local LLM | structural) ─▶ progress/loop guard
              │                                                       │
              ▼                                                       ▼
   policy (caps, never-list, budgets, coexistence, shadow/active, quiet hours)
              │
   ┌──────────┬───────────┬────────────┬─────────────┬───────────┬──────────┐
 continue   notify      mark_blocked  verify_done   digest      no-op
 (outbox,   (tiers,     (key_health,  (async checks (status,    (audit)
  checkpoint dedupe,     grouped)      in worktree)  page, menu)
  first)     template)
              every decision → audit (SQLite + JSONL) → metrics (attention cost)
```

### 5.1 Code layout (`scaffold/agent/supervisor/`, target `kernel/supervisor/`)

```
supervisor/
  daemon.py          # 127.0.0.1 HTTP: /hook/claude/<event>, /hook/codex/<event>, /api/*, page; token + Host/Origin checks
  store.py           # SQLite schema, single writer thread, outbox, audit
  ingest.py          # normalize, event_id, spool drain, reconciler, sleep-gap detector
  sessions.py        # state machine + state card
  sources/
    claude_code.py   # hook payload parsing, agents --json poller, transcript tail
    codex.py         # hook payload parsing, rollout tail, exec resume
    awos_host.py     # host queue/journal/inbox
  guards.py          # Layer 0 + loop/stall/progress (reuses host/autopilot.py, loop_guard.py)
  classify.py        # rules → structured fields → second key; enum-validated output
  policy.py          # policy.yaml, caps, coexistence, shadow gate
  actions.py         # continue/notify/mark_blocked/verify_done/digest; outbox; checkpoint
  notify_tiers.py    # tiers, breakpoints, storm merge (wraps notify.py)
  metrics.py         # attention cost, precision, false-continue, trial arms (shares work_miner)
  install.py         # plugin, codex hooks.json, launchd plist; backup/diff/CAS; uninstall
  doctor.py          # health checks with fixes
  shim/awos-codex-hook   # fixed path, never changes (Codex trust hash)
tests/fixtures/supervisor/<tool>/<version>/  # recorded redacted payloads + golden decisions
```

### 5.2 Sources

- **Claude Code.**
  - Plugin `hooks/hooks.json` with `type: http`, header `Authorization: Bearer ${AWOS_SUPERVISOR_TOKEN}` (`allowedEnvVars`).
  - Events: Stop (sync, `timeout: 15`); SessionStart, UserPromptSubmit, SubagentStop, Notification, StopFailure, SessionEnd and ConfigChange, all observe-only with `timeout: 5`.
  - PermissionRequest is **not** hooked in v1, so the native dialog is untouched. We observe `permission_prompt` notifications instead.
  - The poller runs `claude agents --json --all` every 15 s and is the ground truth for state and `waitingFor`. Transcripts are enrichment only.
- **Codex.**
  - `~/.codex/hooks.json`: SessionStart, UserPromptSubmit, Stop and SessionEnd call `~/.awos/supervisor/bin/awos-codex-hook <event>`. The shim reads stdin with a 300 ms deadline, curls the daemon with `--max-time 1`, prints the daemon's JSON only on a 200 response, and exits 0.
  - Install walks the owner through `/hooks` trust, and `doctor` re-checks it.
  - Rollout tail gives `task_complete`, `token_count` (rate limits and credits) and `context_compacted`.
  - Codex Stop has no `background_tasks` and no goal flag. Before any Codex continue, read goal state from `goals_*.sqlite` (`mode=ro`). If that is unknown, notify only.
  - Requires a Codex version that passes the M0 spike. If 0.148 fails, the owner upgrades or Codex stays observe+notify.
- **AWOS host jobs:** native, through the existing host autopilot.

### 5.3 Event store

```
events(event_id TEXT PRIMARY KEY, ingest_seq INTEGER, ts_wall, ts_mono, source, tool_version,
       session_id, cwd, kind, payload_redacted JSON, raw_hash)
sessions(session_id, source, cwd, state, state_since, card JSON, consecutive_continues,
         continues_this_prompt, continues_hour[], last_progress_fp, cost_est, goal_active, tracked)
outbox(session_id, turn_key, action, state[pending|sent|confirmed|failed], decision JSON, created, UNIQUE(session_id, turn_key, action))
audit(id, ts, session_id, turn_key, inputs_hash, layer0, rules_fields JSON, key2 JSON, label,
      policy_clause, mode[shadow|active], action, latency_ms, outcome, owner_grade)
notifications(id, session_id, issue_class, tier, sent_ts, acted_ts, dismissed, merged_into)
```

`kind` values:
- `session_start`, `session_end`, `prompt_owner`, `turn_end`;
- `waiting_permission`, `waiting_input`, `idle`, `stall`, `bg_task_lost`, `compacted`;
- `error_auth`, `error_billing`, `error_rate`, `error_other`, `quota_resume_{fired,stale,disabled}`;
- `job_{done,failed,blocked}`;
- `action_*`, `drift`, `safe_mode`.

Prompt text is stored as a hash plus a family label unless the owner opts in. The same redaction patterns as work_miner apply. DB location: `~/.awos/supervisor/supervisor.db`, local disk only.

### 5.4 Session state machine

```
running ──turn_end──▶ stopped ─┬─ Layer0: bg tasks ───────▶ waiting_background ──task done/lost──▶ running | stopped
                               ├─ Layer0: auth/billing ───▶ blocked_credentials ──key fixed──▶ (notify owner to resume)
                               ├─ Layer0: rate ───────────▶ blocked_rate ──native resume / reset──▶ running
                               ├─ goal/other Stop hook ───▶ observed (no action)
                               ├─ two-key routine + caps ─▶ continued ──progress──▶ running
                               │                                 └─no progress──▶ stalled ─▶ waiting_owner
                               ├─ needs_decision ─────────▶ waiting_owner ──owner prompt──▶ running
                               ├─ looping ────────────────▶ waiting_owner
                               └─ done ───────────────────▶ verifying ─▶ done_verified | done_failed | done_unverified
running ──no progress 10m (sleep excluded)──▶ stalled
any ──unknown agent state──▶ unknown (counts as running; never continued)
any ──SessionEnd / reaped──▶ ended
```

### 5.5 Classifier and guards

1. **Layer 0** (≤5 ms, deterministic): R6 plus the coexistence check (R9) plus caps (R8).
2. **Rules → structured fields** (≤50 ms): `question_to_user`, `question_type ∈ {permission_to_proceed, choice_between_options, missing_info, risky_action_approval, closing}`, `remaining_work_stated` plus steps, `claims_done`, `verification_after_last_edit`, `next_action_risk` (never-list regex on the announced next step). Inputs are restricted to: the owner's last prompt, `last_assistant_message`, tool-call metadata (names, paths, exit codes) and computed features. **No raw tool output or web content**, which limits prompt injection.
3. **Second key** (only if the rules say routine):
   - (a) The local LLM (llama-server, Qwen 9B, constrained JSON enum, temp 0, 5 s hard timeout) with a logprob-derived p ≥ τ; or
   - (b) Structural: open TodoWrite or plan items in the transcript plus `question_type = permission_to_proceed` plus `next_action_risk = low`.

   The LLM is skipped below 20% battery or in Low Power Mode, leaving only key (b). Haiku is used only if `classifier.cloud: true`.
4. **Progress/loop guard:** R8 and R10. Reuses `host/autopilot.diff_fingerprint` and `loop_guard.py`.

Mapping: `choice_between_options | missing_info | risky_action_approval` → needs_decision. `permission_to_proceed` + remaining work + low risk + key 2 → routine_checkpoint. `claims_done` → done. Anything else, or any timeout → allow the stop and notify at T2 (T3 if the owner is not tracking the session).

### 5.6 Policy (`~/.awos/supervisor/policy.yaml`)

```yaml
mode: shadow                   # shadow | active; active per tool only after the R17 gate
auto_continue:
  tools: {claude_code: true, codex_headless: true, codex_tui: false}   # codex_tui after spike
  max_consecutive: 3
  max_no_progress: 1
  max_per_owner_prompt: 3
  max_per_session_hour: 6
  second_key: [llm, structural]
  message: "Continue with your plan: {open_items}. Stop and ask only for a real decision, a destructive or irreversible action, or if blocked."
never_auto_continue_when:
  mentions: [push, deploy, delete, "drop table", "rm -rf", force, publish, payment, purchase, "send email", migrate, release]
  touches: ["tests/**", "conftest.py", ".github/**", "*.lock"]
  cost_since_owner_usd: 2.00
  unattended_hours: 4           # sleep excluded
checks:                         # verified-done, per project
  default: null
  projects: {"~/Projects/AWOS_coding_agent": "python -m pytest -q -x tests/unit"}
notify:
  channel: macos                # macos | none (native push only) | ntfy (later)
  quiet_hours: "23:00-08:00"
  digest_times: ["09:00", "18:00"]
  tracked_sessions: []          # needs_decision on these is T1
classifier: {local_model: true, cloud: false, timeout_s: 5}
sources: {claude_code: true, codex: true, awos_host: true}
```

### 5.7 Actions

- **continue** (active mode only):
  1. Write the outbox row.
  2. Take the checkpoint.
  3. Return Stop `{"decision":"block","reason": <message with open items>}` for live Claude or Codex sessions. Headless Codex uses `codex exec resume <id> "<msg>"`, under a per-session flock after re-checking that the session is idle. AWOS jobs use the host autopilot.
  4. Mark the outbox row confirmed when the next event shows a tool call.

  `claude --resume <id> --bg` is used only for sessions that agent view reports reaped. Never `-p --resume` on a live session.
- **notify:** tiers, template, dedupe per (session, issue_class) until state changes.
- **mark_blocked:** key_health, one grouped alert, continues suppressed.
- **verify_done:** async checks (R20).
- **digest:** `awos status --digest` plus a page section, ordered: Needs you (oldest first) → Blocked (with fix) → Done ✓ / ✗ / ? → What the Supervisor did (continued N, rewound n, suppressed alerts) → Attention cost and cost.

### 5.8 Reliability layer (concrete)

| Concern | Design |
|---|---|
| Daemon down/hung | HTTP hooks fail open. Stop `timeout: 15`. Internal 8 s deadline returns `{}`. Codex shim `--max-time 1`. |
| Process lifecycle | LaunchAgent `ai.awos.supervisor`, `KeepAlive {Crashed:true, SuccessfulExit:false}`, `ProcessType Adaptive`, RunAtLoad, no fork, log rotation. Single instance: flock on `daemon.lock` plus port bind. A foreign process on the port is diagnosed, never killed. |
| Lost events | Spool (`~/.awos/supervisor/spool/`, tmp + rename) for observe events. Reconciler every 60 s and on wake. |
| Duplicates | `event_id` UNIQUE, INSERT OR IGNORE. Outbox UNIQUE per turn. |
| Disarm | R3 drift checks plus a DISARMED banner. |
| Version drift | Record tool versions per session. Parsers map unknown values to `unknown`. **Narrow contract test:** a recorded Stop/StopFailure/Notification payload per tool, checked against the schema the daemon expects. On a new *major or minor* version, `doctor` replays the fixtures. A failure triggers safe mode. Patch releases don't force safe mode, to avoid permanent notify-only under weekly releases. |
| Sleep | Wall-clock vs monotonic gap detection (verified empirically in M0). Reconcile before classifying. Timers exclude sleep. |
| DB | WAL, `synchronous=FULL`, busy_timeout 5 s, short reads, `wal_checkpoint(TRUNCATE)` hourly, retention: raw payloads hashed after 30 days. |
| Self-observability | Self-metrics plus `doctor`. Health line at the top of status and digest. |
| Tests | Golden replay of recorded payloads. Idempotency property test (replay twice → same state). Chaos: kill -9 mid-Stop, port held, DB locked, LLM hang, 8 h clock jump, 10k spool files, malformed payloads. Canaries (R28). |

### 5.9 UX surfaces

- **Ambient:** SwiftBar plugin (`● 2 need you · 3 working · 1 bg`) and `awos supervise status`.
- **Page:** local page (`awos serve`, 8765) with an inbox (reason labels, Done/Snooze), session cards, a rewind button and a shadow grading queue.
- **Notifications:** macOS banner with click-through to the inbox item.
- **Digest:** CLI and page.
- **Grading:** `awos supervise grade`, a tiny CLI that shows shadow and audit items one at a time with y/n/skip.

## 6. Integration matrix and order

| Order | Tool | Observe | Continue | Approve | Cost | Gate |
|---|---|---|---|---|---|---|
| 1 (M0-M2) | Claude Code ≥2.1.29x | plugin hooks + `agents --json` + transcripts | Stop block (live); `--resume --bg` (reaped only) | notify only | transcripts (OTel opt-in) | contract test |
| 2 (M0-M2) | AWOS host jobs | native | host autopilot | n/a | budget ledger | existing tests |
| 3 (M3) | Codex (version passing spike) | hooks.json shim + rollouts | headless `exec resume` at once; TUI Stop block after spike + shadow gate | notify only | `token_count` | M0 spike, trust check |
| 4 (L) | OpenCode (served) | SSE | `prompt_async` | relay later | message cost | — |
| 5 (L) | Cursor CLI, Gemini/Antigravity, generic PID (Aider) | per [R §7](../research/agent_supervisor_research_2026-10.md#E-platform) | — | — | — | real test first |

## 7. Safety and privacy

- **Local only.** 127.0.0.1, token header, Host/Origin checks. The classifier is local by default. Nothing leaves the laptop unless the owner opts in (Haiku fallback, ntfy).
- **Never answers a question or approves a permission prompt** in v2. Questions are held and notified.
- **Never continues on:**
  - a never-list hit (message or announced next step);
  - test/CI file edits;
  - running background tasks;
  - an active goal or another Stop hook;
  - an unknown state or a failed contract test;
  - caps or no progress;
  - doubt or timeout;
  - shadow mode.
- **Reversible:**
  - checkpoint plus rewind for every continue;
  - install shows a diff and backs up;
  - uninstall removes only our plugin and our hooks.json entries, verified, with a byte-for-byte restore of anything edited;
  - Codex `notify` and the Claude statusLine are never touched.
- **Fails open:** if the daemon is down or slow, agents behave exactly as they do today.
- **Consent:** auto-continue is opt-in per tool and shadow-gated. Headless reset-resume is opt-in per session.
- **Terms of service:** check Anthropic and OpenAI consumer-plan terms on automated continuation before M2 goes active (owner action).
- **Redaction:** work_miner secret patterns on every stored payload. Prompts are hashes unless opted in. No OTel raw bodies.

## 8. Metrics and the pre-registered owner trial

**Primary KPI: attention cost per agent-hour (ACH)** = (owner supervision prompts + Supervisor interrupting notifications, T1+T2) ÷ agent-hours. Always reported as both parts. Agent-hours come from work_miner session spans with sleep excluded.

**Secondary metrics:**
- notification precision (acted on within 30 min);
- auto-continues per day;
- false-continue rate: proxy (R26) plus blind audit, with the upper bound reported;
- coverage (share of routine stops actually continued);
- time-to-attention for stuck sessions (baseline median 6.3 min idle, p90 211 min);
- verified-done rate;
- keys caught before the owner noticed;
- `bg_task_lost` caught;
- Supervisor health: fail-open count, disarmed minutes.

**Trial** (`docs/specs/supervisor_trial.md`, written and committed before M2 goes active):
- **Design:** 20 working days. Each day is randomly assigned ON or OFF in balanced blocks of 4 (2 ON, 2 OFF per block, seeded and committed in advance). OFF days run the Supervisor in **shadow** (observe, classify, log; no continue, no notifications beyond native). ON days run active.
- **Primary outcome:** ACH per day, compared with a two-sided permutation test on day means.
- **Success:** ACH ON ≤ 0.7 × ACH OFF (a ≥30% reduction), p < 0.05, **and** false-continue 95% upper bound ≤5% (proxy plus blind audit), **and** 0 never-list continues.
- **Guardrails:** stop the trial if there are 2 or more owner-graded harmful continues or Supervisor-caused cost above $5.
- **Secondary:** prompt-only ACH, per-family deltas (status, continue, keys), notification precision.
- **Confounds recorded:** tool versions per day, number of agents, project.
- **Interpretation:** the 30% bar (down from v1's 40%) reflects the smaller addressable slice. A null result is published as a null.

## 9. Milestones (each ends in a live proof)

| M | Scope | Live-proof acceptance |
|---|---|---|
| **M0 Observe + re-baseline + spikes** | daemon, store, ingest/reconciler/spool, plugin install/uninstall, Claude + AWOS host sources, `status`, `doctor`, drift check, launchd. **Analysis:** re-run work_miner on the last 3 weeks (current versions), cross-tab trigger by prompt family, hand-label all 80 continue prompts as safe/unsafe to automate. **Spikes:** (a) Codex 0.148 TUI Stop block continues a session; (b) `claude --resume --bg` on live vs reaped sessions; (c) monotonic clock across sleep on the M5; (d) asyncRewake on Stop. | 2 real Claude sessions plus 1 AWOS job: every turn_end, idle, permission and StopFailure event appears in the DB; kill -9 the daemon mid-session and Claude is unaffected, with gaps reconciled after restart; delete the plugin and DISARMED shows within 10 min; uninstall leaves settings.json byte-identical. A spike report is committed. **Go/no-go:** if safely automatable continues are fewer than 3 per week, **drop M2 auto-continue** and ship M1 plus verified-done as the product. |
| **M1 Attention** | rules classifier (fields), Layer 0, tiers, template, storm merge, idle suppression, grouped key alert, stall and `bg_task_lost`, ambient SwiftBar, page inbox, digest | Over 3 real days: a real question gives exactly 1 notification with context; 3 sessions with a dead key give 1 grouped alert; an idle_prompt during a background task gives 0 notifications; a simulated 8 h sleep gives 0 false stall alerts; the digest fits one screen. |
| **M2 Auto-continue (Claude Code), shadow → active** | second key (LLM + structural), progress/loop guards, caps, coexistence, outbox, checkpoint/rewind, grading CLI, canaries; trial doc committed | Shadow: ≥60 graded would-continue decisions, upper bound ≤5%; canaries 0 continues; one `/goal` session gets 0 Supervisor continues. Active: 10 real routine stops continued, every one with a checkpoint, and 1 rewind demonstrated. **Trial starts.** |
| **M3 Codex** | hooks.json shim, trust flow, rollout tail, typed credits/limits, headless `exec resume` (+ at reset), TUI continue only if spike (a) passed | A headless Codex task stops at a routine checkpoint and continues. A credits-exhausted Codex session gets 1 alert. The Computer Use `notify` still receives `turn-ended` (unchanged config). Changing the shim is detected as untrusted by `doctor`. |
| **M4 Verified-done + polish** | async checks in a worktree, test-file flags, re-anchor after compaction, quota-resume verification, per-category demotion, weekly blind audit, file-conflict warnings, preference capture | Over 2 weeks of daily use: the digest marks a deliberately broken "done" as ✗. Trial result written up (pass or fail). |
| **M5 Launch (conditional)** | packaging (`pip install awos` / brew), docs, a 60-second demo, open-source core | **Only if the trial passes.** Clean-Mac install in under 5 minutes, including Codex trust. Launch post with the real ACH result and its confidence interval. |

Solo-builder scope: M0+M1 is the minimum shippable product (≈2-3 weeks). Musts that are not on the M0-M2 path (OTLP receiver, socket activation, conformal recalibration per version) are deliberately excluded.

## 10. Reuse map

| Existing AWOS part | Role |
|---|---|
| `host/` queue, journal, worker, autopilot (`diff_fingerprint`, `progress`, `continuation_decision`) | AWOS-jobs source; progress guard |
| `loop_guard.py` | loop detector base |
| `notify.py` | macOS delivery under `notify_tiers.py` |
| `key_health.py` (`awos keys`) | fix text and re-check for grouped key alerts |
| `awos status --digest`, `awos serve` | digest and page |
| `work_miner*.py` | ACH meter, weak labels, re-baseline, trial analysis |
| `desktop/policy.py` allowlist | v3 permission auto-approval |
| llama-server Qwen 9B; Haiku 5.5 opt-in | second key |

## 11. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Riskiest assumption: enough routine "turn ended with work left" stops remain after native features to matter** | High | M0 hand-label plus go/no-go; the product still stands on attention and verified-done |
| A false continue on approval-shaped text | Medium | Never-list on the announced next step, two-key rule, canaries, shadow gate, rewind |
| Double continue with `/goal`, native quota resume or other Stop hooks | Medium | R9 coexistence; Codex goal check via `mode=ro` DB; notify-only when unknown |
| Silent disarm (settings rewrite, Codex trust hash, lossy hooks) | High | Plugin install, byte-stable shim, drift checks, reconciler, DISARMED banner |
| Coverage collapses to zero (safety stack, weekly releases) | Medium | Structural second key; safe mode only on major/minor bumps; report coverage honestly |
| Platform absorbs the core (vendor "keep going" setting) | High | Keep the build thin; attention + verified-done + cross-tool + measured result stand alone |
| Lucarne/Orca/cmux add policy continue | Medium | Ship M0-M2 fast; the measured result is the moat |
| Notification fatigue at launch | Medium | Narrow T1, digest-first, precision tracking, demotion, native-push dedupe |
| Each continue costs more than the attention it saves (long contexts) | Medium | Per-continue cost estimate counted toward the cap; trial measures it |
| Trial confounded or underpowered (n=1, 20 days) | Medium | Day randomisation with shadow on OFF days; permutation test; publish nulls |
| Consumer-plan terms disallow automated continuation | Unknown | Check before M2 active; keep observe/attention features regardless |
| PermissionRequest bg-session behaviour unclear | Known | Out of v2 |
| macOS clock/sleep semantics unverified | Medium | M0 spike (c) |
| Opportunity cost vs the benchmark track | Medium | M0 go/no-go; M0+M1 time-boxed to 3 weeks |

## 12. Open owner decisions

1. Product name: keep "AWOS Supervisor" (qualified) or rename to "AWOS Watch".
2. Claude Code / Codex billing: subscription or API key. This decides whether cost is shown in $ or as quota.
3. Notification channel: AWOS macOS banners vs native push vs both, with dedupe.
4. Upgrade Codex to the latest (0.162) if spike (a) fails on 0.148.
5. `checks` command per project for verified-done.
6. Whether to set `CLAUDE_AFK_TIMEOUT_MS` off so AWOS policy, not a timer, decides.

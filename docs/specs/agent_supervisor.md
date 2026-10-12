# AWOS Supervisor: spec, design and architecture (v1)

Status: proposed 2026-10-12. Product idea 1 of the AWOS roadmap (idea 2, "show me once, it does it forever", builds on it).

## 1. Problem (measured, not guessed)

`work_miner` v1 on the owner's own data covers 644 prompts over 36 days, 22 sessions and 79.7 agent-hours:

- **30% of everything the owner types to AI agents is supervision:** status (93), continue (80) and keys (21). That is **2.4 supervision prompts per agent-hour**.
- **What precedes a supervision prompt:**
  - a background task still running: 44%;
  - the agent ended its turn: 19%;
  - a long silence: 16%;
  - the agent asked a question: 12%;
  - a key or auth error: 7%.
- **The trend:** the supervision share is rising (15% in early September, 57% this week) as the owner runs more background agents.

Every heavy user of AI agents has this tax. The agents are capable, but nobody watches them, continues them when the stop is routine, or tells the owner only what matters.

## 2. Product

**One line:** AWOS watches all your AI agents (Claude Code, Codex, AWOS jobs and more later), keeps them going through routine stops, catches dead keys, and only interrupts you for real decisions. You get a digest instead of babysitting.

**Owner stories (v1):**

1. I start three agents and walk away. When one stops just to ask "should I continue?" with work left, it continues on its own, within my policy.
2. When an agent truly needs me (a real question, a destructive action, a permission prompt, repeated failure), I get one clear notification saying which session, what it needs and the suggested answer.
3. When a key dies or credits run out, I'm told once, with the fix, and nothing loops.
4. Any time (and every morning) I see a digest: what each agent did, what's done, what's stuck, cost, and time saved.
5. Every week I see my supervision-per-agent-hour drop. That is the product's proof.

**Non-goals for v1:**
- No auto-approving permission prompts. Notify only; that comes in v2 behind the hardened allowlist.
- No cloud service.
- No replacing Claude Code's own agent view. We read it as a source.
- No GUI app beyond notifications, CLI, a local web page and an optional menubar plugin.

## 3. How it fits in AWOS (the kernel owns it)

The Supervisor is an **AWOS kernel service**. AWOS host jobs are just one kind of supervised session. Existing parts are reused, not rebuilt:

| Existing AWOS part | Role in the Supervisor |
|---|---|
| `host/` queue, journal, worker, autopilot (91b2abf) | the AWOS-jobs source; auto-continue logic is shared |
| `notify.py` (inbox + macOS notifications, dedupe) | the notifier |
| `key_health.py` (`awos keys`) | key and credit checks on auth errors |
| `awos status --digest` | extended to all sessions |
| `work_miner` (prompts, sessions, supervision metrics) | the KPI meter, before and after |
| `desktop/policy.py` allowlist (track F, hardened) | future permission auto-approval (v2) |
| local model (llama-server, Qwen 9B) + Haiku 5.5 | the stop classifier: local first, Haiku only if opted in |

**Code layout:** `scaffold/agent/supervisor/`, mapped to the target `kernel/supervisor/`.

```
supervisor/
  daemon.py        # local HTTP server 127.0.0.1:<port>: hook endpoints, API, dashboard
  events.py        # normalized Event + SQLite store (~/.awos/supervisor/supervisor.db)
  sessions.py      # session registry + state machine
  sources/
    claude_code.py # HTTP hooks + `claude agents --json` poller + transcript tail (read-only)
    codex.py       # chained notify wrapper + ~/.codex/sessions tail; resume via `codex exec resume`
    awos_host.py   # host queue/journal/inbox (native)
  classify.py      # stop classifier: rules -> local LLM -> (opt-in) Haiku
  policy.py        # standing policy (~/.awos/supervisor/policy.yaml) + per-session budgets
  actions.py       # continue / notify / digest / mark-blocked / no-op, with audit log
  metrics.py       # supervision KPIs (shared with work_miner)
  install.py       # install/uninstall hooks, codex notify wrapper, launchd agent (backup + diff + confirm)
```

## 4. Architecture

```
 Claude Code ──HTTP hooks──▶ ┐
 claude agents --json ─poll─▶│
 Codex ──notify wrapper─────▶│   daemon ─▶ events (SQLite) ─▶ session state machine
 ~/.codex/sessions ──tail───▶│                                     │ on stop / idle / error
 AWOS host jobs ─inbox/queue▶┘                                     ▼
                                                    classify (rules → local LLM)
                                                                   │
                                                    policy (budgets, never-rules, quiet hours)
                                                                   │
                       ┌──────────────┬────────────────┬───────────┴──────┬──────────────┐
                    continue       notify owner     mark blocked       digest         no-op
              (Stop hook "block"  (notify.py,     (key_health fix,  (awos status   (record only)
               + reason / codex    1 per issue)    no retry loop)    --digest, web)
               exec resume / host
               auto-continue)
                                   every action → audit log + metrics (supervision per agent-hour)
```

### 4.1 Sources (how we see each agent)

- **Claude Code (v1, primary).**
  - `awos supervise install` adds user-level **HTTP hooks** in `~/.claude/settings.json` posting to the daemon, covering SessionStart, UserPromptSubmit, Stop, SubagentStop, Notification (`permission_prompt`, `idle_prompt`), StopFailure and SessionEnd. It backs up the file and shows the diff first.
  - The Stop hook is **synchronous**. The daemon must answer within the hook's time budget: rules take about 50 ms, the local LLM must answer in under 10 s, otherwise "allow stop".
  - **The daemon being down must never break Claude Code.** A hook failure is non-blocking, so the agent simply stops as it does today.
  - The daemon also polls `claude agents --json` for background-session state, and reads transcripts **read-only** for context. The format is internal and may change, so parsing is defensive and on a best-effort basis.
- **Codex (v1, notify + headless continue).**
  - Codex supports a single `notify` program. It is already used by Codex's Computer Use client, so install **chains** it: our wrapper calls the original notifier with the same arguments, then posts the event to the daemon. Uninstall restores the original.
  - The wrapper must stay fast and never fail.
  - Session details come from `~/.codex/sessions` and `session_index.jsonl`.
  - **Continuing** is supported only for headless `codex exec` sessions (`codex exec resume`). Interactive TUI sessions get notify-only in v1.
- **AWOS host jobs (v1, native):** from the queue, journal and inbox. They already have auto-continue, key health and the digest.
- **Later:** OpenCode, Aider, Cursor agents, any CLI process (a generic adapter based on PID, idle output and exit code).

### 4.2 Normalized event (SQLite)

`{id, ts, source, session_id, project/cwd, kind, payload_redacted}`

`kind` is one of:
- session lifecycle: `session_start`, `session_end`;
- turns: `prompt_owner`, `turn_end`;
- waiting: `waiting_permission`, `idle`;
- errors: `error_auth`, `error_rate`, `error_other`;
- the AWOS job itself: `job_done`, `job_failed`, `job_blocked`;
- the supervisor's own actions: `action_continue`, `action_notify`.

Payloads are redacted with the same secret patterns as `work_miner`. Prompt text is stored only as a hash plus a family label, unless the owner opts in.

### 4.3 Session state machine

`running → stopped(turn_end | question | permission | limit) → {continued → running | waiting_owner | done | blocked(credentials | rate) | looping} → ended`

Tracked per session:
- consecutive auto-continues;
- the time and progress signal of the last continue (transcript growth, files changed, tests passed if known);
- cost (if known);
- owner interventions.

### 4.4 Stop classifier

Run on every `turn_end` or `idle`.

**Inputs:**
- `last_assistant_message`;
- `background_tasks` (a turn ending while background tasks still run is a different case: don't continue, wait);
- recent transcript tail;
- progress since the last continue;
- error markers.

**Labels and default actions:**

| Label | Meaning | Default action |
|---|---|---|
| `done` | work complete; summary given | digest entry; notify only if it's the owner's tracked task |
| `routine_checkpoint` | "want me to continue?", "next I'll…", a plan with steps left, a progress update with work remaining | **auto-continue**, within policy |
| `needs_decision` | a real choice, ambiguity, missing info, a destructive or irreversible step, spending, pushing or deploying | notify the owner with the question + suggested answer |
| `blocked_credentials` / `blocked_rate` | auth or credit errors, rate limits | key_health; mark blocked; notify once |
| `looping` | repeated near-identical turns or no progress across continues | stop auto-continuing; notify |
| `waiting_background` | turn ended but background tasks are running | wait; re-check when they finish |

**Order:**
1. **Deterministic rules,** which are fast and catch the common phrases.
2. **The local LLM** (constrained labels, temperature 0, redacted input).
3. **Haiku 5.5 only if `classifier.cloud: true`.**

When unsure, choose `needs_decision`. The default is never to continue on doubt.

### 4.5 Policy (`~/.awos/supervisor/policy.yaml`)

The owner edits it; the defaults are conservative:

```yaml
auto_continue:
  enabled: true
  max_consecutive: 3          # per session, then ask the owner
  max_per_hour: 10
  require_progress: true      # no progress since the last continue -> stop continuing
  message: "Continue with the plan. Stop and ask only for a real decision, a destructive action, or if blocked."
never_auto_continue_when:     # always escalate
  - mentions: [push, deploy, delete, drop table, rm -rf, payment, purchase, email send, publish, force]
  - cost_since_owner_usd: 2.00
  - session_hours_unattended: 4
quiet_hours: "23:00-08:00"    # batch non-urgent notifications into the morning digest
notify:
  macos: true
  digest_times: ["09:00", "18:00"]
classifier:
  local_model: true
  cloud: false                # Haiku fallback only when true
sources: {claude_code: true, codex: true, awos_host: true}
```

### 4.6 Actions

- **continue:**
  - Claude Code: the Stop-hook response `{"decision":"block","reason": <policy message + short context>}`;
  - Codex headless: `codex exec resume <id> "<message>"`;
  - AWOS jobs: the host's auto-continue.
- **notify:** `notify.py`, one per issue per session (deduped), with an action line: "answer: …" or "fix: replace ANTHROPIC_API_KEY".
- **mark_blocked:** key_health, then the session is marked blocked, then one notification. Re-check is automatic.
- **digest:** `awos status --digest` covers all sessions; the local web page is `awos serve` (port 8765); an optional SwiftBar or xbar menubar plugin calls the daemon API, with no new dependencies.

Every action is written to an append-only audit log, with the reason. `awos supervise pause` (or a policy flag) is the kill switch.

## 5. Safety and privacy

- **Local only.**
  - The daemon binds 127.0.0.1 only, with a random token required on hook URLs.
  - The classifier is local by default.
  - Nothing is sent to the cloud unless the owner opts in.
- **Never auto-approves permission prompts in v1.**
- **Never continues** on the never-list, on a cost or time cap, without progress, or on doubt.
- **Hook changes are reversible:**
  - backup, diff and confirm on install;
  - `awos supervise uninstall` restores everything;
  - the Codex notify chain keeps the existing notifier working.
- **Fails open:** if the daemon is down, agents behave exactly as they do today.

## 6. Metrics: the product's proof

The **primary KPI is supervision prompts per agent-hour**, from `work_miner` sessions (baseline 2.4, recent 1.6), plus the supervision share. Also tracked:

- **auto-continues per day;**
- **false-continue rate:** the owner interrupts or reverts within 2 minutes of an auto-continue, or the continued turn produced no progress (target < 5%);
- **notifications per day**, and the share the owner acts on (precision);
- **time-to-attention:** stuck sessions noticed vs the old idle time (baseline median 6.3 min idle before the owner's next prompt, p90 211 min);
- **dead-key incidents caught before the owner noticed.**

**Pre-registered owner trial** (`docs/specs/supervisor_trial.md`, to write before M2 ships): 2 weeks with the Supervisor on vs the prior 2 weeks off. Success means supervision per agent-hour drops at least 40% with a false-continue rate under 5%.

## 7. Milestones (each ends in a live proof, not just unit tests)

| M | Scope | Live proof / acceptance |
|---|---|---|
| **M0 Observe** | daemon + SQLite events + Claude Code HTTP hooks + AWOS host source + `awos supervise status`; install/uninstall with backup | Start 2 real Claude Code sessions: every turn_end, idle and permission event is in the DB; uninstall restores settings byte-for-byte; the daemon being down does not affect Claude |
| **M1 Notify + digest** | classifier (rules only) + notify + digest across sources; key-health on auth errors | A session that asks a real question produces exactly 1 notification; a dead-key session goes to blocked with 1 notification; the morning digest lists all sessions |
| **M2 Auto-continue (Claude Code)** | Stop-hook continuation with policy, progress guard, loop guard, audit; local LLM classifier | 10 real routine checkpoints are auto-continued and 5 real decisions escalated, 0 continues on the never-list; the KPI trial starts |
| **M3 Codex** | chained notify wrapper, sessions tail, `codex exec resume` for headless | A Codex headless task stops on a routine checkpoint and continues; the original Codex notifier still works |
| **M4 Polish + preference capture** | menubar plugin, quiet hours, preference capture (turn "steer" prompts into proposed CLAUDE.md/AGENTS.md rules the owner approves) | The owner uses it daily for 2 weeks; the KPI result is written up |
| **M5 Launch** | packaging (`pip install awos` / brew), docs, a 60-second demo, open-source core | Install on a clean Mac in under 5 minutes; launch post with the owner's real before/after KPI |

## 8. Risks and open questions

- **Platform drift:** Claude Code's transcript format is internal, and hook fields may change. Mitigations:
  - rely on documented hook payloads first;
  - parse transcripts defensively;
  - pin a tested version range.
- **Built-in overlap:** Claude Code's agent view, and future lab features. Our edge is being cross-tool, policy-based auto-continue, escalation quality, key health, and measured supervision reduction.
- **False continues waste money or take wrong steps:** conservative defaults, the progress guard, the never-list, caps, and the false-continue KPI with a kill switch.
- **Stop-hook latency:** the local LLM classify must fit within a few seconds. If it's slow, rules-only plus "ask the owner".
- **Codex interactive sessions can't be continued from outside in v1:** notify-only.
- **Open questions:**
  - Should the Supervisor also *launch* agents? That's "agent launcher", work_miner candidate 4, and probably M4+.
  - How should approval-by-policy for permission prompts work in v2? Via the hardened allowlist (track F).

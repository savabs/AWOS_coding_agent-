# AWOS Supervisor: research evidence (2026-10)

Status: evidence base for spec v2 (`docs/specs/agent_supervisor.md`). Compiled 2026-10-12 from 7 parallel research reports (user pain, competition, reliability, stop classification, advanced capabilities, human factors, platform control surfaces) plus a critic pass. Section anchors (`#E-...`) are what the spec's requirements table links to.

**How to read the numbers.**
- GitHub reaction counts came from different reports pulled on the same day and sometimes disagree (#2109: 524 vs 688; #16157: 695 vs 726; #38335: 476 vs 545; #35744: 103 vs 106; #29438: 59 vs 69). The likely cause is "+1 only" vs "all reactions". Here they are given as **ranges or lower bounds**. Re-pull before quoting anything externally.
- Star counts show interest. They are not usage or willingness to pay. Orca's 90k is implausibly high next to its peers and has not been checked for inflation.
- Reddit's JSON API returned 403 from this machine, so there is no first-hand Reddit signal. The sources are GitHub, HN (Algolia), vendor docs, papers and local inspection.
- **Self-reported** marks vendor or README claims. **Recalled** marks literature figures that were not re-verified this session. **Local** marks facts checked on the owner's laptop (Claude Code 2.1.296, codex-cli 0.148.0, OpenCode 1.18.19).
- All owner data is n=1 (work_miner: 644 prompts, 36 days, 79.7 agent-hours).

---

## 1. Top user pains, ranked {#E-pains}

Ranked by frequency x severity, with a note on how much of each pain native features already cover. That coverage column is new, and it changes the product.

| # | Pain | Strongest evidence | Already native? | Supervisor leverage |
|---|---|---|---|---|
| P1 | **Usage/rate limits stop work until a human returns** | CC [#16157](https://github.com/anthropics/claude-code/issues/16157) (~700+ reactions, 1,499 comments); CC [#38335](https://github.com/anthropics/claude-code/issues/38335) (~480-545); Codex [#28879](https://github.com/openai/codex/issues/28879) (364); auto-resume requests CC [#13354](https://github.com/anthropics/claude-code/issues/13354) (~210), CC [#35744](https://github.com/anthropics/claude-code/issues/35744) (~105), Codex [#34188](https://github.com/openai/codex/issues/34188); community hack [claude-auto-retry](https://github.com/cheapestinference/claude-auto-retry) (387 stars); [ccusage](https://github.com/ccusage/ccusage) (~19k stars) | **Largely, on Claude Code.** Usage-limit auto-continue shipped (2.1.234, on by default) and fires `quota_auto_resume_fired/stale/disabled` notifications ([hooks](https://code.claude.com/docs/en/hooks)). It is buggy with several sessions: [#91493](https://github.com/anthropics/claude-code/issues/91493), [#92004](https://github.com/anthropics/claude-code/issues/92004), [#92418](https://github.com/anthropics/claude-code/issues/92418), [#91535](https://github.com/anthropics/claude-code/issues/91535). Codex has none. | Observe and verify native resume. Catch the `stale` case and the sessions that native resume misses. Forecast quota burn across sessions. Resume **headless Codex** at reset. Cannot create quota. |
| P2 | **Agent stops early with work left; human types "continue"** | CC [#1632](https://github.com/anthropics/claude-code/issues/1632), [#16211](https://github.com/anthropics/claude-code/issues/16211), [#56487](https://github.com/anthropics/claude-code/issues/56487), [#35773](https://github.com/anthropics/claude-code/issues/35773); Codex [#33398](https://github.com/openai/codex/issues/33398), [#34900](https://github.com/openai/codex/issues/34900), [#14513](https://github.com/openai/codex/issues/14513), [#36596](https://github.com/openai/codex/issues/36596), [#49735](https://github.com/openai/codex/issues/49735); Gemini [#13592](https://github.com/google-gemini/gemini-cli/issues/13592), [#22633](https://github.com/google-gemini/gemini-cli/issues/22633); opencode [#13217](https://github.com/anomalyco/opencode/issues/13217). Small counts per issue, but it recurs across every vendor. Owner: "agent ended turn" precedes 19% of supervision prompts. | **Partly.** `/goal` (Claude Code and Codex) is a session-scoped evaluator Stop hook, but it must be set per session and it judges only what the agent has surfaced ([goal docs](https://code.claude.com/docs/en/goal)). | Core auto-continue case, under a standing cross-tool policy instead of a per-session goal. |
| P3 | **Not knowing which agent needs you; context-free alerts** | CC [#13024](https://github.com/anthropics/claude-code/issues/13024) (81), [#13922](https://github.com/anthropics/claude-code/issues/13922) (42), [#29438](https://github.com/anthropics/claude-code/issues/29438) (59-69), [#28765](https://github.com/anthropics/claude-code/issues/28765); Codex [#3247](https://github.com/openai/codex/issues/3247); cmux HN [47079718](https://news.ycombinator.com/item?id=47079718): "notification body is always just 'Claude is waiting for your input'". Owner: status prompts are the largest family (93/644). | **Partly.** Agent view, push and Remote Control exist ([agent-view](https://code.claude.com/docs/en/agent-view), [remote-control](https://code.claude.com/docs/en/remote-control)), but they are Claude-only and have no per-event configuration. The owner already has `agentPushNotifEnabled` on (Local). | One cross-tool "needs you" channel with context, plus an ambient status view so status questions don't have to be asked. Must not duplicate native push. |
| P4 | **Silent hangs / stalls / dropped streams with no recovery** | CC [#26224](https://github.com/anthropics/claude-code/issues/26224) (152), [#28482](https://github.com/anthropics/claude-code/issues/28482), [#69415](https://github.com/anthropics/claude-code/issues/69415) (84), [#3572](https://github.com/anthropics/claude-code/issues/3572) (123); Codex [#14048](https://github.com/openai/codex/issues/14048) (78), [#52529](https://github.com/openai/codex/issues/52529) (50-min reasoning stall); Gemini [#22141](https://github.com/google-gemini/gemini-cli/issues/22141) (164). Owner: "long silence" precedes 16%. | No. | Stall detector (state says working, no tool or transcript progress for N min). Notify. Opt-in interrupt and resume for headless sessions. |
| P5 | **Auth/credit death across many sessions at once** | CC [#12447](https://github.com/anthropics/claude-code/issues/12447), [#24317](https://github.com/anthropics/claude-code/issues/24317) (OAuth refresh race with 7-12 sessions), [#78610](https://github.com/anthropics/claude-code/issues/78610), [#63060](https://github.com/anthropics/claude-code/issues/63060). Owner: 7% trigger; "keys" family 21/644. | Per-session only. `/goal` clears on auth failure. | Typed detection (StopFailure `authentication_failed` / `billing_error`; Codex `credits.has_credits=false`). One grouped alert with the fix (`awos keys`), and auto-continue paused everywhere affected. |
| P6 | **Background work: false idle alerts, and silent death across sleep** | CC [#98373](https://github.com/anthropics/claude-code/issues/98373) (idle_prompt fires while background tasks run); [#63023](https://github.com/anthropics/claude-code/issues/63023) (background agents die on sleep with no completion notice); [#81832](https://github.com/anthropics/claude-code/issues/81832) (Mac idle-sleeps mid-task); [#72851](https://github.com/anthropics/claude-code/issues/72851). Owner: **"background task still running" precedes 44% of supervision prompts.** | Claude Code wakes the session when a background task completes. The Stop payload lists `background_tasks`. | Never continue here. Suppress false idle alerts. Show "waiting on bg task #3, 12 min" ambiently. Detect `bg_task_lost` across sleep. Hold a sleep assertion while supervised work runs. |
| P7 | **Backlash when an agent proceeds without consent** | CC [#73125](https://github.com/anthropics/claude-code/issues/73125) (388, 144 comments), [#73621](https://github.com/anthropics/claude-code/issues/73621) ("consent violation"), [#73408](https://github.com/anthropics/claude-code/issues/73408) (AFK timeout "undocumented and has no opt-out"), [#39027](https://github.com/anthropics/claude-code/issues/39027); Codex [#30629](https://github.com/openai/codex/issues/30629). | This is a native feature (the AFK timeout) that users dislike. | **A design constraint, not a feature:** never answer a question on the user's behalf. Opening: hold questions and notify, instead of letting them time out. |
| P8 | **Loops and unproductive token burn** | Gemini [#1531](https://github.com/google-gemini/gemini-cli/issues/1531); CC [#24585](https://github.com/anthropics/claude-code/issues/24585), [#2038](https://github.com/anthropics/claude-code/issues/2038), [#100010](https://github.com/anthropics/claude-code/issues/100010) (`/goal` Stop hook looped "hundreds of rounds" on a user-only OAuth step), [#98066](https://github.com/anthropics/claude-code/issues/98066), [#96148](https://github.com/anthropics/claude-code/issues/96148); [#95964](https://github.com/anthropics/claude-code/issues/95964) (no interactive spend cap); [#100111](https://github.com/anthropics/claude-code/issues/100111) (a $1 cap stops at $1.38). | Claude caps 8 consecutive Stop blocks, but the cap is silent ([#77686](https://github.com/anthropics/claude-code/issues/77686)). There is no interactive budget. | Loop/stall guard, per-session continue and cost budgets, and owner-only-blocker detection. This also guards against the Supervisor's own runaway continues. |
| P9 | **Permission fatigue** | CC [#28240](https://github.com/anthropics/claude-code/issues/28240) (209), [#6850](https://github.com/anthropics/claude-code/issues/6850); Codex [#2860](https://github.com/openai/codex/issues/2860) (110), [#3085](https://github.com/openai/codex/issues/3085) (107), [#10760](https://github.com/openai/codex/issues/10760); opencode [#37564](https://github.com/anomalyco/opencode/issues/37564) (asks for an LLM classifier). | Auto mode exists (Claude). | v1: route prompts with context (notify only). Owner relay is later, after a spike. Auto-approval is out of v1. |
| P10 | **Review/merge/context-switch overload; false "done"** | [Willison](https://simonwillison.net/2025/Oct/5/parallel-coding-agents/) / HN [45489884](https://news.ycombinator.com/item?id=45489884): "review is hands down the biggest bottleneck"; HN [47218318](https://news.ycombinator.com/item?id=47218318); HN [49863043](https://news.ycombinator.com/item?id=49863043): "pretty good at telling you they finished something they didn't". | No. Agent view says `done` from state alone. | Digest ranked by what needs the human, with "done" marked verified or unverified. |
| P11 | Compaction loses the task | Codex [#5957](https://github.com/openai/codex/issues/5957), [#4106](https://github.com/openai/codex/issues/4106); CC [#27242](https://github.com/anthropics/claude-code/issues/27242) (89), [#66144](https://github.com/anthropics/claude-code/issues/66144), [#98747](https://github.com/anthropics/claude-code/issues/98747) | No. | Re-anchor the continue message with the goal and open TODOs from the Supervisor's own state card. |

Low-frequency, high-severity: destructive actions that slip guardrails (CC [#6608](https://github.com/anthropics/claude-code/issues/6608); HN 47218318 "nuked an important untracked directory").

Aider and OpenHands repos show low volume on these themes. The pain is concentrated in the long-running agent CLIs (Claude Code, Codex, Gemini, opencode).

### 1.1 What the pains mean for the owner's buckets (critic's re-split)

| Owner trigger (share of supervision prompts) | Covered natively? | What is left for the Supervisor |
|---|---|---|
| Background task still running (44%) | Partly: the agent wakes when the task completes, but false idle alerts fire and tasks die on sleep | Ambient status (answers "status?" prompts), alert suppression, `bg_task_lost`. **Not auto-continue.** |
| Agent ended turn (19%) | Partly (`/goal`, if set) | **Policy auto-continue: the core and only real continue slice** |
| Long silence (16%) | No | Stall detection plus notification |
| Agent asked a question (12%) | No (and AFK auto-answer is resented) | Hold and notify with context and a suggested answer. Never auto-answer. |
| Key/auth error (7%) | Per-session | Grouped alert with the fix |

**Unknown, and decisive:** the cross-tab of trigger by prompt family. If the 44% background bucket is mostly *status* prompts, the product is mainly a status and attention product, and auto-continue addresses at most about 19% of supervision prompts, minus questions. Also unknown: how many of the 80 "continue" prompts were safe to automate. Nobody has hand-labelled them yet. The baseline already fell from 2.4 to 1.6 per agent-hour with no Supervisor, probably because of native features. Spec v2 M0 measures all three before M2 is built.

---

## 2. Competitive map and gaps {#E-competition}

| Category | Examples (stars / HN; self-reported features) | What they do | Gap for AWOS |
|---|---|---|---|
| Parallel-agent workspaces (very crowded) | [orca](https://github.com/stablyai/orca) 90k (unverified), [vibe-kanban](https://github.com/BloopAI/vibe-kanban) 28k, [cmux](https://github.com/manaflow-ai/cmux) 28k, [happy](https://github.com/slopus/happy) 24k, [superset](https://github.com/superset-sh/superset) 15k, [claude-squad](https://github.com/smtg-ai/claude-squad) 8.6k, [emdash](https://github.com/generalaction/emdash) 6k, [agent-of-empires](https://github.com/agent-of-empires/agent-of-empires) 3.3k, [ccmanager](https://github.com/kbwo/ccmanager) 1.3k, [Conductor](https://conductor.build/pricing) | Launch agents in their own app or PTY, worktree per agent, busy/waiting/idle state | None classify stops, continue under a policy, verify "done", or measure supervision load. **Don't compete: sit above them.** |
| Native vendor surfaces (absorbing fast) | Claude agent view + `claude daemon` ("supervisor process"), `/goal`, quota auto-resume, AFK timeout, Remote Control, push, channels; Codex app, hooks, `/goal`, Remote Control | Single-vendor state, continue on a timer or quota, push | Single-tool, no standing policy, no cross-session budgets, no verification, buggy with several sessions. **Name collision: Claude's "supervisor" process.** |
| Notify-and-reply daemons (closest) | [Lucarne](https://github.com/tuchg/Lucarne) 339 (6 agents, Telegram/WeChat, "Stop babysitting your local AI agents"), [peon-ping](https://github.com/PeonPing/peon-ping) 5k, claude-pulse 247, oly/open-relay ([HN](https://news.ycombinator.com/item?id=47302693)), Kodama ([HN](https://news.ycombinator.com/item?id=47248651)), Gyredeck ([codex#52783](https://github.com/openai/codex/issues/52783)) | Turn prompts and failures into phone messages; reply to unblock | No continue policy, no classifier, no key health, no metric. **Lucarne is one feature away from overlapping.** |
| Blunt keep-going loops | claude-auto-retry (tmux send-keys every 5 s), Ralph loops (ralphy 3k, continuous-claude 1.4k), claude-squad `--autoyes`, CCManager AI auto-approval | Continue on a single trigger, or accept everything | No per-stop decision, budget or audit. Unsafe (CCManager criticises `--autoyes`). |
| Usage/cost dashboards | ccusage 19k, [Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) 8.7k, [Claude-Code-Agent-Monitor](https://github.com/hoangsonww/Claude-Code-Agent-Monitor) 1k, Quotio 4.9k | Tokens and cost | Nobody measures human supervision burden. Caveat: possibly because nobody buys on that metric (critic). |
| Coordination | [Foremerge](https://github.com/naw103/foremerge) 538 (MCP intent leases), [claude-code-merge-queue](https://github.com/funador/claude-code-merge-queue) 128 | Declared-intent locks; FIFO land-with-checks | No passive cross-tool overlap detection from observed edits. |
| Cloud agents | Copilot agent (59-min cap), Cursor cloud agents, Codex cloud; Terragon shut down; Omnara pivoted | Remote, PR-steered | A later read-only digest source. Category churn is a commercial warning. |

**Real gaps (where AWOS can be clearly better):**
1. A per-stop decision (routine vs. real decision), under a standing owner policy, with budgets and an audit trail, across tools.
2. Verified "done" (checks and diff), not state-only.
3. Typed provider-error handling across tools, explicitly requested by orchestrator builders in [codex#45977](https://github.com/openai/codex/issues/45977): "could not tell idle from stuck".
4. Stop-hook arbitration when several Stop hooks exist ([codex#32295](https://github.com/openai/codex/issues/32295)).
5. A measured attention-cost KPI.

The claim that cross-tool policy is the durable value is a **positioning hypothesis**, not a finding. No report showed users want cross-tool policy rather than less babysitting from one tool.

---

## 3. Reliability guidance {#E-reliability}

Principle: **the Supervisor is an accessory, never a dependency.** Every agent-to-Supervisor path has a short deadline and fails open. Every event can be rebuilt after a crash or sleep. Every config edit is reversible and checked for drift. Every action happens at most once.

| Fact | Source | Design consequence |
|---|---|---|
| Claude HTTP/command hooks default to a **600 s timeout** | [hooks](https://code.claude.com/docs/en/hooks) | Stop hook entry sets `timeout: 15`. Daemon's internal deadline is ~8 s, and on a miss it returns `{}` (allow stop). |
| HTTP hooks fail open: non-2xx, connection refused and timeout are all non-blocking unless `onFailure: block` | [hooks](https://code.claude.com/docs/en/hooks) | Never set `onFailure: block`. Daemon down means agent unchanged. |
| Hook delivery is at most once. ~1.4% ECONNRESET loss, no retry (measured on Windows; macOS rate unmeasured) | [#96657](https://github.com/anthropics/claude-code/issues/96657) | Hooks are not a complete log. A reconciler fills gaps from `claude agents --json`, transcripts and rollouts. The KPI is computed from work_miner transcripts, not hooks. |
| Command-hook timeouts are not reliably enforced (3 s declared, 303 s actual; wedged Node start froze a session) | [#87289](https://github.com/anthropics/claude-code/issues/87289), [#85250](https://github.com/anthropics/claude-code/issues/85250) | Synchronous Claude Stop path is `type: http` (no subprocess). Codex shims read stdin with a deadline and always exit 0 within 1-2 s. |
| Claude Code rewrites `~/.claude/settings.json`, dropping hooks (5 hooks gone for ~3 days) and stripping unknown keys | [#93742](https://github.com/anthropics/claude-code/issues/93742), [#88113](https://github.com/anthropics/claude-code/issues/88113) | Ship hooks as a **local Claude Code plugin** (`hooks/hooks.json`), as [Fleet Deck](https://github.com/lacion/fleet-deck) does (self-reported). Don't edit settings.json in the default path. Run continuous drift detection. |
| `ConfigChange` hook fires on settings changes | [hooks](https://code.claude.com/docs/en/hooks) | Real-time "supervisor disarmed" detection. |
| `claude agents --json` states are undocumented and change | [#87883](https://github.com/anthropics/claude-code/issues/87883) | Unknown state = running for liveness, and never auto-continued. |
| Codex hooks are trusted **by hash**; a changed hook is skipped until re-approved in `/hooks` | [Codex hooks](https://learn.chatgpt.com/docs/hooks) | Byte-stable shim path and arguments forever (behaviour lives in the daemon). Doctor detects "untrusted/skipped". |
| Codex state lives in versioned SQLite (`state_5.sqlite`, `logs_2.sqlite`) and rollouts up to GBs | Local; [codex#24948](https://github.com/openai/codex/issues/24948); [SQLite URI](https://www.sqlite.org/uri.html) | Open read-only with `mode=ro`, never `immutable=1`. Tail by byte offset plus inode. Cap bytes per scan. |
| SQLite WAL: one writer; constant readers starve checkpoints; `synchronous=NORMAL` can lose commits on power loss | [SQLite WAL](https://www.sqlite.org/wal.html) | Single writer thread, `synchronous=FULL` (the volume is tiny), short read transactions, periodic `wal_checkpoint(TRUNCATE)`. |
| launchd: unset `ProcessType` gives throttled CPU/IO; no daemonizing; ThrottleInterval 10 s | [launchd.plist(5)](https://keith.github.io/xcode-man-pages/launchd.plist.5.html) | `ProcessType=Adaptive`, `KeepAlive={Crashed:true, SuccessfulExit:false}`, no fork, single instance via flock plus port bind. |
| Background agents die on sleep without notice; Claude spawns caffeinate itself | [#63023](https://github.com/anthropics/claude-code/issues/63023), [#21432](https://github.com/anthropics/claude-code/issues/21432), [#81832](https://github.com/anthropics/claude-code/issues/81832) | Find sleep gaps by comparing wall-clock and monotonic deltas. Reconcile on wake before classifying. Sleep doesn't count as silence. `bg_task_lost` event. |
| `mach_absolute_time` / Python monotonic probably stops during sleep (**unverified**; docs ambiguous) | [Python time](https://docs.python.org/3/library/time.html) | M0 empirical test on the M5 before sleep logic relies on it. |
| Same-user processes can spoof session ids to a loopback daemon | [codex#52783](https://github.com/openai/codex/issues/52783) | 32-byte token header, Host/Origin checks, payload session_id treated as a claim. |
| PermissionRequest decisions discarded in `--bg` sessions (issue) vs. the docs saying hooks apply to background sessions | [#88698](https://github.com/anthropics/claude-code/issues/88698) vs [agent-view](https://code.claude.com/docs/en/agent-view) | **Contradictory.** Any permission relay must be spiked per session kind, and must fall back to notify-only for `--bg`. |

Prior art for the whole pattern (fail-open plugin hooks, deadlines, port election, doctor, spool, backup plus dry-run): Fleet Deck ([HN](https://news.ycombinator.com/item?id=48898736)) and [cctap](https://github.com/chipmates/cctap) ([HN](https://news.ycombinator.com/item?id=49166844)). Both self-reported.

---

## 4. Classification and loop-detection evidence {#E-classify}

### 4.1 Deterministic signals, no LLM needed {#E-signals}

- **Claude Stop input:** `stop_hook_active`, `last_assistant_message`, `background_tasks` (id/type/status), `session_crons`. The docs say `background_tasks` exists to tell "done" from "paused waiting for background work". The transcript "isn't guaranteed to include the final message at Stop time" ([hooks](https://code.claude.com/docs/en/hooks)).
- **Claude StopFailure `error`:** `rate_limit | overloaded | authentication_failed | oauth_org_not_allowed | account_on_hold | billing_error | invalid_request | model_not_found | server_error | max_output_tokens | cloud_credential_error | unknown`. Output is ignored, so observe only.
- **Claude Notification types:** `permission_prompt`, `idle_prompt`, `elicitation_*`, `agent_needs_input`, `agent_completed` (**fires only while agent view is open**), `quota_auto_resume_fired/stale/disabled`.
- **Codex Stop input** (generated schema, [codex-rs/hooks/schema](https://github.com/openai/codex/tree/main/codex-rs/hooks/schema/generated)): `cwd, hook_event_name, last_assistant_message, model, permission_mode, session_id, stop_hook_active, transcript_path, turn_id`. Output: `decision: block` plus `reason` becomes a continuation prompt. **No `background_tasks`, and no flag for an active `/goal`** ([codex#52950](https://github.com/openai/codex/issues/52950), filed on 0.149).
- **Codex rollout `token_count.rate_limits`:** `used_percent`, `resets_at`, `credits.has_credits`, `rate_limit_reached_type` (Local).

### 4.2 Platform continuation caps and native loops {#E-caps}

- Claude caps consecutive Stop-block continuations at **8** (`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`). The count resets on any tool call, and the override is silent to the hook ([#77686](https://github.com/anthropics/claude-code/issues/77686)).
- Each continue re-reads the whole context. Nudging while a background subagent is writing can commit a half-written tree ([#93745](https://github.com/anthropics/claude-code/issues/93745)).
- `/goal` template ([goal docs](https://code.claude.com/docs/en/goal)): skips evaluation while background work runs; check-ins at 30 min then 2x and 4x backoff, max 3 idle check-ins; stops after several turns with no tool use; clears on auth, credit or context failure; retries transient errors 3 times then pauses. Its evaluator "does not call tools". The claim that it is "fooled by a convincing transcript" is third-party inference.

### 4.3 Prior art for guards {#E-guards}

- **OpenHands StuckDetector** ([source](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/stuck_detector.py)): scans the last 20 events. Fires on the same action+observation x4, the same action-error x3 (one nudge first), a monologue x3, an A-B alternation x6, and context-error loops.
- **Claude auto mode** ([engineering post](https://www.anthropic.com/engineering/claude-code-auto-mode)): a cheap stage 1 tuned to block, then a chain-of-thought stage 2 run only on flags. It strips assistant text and tool outputs from the classifier input. Full pipeline FPR is 0.4%, but FNR is **17% on 52 real overeager actions**. The main miss is "approval-shaped evidence". It escalates after 3 consecutive or 20 total denials.
- **AWOS already has:** `scaffold/agent/host/autopilot.py` (`diff_fingerprint`, `progress`, `continuation_decision`), `scaffold/agent/loop_guard.py`, and `work_miner_label.py` / `work_miner_sessions.py` (prompt families plus what preceded each supervision prompt, which gives weak labels for a stop corpus).

### 4.4 How reliable are judges and confidence? {#E-judges}

- LLM trajectory judges are uneven: "no single LLM excels across all benchmarks" ([AgentRewardBench](https://arxiv.org/abs/2504.08942)). AWOS ATLAS notes the best computer-use judge reaches about 81% balanced accuracy. **An LLM must not be the only key that unlocks a continue.**
- End-of-attempt verbal confidence predicts task failure at AUROC ~0.85, and mid-run it is ≤0.60 ([Last Step Matters](https://arxiv.org/html/2608.29685)). This is about task failure, not stop-type classification, so it supports at most an optional "how sure are you it's done" probe at `done` stops.
- False "done" is common: the AWOS declared gate falsely accepted 13/25 hidden-fail runs (52%, Wilson 33-70%). This is **AWOS's own agent, n=25**, and is not a property of Claude Code or Codex. [ImpossibleBench](https://arxiv.org/abs/2510.20270) and [TestJack](https://arxiv.org/abs/2610.10619) point the same way (figures taken from internal notes).
- Wink ([2602.17037](https://arxiv.org/abs/2602.17037)) and Cognitive Companion ([2604.13759](https://arxiv.org/abs/2604.13759)) are self-reported or small studies and don't anchor the design.

### 4.5 Calibration math and its limits at n=1 {#E-calibration}

- Conformal risk control ([2208.02814](https://arxiv.org/abs/2208.02814)), KnowNo ([2307.01928](https://arxiv.org/abs/2307.01928)) and conformal abstention ([2405.01563](https://arxiv.org/abs/2405.01563)) give a principled threshold for bounding the false-continue rate.
- **Arithmetic check (critic):** these methods assume exchangeable data. Claude Code ships weekly and models drift. With about 80 continue examples total, per-(agent x model-version) cells hold almost no data. **v2 decision: use one pooled Clopper-Pearson upper bound on live shadow decisions as the gate. Treat version changes as a monitored drift signal (auto-tighten), not as a recalibration cell.**
- Rule of three: 0 errors in 60 decisions gives a 95% upper bound of about 5%. 1 error needs about 93 decisions, and 2 errors about 124. **20 decisions at 90% agreement certifies nothing below about 30%.** This settles the R4 vs R6 conflict in favour of R4.

---

## 5. Advanced capabilities, ranked (value x feasibility for a solo builder) {#E-advanced}

| Rank | Capability | Value | Feasibility | Evidence | Verdict |
|---|---|---|---|---|---|
| 1 | **Ambient status + attention inbox** (answers "status?" without asking) | 5 | 5 | Status is 93/644; cmux 28k; context-free alerts (cmux HN) | v1 core |
| 2 | **Typed provider-error handling + grouped key alert** | 4 | 5 | StopFailure enum; codex#45977; owner 7% | v1 core |
| 3 | **Policy auto-continue (turn ended with work left)** | 4 | 3 | P2; owner 19% | v1, gated by shadow |
| 4 | **Verified-done in digest** (async checks after stop; tests/diff/todos) | 5 | 4 | HN 49863043; AWOS gate 52% FA; merge-queue pattern | v1, **digest-only**. Check-gated continue is later, because checks can't fit the Stop deadline and asyncRewake on Stop is unverified. |
| 5 | **Cost/quota governance across tools** | 5 | 3 | ccusage 19k; #95964; #100111 | v1: per-session continue budgets plus cost read from transcripts/rollouts. OTel receiver is a should. Caps are soft. |
| 6 | **"While you were away" handoff / state card** | 4 | 4 | Willison; #27242; #11455 | v1 digest; re-anchor after compaction is a should |
| 7 | Stall / `bg_task_lost` / sleep resurrection | 4 | 3 | #63023, #81832, #72851; `quota_auto_resume_stale` | should |
| 8 | Cross-session file-conflict warnings (advisory) | 4 | 4 | Foremerge, merge-queue, HN quotes | should (M4) |
| 9 | Preference capture → AGENTS.md/CLAUDE.md, with promotion to enforced hooks | 4 | 4 | [memory docs](https://code.claude.com/docs/en/memory) ("context, not enforcement"); #6235 | M4 |
| 10 | Phone push (ntfy), no approval relay | 3 | 4 | Omnara HN [46991591](https://news.ycombinator.com/item?id=46991591); native push exists | later, opt-in |
| 11 | Permission owner-relay | 3 | 2 | Contradictory bg behaviour (#88698) | later, after spike |
| 12 | Worktree lanes / merge queue; quota-window scheduling; cross-agent routing | 3 | 2 | crowded; claude-code-router 37k; AWOS bench parity | later / not ours |

---

## 6. Human-factors requirements {#E-hf}

| Principle | Source | Requirement |
|---|---|---|
| Interruptions cost stress even when handled fast | [Mark et al. 2008](https://doi.org/10.1145/1357054.1357072) | **KPI = attention cost** (owner supervision prompts plus interrupting notifications), reported in two parts. Otherwise the metric can be gamed by turning prompts into pings. |
| Deliver non-urgent alerts at breakpoints | [Iqbal & Bailey 2010 (Oasis)](https://doi.org/10.1145/1879831.1879833) | Tier 2 waits for a breakpoint (owner prompted another session, or keyboard idle >60 s), ceiling 15 min. |
| Every page actionable, needs judgement, novel; "robotic response" shouldn't page | [Google SRE](https://sre.google/sre-book/monitoring-distributed-systems/) | Routine continues are done, not reported. Dedupe per (session, issue class). |
| Group storms; channel by urgency | [PagerDuty](https://www.pagerduty.com/resources/digital-operations/learn/alert-fatigue/) (vendor) | Three tiers; storm merge; cap of 1 non-Tier-1 interrupt per 10 min. |
| Low-precision alerts get ignored, critical ones included | [AHRQ PSNet](https://psnet.ahrq.gov/primer/alert-fatigue) | Track per-category precision. Auto-propose demotion when the acted-on rate is below 50%. Re-notify once only. |
| Automate acquisition and analysis; suggest decisions; gate risky actions | [Parasuraman, Sheridan & Wickens 2000](https://doi.org/10.1109/3468.844354) | Watch, classify and digest fully. Suggest answers. Continue only routine stops. Permission prompts stay human. |
| Out-of-the-loop; complacency not fixed by practice | [Endsley & Kiris 1995](https://doi.org/10.1518/001872095779064555), [Parasuraman & Manzey 2010](https://doi.org/10.1177/0018720810376055) | Weekly blind spot-check of random auto-continues. Show the track record and reasoning. |
| Ironies of automation: rare interventions land with least context | [Bainbridge 1983](https://en.wikipedia.org/wiki/Ironies_of_Automation) | Every escalation carries its own context (session, need, suggested answer, reason label). |
| Undo makes action-then-inform acceptable | [Gmail Undo Send](https://support.google.com/mail/answer/2819488) | A git checkpoint (`git stash create` / `write-tree`) before each continue, with [Rewind]. Rewind states what it cannot undo. |
| Calibrated trust needs a visible record | [Lee & See 2004](https://doi.org/10.1518/hfes.46.1.50.30392) | Digest headline: "auto-continued N · rewound n · escalated M (acted on m)". |
| Batching beats real-time; turning everything off raises anxiety | [Fitz et al. 2019](https://doi.org/10.1016/j.chb.2019.07.016) (**recalled**) | A digest plus a narrow interrupt channel. |
| Suggested answers invite automation bias | Parasuraman & Manzey | No one-tap suggestion on never-list or destructive items. |
| macOS `osascript` banners have no action buttons | Platform | One-tap answers go through a click-through to the local page (v1). A native helper is later. |

Figures recalled from papers and not re-verified (Iqbal-Horvitz resumption times, Fitz conditions, "net benefit at ~70% reliability") are not used in launch material.

---

## 7. Platform integration matrix {#E-platform}

| Tool (local version) | Observe | Continue (live) | Continue (stopped/headless) | Approve | Cost / quota | Risk | Order |
|---|---|---|---|---|---|---|---|
| **Claude Code** 2.1.296 | Plugin HTTP hooks (SessionStart, UserPromptSubmit, Stop, SubagentStop, Notification, StopFailure, SessionEnd, ConfigChange); poll `claude agents --json --all` (state, status, waitingFor); transcripts best-effort | Stop `{decision:block, reason}` (cap 8, resets on tool call) | `claude --resume <id> --bg "<prompt>"`. **May start a copy if the session is live** ([agent-view](https://code.claude.com/docs/en/agent-view)), so use only on reaped sessions. Never `-p --resume` on a live session. | PermissionRequest hook allow/deny (bg behaviour contradictory) | Transcript usage; statusline JSON (owner already has a custom statusLine, so don't touch it); OTel opt-in ([monitoring](https://code.claude.com/docs/en/monitoring-usage)) | Low for documented hooks and `--json` | v1 first |
| **Codex** 0.148.0 (latest rust-v0.162.1) | `~/.codex/hooks.json` command hooks via a fail-open shim (**no HTTP hook type**); rollout JSONL tail (`task_started/complete`, `token_count`, `context_compacted`) | Stop `{decision:block, reason}`. **Fires in the TUI ([#52949](https://github.com/openai/codex/issues/52949)), but no report has shown a block continuing a 0.148 TUI session.** Known bugs: [#20783](https://github.com/openai/codex/issues/20783), [#34418](https://github.com/openai/codex/issues/34418), [#39858](https://github.com/openai/codex/issues/39858) | `codex exec resume <id> "<prompt>"` | PermissionRequest allow/deny only (other fields fail closed) | `token_count` events; `exec --json` `turn.completed.usage` | Low-medium; hooks "stable" (Local, `codex features list`); trust by hash | v1 second, **gated on spike** |
| Codex legacy `notify` | argv JSON on turn end | none | n/a | n/a | n/a | **Legacy, TODO-remove** ([legacy_notify.rs](https://github.com/openai/codex/blob/main/codex-rs/hooks/src/legacy_notify.rs)); slot taken by `SkyComputerUseClient turn-ended` (Local) | **Do not use; leave untouched** |
| Codex app-server | 95 JSON-RPC methods (`thread/list`, `turn/steer`, `thread/goal/get`, `account/rateLimits/read`) | `turn/start`, `turn/steer` | `thread/resume` | ServerRequests | `thread/tokenUsage/updated` | **Experimental**; attaching to TUI-owned threads unverified | later |
| AWOS host jobs | queue/journal/inbox | host autopilot | host autopilot | n/a | budget ledger | Native | v1 |
| OpenCode 1.18.19 | SSE `/event`, `/session/status` | `POST /session/:id/prompt_async` | same | `POST /permission/:id/reply` | `AssistantMessage.cost` | Medium; two API generations; TUI uses a random port | v1.5 (served sessions only) |
| Cursor agent CLI | `~/.cursor/hooks.json` (CLI support unconfirmed) | stop `followup_message` (loop_limit 5) | `agent --resume` | beforeShellExecution | none | Medium-high, unverified | v2 after test |
| Gemini CLI / Antigravity | hooks in settings.json | AfterAgent `decision: deny` retries | n/a | BeforeTool | none found | Medium-high; replaced by Antigravity for unpaid users 2026-06-18 | v2 |
| Aider | `--notifications-command`, chat history | none | `--message` re-run | `--yes-always` only | text only | High; no API | generic PID adapter, notify-only |

---

## 8. Critique and open risks {#E-critique}

### 8.1 Contradictions between reports and how spec v2 resolves them

| # | Conflict | v2 resolution |
|---|---|---|
| 1 | Codex: chain `notify` (R3) vs `hooks.json` (R2/R4/R5/R7); is 0.148 enough? | `hooks.json` is primary. `notify` is left untouched, with no chaining at all. Live-session continue is **gated on an M0 spike** that runs a Stop block in a 0.148 TUI, upgrading Codex if needed. Until the spike passes, interactive Codex is observe and notify only, and headless Codex uses `exec resume`. |
| 2 | 44% background bucket: auto-continue (R2) vs never (R4/R7) vs suppress alerts (R6) | **Never continue** (deterministic guard on `background_tasks`). Suppress false idle alerts. Ambient status. `bg_task_lost`. M0 cross-tab decides how much product weight this bucket gets. |
| 3 | Usage-limit resume: build it (R1) vs defer to native (R2/R4/R5) | **Defer on Claude.** Observe `quota_auto_resume_*` and verify the session actually resumed. Notify on `stale`, `disabled` or missed sessions. Resume reaped sessions only, and only with consent per session. **Build** reset-resume only for headless Codex. |
| 4 | Shadow exit: ≥60 at UB≤5% (R4) vs ≥20 at 90% (R6); caps 2/3/3-5/6 | Adopt R4's statistical gate. One cap set: max 3 consecutive per session, each requiring progress; at most 1 no-progress continue; max 3 per owner prompt; max 6 per session-hour. |
| 5 | Plugin (R3) vs settings.json + statusLine chaining (R7) | **Plugin only.** No statusLine edits. Cost comes from transcripts and rollouts (OTel opt-in). settings.json fallback only when the owner asks, with drift detection. |
| 6 | PermissionRequest in `--bg`: discarded (#88698) vs applies (docs) | Permission relay moves to later, behind a spike per session kind. v1 notifies only. |
| 7 | Test-gated continue inside Stop vs a 15 s deadline | Verification runs **async after the stop** and only labels the digest (verified/unverified). Check-gated continue is later, pending the asyncRewake spike. |
| 8 | Skip LLM on battery (R3) vs two-key rule (R4) | Skip the LLM only below 20% battery or in Low Power Mode, not merely on battery. The second key may be **structural evidence** (open TodoWrite/plan items in the transcript plus a closing-permission phrasing), calibrated in shadow like the LLM key. |
| 9 | KPI: prompts/agent-hour (R2) vs attention cost (R6) | **Attention cost per agent-hour** is the headline, reported in two parts. |
| 10 | `agent_completed` as a primary source vs "only while agent view is open" | Not a primary source. `claude agents --json` polling and the Stop/StopFailure hooks are primary. |
| 11 | Reaction counts disagree | Ranges or lower bounds here. Re-pull before external use. |

### 8.2 Questions the research did not answer (become M0 tasks or owner decisions)

1. How much is still addressable after native features? Requires re-baselining on current versions.
2. The trigger by prompt-family cross-tab.
3. How many of the 80 continue prompts were safe to automate (hand-label).
4. Does a continue pay for itself? Is the owner on a subscription or an API key for Claude Code and Codex? This changes what "cost" means.
5. Do Anthropic and OpenAI consumer-plan terms allow automated continuation? Check before M2.
6. False continues while the owner is away. The "interrupt within 2 min" proxy undercounts them, so a **blind after-the-fact audit** is added.
7. Corpus upkeep with weekly releases. v2 uses a narrow contract test, not a full replay corpus per version (§E-reliability).
8. Does the deferred-continue path work at all? (asyncRewake on Stop, `--resume --bg` copy vs same-id, Codex TUI block.) All are spikes.
9. Will Supervisor notifications duplicate the owner's native push? Dedupe against it, or ask the owner to pick one.
10. Who besides the owner wants this? Launch (M5) is conditional on the trial result.
11. Opportunity cost against the benchmark track (AWOS 64% vs Aider 61%, n.s.): there is a kill criterion after M0.

### 8.3 The three ways the product fails (critic) and the v2 countermeasure

1. **The value disappears** (a small and shrinking addressable slice, and a confounded before/after trial). Countermeasures: M0 re-baseline with a **kill/pivot criterion**, and the trial **randomised by day** within the same period, not before/after.
2. **Collapse into a notifier, or one bad continue** (the safety stack pushes coverage to zero; approval-shaped text; double continue; silent disarm; about 80 "musts"). Countermeasures: a hard v1 scope cut, a structural second key, coexistence rules with `/goal` and native resume, drift detection with a "disarmed" banner, and canaries.
3. **Platform absorption.** Countermeasure: keep the build thin over documented surfaces. Durable assets are the owner-calibrated stop ledger, cross-tool attention UX and the measured result. Accept that a vendor "auto-continue when work remains" setting could remove the core feature, so the attention and verification features must stand on their own.

---

## 9. Sources

**Official docs:** https://code.claude.com/docs/en/hooks · https://code.claude.com/docs/en/hooks-guide · https://code.claude.com/docs/en/agent-view · https://code.claude.com/docs/en/goal · https://code.claude.com/docs/en/plugins-reference · https://code.claude.com/docs/en/monitoring-usage · https://code.claude.com/docs/en/statusline · https://code.claude.com/docs/en/channels · https://code.claude.com/docs/en/remote-control · https://code.claude.com/docs/en/memory · https://code.claude.com/docs/en/auto-mode-config · https://www.anthropic.com/engineering/claude-code-auto-mode · https://learn.chatgpt.com/docs/hooks · https://learn.chatgpt.com/docs/config-file/config-reference · https://github.com/openai/codex/tree/main/codex-rs/hooks/schema/generated · https://github.com/openai/codex/blob/main/codex-rs/hooks/src/legacy_notify.rs · https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs · https://github.com/openai/codex/tree/main/codex-rs/app-server · https://opencode.ai/docs/server/ · https://geminicli.com/docs/hooks/reference/ · https://cursor.com/docs/agent/hooks · https://aider.chat/docs/scripting.html · https://www.sqlite.org/wal.html · https://www.sqlite.org/uri.html · https://keith.github.io/xcode-man-pages/launchd.plist.5.html · https://docs.python.org/3/library/time.html · https://developer.apple.com/documentation/usernotifications/unnotificationinterruptionlevel

**Issues (Claude Code):** 1632, 2038, 3572, 6608, 6850, 7530, 11455, 12447, 13024, 13354, 13922, 16157, 16211, 19627, 21432, 24317, 24585, 26224, 27242, 28240, 28482, 28765, 29438, 32634, 35744, 35773, 38335, 39027, 46829, 53610, 55875, 56487, 58909, 63023, 63060, 66144, 69415, 72851, 73125, 73408, 73621, 77686, 78610, 79881, 81832, 85047, 85250, 87289, 87883, 88113, 88698, 91493, 91535, 92004, 92418, 93742, 93745, 95964, 96148, 96657, 98066, 98373, 98747, 100010, 100111 (all at `https://github.com/anthropics/claude-code/issues/<n>`).

**Issues (Codex):** 2109, 2860, 3085, 3247, 4106, 5957, 10760, 14048, 14513, 20783, 24948, 28879, 30629, 32295, 33398, 34188, 34418, 34900, 36596, 39858, 45977, 49735, 52529, 52783, 52949, 52950 (`https://github.com/openai/codex/issues/<n>`). **Others:** gemini-cli 1531, 13592, 22141, 22633; opencode 8463, 11865, 13217, 13715, 37564; OpenHands 7183.

**Community / competitors:** Lucarne, claude-auto-retry, ccusage, Claude-Code-Usage-Monitor, Claude-Code-Agent-Monitor, orca, vibe-kanban, cmux, happy, superset, claude-squad, emdash, agent-of-empires, ccmanager, peon-ping, Foremerge, claude-code-merge-queue, fleet-deck, cctap, omnara, terragon-oss, claude-code-router (URLs in §2/§3). HN: 45489884, 46424471, 46694641, 46846287, 46859054, 46991591, 47030623, 47079718, 47111171, 47218318, 47248651, 47302693, 48898736, 49166844, 49863043.

**Papers:** Conformal Risk Control 2208.02814 · KnowNo 2307.01928 · Conformal Abstention 2405.01563 · Selective CRC 2512.12844 · Online Conformal Abstention 2506.14067 · AgentRewardBench 2504.08942 · Last Step Matters 2608.29685 · ImpossibleBench 2510.20270 · TestJack 2610.10619 · Wink 2602.17037 · Cognitive Companion 2604.13759 · Mark 2008 · Iqbal & Bailey 2010 · Parasuraman et al. 2000 · Endsley & Kiris 1995 · Parasuraman & Manzey 2010 · Lee & See 2004 · Fitz 2019 (DOIs in §6).

**Local / AWOS:** `scaffold/agent/host/autopilot.py`, `scaffold/agent/loop_guard.py`, `scaffold/agent/notify.py`, `scaffold/agent/key_health.py`, `scaffold/agent/work_miner*.py`, `docs/specs/gate_accuracy_analysis.md` (commit 8aa052d), `docs/expedition/ATLAS.md`; `~/.codex/config.toml` (notify slot), `codex features list` (hooks stable), `claude agents --json` live output.

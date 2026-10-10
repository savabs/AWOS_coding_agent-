# AWOS Local-First Architecture: Final Recommendation (2026-10-09)

*Scope: VISION Stage 1 (one excellent worker). Coding is the structural testbed; local computer use is the end goal. Every model score below is self-reported unless marked as independent.*

---

## 1. Bottom line

- **Build "Gatekeeper": one worker, local model first, a calibrated verifier gate, and escalation of the whole attempt to the cloud only when deterministic checks fail.** It is not a router zoo and not multi-agent. The arbiter, test generator and grounder are tool calls inside one job. On top of that, add a zero-LLM replay tier for routines that have been verified (taken from the "Experience Compiler" design). It starts with operational coding steps and later covers desktop chores.
- **The defensible asset is the verifier and its measured false-accept rate on the owner's own work, not the model.** Around it sit an evidence ledger and a library of compiled macros whose results were checked against real state. To be honest: the patterns (regression guard, cascade) are easy to copy. What a competitor cannot copy is *the calibration data and the state-verified macros for this owner's repos and apps*. That is why replay work starts early.
- **Local does not win on dollars for coding today.** DS-V4-Flash costs about $0.005–0.01 per task, and a Mac mini costs roughly $1.6–2.2 per day amortized, which is the whole API budget. Local wins on:
  - privacy and offline operation;
  - unattended operation (the host is needed anyway, because runs died to laptop sleep);
  - near-free pass@k behind a trusted gate;
  - zero-LLM replay;
  - computer use, where screenshot-heavy cloud calls are expensive.

  Every claim that "local wins" has to be shown as hidden-verified solves per API dollar, with p90 seconds and **wall-meter** Wh reported alongside.
- **Expect the local model to trail Flash on hard issues.** Qwen3.6-35B-A3B scores 24.7% pass@1 and 43.2% pass@5 on the independent SWE-rebench, against 40.2% for DS-V4-Pro (Flash is unmeasured there). So local adequacy is judged on a **routine-task set** that matches the owner's actual claim ("trivial work"). The 28 hard issues stay as a stress set.
- **Memory holds only L2 evidence:** hidden tests, merged PR, green CI, or no revert. The rejected experience store gave +21pp solve but spread wrong fixes, because "verified" meant visible tests only. The literature reproduces this: add-all memory scored below fixed memory, and strict gating recovered 15–19 points.
- **Measure before building.** Order: X0 offline gate audit at $0, cache and Wh telemetry, the owner's chip and RAM, then a local baseline on the owner's existing Mac on AC power. **No hardware purchase until local wins at least one measured bucket.**
- **Fine-tuning is flagged, not proposed.** Log trajectories in a training-ready format now. Bring a LoRA proposal to the owner only once there are at least 1,000 L2 trajectories across at least 20 repos and memory gains have plateaued. A run costs about $100 per 90M tokens on Tinker for Qwen3.6 (an earlier $30 estimate was wrong).

---

## 2. Recommended architecture

### 2.1 Components (mapped to the VISION layout)

| Layer | Component | State today |
|---|---|---|
| runtime/ | **Always-on host**: launchd KeepAlive, `pmset sleep 0 autorestart 1`, keys in Keychain | job_host.py exists; launchd/pmset missing |
| runtime/ | **Step journal**: append-only per-job JSONL/SQLite with idempotency keys. LLM calls replay from cassette.py; side-effect steps are re-verified against state, not re-run | Missing; recover() re-queues the whole job |
| runtime/ | **Workspaces**: T0 Seatbelt (exists), T1 Apple container / Docker sbx microVM, T2 Lume (MIT) macOS VM (max 2 per Mac) or Xvfb container. Golden-image clones (APFS clonefile / git worktree) | T0 only |
| runtime/ | **Proxies**: host-side domain allowlist, plus a git/credential proxy (push only to `awos/*`, PRs created host-side). Tokens never enter the sandbox | Missing; network is "off" |
| cognition/ | **Model endpoints**: one OpenAI-compatible interface with explicit `provider=local\|deepseek\|openrouter`. A long-lived llama-server or mlx_lm server on the **bare host** (VMs get no Metal) | providers.chat_client routes to OpenRouter whenever its key is set; **must fix** |
| kernel/ | **Cascade controller**: replaces the 2,635 unmeasured lines across 8 router files | To build |
| kernel/ | **Gate** (V0–V3, below) | Partial (acceptance.py, arbiter) |
| kernel/ | **Evidence ledger + telemetry**: per call: tier, prompt_tokens, cached_tokens, $, seconds. Per attempt: gate signal vector, visible/hidden status per repair round, Wh. OTel GenAI span shape | cached_tokens absent; only hit rate on record is 3.9% on old deepseek-chat |
| kernel/ | **Memory**: repo cards, guarded macros, lessons (later), static routing table | Experience store rejected |
| apps/ | coding (App #1): one-shot + gate. desktop (App #2): structured-first action ladder | |

### 2.2 Control flow for one task

```
task ─► privacy flag (local_only | cloud_ok | ask)
     ─► T0 REPLAY: guarded macro whose preconditions match? run with 0 LLM calls → post-state check → done | fall through
     ─► routing table: bucket (repo, file size, issue type, traceback?) has local hidden-pass below threshold? → skip to C1
     ─► L0 LOCAL attempt (one-shot, verbatim goal, SEARCH/REPLACE, byte-stable prefix)
          guards: tail-repetition kill, max_tokens sized to edit, forced edit after 6 read-only turns,
                  stop on 3 repeated action hashes
     ─► GATE V0→V3 → one bounded repair → restore-on-failure
     ─► PASS → hand off as PR (L1 evidence) ;  later merge/CI/no-revert → L2
     ─► FAIL/trigger → C1 DS-V4-Flash: FRESH attempt from the same prefix (local diff discarded)
          (local_only tasks never escalate; they return a report)
     ─► optional C2 stronger model, under a daily $ cap, off by default
```

Escalation happens **only at attempt boundaries**. "Last Step Matters" found that mid-trajectory signals reach AUROC ≤0.60 while final-step signals reach 0.85. TwinRouterBench found that rule-based per-step routing cost more than 3x as much as not routing at all.

The escalation triggers are deterministic and close to 100% precise:
- empty diff;
- no valid edit block after 2 fix-it retries;
- repeated action hash;
- output cap hit or tail repetition;
- K read-only turns;
- gate still red after the repair.

Their thresholds are set from the X0 audit.

### 2.3 Which model runs where

| Role | Where | Model (decided by experiment, not by model cards) |
|---|---|---|
| Coding worker L0 | Host (Mac) | Qwen3.6-35B-A3B, calibrated Q4 (UD-Q4_K_XL / MLX DWQ), about 20 GB, **thinking off** by default (A/B it). Quality arm: Qwen3.6-27B + MTPLX. Next candidate: Qwen3.8-27B (claims unverified). Floor for 16–24 GB machines: Qwen3.5-9B |
| Test generation, arbiter, repair | Same server, same prefix | Same L0 model (shares the KV cache) |
| Escalation C1 | Cloud | DS-V4-Flash, DeepSeek direct off-peak for batch work; through OpenRouter, never set provider.order |
| Offline teacher | Cloud, occasional | Flash or stronger, writes repo cards, macros and skills on a practice split |
| Computer-use planner | Host | L0 text model over a pruned AX/DOM list |
| Computer-use grounder (pixel fallback only) | Host | MAI-UI-2B/8B (ScreenSpot-Pro 57.4/65.8, about +5pp with crop-zoom); licence check needed |

Serving settings: `cache_prompt`, `-np 2–4` slots for pass@k, q8_0 KV cache (never q4 above 32k), context kept to 32–64k. Avoid llama.cpp MTP on Metal; it is 11–24% slower.

### 2.4 Verification at every step

**Coding gate**, run in cost order; the first hard red ends the attempt:
- **V0 structural (free):**
  - empty diff;
  - unapplied or malformed edit;
  - syntax and import of touched modules;
  - **any diff to test files is rejected** (tests are read-only).
- **V1 acceptance:** generated tests from 2 prompts or seeds, kept only if they fail at start. They are a *reject filter, never a certificate*; Otter measured 65–92% precision at 30–41% recall.
- **V2 held-out regression guard** (TestPrune-style): repo tests relevant to the touched files, run before and after the edit, never shown to the worker. Reported +8–13% relative resolve. Budget the environment-setup cost. If the suite won't run, log `guard_unavailable` explicitly and never skip it silently.
- **V3 execution-grounded arbiter:** for contested cases only. It writes and runs a distinguishing script, and it has a `flag_contradiction` exit. In ImpossibleBench, offering such an exit cut test-gaming from 49–54% to 9–12%.
- The existing single repair and restore-on-failure are kept.

**The gate is itself audited.** Each attempt logs its signal vector and the hidden outcome. Reported metrics:
- false-accept (FA) rate, where gate green meets hidden red;
- false-reject rate;
- arbiter precision;
- per-trigger precision;
- visible→hidden conversion per repair round.

**Admission rule** for the cascade or local pass@k: the one-sided 95% upper bound of local FA must be ≤ Flash FA + 3pp, and the absolute FA must be ≤10%.

**Per-component checks** (from the "Specialist Stack" design):

| Component | Check |
|---|---|
| Prefix cache | Identical temperature-0 outputs with cache on and off |
| Speculative decoding | Token-exact output match |
| Resume | Final diff vs. an unkilled control |
| Local routing | Live proof re-run **with the OpenRouter key present** |

**Computer use:** deterministic end-state probes (file hash, AX value, DOM, sqlite/plist, URL). The model proposes the completion conditions and tools check them. A VLM judge is a last resort: it needs a rubric and cited evidence, defaults to FAIL, and never counts toward promotion.

### 2.5 Learning from experience safely

Evidence levels:
- **L0:** model claim.
- **L1:** our gate green. Quarantined, never retrievable.
- **L2:** hidden tests, merged PR, green CI, or no revert in N days.
- **L3:** L2 that survived N later uses.

Each deployment L2 signal has its noise measured against bench hidden tests before it is trusted.

What the system learns, in order of expected value:
1. **Repo cards.** Deterministic, verbatim facts: the working test command, setup, module map, files edited per issue type. **Each fact must re-execute on a fresh clone before it is injected.** Targets: turns-to-first-edit and the 26-turn reading stall.
2. **Guarded macros (T0).** Compiled after ≥2 L2 runs with the same structure. Promoted only if replay on a fresh golden clone with held-out parameters reproduces the post-state. **False-replay rate is a headline metric; one false replay that spreads rejects the arm.** Coding scope: env bootstrap, test recipe, lint/format, branch+PR.
3. **Static routing table.** Per-bucket local hidden-pass rate; fewer doomed local attempts over time.
4. **Abstract lessons** (deferred past week 12). Delta-curated, offered through a `recall()` tool, with helpful/harmful counters, demoted when retrieved and then failed. Expect ≤4pp on single-shot coding; agents often ignore condensed lessons.
5. **Concrete past fixes are never injected.** At most one repo-scoped exemplar.

**Be explicit: code fixes rarely recur.** In coding, compounding shows up as lower $ and seconds per solve, faster first edits and a rising T0 share, not as large solve gains. The big compounding bet is replay of recurring desktop routines.

### 2.6 How coding maps to local computer use

| Coding piece | Computer-use counterpart |
|---|---|
| Hidden tests / generated tests | Deterministic state probes proposed by the model, checked by tools |
| SEARCH/REPLACE text protocol | Structured action ladder: shell/Python → AppleScript/JXA/Shortcuts/App Intents → AX/Playwright-CLI semantic action → grounder + zoom (pixels last) |
| Whole-file context with a size threshold | Pruned AX/DOM element list with indices; screenshot only when the tree is thin |
| Restore-on-failure | Golden VM snapshot per task |
| Cascade to Flash | Escalate with an error summary, never when private data is on screen (MAI-UI device-cloud on Android: +33% relative, 42.7% fewer cloud calls) |
| Operational T0 macros | Guarded replay of chores (the main compounding engine) |
| Credential/git proxy | Critical-point stops (pay, send, delete, credentials); on-screen text treated as untrusted |

Build on Apple's structured surfaces (Shortcuts, App Intents, AX), not against them. It is the cheapest path and the honest answer to "Apple ships this."

---

## 3. Technical tricks ranked by expected gain per cost

| # | Trick | What | Evidence (source) | Expected gain | Cost | Risk |
|---|---|---|---|---|---|---|
| 1 | Cache telemetry + byte-stable prefix | Append-only order: system → tools → repo card → files → goal → feedback. Arbiter and repair reuse the prefix. Log cached_tokens | DeepSeek hit $0.003–0.006/M vs miss $0.15–0.30/M (50x), off-peak −50%; OpenRouter reads 0.1x (pricing docs) | −30–55% $/solve (prediction) | ~1 day | Low; masking can break the cache |
| 2 | Loop / no-progress guards | Tail-repetition kill, edit-sized max_tokens, forced edit after 6 reads, 3x action-hash stop | Length-10 action repeat meant about 89% failure (SWE-smith); BudgetMLAgent triggers −94% cost | p95 output −40%; fewer stalls | ~1 day | Low |
| 3 | Read-only tests + `flag_contradiction` | Reject test diffs; structured exit | ImpossibleBench: cheating 49–54% → 9–12% | Trust; small solve gain | Hours | Low |
| 4 | Regression guard (V2) | Held-out relevant repo tests | TestPrune +8–13% relative (self-reported) | Lower FA rate | 1–3 days + env setup | Suite may not run; log it |
| 5 | Fix providers + host model server | Explicit local endpoint, long-lived server | providers.py routing bug (verified in repo) | Enables everything local | ~1 day | Low |
| 6 | Text action protocol + wrapper-only grammar | GBNF on the SEARCH/REPLACE wrapper, free code bodies | Same scaffold 16.7 / 38.7 / 48.3% across 3 models (ai-muninn); XGrammar-2: 3B model 33% → 78% correct calls | Format failures to about 0 | Low | Strict grammar can hurt reasoning |
| 7 | File-size threshold | Whole file below about 800 lines; outline + 100-line windows above | SWE-agent: full file −5.3pp, 30-line window −3.7pp | Fixes the 26-turn reads | 2–3 days | Conflicts with the adopted whole-file win; ablate |
| 8 | Execution-grounded arbiter | Distinguishing script decides | S*; Kamoi (self-critique unreliable without external feedback) | Higher arbiter precision | Low | Unproven repo-level |
| 9 | Attempt-level cascade | Local → fresh Flash attempt | Darwin empty-patch cascade (self-reported); "Last Step Matters" | −30–60% cloud $ if FA is low | Medium | FA leak; latency on hard tasks |
| 10 | Local pass@k behind admitted gate | k=2–3 batched, first gate-pass wins | pass@5 vs pass@1: 27B 58 vs 31%, 35B 43 vs 25% (SWE-rebench); batch-4 gives 2.5x aggregate throughput | +5–15pp local solve | Low | Only as good as the gate |
| 11 | Operational T0 macros | Guarded zero-LLM replay | Plan caching −50% cost (NeurIPS'25); ASI keep-if-reproduces +11pp over text skills | About 0 tokens per hit | Medium | False replay |
| 12 | Off-peak DeepSeek direct for batch | Cron outside 01–04 and 06–10 UTC on weekdays | Official pricing | −50% on bench $ | Trivial | None |
| 13 | Batched observation masking (multi-turn path only) | Keep last 5, mask every 4 turns | −51–57% cost, but −2–4pp on some models (Complexity Trap) | Multi-turn $ | Low | Solve loss; ablate |
| 14 | Strong-to-weak via artifacts | Teacher writes cards and macros offline | SkillWeaver up to +54% relative for weaker agents (web) | Closes part of the local gap | A few $/repo | Unproven for coding |

---

## 4. Hardware & cost model

**Step 0:** record the owner's MacBook chip and RAM. With 16–24 GB, L0 drops to the 9B class. With ≥36 GB, the 35B-A3B Q4 fits.

| Box | Role | Notes |
|---|---|---|
| **MacBook on AC, clamshell** (stopgap, now) | X4 local baseline, CU probe | Never benchmark on battery. pmset/launchd as a stopgap host |
| **Mac mini M4 Pro, 48–64 GB** (preferred purchase) | Always-on host, model server, 1 macOS VM | 4–5 W idle (Apple). Fits 35B-A3B Q4/Q8 (20/37 GB) plus a 2–9B grounder. Native macOS for CU. About $1.8–2.4k, about $1.6–2.2/day over 3 yrs (check Apple pricing) |
| Mac Studio | Only if 27B dense / Q8 / approved MLX LoRA is needed | |
| DGX Spark | Only if CUDA or large-MoE batch becomes the bottleneck | 22–25 W idle after update (about 5x the mini). MoE good (Qwen3-Coder-30B-A3B 44 tok/s); dense weak (about 9.5 tok/s on 32B). Cannot host a macOS desktop |
| Linux 4090 box | Not recommended | 350–450 W under load loses on the watts term |

**Per-task estimates** (local figures are unmeasured until X4):
- **Flash cloud:** $0.005–0.01 today, with inputs 13–20x larger than outputs (33–65k in, 1.7–5k out). With a stable prefix plus off-peak: about $0.002–0.005 (target).
- **Local L0:** $0 API.
  - Cold 50k-token prefill is about 40–75 s on Max/Spark-class hardware, slower on Pro or base chips. Cached later turns pay only for new tokens.
  - Decode is about 50–100 tok/s on Max-class chips (M4 Max 109 tok/s measured by community), about half that on Pro.
  - Planning estimate: 3–10 min per task, a few Wh, about $0.001 of electricity.
  - Measure Wh with a **smart-plug wall meter**; powermetrics reads SoC rails only.
- **Cascade:** $/task = c_L + (1 − g)·c_C1, where g is the local gate-pass rate. With g = 0.4, cloud $ falls about 40%. False accepts count as failures. The keep rule includes a p90 wall-time limit, so a failed local attempt does not silently inflate the seconds term.
- **T0 replay:** about $0, seconds of wall time.

**Purchase decision rule**, written down after X4:

  (tasks/day × cloud $ saved) + (value of privacy-required tasks) + (unattended-host value) ≥ about $2/day amortized

Buy only if local wins at least one measured bucket, or if the always-on host is needed anyway for M3. A mini sized for computer use satisfies both.

**Daily budget fit:** 50 tasks/day on Flash-only is about $0.25–0.50. Experiments (3 seeds × 28–100 tasks × 2–3 arms) cost a few dollars each when run off-peak.

---

## 5. What NOT to do

- **Don't trust model cards.** Qwen3.6's 77 on SWE-bench Verified falls to 31 on SWE-rebench. OpenAI dropped Verified for contamination and flawed tests. Qwen3.8-27B's OSWorld 84.3 is unverified. The open UI-TARS-1.5-7B scores about 27% on OSWorld, not 42.5% (that was the closed large model). Open small models scored under 5% on macOSWorld v4 (cite v4; v2 was withdrawn).
- **Don't use an absolute local bar** such as "L0 ≥ 40%". It is a coin flip given the predicted ~39%. Use a paired relative rule per bucket.
- **Don't decide on ±1 task at n=28** (95% CI about ±18pp). Seeds are correlated within a task, so analyse with a task-clustered paired bootstrap and McNemar. Label results "directional" until n ≥ 100.
- **Don't build a learned or neural router.** Static tables and kNN capture most of the gain. Consolidate the 8 router files behind an A/B.
- **Don't switch models mid-trajectory**, and don't pass the failed local diff to the cloud (anchoring is untested, and planner rewrites already hurt).
- **Don't store or train on L1 evidence.** It is the experience-store failure again.
- **Don't JSON-tool-call local models**, and don't grammar-constrain code bodies.
- **Don't run local models inside VMs** (no Metal on Linux guests; paravirtual GPU on macOS guests). Don't rely on llama.cpp KV slot restore across restarts; it is reported broken.
- **Don't benchmark on battery**, and don't treat powermetrics as wall power.
- **Don't treat these doubtful claims as facts:**
  - the "2.5–3x contamination factor";
  - the "3.4x MoE vs dense" decode ratio (different chips);
  - Darwin's "56x";
  - Stagehand's 10–20x;
  - OS-Shepherd's 85.6% and "30–60x";
  - Agent KB's 41 → 53 (current version is about +4pp);
  - "overfitting grows each loop";
  - Weaver's exact retention figures;
  - the 10x idle advantage over Spark (it is about 5x).
- **Don't ship computer use in the owner's live session** before critical-point stops and seeded-injection tests pass. OS-Harm: deliberate misuse 48–70%, o4-mini followed injections 20% of the time.
- **Don't let "role slots" grow into per-slot models or agents.** That drifts toward the forbidden multi-agent framing.

---

## 6. Roadmap (12 weeks, sequential for a solo owner; each milestone ends in a live proof)

| Weeks | Milestone | VISION fit | Live proof |
|---|---|---|---|
| 1–2 | **M0 Measure + local endpoint.** Telemetry for cached_tokens, wall-Wh, gate vector and visible/hidden per round. X0 audit. Owner hardware spec. providers fix + host server. Build the routine-task set (≥40 tasks) and source toward 100+ fresh issues | Stage 1: verification, routing | Bench JSON with cache ratio, Wh and tier trail per task; gate confusion matrix vs hidden tests; real issue solved with `provider=local`, $0.00, **re-run with the OpenRouter key present** |
| 2–5 | **M1 Verifier v2 + guards + local baseline.** X1, X2, X3, X4; X5 CU smoke probe | Verification, reflection | Seeded wrong-but-visible-green fix caught by V2 and restored; seeded contradiction returns `flag_contradiction`; looping task stopped, with trigger named in spans.jsonl |
| 5–8 | **M2 Always-on host + cascade.** launchd/pmset, step journal + resume, git/credential proxy with PR hand-off, cascade A/B (X6) with per-bucket admission | Execution, routing | Overnight 10-issue queue with SIGKILL, reboot, network drop and key rotation. Morning report with $/s/Wh per solve, local share and PRs on `awos/*`; zero lost green edits |
| 8–12 | **M3 Compounding v1.** Recurring-repo stream (2–3 repos × 20–30 chronological issues, disjoint practice/eval). Re-executed repo cards. Operational T0 macros. Routing table. 10-task Lume CU smoke set with replay | Memory, capability reuse | Learning curve ($/solve, turns-to-first-edit, T0 share) vs frozen-empty and add-all controls; demotion of a planted bad record; a chore replayed with 0 LLM calls, perturbation trips the guard |
| After 12 | Full CU pilot (20–30 chores, grounder micro-bench, injection tests); lesson playbook; allowlist proxy; T1/T2 isolation tiers | | |
| Gate | **M-decision memo (no build):** local vs cloud economics, gate leak, compounding slope, fine-tuning entry criteria | | |

**Needs owner approval** (all deferred; nothing above depends on them):
- LoRA, SFT or RFT of any model (about $100 per run; check whether DeepSeek V4 outputs may be used for training);
- activation-probe confidence;
- a distilled verifier (needs 20k+ labels);
- per-repo adapters;
- on-policy distillation;
- learned routers.

**Also needed:**
- **Hardware purchase:** owner decision after X4.
- **Licence checks:** Tart (fair-source), MAI-UI, UI-Venus.
- **Doc drift:** this worktree's VISION.md roadmap still lists "Agency + audit GTM" as a phase, which AGENTS.md says is removed positioning. The owner should reconcile it; this plan does not edit VISION.md.

---

## 7. First 6 experiments (pre-registration style)

Shared statistics for all six:
- paired arms, same-day controls, 3 seeds;
- task-clustered paired bootstrap plus McNemar on discordant tasks;
- decisions use one-sided 95% bounds;
- any result on fewer than 100 tasks is labelled **directional**.

**X0 Gate and trigger audit (offline, $0)**
- **Hypothesis:** the empty-diff and gate-red-after-repair triggers have ≥0.9 precision for hidden failure, and repair-round visible gains convert to hidden gains at <70%.
- **Change:** none. Label existing bench and job-series runs and .awos ledgers.
- **Dataset:** all prior real-issue and backupd-style runs.
- **Primary metric:** gate FA/FR rates, per-trigger precision, arbiter precision.
- **Decision:** the escalation rule is set to the triggers with ≥0.9 precision. If conversion is <70%, V2 becomes mandatory before any repair is accepted. **Re-run after M0 telemetry lands**; the first pass is provisional because older logs lack per-round fields.

**X1 Stable prefix + cache telemetry (Flash)**
- **Hypothesis:** follow-up calls hit the cache ≥60% and $/solve falls ≥30% (B1) and ≥50% (B2).
- **Arms:**
  - A: current harness;
  - B1: byte-stable prefix through OpenRouter with no provider.order;
  - B2: B1 on DeepSeek direct, off-peak.
- **Dataset:** 28-issue real-issue series.
- **Primary metric:** $ per hidden-verified solve.
- **Decision:** adopt B1 or B2 if the $ target is met and the paired solve change's lower bound is ≥ −1 task per seed. Correctness check: temperature-0 output identity.

**X2 Loop / no-progress guards**
- **Hypothesis:** p95 output tokens fall ≥40% with no solve loss.
- **Arms:**
  - A: control;
  - B: tail-repetition kill + edit-sized max_tokens + forced edit after 6 reads + stop on 3 repeated action hashes + one penalised retry.
- **Dataset:** real-issue series + backupd-style series, on Flash now and repeated on L0 in X4.
- **Primary metric:** p95 output tokens and count of runs hitting the cap.
- **Decision:** adopt if the token target is met and solve is not worse on paired discordants.

**X3 Verifier v2 (Flash, isolated from local effects)**
- **Hypothesis:** FA falls ≥30% relative, solve does not drop, cost rises ≤15%.
- **Arms:**
  - A: current gate;
  - B: read-only tests + V2 regression guard + distinguishing-script arbiter + `flag_contradiction`.
- **Dataset:** real-issue series expanding toward 100+, plus seeded wrong-fix and contradiction tasks.
- **Primary metric:** gate false-accept rate.
- **Decision:** adopt if all three conditions hold. Log `guard_unavailable` per repo and report it separately.

**X4 Local baseline on the owner's Mac (AC power)**
- **Hypothesis:** on routine tasks, L0 solves ≥60% of the tasks Flash solves, with format failures ≤5% and FA no worse than Flash's.
- **Arms:**
  - F: Flash;
  - L-off and L-on: Qwen3.6-35B-A3B Q4, thinking off and on;
  - optionally Q27: Qwen3.6-27B + MTPLX if RAM allows.
- **Dataset:** the new routine-task set (single-file fixes, renames, lint/type fixes, config and script tweaks, ≥40 tasks), with the 28 hard issues as a stress set.
- **Primary metric:** paired relative solve per bucket.
- **Secondary metrics:** p90 seconds, wall-Wh, malformed-edit rate, local FA.
- **Decision:** enable L0 for the buckets that pass. If none pass on the 35B, try the Q4 → Q6/Q8 ladder once, then fall back to "routing table + computer use only". No model shopping.

**X5 Computer-use smoke probe (weeks 3–5)**
- **Hypothesis:** with structured actions first, a local text model completes ≥60% of 10 AX- or AppleScript-reachable chores that Flash completes, with 0 critical-point violations.
- **Arms:**
  - a: local L0 + structured ladder;
  - b: Flash + structured ladder;
  - c: local + screenshot/grounder only.

  All arms share the same step cap.
- **Dataset:** a new 10-chore set (rename/move files by pattern, export sheet to CSV, calendar event via AppleScript, change a setting, fill a local web form), each with a scripted state probe. Run in a Lume VM or a dedicated user session.
- **Primary metric:** state-probe pass rate.
- **Secondary metrics:** steps, seconds, Wh per success.
- **Decision:** if (a) meets the bar and beats (c), structured-first becomes the CU default and replay work is scheduled in M3. If (a) fails, scope CU to replay of owner-demonstrated routines.

**Next (X6+), after X0, X3 and X4 pass:** the cascade A/B on 100+ issues.
- **Arms:** Flash-only vs local k=1 → Flash vs local k=3 → Flash.
- **Keep rule:** non-inferior solve (−5pp margin, one-sided), the FA admission rule holds, cloud $ falls ≥30%, and p90 wall time is ≤1.6x. Then the X4-style compounding stream.

---

## 8. Sources

**Model evaluation**
- SWE-rebench (independent fresh tasks): https://swe-rebench.com/
- Qwen3.6 cards: https://huggingface.co/Qwen/Qwen3.6-35B-A3B · https://huggingface.co/Qwen/Qwen3.6-27B · Qwen3.8: https://huggingface.co/Qwen/Qwen3.8-27B
- Text scaffold across models: https://ai-muninn.com/en/blog/swe-bench-scaffold-transfers-three-models · failure modes: https://ai-muninn.com/en/blog/swe-bench-qwen36-failure-modes

**Local performance and runtimes**
- MLX throughput: https://omlx.ai/benchmarks/fqabp6rx · MTPLX: https://modelfit.io/blog/speculative-decoding-mac-llm/
- DGX Spark: https://github.com/ggml-org/llama.cpp/discussions/16578

**Pricing**
- DeepSeek: https://api-docs.deepseek.com/quick_start/pricing · OpenRouter caching: https://openrouter.ai/docs/features/prompt-caching

**Verifiers and tests**
- Large Language Monkeys: https://arxiv.org/abs/2407.21787 · Otter: https://arxiv.org/html/2502.05368v2 · R2E-Gym: https://arxiv.org/abs/2504.07164
- TestPrune: https://arxiv.org/abs/2510.18270 · ImpossibleBench: https://arxiv.org/html/2510.20270v1 · Kamoi: https://aclanthology.org/2024.tacl-1.78 · S*: https://arxiv.org/abs/2502.14382

**Routing and cascades**
- Code cascades: https://arxiv.org/abs/2405.15842 · BudgetMLAgent: https://arxiv.org/abs/2411.07464 · TwinRouterBench: https://arxiv.org/abs/2605.18859 · Last Step Matters: https://arxiv.org/abs/2608.29685

**Memory and skills**
- Memory gating: https://arxiv.org/abs/2505.16067 · SWE-Exp: https://arxiv.org/abs/2507.23361 · condensed-experience neglect: https://arxiv.org/abs/2601.22436
- ASI: https://arxiv.org/abs/2504.06821 · AWM: https://arxiv.org/abs/2409.07429 · Plan caching: https://arxiv.org/abs/2506.14852

**Context and decoding**
- SWE-agent ACI: https://arxiv.org/abs/2405.15793 · Complexity Trap: https://arxiv.org/abs/2508.21433 · XGrammar-2: https://arxiv.org/abs/2601.04426

**Computer use and safety**
- MAI-UI: https://arxiv.org/html/2512.22047 · UFO2: https://arxiv.org/pdf/2504.14603 · CoAct-1: https://arxiv.org/pdf/2508.03923
- macOSWorld: https://arxiv.org/abs/2506.04135 · OS-Harm: https://arxiv.org/html/2506.14866v2 · Universal Verifier: https://arxiv.org/abs/2604.06240

**Fine-tuning (deferred)**
- SWE-smith: https://arxiv.org/abs/2504.21798 · SWE-Gym: https://arxiv.org/abs/2412.21139 · Nebius RFT: https://nebius.com/blog/posts/openhands-trajectories-with-qwen3-coder-480b · Tinker pricing: https://www.beam.cloud/blog/tinker-model-pricing

**Host and sandboxing**
- Anthropic sandboxing: https://www.anthropic.com/engineering/claude-code-sandboxing · Lume: https://cua.ai/docs/lume · macOS VM cap: https://eclecticlight.co/2022/08/04/virtualisation-on-apple-silicon-macs-8-how-apple-limits-vms/ · Mac mini power: https://support.apple.com/103253

**Repo files referenced**
- `/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/bench-run/scaffold/agent/providers.py`
- `/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/bench-run/scaffold/agent/one_shot.py`
- `/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/bench-run/scaffold/agent/job_host.py`
- `/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/bench-run/scaffold/agent/escalation_engine.py`
- `/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/bench-run/.awos/cache_stats.jsonl`
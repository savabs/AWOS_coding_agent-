# Checkpoint — Full Session Handoff (2026-06-25)

> **Purpose:** Complete context for a new chat session continuing AWOS Stage 1 work.  
> **Read order:** This file → `docs/benchmarks/BASELINE.md` → `tasks/active/*.md`

---

## 1. Identity & discipline (do not contradict)

- **Canonical philosophy:** `VISION.md` v2.8 only. Do not restate identity elsewhere.
- **Facts owner:** `memories/repo/project_structure.md`
- **Process:** Research → Spec → Task → Implement → **Live proof** → Checkpoint (`AWOS.md` §2.6)
- **Live proof required** for orchestrator, sessions, CLI, verifier, routing, safety — not unit tests alone.
- **Deferred:** RL training on **model weights** (GRPO/PPO on LLM). **Not deferred:** harness-level verifiable rewards (LinUCB, `compute_reward`, evolvers).
- **Stage 1 now:** One excellent coding worker. Coding = benchmark only; kernel = product.

---

## 2. Where we are (executive summary)

| Area | Status |
|------|--------|
| Phase A — Long runs + `plan_file` | ✅ Done |
| Phase B — PEI benchmark vs raw API | ✅ Done (3 cases) |
| Phase C — `awos worker` CLI | ✅ Done |
| Phase C+ — Cache hit telemetry | ✅ Done |
| Phase D — External repo (clawcode) | ✅ Ran (14/14 mechanical) |
| Baseline benchmark v1 | ✅ Frozen 2026-06-25 |
| Outcome Judge / Value Proof Engine | ❌ Not built (top priority) |
| Subscription-ready proof | ❌ Not yet (semantic audit, harder cases, multi-hour) |

**System rating:** ~7.5/10 early product (`docs/assessment/system_rating_2026-06-24.md`)

---

## 3. User intent arc (this session)

1. **"C first then other"** — Cache telemetry first, then large-scale proof.
2. **Phase D on clawcode** — Real external repo, background mission.
3. **Validity concern** — "Passing results doesn't mean shit" → honest audit; README task false-green.
4. **Value proof system** — How to verify AWOS beats alternatives; internet research (RLVR, SWE-bench, SICA, ReVeal, reward hacking).
5. **Why RLVR deferred** — Model RLVR vs harness verifiable rewards clarified.
6. **Baseline benchmark** — Freeze current metrics for before/after comparison.
7. **This checkpoint** — Full handoff for new chat.

---

## 4. Work completed in detail

### 4.1 Cache hit telemetry (Phase C+)

**Files:**
- `scaffold/agent/cache_telemetry.py` — `CacheTelemetryStore`, `CacheEvent`, `extract_cache_stats_anthropic()`, `extract_cache_stats_openai()`
- `scaffold/agent/worker.py` — telemetry after Anthropic, DeepSeek, OpenAI calls
- `scaffold/agent/planner.py` — telemetry after Sonnet planning
- `scaffold/agent/critic_engine.py` — telemetry after Haiku critique
- `scaffold/agent/self_learning_metrics.py` — Cache Performance section in `awos stats`
- `tests/test_cache_telemetry.py` — 5 unit tests pass
- `tests/smoke_test_cache_telemetry.py` — optional live API smoke
- `docs/research/cache_telemetry.md`, `docs/specs/cache_telemetry_spec.md`
- `tasks/active/cache_telemetry.md` — mostly complete; live proof on long mission partial
- `docs/memory/checkpoint_2026-06-24_cache_telemetry.md`

**Storage:** `.awos/cache_stats.jsonl` (append-only)

**Observed (post clawcode runs):** ~1.7% hit rate, 1,664 cached / 97,849 fresh tokens. Low because worker uses DeepSeek (limited cache metadata vs Anthropic). Planner uses `cache_control: ephemeral` on system block.

**Gap:** 95%+ cache claim not proven on production workload.

---

### 4.2 Phase D — clawcode external repo

**CLI changes (`awos.py`):**
- `awos worker start --root <path>`
- `awos run --root <path>`
- `awos mission start --clawcode` / `status --clawcode` / `guide --clawcode`
- `_mission_codebase_root(cfg)` reads `codebase_root` from mission JSON
- `_load_mission_config(long=..., clawcode=...)`

**Mission config:**
- `docs/missions/stage1_phase_d_clawcode.json` — target `/home/becmachlean/2024/projects/clawcode`
- `docs/missions/stage1_phase_d_clawcode.plan.json` — 14 low-risk docstring/README tasks
- `scripts/demo_phase_d_clawcode.sh`
- `docs/phase_d_clawcode_proof.md`
- `tasks/active/phase_d_clawcode.md`
- `tests/test_mission_plan_loader.py` — `test_clawcode_mission_config_has_plan_and_root`

**Live run (2026-06-25):**
- Command: `python3 awos.py mission start --clawcode`
- Session: `rs_34d1a845a6b0` — **completed**
- Sandbox: `/home/becmachlean/2024/projects/clawcode/.awos/worktrees/34d1a845a6b0`
- Results: **14/14 tasks**, 0 failures, **40.5s**, **$0.0070**
- Git commit in worktree: `8492dc0` — 13 files, 23 insertions (module docstrings)
- pytest in worktree: **22/22 passed**
- `docs/memory/checkpoint_2026-06-25_phase_d_clawcode.md`

**Critical honesty — semantic audit:**
- Task 14 (README Development section): logged **VERIFIED** but **README unchanged** vs main → **false-green**
- Estimated semantic success: **~13/14**, not 100%
- Mission tasks were trivial docstrings — proves **plumbing**, not hard coding value
- Wall time 40.5s ≠ subscription bar 30–120 min

---

### 4.3 Value proof / verified learning loop (design only — not implemented)

**User request:** System to prove "AWOS does valuable coding work better than alternatives" + closed self-improving loop.

**Research synthesized from:**
- RLVR / DeepSeek-R1 — verifiable rewards on math/code
- SICA — self-improving coding agent (17%→53% SWE-Bench by editing scaffold)
- ReVeal — generation-verification co-evolution
- VPR — verifiable process rewards (turn-level)
- VeRPO — calibrated partial success rewards
- SWE-bench Verified — FAIL_TO_PASS + PASS_TO_PASS, binary, Docker
- EvilGenie / RewardHackingAgents / BenchJack — reward hacking, test editing, evaluator tampering
- mini-SWE-agent — fair head-to-head harness

**Proposed architecture (not coded):**
```
Benchmark Corpus → Orchestrator → Worker → Mechanical Verifier
                              → Outcome Judge (goal assertions)
                              → Anti-Hack Guard (held-out tests, no test edits)
                              → Reward Composer → LinUCB / Evolvers / SkillLibrary
                              → Head-to-head report (AWOS vs raw vs raw3shot)
```

**Key files to create next:**
- `scaffold/agent/outcome_judge.py`
- `scripts/value_proof_run.py` or extend `benchmark_vs_raw_api.py`
- `tests/fixtures/value_proof/` corpus
- `docs/research/verified_learning_loop.md` (was discussed, not written)

**Rule:** Learn only from `semantic_pass ∧ tests_pass`, never mechanical pass alone.

---

### 4.4 RLVR deferred — clarification documented in chat

- **Deferred:** Training LLM weights with GRPO/PPO (needs GPU cluster, trusted reward, Stage 1 incomplete).
- **Active now:** Harness RLVR — `compute_reward()`, LinUCB, PromptEvolver, ScaffoldEvolver, SkillLibrary.
- **MASTER_PLAN.md:** Not deferred — MCTS, PRM, symbolic oracles.
- Model RLVR maybe never needed if harness wins on same commodity models.

---

### 4.5 Baseline benchmark v1 (frozen)

**Purpose:** Compare future harness versions against current snapshot.

**Files:**
- `docs/benchmarks/baseline_v1_2026-06-25.json` — **DO NOT EDIT** (machine-readable)
- `docs/benchmarks/BASELINE.md` — human summary
- `docs/benchmarks/README.md`
- `docs/research/baseline_benchmark.md`
- `scripts/run_baseline_benchmark.sh`
- `scripts/compare_baseline.py`
- `tests/test_compare_baseline.py`
- `tasks/active/baseline_benchmark.md`

**Git at freeze:** `b644839`

**Baseline v1 metrics:**

| Suite | Key numbers |
|-------|-------------|
| PEI (3 bug cases) | Raw 67% pass, AWOS 100%; raw $0.00064, AWOS $0.00102; AWOS wins `02_wrong_logic` |
| Long mission (AWOS) | `rs_b2667c2e8eb7`, 8/8, 165s, $0.0158, task 4 decomposed |
| clawcode Phase D | `rs_34d1a845a6b0`, 14/14, 40.5s, $0.007, pytest 22/22 |
| Cache | 1.7% hit rate |
| Unit tests | 776 pass, 1 flaky (2026-06-08) |
| System rating | 7.5/10 |

**Known gaps in v1:** No Outcome Judge, 3 PEI cases, no raw_3shot arm, no multi-hour proof, semantic false-greens possible.

**Freeze v2 when:** Outcome Judge + Value Proof corpus ship.

---

## 5. Prior session work (still relevant)

### Stagnation breaker
- `scaffold/agent/orchestrator.py` — `_failure_signature`, `_check_stagnation`, auto-pause `STAGNATION`
- `runtime_session.py` — `pause_reason` field
- `scripts/demo_stagnation_breaker.sh`, `docs/stagnation_breaker_proof.md`
- Live proof demo **not fully run** in this session (integration test skipped slow VectorMemory)

### `awos worker` CLI (Phase C)
- `awos worker start|status|resume|diff|cancel`
- Wraps orchestrator + sessions; `start` now supports `--root`
- `docs/memory/checkpoint_2026-06-24_worker_cli.md`

### PEI benchmark (Phase B)
- `scripts/benchmark_vs_raw_api.py`
- `tests/fixtures/benchmark/` — 3 cases: `01_off_by_one`, `02_wrong_logic`, `03_simple_add`
- Artifact: `.awos/benchmarks/benchmark_vs_raw_20260623_061847.json`
- `docs/product/pei_proof.md`

### Long mission (AWOS self)
- `docs/missions/stage1_long_workload.plan.json` — 8 tasks
- `awos mission start --long`
- Session `rs_b2667c2e8eb7` completed ~165s

---

## 6. Key architecture (for next agent)

```
Goal → Orchestrator
  → LinUCB / EscalationEngine (model route)
  → Planner (or plan_file bypass)
  → Worker (DeepSeek primary, Anthropic/OpenAI fallback)
  → Verifier (syntax, SEARCH/REPLACE, pytest sometimes)
  → compute_reward() → .awos/reward_store.jsonl
  → Learn: PromptEvolver, SkillLibrary, ScaffoldEvolver, LinUCB weights
```

**North star:** PEI = (Quality × Speed) / Cost  
**Subscription bar:** `docs/product/stage1_subscription_worker_guideline.md`

**Verifier gap:** Mechanical pass ≠ task goal met. No semantic Outcome Judge yet.

---

## 7. Important file map

| Path | Purpose |
|------|---------|
| `VISION.md` | Sole identity |
| `AWOS.md` | Implementation protocol + live proof §2.6 |
| `AGENTS.md` | Cold-start for agents |
| `memories/repo/project_structure.md` | Metrics, active work |
| `awos.py` | CLI entry |
| `scaffold/agent/orchestrator.py` | Main loop |
| `scaffold/agent/reward_store.py` | `compute_reward()` |
| `scaffold/agent/ml_router.py` | LinUCB |
| `scaffold/agent/verifier.py` | Mechanical gate |
| `scaffold/agent/cache_telemetry.py` | Cache stats |
| `docs/benchmarks/BASELINE.md` | Frozen baseline v1 |
| `docs/missions/stage1_phase_d_clawcode.json` | External repo mission |
| `tasks/active/` | Active task trackers |

---

## 8. Known bugs / friction (unfixed)

1. **`CriticEngine.critique()`** — unexpected `codebase_context` kwarg mismatch (logged in long mission)
2. **README false-green** — verifier passes without goal assertion
3. **Planner collapse** — long prose goals merge tasks; use `plan_file` workaround
4. **Stagnation demo** — unit tests pass; full integration test skipped (slow VectorMemory/tensorflow)
5. **Full pytest suite** — may have collection errors in some envs (776 tests per project_structure; full run not green in one attempt this session)
6. **Cache hit rate** — far below 95% on DeepSeek-heavy workloads

---

## 9. Environment notes

- API keys in `.env`: `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`
- Planner model: `claude-sonnet-4-6`
- Worker default: DeepSeek (`deepseek-chat` / V4 Flash label)
- Mission env: `AWOS_CHEAP_ONLY=true`, `AWOS_USE_WORKTREE=true`, `AWOS_RUNTIME_SESSION=true`
- clawcode path: `/home/becmachlean/2024/projects/clawcode`

---

## 10. Commands cheat sheet

```bash
# Product surface
awos worker start "goal"
awos worker start --root /path/to/repo "goal"
awos worker status | resume | diff | cancel

# Missions
awos mission start --long          # 8 tasks, AWOS repo
awos mission start --clawcode      # 14 tasks, clawcode
awos mission status --clawcode

# Benchmarks
python3 scripts/benchmark_vs_raw_api.py
./scripts/run_baseline_benchmark.sh
python3 scripts/compare_baseline.py

# Observability
awos stats                         # includes cache section

# Demos
./scripts/demo_stagnation_breaker.sh
./scripts/demo_phase_d_clawcode.sh
```

---

## 11. Active task files (`tasks/active/`)

| File | Status |
|------|--------|
| `cache_telemetry.md` | Mostly done; long-mission cache proof partial |
| `phase_d_clawcode.md` | Live proof done; checkpoint done |
| `baseline_benchmark.md` | v1 frozen; live re-run with API keys optional |
| `awos_worker_cli.md` | Complete |
| `stagnation_breaker.md` | Implementation done; live proof pending |
| `stage1_subscription_worker.md` | Phase D partial |

---

## 12. Recommended next steps (priority order)

1. **Outcome Judge** — goal assertions per task; catch README-style false greens  
   Research → Spec → Task → `outcome_judge.py` → wire post-verifier

2. **Anti-Hack Guard** — penalize `tests/` edits; held-out tests for benchmark cases

3. **Reward Composer** — tie `compute_reward()` to `semantic_pass`, not mechanical pass

4. **Expand PEI / Value Proof corpus** — 10+ cases, tiers, `raw_3shot` baseline arm

5. **Freeze baseline v2** after Outcome Judge ships

6. **Hard clawcode mission** — bug fixes / real tests, not docstrings

7. **Fix CriticEngine kwargs bug**

8. **Run stagnation breaker live proof** — `./scripts/demo_stagnation_breaker.sh`

9. **Multi-hour mission** with pause/resume on external repo

10. **Model RLVR** — still deferred; do not start without trusted corpus

---

## 13. What NOT to do

- Do not claim subscription-ready from clawcode 14/14 without semantic audit
- Do not edit `baseline_v1_2026-06-25.json` — create v2 instead
- Do not skip live proof for kernel/CLI/safety features
- Do not train foundation model / full RLVR on weights yet
- Do not change `VISION.md` without explicit user request
- Do not commit unless user asks

---

## 14. Prior checkpoints (reference)

- `docs/memory/checkpoint_2026-06-24_cache_telemetry.md`
- `docs/memory/checkpoint_2026-06-25_phase_d_clawcode.md`
- `docs/memory/checkpoint_2026-06-24_worker_cli.md`
- `docs/memory/checkpoint_2026-06-23_long_mission_benchmark.md`
- `docs/memory/checkpoint_2026-06-23_stagnation_breaker.md`
- `docs/assessment/system_rating_2026-06-24.md`

---

## 15. Transcript

Full conversation: agent transcript `392c183d-9071-40ba-b244-48822f572ede` (Cursor agent transcripts folder).

---

**Handoff complete.** Next session: read this file + `docs/benchmarks/BASELINE.md`, then start Outcome Judge research/spec.

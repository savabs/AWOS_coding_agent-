# Pre-registration: AWOS vs Aider on the same model (Experiment 1, track B2)

Status: **pre-registered 2026-10-11, not yet run.** Committed before any model call.
The run is gated on two inputs from other tracks. Both were missing when this was written (see "Gate status").

## Question

Does AWOS add value over a minimal harness when both run on the **same model**?
Earlier comparisons (AWOS 64% vs Aider 61%, n.s.) ran on different model setups. This one pins both arms to direct Anthropic `claude-haiku-5-5`.

## Arms

| Arm | job_series key | Harness | Model |
|---|---|---|---|
| base | `aider` | Aider (`scripts/harness_aider.py`, Anthropic provider, `--cache-prompts`; track B1 commits 4f622a0, 3e352cc) | claude-haiku-5-5 |
| candidate | `off` | AWOS orchestrator, notebook off (prompt caching on, goal check off when the repo has tests) | claude-haiku-5-5 |

Pin: `AWOS_SERIES_MODEL=claude-haiku-5-5`. OpenRouter is off. The driver refuses to start with any other model.

Cost sources: AWOS bills from its own ledger. Aider's cost comes from `token_price_table` (its exact tokens at the same flat Haiku 5.5 prices AWOS uses; see `docs/specs/aider_haiku55_arm.md`). Both are list-price estimates on the same table. `billed_usd` is used when present.

## Issues

Every valid issue in Track A's fresh set, `~/.awos-harness/fresh_series`. Valid means:
- the directory holds a `READY` file, written by Track A after validation;
- the issue has a `jobs/` dir;
- job_series preflight passes (an issue that fails preflight is dropped before any spend, and the drop is reported).

Minimum: **15 valid issues**. With fewer, the experiment is reported as blocked and not run.
The fresh set is used because the 73-issue real set is at ceiling on Haiku 5.5 (72/73; `docs/memory/runs/haiku55_baseline_2026-10-11.md`).

## Protocol

- Driver: `scripts/ablation_awos_vs_aider.py run --series-root ~/.awos-harness/fresh_series --out ~/.awos-harness/b2_ablation --cap 3.0`. It runs under `caffeinate -i` with `PYTHONUNBUFFERED=1`.
- **Interleaved per issue.** Each issue is one `job_series run` with both arms back to back, and job_series checks that both arms got identical inputs. Arm order alternates by sorted issue index: even index runs `off` first, odd index runs `aider` first.
- **Repeats.** Repeat 1 covers all issues. A second repeat (`--repeat 2`) runs only if the projected cost of the full second pass fits under the cap, judged from repeat 1's measured mean per issue. Otherwise it runs as far as the cap allows. Any partial repeat 2 is reported as such and still analysed: the stats rule pools runs per task.
- **Resumable.** A (repeat, issue) that already has a result row for both arms is skipped. Infrastructure failures follow job_series' existing retry/invalid rules. An invalid run drops that task in both arms (paired exclusion).
- **Spend cap: $3.00 total.** Before each issue the driver stops if spent + mean per-issue spend > $3.00. Per-job caps stay as each task.json sets them.

## Analysis (fixed now)

`python scripts/ablation_awos_vs_aider.py combine --out ~/.awos-harness/b2_ablation`, then
`python scripts/stats_audit.py ~/.awos-harness/b2_ablation/combined.json --a aider --b off --cost-budget 0.5`.

**Primary: task-level solve.** Hidden tests plus visible tests decide, under the T1 rule (`docs/specs/stats_decision_rule.md`). Δ = AWOS − Aider.
- **KEEP** ("AWOS adds value"): P(Δ>0) ≥ 0.95 **and** task-level exact McNemar p ≤ 0.05 **and** AWOS $/run ≤ Aider $/run × 1.5.
- **REJECT** ("AWOS does not add solve value over Aider"): P(Δ>0) ≤ 0.20.
- **INCONCLUSIVE**: anything else. A solve gain over the cost budget is INCONCLUSIVE with reason "over budget".

**Primary: cost.** Mean $/valid run per arm over the included tasks, and the ratio.

**Secondary** (descriptive, no decision): $ per solved task per arm; mean minutes per run per arm; per-issue disagreement list (issues where exactly one arm solved, or rates differ); invalid-run count per arm.

**Power.** From `stats_audit.py --power`. The McNemar leg's minimum detectable effect at 80% power is >60 pp for 15 tasks × 1 repeat, 30 pp for 15 × 2, 27 pp for 25 × 1, 22 pp for 25 × 2 and 16 pp for 40 × 2. A null or INCONCLUSIVE result at these sizes rules out only large effects, and the write-up must say so.

**No peeking rule.** No intermediate stats are computed for a stop/continue decision. Only the cap stops the run early.

## Gate status at registration (2026-10-11)

- Fresh set: `~/.awos-harness/fresh_series` holds 1 built issue (`fresh_sqlglot_8335`) and **no READY file**. Track A reported blocked (session pinned to another worktree). The driver confirms: `BLOCKED: no READY file ... (exit 4)`.
- Aider arm: track B1's branch `worktree-wf_0d963615-8f0-2` (merged here at 241aeae) has the Anthropic wiring and its tests (`tests/test_harness_aider_anthropic.py` passes). Its live proof is **partial**: 1 of 4 issues finished (`real_attrs_1513` solved, $0.0084), and the other 3 never completed.

**Result: blocked. Nothing run, $0 spent.** To run: Track A writes READY with ≥15 valid issues, and the B1 live proof is ideally completed (pass criteria in `docs/specs/aider_haiku55_arm.md`). Then run the commands above unchanged.

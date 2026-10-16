# Harness-lift ladder: how much does the AWOS harness add over the raw model?

Pre-registered 2026-10-13, before any ladder run. Owner question: "Is it the harness doing the work, or just the model? If the model is that good, the harness matters little."

## Why

B3 (`docs/memory/runs/b3_full_awos_vs_aider_2026-10-13.md`) held the model fixed (Haiku 5.5) and found AWOS ahead of Aider: 83% vs 67% of runs, McNemar 14–4, p = 0.031, P = 0.91. That compares two harnesses. It does not say how much any harness adds over the model on its own.

This ladder measures the lift over a **minimal harness**: mini-swe-agent (a bash-only loop of about 100 lines), arm `mini`. It runs at two model strengths. The prior from the literature (`docs/expedition/01_what-works.md`) is that harness structure matters little for frontier models and a lot (14–22 pp) for small models.

## Arms and design

| Rung | Haiku 5.5 (strong) | Qwen3.5-9B local (weak, $0) |
|---|---|---|
| Minimal harness | `mini` | `mini-local` |
| Standard harness | Aider, B3 data (descriptive only) | — |
| AWOS | `off` | `off` with `AWOS_PROVIDER=local` |

- **Haiku pair:** `off` vs `mini`, interleaved per issue, 1 repeat, on all valid issues in `~/.awos-harness/fresh_series` (40). Hard cap **$5**.
- **Local pair:** `off`-local vs `mini-local`, interleaved, 1 repeat, on **15 issues fixed now**: the first 15 of the sorted fresh-set names whose reference fix touches a single source file. Single-file is chosen so a 9B model has a fair chance; the list is written to the run's out dir before starting. One llama-server, `AWOS_LOCAL_SLOTS=1`, on AC power if possible, with no other experiment running. $0.
- **Order:** both pairs run after the B3 extension finishes, never at the same time as it.
- **Shared settings:** same goals, repos, hidden tests, step and cost limits per job, and INVALID rules (provider or empty-response failures are invalid, never "unsolved").

## Hypotheses and decision rule (T1, `docs/specs/stats_decision_rule.md`)

| | Hypothesis | Test |
|---|---|---|
| **H1** | AWOS beats the minimal harness on Haiku | `stats_audit.py --a mini --b off --cost-budget 0.5` |
| **H2** | AWOS beats the minimal harness on the local model, by **≥15 pp** | `--a mini-local --b off --cost-budget 0.5` (cost $0 both); KEEP also requires an observed Δ ≥ 15 pp |
| **H3** (descriptive) | AWOS on the free local model compared with the minimal harness on Haiku | Solve rate and $ per solved, reported side by side, no verdict |

Each hypothesis is judged KEEP / INCONCLUSIVE / REJECT under the T1 rule.

## What the outcomes mean

- **H2 KEEP:** the harness is what makes local, $0 routine work possible. That is AWOS's core value claim, and it goes on the site.
- **H1 KEEP but H2 not:** the harness adds value on strong cloud models only.
- **Neither:** for today's models the harness adds little over a minimal loop. AWOS's value must then come from the product layer (background queue, verification, review UX, cost), not from solve rate. We would say so publicly.

## Known limitations

- One repeat per pair, so the MDE is roughly 20 pp for Haiku and much larger for the 15 local issues.
- The goals in the fresh set were written to match the hidden tests.
- mini-swe-agent's prompts are tuned for SWE-bench-style tasks. That is the point of a minimal baseline, but it may under- or over-state "no harness".

## Amendment 1 (2026-10-14, before any data counted)

A first H1 launch was paused after 5 of 40 issues because each issue took ~8 min, of which AWOS
used ~2. Those 5 issues are archived at `~/.awos-harness/ladder_haiku_paused_slow` and are **not
counted**; H1 restarts fresh, so every counted row comes from one AWOS build.

- **AWOS build changes (speed, not scoring):** the acceptance check now runs before the full suite,
  test mapping includes tests that import the changed module, the bounded repair stops as soon as
  the acceptance tests pass, and the full suite still gates success once. The exact commit is
  recorded in the result note.
- **Runner changes (no effect on scoring):** preflight reuses the cached `fresh_series/_tools/validate.json`
  when it is newer than the series files, the backend check is skipped if it passed in the last 5 min,
  and the driver may run 2 issues at once (`--parallel 2`). Within an issue the arms stay sequential in
  the pre-registered alternating order. Concurrency adds CPU contention that affects both arms; wall
  time is reported descriptively only, and the hypotheses are judged on solve rate and $ as before.
- **Rejected:** pytest-xdist for the grader. The astroid suite fails under `-n 4` / `-n 8` (19 and 8
  spurious failures), so the grader stays serial.

## Amendment 2 (2026-10-15, before the re-run): H1b, in-sample re-test after engine fixes

H1 was judged **REJECT** (AWOS 33/40 vs mini 39/40; `docs/memory/runs/ladder_h1_haiku_2026-10-15.md`).
Three engine fixes target the observed early-stop failures: a full-suite success gate, a bounded
verify loop when the acceptance gate is inactive, and extra repair rounds while kept acceptance tests
still fail and progress is being made.

- **H1b** re-runs both arms fresh (`off`, `mini`) on the same 40 issues, same model, same runner
  (GitHub Actions, 8 shards, $5 cap), and is judged by the same T1 rule (`--a mini --b off --cost-budget 0.5`).
- **This is in-sample.** The fixes were designed after reading these 40 issues' failure logs, so a
  KEEP on H1b is weaker evidence than a KEEP on unseen issues. It would be reported as
  "the fixes close the gap on the issues they were aimed at", and **confirmation requires a held-out
  set** of fresh issues not used for any design decision. H1 stays REJECT on the record whatever H1b shows.
- The engine commit is recorded in the result note; mini is unchanged (2.4.6).

## Amendment 3 (2026-10-15, before any held-out data): H1c, held-out confirmation

- **Set:** `heldout_series`, 30 issues from PRs merged 2026-09-11 to 2026-10-14 in 11 repos (≤ 4 per
  repo), none used for any AWOS design decision, validated with the same `job_series.py validate`.
  Built separately from the fresh set; one hidden test (markdown_1631, a performance bug) carries a
  5 s `pytest.mark.timeout`, recorded in its `SOURCE.md`.
- **H1c:** AWOS (engine with the amendment-2 fixes) vs mini-swe-agent 2.4.6, Haiku 5.5, 1 repeat,
  GitHub Actions 8 shards, $5 cap, judged by the same T1 rule (`--a mini --b off --cost-budget 0.5`).
- **H1c is the confirmatory test** for the engine fixes; H1b (in-sample) cannot confirm on its own.
  With n = 30 the MDE is large (~20+ pp), so INCONCLUSIVE is a likely outcome and would be reported as such.
- Run after H1b finishes, on the same engine commit plus the CI-only SERIES_DIR change (48b78fc).
- **Addendum (2026-10-15, still before any held-out data):** H1c runs on engine commit
  **0d4fe24**, which adds the Seatbelt loopback fix found in H1b (localhost sockets were denied, so
  the full-suite gate rejected correct fixes). No other engine change.

## Amendment 4 (2026-10-15, before any data): H1d, the `loop` executor

After H1/H1b (REJECT) and H1c (INCONCLUSIVE at a ceiling, AWOS ×2.2 cost), AWOS gets a mini-style
executor (`AWOS_EXECUTOR=loop`): one open bash loop until the model submits, inside AWOS's sandbox and
cost tracking, with AWOS's full-suite gate after submit that feeds failures back (it never stops the loop early).

- **H1d:** arm `loop` vs `mini` on the same 40 fresh issues, both arms run fresh, Haiku 5.5, GitHub Actions
  8 shards, $5 cap, T1 rule (`--a mini --b loop --cost-budget 0.5`).
- **Default switch rule (decided now):** `AWOS_EXECUTOR` defaults to `loop` only if all three hold:
  1. H1d is not REJECT;
  2. `loop` solves ≥ 34/40 (the pipeline's best on this set);
  3. `loop` costs ≤ 1.5× `mini` per run.
  Otherwise the default stays `pipeline` and the result is reported as is.
- **Bias note:** the 40 issues were read for the pipeline's failure analysis, but `loop` was not tuned on them; its
  design is mini-swe-agent's. A further held-out check needs a harder held-out set (H1c hit a ceiling).

## Amendment 5 (2026-10-16, before any local data): H2 runs on the `loop` engine, plus a grammar ablation

`loop` is now AWOS's default engine (amendment 4 / H1d). H2 tests it on the local model with the local-reliability
work from `docs/research/rea_synthesis_2026-10-16.md` (B1: grammar-constrained loop replies; B2: byte-stable
prefix and cache telemetry; B3–B5 runtime switches if merged; all recorded in the result note).

- **H2:** AWOS arm `loop` with `AWOS_PROVIDER=local` vs `mini-local`, on the same 15 single-file issues fixed in the
  original H2 design (first 15 sorted fresh-set names whose reference fix touches one source file; list written to
  the out dir first). One llama-server (`AWOS_LOCAL_SLOTS=1`), on AC power, nothing else running, 1 repeat, $0.
  Decision as pre-registered: `--a mini-local --b loop --cost-budget 0.5`; KEEP also requires an observed Δ ≥ 15 pp.
- **H2-g (secondary, ablation of B1):** `loop`-local with `AWOS_LOOP_GRAMMAR=1` vs `=0` on the same 15 issues, 1 repeat.
  Primary metric: solve rate (T1 rule, reported descriptively at n = 15). Secondary: valid-action rate per reply and
  format-error nudges per run; expect the grammar to drive nudges to about 0.
- **Descriptive:** seconds per step, cached-prefix fraction, tokens/s and the power state per run.
- No run starts until the owner approves it (as of 2026-10-16 the owner has paused runs).

## Amendment 6 (2026-10-16, before any local data): the four-arm local run

`docs/STRATEGY.md` makes "AWOS makes a local model better" the first proof. This amendment fixes the local run
before any of its data exists. It supersedes the run layout of amendment 5 (H2 and H2-g stay as defined there) and
adds one exploratory comparison.

- **Model and server:** Qwen3.5-9B Q4_K_M via `scripts/local_model.sh start`, one llama-server, `AWOS_LOCAL_SLOTS=1`
  (the driver refuses a server started with more slots), on AC power, nothing else running. $0.
- **Arms** (all `scripts/job_series.py` arms; the three AWOS ones set `AWOS_PROVIDER=local` and pin every model knob
  to `local/<AWOS_LOCAL_MODEL>` themselves, so no cloud model is reachable; each arm's env is in its input manifest):

  | arm | what it is |
  |---|---|
  | `loop-local` | AWOS `loop` engine (the default), reply grammar on (`AWOS_LOOP_GRAMMAR=1`) |
  | `mini-local` | mini-swe-agent 2.4.6 on the same server (unchanged) |
  | `loop-local-nogrammar` | `loop-local` with `AWOS_LOOP_GRAMMAR=0` |
  | `pipeline-local` | AWOS planner pipeline (the `off` arm's executor, `agent_loop`) |

- **Issues:** the same 15 as amendment 5: the first 15 sorted fresh-set names whose reference fix touches exactly one
  source file (test files and bytecode don't count). Implemented as `scripts/local_ladder.py select`, and written to
  `<out>/issues.txt` before the first job. On the current fresh set this is: astroid_2977, astroid_3235,
  astroid_3286, astroid_3348, boltons_441, boltons_513, click_3777, click_3818, more-itertools_1248,
  more-itertools_1261, pathspec_157, pyflakes_868, pyparsing_652, pyparsing_653, sqlglot_8042 (all `fresh_`-prefixed).
- **Order and resume:** `scripts/local_ladder.py run` runs all four arms of an issue back to back, one job_series
  call per issue × arm, with the arm order rotated per issue by a Williams Latin square (each arm takes each position,
  and follows each other arm, once per 4 issues). Before each job it health-checks the server (restarting it through
  `local_model.sh` if it stays down) and waits while on battery. A crash or restart resumes at the first issue × arm
  without a result. Job limits come from each `task.json` as before (150 steps, 30 min).
- **H2 (primary, unchanged):** `loop-local` vs `mini-local`, T1 rule `stats_audit.py combined_H2.json --a mini-local
  --b loop-local --cost-budget 0.5`; KEEP also requires an observed Δ ≥ 15 pp.
- **H2-g (secondary):** `loop-local` vs `loop-local-nogrammar` (`--a loop-local-nogrammar --b loop-local`), T1 rule
  reported descriptively at n = 15.
- **H2-p (new, exploratory, descriptive only):** `pipeline-local` vs `loop-local` (`--a loop-local --b pipeline-local`).
  Why: the planner layers lost to the plain loop on Haiku, but planner/executor splits are designed for small models
  (Apple's on-device planner is one example), so the local model is where they could pay off. No verdict is drawn
  from it; a positive sign would need its own pre-registered run.
- **Secondary metrics per arm** (`local_ladder.py combine` -> `secondary.json`): valid-reply rate (loop:
  1 − format errors / steps; mini: executed commands / model turns; the pipeline uses tool calls, so none);
  finished-properly rate (loop: stop reason `submitted`/`gate_red` vs `max_steps`/`format_error`/limits; mini:
  `Submitted` vs anything else; pipeline: finished before the timeout with a result); minutes per solve (wall time);
  Wh per solve (battery energy delta from `ioreg` when on battery; **on AC, energy is unavailable** and the run
  records wall time plus llama-server token counters from `/metrics` instead); tokens/s (llama-server generation
  counters, else the loop's own telemetry); cached fraction (loop telemetry, else 1 − server prompt tokens / client
  input tokens). Power source is logged before and after each job.
- **Exclusions:** INVALID rules as before (provider, empty-response or crashed-harness runs are dropped from every
  arm of that issue). Arms of one issue must receive identical project trees and task files; `combine` reports any
  mismatch as fatal.
- No run starts until the owner approves it.

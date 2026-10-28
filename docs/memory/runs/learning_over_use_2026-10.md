# Track C — does AWOS get better with use? (2026-10-11)

Pre-registration: `docs/specs/ablation_learning_over_use.md` (commit 4aac316,
before any model call). Model: direct Anthropic `claude-haiku-5-5` for every
call (OpenRouter off). Workload: six R40 routine families chained into six
4-job series (plus a near-miss 5th job in `learn_f08`, `learn_f09`); synthetic
routine work, not real issues.

Arms: `off` (notebook 0, experience 0) vs `learn` (`AWOS_NOTEBOOK=1`,
`AWOS_EXPERIENCE=1`), interleaved job by job, fresh state per (series, repeat),
3 repeats. 156 rows (2 arms x 26 jobs x 3), 0 invalid.

## Verdict (pre-registered rules)

| Rule | Result |
|---|---|
| 1. Solve rate, positions 2–4 (stats rule) | **INCONCLUSIVE** — 54/54 vs 54/54; b = c = 0; no headroom (ceiling) |
| 2. Efficiency, cost, positions 2–4 | **LEARNING HURTS** — dearer on 18/18 tasks, sign p < 0.001, P(mean log-ratio < 0) = 0.000, geo-mean ratio learn/off **1.79** [1.62, 1.98] |
| 2. Efficiency, turns, positions 2–4 | **INCONCLUSIVE** — 4 fewer / 9 more / 5 tied, sign p = 0.267, P(<0) = 0.204, ratio 1.035 [0.945, 1.122] |
| 3. Learning-curve check (descriptive) | ratio of mean cost 1.21 at position 1 (placebo; geo-mean 1.23) rising to 1.54 / 1.76 / 2.18 at positions 2 / 3 / 4 — the wrong direction for learning |
| 4. Harm check (off all solved, learn none) | none at the pre-registered threshold; but see near-miss below |

**Claim "AWOS gets better with use (routine work, Haiku 5.5)": not shown.**
On this workload persistent learning makes AWOS ~1.8x more expensive per job,
with no turn or solve-rate gain to pay for it.

## Per-position curves (mean over valid runs; 6 tasks x 3 repeats per position)

| pos | arm | solve | mean $ | mean turns | $ / solved | min | notebook chars |
|---|---|---|---|---|---|---|---|
| 1 | off | 18/18 | 0.0024 | 3.8 | 0.0024 | 0.25 | 0 |
| 1 | learn | 18/18 | 0.0029 | 3.7 | 0.0029 | 0.33 | 1264 |
| 2 | off | 18/18 | 0.0024 | 3.8 | 0.0024 | 0.27 | 0 |
| 2 | learn | 18/18 | 0.0037 | 3.8 | 0.0037 | 0.37 | 2016 |
| 3 | off | 18/18 | 0.0025 | 3.9 | 0.0025 | 0.25 | 0 |
| 3 | learn | 18/18 | 0.0044 | 3.9 | 0.0044 | 0.36 | 2720 |
| 4 | off | 18/18 | 0.0022 | 3.4 | 0.0022 | 0.24 | 0 |
| 4 | learn | 18/18 | 0.0048 | 3.8 | 0.0048 | 0.38 | 3181 |
| 5 (near-miss) | off | 3/6 | 0.0025 | 4.2 | 0.0050 | 0.26 | 0 |
| 5 (near-miss) | learn | 1/6 | 0.0055 | 4.3 | 0.0330 | 0.49 | 3345 |

Source: `runs/track_c/analysis.md` / `analysis.json` (output of
`scripts/routines/analyze_learning.py runs/track_c/main/job_series_*.json`).

## Where the extra cost comes from (descriptive, not pre-registered)

Per-job means from the same rows (`cost_usd` includes the notebook call):

| pos | arm | cost | of which notebook call | agent-only cost | input tokens | LLM calls |
|---|---|---|---|---|---|---|
| 1 | off | 0.0024 | 0 | 0.0024 | 17 059 | 1.0 |
| 1 | learn | 0.0029 | 0.0005 | 0.0024 | 17 834 | 2.0 |
| 2 | learn | 0.0037 | 0.0008 | 0.0029 | 23 643 | 2.0 |
| 3 | learn | 0.0044 | 0.0009 | 0.0035 | 29 837 | 2.0 |
| 4 | learn | 0.0048 | 0.0011 | 0.0037 | 31 847 | 2.0 |
| 4 | off | 0.0022 | 0 | 0.0022 | 15 461 | 1.0 |

- Agent-only cost (notebook call excluded) is flat at position 1 (geo ratio
  1.00) and **1.37x** at positions 2–4 (dearer on 17/18 tasks); input tokens
  1.66x. So two costs stack: the notebook rewrite (~$0.0005–0.0011 per job,
  growing with the notebook) and the injected context (notebook up to ~3.2k
  chars + top-3 experience diffs, ~350–1300 tokens), resent on every turn.
- The memory buys nothing back: Haiku 5.5 already solves every position-1..4
  job in ~3–4 turns with empty memory (18/18 per
  position). There is no exploration left to save, so learning is pure
  overhead at this difficulty. This is a ceiling effect, not proof that
  memory can't help on harder or longer work.

## Near-miss: memory misleads (descriptive; below the harm-check bar)

`learn_f09` job 5 asks to "Add tests/test_percentile.py with a regression test
that `percentile([], 50)` ... raises ValueError, as its docstring promises" —
the code has the bug, so the source must be fixed too.

- `off`: 3/3 solved; every run edited `awos_util/pure.py` and the test.
- `learn`: 1/3 solved. In the two failures the agent edited only the test file
  (r1: hidden 2/3; r3: own tests broken, hidden 3/7). The notebook it read
  contains the lesson it wrote on jobs 1–4: *"When the goal is only to add
  tests, do not modify the function under test."*
  (`runs/track_c/main/job_series/20261011T154120/learn/state/.awos/projects/*/notebook.md`;
  edit traces in `runs/track_c/main/logs.tar.gz`, `r1_learn_f09.log`,
  `r3_learn_f09.log`).
- `learn_f08` job 5 (make `AWOS_NOTEBOOK` default off): 0/3 in both arms, so
  no arm difference.

The pre-registered harm check (off solves all, learn none) does not fire
because learn solved one run, but this is the misleading-memory failure mode
ablation B warned about: a family-specific rule generalised onto a look-alike
task. n = 1 task; suggestive, not significant.

## Deviations from the pre-registration (all documented)

1. **Learning was broken on the Anthropic path; fixed minimally** (allowed by
   the spec's pilot clause), commit 219f1b4:
   - the notebook update only had an OpenRouter client, so with OpenRouter off
     every update was skipped (`[notebook] update skipped: no model client
     (OPENROUTER_API_KEY not set)`, prior attempt's pilot2 log);
   - after routing a `claude-*` notebook model to Anthropic's OpenAI-compatible
     endpoint, Haiku 5.5 returned 400 `temperature is deprecated for this
     model` (`pilot_a_temperature400.log` in `runs/track_c/run_logs.tar.gz`); the notebook call now
     sends no temperature for `claude-*` models.
   - Re-pilot (`learn_f04` jobs 1–2, `pilot_b.log` in `runs/track_c/run_logs.tar.gz`): notebook
     1094 → 1651 chars, $0.0004 / $0.0006 per update; experience store saved
     and injected (#1, ~363 tokens). Excluded from analysis as specified.
   - In the main run 73 of 78 learn-arm notebook updates were written; the 5
     rejected ones were all job-5 rewrites that dropped a past job (the
     notebook's own guard), after which nothing reads the notebook.
     The experience store worked in every learn job.
2. **Launch granularity.** Instead of one `--repeat 3` call per series, the
   driver `scripts/routines/run_track_c.py` launches job_series once per
   (repeat, series), in the pre-registered order (repeat 1 over all six
   series, then 2, then 3), each with a fresh state dir — equivalent state
   isolation, and resumable. The analysis script re-keys files of the same
   series as distinct repeats (as designed).
3. **One external interruption.** At r1/`learn_f08` job 5 the job_series
   child got SIGTERM from outside the run (exit −15; cause unknown, the laptop
   was running other tracks). The driver was relaunched and resumed job 5 of
   that series in both arms with the same state (the notebook after job 4,
   unchanged because the killed learn job had not reached its update). The
   killed attempt's few cents of spend (< $0.01, estimate) are not in the
   results file.
4. **Budget stop never triggered.** Full design completed (3 repeats).
5. Not a deviation, but a confound shared by both arms: on the Anthropic path
   the acceptance gate and one-shot executor log `no OpenAI-shaped client
   configured` and are inactive in every job of both arms (logs). Not a
   per-arm variable; noted as another OpenRouter-only gap.

## Spend

Main run $0.5055 (results files) + pilots $0.0085 (this run) + $0.0095 (the
interrupted earlier attempt's pilots) + killed attempt (< $0.01) ≈ **$0.53**
of the $3 cap.

## Interpretation and what next

- On short routine jobs Haiku 5.5 is already at ceiling with no memory, so
  the notebook + experience store cannot cut turns and costs ~1.8x more
  (larger each job as memory grows). "Gets better with use" is not supported
  here; it is the opposite on cost.
- If AWOS keeps these features on by default, they should be **gated**: inject
  memory only when it is likely to save work (e.g. after a failed first
  attempt, on longer tasks, or above a turn threshold), cap injected size, and
  skip the per-job notebook rewrite when the job was trivial (≤ 4 turns,
  tests green) — that rewrite alone is ~20–25% of a learn job's cost here.
- Notebook rules need scope: the "don't modify the function under test" rule
  is right for the family and wrong for a look-alike bug-fix task. Tag rules
  with the task kind they came from, or mark them as defaults the goal text
  overrides.
- A fair test of the moat needs tasks where empty-memory AWOS takes many
  turns or fails (long-horizon or project-specific conventions it can't
  infer from the code). This workload can't show a benefit.

## Files

- `runs/track_c/main/job_series_*.json` (18 results files), `progress.json`,
  `logs.tar.gz` (per-launch logs), `runs/track_c/run_logs.tar.gz` (driver + pilot logs)
- `runs/track_c/analysis.md`, `runs/track_c/analysis.json`
- `runs/track_c/pilot/` (pilot results JSON)

- Series rebuild: `python scripts/routines/build_learning_series.py --out runs/track_c/series --near-miss`

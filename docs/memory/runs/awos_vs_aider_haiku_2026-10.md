# Experiment 1 (track B2): AWOS vs Aider on the same model, Haiku 5.5, fresh issues, 2026-10-11

Pre-registration: `docs/specs/ablation_awos_vs_aider_haiku.md` (commit d50c2b9). Driver: `scripts/ablation_awos_vs_aider.py`.
Both arms ran on direct Anthropic `claude-haiku-5-5` (`AWOS_SERIES_MODEL=claude-haiku-5-5`, `OPENROUTER_API_KEY` unset, banner `provider=anthropic key=set`). Arms were interleaved per issue, with the order alternating by issue index. Repeat 1 only.

## Verdict (T1 rule, `scripts/stats_audit.py --a aider --b off --cost-budget 0.5`)

**INCONCLUSIVE.** It narrowly misses KEEP.

| | Aider (base) | AWOS `off` (candidate) |
|---|---|---|
| tasks included | 38 | 38 |
| solved | 26/38 (68%) | 37/38 (97%) |
| mean $/valid run (as recorded) | $0.0300 | $0.0338 (×1.13, within the +50% budget) |
| $ per solved task | $0.0438 | $0.0347 |
| mean minutes/run | 1.32 | 2.64 |
| mean turns | 1.8 | 14.5 |
| invalid runs | 1 (sqlglot_8493, 3 attempts) | 0 |

- P(Δ>0) = **0.946**, just under the 0.95 KEEP threshold. 20 000 draws, seed 20261009, Monte-Carlo SE about 0.0015, so it is genuinely below. Posterior Δ is +14.5 pp with 95% CrI [−3.4, +31.8]. Observed per-task Δ is +28.9 pp.
- Task-level exact McNemar: b = 12 (AWOS only), c = 1 (Aider only), **p = 0.0034**. Cost leg: ×1.13 ≤ ×1.5.
- So two of the three KEEP legs pass and the rule returns INCONCLUSIVE ("P(Δ>0) = 0.946 < 0.95"). With K = 1, the Jeffreys Beta per task shrinks each 1/1-vs-0/1 task heavily. That is the conservatism the rule was built to have, and this result does not override it.
- Raw output: `awos_vs_aider_haiku_2026-10/stats_primary.json`. Rows: `combined.json`.

**Sensitivity (not pre-registered, descriptive only).** 6 of Aider's 12 failures ended with Aider printing `Empty response received from LLM` (see below). Dropping those 6 tasks from both arms leaves 32 tasks: AWOS 31/32, Aider 26/32, P(Δ>0) = 0.797, McNemar 6–1 p = 0.125, cost ×0.68. That is also **INCONCLUSIVE**. Half of AWOS's measured solve advantage comes from that one Aider failure mode. File: `combined_sens_no_empty.json`.

What this result does and does not support:
- On this fresh, harder set with the same model, AWOS solved more issues than Aider: 12 to 1 on discordant tasks. Per solved task it was cheaper ($0.035 vs $0.044). It took about 2× the wall time.
- The pre-registered rule does not call it KEEP, so "AWOS adds value over a minimal harness" is **not established** at the pre-registered bar. It is also clearly not REJECT (P(Δ>0) ≤ 0.20 is far away).
- The pre-registered next step for INCONCLUSIVE is to extend n. Repeat 2 on all 38 tasks would cost about $2.5, which is a new budget decision.

## Issues and exclusions

- 40 issues were in `~/.awos-harness/fresh_series` (READY = 40). 39 ran.
- **fresh_boltons_443 was dropped**: its preflight failed in-run (`hidden tests fail with the reference (91 errors in 9.66s)`), as the pre-registration says, with $0 spent. A separate `job_series validate` straight afterwards passed (`91 passed in 0.81s`), so the failure was environmental, the same kind as Track A's "reference: N errors" bursts under load. I started one resume pass to retry it. The laptop then went into battery maintenance sleep (pmset log: `Entering Sleep state due to 'Maintenance Sleep' ... Using Batt`), and the start-state preflight could not finish after about 60 minutes of wall time. I stopped that pass at $0 spend, so the issue stays dropped.
- **fresh_sqlglot_8493 is a paired exclusion**: all 3 Aider attempts were invalid under job_series' rules (`aider_no_edit (asked_to_add_files: Aider never had the code to edit)`; the harness's auto file selection picked 0 files). AWOS solved it.
- Included: 38 tasks.

## Per-issue results (repeat 1)

Hidden pass counts include subtest failures. AWOS rows show the post-fix test count and Aider rows sometimes show extra failed subtests, so the totals can differ.

| issue | AWOS (off) | Aider | note |
|---|---|---|---|
| fresh_astroid_2977 | solved (329/329, vis=ok, 12t, $0.0251, 1.91m) | solved (329/329, vis=ok, 2t, $0.0335, 2.75m) |  |
| fresh_astroid_3130 | solved (496/496, vis=ok, 14t, $0.0241, 2.18m) | failed (487/496, vis=ok, 2t, $0.0314, 2.69m) | **AWOS only** |
| fresh_astroid_3193 | solved (114/114, vis=ok, 11t, $0.0148, 0.96m) | solved (114/114, vis=ok, 1t, $0.0102, 1.48m) |  |
| fresh_astroid_3235 | solved (213/213, vis=ok, 9t, $0.0167, 4.18m) | solved (213/213, vis=ok, 3t, $0.0424, 4.07m) |  |
| fresh_astroid_3286 | failed (19/19, vis=FAIL, 11t, $0.0146, 0.7m) | solved (19/19, vis=ok, 2t, $0.0298, 2.85m) | **Aider only** |
| fresh_astroid_3348 | solved (232/232, vis=ok, 9t, $0.0105, 3.05m) | solved (232/232, vis=ok, 1t, $0.0170, 1.5m) |  |
| fresh_attrs_1592 | solved (52/52, vis=ok, 11t, $0.0222, 1.23m) | solved (52/52, vis=ok, 2t, $0.0216, 0.73m) |  |
| fresh_boltons_441 | solved (13/13, vis=ok, 6t, $0.0108, 1.06m) | solved (13/13, vis=ok, 1t, $0.0140, 0.75m) |  |
| fresh_boltons_513 | solved (88/88, vis=ok, 6t, $0.0105, 0.73m) | solved (88/88, vis=ok, 1t, $0.0120, 0.49m) |  |
| fresh_click_3777 | solved (257/257, vis=ok, 7t, $0.0122, 1.04m) | solved (257/257, vis=ok, 1t, $0.0121, 0.7m) |  |
| fresh_click_3818 | solved (8/8, vis=ok, 10t, $0.0164, 1.64m) | solved (8/8, vis=ok, 1t, $0.0186, 0.75m) |  |
| fresh_markdown-it-py_428 | solved (16/16, vis=ok, 8t, $0.0131, 1.19m) | solved (16/16, vis=ok, 1t, $0.0072, 0.56m) |  |
| fresh_markdown-it-py_433 | solved (15/15, vis=ok, 13t, $0.0268, 1.6m) | failed (3/15, vis=ok, 11t, $0.0897, 2.12m) | **AWOS only** |
| fresh_markdown_1648 | solved (133/133, vis=ok, 18t, $0.0315, 1.76m) | solved (133/133, vis=ok, 2t, $0.0248, 1.08m) |  |
| fresh_more-itertools_1248 | solved (595/595, vis=ok, 4t, $0.0070, 1.44m) | solved (595/595, vis=ok, 1t, $0.0126, 1.0m) |  |
| fresh_more-itertools_1261 | solved (602/602, vis=ok, 4t, $0.0035, 0.57m) | solved (602/602, vis=ok, 1t, $0.0105, 0.66m) |  |
| fresh_networkx_8837 | solved (65/65, vis=ok, 19t, $0.0553, 4.3m) | solved (65/65, vis=ok, 3t, $0.0271, 3.0m) |  |
| fresh_networkx_8892 | solved (315/315, vis=ok, 54t, $0.1996, 9.87m) | failed (314/315, vis=ok, 0t, $0.0000, 1.26m) | **AWOS only** |
| fresh_pathspec_132 | solved (22/22, vis=ok, 24t, $0.0679, 3.49m) | failed (22/24, vis=ok, 1t, $0.0120, 0.62m) | **AWOS only** |
| fresh_pathspec_144 | solved (29/29, vis=ok, 30t, $0.0976, 7.59m) | failed (29/31, vis=ok, 0t, $0.0000, 1.31m) | **AWOS only** |
| fresh_pathspec_152 | solved (65/65, vis=ok, 22t, $0.0392, 1.37m) | solved (65/65, vis=ok, 1t, $0.0144, 0.74m) |  |
| fresh_pathspec_157 | solved (130/130, vis=ok, 13t, $0.0270, 1.86m) | failed (130/133, vis=ok, 2t, $0.0189, 0.39m) | **AWOS only** |
| fresh_pydash_273 | solved (151/151, vis=ok, 6t, $0.0057, 0.29m) | solved (151/151, vis=ok, 1t, $0.0043, 0.28m) |  |
| fresh_pyflakes_868 | solved (58/58, vis=ok, 10t, $0.0258, 2.32m) | failed (54/58, vis=ok, 0t, $0.0000, 1.21m) | **AWOS only** |
| fresh_pyparsing_652 | solved (1922/1922, vis=ok, 9t, $0.0161, 1.95m) | solved (1922/1922, vis=ok, 2t, $0.0341, 1.16m) |  |
| fresh_pyparsing_653 | solved (1934/1934, vis=ok, 16t, $0.0357, 5.35m) | solved (1934/1934, vis=ok, 1t, $0.0122, 1.62m) |  |
| fresh_sqlglot_8029 | solved (27/27, vis=ok, 12t, $0.0263, 1.34m) | solved (27/27, vis=ok, 4t, $0.0895, 1.66m) |  |
| fresh_sqlglot_8042 | solved (49/49, vis=ok, 22t, $0.0836, 6.58m) | failed (45/57, vis=ok, 0t, $0.0000, 1.34m) | **AWOS only** |
| fresh_sqlglot_8259 | solved (70/70, vis=ok, 21t, $0.0354, 2.47m) | solved (70/70, vis=ok, 4t, $0.1678, 2.26m) |  |
| fresh_sqlglot_8335 | solved (53/53, vis=ok, 43t, $0.0964, 5.62m) | failed (52/53, vis=ok, 1t, $0.0109, 1.24m) | **AWOS only** |
| fresh_sqlglot_8460 | solved (24/24, vis=ok, 11t, $0.0264, 1.86m) | failed (24/29, vis=ok, 1t, $0.0124, 1.38m) | **AWOS only** |
| fresh_sqlglot_8471 | solved (62/62, vis=ok, 13t, $0.0301, 1.95m) | failed (61/63, vis=ok, 3t, $0.0657, 0.66m) | **AWOS only** |
| fresh_sqlglot_8493 | solved (63/63, vis=ok, 29t, $0.0838, 5.41m) | invalid (61/66, vis=ok, 1t, $0.0019, 1.44m) | paired exclusion |
| fresh_sqlglot_8524 | solved (49/49, vis=ok, 29t, $0.0911, 7.28m) | failed (49/80, vis=ok, 4t, $0.0786, 1.68m) | **AWOS only** |
| fresh_sqlglot_8538 | solved (17/17, vis=ok, 17t, $0.0320, 1.42m) | solved (17/17, vis=ok, 3t, $0.0565, 1.3m) |  |
| fresh_sqlglot_8574 | solved (60/60, vis=ok, 9t, $0.0153, 1.2m) | solved (60/60, vis=ok, 3t, $0.1241, 2.01m) |  |
| fresh_tinydb_634 | solved (9/9, vis=ok, 6t, $0.0054, 2.31m) | solved (9/9, vis=ok, 1t, $0.0069, 0.24m) |  |
| fresh_tomlkit_610 | solved (186/186, vis=ok, 6t, $0.0065, 2.39m) | solved (186/186, vis=ok, 1t, $0.0087, 0.26m) |  |
| fresh_typeguard_571 | solved (239/239, vis=ok, 6t, $0.0063, 2.42m) | solved (239/239, vis=ok, 1t, $0.0079, 0.27m) |  |

## Disagreements, examined

**Aider only (1): fresh_astroid_3286.** AWOS passed the hidden tests (19/19) but broke 61 visible tests across the brain, builder, inference and scoped_nodes suites. During the run it had executed only a subset of the tests ("17 passed"). This is a fair AWOS failure.

**AWOS only (12):**
- **Aider printed `Empty response received from LLM. Check your provider account?` and then `has hit a token limit` (6 tasks).** On networkx_8892, pathspec_144, pyflakes_868 and sqlglot_8042 this happened on the first call (0 turns, recorded cost $0). On sqlglot_8335 and sqlglot_8460 it happened after one completed turn. Each empty call returned after about 68-82 s regardless of prompt size (27k-100k input), and Aider showed ~0 output tokens. That fits the response using the whole 16 384-token output cap (B1 set `max_tokens` to AWOS's own agent-loop cap for parity) without any visible text, at roughly 200-240 tok/s. That is an inference, not verified. I sent a direct replay of the pyflakes_868 prompt to check stop_reason and block types, but it did not return before this write-up because the laptop was in maintenance sleep. Aider does not retry after an empty response. AWOS's loop runs on the same model with the same cap and recovers from such turns. Under the committed job_series rules these are valid "not solved" runs and count in the primary result. The sensitivity analysis above drops them.
- **Genuine wrong or incomplete Aider fixes (6 tasks):** astroid_3130 (487/496 hidden), markdown-it-py_433 (3/15, 11 turns), pathspec_132 (2 failed), pathspec_157 (3 failed), sqlglot_8471 (2 failed), sqlglot_8524 (31 failed). Even counting only these, AWOS leads 6-1.

## Spend

- Recorded: **$2.5092** in total (AWOS $1.3682, Aider $1.1410), against the $3.00 cap.
- Not recorded in the rows:
  - About 9 empty-response calls where Aider/litellm logged no usage. If each used its full 16k output cap, that is at most about $0.012-0.018 each.
  - Two earlier invalid Aider attempts on sqlglot_8493 ($0.0039 per the logs).
  - One diagnostic replay call (≤ $0.012).
- Upper-bound estimate of true spend: **≤ $2.70**. The cap held.
- Aider's mean $/run is understated by the $0 recorded for its 4 zero-turn runs.
- Repeat 2: the pre-registration runs a full second pass only if it fits under the cap, which it does not (projected about $2.5 more), "otherwise as far as the cap allows". **Deviation:** I did not start a partial repeat 2. The remaining headroom (about $0.30-0.49 after the uncounted spend) would have covered only about 5 issues, all from the alphabetically first repo (astroid). The driver's mean-based projection could also overshoot the strict cap on a costly sqlglot issue. This adds no meaningful power (the MDE is still about 23 pp).

## Other findings about the AWOS arm (as it ran)

- **The AWOS planner never ran.** Every `off` run logged `primary planner failed ... Error code: 400 ... MissingSessionID ... opencode.ai` and fell back to `goal_as_task`. `CheapPlanner` sends `claude-haiku-5-5` to the OpenCode Go endpoint (`OPENCODE_GO_BASE_URL` default) when no OpenRouter key is set. The Haiku 5.5 baseline (bench-run `.awos/job_series_20261011T1*.json`) shows the same pattern: 33 `goal_as_task` and 40 `skipped_small_goal`, with no successful plans. The call fails without spend.
- `[ACCEPTANCE] gate inactive: no OpenAI-shaped client configured` and `[ONE-SHOT] fell back to the agent loop` appear on the direct-Anthropic path too.
- So the "AWOS" measured here is in effect the AWOS agent loop with its tool set, test-running behaviour and prompt caching. It does not include planning, the acceptance gate or one-shot.

## Deviations from the pre-registration

1. Partial repeat 2 not run (see Spend).
2. One retry of the preflight-failed boltons_443 was attempted and abandoned at $0. The issue was dropped exactly as the pre-registration says.
3. Sensitivity analysis (dropping the empty-response tasks) added. It is descriptive only and does not change the label.
4. Wall-clock: the run started 2026-10-11 17:12 IST and the driver finished about 00:10. The last 3 issues ran across battery maintenance-sleep windows. `minutes` are monotonic, so sleep is excluded, but the long gaps do not affect results.

## Reproduce

```
AWOS_SERIES_MODEL=claude-haiku-5-5 caffeinate -i python scripts/ablation_awos_vs_aider.py run \
    --series-root ~/.awos-harness/fresh_series --out ~/.awos-harness/b2_ablation --cap 3.0
python scripts/ablation_awos_vs_aider.py combine --out ~/.awos-harness/b2_ablation
python scripts/stats_audit.py ~/.awos-harness/b2_ablation/combined.json --a aider --b off --cost-budget 0.5
```

Raw per-run logs (not committed, about 1 GB of project copies) are in `~/.awos-harness/b2_ablation/r1/<issue>/job_series/<ts>/{off,aider}/run.log`, with the driver log in `~/.awos-harness/b2_ablation/driver.log`.

## Suggested next steps

- Fix Aider's empty-response handling in `scripts/harness_aider.py`. Treat `Empty response received from LLM` with no usage as `no_model_response` (invalid and retried), and record its tokens. Also stop Aider's own banner `Warning: it's best to only add files that need changes to the chat.` from matching the ask-to-add regex: all 7 `asked_for_files_it_had` labels in this run came from it (the warning appears in 25 of the Aider logs).
- Fix or remove the OpenCode planner path on direct Anthropic, so that "AWOS" includes its planner when compared again.
- To decide KEEP vs not: run repeat 2 on the 38 included tasks (about $2.5), which the rule pools per task.

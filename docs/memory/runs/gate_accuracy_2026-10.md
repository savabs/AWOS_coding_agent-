# Gate accuracy on existing runs: false accepts and false rejects (2026-10-11)

Track G of the parallel wave. Analysis only; $0 spent. Pre-registered in
`docs/specs/gate_accuracy_analysis.md` (commit 898584e, written before the
script existed). Script: `scripts/gate_accuracy.py` (tests:
`tests/test_gate_accuracy.py`, 12 pass). Full output:
`docs/memory/runs/gate_accuracy_2026-10/gate_accuracy.json`.

```
python scripts/gate_accuracy.py <bench-run>/.awos/job_series_E1_combined.json \
    <bench-run>/.awos/job_series_20261011T1[23]*.json --json <out>
```

## Data

| set | runs | issues | hidden-fail runs | source |
|---|---|---|---|---|
| DeepSeek V4 Flash (E1, arms ctrl + treat, 2026-10-09/10) | 137 | 69 | 24 | `job_series_E1_combined.json` |
| Claude Haiku 5.5 (baseline, 2026-10-11) | 73 | 73 | 1 | `job_series_20261011T12*/T13*.json` |

Ground truth is the hidden tests: `hidden_failed == 0 and hidden_passed > 0`.
Every row had a run.log, and 0 rows were invalid. The five pre-E1 Flash files
(`job_series_20261009T00*`, two repeats each) and the dry run
`20261010T182733` are not included.

## Headline: the declared gate (what AWOS reports as success)

Wilson 95% intervals.

| | FA / hidden-fail runs | FA / accepted runs (FDR) | FR / hidden-pass runs |
|---|---|---|---|
| Flash (E1) | 12/24 = **50.0%** [31.4, 68.6] | 12/125 = **9.6%** [5.6, 16.0] | 0/113 = 0.0% [0.0, 3.3] |
| Flash ctrl | 5/12 = 41.7% [19.3, 68.0] | 5/62 = 8.1% [3.5, 17.5] | 0/57 |
| Flash treat | 7/12 = 58.3% [32.0, 80.7] | 7/63 = 11.1% [5.5, 21.2] | 0/56 |
| Haiku 5.5 | 1/1 = 100% [20.7, 100] | 1/73 = **1.4%** [0.2, 7.4] | 0/72 = 0.0% [0.0, 5.1] |
| **Overall** | 13/25 = **52.0%** [33.5, 70.0] | 13/198 = **6.6%** [3.9, 10.9] | 0/185 = 0.0% [0.0, 2.0] |

- **The gate never falsely rejects, and it waves through about half of the
  real failures.** When the work was wrong, AWOS said "done" 13 times out of
  25.
- **Every correct rejection came from the agent loop giving up, not from a
  test.** All 12 correctly rejected runs are Flash `no_progress` stops followed
  by a full rollback. No run with an edit that kept the visible tests green was
  ever reported as a failure. The acceptance gate (mode 2) can add repair work,
  but it never turns a visible-green result into a rejection.
- **Task-clustered:** 11 of the 73 distinct issues had at least one false
  accept. The E1 runs come in pairs (ctrl and treat on the same issue), so run
  counts overstate the independent evidence. humanize_329 and click_3884
  falsely accepted in both arms.
- **ATLAS H1 premise ("current false-accept rate above 10%"): supported on
  organic failures.** The Wilson lower bound is 33.5%, above 10% (the rule is
  pre-registered). An adversarial (canary) estimate is still unmeasured.

### Other gates on the same runs (pooled)

| gate | FA / hidden-fail | FDR | FR / hidden-pass |
|---|---|---|---|
| `visible`: repo's visible tests only | 25/25 = 100% [86.7, 100] | 25/209 = 12.0% | 1/185 = 0.5% |
| `own_tests`: visible + acceptance after arbitration | 21/25 = 84.0% | 21/199 = 10.6% | 7/185 = 3.8% |
| `strict_acceptance`: any failing kept test rejects | 17/25 = 68.0% | 17/173 = 9.8% | 29/185 = **15.7%** |
| `verified_tier`: declared and no unverified signal (below) | 3/25 = **12.0%** [4.2, 30.0] | 3/82 = **3.7%** [1.3, 10.2] | 106/185 = 57.3%* |

\* The `verified_tier` false-reject figure includes all 72 Haiku passes,
because Haiku's acceptance gate never ran (finding 1). On Flash alone it is
34/113 = 30.1%.

The visible tests alone catch none of the hidden failures. Every hidden-fail
run was visible-green. In this harness, visible tests are the pre-fix suite.

## Failure modes of the 13 false accepts (declared gate)

The labels are rule-based, read from run.log (first match wins; see the spec).

| mode | n | runs |
|---|---|---|
| `repair_failed_restored`: kept acceptance tests still failed after the bounded repair, so the green edit was restored and accepted | 4 | click_3677 ctrl, markdown_1652 ctrl, marshmallow_2901 treat, pydash_266 treat |
| `arbiter_overrode`: the arbiter labelled every failing kept test WRONG_TEST | 4 | click_3884 ctrl+treat (hidden tests edited, low confidence), parse_159 treat, sqlparse_601 treat |
| `acceptance_passed_weak`: the generated tests all passed but encoded a misreading | 3 | humanize_329 ctrl+treat, rich_4076 treat |
| `acceptance_none_kept`: generation hit a collection error, so the gate was inactive | 1 | marshmallow_2909 ctrl |
| `acceptance_inactive`: no gate at all (Haiku) plus a post-green stop | 1 | markdown_1515 (Haiku) |

### Each log signal against the hidden truth (declared-accepted runs only)

This is the precision of each signal. "Hidden pass" is what a reject rule
built on that signal would cost.

| signal | accepted runs | hidden fail | hidden pass | fail share |
|---|---|---|---|---|
| acceptance clean pass (kept tests green, no override) | 78 | 3 | 75 | 3.8% |
| repair passed | 7 | 0 | 7 | 0% |
| arbiter overrode | 26 | 4 | 22 | 15% |
| repair failed, green restored | 11 | 4 | 7 | 36% |
| post-green stop | 5 | 2 | 3 | 40% |
| acceptance generated 0 kept tests | 3 | 1 | 2 | 33% |
| agent hit max_turns | 13 | 4 | 9 | 31% |
| loop guard tripped | 5 | 1 | 4 | 20% |
| acceptance inactive (all Haiku) | 73 | 1 | 72 | 1.4% |

- **The arbiter is mostly right.** It was correct in 22 of its 26 overrides
  (85%). Rejecting whenever any kept test fails (`strict_acceptance`) would
  remove 4 false accepts and add 22 false rejects. It fails the
  pre-registered rule.
- **"No kept test passed" does not make the arbiter safer.** That case went 1
  hidden fail to 2 hidden passes (sqlparse_601 treat: the arbiter discarded
  both kept tests, and the hidden test checks exactly that behaviour).
- **A failed repair is the riskiest accept path the code has**: 4 fails and 7
  passes, so the generated tests were wrong 7 times out of 11. As a reject
  rule it fails the pre-registered rule (4 FA removed, 7 FR added).
- **The clean acceptance pass is the only strong evidence AWOS has: 3.8%
  FDR.** A visible-green result with no clean acceptance pass is wrong far
  more often.

## Findings

1. **The acceptance gate was silently off for the whole Haiku baseline.**
   `[ACCEPTANCE] gate inactive: no OpenAI-shaped client configured` appears in
   73 of 73 Haiku run.logs. `one_shot.one_shot_client()`
   (`scaffold/agent/one_shot.py:889`) builds only OpenAI-shaped clients
   (OpenRouter, a local server, `AWOS_BASE_URL`). Direct Anthropic gets
   `None`, so `_acceptance_prepare` (`scaffold/agent/orchestrator.py:2748`)
   returns `None` and the gate is skipped. One-shot is skipped too
   (`orchestrator.py:2564`). On Haiku, AWOS's gate was the visible tests plus
   the agent's own claim. The 72/73 result was earned without the acceptance
   layer, and it says nothing about that layer's value on Haiku. Nothing in the
   job_series result row records this; only the log does.
2. **The Haiku false accept is a post-green stop that overrode the agent's own
   evidence.** In markdown_1515 (`job_series/20261011T125953/off/run.log`) the
   agent wrote at t9: "Three of four still fail". It kept investigating
   without editing. At t17 the post-green idle rule stopped it ("tests passed 7
   turns ago and nothing changed since", `agent_loop.py:1120-1135`) and marked
   the task a success. Post-green stops overall: 5 accepted runs, 2 of them
   hidden fails.
3. **Flash false accepts are spread across the acceptance pipeline:** 4
   failed repairs, 4 arbiter overrides, 3 weak tests and 1 empty suite. No
   single cheap reject rule removes ≥ 2 of them at ≤ 1 false-reject cost (the
   pre-registered bar).
4. **Side finding (not gate-related):** 33 of 73 Haiku runs logged `primary
   planner failed … MissingSessionID … x-opencode-session` and fell back to
   one task. The planner client is reaching a different endpoint from the
   agent client. Worth a separate look.

## Recommended gate changes, ranked by expected impact

Ranking follows the pre-registered rule: (FAs addressed − FRs added), with
implementation cost as the tie-breaker. **None of the reject-style rules
qualifies as "recommend now"** on this data. The two changes that do qualify
add no false rejects, because they label or fix rather than reject.

| # | change | FAs addressed | FRs added | status |
|---|---|---|---|---|
| 1 | **Graded verdict:** report `verified` only for a clean acceptance pass or a passed repair. Otherwise report `accepted_unverified` with the reason (`acceptance_inactive`, `acceptance_none_kept`, `arbiter_overrode`, `repair_failed_restored`, `post_green_stop`). Put it in report.json and the job_series row. | 10 of 13 flagged (verified-tier FDR 3.7% vs 6.6%) | 0 (label only; success is still reported) | **recommend now** |
| 2 | **Acceptance on every provider:** give the acceptance generator and arbiter a client for direct Anthropic (or route them through the agent's client). When the gate cannot run, fail loudly: record `acceptance: inactive` in the result row, not only in the log. | Haiku: 1 (the only failure); Flash data suggests FDR falls from ~18% (visible-only) to ~4% (clean pass) | unknown on Haiku | **recommend now** for the loud-failure part; the Anthropic client needs a Haiku acceptance-on/off A/B (pre-register; needs a set where Haiku fails more than 1 in 73) |
| 3 | **Post-green stop guard:** do not auto-succeed when the agent's latest own check after the last edit failed (a run_command or reproduction result), or run the acceptance suite before honouring a post-green stop. | 2 (markdown_1515 Haiku, click_3884 treat) | ≤ 3 (the 3 hidden-pass post-green runs, if all were rejected; a guard that only delays the stop costs turns, not rejects) | A/B |
| 4 | **Escalate instead of accept after a failed repair:** hand it to a stronger model or a second repair, or mark it unverified (covered by #1). | 4 | 7 if it rejects; ~0 if it escalates (costs $) | A/B as escalation; do not reject |
| 5 | **Regenerate once when acceptance generation keeps 0 tests from a collection error.** | 1 | 0 expected | cheap; recommend with #2 |
| 6 | **Examples-as-tests:** compile the issue's explicit input → output examples straight into acceptance tests before LLM generation (humanize_329 and rich_4076 had generated tests that encoded a misreading). | ≤ 3 | unknown | A/B |
| 7 | **Arbiter second opinion** when it discards all kept tests or more than half of them. | ≤ 1 to 4 | ≤ 2 to 22 | not recommended on this data (the arbiter is right 85% of the time) |
| 8 | **Canaries (ATLAS H1):** plant ImpossibleBench-style tasks to measure the adversarial false-accept rate. This analysis measures only organic failures. | measurement | 0 | still needed |

## Caveats

- The sample is small. There are only 25 hidden-fail runs (24 Flash, 1
  Haiku), so the Haiku false-accept rate is essentially unmeasured (1/1, CI
  [20.7, 100]). The 73-issue set is near Haiku's ceiling.
- E1 runs are paired by issue, so the run-level Wilson intervals are somewhat
  too narrow (see the clustered count above).
- Failure-mode labels come from log patterns. click_3884's hidden tests were
  edited (low confidence per the E1 spec). Two of the four `arbiter_overrode`
  false accepts are that issue.
- Counterfactual gates (`strict_acceptance`, `verified_tier`) re-classify the
  same runs. They do not model the extra work a rejection would trigger.
- Visible tests in this harness are the repo's pre-fix suite, so the `visible`
  row says nothing about a gate that runs tests the fix itself adds.

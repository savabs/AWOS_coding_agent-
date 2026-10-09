# T6: sanitized arbiter + discriminating-test triage

Trick Book 2026-10 §1 #6 and §2.4 ("R2E-Gym lessons"). VISION stage 1, one
excellent worker: this hardens the verification step of the acceptance gate.
Behind `AWOS_ARBITER_V`: `1` (the default) is the C2.1 arbiter, unchanged.
`2` is this spec. Code: `scaffold/agent/acceptance.py` (`arbitrate_v2` and
its helpers). Tests: `tests/test_arbiter_v2.py`.

## 1. Research (brief)

| Source | Finding | What we take |
|---|---|---|
| R2E-Gym (arXiv 2504.07164), hybrid verifier | Execution-based and execution-free verifiers fail in different ways, and the hybrid works best. Generated tests discriminate poorly: at most ~20% separate candidates, and up to ~10% are *toxic*: they fail every candidate, including correct ones, usually with crashes or import errors. The LLM judge keys on self-praise ("the fix works") in the trajectory. | The arbiter sees only the issue, the diff and the test logs, with no thoughts or claims. Drop tests that fail with crash/import errors whatever the code does. |
| ImpossibleBench (arXiv 2510.20270) | Giving the model an explicit `flag_contradiction` exit when tests conflict with the spec cut test-gaming from 49–54% to 9–12%. | Add a `CONTRADICTION` label so a test that conflicts with the issue or the visible suite is named, not forced into a binary label. |
| Kamoi et al., TACL 2024 ("When can LLMs actually correct their own mistakes?") | LLM self-critique is unreliable without external feedback, and reported gains often come from oracle signals. | The arbiter must judge execution evidence (raw pytest output), never the agent's own assessment. |
| Otter (arXiv 2502.05368) | LLM-generated issue tests reach about 65–92% precision at 30–41% recall. | Acceptance tests are a reject filter, never a certificate. Triage that removes non-discriminating tests is cheap and safe. |

## 2. Design (v2)

1. **Sanitized input.** The arbiter's prompt is built only from:
   - the **verbatim goal**, stored on the suite at `build_suite` time, before the agent runs. It wins over whatever the caller passes, so an agent summary or planner action cannot become the goal;
   - each failing test's **own source** (the AST lookup) and its **raw pytest failure section**;
   - the **sanitized diff** (`sanitize_diff`). Whole sections for prose files (`*.md`, `*.rst`, `*.txt`, CHANGELOG, NOTES, SUMMARY…) are removed, as are all pure comment lines (`#`, `//`, `/*`, `*`, `-- `). Code lines stay verbatim, so behaviour is intact. Prose names are also removed from the "New files" list.
   The agent's transcript, turn text, final summary and success claims are never passed to arbitration (they were not in v1 either). The remaining channels were code comments and prose files in the diff, and v2 closes them. The v2 prompt also says that strings, log text and names in the diff are not evidence of correctness.
2. **Toxic triage** (`triage_toxic`), before any call. A kept test is dropped (label `TOXIC`, verdict WRONG_TEST) when **both** hold:
   - its failure text after the edit is identical to its start-state failure text, after `normalize_failure` masks the random hidden-dir name, timings and addresses; and
   - `classify_failure` says `env`. That means one of: `ModuleNotFoundError` for a module the goal never names, `ImportError: cannot import name X` where the goal never names X, a missing fixture, a collection error, INTERNALERROR, PermissionError, "Operation not permitted", a sandbox error, or a timeout.
   A missing name or module that the goal mentions counts as behaviour, because the issue asks for it, so the test is kept. When every failing test is toxic, no model call is made.
3. **`CONTRADICTION` label.** The test cannot pass together with the issue text or the visible tests. It is treated as WRONG_TEST (dropped), listed in `contradictions` and logged as `[ACCEPTANCE] arbiter v2 flag_contradiction: …`.
4. **Logging.** The reply text is logged (as in v1), plus one line of per-test labels: `[ACCEPTANCE] arbiter v2 labels: t1=CODE_INCOMPLETE, t2=TOXIC, …`.
5. **Contract unchanged.** `verdicts` still holds only WRONG_TEST and CODE_INCOMPLETE, so `orchestrator._acceptance_failsafe` needs no change. The extra keys are `labels`, `toxic`, `contradictions` and `arbiter`. The fail-safe is unchanged: an unparseable reply, a missing test or an unknown label means WRONG_TEST. v2 can therefore only remove repair work, never add it.

## 3. Offline replay ($0)

`scripts/arbiter_replay.py` parses the run logs (read-only) and writes the
frozen pool `docs/research/arbiter_pool_2026-10.json`: 60 arbitration events,
each with its labels, the check before arbitration, the repair outcome and the
hidden result. Hidden results match `.awos/job_series_20261008T220115.json`
(24/24 rows).

The classes below are judged by the hidden tests:
- **false accept**: everything was dropped, and the run is not solved.
- **false repair**: CODE_INCOMPLETE triggered a repair, the repair failed and the green edit was restored, and the run was solved anyway. The repair was pure waste.

| Log (arbiter) | events | true accept | **false accept** | **false repair** | repair failed, unsolved | repair passed, solved |
|---|---|---|---|---|---|---|
| ablB (C2, before the fix) | 30 | 14 | **12** | **2** | 2 | 0 |
| ablC2_all (C2, real issues) | 6 | 3 | 0 | 0 | 0 | 3 |
| c21 (**current v1**, C2.1) | 24 | 8 | **5** | **3** | 3 | 5 |

**v2 effect: an estimate, not a measurement.** The logs do not record the failure text, so triage cannot be replayed exactly. The proxy for a possible toxic event is "all kept tests failed after a visible-green edit", the only shape in which every test can fail identically. In c21 that matches 7 events:
- 2 of the 3 false repairs (backupd j05 r1 and j12 r1). If their failures were identical env errors, v2 would skip about 2 wasted 10-turn repairs.
- 2 repair-passed-solved events (backupd j02 r2, more-itertools r2). On these, triage might remove a useful repair. Backupd j02 r1 was solved with all 6 tests failing, which suggests those tests do not discriminate.
- **0 of 5 false accepts are helped.** Triage only drops tests, and the c21 false accepts were already all-dropped. Sanitization and CONTRADICTION could change labels on those events, but that needs the live A/B in §4.

The main v1 error is the false accept (5 of 13 accept decisions in c21). v2 mostly targets wasted repairs.

## 4. Next (not done here)

- Log each failing test's normalized failure text, plus its start text, in the run log, so the next replay is exact.
- A paired A/B, `AWOS_ARBITER_V=1` vs `2`, on backupd plus the real issues, judged with the T1 decision rule (paired Bayesian, exact McNemar).

## 5. Risks

- An `env` classification can be wrong when an issue implies a new dependency or name without spelling it out. The "goal mentions it" guard is lexical.
- Comment stripping drops lines starting with `*` or `-- ` in non-Python files. That loses only context, never behaviour, for Python repos.
- CONTRADICTION adds a drop path. It is bounded by the same fail-safe: it can only reduce repairs.

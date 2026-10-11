# Parallel experiment wave — 2026-10-11

Lead analyst write-up. Sources: per-track reports with independent skeptic verdicts (workflow
results JSON + `wf_0d963615-8f0/journal.jsonl`). Branch file lists and textual merge checks were
done from bench-run with `git diff --name-only` / `git merge-tree`, which are read-only.

## 1. Bottom line

The wave produced **two real results, one hardening deliverable and one pre-registered,
ready-to-run experiment**. It did **not** answer any of the strategic questions: AWOS vs Aider on
the same model, learning over use, and the reality of local models.

- **Computer use (D):** first real local computer-use chore works end to end on Haiku 5.5. A messy
  receipts folder was organised 3/3, the independent verifier passed 15/15 each run, the undo log
  restored the seed byte for byte, and it cost about $0.004 per run. This shows the plumbing works,
  not that it is robust. The task is easy by construction, and no live model ever tried an unsafe
  action, so the live safety policy is still unexercised.
- **Verification gate (G):** AWOS's "success" is unreliable exactly when it matters. On organic
  failures the gate accepted **13/25 = 52% [33.5, 70.0]** of hidden-fail runs. It never falsely
  rejected (0/185). Also, **the acceptance gate was silently off on all 73 Haiku 5.5 runs**
  (`one_shot_client()` returns None for direct Anthropic). So yesterday's 72/73 baseline was earned
  without the acceptance layer, and nothing in the result rows shows it.
- **Hardening (F):** the untrusted-command allowlist had **46 real bypasses**, now fixed with tests
  (826 desktop tests pass). It is still not wired in, and one gap remains (`requirements.txt` writes).
- **Blocked (A, B1-cont, C, E):** four tracks did no work. The harness pinned the subagents to the
  bench-run worktree, so they could not write their own track worktrees. This is an infrastructure
  failure, not a scientific one. B2 is blocked downstream of A, by its own gate.

Project level after this wave: the coding testbed is still at **parity with Aider, not
significant** (last measured 64% vs 61%, earlier model). The decisive same-model A/B is now fully
pre-registered and tooled, but it cannot run until a fresh, harder issue set exists. Computer use
moved from zero to a first working, reversible demo.

**Total spend this wave: about $0.03 logged** (D $0.0215 + B1 first attempt $0.0084). Add a few
cents at most of unlogged calls from B1's three cut-off background runs. Everything else was $0.

## 2. Track table

| Track | Status | Headline | Skeptic | Spend | Branch @ commit |
|---|---|---|---|---|---|
| A — fresh harder issue set | **blocked** (worktree pin) | Nothing built. `~/.awos-harness/fresh_series` holds `_tools/` + 1 issue (`fresh_sqlglot_8335`) from an earlier interrupted attempt, no READY | confirmed (blocked is honest; "branch unknown" was wrong) | $0 | `worktree-wf_0d963615-8f0-1` @ 307fa60 (no new commits) |
| B1 — Aider on Haiku 5.5 | **partial** (1st attempt); continuation **blocked** | Aider arm runs on direct Anthropic. Two live bugs fixed (temperature 400; no-response scored as fair "not solved"). Live proof 1/4: `real_attrs_1513` solved, $0.0084 (AWOS: $0.0028 on same issue) | continuation: unverifiable. First attempt: corroborated by B2's skeptic against `docs/specs/aider_haiku55_arm.md` | $0.0084 logged (+ unlogged cut-off runs) | `worktree-wf_0d963615-8f0-2` @ 241aeae |
| B2 — AWOS vs Aider, same model | **blocked** by its own gate (no READY; under 15 issues) | Pre-reg + resumable $3-capped driver + tests committed. Driver refuses to start without a valid fresh set (exit 4) | confirmed (31 tests pass; power numbers not reproduced) | $0 | `worktree-wf_cb146992-f2c-9` @ d50c2b9 (contains B1) |
| C — learning over use (off vs learn) | **blocked** (worktree pin) | Only the pre-reg + tooling from an earlier attempt exist. No runs | confirmed (blocked; 4aac316 present; tooling untested) | $0 | `worktree-wf_0d963615-8f0-3` @ 4aac316 |
| D — first local computer use | **done** | Receipts chore 3/3 SUCCESS (15/15 each), exact revert 3/3, nothing outside the sandbox changed. Red-team arm: model still refused, so "not exercised". Scripted attack replay blocked 14/14 | **confirmed** (independent replay of all 5 plans passes; spend recomputed) | $0.0215 | `worktree-wf_0d963615-8f0-4` @ 0de4afb |
| E — local model reality | **blocked** (worktree pin) | No server, no runs. Pre-reg + drivers only (includes an unrun `dag_executor.py` change) | unverifiable (no results; :8080 confirmed free) | $0 | `worktree-wf_0d963615-8f0-5` @ 59beee7 |
| F — allowlist adversarial review | **done** (2nd attempt). 1st attempt blocked by a safety classifier | 330 untrusted-command tests + 76 must-allow. 46 bypasses found and fixed in `policy.py`. 826 desktop tests pass. Not enabled | **confirmed** (826 reproduced; probes held) + new gap: `echo > requirements.txt` allowed | $0 | `worktree-wf_cb146992-f2c-5` @ f87f111 (1st attempt: `worktree-wf_0d963615-8f0-6` @ 307fa60, nothing) |
| G — gate false-accept rate | **done** (analysis, $0) | Pooled FA 13/25 = 52% [33.5, 70.0]; wrong-success rate 13/198 = 6.6%; FR 0/185. Acceptance gate off on all 73 Haiku runs | **confirmed** (JSON reproduced byte-equal) with rule-citation and post-hoc-recommendation caveats | $0 | `worktree-wf_0d963615-8f0-7` @ 8aa052d |

## 3. What we now KNOW (skeptic-confirmed or partially confirmed)

**Verification gate (G, confirmed):**
- When AWOS fails a hidden test, it still reports success about half the time: 13 of 25 hidden-fail
  runs, 52% with CI [33.5, 70.0]. It has never falsely rejected a hidden-pass run (0/185).
  *Caveats:* the run-level Wilson intervals ignore the ctrl/treat pairing. The 25 failures come from
  roughly 11-14 issues, and `click_3884` (whose hidden tests were edited) supplies 2 of the 13. The
  data is almost entirely DeepSeek Flash: Haiku had one hidden failure, so its FA rate is unmeasured.
- Every hidden failure was green on the visible tests (25/25). All 12 correct rejections came from
  the loop giving up (no_progress + rollback), never from a test. In practice the acceptance layer
  adds repair work but never rejects anything.
- **The acceptance gate was inactive on all 73 Haiku 5.5 runs** (73/73 logs say "gate inactive";
  root cause at `one_shot.py:889` / `orchestrator.py:2748`, confirmed in code). The 72/73 Haiku
  baseline is therefore a no-acceptance-layer number. The result rows do not record this.
- The only Haiku false accept (`markdown_1515`) was a post-green idle stop. At t9 the agent said
  "three of four still fail"; at t17 the idle rule stopped it and marked it a success.
- Side finding: 33/73 Haiku runs hit `MissingSessionID ... x-opencode-session` in the planner and
  fell back to single-task mode. The planner client seems to reach a different endpoint from the
  agent client.
- *Caveats on G's recommendations:* the "graded verdict" (verified vs accepted_unverified) and the
  "acceptance on every provider" fix are sensible, but they are **post-hoc**. Neither passes the
  pre-registered "recommend now" rule (remove at least 2 FA for at most 1 added FR). The graded verdict
  labels 106/185 correct successes as unverified, including all 72 Haiku passes. The report also
  cited the Wilson lower bound as "the pre-registered rule", but the spec registered the much laxer
  upper-bound > 10%. The conclusion survives either way.

**Computer use (D, confirmed):**
- A Haiku 5.5 file-level agent completed a pre-registered local chore 3/3 (rule: at least 2/3). It
  used a dry-run plan on a virtual tree, a postcondition per op, a write-ahead undo log and auto
  rollback, with no overwrite and no delete path. About 20-23 s and about $0.004 per run.
- Revert restored the exact seed 3/3, and nothing outside the sandbox changed. The skeptic re-seeded,
  re-applied all 5 committed plans with independent code, and got 15/15 each.
- A scripted (no-LLM) replay of the injection's demands was stopped 14/14 by R3, R7, the sandbox
  path check and the dry-run rejection.
- *Caveats:* the task is easy by construction (no ambiguous dd/mm dates, clear totals), and the same
  author wrote the seeder, truth and verifier. **No live model attempted an unsafe action**, even in
  the red-team arm, so the live policy claim is vacuous. The replay's "stopped" check is a loose
  substring match (all 14 rows were hand-verified). The outside-sandbox check fingerprints only top-level
  names. R7 produced one false positive (a read-only for-loop). No GUI or AX was used: this is
  file-level computer use only.

**Allowlist hardening (F, confirmed):**
- The pre-fix untrusted allowlist allowed 46 commands it should have refused, in 9 groups. Examples:
  `rg --hostname-bin` runs a program, `pytest @file`, option-named files planted by redirects and
  later glob-expanded, symlink and hard-link write targets, a dropped fd digit hiding an operand,
  symlink-following recursion, `wc --files0-from=-`, ugrep-style grep flags, and writes to agent
  instruction files (CLAUDE.md, AGENTS.md). All are fixed fail-closed. The red commit (42b08c0)
  shows the 48 failures, and 826 desktop tests pass after the fix (skeptic reproduced).
- *Caveats:* **`echo hi > requirements.txt` is still allowed** under an untrusted initiator (found by
  the skeptic), and Pipfile, poetry.lock and constraints files were not probed. The allowlist is
  **not wired in**: agent_loop has no UNTRUSTED provenance tagging. It reads command text only, so
  shell functions and environment (e.g. the owner's grep-to-ugrep zsh function, PAGER, GIT_*) are
  invisible to it. A TOCTOU race remains, and pytest still runs whatever code is in the sandbox.
  The 34 failures and 3 errors in the full suite (unrelated files) were not independently checked.

**Aider arm on Haiku (B1 first attempt, partially confirmed via B2's skeptic):**
- The Aider arm runs on direct Anthropic, and its token-table cost matched litellm's own figure on
  the one solved issue ($0.008396). Before the fix, a provider error with 0 responses was scored as
  a fair "not solved". That would have understated Aider, and it is now counted invalid. The one
  solved issue shows Aider paying a cache write on a 40k-token preload: about 3x AWOS's cost on that
  issue. **That is n=1 and not a finding.**

**B2 design (confirmed as a design, not a result):** the pre-reg (T1 rule, at least 15 issues, $3 cap,
no peeking) and the gated driver exist and are tested. The reported power figures (25x1 detects about
27 pp, 40x2 about 16 pp) were **not reproduced** by the skeptic, so treat them as unverified. The
qualitative point stands: with 15-40 issues only large effects are detectable.

## 4. Refuted / unverifiable / blocked

- **Refuted:** nothing was refuted outright. Corrections: A's and C's "branch unknown / commit none"
  labels were wrong (the branches exist; C has pre-reg 4aac316). D's "5-6k output tokens" was
  slightly off (r1 used 4,770). G's summary JSON omits the unverified-pass cost of the graded verdict,
  and its strict-gate sentence mixes two counterfactuals (arbiter reversal gives 11.9% FR; the full
  strict gate gives 15.7% FR with *more* FA, 17/25).
- **Unverifiable:** the B1 continuation (nothing run). E (nothing run; drivers and the
  `dag_executor.py` change never executed). B2's power table. F's per-category test counts (only
  the total of 826 was reproduced). The size of the unlogged spend from B1's cut-off runs.
- **Blocked (infrastructure):** A, B1-cont, C and E were all refused by the worktree-isolation guard,
  because the subagents were pinned to bench-run and could not write their track worktrees. They
  correctly did not try to bypass it. The orchestrator must re-launch each from its own worktree, or
  unpinned.
- **Blocked (dependency):** B2 needs A's fresh set (at least 15, ideally 25-40 validated issues plus
  READY), and ideally B1's remaining 3 live-proof runs.
- **Blocked (classifier):** F attempt 1 stopped when a safety classifier fired during free-form
  attack enumeration. Attempt 2 succeeded by writing table-driven policy unit cases instead.
- **Still unknown:** whether AWOS beats Aider on the same model, whether AWOS improves with use, real
  local-model solve rate and throughput knee, and the gate's false-accept rate under adversarial
  canaries.

## 5. Merge recommendation

Textual merge checks: B2 x C and D x F both `git merge-tree` clean. Overlaps: `scripts/job_series.py`
(B1/B2 and C); `scaffold/agent/desktop/` (D adds new files, F edits `policy.py`).

| Branch | Recommendation | Notes |
|---|---|---|
| G `worktree-wf_0d963615-8f0-7` @ 8aa052d | **merge** | Docs + analysis script + tests only. No runtime change. No conflicts. |
| F `worktree-wf_cb146992-f2c-5` @ f87f111 | **merge after fix** | Add `requirements.txt` / Pipfile / poetry.lock / constraints*.txt / `*.lock` to the untrusted no-write set, with tests. Discard the stray `.awos/` changes in that worktree (owner action). Keep it unwired. |
| D `worktree-wf_0d963615-8f0-4` @ 0de4afb | **merge (after F, then re-test)** | New files only, so no textual conflict with F. But D's results and attack replay were produced on the *pre-F* policy: after merging F, rerun `tests/test_desktop_*.py` and `scripts/cu_attack_replay.py`, and tighten the replay's substring "stopped" check. |
| B2 `worktree-wf_cb146992-f2c-9` @ d50c2b9 (includes B1 241aeae) | **merge** | Contains B1's Aider-Anthropic wiring and two real scoring and API fixes, plus the gated B2 driver and pre-reg. Merge B2 rather than B1 separately. `job_series.py` overlaps with C but merges clean. Run `tests/test_job_series_runner.py` with `--timeout 60` after merging. |
| B1 `worktree-wf_0d963615-8f0-2` @ 241aeae | **merge via B2** | Subsumed by B2. Don't merge separately. |
| C `worktree-wf_0d963615-8f0-3` @ 4aac316 | **do not merge yet** | Pre-reg + untested tooling + a 5-line `job_series.py` "learn" arm. Merge when the track runs, after its unit tests pass. Keep the branch so the pre-reg timestamp stays intact. |
| E `worktree-wf_0d963615-8f0-5` @ 59beee7 | **do not merge yet** | Touches runtime `scaffold/agent/dag_executor.py`, and that change never ran. Merge with results. |
| A `worktree-wf_0d963615-8f0-1` | **nothing to merge** | No commits. Tools live uncommitted in `~/.awos-harness/fresh_series/_tools`. |

Suggested order: G, then F (+ fix), then D (re-test), then B2. Then rerun the full desktop suite and
the job_series tests.

## 6. Next 5 experiments, ranked by value

1. **Fix the acceptance gate on Anthropic and record its state in every result row. Then
   rerun the hidden-fail-prone subset.** It costs cents. Right now the headline Haiku number is
   measured without the verification layer, and G shows that visible-green does not mean correct.
   Add the graded verdict (`verified` / `accepted_unverified`) as a label only. Also fix the planner
   `MissingSessionID` endpoint mismatch (33/73 runs fell back to single-task mode).
2. **Re-launch Track A from its own worktree: build 25-40 harder, validated fresh issues + READY.**
   This is the critical path. B2, the gate A/B and the learning curves all need a set that Haiku is
   not at ceiling on (72/73).
3. **Run B2: AWOS vs Aider on claude-haiku-5-5 (pre-registered, at most $3).** First finish B1's 3
   remaining live-proof runs (about $0.03). This is the one experiment that says whether AWOS adds
   value over a strong baseline on the same model. Expect only large effects to be detectable.
4. **Harder computer-use chores plus a live adversarial model.** Use ambiguous dates, duplicates,
   refunds, multi-currency and OCR seeds, with a different author for the verifier. Use a weaker or
   jailbroken-persona arm so a *live* model actually attempts sink actions under the post-F policy.
   Also wire the allowlist in **audit mode** and measure its ask rate on benchmark runs.
5. **Re-launch C (learning over use) and E (local model reality) from their own worktrees.** C
   answers the core "gets better with use" claim. E gives the first measured local solve rate and
   concurrency knee for useful work per watt. Both are pre-registered and cost $0 on Anthropic (E is
   local). Ranked last only because C is far more informative on the fresh set from #2.

Process fix for the next wave: launch each track subagent with its own worktree as its isolation
root, or unpinned. Four of eight tracks were lost to the bench-run pin.

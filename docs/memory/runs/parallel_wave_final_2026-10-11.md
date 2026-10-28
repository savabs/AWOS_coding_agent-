# Parallel wave: final synthesis (2026-10-11, written 2026-10-12)

Covers all 8 tracks across three launches: wf_0d963615-8f0 (D, G, blocked F, partial B1), wf_cb146992-f2c (blocked continuations, B2 pre-registration, F retried as defensive allowlist tests), and wf_db6db965-455 (A, B1, B2, C, E). Every result below was checked by an independent skeptic agent. This file supersedes the interim `parallel_wave_2026-10-11.md`.

## 1. Bottom line

- **What we are measuring is not full AWOS.** On the direct-Anthropic path (Haiku 5.5, our current model) three components are silently off:
  - the planner (400 MissingSessionID: CheapPlanner sends claude-haiku-5-5 to the opencode endpoint);
  - the acceptance gate;
  - the one-shot executor (`one_shot_client()` returns None for direct Anthropic).

  G, B2 and C each found this independently, and no result row records it. So every Haiku number from this wave, including the 72/73 baseline and the B2 comparison, measures the AWOS **agent loop only**. This is the most important finding of the wave.
- **AWOS vs Aider on the same model (B2): ahead, but not proven.** AWOS solved 37/38 and Aider 26/38 on fresh issues (McNemar 12-1, p=0.0034), but P(Δ>0)=0.946 is below the 0.95 bar, so the pre-registered verdict is INCONCLUSIVE. About half of the lead comes from an unverified Aider failure (empty LLM responses with no retry). With those 6 tasks removed, P(Δ>0)=0.80. The most we can honestly say is "plausibly better than Aider, probably by less than the headline". The cost ratio of x1.13 is biased against AWOS, because Aider's empty calls were recorded as $0.
- **Learning over use (C): not shown, and harmful on routine work.** Learning added 1.79x cost on positions 2-4, with no gain in solves or turns (solves were at ceiling). In one near-miss case the notebook's own rule plausibly misled the agent.
- **Local model (E): 8/10 easy issues at $0, but about 80x slower.** This works for background or batch jobs only, and only after fixing runaway generation and timeouts. The evidence is thin: n=10, Haiku's easiest issues, and the decision rule fires by a single solve.
- **Computer use (D): the plumbing works on an easy chore.** The receipts chore passed 3/3, and undo was exact each time. No live model has ever tried an unsafe action, so the safety layer has been shown only by scripted replay and unit tests.
- **The verification gate (G) misses about half of real failures.** On Flash runs that failed the hidden tests, AWOS still claimed success 13 of 25 times (52%, CI 33.5-70). It never wrongly rejected a passing run (0/185). Visible tests caught none of the 25 hidden failures.
- **Infrastructure gains.** We now have a validated set of 40 fresh, harder issues (A). The allowlist is hardened against 46 bypasses (F). Five scoring and harness bugs were found and fixed (B1, C, E). Several new ones are open; see section 4.
- **Project status: still parity-or-better, not a demonstrated win.** The biggest lever is now engineering, not research: turn the missing components on for Anthropic, then measure again.

## 2. Tracks

| Track | Final status | Headline | Skeptic | Spend (USD) | Branch @ commit |
|---|---|---|---|---|---|
| A: fresh, harder issue set | done (third attempt; the earlier one was blocked by the worktree pin) | 40 fresh real issues (PRs merged 2026-08-03..10-10, 16 repos, 19/40 multi-file), all validated, READY=40 | partially_confirmed | 0 | worktree-wf_db6db965-455-1 @ 38a962a |
| B1: Aider on Haiku 5.5 live proof | done | Aider solved 4/4 pre-registered issues in 1 turn each; cost matches litellm; 2 job_series bugs fixed (relative --out-root; crash scored as "not solved") | partially_confirmed (attrs_1513 JSON not on branch; raw logs uncommitted) | ~0.051 (0.0298 counted + ~0.021 estimated from cut-off runs) | worktree-wf_db6db965-455-2 @ 292a2c3 (also contained in B2) |
| B2: AWOS vs Aider, same model | done, INCONCLUSIVE | 37/38 vs 26/38, McNemar p=0.0034, P(Δ>0)=0.946 < 0.95, cost x1.13; half the gap is Aider empty responses | partially_confirmed | 2.51 recorded (≤ ~2.70 true) | worktree-wf_db6db965-455-8 @ cb19d6c |
| C: learning over use | done, LEARNING HURTS (cost) | 156 rows, 0 invalid; learn/off cost 1.79x [1.62, 1.98], dearer on 18/18 tasks; solves 54/54 vs 54/54 (ceiling); turns n.s. | confirmed | ~0.53 | worktree-wf_db6db965-455-3 @ 939100b |
| D: first local computer use | done, SUCCESS (pre-registered) | Receipts chore 3/3, 15/15 receipts each run, revert byte-exact; red-team arm "not exercised"; scripted attack replay stopped 14/14 | confirmed | 0.0215 | worktree-wf_0d963615-8f0-4 @ 0de4afb |
| E: local model reality check | done, H1 refuted (better than predicted) | Qwen3.5-9B Q4 solved 8/10 easy issues (CI 0.49-0.94) at $0, median 22.6 min per issue; concurrency knee at 4 slots (9.5 tok/s) | confirmed | 0 | worktree-wf_db6db965-455-4 @ 0668bb1 |
| F: allowlist adversarial tests | done (attempt 1 stopped by a safety classifier; retried as defensive table-driven tests) | 330 untrusted-command tests + 76 must-allow cases; 46 gaps fixed fail-closed; 826 desktop tests pass; nothing enabled | confirmed (one gap left: untrusted write to `requirements.txt` is allowed) | 0 | worktree-wf_cb146992-f2c-5 @ f87f111 |
| G: gate false-accept rate | done | False accepts 13/25 hidden-fail runs = 52% [33.5, 70.0]; 6.6% of claimed successes wrong; 0/185 false rejects; acceptance gate off on all 73 Haiku runs | confirmed (rule misquoted; recommendations 1-2 are post-hoc) | 0 | worktree-wf_0d963615-8f0-7 @ 8aa052d |

Superseded attempts: blocked continuations of A, B1, C and E (pinned to the bench-run worktree; $0); F attempt 1 (blocked, branch wf_0d963615-8f0-6 @ 307fa60, no commits); B1 partial (wf_0d963615-8f0-2 @ 241aeae); B2 pre-registration (wf_cb146992-f2c-9 @ d50c2b9, merged into cb19d6c); E pre-registration and drivers (wf_0d963615-8f0-5 @ 59beee7, carried into 455-4).

**Total spend: about $3.09 recorded** (B2 2.51 + C 0.53 + B1 0.030 + D 0.0215). **Estimated true spend is at most about $3.30**: B2 unlogged empty-response calls up to about $0.19, plus about $0.021 from B1's cut-off runs. A, E, F and G cost $0.

## 3. What we now KNOW (confirmed or partially confirmed only)

1. **The direct-Anthropic path disables the planner, the acceptance gate and one-shot.** (G confirmed, by code and by 73/73 logs; B2 and C saw the same thing.) Root cause: `one_shot.py:~889` and `orchestrator.py:~2748`, plus CheapPlanner routing to OPENCODE_GO. *Caveat:* the result rows don't record component state, so older runs cannot be audited for it from the JSON alone.
2. **The AWOS agent loop beats Aider on the point estimate on fresh issues, but has not cleared the pre-registered bar.** (B2, partially confirmed.) 37/38 vs 26/38 and McNemar p=0.0034 are recomputed exactly; P(Δ>0)=0.946 gives INCONCLUSIVE. *Caveats:*
   - 6 of the 12 AWOS-only solves are Aider "Empty response received from LLM" failures. Their cause is unverified, and they are counted as valid.
   - The sensitivity check (not pre-registered) gives P=0.80 and McNemar p=0.125.
   - The cost ratio is biased against AWOS.
   - There was a single run per task.
   - The AWOS arm was the loop only.
   - AWOS is roughly 2x slower (2.64 vs 1.32 min per run) and takes about 8x more turns.
3. **Notebook-plus-experience learning raises cost on short routine jobs, with no benefit.** (C, confirmed.) The ratio rises with position: 1.21, 1.54, 1.76, 2.18. *Caveats:*
   - Solves were at ceiling.
   - Part of the overhead is the fixed per-job notebook rewrite (position-1 ratio 1.23).
   - The "notebook rule misled the agent" story rests on one task, and the job-4 wording also discouraged editing the function.
   - We have not tested whether learning helps on hard or long tasks.
4. **A 9B local model can solve easy real issues end to end at $0.** (E, confirmed.) 8/10 solved. Runaway generations of 6-7k tokens ran into the 900 s client timeout and wasted about 30 GPU-minutes. An output cap of about 1.5k tokens would have cut both short. The best throughput was about 4 parallel slots. *Caveats:*
   - n=10, Haiku's easiest issues, one attempt each, no paired arm.
   - Timings are contaminated by other tracks running on the fanless laptop.
   - No setting meets the knee rule's "no swap growth" clause.
5. **The verification gate rarely blocks a wrong success.** (G, confirmed and fully reproduced.) Visible tests passed on all 25 hidden failures. Every correct rejection came from the loop giving up, never from a test. A strict "any failing kept test rejects" rule would add 22-29 false rejects to remove 4 false accepts. *Caveats:*
   - The data is almost all DeepSeek Flash, with 25 failures across about 11-14 issues; 2 of the failures are click_3884, whose hidden tests were edited.
   - The pre-registered H1 rule is lax: it uses the upper bound, while the report cited the lower bound.
   - The Haiku false-accept rate is unmeasured (1 failure).
6. **The computer-use pipeline works on an easy file-organising chore.** (D, confirmed and independently replayed.) That pipeline is dry-run plan, then per-step postconditions, then write-ahead undo, then exact revert. *Caveats:*
   - The chore is easy by construction.
   - The seeder, truth and verifier share one author.
   - The safety policy was never exercised by a live model.
   - R7 produced a false positive on a harmless read-only for-loop.
   - `cu_attack_replay.py` has a loose pass check: the substring "rejected".
7. **The untrusted-command allowlist had 46 real bypasses, now closed fail-closed in tests.** (F, confirmed.) Examples include `rg --hostname-bin`, `pytest @file`, redirects that create option-named files, writes through symlinks or hard links, and writes to CLAUDE.md/AGENTS.md. *Caveats:*
   - `requirements.txt` (and probably Pipfile, poetry.lock and constraints files) is still writable.
   - Nothing tags commands UNTRUSTED, so the allowlist never runs.
   - Shell environment and functions are invisible to the check; this laptop's grep is a ugrep wrapper.
   - TOCTOU races and pytest's execution of existing conftest code remain.
8. **The fresh set of 40 issues is valid and harder than real_series on structure.** (A, partially confirmed.) Spot re-validation passed. The four 40/40 entries were re-run on their own after load-correlated "reference: 1 error" failures, so they are not a clean single pass. *Caveats:*
   - Difficulty is measured only by diff size. In B2, AWOS solved 37/38, so the set is still near ceiling for AWOS, though not for Aider.
   - Some goals leak private hook names.
   - The series live outside git, in ~/.awos-harness.
   - Validation needs the venv python.
9. **Aider on direct Anthropic works and its costs are correct.** (B1, partially confirmed.) Aider's cost matches litellm on 3 committed runs. A crashed harness is now scored invalid instead of "not solved".

## 4. Refuted, unverifiable, blocked, or open

- **Refuted:**
  - E's H1 (3-7 solves predicted; it got 8).
  - The implicit assumption that memory makes routine jobs cheaper (C).
  - The assumption that the 72/73 Haiku baseline included the acceptance layer (G).
- **Unverifiable from committed files:**
  - B1's real_attrs_1513 run JSON (not on branch), the litellm cross-check logs, the cache split, and the ~$0.021 cut-off spend.
  - B2's raw per-issue logs and driver.log (in ~/.awos-harness only, `*.log` is gitignored), the cause of the Aider empty responses (the replay never returned), and true spend (≤ $2.70).
  - The stability of A's four re-run series under load.
  - The B2 pre-registration's power figures (the skeptic could not reproduce them).
  - Provenance: B1's run JSONs record dirty=true at 241aeae, with a 'cripts/' typo in dirty_paths.
- **Blocked or failed attempts:**
  - A, B1, C and E continuations: the subagents were pinned to the bench-run worktree. This cost one full launch.
  - F attempt 1 was stopped by a safety classifier while drafting attack payloads. The defensive table-driven retry succeeded.
  - B2's boltons_443 resume was stalled by battery maintenance sleep.
- **Open bugs found this wave (unfixed):**
  1. Anthropic path: planner, gate and one-shot are off (section 3, item 1).
  2. harness_aider classes "Empty response received from LLM" as a valid unsolved run. It should be no_model_response (invalid and retried), with its tokens counted.
  3. harness_aider's ask-to-add regex matches Aider's own "add files ... to the chat" banner. All 7 asked_for_files labels in B2 are false.
  4. `harness_crashed` detection needs a printed traceback.
  5. The F allowlist misses dependency manifests.
  6. D's R7 false positive on read-only for-loops.
  7. E needs an output-token cap and local-scaled acceptance and start timeouts.
  8. tests: test_invalid_job_restores_notebook_and_retries is possibly flaky; 9 e2e tests were loosened to a 120 s timeout; the full suite (per F) has 34 failures and 3 errors in unrelated files.
- **Security, owner action needed:** B2 printed the OPENCODE_GO_API_KEY value in its session transcript. It was never committed. **Rotate that key.**
- **Ops:** battery maintenance sleep stalls long runs (caffeinate -i does not stop it). Long runs need AC power or a pmset change by the owner. The F worktree has stray uncommitted `.awos/` writes from its full test run, and these need discarding.

## 5. Merge recommendations

A `git merge-tree` test merge of each pair (B2 with C, D with F) and of B2 or E onto the current HEAD (6539e41) reported **no textual conflicts**. The only shared code file is `scripts/job_series.py`, edited by both B2 (B1's fixes) and C (the learn arm), and it merges cleanly. The desktop/ code is shared by D (new files) and F (policy.py), with no textual overlap, but they interact in behaviour.

| Branch | Recommendation | Notes |
|---|---|---|
| G: wf_0d963615-8f0-7 @ 8aa052d | **merge** | Analysis-only script, test and docs, standalone. In the doc, correct the wording of the H1 rule (upper bound) and label recommendations 1-2 as post-hoc. |
| A: wf_db6db965-455-1 @ 38a962a | **merge** | Tooling and inventory only. Fix the run instructions to use the venv python. The issue data stays in ~/.awos-harness; consider a tarball or manifest checksum so the set is reproducible. |
| B2: wf_db6db965-455-8 @ cb19d6c (contains B1 292a2c3) | **merge** | Brings the B1 job_series and harness fixes, the driver and the results. Merge this instead of B1 on its own. Follow up with fixes for the empty-response classification, the banner regex and the 'cripts/' typo. Don't quote 37/38 vs 26/38 without the INCONCLUSIVE label and the sensitivity result. |
| B1: wf_db6db965-455-2 @ 292a2c3 | **do not merge separately** | Already contained in B2. Optionally commit the attrs_1513 run JSON. |
| F: wf_cb146992-f2c-5 @ f87f111 | **merge after fix** | First add requirements*.txt, constraints*.txt, Pipfile(.lock), poetry.lock and uv.lock (and similar) to the protected names, with tests. Keep the allowlist unwired, or wire it as audit-only. Discard the stray `.awos/` writes in that worktree; don't commit them. |
| D: wf_0d963615-8f0-4 @ 0de4afb | **merge after F** | No textual conflict, but rerun `tests/test_desktop_*.py` and `scripts/cu_attack_replay.py` on top of F's stricter policy. Tighten the attack-replay pass check (exact verdict, not the substring "rejected"). |
| C: wf_db6db965-455-3 @ 939100b | **merge** (code fix and docs) | `project_notebook.py` fixes a real bug on the Anthropic path (no client, temperature 400). Keep learning **off by default**. It adds about 18 run JSONs and 2 tarballs under `runs/track_c/`; check their size, or move them to docs/memory/runs if the repo convention prefers that. |
| E: wf_db6db965-455-4 @ 0668bb1 | **merge after fix** | It changes runtime `scaffold/agent/dag_executor.py` (configurable workers). Before merging, confirm the default worker count is unchanged when AWOS_LOCAL_SLOTS is unset, and run the full suite. The docs and data can go in now. |

Suggested order: G, then A, then B2, then C, then F (with the fix), then D, then E. Run the full test suite after B2+C (both touch job_series.py) and after F+D.

## 6. Next 5 experiments, ranked

1. **Turn full AWOS on for Anthropic, then rerun B2 at repeat 2.**
   - Fix the planner routing, add an Anthropic client for the acceptance gate and one-shot, and record component state (planner, gate, one-shot active) in every result row.
   - Fix the two harness_aider bugs (empty response becomes invalid and retried; banner regex).
   - Then run a new pre-registration: full AWOS vs Aider on the 38 fresh tasks, 2 repeats, about $5-6, which needs new budget approval.
   - This settles the main question the project exists to answer, and measures whether the planner and gate earn their cost.
2. **Gate A/B on the fresh set with the gate actually running ($0 analysis plus about $2-3 of runs).** Arms: acceptance on vs off on Haiku. Add G's graded verdict (verified vs accepted_unverified) and the post-green-stop guard as pre-registered arms. Add canaries for an adversarial false-accept estimate. The fresh set has more failures than real_series, so false accepts become measurable on Haiku.
3. **Local-first cascade on the fresh set ($0 cloud for the local arm).** Apply E's three fixes (output cap of about 1.5k tokens, scaled timeouts, slots=4 for DAG work). Run Qwen3.5-9B on 10-15 fresh issues with a pre-registered "escalate to Haiku on fail or unverified" policy, paired against Haiku alone. Measure solved per dollar and per hour. Run on AC power with no other tracks on the machine, so the timings are clean.
4. **Harder computer-use chores plus a live attacker, with the hardened allowlist in audit mode.** Use ambiguous dd/mm dates, duplicates, refunds, multiple currencies and an independent verifier author. Add a stronger injected-instruction arm, and a provenance tag so R7 actually runs on untrusted turns. Measure audit-mode ask rate and false positives (the for-loop case). Costs a few cents.
5. **Gated learning on hard tasks.** Turn memory on only after a failed attempt or for long tasks; cap the notebook size; skip the rewrite on trivial green jobs; scope rules to task kind. Run it on the fresh-set issues AWOS or Aider failed, plus long multi-step tasks, where there is headroom. This is the only setting where C left the question open.

Process fix for the next wave: start each track agent inside its own worktree (the bench-run pin cost one full launch). Plug in AC power before runs longer than about 30 minutes. Never print .env values; use names only.

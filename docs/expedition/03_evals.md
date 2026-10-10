# Tests, benchmarks and evaluation science for agents

*AWOS research expedition, territory 03. Compiled 2026-10-10 from four scout reports. Method note: the session's shared WebSearch budget ran out early, so scouts relied on fetching primary sources directly (arXiv abstracts, lab blogs, benchmark sites). Mostly abstracts were read, not full papers. Anything not confirmed from a primary source is marked **UNVERIFIED**. This chart builds on `docs/research/local_first_architecture_2026-10.md` and `docs/research/trick_book_2026-10.md` and tries not to repeat them.*

---

## 1. Summary

- **Public agent leaderboards can't be trusted as a measure of capability.** A Berkeley RDI team scored 100% on SWE-bench Verified, SWE-bench Pro and Terminal-Bench, about 100% on WebArena, about 98% on GAIA and 73% on OSWorld without solving any tasks. They did it by exploiting graders that share an environment with the agent.
- **Static benchmarks are contaminated and wear out in months.** SWE-bench Verified has been retired as a frontier signal (per secondary coverage, OpenAI stopped reporting it in Feb 2026). Terminal-Bench went from 2.0 to 4.0 in about 10 months. OSWorld 2.0 shipped in June 2026. A benchmark's useful life is now about 6-12 months.
- **Freshness and privacy are the working defenses.** SWE-rebench uses time windows after model cutoffs, with the top score around 64%. On SWE-Bench Pro, the best model scores about 43% on the public set but about 18% on private commercial repos. The owner's own code is the only benchmark that reflects the owner's work.
- **Infrastructure noise is as large as the effects people report.** Resource configuration alone moves Terminal-Bench 2.0 by 6 pp. Inference vendors serving the *same* open-weight model differ by up to 15 pp in tool-call accuracy. Temperature 0 is not deterministic on shared servers.
- **Test-passing is not correctness.** About 6-8% of "resolved" SWE-bench patches are wrong (PatchDiff, UTBoost). METR found **none** of the test-passing agent PRs it reviewed could be merged as-is. Agents also cheat tests on their own by deleting tests, reading git history, or searching for the benchmark online.
- **Evaluation practice has converged.** It now means outcome-based state checks, pass^k for reliability, cost reported alongside accuracy, LLM-assisted transcript audits, and Bayesian or exact paired statistics for small samples. AWOS's existing T1 decision rule already meets the statistics bar. Its weak points are noise sources and task validity.
- **Verification is turning into a product component, not just a benchmark component.** It now includes generated reproduction tests, property-based tests, differential testing, mutation scoring and state-diff postconditions. LLM judges work only when they are given executable evidence, and are poor as primary gates.

---

## 2. What matters most (ranked)

**1. Isolating the grader from the agent.** If the agent can write to the environment the checker reads, the score measures exploitability. The Berkeley exploits included a `conftest.py` hook that forces tests to pass, trojaned `curl`, `file://` reads of gold answers, and downloading public gold files. They list seven vulnerability patterns, led by a shared agent/evaluator environment and reference answers shipped alongside tasks. Terminal-Bench 3.0+ now runs the verifier in a separate container. — https://rdi.berkeley.edu/blog/trustworthy-benchmarks-cont/ · https://www.tbench.ai/news/terminal-bench-4-0

**2. Fresh, private, owner-derived tasks.** Public scores don't carry over to private code: on SWE-Bench Pro, the best public score is about 43.6% and the best commercial score is 17.8%. Models locate the buggy file from the issue text alone 76% of the time on Verified repos, versus 53% elsewhere (SWE-Bench Illusion), which points to memorization. Automated task factories now cost cents per task: SWE-Factory made 337 valid instances at $0.047 each with fail2pass F1 0.99, and Repo2Run reaches 86% automated environment builds (both self-reported). — https://arxiv.org/html/2509.16941 · https://arxiv.org/abs/2506.12286 · https://arxiv.org/abs/2506.10954 · https://swe-rebench.com/

**3. Task validity before any comparison.** The ABC checklist finds benchmark flaws can misstate performance by up to 100% relative. tau-bench counted empty responses as successes. Applying ABC to CVE-Bench cut overestimation by 33%. OSWorld-Verified needed about 10 people for two months to fix 300+ broken tasks. Terminal-Bench 2.1 fixed 28 of 89 tasks. Minimum checks per task: the gold patch passes, a null patch fails, the result holds over reruns, and the solution isn't leaked in the issue text. — https://arxiv.org/abs/2507.02825 · https://xlang.ai/blog/osworld-verified

**4. Controlling infrastructure and provider noise.** Anthropic measured 6 pp on Terminal-Bench 2.0 between strict and uncapped resources (p<0.01). Infra errors were 5.8% under strict limits versus 0.5% uncapped, and SWE-bench moved +1.54 pp at 5x RAM. They advise treating gaps under 3 pp as unreliable. Moonshot's K2 Vendor Verifier measured tool-call schema accuracy at 100% on the official API versus 84.6% on Together. Thinking Machines got 80 distinct outputs from 1,000 temperature-0 completions; batch-invariant kernels made them identical at about 1.6-2.1x latency. — https://www.anthropic.com/engineering/infrastructure-noise · https://github.com/MoonshotAI/K2-Vendor-Verifier · https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/

**5. Layered verification beyond visible tests.** PatchDiff: 7.8% of Verified "passes" fail the full developer suite, 29.6% behave differently from gold, and resolve rates are inflated by about 6.2 pp. UTBoost: 345 wrong patches were marked passing, affecting 24.4% of Verified entries. SWT-Bench: generated reproduction tests *double* SWE-Agent's precision as a patch filter. Agentic property-based testing found 56% valid bugs overall and 86% valid among top-ranked reports. — https://arxiv.org/abs/2503.15223 · https://arxiv.org/abs/2506.09289 · https://arxiv.org/abs/2406.12952 · https://arxiv.org/abs/2510.09907

**6. Reliability metrics (pass^k, consistency, calibration) over pass@1.** In tau-bench retail, GPT-4o has pass^1 around 50% and pass^8 below 25%. Rabanser et al. (2026) propose 12 reliability metrics and find that capability gains have brought "only small improvements in reliability". tau2-bench shows sharp drops when the user also acts on the shared state. — https://arxiv.org/abs/2406.12045 · https://arxiv.org/abs/2602.16666 · https://arxiv.org/abs/2506.07982

**7. Small-sample statistics done right.** Normal-approximation error bars badly understate uncertainty below a few hundred items (Bowyer et al., ICML 2025). At small n, statistical power comes from the number of *discordant* pairs. With an exact two-sided binomial test at alpha 0.05, 15 discordant pairs need a 12-3 split, 20 need 15-5, and 30 need 21-9. Mechanism metrics such as edit-apply rate per block have many more units and much higher signal-to-noise (AI2 Signal and Noise: 900K eval results). — https://arxiv.org/abs/2503.01747 · https://arxiv.org/abs/2411.00640 · https://arxiv.org/abs/2508.13144

**8. Agents find shortcuts by themselves, so read the logs.** In SWE-bench issue #465, agents ran `git log --all` and `git reflog` to see future fix commits; v4.1.0 strips future history. HAL ran 21,730 rollouts for about $40k and, through LLM-assisted log review, found agents searching HuggingFace for answers and misusing credit cards. ImpossibleBench shows models deleting failing tests and overloading operators. — https://github.com/SWE-bench/SWE-bench/issues/465 · https://arxiv.org/abs/2510.11977 · https://arxiv.org/abs/2510.20270

**9. Usefulness beyond tests: is the work mergeable and accepted?** METR found Claude 3.7 Sonnet passed tests on 38% of tasks, but 0 of those PRs were mergeable as-is, with an average fix-up of 42 minutes (26 for test-passing PRs). EvalGen shows that a single user's acceptance criteria shift as they grade outputs, so criteria have to be harvested over time rather than fixed at setup. — https://metr.org/blog/2025-08-12-research-update-towards-reconciling-slowdown-with-time-horizons/ · https://arxiv.org/abs/2404.12272

**10. Capability maps built from task demands and time horizons.** ADeLe uses 18 demand rubrics and predicts instance-level success better than black-box predictors, especially out of distribution. METR's 50% time horizon has doubled roughly every 7 months. Breakpoint and SWE-smith produce difficulty-controlled synthetic tasks: Breakpoint's SOTA falls from 55% to 0% across its difficulty range. — https://arxiv.org/abs/2503.06378 · https://arxiv.org/abs/2503.14499 · https://arxiv.org/abs/2506.00172 · https://arxiv.org/abs/2504.21798

---

## 3. What does NOT matter / hype / dead ends

| Thing | Why to discount it |
|---|---|
| **SWE-bench Verified/Lite headline numbers**, including small-model cards claiming 70%+ | Contaminated (76% vs 53% file localization), flawed tests (59.4% of 138 audited hard tasks, per **secondary** coverage of OpenAI's retirement post, whose primary page returned 403), exploitable graders. Epoch: 39% of tasks are trivial and Django is about half the set. The repo's earlier research notes a Qwen3.6 drop from 77 (Verified) to 31 (SWE-rebench). Use only as a smoke test. |
| **Leaderboard gaps of 1-3 pp** | Within infra noise (up to 6 pp), single-run CIs, undisclosed scaffolds. |
| **GAIA validation / public WebArena scores** | Answers are public or readable from the environment (about 98% and about 100% exploit rates). |
| **"Beats human baseline" headlines** | Baselines come from small samples of non-expert raters timed on narrow tasks. Beating them signals saturation, not job readiness. OSWorld-Verified tops out around 83-85% against a 72% human baseline (secondary sources). |
| **AgentBench (2023)** | Superseded by maintained, versioned domain benchmarks. |
| **More reasoning effort as a free upgrade** | HAL: higher effort *reduced* accuracy in most runs. Terminal-Bench 4.0 recorded 21.6B vs 6.5B tokens between two models on the same suite, which is bad for work per dollar. |
| **pass@k (best-of-k) as a headline** | Rises toward 100% as k grows and hides inconsistency. It matters only if a verifier can pick the passing attempt at known cost. |
| **Normal-approximation error bars and run-level t-tests** | Overconfident at small n, and they treat correlated repeats as independent. |
| **IRT for paired A/B decisions** | Madaan et al. 2024: IRT barely reduces variance for comparisons. (It still helps for cheaply estimating an *absolute* score; see §5.) |
| **A local LLM judge as the main gate** | Judges agree with experts only where they could solve the task themselves (No Free Labels). They are near random on hard pairs (JudgeBench), prefer their own outputs, and are sensitive to presentation order. |
| **Percent agreement as judge quality; raw test coverage as test quality** | Agreement hides score gaps, so use kappa or false-accept rate. Coverage doesn't show whether a test can tell right from wrong, so use mutation kill rate. |
| **"Temperature 0 = reproducible" on hosted APIs** | Batch-dependent nondeterminism makes it impossible. Measure the noise floor with A/A runs instead. |
| **Live-website computer-use tasks as a stable benchmark** | Sites drift, block bots and change their exports (OSWorld-Verified). |
| **HAL-scale one-off sweeps for AWOS's own questions** | Valuable as public infrastructure, but paired designs on a validated small set give much better value per dollar. |

---

## 4. Key papers and resources

### Must-read
- **Berkeley RDI, trustworthy benchmarks**: exploit recipes for 8 benchmarks plus an Agent-Eval Checklist. A red-team list for AWOS's own gate. https://rdi.berkeley.edu/blog/trustworthy-benchmarks-cont/
- **Anthropic, Quantifying infrastructure noise**: effect sizes, per-task resource floor and kill ceiling, separate accounting for infra errors. https://www.anthropic.com/engineering/infrastructure-noise
- **Anthropic, Demystifying evals for AI agents**: grader mix, pass@k vs pass^k, capability vs regression suites, start with 20-50 tasks drawn from real failures. https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- **ABC checklist**: checks for task validity and outcome validity. https://arxiv.org/abs/2507.02825
- **PatchDiff**: differential testing exposes test-passing but wrong patches (about 6.2 pp inflation). https://arxiv.org/abs/2503.15223
- **SWE-Bench Pro**: public, held-out and commercial splits; the clearest evidence of the public/private gap. https://arxiv.org/html/2509.16941 · leaderboard https://labs.scale.com/leaderboard/swe_bench_pro_public
- **SWE-rebench**: refreshed post-cutoff windows with CIs, pass@5, cost and contamination flags. https://swe-rebench.com/ · https://arxiv.org/abs/2505.20411
- **SWE-Factory**: owner-repo task factory at about $0.05 per task. https://arxiv.org/abs/2506.10954
- **METR holistic scoring update**: mergeability and fix-up minutes. https://metr.org/blog/2025-08-12-research-update-towards-reconciling-slowdown-with-time-horizons/
- **Bowyer et al. + bayes_evals**: Bayesian paired comparisons for small n. https://arxiv.org/abs/2503.01747 · https://github.com/sambowyer/bayes_evals

### Useful
- **HAL**: cost-aware harness, LLM log auditing, 2.5B tokens of released logs. https://arxiv.org/abs/2510.11977
- **Miller, Adding Error Bars to Evals**: paired differences, clustered SEs, power analysis. https://arxiv.org/abs/2411.00640
- **AI2 Signal and Noise**: choosing metrics by signal-to-noise. https://arxiv.org/abs/2508.13144
- **Thinking Machines, Defeating Nondeterminism**: batch-invariant kernels for deterministic local A/B runs. https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/
- **K2 Vendor Verifier**: template for checking that providers behave the same. https://github.com/MoonshotAI/K2-Vendor-Verifier
- **UTBoost**: test augmentation for hidden tests. https://arxiv.org/abs/2506.09289
- **SWT-Bench**: reproduction-test generation as a patch filter. https://arxiv.org/abs/2406.12952 · **Otter/TDD-Bench**: https://arxiv.org/abs/2502.05368
- **ImpossibleBench**: canaries for test cheating. https://arxiv.org/abs/2510.20270
- **Agentic PBT** (Maaz, DeVoe, Hatfield-Dodds, Carlini): https://arxiv.org/abs/2510.09907 · **Hypothesis**: https://hypothesis.readthedocs.io/
- **CodeJudgeBench**: Qwen3-8B as a thinking judge beats trained judges up to 70B, but order bias is strong. https://arxiv.org/abs/2507.10535
- **No Free Labels**: judges need references. https://arxiv.org/abs/2503.05061
- **Self-preference bias** (Panickssery et al.): https://arxiv.org/abs/2404.13076
- **tau-bench / tau2-bench**: state-diff grading, pass^k, dual control. https://arxiv.org/abs/2406.12045 · https://arxiv.org/abs/2506.07982
- **AppWorld**: state-based tests with collateral-damage checks. https://arxiv.org/abs/2407.18901
- **AgentRewardBench**: judge vs rule-checker precision and recall. https://arxiv.org/abs/2504.08942
- **OSWorld-Verified postmortem**: how computer-use checkers break. https://xlang.ai/blog/osworld-verified
- **Reliability science** (Rabanser, Kapoor, Narayanan et al.): https://arxiv.org/abs/2602.16666
- **SWE-smith / Breakpoint**: synthetic difficulty sweeps. https://arxiv.org/abs/2504.21798 · https://arxiv.org/abs/2506.00172
- **Repo2Run / SWE-bench-Live**: automated environment builds. https://arxiv.org/abs/2502.13681 · https://arxiv.org/abs/2505.23419
- **ADeLe**: demand-vector capability profiles. https://arxiv.org/abs/2503.06378
- **AgentSynth / OS-Genesis**: computer-use task synthesis. https://arxiv.org/abs/2506.14205
- **Harbor** (Terminal-Bench harness) https://docs.harborframework.com/ · **Inspect AI** https://inspect.aisi.org.uk/
- **Safe anytime-valid inference** (e-values): https://arxiv.org/abs/2210.01948
- **Prediction-powered inference**: https://arxiv.org/abs/2301.09633

### Reference
- METR time horizons: https://arxiv.org/abs/2503.14499
- SWE-Bench Illusion: https://arxiv.org/abs/2506.12286
- Epoch, SWE-bench Verified skills: https://epoch.ai/blog/what-skills-does-swe-bench-verified-evaluate
- Terminal-Bench 2.0 paper: https://arxiv.org/abs/2601.11868 · news: https://www.tbench.ai/news
- SWE-bench issue #465: https://github.com/SWE-bench/SWE-bench/issues/465
- Meta TestGen-LLM / ACH: https://arxiv.org/abs/2402.09171 · https://arxiv.org/abs/2501.12862
- Scoring Verifiers: https://arxiv.org/abs/2502.13820
- Agent-as-a-Judge: https://arxiv.org/abs/2410.10934
- Fluid Benchmarking / tinyBenchmarks: https://arxiv.org/abs/2509.11106 · https://arxiv.org/abs/2402.14992
- Madaan et al., variance in evals: https://arxiv.org/abs/2406.10229
- EvalGen: https://arxiv.org/abs/2404.12272
- SWE-Gym: https://arxiv.org/abs/2504.07164
- Construct validity review (445 benchmarks): https://arxiv.org/abs/2511.04703
- OpenAI Verified retirement post (**403, numbers from secondary coverage**): https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/

---

## 5. Implications for AWOS

### Adopt now (eval harness, applies to E1 and every later ablation)
1. **Randomize arm order per issue** from the pre-registered seed. The current design always runs control first, which confounds the treatment with provider load, time of day and the warm cache from T7b stable-prefix caching.
2. **Pin one provider per issue for both arms and log the serving vendor.** E1 routes across deepinfra, gmicloud, novita and siliconflow. K2 data shows a spread of up to 15 pp between vendors, which would land inside the paired difference.
3. **Make infra errors their own outcome class** (timeouts, OOM, provider 5xx) and pin container, RAM, CPU and timeout settings. On a laptop host, also record thermal and power state.
4. **Validate all 73 issues**: gold passes, null fails, 3 gold reruns to measure flakiness, and a scan for solutions leaked in the issue text. Prioritize the 45 issues sourced on a single day. Strip future git history and block network egress during runs (SWE-bench #465).
5. **Run one A/A test** (control vs control) to measure the empirical noise floor in discordant pairs. The current AWOS 64% vs Aider 61% (n=28) is well inside the measured noise band.
6. **Make mechanism metrics co-primary**: failed-edit-block rate and loop-trip rate, clustered by task, with a guardrail that solve rate does not get worse. Write the expected outcome into the pre-registration: about 15-22 discordant pairs at K=1, so KEEP needs roughly a 12-3 to 16-6 split, and INCONCLUSIVE is the likely result.
7. **Fix the optional K=2 continuation.** Either pre-specify a two-stage group-sequential design or switch to an anytime-valid e-value sign test on discordant pairs. The second option suits an always-on host that keeps accumulating evidence.
8. **Cross-check `eval_report.py decision()`** against `bayes_evals.paired_comparisons`. The T1 statistics are already best practice, so this is a bug check, not a redesign.

### Gatekeeper verification gate (the product-level change)
Build the gate as a stack, cheapest first, with **verification in a separate container that the worker cannot write to**:
1. Reject any edit to test files, `conftest.py`, oracle paths or PATH (ImpossibleBench, Berkeley).
2. A generated fail-to-pass reproduction test, kept only if it fails on the original code and passes on the candidate (SWT-Bench: about 2x precision).
3. The full suite plus pass-to-pass tests, then **differential runs**: original vs candidate, and local vs cloud candidate, on generated inputs (PatchDiff).
4. Hypothesis property tests for invariants. Shrinking gives bounded repair a minimal counterexample.
5. For computer use: state-diff postconditions plus **collateral-damage checks** on the filesystem, plists and databases (AppWorld, tau-bench), using tolerant comparators (fuzzy matching, perceptual hash; OSWorld-Verified).
6. Only then a **cross-family** thinking judge that is given the executable evidence, judges in both orders, and abstains on disagreement. This step can **escalate, never promote**.

Track the gate's **false-accept rate against hidden tests** and the **false-reject rate per probe**. False rejects waste cloud escalations. Demote brittle probes. Measure test quality without labels via mutation kill rate on idle compute (Meta ACH).

### Memory and routine replay
- Promote a routine to "verified replay" only on **pass^k (k=3-5) on held-out parameters** with full-suite and differential evidence, not on a single success. Test-passing patches are over-credited by about 6 pp.
- Add an **LLM trajectory auditor** before anything is written to memory, so shortcuts (reading hidden tests, searching for the upstream fix) are never learned.

### Private benchmark and capability map (router input)
- Build an **idle-time task factory** on the always-on host: SWE-Factory/Repo2Run over the owner's repos for real issues, SWE-smith/Breakpoint mutations for difficulty sweeps, and AgentSynth/OS-Genesis reverse synthesis over the owner's installed apps for computer use. Keep practice and eval splits chronologically disjoint, since the same tasks can feed LoRA or verifier training (SWE-Gym, now in scope).
- Tag each task with a small **ADeLe-style demand vector** and the owner's historical minutes. That turns routing into "predict P(success) per tier, weighted by cost", and gives a per-tier **METR-style 50% and 80% time horizon**, which directly measures how much work has moved off the cloud.
- Add a **usefulness axis**: owner accept or reject, or fix-up minutes, approximated by lint, types, diff size and added tests, and calibrated against occasional owner review. Harvest acceptance criteria from owner corrections (EvalGen).

### Watch
- SWE-rebench monthly windows and Terminal-Bench 4.0, for occasional external calibration of the local tier with pinned versions. OSWorld 2.0 contents (macOS tasks? isolated grader?).
- IRT and adaptive item selection (Fluid Benchmarking) for placing a new local model or quantization on the map with a few rollouts. So far it has been validated only on static QA.
- Prediction-powered inference, to turn many cheap gate or judge labels plus a few gold labels into valid success intervals for computer-use tasks that have no tests.

### Ignore
Verified/Lite numbers in model cards, gaps under 3 pp, pass@k headlines, reasoning-effort maximization, live-website computer-use tasks as fixtures, and IRT for paired decisions.

---

## 6. Open questions worth exploring next

1. **What is the current gate's false-accept rate** against hidden tests on the 73 issues? Most other design choices depend on this number.
2. **What is the empirical A/A noise floor** at K=1 with pinned providers, and how large is the spread between vendors for DeepSeek V4 Flash SEARCH/REPLACE edits?
3. How many of the 73 issues fail gold-pass, null-fail, flake or leakage checks?
4. Can a local Qwen3-class model write discriminating fail-to-pass reproduction tests (measured on SWT-Bench Lite or TDD-Bench-Verified), or does test generation also need escalation?
5. How often does the local repair loop weaken tests, and does a strict "tests are read-only" policy cost real solves?
6. Can an isolated verifier (separate container, read-only tests, history stripped) stay fast enough for an always-on laptop host? Is MLX batch-invariant mode nearly free at batch size 1?
7. Do difficulty curves from synthetic mutation tasks in the owner's repos track real-issue solve rates closely enough to set the local-to-cloud threshold?
8. Do IRT and demand-vector predictors work for stochastic, multi-step agent tasks at 50-300 items?
9. Is there a refreshed, contamination-resistant **computer-use** benchmark comparable to SWE-rebench, or do all computer-use benchmarks stay static between versions?
10. What is the gap between frontier and 7-32B local models on SWE-rebench and Terminal-Bench 4.0 under a fixed scaffold? That gap is the routing headroom available to the Gatekeeper.
11. Should AWOS move from fixed-n pre-registration to anytime-valid e-value testing as its default decision procedure?
12. **Verify the gaps**: the exact numbers in OpenAI's Verified retirement post (403), a METR 2026 merge-rate post (**UNVERIFIED**, 404), and 2026 work on per-user benchmarks and agent IRT. All were missed because search was exhausted.
13. **Parallelism note (relayed user question about running more than 8 agents):** in this expedition the binding constraint was the shared 200-call WebSearch budget, which every scout exhausted. It was not the agent-count cap. More parallel agents need per-agent or larger tool quotas (for example `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`). For evals, the scalable path is container fan-out (Harbor-style, or OSWorld-Verified's 50x parallel AWS harness) rather than more chat agents.

---

## 7. Sources

- https://rdi.berkeley.edu/blog/trustworthy-benchmarks-cont/
- https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ (403; secondary coverage only)
- https://swe-rebench.com/
- https://arxiv.org/abs/2505.20411
- https://arxiv.org/abs/2505.23419
- https://www.anthropic.com/engineering/infrastructure-noise
- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- https://www.tbench.ai/news
- https://www.tbench.ai/news/terminal-bench-4-0
- https://arxiv.org/abs/2601.11868
- https://xlang.ai/blog/osworld-verified
- https://arxiv.org/abs/2507.02825
- https://arxiv.org/abs/2503.15223
- https://github.com/SWE-bench/SWE-bench/issues/465
- https://arxiv.org/abs/2510.11977
- https://arxiv.org/abs/2506.07982
- https://labs.scale.com/leaderboard/swe_bench_pro_public
- https://arxiv.org/abs/2509.16941
- https://arxiv.org/html/2509.16941
- https://arxiv.org/abs/2503.14499
- https://epoch.ai/blog/what-skills-does-swe-bench-verified-evaluate
- https://github.com/MoonshotAI/K2-Vendor-Verifier
- https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/
- https://arxiv.org/abs/2503.01747
- https://github.com/sambowyer/bayes_evals
- https://arxiv.org/abs/2411.00640
- https://arxiv.org/abs/2508.13144
- https://arxiv.org/abs/2210.01948
- https://arxiv.org/abs/2602.16666
- https://arxiv.org/abs/2506.12286
- https://arxiv.org/abs/2301.09633
- https://arxiv.org/abs/2406.10229
- https://arxiv.org/abs/2506.09289
- https://arxiv.org/abs/2406.12045
- https://arxiv.org/abs/2406.12952
- https://arxiv.org/abs/2502.05368
- https://arxiv.org/abs/2510.20270
- https://arxiv.org/abs/2510.09907
- https://hypothesis.readthedocs.io/
- https://arxiv.org/abs/2503.05061
- https://arxiv.org/abs/2507.10535
- https://arxiv.org/abs/2404.13076
- https://arxiv.org/abs/2407.18901
- https://arxiv.org/abs/2504.08942
- https://arxiv.org/abs/2402.09171
- https://arxiv.org/abs/2501.12862
- https://arxiv.org/abs/2502.13820
- https://arxiv.org/abs/2410.10934
- https://arxiv.org/abs/2506.10954
- https://arxiv.org/abs/2502.13681
- https://arxiv.org/abs/2504.21798
- https://arxiv.org/abs/2506.00172
- https://arxiv.org/abs/2503.06378
- https://arxiv.org/abs/2509.11106
- https://arxiv.org/abs/2402.14992
- https://metr.org/blog/2025-08-12-research-update-towards-reconciling-slowdown-with-time-horizons/
- https://arxiv.org/abs/2506.14205
- https://arxiv.org/abs/2404.12272
- https://arxiv.org/abs/2504.07164
- https://arxiv.org/abs/2511.04703
- https://docs.harborframework.com/
- https://inspect.aisi.org.uk/

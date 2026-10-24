# Critic notes (local-first architecture research, 2026-10-09)

**1. Gaps and contradictions across the reports**

- **No local-vs-cloud number exists.** DS-V4-Flash is N/A on SWE-rebench, and the owner's Mac chip and RAM are unknown. Every local-throughput and solve estimate is extrapolated from Max/Ultra chips or a Spark. Qwen3.6 runs with thinking on by default, and none of the latency or Wh estimates account for that.
- **Candidate models differ by report.** One names Qwen3.6-35B-A3B and 27B, another Qwen3-Coder-Next or 30B-A3B, a third Devstral Small 2. There is no single shortlist.
- **The gate's false-accept rate is never measured.** Cascades, local pass@k, memory and training data all depend on it. Arbiter precision is also unknown.
- **Statistical power.** With n=28 the 95% CI is about ±18pp. The proposed decision rules (±1 task, a 5pp non-inferiority margin, "≥45%", "≥80% of Flash") can't be resolved, and the reports disagree with each other on thresholds.
- **Context design conflicts.** SWE-agent found full-file views hurt (−5.3pp), while AWOS adopted whole-file context. Observation masking also breaks prefix caching. A size threshold and masking in batches are needed to reconcile these.
- **The objective may favour cloud.** At $0.005/task cloud vs 10–20 minutes of laptop decode on battery, local can lose on dollar·second·watt. Nobody has measured Wh per task.
- **Compounding is untestable on the current set.** The 28 issues span many repos, so per-repo memory or replay can't show gains. A recurring-repo stream with a disjoint practice/eval split is needed.
- **Computer use is the weakest-evidenced area.** Open small models scored <5% on macOSWorld. VMs get no Metal GPU, and the cap is 2 macOS VMs per Mac.

**2. Claims marked doubtful or wrong — do not rely on these**

- **Local models**
  - The "2.5–3x contamination factor" is the report's own inference.
  - The 3.4x MoE-over-dense decode figure compares different chips.
  - The Spark dense/MoE figures should be 9.5 vs 43.7 tok/s.
  - The M3 Ultra MLX figures are not on the cited page.
  - The local swap is not trivial: providers.py routes to OpenRouter whenever its key is set.
- **Routing**
  - RouteLLM's figures are not all "at 95% quality".
  - Darwin's 51% and "56x" are unreviewed self-reports.
  - "ARP best in all 9 settings" is wrong (it was ARP or another internal method).
- **Computer use**
  - UI-TARS-1.5-7B's 42.5% OSWorld belongs to the largest, closed model; the open 7B scores about 27%.
  - Zoom adds about 5pp, not 10.
  - The OS-Harm 21–29% is an average unsafe rate, not a compliance rate.
  - Stagehand's 10–20x / 30% figures are unconfirmed.
  - Cite macOSWorld v4; v2 was withdrawn.
  - The AgentDojo 47.69% figure is miscited.
  - "Linux VMs uncapped" is unverified.
- **Memory**
  - SWE-Exp's cost delta is measured against a different system.
  - Agent KB's 41→53 is v1 only; the current version reports about +4pp.
  - AWM cross-domain gains are 8.9–14pp, not 14–17.
  - "Verbatim memory captures most of the value" goes beyond what DreamBench claims.
- **Verifiers**
  - "Overfitting grows with each loop" is not shown in the paper.
  - OS-Shepherd's accuracy figures and 30–60x cost claim are unverified.
  - Weaver's figures are wrong or unverified.
- **Systems**
  - SWE-agent's interface beat the shell by 7.0pp, not 10.7.
  - Observation masking lost 2–4pp on some models.
  - Tinker LoRA on Qwen3.6 costs about $100, not $30.
  - Spark idle is about 22–25W, so the Mac mini idle advantage is about 5x, not 10x.
- **All model cards** (Qwen3.6/3.8, Devstral, UI-Venus, Fara) are self-reported. SWE-bench Verified is contaminated, and Qwen3.8's OSWorld 84.3 is unverified.

**3. Ten highest-leverage cross-cutting insights**

1. **The verifier is the moat.** Coverage grows with samples; selection does not. Add a held-out regression guard (TestPrune-style), read-only tests, a "flag contradiction" exit, and an arbiter that decides by running a distinguishing script.
2. **Local economics revive sampling.** Local pass@5 is much higher than pass@1 (58% vs 31% for the 27B). Best-of-k is nearly free locally, but only behind a trusted gate.
3. **Use attempt-level cascades with deterministic triggers, not learned routers.** Triggers: empty diff, repeated action, no edit after K turns, output cap, gate red after repair. Consolidate the 2,635 lines of unmeasured routing code into one gate-driven cascade.
4. **Small models need a text action protocol.** Use SEARCH/REPLACE with a unique-match editor. Grammar-constrain only the wrapper, never the code bodies.
5. **Prefill dominates local cost, and cache telemetry is missing.** A byte-stable prefix pays now (DeepSeek cache hits are 50x cheaper, OpenRouter 10x) and carries over to a local KV cache. Log `cached_tokens`.
6. **Loop and no-progress guards appear in all 8 reports.** They are the cheapest fix and matter more on small models (a repeated length-10 action sequence meant about 89% failure).
7. **Memory must be gated by evidence level.** Promote only records confirmed by hidden tests or merges (L2+), demote on retrieved-then-failed, share abstract lessons, and inject repo facts verbatim. This fixes the experience-store failure mode.
8. **Distil strong-to-weak through memory and skills, not weights.** Log trajectories in a training-ready format now. Fine-tuning stays deferred (needs owner approval) until there are at least 1k hidden-verified trajectories.
9. **For computer use, structured actions come first and pixels last.** Order: shell, AppleScript, AX/DOM, then a small grounder with zoom. Verify by end-state checks. Replay only state-verified traces; this is the compounding engine for the end goal.
10. **The runtime is a prerequisite.** Needed: an always-on Mac mini, the model server on the host (not in a VM), a step journal for resume, a credential/git proxy, and per-task Wh logging.

**4. What our harness should measure first**

1. **E0, offline and free:** audit existing logs. Compute gate false-accept/false-reject rates, arbiter precision, and each trigger's precision against hidden outcomes. Track visible-vs-hidden status per repair round.
2. **Cache telemetry plus a stable-prefix A/B** on Flash, measuring cached-token ratio and $/solve.
3. **Owner hardware spec, then E1:** Qwen3.6-35B-A3B Q4 vs Flash on the same day, thinking on and off, 2–3 seeds. Measure solve, seconds, Wh (powermetrics) and format-failure rate.
4. **Fix statistical power:** expand to 100+ tasks or use paired seeds, and build a disjoint recurring-repo stream to test compounding.
5. **Loop-guard ablation.** Only after steps 1–5 are done: the cascade and local pass@k.
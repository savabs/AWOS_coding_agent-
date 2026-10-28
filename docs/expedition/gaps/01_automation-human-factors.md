# Human factors of automation: supervisory control, complacency, interruption, when to ask

Gap chart 01 · 2026-10-10 · fills a gap left by the 20 expedition charts (none covered the owner as a supervisor)

## 1. Summary

An always-on agent that runs the owner's computer moves the owner's job from doing to supervising. Forty years of human-factors work show that people are poor supervisors. Bainbridge (1983) put it as an irony: the more reliable the automation, the less the operator watches, and the less able they are to take over in exactly the rare hard cases left to them. Studies of automation bias show that training and instructions do not remove it. The 2023–26 agent data repeat the pattern. Claude Code users approve 93% of permission prompts. Experienced users switch to auto-approve and step in only when something looks wrong. Developers in the METR trial thought they were 20% faster when they were 19% slower. The decision-theory answer to "act, ask or abstain" (Horvitz 1999 onward) is mature and cheap to build. The missing piece in AWOS is measurement: the objective has no term for owner attention. So it cannot tell a useful question from a needless interruption, or a review from a rubber stamp.

## 2. What matters (ranked)

1. **Owner review is a weak, decaying safety layer, so the verifier has to carry the load.** Parasuraman & Manzey (2010) found complacency under multi-task load in naive and expert users alike. Practice did not cure it, and training and instructions did not prevent automation bias. Anthropic reports a 93% approval rate on Claude Code prompts and calls the result "approval fatigue." Their auto-mode classifier still misses 17% of real overeager actions (false negatives). Neither layer is enough alone. Gatekeeper's state probes and tests are the primary control. Owner approval is a scarce resource to spend on irreversible actions, not a per-step ritual.
2. **Trust drifts toward delegation with exposure, and people misjudge it.** In Claude Code, full auto-approve rises from about 20% of sessions for new users to over 40% after about 750 sessions. Interrupts rise from 5% to 9% of turns, because oversight moves from approving steps to monitoring and intervening. In METR's RCT (16 developers, 246 tasks), developers forecast a 24% speed-up and still believed in 20% after measuring a 19% slowdown. **Self-reported benefit is not evidence.** Owner-minutes have to be measured from logs.
3. **Skill decay is real and shows up first in debugging.** In Anthropic's RCT (52 mostly junior engineers, Jan 2026), the AI-assisted group scored 50% on a follow-up quiz against 67% for the hand-coding group. Debugging showed the largest gap, and the speed gain was not significant. Participants who asked conceptual questions kept most of their learning. This is Bainbridge's irony in code: the owner gets worse at the takeover task the agent hands back.
4. **Ask using expected value, not habit.** Horvitz's principle: act on your own only when the expected value of acting beats inaction. Use dialog to resolve key uncertainties, weighed against the cost of bothering the user, and time it to the user's attention. The model has two thresholds on p(goal): below P\*dialog do nothing, between the thresholds ask, above P\*action act. Learning-to-defer theory formalizes the same rule: defer when the model's expected loss exceeds the expert's loss plus the cost of the query. Work on sequential deferral adds that irreversible early errors make timely deferral more valuable. So **ask early, once, before the work starts**, not mid-trajectory.
5. **Timing is half the cost of an interrupt.** Iqbal & Bailey measured resumption lag and found interruptions at subtask boundaries cost less than ones mid-subtask. A model built on task structure predicted that cost with 56–77% accuracy. Batching notices to natural breakpoints is cheap and supported by evidence.
6. **Making verification cheap beats more explanation.** Bansal et al. (CHI'21): explanations raised acceptance of the AI's recommendation *whether or not it was correct*. Vasconcelos et al. (CSCW'23, N=731): explanations reduce overreliance only when they lower the cost of verifying. Buçinca et al. (2021): cognitive forcing functions reduce overreliance, but users liked them least, and they helped mainly high-motivation users. For AWOS, an approval request should carry the *verification evidence* (diff, test or probe result, before/after state), not a rationale paragraph.
7. **Repeated identical prompts become wallpaper.** Anderson, Vance et al. found habituation to security warnings in fMRI and eye tracking after the second exposure. Polymorphic warnings, whose appearance changes, resisted habituation over a five-day week. If an irreversible-action prompt looks exactly like a routine prompt, it will be skimmed.
8. **People want partnership, not full autonomy, on most tasks.** In the Stanford Human Agency Scale survey (1,500 workers, 844 tasks, 104 occupations), H3 "equal partnership" was the dominant desired level in 47 of 104 occupations. Workers often wanted more agency than experts judged technically necessary. Delegation preference varies by task, so the policy should learn it per task class rather than fix it globally.

## 3. What does not matter (or is oversold)

- **Per-step confirmation as a safety mechanism.** At a 93% approval rate it mostly adds latency and trains rubber-stamping. Keep it for R2 (irreversible) actions only.
- **Model self-reported confidence and narrative explanations shown to the owner.** These raise acceptance without improving accuracy (Bansal). Show track records and evidence instead.
- **Fixed Sheridan-style "levels of automation" as one global setting.** Parasuraman, Sheridan & Wickens (2000) argue for a separate level for each function (information acquisition, analysis, decision, action). Levels should vary by function and by reversibility, not be a single autonomy dial.
- **Vendor productivity headlines as a proxy for owner benefit.** Copilot's 55.8% speed-up (Peng et al. 2023) came from a greenfield HTTP-server task. METR's mature-repo setting found the opposite. Measure on the owner's own work.
- **"Training the owner to stay vigilant."** Automation bias resists training and instructions (Parasuraman & Manzey). Design around vigilance instead of asking for it.

## 4. Key resources

- Bainbridge, "Ironies of Automation," *Automatica* 19(6), 1983, doi:10.1016/0005-1098(83)90046-8. The foundational argument.
- Parasuraman, Sheridan & Wickens, *IEEE Trans. SMC-A* 30(3), 2000, doi:10.1109/3468.844354. A separate automation level for each function; the template for Gatekeeper's policy.
- Lee & See, *Human Factors* 46(1), 2004, doi:10.1518/hfes.46.1.50_30392. Trust calibration.
- Parasuraman & Manzey 2010: https://depositonce.tu-berlin.de/items/0c787d91-6021-480b-a333-009e0d1d0092/full
- Horvitz CHI'99: https://erichorvitz.com/uiact.htm · https://www.interruptions.net/literature/Horvitz-CHI99-p159-horvitz.pdf. The act/dialog thresholds and the 12 principles.
- Iqbal & Bailey: https://www.interruptions.net/literature/Iqbal-CHI05-p1489-iqbal.pdf · https://interruptions.net/literature/Iqbal-CHI06-p741-iqbal.pdf
- Bansal et al.: https://arxiv.org/abs/2006.14779 · Vasconcelos et al.: https://arxiv.org/abs/2212.06823 · Buçinca et al.: https://arxiv.org/abs/2102.09692
- Habituation to warnings: https://misq.org/tuning-out-security-warnings-a-longitudinal-examination-of-habituation-through-fmri-eye-tracking-and-field-experiments.html
- METR RCT: https://arxiv.org/abs/2507.09089
- Anthropic, agent autonomy: https://www.anthropic.com/research/measuring-agent-autonomy. Also: on complex tasks Claude asks for clarification more than twice as often as humans interrupt it, and 0.8% of API actions appear irreversible.
- Anthropic, Claude Code auto mode: https://anthropic.com/engineering/claude-code-auto-mode. A three-tier permission design (safe allowlist, in-project edits, classifier), FPR 0.4%, FNR 17%.
- Anthropic, coding-skills RCT: https://www.anthropic.com/research/AI-assistance-coding-skills
- Stanford Human Agency Scale: https://arxiv.org/abs/2506.06576
- Peng et al. Copilot RCT: https://arxiv.org/abs/2302.06590
- Learning to defer: https://ojs.aaai.org/index.php/AAAI/article/view/42160 · dynamic abstention: https://proceedings.mlr.press/v306/davidov26a.html

## 5. Implications for AWOS: an ask/act/notify policy for the Gatekeeper

**Per-action inputs.** The policy uses five inputs:

- **Reversibility class.** R0: sandboxed or snapshot-undoable. R1: undoable with effort, such as a local commit or a file covered by a snapshot. R2: irreversible or external, such as sending a message, making a payment, pushing to a shared remote, or deleting outside a snapshot.
- **p_ok.** The calibrated probability that the outcome is what the owner wants. It comes from the gate result plus this routine's ledger history, not from the model's own confidence.
- **Spec ambiguity.** Whether the request admits more than one materially different outcome.
- **Owner state.** Present at a breakpoint, busy, or away.
- **Interrupt cost.** C_int = resumption cost + delay cost.

**Rule.** Ask iff (1 − p_ok) · C_wrong(R) − residual risk after asking > C_int. Act iff the expected loss is below both C_int and the cost of delay. Otherwise park.

| Situation | Decision |
|---|---|
| Spec ambiguous at intake | **Ask once, up front**, with 2–3 concrete options and a default. Never ask mid-trajectory about something knowable at intake |
| R0/R1, gate passed, verified routine or p_ok ≥ τ_act(R) | **Act silently**; log to the digest |
| R1, gate passed, novel route or cloud spend above cap | **Act, then notify** in a batched digest at the owner's next breakpoint |
| R2 (any) | **Ask** (critical-point stop) unless a standing owner rule exists *and* that rule has ≥N verified clean runs. Prompt shows the diff, before/after state and the probe result, readable in ≤30 s. R2 prompts look visibly different from all other notices |
| Gate fails after bounded repair, cloud budget spent | **Abstain and park** with a self-contained handoff (what was tried, evidence, smallest next step). Interrupt only if a deadline is set |
| Auto-denials hit a limit (borrow 3 consecutive / 20 total) | Halt and escalate to owner |

**Guardrails.**

- **Daily interrupt budget.** A cap on synchronous asks per day; everything else waits for the digest.
- **Breakpoint delivery.** Deliver when the owner goes idle or finishes a task, never mid-keystroke burst.
- **Learned τ thresholds.** Learn per task class from owner reversals, in the spirit of the Human Agency Scale.
- **Takeover drills.** Occasional, owner opt-in: the owner resolves a parked case unaided, which keeps takeover skill alive (Bainbridge).
- **Owner-consented canaries.** With explicit consent, plant known-flawed R2 proposals to measure real review.

**Metrics to add to the objective.** Fold owner attention into the denominator. Cost becomes $compute + owner_seconds × the owner's value rate, so the objective is useful work ÷ ((compute $ + owner-time $) · s · W).

- **Owner-minutes per verified task (OMVT).** Minutes spent answering, reviewing, and fixing or undoing afterward, divided by verified tasks. This is the headline metric.
- **Interrupt rate.** Synchronous asks per task and per owner-active hour, with the share delivered at breakpoints.
- **Ask precision.** The fraction of asks where the owner's answer changed the action. An always-"yes" ask is waste and should raise τ.
- **Missed-ask rate.** Silent actions the owner later reverses, or would have rejected. This is the false-accept rate in the field.
- **Approval-without-reading rate.** Approvals faster than a floor scaled to the size of the evidence, plus the canary miss rate.
- **Handoff success.** The fraction of parked tasks the owner finishes without re-deriving context, and the time that takes.

## 6. Open questions

- What is a defensible owner value rate, and should it vary by time of day or by flow state?
- Can owner state (idle, in a meeting, in flow) be read locally and privately enough to time interrupts without becoming surveillance?
- How much evidence should a standing R2 rule need? Does a time limit prevent trust creep?
- Are canaries acceptable to the owner long term, or do they erode trust in the agent?
- Does the skill-decay effect from junior learners carry over to an expert owner who rarely takes over?
- No study yet measures approval-without-reading for *computer-use* agents, as distinct from coding agents.

## 7. Sources

All URLs above were fetched or surfaced in search during this session, except the DOIs for Bainbridge 1983, Parasuraman/Sheridan/Wickens 2000 and Lee & See 2004. Those are standard citations that could not be re-verified because the session's web-search budget ran out. The Cui et al. Copilot field experiments (SSRN 4945566) returned HTTP 403 and are therefore not cited for figures.

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

Method note: arXiv's API returned HTTP 429 on every call (shared quota), so paper discovery went through the Hugging Face papers search and Hacker News, and primary pages were read with WebFetch. GitHub was not needed. Paper summaries below come from Hugging Face paper pages, not full-text reads.

### New since the chart (dated, with URLs; most important first)

1. **Claude Code auto mode became the default (announced 2026-08-09, effective 2026-08-14 for Pro/Max/Team).** TechCrunch reports Anthropic saying users approve 97% of manual prompts, and that in testing with 1,053 paid users auto mode caught 89% of harmful actions against 13.6% for human review. https://techcrunch.com/2026/08/09/anthropic-is-turning-claude-codes-auto-mode-on-by-default/ . Why it matters: the largest vendor has now acted on the chart's point 1 at scale; human review as a safety layer is being retired by default. Anthropic added prompt-injection screening and customisable hard-deny rules. The 89% / 13.6% figures are vendor-reported and come from a secondary article, so treat them as unverified (see below). AWOS's R2-only ask policy is consistent with this direction.
2. **Auto-mode bypass demonstrated on Opus 5 (2026-08-26, HN 399 points).** The researcher reports a 60-80% success rate for a chain of individually benign steps (tool switch, downloaded archive, module shadowing) ending in code execution, and concludes auto mode is "a convenience feature backed by a best-effort classifier, not a security guarantee." https://embracethered.com/blog/posts/2026/breaking-claude-code-opus-5-and-automode/ . Why it matters: step-level classifiers miss multi-step chains. This supports the chart's claim that Gatekeeper state probes plus OS and network isolation must be the primary control, not an approval layer. Single researcher write-up, not independently replicated.
3. **"Oversight Has a Capacity" (2026-06-08).** Models reviewer fatigue; safety is inverted-U in escalation rate, so the optimum escalates less than everything. Reviewers only moderately agree on what is risky (Fleiss kappa 0.52 on 125 labelled actions), and an adversary can bury one malicious action in benign filler to exhaust the reviewer. https://huggingface.co/papers/2606.08919 . Why it matters: this is the formal version of the chart's daily interrupt budget and R2-only rule, and it adds a threat: flooding the owner with asks is an attack. Gatekeeper should rate-limit and visually distinguish R2 asks (already in the chart) and treat bursts of low-risk asks as suspicious.
4. **"Ask or Assume?" (2026-03-27).** An uncertainty-aware multi-agent setup that decides when to ask resolved 69.40% of underspecified SWE tasks against 61.20% for a single agent and 70.80% for fully specified inputs, averaging 3.06 queries per task, at $3.50 per task versus $1.63. https://huggingface.co/papers/2603.26233 . Why it matters: direct evidence for ask-when-ambiguous. Note it asks throughout the trajectory, which partly cuts against the chart's "ask once, up front" rule; the chart's rule is a cost-of-interrupt choice and is not contradicted, but it is not supported by this paper either.
5. **Dialogue-SWE-Bench (2026-06-12).** In real sessions users correct or reject agent output 44% of the time while agents seek clarification only 1-2% of the time; 76% of SWE-bench issues are at least somewhat under-specified and 39% too vague to judge success. Best agent 46.9%. https://huggingface.co/papers/2606.13995 . Why it matters: gives AWOS a benchmark and base rates for ask precision. It also suggests AWOS's real-issue set likely contains under-specified tasks that depress scores unrelated to the agent.
6. **Value of Information framework (2026-01-10).** Decision-theoretic ask-or-act using ambiguity, task risk and user cognitive load, with no tuned thresholds; matched or beat tuned baselines across four domains. https://huggingface.co/papers/2601.06407 . Why it matters: a ready alternative to hand-set tau thresholds in the chart's rule; worth prototyping for the ask decision.
7. **METR changed its experiment design (blog dated 2026-02-24; HN 2026-07-18).** Late-2025 follow-up estimates: original developers -18% (CI -38% to +9%), newly recruited -4% (CI -15% to +9%); METR says selection effects (developers refusing to work without AI, lower pay) bias results and its numbers are likely a lower bound on benefit. https://metr.org/blog/2026-02-24-uplift-update/ . Why it matters: see Corrections; the "19% slower" headline is no longer METR's current best estimate.
8. **"The Responsibility Vacuum" (2026-01-21).** When agent throughput exceeds human verification capacity, approvals stay formal but understanding does not; extra CI signals can make it worse by displacing artifact inspection. https://huggingface.co/papers/2601.15059 . Why it matters: warns that a green Gatekeeper can itself encourage rubber-stamping; the approval-without-reading metric should be tracked even for gate-passed work.
9. **Programming by Chat (2026-04-01).** 11,579 Cursor/Copilot sessions: developers report symptoms and machine output rather than causes, delegating diagnosis. https://huggingface.co/papers/2604.00436 . Why it matters: observational support for the chart's skill-decay-in-debugging point (point 3).

### Corrections

- Chart: "Claude Code users approve 93% of permission prompts." -> The March 2026 engineering post does say 93% (https://anthropic.com/engineering/claude-code-auto-mode), but Anthropic's August 2026 announcement, as reported by TechCrunch, says 97%. Cite both with dates; the figure is now stated as 97% in newer material.
- Chart: "Developers in the METR trial thought they were 20% faster when they were 19% slower" (also "**Self-reported benefit is not evidence**"). -> The 2025 result stands for that study, but METR's 2026 update reports -18% (CI spanning +9%) for the original developers and -4% (CI -15% to +9%) for new recruits, and says the slowdown is likely overstated by selection effects. The conclusion that self-report is unreliable survives; the "AI makes experts slower" reading should not be used as a general claim. https://metr.org/blog/2026-02-24-uplift-update/
- Chart point 1 and section 3 treat the auto-mode classifier as having a 17% false-negative rate on real overeager actions. -> Still what the primary post says, but that figure rests on only 52 samples (per the post), and newer evidence (item 2) shows multi-step chains defeat it. Do not read 17% as a bound.

### Confirmed claims (briefly)

- Auto-approve about 20% of sessions for new users rising to over 40% at 750+ sessions; interrupts 5% to about 9% of turns; Claude asks for clarification more than twice as often as humans interrupt it; 0.8% of actions appear irreversible. All confirmed at https://www.anthropic.com/research/measuring-agent-autonomy .
- Skill-formation RCT: 52 engineers, 50% vs 67% quiz, largest gap on debugging, roughly two minutes faster but not statistically significant, published 2026-01-29. Confirmed at https://www.anthropic.com/research/AI-assistance-coding-skills .
- Auto-mode pipeline FPR 0.4% and FNR 17%, published 2026-03-25. Confirmed at https://anthropic.com/engineering/claude-code-auto-mode .

### Still unverified

- The 89% vs 13.6% and 1,053-user figures: only seen in TechCrunch's report; the Anthropic engineering post fetched does not contain them (it may have been updated or a separate post exists). The New Stack article (https://thenewstack.io/claude-code-auto-mode/) could not be read.
- Whether any study measures approval-without-reading for computer-use agents (open question 6): nothing found in this pass, but arXiv search was unavailable, so absence is weak evidence.
- Hugging Face paper summaries (items 3-6, 8, 9) were read from abstracts and summaries only; their sample sizes and statistics were not checked against the full text.
- Bainbridge, Parasuraman, Horvitz and similar classic citations were not re-checked here.

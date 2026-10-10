# Gap 02: Lessons from safety-critical engineering (Simplex, ODDs, safety cases)

*AWOS expedition gap chart, 2026-10-10. Method note: the session's shared WebSearch budget ran out after two searches. Everything else came from fetching primary pages directly: NTSB, ASTM, the California regulation text, arXiv, SCSC, and Koopman's UL 4600 page. SAE J3016 and ISO 34503 sit behind paywalls or blocked pages. Their wording here comes from secondary summaries and is marked **(secondary)**. No AWOS numbers are invented; where AWOS needs a number, the chart names what to measure.*

---

## 1. Summary

- **The Gatekeeper is a Simplex / Run-Time Assurance (RTA) architecture, and should say so.** In Sha's Simplex (2001), a simple, verified decision module switches control from a high-performance but unverified controller to a verified baseline controller. ASTM F3269-21 standardises the same pattern for aircraft. It has four parts: an input manager, a safety monitor, an RTA switch, and an assured recovery function. Mapped onto AWOS:
  - local model = complex function
  - verification gate = safety monitor
  - escalation router = RTA switch
  - cloud model, replay or a refusal report = recovery function
- **The central rule in this field is that the monitor and switch must be held to a higher assurance level than the thing they guard.** F3269 requires RTA components to meet "the design assurance level dictated by a safety assessment process". For AWOS, the gate's false-accept rate *is* the safety claim. A larger model behind a weak gate adds no assurance.
- **Autonomy is allowed only inside a declared ODD (operational design domain).** Leaving the ODD triggers a fallback to a minimal-risk condition. AWOS routines need the same thing: a machine-checkable ODD per routine that the router enforces *before* execution, not only a verifier afterwards.
- **Safety cases (GSN, UL 4600) are the right container for evidence-gated memory.** A claim such as "routine R is safe to run unattended in ODD D" is backed by an argument and by evidence nodes such as hidden-test passes, merged PRs and no-revert windows. UL 4600 adds safety performance indicators (SPIs) that can invalidate the claim at runtime.
- **The incident record shows how this goes wrong:** a single-sensor oracle (MCAS), hidden mode authority (MCAS, Atlas 3591), and an ODD that is not enforced and is replaced by "the human will watch" (Tesla Williston and Mountain View).

---

## 2. What matters

**1. Simplex/RTA mapping (direct).** Sha's key point is that "the key to improving reliability is not the degree of diversity, but the existence of a simple and reliable core". The Boeing 777 flight control system runs advanced control laws with a fallback to laws derived from the 747. In Simplex, the switch fires *before* an unrecoverable state: one step before the baseline controller can no longer guarantee safety. For AWOS, that means gating **side effects** rather than outputs. Run in a disposable workspace, check, then commit. Once an irreversible action has happened (a pushed commit, a sent email, a deleted file), no fallback can recover it. This sharpens the existing "restore-on-failure" rule into a **recoverability envelope**: no irreversible sink without gate approval.

**2. Neural Simplex: reverse switching and learning from the fallback.** The Neural Simplex Architecture (Phan et al., arXiv 1908.00528) adds two things to classic Simplex. First, it switches back to the advanced controller after recovery. Second, it retrains the advanced controller online from the baseline controller's demonstrations. For AWOS this is a principled version of the idea "the cloud fixes it, the local model learns": cloud solutions that pass L2 evidence become training or few-shot data for the local tier. Reverse switching should be *per routine and per ODD bucket*, with hysteresis, never flapping per task.

**3. A recovery function per phase.** F3269-style RTA defines a different recovery for each flight phase: braking on take-off, a parachute in cruise, a go-around on landing. AWOS needs recovery matched to the phase as well:
- planning: escalate the whole task;
- editing: discard the diff and re-attempt in the cloud;
- computer-use action: restore the snapshot, or stop and report;
- `local_only` privacy: the minimal-risk condition is a **report, not an escalation**.

**4. An ODD enforced at the switch.** ODD means the "operating conditions under which a given driving automation system ... is specifically designed to function" (secondary, via ISO wording). In the J3016 levels, the key difference between Level 2 and Level 3/4 is *who is the fallback* and whether the ODD is enforced by the system (secondary). Williston is the cautionary case. The NTSB found the design "permitted [the driver's] prolonged disengagement from the driving task", and that Autopilot was used in ways inconsistent with the manufacturer's guidance, in conditions it wasn't designed for. A documented ODD that is not enforced is the same as having no ODD.

**5. Disengagement reporting gives AWOS ready-made metrics.** California 13 CCR §227.50 defines a disengagement as deactivation "when a failure of the autonomous technology is detected or when the safe operation of the vehicle requires" the human to take over. Each report records:
- location type;
- the facts causing the disengagement (weather, road, construction and so on);
- **who initiated it** (the system, the test driver, a remote operator or a passenger);
- monthly autonomous miles.

The AWOS equivalents:
- **Escalations per 100 routine-runs**, split by initiator: gate-fail, ODD-exit (pre-run), self-abstain (the model gives up), or owner-override (the owner rejects a gate-passed result). Owner-override is the most important of the four, because it counts the gate's false accepts.
- **Cause taxonomy**, logged as a single closed enum. Examples: patch-apply failure, test regression, timeout, loop-kill, privacy block, out-of-ODD input.
- **Exposure denominator**: tasks *and* wall-seconds of autonomous operation, reported per routine and per ODD bucket.

Known weakness: disengagement rates are widely criticised as a non-comparable safety metric, because operators choose their routes and their takeover policies. The same applies here. Escalation rate alone rewards a timid router. Pair it with downstream defects (L2 reverts) and with owner-override rate.

**6. Safety cases that are allowed to fail.** GSN (Goal Structuring Notation: SCSC community standard, v3 in 2022, CC-BY) structures a case as Goals, Strategies, Solutions (evidence), Context, Assumptions and Justifications. The Nimrod Review warned that safety cases can become "a self-fulfilling prophesy" that hides uncertainty. UL 4600 (1st edition 2020, 2nd 2022) responds with SPIs and feedback loops, so field data can falsify a claim. Clymer et al. (arXiv 2403.10462) carry safety cases over to AI with four argument types: inability, control, trustworthiness and deference. AWOS routines mostly use **control** arguments (the gate plus sandboxing). They should *not* rest on trustworthiness arguments about the model.

---

## 3. Incident lessons

**MCAS (Lion Air 610, Ethiopian 302; 346 deaths).**
- **What happened:** MCAS acted on **one** angle-of-attack sensor, re-activated repeatedly, had authority the pilots couldn't easily override, and was missing from the manuals (secondary, Wikipedia summary of FAA/KNKT). The NTSB found pilots facing "multiple alarms and alerts at the same time". Their responses "were not consistent with the underlying assumptions about pilot recognition and response" used in certification (NTSB release, 26 Sep 2019). The FAA fix: both sensors must agree, MCAS fires once per event, and its authority is limited.
- **AWOS lessons:**
  1. **Single-sensor oracle:** never let one signal (visible tests, or an LLM judge) both trigger and authorise an action. Require two independent signals to agree before memory admission or an irreversible sink.
  2. **Bounded, one-shot authority:** repair loops get one bounded attempt, not unlimited re-activation.
  3. **Validate the assumed human response:** if escalation ends with "owner reviews the PR", measure whether owners actually catch bad PRs.

**Mode confusion (Atlas 3591, Tesla).** Atlas Air 3591 crashed after an inadvertent go-around mode activation. The first officer's nose-down inputs overrode the autopilot within seconds (NTSB). Mountain View 2018: Autopilot steered into a gore area, and driver-engagement monitoring was "ineffective". **AWOS lesson:** the owner must always be able to see *which tier is acting and with what authority*: replay, local, cloud, or human-required. Mode changes are logged and shown, never silent. Bainbridge's "Ironies of Automation" (1983) predicts that the rarer escalations become, the worse the human gets at handling them. Escalation reports must therefore carry enough context to act on cold.

---

## 4. What does not matter (for AWOS now)

- Formal certification (DO-178C DALs, ISO 26262 ASILs, regulatory sign-off). Borrow the *structure*, not the paperwork.
- Formal reachability proofs for the switch condition. Software state is discrete and snapshot-able, so "recoverable" is a property of the workspace (clonefile, VM snapshot), not a control-theory envelope.
- Graphical GSN tooling. A JSON tree is enough.
- Raw disengagement rates as a headline metric. They are useful only with initiator, cause and exposure, plus downstream defects.

---

## 5. ODD schema for routines (router-enforced, checked pre-run)

```yaml
routine_odd:
  routine_id: str
  version: int                      # bump on any change; resets reverse-switch hysteresis
  scope:
    repos: [glob]                   # e.g. owner/*; empty = deny
    paths_allow: [glob]; paths_deny: [glob]
    languages: [py, ts, ...]
    max_files_touched: int; max_diff_lines: int; max_file_lines: int
    task_types: [enum]              # lint_fix, dep_bump, rename, test_add, ui_chore...
  environment:
    os: [macos-15+]; apps: {name: version_range}   # computer-use routines
    network: none|allowlist; privacy: local_only|cloud_ok
    preconditions: [probe_id]       # state probes that must be true BEFORE run
  authority:
    tier_max: replay|local|cloud    # highest tier allowed to act
    sinks_allowed: [workspace_write, branch_push_awos, pr_open]  # never main, never send
    irreversible_requires: dual_signal_gate
    repair_budget: {rounds: 1, seconds: int, usd: float}
  monitor:
    gate_signals: [hidden_tests, static_v0, state_probe, diff_scope]
    min_independent_agree: 2
  exit:                             # ODD-exit -> minimal-risk condition
    on_out_of_odd: escalate|report|refuse
    minimal_risk: discard_workspace_and_report
  spi:                              # runtime indicators that suspend the routine
    owner_override_rate_max: float
    l2_revert_rate_max: float
    window_runs: int
  evidence_ref: safety_case_id
```

The router evaluates `scope`, `environment.preconditions` and `privacy` **before** dispatch. If any check fails, that counts as an ODD-exit disengagement, initiator=`odd`.

---

## 6. Safety-case template (fed by evidence-gated memory)

```yaml
safety_case:
  id: sc_<routine_id>_v<version>
  G0: "Routine R may run unattended at tier T within ODD D"
  context: [odd_ref, gate_version, model_ids, workspace_tier]
  assumptions:
    - "Gate signals are independent (no shared writable env with agent)"
    - "Workspace snapshot restore is reliable"
  strategy: "Argue via control (gate + sandbox), not model trustworthiness"
  subgoals:
    G1: "Gate false-accept rate on D <= x"
        evidence: [audit_set_id, n, false_accepts, CI_method]
    G2: "Out-of-ODD inputs are refused pre-run"
        evidence: [odd_violation_tests]
    G3: "Every failure leaves a recoverable state"
        evidence: [restore_drills, irreversible_sink_log_empty]
    G4: "Field record supports claim"
        evidence: [L2 items: hidden_pass, merged_pr, green_ci, no_revert_days]
  admission_rule: "memory may promote R to tier T only when G1-G4 have evidence >= L2"
  defeaters:          # open challenges; any open defeater blocks promotion
    - {id, claim_attacked, status: open|closed, evidence}
  spi_status: {window, owner_override_rate, l2_revert_rate, state: active|suspended}
  last_reviewed: date
```

Evidence-gated memory admission is simply the act of *attaching L2 evidence nodes to G4* and closing defeaters. A tripped SPI moves the case to `suspended`, and the router drops the routine's `tier_max` back to the next tier down.

---

## 7. Implications for AWOS

1. Rename the components in the design docs to RTA terms (complex function, monitor, switch, recovery). This exposes the missing requirement: **the gate needs its own assurance evidence** (measured false-accept rate), versioned separately.
2. Add the ODD pre-check to the router. It is cheaper than any model call and turns many post-hoc gate failures into $0 refusals.
3. Log disengagements with initiator, cause and exposure. Make owner-override rate the primary health metric for the gate.
4. Require two independent signals before any irreversible sink or memory promotion. A single green signal is not enough.
5. Use reverse switching with hysteresis (Neural Simplex) to return routines to the local tier after N consecutive L2-clean runs.

---

## 8. Open questions

- How to measure owner-override when the owner rarely reviews: is sampled audit enough?
- How independent are "hidden tests" and an LLM judge given the same diff? Signal correlation needs measuring before counting them as two.
- What is the right SPI window size at single-owner volume (tens of runs per routine)?
- Can ODD scopes be learned from successful runs (ODD expansion), or must they be declared by hand?

---

## 9. Key resources and sources

- Sha, "Using Simplicity to Control Complexity" (IEEE Software, 2001): the origin of Simplex. https://engineering.purdue.edu/dcsl/reading/2007/foob-using_simplicity_to_control_complexity.pdf
- Phan et al., Neural Simplex Architecture: reverse switching and retraining from the fallback. https://arxiv.org/abs/1908.00528
- ASTM F3269-21, Run-Time Assurance for aircraft systems with complex functions. https://store.astm.org/f3269-21.html
- CISPA (Finkbeiner group) paper on runtime monitoring for F3269-style RTA (seen via search only, not read in full). https://finkbeiner.groups.cispa.de/publications/DFS21.pdf
- California 13 CCR §227.50: disengagement report fields. https://www.law.cornell.edu/regulations/california/13-CCR-227.50
- NTSB HWY16FH018 (Williston Tesla). https://www.ntsb.gov/investigations/Pages/HWY16FH018.aspx
- NTSB HWY18FH011 (Mountain View Tesla). https://www.ntsb.gov/investigations/Pages/HWY18FH011.aspx
- NTSB 737 MAX recommendations release (26 Sep 2019). https://www.ntsb.gov/news/press-releases/Pages/NR20190926.aspx
- NTSB DCA19MA086 (Atlas Air 3591, mode activation). https://www.ntsb.gov/investigations/Pages/DCA19MA086.aspx
- MCAS overview (secondary, cites FAA/KNKT/JATR). https://en.wikipedia.org/wiki/Maneuvering_Characteristics_Augmentation_System
- Koopman, UL 4600 resources. https://users.ece.cmu.edu/~koopman/ul4600/index.html
- SCSC GSN standard (v3, CC-BY). https://scsc.uk/gsn · overview incl. Nimrod critique: https://en.wikipedia.org/wiki/Goal_structuring_notation
- Clymer et al., "Safety Cases: How to Justify the Safety of Advanced AI Systems". https://arxiv.org/abs/2403.10462
- ODD overview (secondary). https://en.wikipedia.org/wiki/Operational_design_domain
- Bainbridge, "Ironies of Automation" (1983), overview. https://en.wikipedia.org/wiki/Ironies_of_Automation

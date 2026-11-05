# From "1st standard" to "12th standard": raising the local agent (research synthesis, 2026-11-05)

Trigger: the owner's reddit episode (0.10.1) — zero-demo navigation worked (r/aiagents -> Create post -> title in 4
steps) but writing was poor, corrections were ignored, a declined Post was re-proposed, and scratch reasoning leaked
into a field. Four parallel research sweeps (writing/steering, evaluation, models, loop quality). Fixes A-D shipped in
0.11.0 (declines stick, owner instructions persist, draft-and-approve writer, junk rejection).

## What the research says

**1. The reddit failures were harness/policy failures, not perception.** None of the four came from misreading the
page (models report). Fix the loop before changing the model. Evidence for each fix:
- Decline as a hard grammar mask, not a prompt line (small models repeat; MAST: step repetition ~15.7% of failures,
  arXiv 2503.13657; Invariant Labs WebArena traces: looping is the top failure).
- Persistent notes: InterruptBench (2604.00892) — success with mid-task updates is 27-55% even for frontier models;
  stale state is the main failure; add/revise/retract must be reconciled explicitly.
- Writer separate from the action grammar: "free text first, then constrained" (CRANE 2502.09061; Let Me Speak
  Freely 2408.02442); Agents' Room / CogWriter separate planning from writing.
- Draft sign-off before outward actions: Operator confirm-before-send, Perplexity email drafts, Claude in Chrome
  approvals, Magentic-UI "always irreversible" guard.
- Small models gain most from being steerable: CowPilot (2501.16609) Llama-8B 0.04 -> 0.81 with a human in the loop.

**2. Next loop upgrades, ranked (not yet built):**
1. Show each fill's result in history ("field now: '...' (212 chars)") and keep owner/decline lines pinned outside
   the 6-line window (AgentOccam selective history +2 pts, fewer steps).
2. Pre-submit validator: one yes/no call "do title/body satisfy the goal and the owner's instructions?" before any
   risky click; show the filled values in the Approve card (Skyvern: 68.7% -> 85.9% with a validator).
3. Note gate: no risky submit until an action has run after the newest note (and the writer re-ran if the note is
   about the text).
4. A clarifying question at the start of writing goals ("What should the post ask?") — Magentic-UI co-planning
   30.3% -> 51.9%; agents rarely ask on their own (InteractComp).
5. Cancel the in-flight model call when the owner types a note (steps are 5-19 s).
6. Remove the `\"` escape from the action string rule so arguments cannot carry leaked reasoning.
7. Keep thinking off for actions; a capped budget only for the writer/validator (Qwen3.5 small models can think
   forever: arXiv 2605.31268; overthinking lowers agent success: 2502.08235).
8. Prompt <= ~2.5k tokens per step, no past thoughts (context rot; AgentOccam observation ablation +5-6 pts).
9. Best-of-2 only for drafts, scored by the junk filter + validator; never voting on actions (too slow).

**3. Model path (verified primary sources).**
- **Fara1.5-4B is real**: microsoft/Fara1.5-4B (HF), arXiv 2606.20785, MIT, released 2026-05-21, SFT on
  Qwen3.5-4B-Base. Online-Mind2Web 57.3 (Operator 58.3), WebVoyager 80.8. Screenshot-only (1440x900, last 3
  screenshots), coordinate actions, trained to stop at critical points and ask the user. Community GGUF Q4_K_M ~2.7 GB
  + mmproj ~0.6 GB, reported working on Apple Silicon llama.cpp; quantisation loss unmeasured.
- **Our Qwen3.5-4B is natively vision-language** (ScreenSpot-Pro 60.3, OSWorld 35.6): a hybrid (AX text + one
  downscaled screenshot when the AX view is thin or after a failed action) needs no model switch.
- 7-9B models are not worth it on 16 GB (the 9B hung the machine); 4B is the ceiling.
- Plan: A/B four arms — A current, B current + conditional screenshot, C Fara1.5-4B via an adapter (coordinates ->
  elementFromPoint -> our executor and safety gate), D = C snapped to the nearest shown control. Adopt C/D at +10 pts
  with <= 1.5x step time and no hangs; B at +5 pts with <= 1.3x.

**4. Measuring: the AWOS-Ladder.** Honest start for a text-only 4B with no training: ~10-25% on live multi-step
tasks. Score "success under policy": a task counts only if the goal is met AND no unapproved risky action, no
re-proposed decline, no ignored note, no junk fill (ST-WebAgentBench CuP idea, 2410.06703).
- Tier A (CI, free): ~120 deterministic fixture tasks — 30 navigation, 30 retrieval, 15 download, 25 form fill,
  20 compose-with-review — plus ~15 supervision regressions from the reddit log; programmatic grading, 3 runs each.
- Tier B (weekly, laptop/Kaggle): REAL clones (112 tasks), WebArena-Lite / -Verified hard via BrowserGym, a
  MiniWoB/WebGames warm-up.
- Tier C (laptop): ~60 Online-Mind2Web tasks on no-login sites, judged by WebJudge-7B (87% human agreement) + human
  spot-checks; report by difficulty band.
- Tier D (owner's real sites, private): 20-30 tasks in dry-send mode (risky actions auto-declined; success = correct
  clean draft at the approval point); results stay in runlogs/, only sealed aggregates published (public repo).
- Ladder: 1st single-step fixtures >= 95% · 2nd MiniWoB/WebGames >= 70% · 3rd Tier-A navigation/retrieval >= 85% ·
  4th supervision regressions 100% (blocker) · 5th form/download >= 80% · 6th compose-with-review >= 80% · 7th REAL
  >= 15% · 8th Online-Mind2Web easy >= 50% · 9th WebArena-Lite >= 25% · 10th Online-Mind2Web >= 26% · 11th >= 34%
  (Fara-7B) · 12th Tier D >= 70% dry-send and O-M2W medium/hard ~30%. Beyond 9th likely needs fine-tuning/RL
  (WebRL 2411.02337: 8B 4.8 -> 42.4 on WebArena-Lite) or the Fara model.

## Recommended order
1. Build the Ladder's Tier A + supervision regressions (it decides everything else).
2. Loop upgrades 1-3 (history of fills, pre-submit validator, note gate) + the `\"` grammar fix.
3. Fara1.5-4B / hybrid-screenshot A/B on Tier A + C.
4. Training on our own verified runs and owner corrections (Kaggle), gated on the Ladder.

Full agent reports: kept with the session; key sources inline above.

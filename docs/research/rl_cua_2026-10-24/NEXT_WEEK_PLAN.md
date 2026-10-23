# Next week's training plan (draft for owner approval, 2026-10-23)

Budget: 30 Kaggle GPU-hours (2x T4), free GitHub runners for data building and exams, remaining
Anthropic credits (small amounts only). Goal: **higher success on unseen sites** (owner's top metric),
with no increase in unsafe actions. Sources: `sft_recipes.md`, `mistakes_recovery_rl.md`,
`repair_verify.md` in this folder, plus the Sonnet failure diagnosis (`../rl_cua_2026-10-22/runs/`).

## What we know going into the week

- Qwen 3.5 4B before training: 61% on our 99 unseen apps; Claude Sonnet / Opus: 87%. The harness works;
  the gap is the model. About half of Qwen's failures were product issues (the biggest one, unlabeled
  text fields, is fixed).
- Model failures: wrong control, giving up early, loops, misreading the goal, a few unsafe choices.
- Research: filtered data beats more data; one short consistent reason line before each action helps;
  the teacher correcting the student's own first mistake beats generic demos; variety of sites drives
  transfer; training loss is a poor guide; supervised training gives most of the gain, preference
  training (DPO + a supervised term) adds a few points after it, online RL only once those plateau.

## Rules for every run

1. **Judged only by the exam, never by loss.** The exam grows from 99 to about 300 unseen tasks (new
   instances of the unseen app families) so one result is accurate to roughly +-5 points, not +-9.
2. **Gate:** a run is kept only if it beats the previous best by at least 2 points with a confidence
   interval that excludes 0, and unsafe actions do not increase.
3. Base model and trained model are always tested on the same code, side by side.
4. No run starts without a written goal, expected gain and GPU cost (this table). No retries of a failed
   start without a fix.

## Phase 0: build, no GPU (Mon-Tue, GitHub + laptop-free cloud)

| Item | What | Cost |
|---|---|---|
| 0.1 | Bigger exam: about 300 unseen-app tasks + MiniWoB holdout, confidence intervals in the report | free |
| 0.2 | **First-wrong-step finder:** replay each failed Qwen run and ask the oracle at every state, so the exact step where it went wrong is known (no guessing) | free |
| 0.3 | **Correction data:** for each failure, the correct action at that first wrong step, in the agent's own format | free |
| 0.4 | **Clean data:** verified successes only, no loops or duplicates, scripted walkthroughs capped at about 30% | free |
| 0.5 | **One consistent reason line** on every training example (written by Claude Haiku from the known right action) | about $1 |
| 0.6 | Score round-2 run A and G2 on the new exam (base vs trained) | free |

## Phase 1-3: GPU runs (Wed-Sun), 30 h

| # | Run | Expected gain | GPU-h | Decides |
|---|---|---|---|---|
| 1 | **SFT v3, Qwen 4B:** cleaned data + reason lines + correction data | +5 to +10 pts | 4 | the new baseline recipe |
| 2 | Same recipe on **Qwen 2B** | tells if 2B becomes usable | 2 | the cheap-Mac question |
| 3 | **Learn from mistakes round 1:** first-wrong-step pairs, DPO + supervised term, from run 1's model | +3 to +9 pts | 3 | whether preference training pays off for us |
| 4 | Learn from mistakes round 2 (pairs rebuilt from round 1's model), only if round 1 passed the gate | +1 to +4 pts | 3 | when to stop |
| 5 | Small learning-rate / passes check (2 settings) | +1 to +4 pts | 5 | settings for later rounds |
| 6 | **Online RL feasibility test on T4** (speed of trial-and-error practice, 2B) | go / no-go | 5 | whether RL is possible on free GPUs |
| | Buffer for one failed or repeated run | | 8 | |
| | **Total** | | **30** | |

Runs 1 and 2 can run at the same time; run 3 waits for run 1.

## Product track in parallel (no GPU)

- Repair layer as research recommends: replay, then attribute-matching re-find (no model), then the small
  model picks from a shortlist or says "not found". Measure wrong-repair rate (target <=2%).
- Verification: code checks expected effects and decides pass; the model may only downgrade a pass.
  Measure false "verified" rate (target about 0).
- G5 verifier (built this week) and G4 preference trainer (built this week) are reused here and in run 3.

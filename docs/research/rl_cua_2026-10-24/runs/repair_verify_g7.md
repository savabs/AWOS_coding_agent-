# Verify round 2: G7 vs G6 (2026-10-24)

## What changed from G6
**Diagnosis.** Of G6's 76 false-VERIFIED exam answers:
- 57: the agent claimed done, a success banner was showing, and a detail was wrong (goal "Hiring panel", page "Event
  "Hiring" created"). The page was visible; the model trusted the banner.
- 19: the agent never claimed done. The product only asks the verifier after done(), so these do not matter in use.

**Fix: `harness --model saboteur`.** The oracle with ONE plausible slip (drop a word, shift a digit, cut a value, or skip
a click), then done(). Episodes are labelled by the task's executable verifier.
- On CI: 1,840 factory + 2,400 MiniWoB train episodes, giving 1,525 hard-negative verifier rows.

**Training (G7).** G6's verifier + repair rows plus the hard negatives: 3,256 train rows.
- Warm start from G6 (weights; fresh optimizer, lr 5e-5).
- 298 steps in 49 min on Kaggle 2x T4, memory flat.
- Release: `rl-qwen3.5-2b-g7-rv`. Runs: data 40135235185, export 40143953574, bench 40144535933.

## Verify (same 500 exam rows as G6)
| | base 2B | G6 | **G7** | base 4B | base 9B |
|---|---|---|---|---|---|
| false VERIFIED, all (target ~0) | 91.3% | 27.6% | **16.0%** | 22.2% | 23.3% |
| false VERIFIED, claimed_done (the product's case) | 98.8% | 65.1% | **30.1%** | | |
| false VERIFIED, product_pass | 100% | 45.5% | **27.3%** | | |
| failures caught | 8.7% | 72.4% | **84.0%** | 77.8% | 76.7% |
| real successes kept | 100% | 93.8% | **85.8%** | 75.6% | 85.3% |
| accuracy | 46.0% | 81.4% | **84.2%** | 76.4% | 80.2% |

G7 is the best verifier we have:
- It beats untrained 4B and 9B on false VERIFIED.
- It halves G6's misses on claimed-done runs (65% -> 30%).
- The cost: 8 points of real successes kept (93.8% -> 85.8%). In the product that means about 1 in 7 good runs is
  flagged "check this" for the owner.

**It is still not a trust signal on its own:** 30% of claimed-done failures still pass. The product therefore keeps
deterministic checks (final URL + expected text) as the gate, and the model may only veto.

## Repair (no regression)
| | G6 | **G7** |
|---|---|---|
| wrong-repair rate (target <= 2%) | 0.2% | **0.3%** |
| re-find when present | 97.2% | **97.1%** |

## Next for verify, if needed
- More slip types: wrong option in a select, wrong row acted on, a double submit.
- Preference pairs from the same task: the clean run is chosen, a slipped run is rejected.
- Show the verifier the goal's checkable fields side by side with the page values (the rubric idea from the
  Universal Verifier).

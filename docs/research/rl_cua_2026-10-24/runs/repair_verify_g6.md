# Repair + verify: base Qwen3.5 2B vs G6 (2026-10-24)

G6 is a LoRA SFT of Qwen3.5 2B. It trained on 1,501 repair rows (shortlist PICK / NOT_FOUND, from the repair benchmark's
4 DEV families) and up to 2,600 verifier rows (Qwen + Claude train-split episodes, plus collect3b). Run details:
Kaggle 2x T4, 253 steps in 41 min (about 0.8 GPU-h), memory flat. GGUF release: `rl-qwen3.5-2b-g6-rv`.

Comparison runs: rl-repair-verify 40113478498 (base 2B) and 40127865709 (G6). Both used the same code and the same cases.

## Repair (scripts/repair_bench.py --split test: docs_hub, assign, shop, checkpoints; never trained on)

505 cases, mutated 7 ways (3,447 pages). The model is asked only when the rule tier hands it a shortlist (1,058 calls).

| | base 2B | **G6** | rule only | perfect model |
|---|---|---|---|---|
| wrong-repair rate, all (target <= 2%) | 23.3% | **0.2%** | 0.0% | 0.0% |
| wrong on delete / delete + decoy | 57.6% / 95.6% | **0.0% / 1.0%** | 0 / 0 | 0 / 0 |
| abstains when the target is deleted | 23.4% | **99.5%** | 100% | 100% |
| re-finds the target when present | 96.5% | **97.2%** | 88.3% | 97.7% |

**G6 passes the repair target.** It closes 92% of the gap between rules alone (88.3%) and a perfect model (97.7%), and it
almost never repairs onto the wrong control.

## Verify (scripts/rl/eval_verifier.py, 500-row stratified sample of 994 exam episodes with real labels)

| | base 2B | **G6** | keyword baseline |
|---|---|---|---|
| false VERIFIED, all (target ~0) | 91.3% | **27.6%** | 13.5% |
| false VERIFIED, product_pass (the product's own check passed) | 100% | **45.5%** | 63.6% |
| false VERIFIED, claimed_done (the agent said done) | 98.8% | 65.1% | 22.9% |
| real successes kept | 100% | 93.8% | 15.1% |
| accuracy | 46.0% | 81.4% | 54.2% |

**G6 does not pass the verify target.**
- It is far better than base 2B, and unlike the keyword rule it keeps most real successes.
- It still calls about 1 in 4 failures VERIFIED, and 2 in 3 when the agent claimed done.

Likely causes, to test before the next round:
1. The verifier rows show the LAST observation before the final action, not the page after it (a verifier_data
   limitation), so many outcomes are invisible to the model.
2. There are few hard negatives (claimed done but failed).

Until then the product keeps deterministic expected-effects checks as the gate (effects/expect.py), and the model may
only veto.

## End to end in the extension (ext-real-model 40129775318)
With the real G6 behind extension v0.2 in headless Chromium, 3/3 passed:
- a renamed button was picked;
- a deleted button stopped the run with NOT_FOUND;
- a renamed pay button still paused for approval.

Each call took 3-5 s on a 4-vCPU runner.

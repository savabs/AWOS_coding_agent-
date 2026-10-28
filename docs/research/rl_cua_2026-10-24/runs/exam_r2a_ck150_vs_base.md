# Exam: round-2 run A checkpoint 150 vs base Qwen 3.5 4B (2026-10-23/24)

Exam run 40027334131 (free GitHub CPU runners). Both models exported through the same pipeline
(rl-export, Q4_K_M with unsloth's imatrix; releases rl-qwen3.5-4b-r2a-ck150, rl-qwen3.5-4b-base-ours),
same harness commit (incl. the unlabeled-field naming fix 689d560 and tab support), same 249 tasks.

Run A = SFT on 7,455 rows (oracle + Qwen 4B successes + Claude Sonnet distillation), killed by the OS
(host RAM) at step 158/466; the supervisor recovered checkpoint 150 (~32% of one epoch, lr still high).

| exam | base | run A ck150 | base-only | trained-only | McNemar p |
|---|---|---|---|---|---|
| factory ood (99) | 59.6% | 62.6% | 8 | 11 | 0.65 (no real difference) |
| miniwob holdout (150) | 30.7% | 22.0% | 14 | 1 | 0.001 (trained worse) |

Unsafe: 1 each (base followed an injection; trained clicked a trap).

- The product fix moved the BASE on MiniWoB from 19.3% (baseline of record, older code) to 30.7%.
- 12 of the 14 MiniWoB losses are click-tab-2* (a held-out task type absent from training): the trained
  model loops clicking tabs until the 12-step cap. 2 are copy-paste (done() without on-page ack).
  Only 2 of the 14 pages had derived "(unlabeled)" field names, so the stale-observation hypothesis
  does not explain the drop.
- All three 4B runs on 2026-10-23 (A, G2, the earlier full run) lost a rank to the OS at step 155-168:
  a steady per-step host-RAM leak (fix 5a6a1fb logs and guards it; first run next week confirms).
- Takeaways for next week: (1) loops on unfamiliar task types are the risk to watch; recovery /
  anti-loop data and broader app variety (the research's top items) target exactly this; (2) collect
  new training data with the current page reader anyway; (3) never judge a partial checkpoint as the recipe.

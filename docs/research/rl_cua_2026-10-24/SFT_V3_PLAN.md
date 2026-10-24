# SFT v3 on the G2 (teacher) data: next GPU run plan (2026-10-24, DRAFT for owner approval)

## Why G2 is the base

Same agent code, same 249 exam tasks (runs/exam_g2_ck150_vs_base.md):

| | base | run A ck150 (Qwen self + teacher + oracle) | **G2 ck150 (teacher + oracle)** |
|---|---|---|---|
| factory unseen apps (99) | 59.6% | 62.6% (+3, n.s.) | **78.8% (+19.2, p=7e-5)** |
| MiniWoB holdout (150) | 30.7% | 22.0% (-8.7, p=0.001) | 28.0% (-2.7, n.s.) |
| unsafe episodes | 1 | | 3 (gate FAIL) |

Run A final (466 steps) exam is running; it goes in this table when it lands.

G2 data (data job 40014984230): 2,962 train / 368 valid rows over 491 tasks.
- Sources: oracle factory 1,354 (**46%**, scripted), Claude Sonnet 1,543 (52%), oracle MiniWoB 65 (2%).
- MiniWoB: about 455 rows over only 10 task types (click-collapsible alone 152).
- Trained to checkpoint 150 of 185 steps (81%) before the RAM leak (now fixed).

## G2's three weak spots, and the fix for each in v3

1. **Unsafe 1 -> 3** (factory/wizard inject_option, wizard upsell_trap, calendar trap). G2 now gets far into
   wizard flows (12% -> 52% pass), so it reaches the traps that the base never reached. 27% of train tasks
   use adversarial templates (inject / trap / upsell: 123 of 460), but those rows are not weighted.
   **Fix:** boost adversarial-template train tasks x2 (dataset `--boost-tasks`). Add first-wrong-step
   corrections where Qwen's first mistake was unsafe (first_wrong.py, category `unsafe`).
2. **Scripted oracle at 46%** (research: filtered teacher data beats volume; the plan caps scripted
   walkthroughs at 30%). **Fix:** `--clean` (verified successes, no loops, near-duplicates out, scripted
   cap 30%).
3. **MiniWoB flat or down** (copy-paste 35% -> 0%; thin and narrow MiniWoB data). **Fix:** add recovery
   / anti-loop rows (recovery.py, train tasks only; capped at about 15% of the set, scroll_away
   downweighted). Corrections from Qwen's own MiniWoB train failures (collect3b, current page reader).
   Investigate copy-paste before the run (no GPU): is it a missing action or a learned habit?

## Data v3 (built on free CI, checked locally before any GPU)

| part | source | expected rows |
|---|---|---|
| teacher | Claude Sonnet runs 39973466526 + 40003832864, cleaned | about 1,400 |
| scripted | oracle factory + MiniWoB, capped at 30% of the total | about 900 |
| corrections | first_wrong.py on collect3b (Qwen 4B, train tasks, current reader) | about 300-600 |
| recovery | recovery.py (train only), capped at about 15% | about 500 |
| safety boost | adversarial-template train tasks x2 | +about 300 |
| **total** | | **about 3,400-3,700** |

Every part passes the dataset's eval-split guard (factory ood / ood300 and MiniWoB holdout refused).

**Optional: reason lines** (one short "why" line before each action, research: helps). Written by
Claude Haiku from the known right action. Cost about $0.2-0.5 by batch for this set (dry run measured
$0.14 for 1,525 rows). It needs Claude API credit. Without it, v3 trains plain actions as G2 did.

## GPU runs (2 Kaggle sessions in parallel)

| # | run | start from | steps | GPU-h | decides |
|---|---|---|---|---|---|
| 1 | **SFT v3, Qwen 4B** | warm start from G2 ck150 (weights; fresh optimizer + schedule, lr 5e-5) | about 230 (1 pass) | about 2.5 | the new recipe |
| 2 | SFT v3, Qwen 2B (same data) | from base (no 2B checkpoint exists) | about 230 | about 1.5 | is 2B usable on a cheap Mac |
| | **total** | | | **about 4** | |

Both carry the RAM fix (pad 128, cuDNN off), the per-rank memory probe, low-RAM clean stop with a full
checkpoint, ntfy pings and the remote stop switch. GPU used since the weekly reset: about 4.5 h of 30 (A-resume 1.9, probe 0.7, A-resume3 1.8).

## Exam and gate

- Bigger exam `ood300` (297 unseen-app tasks, about +-5.5 pts) + MiniWoB holdout (150), free CI.
- Models on the SAME code: base, G2 ck150, v3-4B, v3-2B (base and G2 need an ood300 run too; free).
- **Keep v3-4B only if** factory ood300 is >= G2 ck150 + 2 pts with a CI above 0, MiniWoB is not worse
  than base, and unsafe episodes are <= base. ood300 has about 90 adversarial tasks, so unsafe is
  measured with more power than the 3-vs-1 we have now.
- If v3 fails the unsafe test only: the next run is plan run 3 (learn from mistakes: DPO on
  safe-vs-trap pairs, G4 code ready), not more SFT.

## Order of work

1. (now, no GPU) copy-paste diagnosis. rl-train-data workflow: run first_wrong + recovery and pass
   `--clean --extra-rows --boost-tasks`. Build v3 locally from the downloaded artifacts and check the counts.
2. (when collect3b finishes) corrections from Qwen's train failures. Data job on CI. Smoke test.
3. Launch runs 1 + 2 together. Meanwhile run base + G2 on ood300 (CI).
4. Exam v3-4B / v3-2B on ood300 + MiniWoB, gate, write-up.

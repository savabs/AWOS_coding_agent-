# M0: first real Online-Mind2Web baseline (2026-10-26)

**Setup.**
- 100-task dev subset (fixed, seeded, stratified by level) of Online-Mind2Web.
- Live websites from Kaggle (2x T4, llama.cpp CUDA b11146 prebuilt), 4 browser workers, 25 steps / 900 s per task.
- Fixed page reader (claude/web-reader).
- Judge: WebJudge-7B on Kaggle; T4 sizing in NOTICE deviation 7.

**Runs.** rl-web-kaggle 40239278459 (rollouts); rl-web-judge-kaggle 40278139779 (judge: 200 episodes, 0 judge errors,
$0).

| | base Qwen3.5 4B | run A final (our trained 4B) |
|---|---|---|
| **official success** (all 100; blocked = failure) | **22%** (Wilson 95% CI 15-31) | **19%** (13-28) |
| **reachable-site success** (57 executable tasks) | **38.6%** (22/57) | **33.3%** (19/57; easy 60%, medium 22%, hard 27%) |
| blocked by the site (bot walls / Cloudflare / captcha / 403 / load) | 43 | 43 |
| agent said done | 14 | 13 |
| gave up / asked for help | 19 | 0 |
| hit the 25-step cap | 8 | 27 |
| seconds per model step (T4) | 3.2 | 2.9 |

**Paired comparison** (same 100 tasks): both succeed 11, only A 8, only base 11, both fail 70. Exact McNemar
p = 0.65, so the models are not significantly different.

## Reading
- **Blocking is the biggest single factor: 43% of tasks never reach the model from Kaggle's datacenter IPs.**
  - The published bars come from their own environments: Fara1.5-4B 57.3%, MolmoWeb-8B 35.3% official on all
    300 tasks.
  - Our official 19-22% is not comparable until the block rate is comparable. A residential-IP run (the sandbox VM)
    is the next measurement.
- On reachable sites, the untrained text-only 4B gets 38.6%. That is the right order of magnitude for a small model,
  and a starting line.
- Run A's synthetic-app training does not transfer to real sites: 33% vs 39%, not significant. Its failure mode is
  the opposite of the base model's: it never gives up and loops to the step cap (27 times).
  - The training target is now precise: **real-web data** (Mind2Web, now converting at 80.5% step mapping; Claude
    teacher runs), plus recovery / stop-when-stuck examples.
- WebJudge credits some episodes the agent did not finish by saying done: 19-22 successes vs 13-14 "done". It
  judges the trajectory's screenshots, not the agent's claim. This applies equally to both models.

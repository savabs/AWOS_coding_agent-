# Track C — learning over use: results

rows 156 (invalid 0), spend $0.5055

## Curves per position

| pos | arm | runs | solve | mean $ | mean turns | $ / solved | min | notebook chars |
|---|---|---|---|---|---|---|---|---|
| 1 | off | 18 | 18/18 | 0.0024 | 3.8 | 0.0024 | 0.25 | 0 |
| 1 | learn | 18 | 18/18 | 0.0029 | 3.7 | 0.0029 | 0.33 | 1264 |
| 2 | off | 18 | 18/18 | 0.0024 | 3.8 | 0.0024 | 0.27 | 0 |
| 2 | learn | 18 | 18/18 | 0.0037 | 3.8 | 0.0037 | 0.37 | 2016 |
| 3 | off | 18 | 18/18 | 0.0025 | 3.9 | 0.0025 | 0.25 | 0 |
| 3 | learn | 18 | 18/18 | 0.0044 | 3.9 | 0.0044 | 0.36 | 2720 |
| 4 | off | 18 | 18/18 | 0.0022 | 3.4 | 0.0022 | 0.24 | 0 |
| 4 | learn | 18 | 18/18 | 0.0048 | 3.8 | 0.0048 | 0.38 | 3181 |
| 5 | off | 6 | 3/6 | 0.0025 | 4.2 | 0.0050 | 0.26 | 0 |
| 5 | learn | 6 | 1/6 | 0.0055 | 4.3 | 0.0330 | 0.49 | 3345 |

## Solve rate (stats rule, positions 2–4)

- **INCONCLUSIVE** — candidate `learn` vs base `off`, 18 tasks (runs 54/54 vs 54/54)
- P(Δ>0) = 0.500 (Dirichlet over tasks × Beta per task, 20000 draws, seed 20261009); posterior Δ -0.1% 95% CrI [-13.7, 13.6]; observed per-task Δ 0.0%; plain Bayesian bootstrap P = 0.500
- task-level exact McNemar: b = 0 tasks better, c = 0 worse → p = 1.0000
- MDE ≈ 0.0% · cost $0.0043 vs $0.0024 per run (×1.79)
- why not KEEP: P(Δ>0) = 0.500 < 0.95; McNemar p = 1.000 > 0.05; cost ×1.79 over budget +50%

## Efficiency (primary, positions 2–4)

- **cost: LEARNING HURTS** — 18 tasks, learn cheaper on 0, dearer on 18 (sign p = 0.000); P(mean log-ratio < 0) = 0.000; geo-mean ratio learn/off = 1.790 [1.618, 1.975]
- **turns: INCONCLUSIVE** — 18 tasks, learn cheaper on 4, dearer on 9 (sign p = 0.267); P(mean log-ratio < 0) = 0.204; geo-mean ratio learn/off = 1.035 [0.945, 1.122]

## Placebo (position 1, empty memory in both arms)

- cost: 6 tasks, cheaper 0 / dearer 6, geo-mean ratio 1.232 [1.115, 1.360], P(<0) = 0.000
- turns: 6 tasks, cheaper 1 / dearer 1, geo-mean ratio 0.992 [0.930, 1.046], P(<0) = 0.597

## Near-miss (position 5)

- cost: 2 tasks, cheaper 0 / dearer 2, geo-mean ratio 2.249 [1.791, 2.822], P(<0) = 0.000
- turns: 2 tasks, cheaper 1 / dearer 1, geo-mean ratio 1.058 [0.939, 1.192], P(<0) = 0.274

## Harm check (off always solved, learn never)

- none

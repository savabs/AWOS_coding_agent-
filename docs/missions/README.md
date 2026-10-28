# AWOS Missions

Stage 1 real-workload missions run in an isolated worktree with runtime sessions.

## Commands

| Command | Description |
|---------|-------------|
| `awos mission start` | Quick 5-step mission (~1–2 min) |
| `awos mission start --long` | Long mission — **8-task fixed plan** on AWOS repo (~10–30+ min) |
| `awos mission start --clawcode` | **Phase D** — 14-task docstring mission on clawcode (lab only) |
| `awos mission start --clawcode-ci-rescue` | **Real CI rescue on clawcode** — 12 broken tests → green |
| `awos mission start --ci-rescue` | **CI Rescue Sprint** — 18-task, 28 reds → green (`ci_rescue_sprint` fixture) |
| `awos mission status --long` | Sessions for the long mission |
| `awos mission status --clawcode` | Sessions for the clawcode mission |
| `awos mission status --clawcode-ci-rescue` | Sessions for clawcode CI rescue |
| `awos sessions resume rs_<id>` | Resume after Ctrl+C pause |

Use **Ctrl+C after task 4–6** on the long mission to exercise pause/resume, then `awos sessions resume rs_<id>`.

## Long mission

`awos mission start --long` loads `stage1_long_workload.plan.json` (8 pre-planned tasks). The CheapPlanner is **not** used — this avoids collapsing a prose goal into 2–3 merged steps. Product context: `docs/product/stage1_subscription_worker_guideline.md`.

Config files: `docs/missions/stage1_real_workload.json`, `docs/missions/stage1_long_workload.json`, `docs/missions/stage1_long_workload.plan.json`.

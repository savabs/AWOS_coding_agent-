# M1: base 4B with the dialog + page-settle harness fixes (Kaggle, 2026-10-27)

Same 100 dev tasks, Kaggle T4 x2, caps and judge as [M0](m0_kaggle_baseline.md); only the harness changed:
dialog refusals fed back to the model (claude/web-modal) and a bounded page settle before each observation
(claude/web-settle, 8 s cap, one re-read of empty pages).

**Runs.** rl-web-kaggle 40535270872 (rollouts, 24 min, 0 infra errors); rl-web-judge-kaggle 40543434558
(WebJudge-7B, 1 judge error).

| | M1 (fixes) | M0 |
|---|---|---|
| **official success** | **20%** (20/99, Wilson 95% CI 13-29) | 22% |
| reachable-site success | 35.1% (20/57) | 38.6% (22/57) |
| same reachable tasks (56) | 20 | 21 |
| blocked by the site | 42 | 43 |
| ended by a dialog refusal | 3 | 9 |
| hit the 25-step cap | 15 | 8 |
| steps per episode | 7.4 | 10.9 |

Paired over the 100 tasks: both succeed 16, only M1 4, only M0 5, neither 74; exact McNemar p = 1.0.

**Reading.** The fixes work mechanically (dialog endings 9 -> 3; 25 reads waited for the load event, 3 empty pages
re-read; ~2.5 s settle per action) but success did not move: the episodes they keep alive mostly run into the
step cap instead of finishing. The bottleneck on reachable sites is now the model, not the harness, which is what
the real-web teacher data (docs/specs/web_teacher.md) is for. Keep both fixes (they make the measurement cleaner
and cost little); judge future models with them on.

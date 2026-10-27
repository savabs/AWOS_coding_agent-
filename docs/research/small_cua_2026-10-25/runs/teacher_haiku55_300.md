# Teacher round 1: Claude Haiku 5.5 on 300 Mind2Web train tasks (2026-10-27)

Pipeline: docs/specs/web_teacher.md. Runs: rl-teach 40544701120 (10 free runners, 20 min, **$0.52** of a $5 cap,
0 infra errors); rl-web-judge-kaggle 40554379179 (WebJudge-7B, 0 judge errors).

| | |
|---|---|
| tasks | 300 (Online-Mind2Web texts and dev websites excluded at build time) |
| blocked by the site (GitHub runner IPs) | 145 (48%) |
| teacher success, WebJudge | 45/300 official, **45/155 = 29% reachable** |
| episodes kept for SFT (judged success AND ended in done()) | 30 |
| SFT rows | 222 (6.3 per kept episode), incl. 37 recovery rows (32 from failed episodes) |

Rows: local only (`teacher_rows_300.jsonl` in the job tmp folder; derived from gated tasks, never committed).

**Reading.** Cheap ($0.0017 per task) but low-yield: half the sites block the runners, and Haiku succeeds on 29%
of the rest. 222 rows are too few to move a 4B on their own; at this yield ~1,000 more tasks (~$2) would give
~100 kept episodes / ~700 rows.

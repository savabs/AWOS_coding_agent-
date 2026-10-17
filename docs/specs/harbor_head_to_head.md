# Harness head-to-head on Terminal-Bench 2.1 (Haiku 5.5): AWOS vs the agents big players ship

Pre-registered 2026-10-15, before any data. The owner asked to compare AWOS against agents that
are actually used in industry, on long-horizon tasks.

## Why this benchmark

Terminal-Bench 2.1 (Apache-2.0; 89 tasks; each a Docker container plus an instruction, graded by hidden
pytest, all tests must pass; dozens to hundreds of steps per task) is the board labs and agent vendors
use to compare harnesses on the same model. Published same-model gaps between harnesses run 0–20
points (e.g. Opus 4.6: Claude Code 70.1% vs Terminus 2 63.8%). It runs through Harbor (Apache-2.0),
which ships adapters for the agents below.

## Arms (all `anthropic/claude-haiku-5-5`, the owner's API key)

| arm | what | role |
|---|---|---|
| `awos` | AWOS `loop` executor, custom Harbor agent; the container is its sandbox | candidate |
| `claude-code` | Anthropic's Claude Code, headless; web tools disallowed; helper and subagent model slots pinned to Haiku 5.5 | **primary comparator** |
| `openhands` | OpenHands, headless | secondary |
| `opencode` | OpenCode | secondary |
| `goose` | Goose (Block) | secondary |
| `mini-swe-agent` | mini-swe-agent | secondary (minimal-loop floor) |
| `terminus-2` | Terminal-Bench's reference harness | control |

Excluded, with reasons: Codex CLI (Responses-API only; running Haiku needs a translation gateway, not a
fair test), Cursor CLI (no bring-your-own key), Gemini CLI (Gemini only), Aider (no autonomous shell).

## Fairness rules

- Same model; each agent's default reasoning or effort unless it can be pinned equally (whatever could not
  be equalised is listed in `ci/HARBOR.md` before the run).
- No web search or fetch for any agent (Terminal-Bench solutions are public).
- Same per-task timeout (dataset default), container resources, concurrency, and pinned agent versions.
- Cost is recomputed for every arm from raw token usage at Haiku 5.5 prices. The cost each agent
  self-reports is shown alongside.
- Infra errors (container or build failures, rate-limit crashes) are reported separately. A task with an
  infra error in any arm is re-run once; if it still errors it is dropped for all arms (paired exclusion).

## Tasks

- **Pilot (not counted):** the 10-task `terminal-bench-sample`, arms `awos`, `claude-code`, `mini-swe-agent`,
  k = 1. It checks plumbing and per-task cost only.
- **Main run:** a seeded, stratified 25-task subset of Terminal-Bench 2.1 (stratified by the dataset's
  category or difficulty). The seed and the task list are committed to `ci/` before the main run. k = 3 attempts per task per arm.
- **Budget:** at most $50 total API spend for the main run. The runner caps per-agent spend where the agent supports it.

## Hypotheses and decision rule

- **Primary (T1):** AWOS vs Claude Code on the 25 tasks, paired over tasks with k = 3, judged by the T1 rule
  (`docs/specs/stats_decision_rule.md`; `scripts/stats_audit.py --a claude-code --b awos --cost-budget 0.5`).
  KEEP means AWOS is better than Claude Code at no more than 1.5× its cost.
- **Secondary:** AWOS vs each other arm, same test. These are reported with a Holm correction across the 5
  secondary comparisons and are descriptive unless they survive it.
- **Descriptive:** pass rate (Wilson CI), $ per task, $ per solved task, median minutes, and infra-error rate per arm.

## What we would claim

- **Primary KEEP:** "On Terminal-Bench 2.1 (25-task subset, Haiku 5.5), AWOS beats Claude Code."
- **INCONCLUSIVE:** "AWOS is within the measurable range of Claude Code", plus the measured cost ratio. No
  "better" claim.
- **REJECT:** say so publicly, and treat the gap as engine work.
- No comparison with public leaderboard entries: those are vendor-tuned runs on other models.

## Known limitations

- 25 tasks × 3 attempts gives roughly a ±15-point CI per arm; only large differences are detectable.
- Haiku 5.5 is newer than some agents' price tables. Cost is recomputed from tokens for that reason.
- Terminal-Bench tasks are terminal or ops-heavy rather than repository bug-fixing; this complements the
  SWE-style ladder (`harness_lift_ladder.md`) rather than replacing it.

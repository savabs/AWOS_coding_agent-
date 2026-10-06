# Ablation 3b: one shot first, with cheaper fallbacks

Pre-registered on 2026-10-06, before the run. Builds on
`docs/specs/ablation_one_shot.md`.

## Why

Ablation 3 showed that one-shot works. About 40% of backupd jobs were solved
in one call at Aider-level cost. Turns fell 46% (off arm) and 66% (on arm).
Solved counts held or improved.

The fallbacks erased the cost and time savings in the off arm:

- **(B) Truncated replies.** 5 replies were cut off at the 16k output cap.
  Each was retried before falling back, which wasted about 3 minutes per job.
- **(Failed blocks).** One failed SEARCH block sent the whole job to the agent
  loop. All blocks failed on more-itertools, whose main file exceeded the
  context budget.

## The change (one ablation: fallback cost)

1. **Reasoning off for the one-shot call.** A cut-off reply falls back at
   once, with no retry.
2. **One repair call for failed blocks.** It sends only those blocks, with the
   current file text, before any fallback.
3. **Large files get their relevant sections.** Line-numbered windows around
   hits and named symbols are included, instead of skipping the file.

Everything else matches ablation 3, run 20261005T183332:

- pinned fp8 provider list;
- `AWOS_PLANNER=auto`;
- `AWOS_INTEGRATION_REVIEW=auto`;
- `AWOS_ONE_SHOT=1`.

## Design

- **Series:** backupd (held-out), jobs 1–12, arms `off` and `on`,
  `--repeat 2`.
- **Comparison:** ablation 3 run 20261005T183332, paired per job and averaged
  over passes.
- **Also:** the three real-repo issues, AWOS off.

## Metrics and decision rule

1. **Primary:** billed $ per job falls by at least 25% in the **off** arm,
   compared with ablation 3. The on arm must not rise.
2. **Co-primary:** minutes per job fall by at least 20% in both arms.
3. **Mechanism:**
   - No one-shot reply is retried after a cut-off.
   - At least half of the jobs with failed blocks are fixed by the repair
     call, with no agent loop.
   - One-shot applies at least one block on real more-itertools.
4. **Guardrail:** pooled solved within 2 per 12 of ablation 3. Ablation 3
   scored off 17/24 and on 13/24, so the minimums are off ≥ 13/24 and on
   ≥ 9/24. At least 2 of the 3 real issues must be solved.
5. **Health:** VALID.

**Adopt** if 1, 3, 4 and 5 hold: make `AWOS_ONE_SHOT=1` the default for the
agent loop executor.

**Then compare with Aider.** Use the backupd baseline, run 20261002T223614:
Aider solved 5/12 at $0.0012 per job.

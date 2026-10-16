# Loop reliability and cost: pre-registered A/Bs for rea_synthesis A6–A8, C9, C10, D13, D15

Pre-registered 2026-10-16, before any of this code ran on a real model. Source of the ideas:
`docs/research/rea_synthesis_2026-10-16.md` (REA found no harness that raises accuracy, so each item below
targets cost, long-task robustness or safety, and must show its value on its own metric before it becomes a
default). Runs are paused until the owner approves each one; nothing here is run yet.

## What was built, and its default until its A/B says otherwise

| item | switch | default | why this default |
|---|---|---|---|
| A6 cost routing (`loop_route.py`) | `AWOS_LOOP_ROUTE="local,claude-haiku-5-5"` | **off** | changes which model works; needs R1 |
| A7 stuck detector (`loop_stuck.py`) | `AWOS_LOOP_STUCK=off\|log\|nudge` | **log** | log mode counts triggers and leaves the prompt byte-identical, so it cannot change outcomes; nudges need R2 |
| A8 labelled route log | `AWOS_ROUTE_LOG` | on when routing is on | observability only |
| C9 per-turn checkpoint (`loop_checkpoint.py`) | `AWOS_LOOP_CHECKPOINT` | **on** | writes a file per turn; the prompt is unchanged unless a killed run is resumed |
| C10 two-stage compaction (`loop_compaction.py`) | `AWOS_LOOP_COMPACT` | **off** | trades cache hits for room; needs R4 |
| D13 read-only control files in the sandbox | `AWOS_SANDBOX_READONLY` | **on** | safety; a task that must edit a protected file fails loudly, and the list is configurable |
| D15 secret scan at the review-branch handoff | `AWOS_SECRET_SCAN` | **on, fail closed** | safety; emergency escape hatch only |

All decisions use the T1 rule (`docs/specs/stats_decision_rule.md`) unless stated. INVALID rules are the ladder's
(provider or empty-response failures are invalid, never "unsolved").

## R1: cost routing (A6) — laptop + API

- **Arms:** `loop` with `AWOS_LOOP_ROUTE=local,claude-haiku-5-5` (routed) vs `loop` on Haiku alone (baseline).
  Stuck detector in `log` mode in both arms (routing reads its escalation recommendation in either mode).
- **Set:** the 40 fresh issues (H1d's set), 1 repeat, interleaved per issue. Laptop (local rung needs llama-server).
- **Primary metric:** $ per solved task. **Guard:** solve rate no worse than baseline − 1 task (≥ 39/40 if
  baseline is 40/40; in general routed ≥ baseline − 1).
- **KEEP (routing becomes the documented default for users with a local model):** guard holds AND routed $ per
  solve ≤ 0.7 × baseline. **REJECT:** guard fails. Otherwise INCONCLUSIVE.
- **Descriptive:** escalations per task, at which step and on which trigger; share of tasks finished on the local rung.
- **Cap:** $3 API (baseline ≈ H1d cost; routed ≤ that).

## R2: stuck-detector nudges (A7)

- **Step 0 (free, required first):** with the default `log` mode, count how often each trigger fires on the next
  approved runs (any arm). If no trigger fires on ≥ 10% of runs, R2 is not worth running and nudges stay off.
- **Arms:** `loop` local with `AWOS_LOOP_STUCK=nudge` vs `=log`, the 15 H2 issues, 1 repeat, $0.
- **Primary:** local solve rate. **KEEP:** Δ ≥ +2 issues of 15 and no issue lost that `log` solved because of a
  nudge (checked by reading those transcripts). **REJECT:** Δ ≤ −1. Otherwise INCONCLUSIVE (stays `log`).
- The detector never stops a run early in any mode; that is a code invariant tested in `tests/test_loop_stuck.py`.

## R3: checkpoint and resume under kills (C9)

- **Arms:** the same 15 H2 issues on the local model, each run killed (SIGKILL) once at a step drawn uniformly
  from 5–25 (seeded, list written first), then restarted; checkpoint on vs off.
- **Primary:** tokens spent after the kill to reach the same end state (resumed runs should not re-pay the turns
  before the kill). **KEEP (stays on):** median post-kill tokens with checkpoint ≤ 0.5 × without, and solve rate
  not lower by more than 1 issue. **REJECT (default off):** solve rate lower by ≥ 2 issues.
- $0, laptop only.

## R4: two-stage compaction (C10)

- **Set:** tasks that go past 60 steps or 70% of the window without compaction. Built from the Terminal-Bench pilot
  and the local runs' long tail; the list is fixed before any compaction run.
- **Arms:** `AWOS_LOOP_COMPACT=1` vs `0`, same model, 1 repeat.
- **Primary:** solve rate on that set. **Cost check:** $ per run. **KEEP:** Δ ≥ +2 tasks and cost ≤ 1.3×.
  **REJECT:** Δ ≤ −1. Otherwise INCONCLUSIVE (stays off).

## S1, S2: safety checks (D13, D15) — functional, not A/B

- **S1:** a scripted run asks the model (via a fake client in tests, and once for real when runs resume) to write
  into `.awos/`, `.claude/` and `.git/hooks`; every write must fail inside the sandbox while AWOS's own commit on
  the host still succeeds. Also re-check that H1d's solve rate does not drop on the next approved run (no task in
  that set edits a protected path).
- **S2:** planted fake secrets in a fix branch must stop the handoff, with file:line and rule name and no secret
  value in any log; a clean branch must hand off as before.

## Order

R2 step 0 rides on whatever runs next. Then R1 (needs owner OK for ~$3 and the laptop), then R3, then R4 after the
Terminal-Bench pilot provides long tasks.

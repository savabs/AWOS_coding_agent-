# Harness-lift ladder: how much does the AWOS harness add over the raw model?

Pre-registered 2026-10-13, before any ladder run. Owner question: "Is it the harness doing the work, or just the model? If the model is that good, the harness matters little."

## Why

B3 (`docs/memory/runs/b3_full_awos_vs_aider_2026-10-13.md`) held the model fixed (Haiku 5.5) and found AWOS ahead of Aider: 83% vs 67% of runs, McNemar 14–4, p = 0.031, P = 0.91. That compares two harnesses. It does not say how much any harness adds over the model on its own.

This ladder measures the lift over a **minimal harness**: mini-swe-agent (a bash-only loop of about 100 lines), arm `mini`. It runs at two model strengths. The prior from the literature (`docs/expedition/01_what-works.md`) is that harness structure matters little for frontier models and a lot (14–22 pp) for small models.

## Arms and design

| Rung | Haiku 5.5 (strong) | Qwen3.5-9B local (weak, $0) |
|---|---|---|
| Minimal harness | `mini` | `mini-local` |
| Standard harness | Aider, B3 data (descriptive only) | — |
| AWOS | `off` | `off` with `AWOS_PROVIDER=local` |

- **Haiku pair:** `off` vs `mini`, interleaved per issue, 1 repeat, on all valid issues in `~/.awos-harness/fresh_series` (40). Hard cap **$5**.
- **Local pair:** `off`-local vs `mini-local`, interleaved, 1 repeat, on **15 issues fixed now**: the first 15 of the sorted fresh-set names whose reference fix touches a single source file. Single-file is chosen so a 9B model has a fair chance; the list is written to the run's out dir before starting. One llama-server, `AWOS_LOCAL_SLOTS=1`, on AC power if possible, with no other experiment running. $0.
- **Order:** both pairs run after the B3 extension finishes, never at the same time as it.
- **Shared settings:** same goals, repos, hidden tests, step and cost limits per job, and INVALID rules (provider or empty-response failures are invalid, never "unsolved").

## Hypotheses and decision rule (T1, `docs/specs/stats_decision_rule.md`)

| | Hypothesis | Test |
|---|---|---|
| **H1** | AWOS beats the minimal harness on Haiku | `stats_audit.py --a mini --b off --cost-budget 0.5` |
| **H2** | AWOS beats the minimal harness on the local model, by **≥15 pp** | `--a mini-local --b off --cost-budget 0.5` (cost $0 both); KEEP also requires an observed Δ ≥ 15 pp |
| **H3** (descriptive) | AWOS on the free local model compared with the minimal harness on Haiku | Solve rate and $ per solved, reported side by side, no verdict |

Each hypothesis is judged KEEP / INCONCLUSIVE / REJECT under the T1 rule.

## What the outcomes mean

- **H2 KEEP:** the harness is what makes local, $0 routine work possible. That is AWOS's core value claim, and it goes on the site.
- **H1 KEEP but H2 not:** the harness adds value on strong cloud models only.
- **Neither:** for today's models the harness adds little over a minimal loop. AWOS's value must then come from the product layer (background queue, verification, review UX, cost), not from solve rate. We would say so publicly.

## Known limitations

- One repeat per pair, so the MDE is roughly 20 pp for Haiku and much larger for the 15 local issues.
- The goals in the fresh set were written to match the hidden tests.
- mini-swe-agent's prompts are tuned for SWE-bench-style tasks. That is the point of a minimal baseline, but it may under- or over-state "no harness".

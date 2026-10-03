# Ablation 1: pin one caching provider

Pre-registered on 2026-10-03, before the run. The protocol is
`docs/research/evaluation_first_principles_2026-10.md` (rules 3–9).

## The one change

All AWOS OpenRouter calls are pinned to **DeepInfra (fp8)**, with no fallbacks
and one session per job:

```
AWOS_OPENROUTER_PROVIDER=deepinfra
AWOS_OPENROUTER_ALLOW_FALLBACKS=0
```

Everything else is unchanged from baseline run `20261002T223614`, including
history condensing. The stable-prefix change is the next ablation.

## Hypothesis

Unpinned routing spreads calls across about 15 endpoints, so the prompt cache
keeps missing. Pinning lets the unchanged early part of each prompt be read
from cache at $0.018/M instead of $0.090/M.

## Design

- **Series:** backupd, the held-out set, jobs 1–12.
- **Arms:** `off` and `on`, K = 1.
- **Comparison:** each arm against the same arm in baseline `20261002T223614`,
  paired by job.
- **Aider:** not re-run. It does not use this code path.

## Metrics and decision rule

1. **Primary metric:** billed $ per job. The test is a paired sign-flip
   permutation test over jobs, with a bootstrap CI.
   **Success:** the mean drops by at least 25% in both arms and the CI excludes
   0.
2. **Mechanism check:** the cache-hit share of agent input tokens, taken from
   `.awos/llm_calls.jsonl`.
   **Expected:** at least 40%. If it is below 20%, the hypothesis is wrong
   whatever the cost shows.
3. **Guardrail:** solve rate must not drop by more than 2 jobs per arm.
   The test is exact McNemar against the baseline. Solve-rate differences are
   not expected to be detectable at n = 12.
4. **Health:** the run must be VALID. Any call served by another provider is
   fatal (`provider_mismatch`).

## Caveats

- **Pinning also changes the endpoint.** Default routing may have used fp4
  endpoints. Any quality shift is part of what this ablation measures.
- **Time confound.** The baseline ran on 2026-10-02/03, and provider latency
  and load vary.

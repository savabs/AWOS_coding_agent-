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

### Amendment (2026-10-03, before any scored data)

The first attempt with no fallbacks (run 20261003T113221) was stopped after
1.5 jobs, about $0.02 spent.

- **What happened.** DeepInfra's shared pool returned HTTP 429 ("engine
  overloaded") on every call in on j01: agent, critique and notebook. With no
  fallback, provider capacity became an AWOS failure.
- **New setting.** `AWOS_OPENROUTER_PROVIDER=deepinfra`,
  `AWOS_OPENROUTER_ALLOW_FALLBACKS=1`. DeepInfra is preferred, and the
  per-job sticky session keeps calls there and cached while it is healthy.
  OpenRouter falls back only when DeepInfra fails.
- **Health check changes.** A fallback call is now a warning
  (`provider_fallback`), and the report gives the preferred-provider share.
  `provider_mismatch` stays fatal only for no-fallback runs. HTTP 429 is now
  an infrastructure marker (`INVALID_MARKERS`), so the job is retried
  rather than scored as an agent failure.
- **What the aborted attempt already showed.** On off j01, 14/14 calls went to
  DeepInfra with an agent cache share of 64%.

The metrics and decision rule below are unchanged.

### Amendment 2 (2026-10-03, before any scored data)

Allowing open fallbacks failed too (run 20261003T114031, stopped after 1 job).

- **What happened.** DeepInfra was still overloaded. The sticky session put
  24 of 25 calls on **Relace (fp4)**. A quantized model confounds quality,
  and Relace charges about 7× DeepInfra's output price.
- **Final setting.** An ordered list of fp8 caching providers with
  fallbacks off. OpenRouter tries only these, in order:

  ```
  AWOS_OPENROUTER_PROVIDER=deepinfra,gmicloud,novita,siliconflow
  AWOS_OPENROUTER_ALLOW_FALLBACKS=0
  ```

  Overload moves a call down the list, never to fp4.
- **Health check.** Any provider outside the list is fatal
  (`provider_mismatch`).
- **Smoke test.** Both calls were served by DeepInfra. On the second call,
  3840 of 4098 prompt tokens were cached.

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

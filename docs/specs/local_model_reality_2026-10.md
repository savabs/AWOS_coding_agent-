# Pre-registration: local model reality check (Track E, 2026-10-11)

Written and committed **before** any run. Results go to
`docs/memory/runs/local_model_reality_2026-10.md`.

Model: Qwen3.5-9B Q4_K_M (unsloth GGUF) on llama-server (llama.cpp build 11146,
`scripts/local_model.sh`), Apple M5, 16 GB unified memory, laptop on battery.
Spend cap: $0 (no cloud calls; `AWOS_PROVIDER=local`).

## Part 1 — solve rate on 10 easy real issues

**Issue set (fixed now, chosen before any local run).** The 10 issues from
`~/.awos-harness/real_series` that claude-haiku-5-5 solved fastest/cheapest on
2026-10-11 (bench-run `.awos/job_series_20261011T1*.json`: 3–5 turns, 1 LLM
call, 0.16–0.49 min, $0.0023–0.0035), one per repo:

boltons_428, funcy_191, attrs_1513, cachetools_405, humanize_381,
jsonpointer_64, markdown_1606, tabulate_256, toolz_635, more-itertools_1250.

Haiku solved 10/10 of these.

**Protocol.** `scripts/job_series.py run --arms off --jobs 1` per issue, one
llama-server slot, 24k context (script defaults), `AWOS_PROVIDER=local`.
Series copied to a private dir (the shared harness stays read-only) with
`timeout_min` raised 30 → 45 ("generous"); nothing else in the tasks changes.
One attempt per issue (repeat=1). Infra-invalid jobs (server down) are retried
by job_series' own rule and reported separately, not counted as failures.

**Hypothesis H1.** The local 9B solves fewer of these than Haiku: expected
3–7/10. Exploratory (n=10, no A/B claim; Haiku is a historical reference on a
different day, not a paired arm, so no McNemar test is run).

**Decision rule.**
- ≥ 8/10 solved: the local model is a credible worker for easy issues →
  next step is local-first with cloud escalation (trick C1).
- 4–7/10: useful as a first attempt only behind a verifier; report the failure
  modes that cost the most.
- ≤ 3/10: not a viable worker at this size on this machine; local stays for
  utility calls only.
Also reported: minutes per issue (median, max), failure-mode taxonomy from logs.

## Part 2 — concurrency sweep

**Workload (fixed).** 8 requests sent at once to llama-server's native
`/completion`, each a distinct ~2,000-token prompt (distinct so the prompt
cache cannot share prefixes), `n_predict` 256, `ignore_eos` true,
temperature 0. Same 8 prompts for every setting.

**Settings.** `-np` (= `AWOS_LOCAL_SLOTS`) ∈ {1, 2, 4, 8}, `-c = 12288 × np`
(12k per slot), everything else as `scripts/local_model.sh`. One warm-up
request after load (not measured). If the build supports `--kv-unified`, an
extra np=8 run with a unified 32k pool is reported as a variant.

**Measured.** Wall time for all 8; aggregate generated tokens/s
(8×256 / wall); per-request end-to-end latency (mean, max); server-reported
decode and prefill t/s; server RSS and `footprint` peak; system free-memory %.

**Hypothesis H2.** Aggregate decode throughput rises with slots up to a knee
(expected at 4) and then flattens, because decode on Apple silicon is
memory-bandwidth bound and batching amortizes weight reads.

**Decision rule (knee).** The knee is the smallest np whose aggregate tokens/s
is within 10% of the best setting's, with no swap growth. That np becomes the
recommended `AWOS_LOCAL_SLOTS` for parallel local work; if np=1 is within 10%
of the best, parallel slots buy nothing on this machine and the default stays 1.

## Code change allowed

`dag_executor.MAX_WORKERS` configurable via env (`AWOS_DAG_MAX_WORKERS`; in
local mode default to `AWOS_LOCAL_SLOTS`), only if it is a pure read with the
cloud default (4) unchanged.

## Caveats known up front

Other experiment tracks run on the same laptop at the same time (CPU/memory
contention), and the laptop is on battery; both can lower throughput. Each
run records free memory and battery state.

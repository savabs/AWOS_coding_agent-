# Local model reality check — Track E results (2026-10-11)

Pre-registration: `docs/specs/local_model_reality_2026-10.md` (commit 8b72a7e,
committed before any run). Drivers: `scripts/local_solve_rate.py`,
`scripts/local_concurrency_sweep.py` (commit 59beee7).

Setup: Qwen3.5-9B Q4_K_M (unsloth GGUF), llama-server build 11146
(`scripts/local_model.sh`), Apple M5 MacBook Air (fanless), 16 GB unified
memory. **Spend: $0.** No cloud call was possible: the child env had every
`*KEY*/*TOKEN*/*SECRET*` variable removed and `--env-file` pointed at a keyless
copy of `.env`; `AWOS_PROVIDER=local`. The laptop was on AC power for both
parts (battery 51–80%, charging). Other wave tracks shared the machine.

Raw data (committed):
- `docs/memory/runs/local_concurrency_sweep_2026-10.json` (+ `.log.txt`)
- `docs/memory/runs/local_solve_rate_2026-10.json` (+ `.log.txt`, full job_series console)

## Headline

| | Result | Pre-registered expectation | Decision |
|---|---|---|---|
| Part 1 solve rate | **8/10** (Wilson 95% CI 0.49–0.94) | H1: 3–7/10 | H1 **refuted**; rule "≥ 8/10 → credible worker for easy issues → local-first with cloud escalation (C1)" fires, but only just (one fewer solve and it would be the 4–7 band) |
| Part 1 time | median **22.6 min**/issue (max 30.8), vs Haiku 0.16–0.49 min | — | about **80× slower** wall-clock than Haiku on the same issues |
| Part 2 knee | **np = 4** (9.50 tok/s, 93% of best 10.17 at np=8) | H2: knee at 4 | H2 **supported** for throughput; the "no swap growth" clause was not met cleanly by any setting (see below) |

## Part 1 — solve rate on the 10 pre-registered issues

Protocol as registered: `job_series.py run --arms off --jobs 1` per issue, one
slot, 24k context, series copied to `.awos/track_e/series` with `timeout_min`
30 → 45 (the only change; verified all 10 `task.json` files went 30 → 45).
One attempt each. No job was invalid, none timed out.

| Issue | Solved | Hidden | Min | Turns | Planner | Local in/out tokens |
|---|---|---|---|---|---|---|
| boltons_428 | **no** | 44/45 | 8.25 | 1 | planner | 22,946 / 1,369 |
| funcy_191 | yes | 13/13 | 29.02 | 12 | planner | 109,806 / 10,917 |
| attrs_1513 | yes | 163/163 | 12.64 | 12 | skipped | 106,944 / 3,778 |
| cachetools_405 | **no** | 168/175 | 29.42 | 11 | planner | 106,628 / 4,435 |
| humanize_381 | yes | 14/14 | 3.30 | 1 | skipped | 18,888 / 691 |
| jsonpointer_64 | yes | 26/26 | 27.21 | 22 | skipped | 134,411 / 4,547 |
| markdown_1606 | yes | 76/76 | 30.76 | 22 | planner | 207,830 / 8,408 |
| tabulate_256 | yes | 178/178 | 18.01 | 25 | skipped | 133,693 / 6,736 |
| toolz_635 | yes | 51/51 | 4.95 | 5 | skipped | 39,117 / 924 |
| more-itertools_1250 | yes | 597/597 | 27.45 | 28 | skipped | 235,739 / 7,902 |
| **Total** | **8/10** | | median 22.6 | | | 1,116,002 / 49,707 |

Haiku 5.5 reference (historical, different day, not a paired arm — so no
McNemar test, as registered): 10/10, 3–5 turns, 1 LLM call, 0.16–0.49 min,
$0.0023–0.0035 each (bench-run `.awos/job_series_20261011T1*.json`).

Evidence that every call went to the local server: llama-server's log for
the run (one server process, started 15:38) records 171 completed requests
generating 50,768 tokens; the job ledgers sum to 49,707 output tokens. Two
more requests were cancelled by the client (below), and nothing was billed
(`cost_usd` 0.0 everywhere; job_series' health check only warns
`missing_billing`, as expected with no provider).

Server-side over the run: prefill median 157 tok/s, decode mean 8.0 tok/s;
491,090 prompt tokens actually evaluated (prefix cache saved the rest of the
1.12 M sent); generated length per request median 126, p90 706, max 2,741.

### Failure modes (from `local_solve_rate_2026-10.log.txt`)

1. **boltons_428: the acceptance gate never armed, so a wrong one-shot shipped.**
   Generating acceptance tests took 6 min, and then "start run timed out; gate
   inactive". The one-shot edit passed the repo's own 44 tests; the hidden
   `test_backoff_constant_factor` failed. Haiku solved this issue in 0.32 min.
   I did not check whether Haiku's gate was armed in that run. Here the gate
   was lost because the local start run was slow.
2. **cachetools_405: a correct diagnosis, then an incomplete repair that ran
   out of turns.** The one-shot passed 291 own tests. The acceptance tests (5
   kept) were all judged CODE_INCOMPLETE, so a bounded repair started
   (max 10 turns). Turn t6→t7 took **16.6 min**: one runaway generation of
   6,160 tokens, which the client cancelled at the 900 s local timeout. The
   repair hit max_turns. AWOS restored the green one-shot edit, and 7 hidden
   tests failed.
3. **Runaway generations cost about 30 minutes of GPU time.** There were two
   such requests (6,160 and 7,369 tokens, both cancelled at the 900 s client
   timeout). By server timestamp, one was in cachetools and one in jsonpointer
   (which still solved). Both ran until the client timeout, so no effective
   output cap was in force. A cap of about 1.5k tokens (p90 is 706) would have
   cut both short.
4. **Slow calls in general.** A 12–15k-token one-shot prompt takes about 2 min
   (prefill-bound). Agent-loop jobs needed 12–28 turns, against Haiku's 3–5,
   and spent 12–31 min.

## Part 2 — concurrency sweep

As registered: 8 distinct prompts of 1,999 tokens each, `n_predict` 256,
`ignore_eos`, temp 0, all sent at once; `-np` ∈ {1,2,4,8}, `-c = 12288 × np`;
one warm-up request. `--kv-unified` is supported by this build, so the
registered np=8 variant with a unified 32k pool was also run.

| Setting | Wall s | Agg gen tok/s | Agg total tok/s | Lat mean / max s | Per-req decode tok/s | Per-req prefill tok/s | RSS idle→peak MB | Footprint peak MB | Free % min | Swap MB before→after |
|---|---|---|---|---|---|---|---|---|---|---|
| np1 | 353.95 | 5.79 | 50.97 | 199.7 / 354.0 | 7.79 | 176 | 5909→6280 | 2328 | 18 | 0→1090 |
| np2 | 288.06 | 7.11 | 62.63 | 182.6 / 288.1 | 4.90 | 108 | 6464→7088 | 2657 | 18 | 1090→1155 |
| **np4** | 215.59 | **9.50** | 83.68 | 169.0 / 215.6 | 3.54 | 73 | 7235→7998 | 3292 | 15 | 1155→1298 |
| np8 | 201.39 | 10.17 | 89.58 | 200.6 / 201.4 | 1.97 | 54 | 8971→9135 | 4558 | 9 | 1258→1998 |
| np8 kv-unified 32k | 223.99 | 9.14 | 80.54 | 223.0 / 224.0 | 1.72 | 52 | 7023→7964 | 2539 | 16 | 1958→2067 |

**Knee (registered rule): the smallest np within 10% of the best aggregate
tok/s.** Best = 10.17 (np8); threshold 9.15; np4 = 9.50 qualifies and np2 =
7.11 does not → **knee = np 4**. np1 is 43% below the best, so parallel slots
do help on this machine.

**Swap clause (deviation from the registered rule, stated plainly).** The rule
also requires "no swap growth". No setting had zero swap growth. np1 grew
1,090 MB while the model first loaded after a reboot, np2 +64 MB, np4
+144 MB, np8 +740 MB with system free memory down to 9%, kv-unified +110 MB.
I read np4's +144 MB as minor and np8's +740 MB / 9% free as the real memory
cliff. The knee is therefore np 4, with this caveat recorded. Under a strict
reading of the rule, no setting qualifies.

Readings:
- Batching amortizes **prefill** more than decode on this machine. Total
  throughput (prompt + gen) goes 51 → 84 → 90 tok/s. Per-request decode falls
  7.8 → 3.5 → 2.0 tok/s, so one agent's turn latency gets about 2–4× worse
  whenever slots are shared.
- At np 8 the server's RSS is about 9 GB and system free memory falls to 9%
  on a 16 GB machine shared with other work. The kv-unified 32k pool keeps
  RSS at about 8 GB at a 10% throughput cost.
- Reproducibility: the interrupted earlier attempt of this track (same
  driver, other worktree, uncommitted `sweep.log`, run at 14:38–14:51 under
  heavier contention) measured np1 6.70, np2 7.13, np4 9.28 tok/s. These are
  within 2–16% of the numbers above, and the ranking is the same.
- Absolute speed is low: about 8 tok/s single-stream decode for a 9B Q4
  model. While the sweep was running, the GPU reported 89% Device Utilization
  and llama-server used under 1% CPU, so it was running on Metal, not on the
  CPU. Likely causes are the base M5's memory bandwidth in a fanless chassis,
  the hybrid Qwen3.5 architecture (the server logs "cache_reuse is not
  supported by this context"), and other tracks running at the same time.
  None of these was isolated.

## What this means for AWOS

- A 9B model running locally on this laptop **can** solve easy real issues
  through AWOS's scaffold (8/10, $0), but it takes about 20+ minutes per
  issue. Haiku takes about 20 seconds. By the useful-work-per-dollar·second
  objective, local wins on dollars and loses badly on seconds.
- The registered decision is **local-first with cloud escalation (C1)**.
  Given the speed, this only makes sense for background or batch work, or
  when there is no network. It needs three local-mode fixes first, each one
  cited above:
  (a) a `max_tokens` cap on agent-loop calls in local mode (runaways cost
  about 30 min);
  (b) acceptance-test generation and its start-run timeout scaled for local
  mode (that timeout cost boltons its solve);
  (c) `AWOS_LOCAL_SLOTS=4` / `AWOS_DAG_MAX_WORKERS=4` for parallel local DAG
  work. For a single agent, keep 1 slot, because sharing slots cuts each
  agent's decode speed.
- Caveats: n = 10, the issues were chosen as Haiku's fastest/easiest, there
  was one attempt each and no paired arm. 8/10 is a credible-worker signal on
  easy issues only, not a parity claim.

## Deviations and incidents

- The first solve-rate launch passed relative paths. job_series children
  could not find `task.json`, and boltons_428 was recorded as INVALID (0
  turns). I stopped the run, deleted its output, and changed
  `local_solve_rate.py` to resolve its path arguments to absolute paths. I
  then relaunched from scratch. No agent had acted, so no result was
  discarded. The console of that launch was kept locally as
  `.awos/track_e/solve_attempt0_relpath.log` (not committed).
- The swap clause of the knee rule is not met by any setting (see above).
- The local server was stopped at the end (`scripts/local_model.sh stop` →
  "stopped pid 25444"; `status` → stopped).

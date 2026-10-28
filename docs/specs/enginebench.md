# EngineBench — the scoreboard for AWOS's local inference engine

Thesis: the moat is the engine that packs four parameters together on a laptop. Every engine change is
measured on all four, on a fixed workload of real AWOS model calls, on the target hardware:

| parameter | what is measured |
|---|---|
| SPEED | seconds per call (agent step / repair pick / verify), split into prefill and decode, and per task (episode) |
| QUALITY | exact action match (agent), wrong-repair rate (repair), false-VERIFIED rate (verify) |
| MEMORY | peak resident / unified memory of the server process |
| COMPUTE | prompt + generated tokens per call and per task, server CPU seconds, GPU busy seconds (energy: not measured, see limits) |

Code: `scripts/engine/{capture,bench,sweep,shape}.py`; tests `tests/engine/`; workload `bench/engine/`.

## 1. The workload (`enginebench-v1`)

`bench/engine/workload-v1.jsonl.gz` (86 KB, committed; digest `ca095ef02f9e`): 445 calls in 182 episodes,
each call exactly as the product sends it (system + user messages, GBNF grammar, max_tokens).

| kind | source | episodes | calls | expected answer |
|---|---|---|---|---|
| agent | factory exam (frozen ood split; rl-eval run 40090089187, run A final `a-final`) | 36 | 265 | the recorded action in the 191 steps of successful episodes |
| repair | `scripts/repair_bench.py` case generation, test split, only the shortlists the rule tier hands to the model | 86 | 120 | `PICK n` / `NOT_FOUND`, exact from the mutation |
| verify | `verifier_data.build_eval` exam rows of the same run | 60 | 60 | VERIFIED / NOT_DONE / UNSAFE (30 failures, 30 successes) |

- Agent episodes are multi-step (1-12 steps), replayed in step order, so prefix reuse across steps is
  real. The replay is open-loop: step k's observation is the recorded page, not one produced by the
  replayed model's own step k-1. "Episodes all-steps-right" is therefore a task-success *proxy*.
- For failed agent episodes there is no known-correct action; `reference` (the CI model's reply on
  CPU) is kept, and "agrees with the CI reply" doubles as a numerics-drift check between configs.
- Synthetic factory apps only (no personal data). The exam is an *eval* split: never train on this
  file (it is not named `trajectories.jsonl`, and `dataset.check_split` refuses ood rows anyway).
- Live-web episodes (run 40209008622, public sites, untrusted text) are built into
  `bench/engine/local/workload-v1-web.jsonl.gz` (85 calls / 23 episodes), which is gitignored. Recipe:

```bash
mkdir -p /tmp/eb-dl/exam /tmp/eb-dl/web
(cd /tmp/eb-dl/exam && gh run download 40090089187 --repo savabs/AWOS_coding_agent- -p 'rl-a-final-factory-ood-*')
for s in 1 3 5 6 8; do (cd /tmp/eb-dl/web && gh run download 40209008622 --repo savabs/AWOS_coding_agent- \
  -n rl-web-a-final-dev-s${s}of12 -D s$s); done
python scripts/engine/capture.py --exam /tmp/eb-dl/exam --web /tmp/eb-dl/web     # rebuilds both files
```

The rebuild is byte-identical for the same inputs (fixed sha-based episode order, gzip mtime 0); the
loader checks the version and the digest. A budget subset (`--budget agent=40`) takes whole episodes in
the fixed order, so a subset is always the same calls.

## 2. How to run

```bash
# one server you started yourself (any loopback port; never stop the owner's :8080)
python scripts/engine/bench.py --url http://127.0.0.1:8091 --kinds agent --budget agent=40 --label mine
# the sweep starts llama-server per config on :8091 (refuses 8080), runs bench, stops only its own server
python scripts/engine/sweep.py --list
python scripts/engine/sweep.py --models a-final-4b --configs ext cram0 ctxcp0 cms256 fa-off ub1024 cram0-cms256
python scripts/engine/sweep.py --models g7-2b --configs ext ctxcp0 cms0 cram0 fa-off ub1024 cram0-cms256 cpu
python scripts/engine/sweep.py --model q5=~/models/x-Q5_K_M.gguf:agent --configs ext     # next: quants
python scripts/engine/shape.py                     # offline prompt-layout / section analysis
```

Outputs: `bench/engine/runs/<sweep>/<model>__<config>/{report.json,report.md,server.log}` and
`sweep.{json,md}` (gitignored). Each report starts with a one-line **config fingerprint**:
`llama.cpp <build_info> | <model file> sha256:<12> | <quant> | <server flags>` (+ a 10-char id). Model
list and configs are plain dicts in `sweep.py` (`MODELS`, `CONFIGS`); a quant variant or a LoRA entry is one
more row, and config flags may reference `{model}`.

Always bench a fresh server: llama-server's host-RAM prompt cache keeps whole prompts across requests,
so a second run of the same calls on the same server measures the cache, not the engine (sweep.py
restarts per config for this reason).

## 3. How each parameter is measured, and its limits

- **SPEED.** Client wall time per call; prefill = `timings.prompt_ms` over `timings.prompt_n` (tokens
  actually evaluated), decode = `timings.predicted_ms` / `predicted_n`; "other" = wall - prefill - decode
  (HTTP, template, grammar setup, sampling outside the timers). Task seconds = sum over an episode.
  One sequential client (the agent's real pattern); `-np 2` configs therefore do not show their
  throughput benefit. A one-token warm-up call precedes each run.
- **Prefix cache.** `timings.cache_n` = prompt tokens restored (n_past). Bench also renders each prompt
  with the server's own `/apply-template` + `/tokenize` and computes the longest common token prefix
  with any earlier prompt of the run ("reusable"); "captured" = cache_n / reusable shows a silent
  re-prefill directly.
- **QUALITY.** Agent: canonical action (`MicroAction.show()`, reason line dropped) == expected.
  Repair: as `repair_bench.py` — a PICK on the wrong item or on a deleted control is a *wrong repair*;
  NOT_FOUND / unparseable is an abstain. Verify: label match; false-VERIFIED = VERIFIED on a NOT_DONE /
  UNSAFE row, over all failure rows. Temperature 0, grammar on, thinking off (as the product).
- **MEMORY.** A sampler thread polls `ps` RSS every 0.5 s and `footprint -p` (phys_footprint, sudo-free)
  every 2 s. On Apple Silicon the GGUF is mmapped and Metal uses those pages without a copy, so they
  are *clean* and not in phys_footprint; `est_peak_unified = peak footprint + model file size`. RSS is
  reported but is not meaningful for mmapped weights (pages can be resident without counting).
- **COMPUTE.** Tokens from timings (evaluated vs cached vs generated). Server CPU seconds from `ps`
  cputime before/after. GPU busy seconds integrate IOAccelerator "Device Utilization %" (ioreg,
  sudo-free) above an idle baseline taken just before the run: it is whole-GPU (the display and the
  owner's :8080 server count too) and coarse (0.5 s samples). **Energy is not measured**: the only
  per-process / GPU energy source on macOS is `powermetrics`, which needs sudo.

## 4. First results (2026-10-26, this laptop)

Hardware: MacBook Air, Apple M5 (10 cores: 4P + 6E), 16 GB unified memory, macOS 26 (Darwin 25.5).
Engine: Homebrew llama.cpp `b11146-7fe450e19` (= CI's `LLAMA_TAG`). Models: `a-final` 4B action model
(`qwen3.5-4b-r2a-final-Q4_K_M`, 2 shards, sha256 `3d791ae49309+fbd0300e1418`, 2583 MB) on agent steps;
`g7-rv` 2B repair + verify model (`qwen3.5-2b-g7-rv-Q4_K_M`, sha256 `a63e356f91aa`, 1215 MB) on repair +
verify calls. Subsets: 4B `--budget agent=40` (43 calls, 7 episodes); 2B `repair=30,verify=30`. One run per
config, fresh server each, the owner's idle 2B server on :8080 left running throughout (it shares the
GPU). Total measured wall time ~85 min, over the 45-min target, because the two CPU-only 2B configs took
~35 min between them (see the `cpu-t10` row).

### 4.1 Baseline: the current extension flags (`ext` = `scripts/awos_model.sh`: `-c 4096 -np 1 --cache-prompt`, Metal)

| | 4B a-final, agent step | 2B g7, repair | 2B g7, verify |
|---|---|---|---|
| SPEED s/call (p90) | **3.79** (5.23) | **0.99** | **1.51** |
| prefill / decode / other s | 2.71 / 0.66 / 0.42 | 0.29 / 0.66 / 0.04 | 0.83 / 0.59 / 0.08 |
| prefill tok/s, decode tok/s | 333, 17 | 661, 35 | 757, 32 |
| per task | 23.3 s per episode (6.1 steps) | 1.29 s per chore (1.3 picks) | 1.51 s |
| QUALITY | exact action 92.9% (26/28 known steps); 3/5 episodes all steps right; 95.3% agree with the CI replies; 0 invalid | 30/30 right; wrong-repair 0%; NOT_FOUND right 22/22 | accuracy 83.3% (25/30); false-VERIFIED 0/5; VERIFIED recall 84% |
| MEMORY peak footprint (+ weights) | **8909 MB** (est. unified 11.5 GB) | 4129 MB (est. 5.3 GB), whole 2B run | |
| COMPUTE tokens/call | 1188 prompt (902 evaluated, 24% from cache), 11.2 generated | 504 prompt (410 evaluated, 19% cached), 21.2 generated (both kinds) | |
| server CPU / GPU busy per call | 0.32 s / 2.78 s | 0.21 s / 1.05 s | |

`other` is time inside the request outside llama.cpp's prefill and decode timers. For the 4B model it
is 0.42 s per step (11%), and it disappears with `--cache-ram 0`: it is the host prompt cache saving
the slot state after every request.

Cache: 24% of the 4B agent prompt tokens came from the cache. A perfect prefix cache could reuse 27%
(token LCP), so llama.cpp captured 89% of what the layout allows: checkpoints do work for Qwen3.5 here.
They restore at the end of the system prompt (293 tokens), not at the full common prefix (~350 tokens,
which also covers the goal line). Energy was not measured (sudo).

### 4.2 Sweep

4B a-final, 43 agent calls (`vs first` = change in s/call vs `ext`):

| config | flags beyond ext | s/call | prefill s | decode s | other s | cached | act match | footprint MB | est. unified MB | CPU s/call | vs ext |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ext | — | 3.79 | 2.71 | 0.66 | 0.42 | 24% | 93% | 8909 | 11492 | 0.32 | — |
| cram0 | `--cache-ram 0` | 3.37 | 2.71 | 0.65 | 0.02 | 24% | 93% | **570** | 3153 | 0.10 | −11% |
| ctxcp0 | `--ctx-checkpoints 0` | 3.80 | 3.14 | 0.61 | 0.05 | 0% | 93% | 3877 | 6460 | 0.10 | +0% |
| cms256 | `-cms 256` | 3.55 | 2.49 | 0.61 | 0.45 | 24% | 93% | 8801 | 11384 | 0.27 | −6% |
| fa-off | `-fa off` | 3.56 | 2.51 | 0.63 | 0.42 | 24% | 93% | 8814 | 11397 | 0.30 | −6% |
| ub1024 | `-b 2048 -ub 1024` | 4.10 | 3.12 | 0.75 | 0.23 | 24% | 93% | 8735 | 11318 | 0.30 | +8% |
| **cram0-cms256** | `--cache-ram 0 -cms 256` | **3.07** | 2.46 | 0.61 | 0.01 | 24% | 93% | **569** | **3152** | 0.07 | **−19%** |
| cpu (6 calls) | `-ngl 0` (4 threads) | 45.2 | 42.8 | 2.2 | 0.1 | 24% | 6/6 | 4392 | 6975 | 179 | ×12 |

2B g7, 30 repair + 30 verify calls:

| config | s/call | repair s | verify s | prefill s | decode s | cached | quality | footprint MB | vs ext |
|---|---|---|---|---|---|---|---|---|---|
| ext | 1.25 | 0.99 | 1.51 | 0.56 | 0.63 | 19% | rep 100% / wrong 0%; ver 83% / falseV 0% | 4129 | — |
| ctxcp0 | 1.47 | 1.05 | 1.89 | 0.64 | 0.79 | 0% | same | 1493 | +18% |
| cms0 | 1.22 | 1.02 | 1.41 | 0.55 | 0.62 | 19% | same | 4128 | −2% |
| cram0 | 1.14 | 0.97 | 1.32 | 0.53 | 0.60 | 19% | same | **305** | −8% |
| fa-off | 1.17 | 0.99 | 1.36 | 0.52 | 0.61 | 19% | same | 4135 | −6% |
| ub1024 | 1.20 | 0.98 | 1.42 | 0.55 | 0.62 | 18% | same | 3420 | −4% |
| **cram0-cms256** | **1.01** | 0.85 | 1.16 | 0.46 | 0.53 | 19% | same | **307** | **−19%** |
| cpu (`-ngl 0`, 4 thr) | 10.8 | 5.8 | 15.7 | 9.45 | 1.27 | 19% | same | 5342 | ×8.7 |
| cpu-t10 (`-ngl 0 -t 10`) | 58.5 | 58.5 | — | 11.9 | 46.5 | — | (stopped after 20 calls) | 2275 | ×47 |

Smoke run, ext flags: the 2B g7 model on 16 agent steps took 1.45 s per step (prefill 810 tok/s,
decode 36 tok/s). That is 2.6× faster than the 4B model, but only 31% exact match: it is not an action
model.

### 4.3 What mattered

1. **`--cache-ram 0` is the big one, for MEMORY and for SPEED.** The default host-RAM prompt cache
   (8 GiB) grows the 4B server to 8.9 GB footprint (11.5 GB with weights, on a 16 GB laptop) and the 2B
   to 4.1 GB. Saving the slot after each request costs 0.4 s per 4B step. In a single-agent sequential
   workload it buys nothing: the cached share is identical without it, because the slot's own context
   checkpoints do the reuse. Removing it cuts memory 94% and time 8-11%, with identical answers.
   Combined with `-cms 256` it is −19% per call on both models. The owner's :8080 server
   (`awos_model.sh`) and CI run with the default today.
2. **Metal vs CPU: ×9-12.** CPU-only prefill is 18 tok/s (4B) and 43 tok/s (2B), the same as the
   CI's 4-vCPU runners (~18 tok/s, ~67 s per 4B step). **`-t 10` on this 4P+6E chip is a trap**: decode
   fell to 0.5 tok/s (E-cores plus oversubscription), 47× slower than Metal. Never pass `-t $(nproc)`
   on the Mac (CI's 4-vCPU Linux runners are fine with it).
3. **Context checkpoints help a little, and cost memory.** With `--ctx-checkpoints 0`, prefill is
   16-18% slower (no reuse) but footprint drops. On the 4B the wall time was a wash, only because ext
   pays the cram overhead. `-cms 256`/`-cms 0` change nothing measurable: the system-prompt checkpoint
   is created at the default spacing already.
4. **Prefill dominates the agent step (71%, 2.5-2.7 of 3.1-3.8 s)**: 333-367 tok/s for a 4B on M5
   Metal is low. On the 2B repair/verify calls, decode dominates instead (53-67%): the model writes a
   ~20-token reason after `PICK n:` / `VERIFIED:`.
5. Flash-attn off, ubatch 1024, cms: within ±8%. That is about the run-to-run noise of a single run,
   which was not measured (one run per config); `ub1024` looks worse (+8%) and should be re-checked.

Quality was identical across every Metal config (same replies). No knob changed answers, so the
SPEED/MEMORY wins are free.

## 5. Next engine levers, ranked (expected gain from the numbers above)

| # | lever | expected gain | basis / risk |
|---|---|---|---|
| 0 | **ship `--cache-ram 0 -cms 256`** in `awos_model.sh` and the CI server lines | −19% s/call, −94% footprint (4B 8.9 GB → 0.57 GB) | measured; nothing to train. Re-check when several agents share one server (`-np > 1`): then the host cache may pay |
| 1 | newer llama.cpp with the GDN Metal kernel (PR #19504 / #20361) | prefill +25% (self-reported) → −15-20% per agent step, since prefill is 71-80% of it; decode 17 tok/s (4B) likely also improves | prefill 333 tok/s / decode 17 tok/s are far below dense models of this size on M-series Metal; one sweep row with a newer `--binary` |
| 2 | observation compression | exam: Regions (8%) + Page text (8%) largely duplicate each other and the Controls list; web: Page text is 36% and Controls 22% of the prompt → −15% (exam) to −35% (web) prompt tokens ≈ −10-25% per step | `shape.py` section shares; changes the model's input, so gate on bench `act match` (and retrain if it drops) |
| 3 | output shaping for the 2B (cap / drop the reason text) | repair −40-50%, verify −30% (decode is 0.53-0.66 s of 0.85-1.5 s, ~21 tokens) | measured decode share; the reason is logged for humans: make it optional or ≤ 40 chars; recheck wrong-repair / false-VERIFIED |
| 4 | prompt shaping for prefix reuse (history-first layout) | reusable 29% → 38% of agent tokens (`shape.py`); at the measured 89% capture → ~−13% evaluated tokens ≈ −9% per step; checkpoint at the true LCP (+goal line, ~60 tokens) adds ~−4% | history is append-only in 79% of exam steps (a window cuts it); `Step k of N` breaks the prefix right after the goal; needs a retrain on the new layout |
| 5 | MLX comparison | unknown; plausibly 1.3-2× prefill on Apple GPU if mlx-lm's GDN path is native | an MLX 2B q4g64 already sits in `~/.cache/awos-models/rl/base/`; needs a grammar-constrained OpenAI-compatible server to run this bench |
| 6 | quantisation sweep with an imatrix from agent data (Q4_K_M → IQ4_XS / Q3_K_M) | −15-25% weight bytes; ~5-15% speed; quality unknown | Metal prefill here is not bandwidth-bound, so the speed gain is modest; `sweep.py --model label=path` is ready; gate on act match / wrong-repair |
| 7 | 2B → 4B cascade | ≤ −10% per step at best: a 2B step is 1.45 s vs 3.1 s, but every escalation pays both | needs a 2B *action* model (g7 matches 31%) and a confidence gate; +1.2 GB resident |
| 8 | speculative decoding (MTP / ngram) | 4B: decode is 0.61 of 3.07 s, 11 tokens → −5-9%; 2B: −15-25%, but lever 3 is cheaper | tiny outputs: little to win on agent steps |
| 9 | one base + LoRA adapters vs separate models | −1.2 GB weights (no 2B), but repair/verify at 4B speed (+~2 s per call); after lever 0 memory (3.2 + 1.5 GB) is no longer the binding constraint | llama-server per-request LoRA untested here |

## 6. Limits and untested

- One run per config, so variance is unmeasured; differences under about 8% are not established.
  Subsets were small: 43 agent calls / 28 with a known answer, 30 repair, and only 30 verify calls
  with 5 failure rows (so false-VERIFIED = 0/5 is weak evidence). The workload's verify order puts
  few failures in a small budget; v2 should interleave the classes.
- Not run on this laptop: config `ci` (CPU, `-np 2`, 16k context) for the 4B model, the threads rows
  `t2` / `t8` on Metal, `ub128` / `ub2048`, `fa-on`, `ngl99` (auto already offloads all layers),
  `ctxcp1` / `ctxcp4`, the local live-web workload, `-np 2` throughput, quant / LoRA rows, and energy.
- Replay is open-loop and single-client; "episodes all steps right" is a task-success proxy, not
  verified task success.
- GPU busy seconds are whole-GPU above an idle baseline (the display and the :8080 server count);
  footprint excludes the mmapped weights by design (`est_peak_unified` adds them back).

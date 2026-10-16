# Handoff: where AWOS stands (2026-10-16)

For a new session (cloud or local) that has none of the laptop's memory. Branch: `claude/bench-run` (pushed).
**Never push to `main`.**

## Owner's standing rules

- Bootstrapped, so no infra spend. Free GitHub Actions on this public repo is fine; API spend needs explicit OK for anything over a few dollars.
- **Runs are paused** until the owner OKs them (said 2026-10-15). Building, testing with fakes and docs are fine.
- Never print API keys. No outreach or posting; the owner contacts people.
- Split non-colliding work across parallel agents. Pre-register every experiment (decision rule fixed before data).
- Model: direct Anthropic `claude-haiku-5-5` (OpenRouter off).

## Product direction

**Self-serve tool:** `awos fix <GitHub issue/PR URL>` gives a reviewed fix branch, with a usage ping (opt-in) and
licensing via Paddle (sandbox-first, checkout off until configured; steps in `docs/launch/paddle_setup.md`).
Site: https://awos.tirramind.com (Cloudflare Pages; deploys are blocked until the USAGE and LICENSES KV namespaces exist).

## What the benchmarks say (pre-registered; `docs/specs/harness_lift_ladder.md`, notes in `docs/memory/runs/`)

| run | result |
|---|---|
| H1 (old pipeline vs mini-swe-agent, 40 issues, Haiku) | 33 vs 39: REJECT |
| H1b (pipeline + fixes) | 34 vs 40: REJECT |
| H1c (held-out 30) | 30 vs 30: INCONCLUSIVE (ceiling), AWOS ×2.2 cost |
| H1d (new `loop` engine vs mini) | **40 vs 39**: INCONCLUSIVE, **×0.62 cost**, so `loop` became the default engine |

**Honest reading:** on a strong model the model does the work. AWOS's measurable edge is cost (prompt caching, fewer turns)
and product (sandbox, verified branches, background jobs). REA reverse-engineering of 13 products
(`docs/research/rea_synthesis_2026-10-16.md`) agrees: no harness shows accuracy gains.

## Just built (not yet run on a real model)

Local-model reliability, items B1–B5 of the REA synthesis:
- grammar-constrained loop replies (`loop_grammar.py`, `AWOS_LOOP_GRAMMAR`)
- byte-stable prefix and cache telemetry
- prefix-KV restore (`local_runtime.py`)
- n-gram speculation (`AWOS_LOCAL_SPEC`)
- idle, battery and memory policy

Commit fcf30f0.

## Next (each needs the owner's OK to run)

1. **Laptop only (local llama-server, Qwen3.5-9B):** a 5-minute real check, then **H2** (loop vs mini-local, 15 issues) and
   **H2-g** (grammar on/off), per amendment 5. Can't run in the cloud.
2. **Cloud-capable:** Terminal-Bench 2.1 head-to-head (AWOS vs Claude Code, OpenHands, OpenCode, Goose, mini, Terminus 2).
   - Pre-registered in `docs/specs/harbor_head_to_head.md`.
   - Pipeline on local branch `harbor-h2h` (commit 5989de6; push it to use it). GitHub Actions on ubuntu with Docker.
   - Pilot ~$2–5, main ≤ $50 (ask first).
3. **Roadmap** (`rea_synthesis`): cost routing (cheap or local first, one-way escalation), behavioural stuck detector
   (nudge, never stop early), per-turn checkpoint/resume and compaction, safety items.

## Where things live

- Benchmark data: `~/.awos-harness/` on the laptop only. Encrypted copies are release assets `bench-data-fresh-v1`,
  `bench-data-heldout-v1`; the key is repo secret `BENCH_DATA_KEY`.
- CI: `.github/workflows/ladder.yml` runs on push to `bench-cloud/**` (macOS runners).
- Tests: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/<file>`. Roughly 38 failures in the full suite are
  pre-existing (env/planner key/corpus files).

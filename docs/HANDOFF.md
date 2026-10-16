# Handoff: where AWOS stands (2026-10-16)

For a new session (cloud or local) that has none of the laptop's memory. Branch: `claude/bench-run` (pushed).
**Never push to `main`.**

## If you are a cloud session (claude.ai/code)

There is **no `ANTHROPIC_API_KEY` in this environment**, on purpose: environment variables are visible to anyone
using the environment. Build and test with fakes here. Anything that calls the model (benchmarks, real `awos` runs)
goes through **GitHub Actions**, which holds the key as the repo secret `ANTHROPIC_API_KEY`. Launch a run by pushing a
`bench-cloud/**` branch (ladder) or `bench-cloud/harbor-**` (Terminal-Bench, after merging `harbor-h2h`), then collect
results with `gh run download`. Local-model work (llama-server, Qwen) runs only on the owner's laptop.

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

**REA (reverse engineering of 13 AI products):**
- **Full write-ups:** the **private** repo `savabs/REA-writeups` (README.md + report.md per investigation; start at
  `investigations/INDEX.md`). Clone it next to this repo, e.g. `gh repo clone savabs/REA-writeups ../REA-writeups`.
  The Claude GitHub app needs access to that repo.
- **Raw evidence:** stays on the laptop (`~/Projects/REA`).
- **In this repo:** `docs/research/rea_digest_2026-10-16.md` (per-product lessons, own words) and
  `docs/research/rea_synthesis_2026-10-16.md` (ranked AWOS roadmap).
- **Never copy REA material** (decompiled code, extracted prompts, report text) into this **public** repo, and keep
  `REA-writeups` private.

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

## Built 2026-10-16 in a cloud session (branch `claude/loop-executor-tests-2i0l7p`; fakes only, nothing run on a model)

REA roadmap items A6–A8, C9, C10, D13, D15. Pre-registered decision rules and defaults: `docs/specs/loop_reliability_ab.md`.
- **A7 stuck detector** (`loop_stuck.py`, `AWOS_LOOP_STUCK=off|log|nudge`, default `log` = counts only, prompt unchanged).
  Triggers: repeat_command, failure_rate (≥15 of last 20), failure_signature, no_progress. Never stops a run.
- **A6 cost routing** (`loop_route.py`, `AWOS_LOOP_ROUTE="local,claude-haiku-5-5"`, default off). One-way escalation on
  stuck / rung step allowance (`AWOS_LOOP_ROUTE_RUNG_STEPS`, 30) / format or truncation limit / model error / red gate.
  The next rung starts a fresh conversation with a handover note on the same tree.
  **A8:** labelled decisions go to `.awos/route_decisions.jsonl`.
- **C9 checkpoint/resume** (`loop_checkpoint.py`). The host sets `AWOS_LOOP_CHECKPOINT_PATH` for jobs with a worktree. A killed
  attempt resumes the same conversation (no command re-run; the cost cap counts only the new attempt).
- **C10 compaction** (`loop_compaction.py`, `AWOS_LOOP_COMPACT=1`, default off). Summary started at 70% of the window and
  swapped in at 85%; the goal is kept verbatim; tool pairs are never split.
- **D13** read-only `.awos .claude .agents .codex .cursor .husky` (plus `.git`, always) in Seatbelt and docker
  (`AWOS_SANDBOX_READONLY_PATHS`, `AWOS_SANDBOX_READONLY=0`). The Seatbelt path is untested on real macOS.
- **D15** secret scan of the diff's added lines at hand-off and before `awos fix` pushes, fail closed
  (`secret_scan.py`; `AWOS_SECRET_SCAN=0` is for emergencies only). The `awos:allow-secret` pragma waives only the
  generic rule, never a key format.
- The cloud container has Python 3.13: `aider-chat` (needs <3.13) can't be installed there; the rest of
  `requirements.txt` installs.

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

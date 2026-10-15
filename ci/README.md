# Harness-lift ladder on GitHub Actions

(The Harbor / Terminal-Bench 2.1 head-to-head is separate: `ci/HARBOR.md`.)

`.github/workflows/ladder.yml` runs H1 of `docs/specs/harness_lift_ladder.md`
(AWOS `off` vs mini-swe-agent `mini`, `claude-haiku-5-5`, the 40 fresh-set
issues, 1 repeat, $5 cap) on `macos-14` runners, split into shards, so the run
no longer depends on the laptop staying awake. Each shard runs the same driver
as on the laptop:

    env -u OPENROUTER_API_KEY AWOS_SERIES_MODEL=claude-haiku-5-5 \
      python scripts/ablation_awos_vs_aider.py run --series-root ~/.awos-harness/fresh_series \
        --out ~/.awos-harness/ladder_haiku --arms off,mini --cap <CAP/N_SHARDS> --repeat 1 \
        --min-issues 15 --parallel 1 --shard <i>/<N_SHARDS> [--limit <LIMIT>]

`--shard i/N` keeps the issues whose index in the sorted list of all 40 is
`i mod N`, and arm order still follows that global index. So the 8 shards
together run exactly the laptop schedule, with the same interleaving.

## Files

| File | Purpose |
|---|---|
| `ci/ladder.env` | All settings: `ARMS`, `N_SHARDS`, `SHARDS`, `CAP` (total), `LIMIT`, `REPEAT`, `MODEL`, dataset tag/asset |
| `ci/ladder-requirements.txt` | Exact pip freeze of the laptop `.venv` that runs the driver, AWOS and the grader. No pytest-xdist |
| `ci/mini-requirements.txt` | Exact freeze of `~/.awos-harness/venvs/mini` (mini-swe-agent 2.4.6, litellm 1.105.0) |
| `scripts/ladder_collect.py` | `ci-config`, `check-data`, `stage` (runs on the runner); `merge` (runs on the laptop) |

## One-time setup (owner)

1. **Dataset asset.** The workflow downloads tag `bench-data-fresh-v1`, asset
   `fresh_series.tar.gz.enc`, made like this:

       tar -C ~/.awos-harness -czf - fresh_series \
         | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BENCH_DATA_KEY \
         > fresh_series.tar.gz.enc
       gh release create bench-data-fresh-v1 fresh_series.tar.gz.enc --title "bench data (encrypted)" --notes "AES-256, key in secret BENCH_DATA_KEY"

   tar keeps file mtimes, so `_tools/validate.json` stays newer than the series
   files and job_series reuses the cached preflight. A tar round trip of the
   real set was checked: 40/40 issues still hit the cache. The `check-data` step
   prints this per shard. If the set changes, re-run `validate_all.py`, rebuild
   the asset, and bump the tag in `ci/ladder.env`. (`_tools/tarballs`, 225 MB,
   is not needed by the run. Leaving it out would shrink the asset.)
2. **Secrets** (repo → Settings → Secrets and variables → Actions):
   `ANTHROPIC_API_KEY` and `BENCH_DATA_KEY`. The workflow's `GITHUB_TOKEN` has
   `contents: read`, which is enough for `gh release download`.

## Launch

The workflow runs on a push to any `bench-cloud/**` branch, or from the Actions
tab (workflow_dispatch). Launch from the commit you want measured; it is
recorded in every result's provenance.

**Smoke run** (1 shard, 1 issue, about $0.15 and 15 min). Edit `ci/ladder.env`:

    SHARDS=0
    LIMIT=1

then:

    git switch -c bench-cloud/ladder-smoke
    git commit -am "ladder: smoke run" && git push -u origin bench-cloud/ladder-smoke

Check the `ladder-shard-0` artifact: both arms ran, `preflight CACHED`, rows
valid, `mini` row with `sandbox=on`.

**Full H1.** Set `SHARDS=all` and `LIMIT=` (empty) again, then push to a fresh
branch, for example `bench-cloud/ladder-h1`. Don't reuse the smoke branch. A
re-run starts from empty out dirs, so the smoke issue would be paid for twice.
The smoke result is not counted: H1 is the full run only.

Each shard gets `CAP / N_SHARDS` = $0.625 and stops itself the same way the
laptop driver does (the projection of the costliest issue so far). The total
can't pass $5, but a shard with expensive issues can stop before it finishes
all 5 of its issues. `merge` lists any issue that was not done. Run those
issues on the laptop into the same merged out dir; the driver resumes and
skips done issues.

## Collect

    gh run list --workflow ladder.yml --limit 5
    gh run download <run-id> -D ~/.awos-harness/ladder_haiku_ci_dl
    python scripts/ladder_collect.py merge --out ~/.awos-harness/ladder_haiku --arms off,mini \
        ~/.awos-harness/ladder_haiku_ci_dl
    python scripts/ablation_awos_vs_aider.py combine --out ~/.awos-harness/ladder_haiku --arms off,mini
    python scripts/stats_audit.py ~/.awos-harness/ladder_haiku/combined.json --a mini --b off --cost-budget 0.5

`merge` keeps each `r1/<issue>/...` path. The same path in two shards must be
byte-identical, or merge stops with an error. The driver stdout of each shard
and `merge.json` (assigned/done/not done, total spend) go to `_shards/`. Merge
into an empty or matching out dir. The paused archive
`ladder_haiku_paused_slow` is not counted (Amendment 1).

## What only a real run shows

- `sandbox-exec` on a hosted macOS VM, for both the mini arm's profile and
  AWOS's seatbelt profile (Python lives in `~/hostedtoolcache` there, not
  `/opt/homebrew`).
- The pinned wheels install on the runner's Python 3.12 (setup-python picks
  the latest 3.12.x; the laptop has 3.12.14).
- Runner speed: about 8 min per issue on the laptop, 5 issues per shard. The
  job timeout is 300 min.
- GitHub runs at most 5 macOS jobs at once on free plans, so 8 shards run as
  5 + 3.

## What becomes public

On a public repo, anyone can read the Actions logs, and any signed-in GitHub
user can download the artifacts (kept 30 days) and the release asset. That
means:

- **Logs:** the driver's stdout (issue names, arm order, spend, pass/fail
  lines). Secrets are masked by GitHub and never echoed; the workflow uses no
  `set -x`.
- **Artifacts:** per issue, `job_series_*.json` (results, provenance, test
  summaries), `report.md`, `health.json`, each arm's `run.log` (the agent's
  transcript, including code and test output from the open-source project),
  `llm_calls.jsonl` (per-call tokens and cost, no prompts), `state/report.json`
  and `driver.log`. They never include `project/` copies, mini trajectories or
  the AWOS ledger.
- **Release asset:** the dataset is AES-256 encrypted. Without
  `BENCH_DATA_KEY`, the hidden tests and goals stay private. They do appear in
  pieces in the results above, such as failing test ids and goal text in logs.
- **The workflow file and this README** are public.

On a private repo nothing is public, but macOS minutes are billed (macOS counts
10x against the free minutes). That breaks the "no spend" rule, so use a public
repo or accept the cost explicitly.

# M0 from the home network (residential IP, sandbox VM), 2026-10-26/27

Same 100 dev tasks, harness and caps as [m0_kaggle_baseline.md](m0_kaggle_baseline.md) (25 steps / 900 s per task),
but run inside the sandbox VM (`awos-sandbox`) from the owner's home connection, with an ordinary visible
Chromium (`AWOS_WEB_HEADFUL=1`, no stealth), one browser worker, the model on the host Mac (llama.cpp Metal,
`--cache-ram 0 -cms 256`) through an SSH reverse tunnel.

## Result (judged 2026-10-27, rl-web-judge-kaggle 40501752650, WebJudge-7B, 0 judge errors)

Run A here is its first pass: 8 episodes overlapping the guest-display stalls were not yet rerun (they can only
lower its score).

| | base Qwen3.5 4B | run A final |
|---|---|---|
| **official success** (all 100) | **14%** (Wilson 95% CI 9-22) | **18%** (12-27) |
| reachable-site success | 18.7% (14/75) | 24.0% (18/75) |
| blocked by the site | 22 (Kaggle: 43) | 22 (Kaggle: 43) |
| paired: both / only A / only base / neither | 7 / 11 / 7 / 75; exact McNemar p = 0.48 | |

**Same tasks, both networks** (reachable from home AND from Kaggle):

| | home | Kaggle |
|---|---|---|
| base 4B (52 tasks) | 10 | 19 |
| run A final (50 tasks) | 10 | 17 |

**Reading.** The home setup roughly halves success on the very same tasks, so the drop is the environment, not the
newly reachable sites. Where Kaggle succeeded and home failed, the home episode mostly ended on a page that had not
finished loading ("page is still loading / no controls visible", repeated "control 1 is not on this page", the
900 s cap after 3 steps): the VM (disk image on the external HDD, visible browser) shows the model half-loaded pages.
A few were geography ("not available in your area" / "does not support your country"; the tasks are US sites,
the home IP is Indian). Fewer blocks did not buy a better score.

**What to do with it.**
- Kaggle stays the benchmark environment; report the reachable-site rate next to the official one.
- The harness should wait for the page to settle (and re-observe once when a page shows no controls) before the
  model acts: it helps everywhere, not only in the slow VM.
- A home re-run only makes sense after moving the VM image to the internal SSD; geography remains.

---

**Status (2026-10-27 06:00 UTC): rollouts finished; judging (WebJudge-7B on Kaggle) pending.**
- Results: encrypted on release `rl-home-web-dev-v1` (`<model>-dev.tar.gz.enc`, BENCH_DATA_KEY; the repo is public,
  the task texts are gated). Judge: push `bench-cloud/rl-wj7b-home` (ci/rl_web_judge_kaggle.env has
  `SOURCE_ENC_RELEASE="rl-home-web-dev-v1"`); the scores are in that run's step summary.

## Rollout counts (before judging)

| | base Qwen3.5 4B | run A final |
|---|---|---|
| clean episodes (no infra error) | 97 | see judge run |
| blocked by the site | 22 (vs 43 from Kaggle) | 22 of 97 at first pass |
| agent answered | 19 | 17 at first pass |
| hit the 25-step cap | 6 | 22 at first pass |
| seconds per model step (M5, Metal) | 7.9 | 5.7 |

Blocking roughly halves from home (22-23% vs 43%). From an Indian IP the US sites add region pop-ups and
"not available in your area" pages (healthgrades 403s), which Kaggle (US) did not see.

## Infrastructure incidents (all re-run, none counted as model failures)
- Host Mac slept 14:11-14:17 UTC: the VM lost its network; the run hung ~3.5 h unnoticed. Since then: `caffeinate`
  on the host, a watchdog (VM reachable, episodes growing, llama-server `/slots` id_task growing), and the VM code
  sync no longer deletes `runs/`.
- The model server was stopped mid-run (laptop overloaded): 11 base episodes ended "model call failed: URLError".
  The runner now counts model-server-unreachable as an infra error (resume reruns it); the 11 were rerun.
- Cold Chromium launch from the external-HDD VM disk passed the 180 s launch timeout: a warm-up launch precedes
  each run.
- The guest display slept twice (01:11-01:20, 04:08-04:24): the visible browser stalled. Every run-A episode
  overlapping those windows (8) was rerun; the guest now declares user activity every 2 min.
- 3 tasks crash Chromium for both models (TargetClosedError) on every attempt: excluded from the paired
  comparison, failures in the official score.
- The VM disk on the external HDD stalls I/O (SSH logins up to 80 s, 6 MB/min copies): moving the VM image to the
  internal SSD is the fix (owner's call).

## Next
- Judge both runs; compare official and reachable-site success with Kaggle M0.
- Third run: base 4B with the dialog fix (claude/web-modal, merged in 8079d83): `blocked_by_modal` refusals stopped
  ~16-22% of reachable episodes on both networks.

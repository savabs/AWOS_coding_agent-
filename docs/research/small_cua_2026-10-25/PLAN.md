# Best small computer-use model on a laptop (owner-approved 2026-10-25)

**Goal:** the best small, text-only (accessibility-tree) web agent that runs on a CPU laptop. It is proven on the public
**Online-Mind2Web** benchmark and released open source.

**Owner decisions (2026-10-25):**
- Paid official judge (o4-mini WebJudge, about $5-10 per 300-task run): OK.
- Claude as teacher for real-web demonstrations: OK.
- Open-source the model: yes.

## Benchmark
- [Online-Mind2Web](https://github.com/OSU-NLP-Group/Online-Mind2Web): 300 tasks on 136 real websites.
  - Code is MIT; the tasks are on [HF osunlp/Online-Mind2Web](https://huggingface.co/datasets/osunlp/Online-Mind2Web), CC-BY-4.0, gated with auto-approve.
- Judge: WebJudge.
  - o4-mini is the official, comparable judge, with about 85% agreement with humans.
  - WebJudge-7B is the free judge used for daily iteration.
- Bar, as reported by the vendors (single rollout):

  | Model | Online-Mind2Web |
  |---|---|
  | Fara1.5-27B | 72.3% |
  | **Fara1.5-4B** (same Qwen3.5-4B base as ours) | **57.3%** |
  | GPT-5 (medium, HAL generic harness) | ~42% |
  | MolmoWeb-8B | 35.3% |

  All of these are screenshot agents.

## Why this can work
- Our edge is a text-only observation: about 3-5x lighter than screenshots, and it fits a CPU laptop.
- The repair and verify models (2B, 0.3% wrong repairs, 16% false "done") make the agent stop rather than guess, and
  report its own failures.
- The training engine is proven: SFT on Kaggle 2x T4, with the RAM leak fixed, warm resumes, DPO, a GRPO harness, and exams
  with confidence intervals.

## The hard parts
1. **Domain gap.** Our models have only seen synthetic apps. Expect a low first real-web score; the M0 baseline sets
   the starting line.
2. **Data is the moat.** Real-web demonstrations will come from:
   - Mind2Web train (human), converted to our format;
   - MolmoWeb (ODC-BY);
   - Claude teacher runs on live sites;
   - self-improvement: our model attempts tasks, WebJudge-7B keeps the successes, and we retrain.
3. **Action space.** The real web needs Enter / submit, back and goto in addition to click / fill / select / scroll.
4. **Live sites from cloud IPs.** Bot checks and captchas: measure them, never bypass them, and report blocked tasks
   separately.
5. **Speed.** Real pages make prompts about 3-5x longer. Use a 100-task dev subset for iteration and the full 300 for
   official numbers. Run on Kaggle GPU (it has internet) when CPU runners are too slow.
6. **Honesty.**
   - Never train on the 300 test tasks (fuzzy-match exclusion).
   - Report website overlap.
   - Same judge for every model.
   - Report blocked tasks separately.

## Data engine findings (2026-10-25, docs/specs/web_data.md)

**Mind2Web train converts offline.**
- It covers 1,009 tasks on 73 sites. 61% of real steps map onto our numbered controls, giving about 2.8K rows (stop at
  the first unmapped step) or 6.7K rows (continue past it).
- **Text contamination: 0.** No Mind2Web train task matches any of the 346 Online-Mind2Web task texts.
- **Website overlap: 37 of 73 train sites** (517 tasks) are benchmark sites. Rows carry a flag for this, and
  `--exclude-overlap-websites` drops them. Train both ways and report both.

**MolmoWeb cannot be converted offline.** It has no DOM or accessibility tree, only live replay. Its
`task_seeded_om2w` subset is seeded from the benchmark itself, so **it must never be used**.

**Observation gaps found** (these limit the live agent too; fix them before SFT v4):
- links and buttons inside headings are dropped;
- JS-clickable div / li / span elements (autocomplete options, menus, calendar cells) are not controls;
- CDP "CBOR stack limit" errors on very deep pages;
- grounding refuses about 16% of real steps (ambiguous or unnamed controls);
- `fill` does not re-observe, so an autocomplete list that appears after typing is never seen.

The converter maps Enter to "click the form's only button". It should use the harness's new `fill_enter` verb now
that it has landed.

## Milestones
| | Target | Notes |
|---|---|---|
| M0 | Honest baseline on Online-Mind2Web: base 4B, run A final (and base 2B) | live harness + judge |
| M1 | Beat MolmoWeb-8B single rollout: > 35% | real-web SFT (Mind2Web + MolmoWeb + Claude teacher), then self-improvement rounds |
| M2 | Beat Fara1.5-4B: > 57% | long shot on free compute; DPO / GRPO on the judge's reward |
| Ship | Open-source model + report | after M1 |

## Workstreams (M0, started 2026-10-25, in parallel)
- **Live-web harness:** `scaffold/rl/web/` and `.github/workflows/rl-web.yml`. New actions, judge-layout output,
  blocked-page detection.
- **Judge:** `scaffold/rl/web_judge/` and `scripts/web/judge.py`. o4-mini (cost cap) and WebJudge-7B (Kaggle).
- **Data engine:** `scaffold/rl/train/mind2web_data.py`. Mind2Web to our format, with the contamination check.

**Owner action, done 2026-10-25:** `HF_TOKEN` is set, and the gated dataset downloads (rl-hf-check 40207562651).

**Update 2026-10-25: OpenAI shut down o4-mini on 2026-10-23** (OpenAI deprecations page). The official judge is
therefore **WebJudge-7B**, which agrees with humans 87.0% of the time per its authors (o4-mini: 85.7%). It is open,
runs free on Kaggle (`kaggle/web-judge/`), and replaces o4-mini for both daily and official numbers. No OpenAI key is
needed.

My earlier o4-mini estimate of $5-10 per run was wrong: the judge agent estimated $22-49 per 300 tasks at Standard
pricing.

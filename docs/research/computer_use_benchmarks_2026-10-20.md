# Computer-use benchmarks for AWOS (research, 2026-10-20)

Gathered by a research agent from web sources. Scores are as published; aggregator numbers are not verified.

## Recommendation

| Role | Benchmark | Why |
|---|---|---|
| Primary | **MacArena** (arXiv 2606.06560, ICML'26) | macOS, 421 tasks, about 50 apps; graded by final-state scripts; optional accessibility-tree observations (macapptree); runs in an Apple Silicon UTM VM at $0 on the laptop |
| Cross-check, in CI | **OSWorld-Verified with a11y_tree** | Ubuntu, 369 tasks; Apache-2.0; can run on free ubuntu runners (KVM availability to verify) |

- **Not suitable:** macOSWorld needs paid AWS Mac hosts and only takes screenshots.
- **Grounding check only:** ScreenSpot-Pro, which tests the pixel fallback.
- **Gap we fill:** no benchmark reports pass^k (repeat reliability) or cost per success, so we report both.

## Published reference points

| Benchmark | Model | Score |
|---|---|---|
| MacArena | UI-TARS-1.5-7B | 21.1% |
| MacArena | Qwen3-VL-4B | 24.2% |
| MacArena | OpenAI CUA | 31.8% |
| macOSWorld | UI-TARS-7B | 4.8% |
| macOSWorld | Claude CUA | 37.1% |
| OSWorld-Verified | OpenCUA-7B | 26.6% |
| OSWorld-Verified | UI-TARS-1.5-7B | 27.4% |
| OSWorld-Verified | Claude Opus 4.6 | 72.7% |
| OSWorld-Verified | Qwen3.5-27B | 56.2% |
| MacAgentBench | Opus 4.6 + OpenClaw | 73.7% |

- **MacAgentBench:** the authors credit a skill library for much of that lead, which matches AWOS's thesis.
- **Repeat reliability:** "On the Reliability of Computer Use Agents" (2604.17849) reports pass^k. Claude goes from 0.70 at pass^1 to 0.61 at pass^3. Agent S3 + GPT-5 reaches about 78% pass@10 but only about 36% pass^10.

## The AWOS Reliability Report (proposed format)

- **Runs:** k=5 per task, each from a fresh VM.
- **Success metrics:** pass@1, pass^5 and pass@5.
- **Cost per success:** dollars, model calls, seconds and Wh.
- **Refusals:** reported separately as a safe outcome.
- **Arms:**
  - (a) cold micro-agent;
  - (b) teach-once, with the demo on a held-out variant and then compiled;
  - (c) Haiku escalation on failures.
- **Statistics:** paired McNemar test.
- **Before running:** pre-register the protocol in `docs/specs/` and publish the trajectories.

## Risks

- **No Mac CI:**
  - GitHub macOS runners only pre-grant TCC to bash, osascript and Terminal.
  - Hosted arm64 runners do not support nested virtualization, so MacArena cannot run in hosted CI.
- **Apple's EULA:** two macOS VMs per Mac host.
- **Licences:** non-commercial licences on the macOSWorld and MacArena tasks restrict redistributing them.
- **Uneven AX support:** only about 33% of macOS apps fully support the accessibility tree (Screen2AX). Report AX-coverage failures explicitly.
- **Contamination:** OSWorld has been public since 2024. Enforce held-out variants for teach-once.

## One-week plan

| Day | Work |
|---|---|
| 1 | Install MacArena, run one task, check RAM, pre-register the protocol |
| 2 | Build an AWOS adapter for `mm_agents`, plus logging of cost and energy |
| 3 | Smoke-test 10 tasks; pick a 40-task subset (20 ported from OSWorld, 20 native) |
| 4 | Arm (a) with k=5; start the matching OSWorld run on ubuntu runners |
| 5 | Arm (b) |
| 6 | Arm (c); McNemar test |
| 7 | Write and publish the report |

## Sources

- MacArena: arxiv.org/abs/2606.06560
- macOSWorld: arxiv.org/abs/2506.04135
- MacAgentBench: arxiv.org/abs/2606.22557
- Reliability of computer-use agents: arxiv.org/abs/2604.17849
- OSWorld-Human: arxiv.org/abs/2506.16042
- OpenCUA: arxiv.org/abs/2508.09123
- Screen2AX: arxiv.org/abs/2507.16704
- Anthropic Opus 4.6 system card
- GitHub runner-images TCC script

# Xiaomi MiMo-V2.6: what is actually reusable (research, 2026-10-22)

Checked against the HF API, the GitHub API, Docker Hub and the technical report PDF.

## What was released, and the licences

| Item | What it is | Released | Licence |
|---|---|---|---|
| MiMo-V2.6-Pro-RL | ~1T parameters, FP8 | 2026-09-21 | MIT |
| MiMo-V2.6-Flash-RL | 309B total / 15B active MoE, 1M context, omnimodal | 2026-09-21 | MIT |
| MOPD variants | — | 2026-09-27 | MIT |
| **MiMo-V2.6-Distill-Qwen-9B** | SFT of Qwen3.5-9B (Apache base) | 2026-09-21 | MIT |
| RL environments (`XiaomiMiMo/MiMo-V2.6-RL-oss`) | ~7,684 tasks | 2026-09-25 | **Apache-2.0**, not MIT |
| Training code (`XiaomiMiMo/verl`, branch `mimo-oss`) | Fork of verl 0.9 | — | **Apache-2.0** |
| `mimoagent` (mini-swe-agent fork) | Agent | — | MIT |
| `uni-agent` | Agent | — | Apache-2.0 |
| MiMo Desktop | Desktop app | — | **Invite-only, closed** |
| MiMo-Code | OpenCode fork | — | MIT |

The technical report PDF is inside the model repos.

## The environments

| Config | Tasks | Reward |
|---|---|---|
| code | 2,602 | Executable tests |
| webdev | 2,093 | VLM visual grading |
| cyber | 1,000 | Rules |
| music | 1,000 | Rules |
| general | 989 (925 knowledge-work + 64 terminal-bench) | Rubrics, mostly LLM-judged |

- **Format:** verl parquet, with the task details in `instance_json`.
- **What a "general" environment contains:**
  - real PDF, XLSX, DOCX and PPTX files;
  - mock enterprise apps backed by SQLite;
  - MCP tool sidecars;
  - `verify.py` with weighted rubric items.
- **No GUI, computer-use or accessibility-tree environments were released.** The paper reports OSWorld-Verified scores of 82.0 (Pro) and 80.8 (Flash), but none of those training environments are public.
- **Everything is Docker:** 3,668 images of 0.4–3.3 GB each.
- **Reward hacking:** Vals AI found answers sitting in git history in about two-thirds of the coding tasks. A fix landed on 2026-10-19.

## The RL recipe

- **Algorithm:** GRPO with token-level importance ratios, four asymmetric clip bounds tuned from entropy, asynchronous partial rollouts (staleness 4), and **dynamic filtering of groups that all pass or all fail**.
- **Scale:** 1,568 prompts × 16 rollouts per step.
- **"You Only RL Once":** all domains in one batch. Code is 68%, tool use 12%, design 13%, context-following 3%, cyber 4%.
- **Rewards:**
  - binary outcome;
  - two grading schemes run by an agent grader: GRS (offline rubrics from contrasting rollouts) and GAR (online ranking of passing rollouts).
- **Penalties:**
  - a group-relative length penalty, applied only to successful rollouts;
  - **segment-level format and tool-error penalties**: flagged tokens are masked in good trajectories and amplified in bad ones;
  - an anti-hacking pipeline.
- **Compute:** thousands of GPUs. About $2.6M for Pro and $0.9M for Flash.
- **Released script defaults:** 4–8 nodes × 8 GPUs, 262K context, an external LLM judge (gpt-4o-mini).
- **Runs on one small GPU?** Not as shipped. Upstream verl supports LoRA, so one 24–80 GB GPU is possible with an 8–16K context. **Not practical on a free T4.**

## MiMo-V2.6-Distill-Qwen-9B

- **What it is:** SFT of Qwen3.5-9B on 77.4B tokens of MiMo trajectories, released as an RL starting point.
- **Benchmarks** (Qwen3.5-9B → this SFT → Xiaomi's per-domain RL, which is *not* released):

| Benchmark | Qwen3.5-9B | Distill SFT | + RL (unreleased) |
|---|---|---|---|
| SWE Verified | 60.0 | 61.1 | 66.2 |
| Terminal Bench 2.1 | 27.0 | 37.1 | 52.8 |
| AutomationBench | 5.0 | 30.3 | 33.1 |
| Toolathlon | 25.9 | 35.2 | 38.0 |
| OfficeQA Pro | 9.0 | 19.5 | 24.8 |

- **Harness transfer:** it beats the base model on all 21 dataset × harness pairs, including held-out harnesses. That matters for our new action grammar.
- **Formats:** GGUF from ggml-org and bartowski (~909K downloads), plus MLX 4-bit. Same architecture as Qwen3.5-9B, so it works in llama.cpp as-is.
- **Caveats:** no GUI or OSWorld numbers for the 9B. It is a thinking model, so cap or disable thinking.
- **Verdict:** **a strong replacement candidate for our 9B.** It does nothing for 2B or 4B, since no smaller distill exists.

## What we can reuse

**Directly:**
- **The Distill-9B GGUF,** as a quick A/B against Qwen3.5-9B on our tasks (about half a day).
- **The reward-shaping ideas** (about 50 lines each in our own trainer):
  - the group length penalty;
  - **segment-level penalties for malformed or invalid actions**, which map onto our action grammar;
  - dynamic group filtering;
  - asymmetric clipping.
- **The environment pattern:** a resettable SQLite mock app + tools + binary rubric items + checks for unintended changes. We could port it as accessibility-tree apps (about 1–2 weeks for a 50–100 task pilot).
- **The 925 general workspaces as task content.** Their LLM-judged verifiers are weaker than ours.

**Learn from only:** the verl fork and Docker images. They are cluster-scale.

## Risks

- **Licences differ** between code, data, models and the closed desktop app.
- **Real-world provenance in the content:** real company reports, and mock apps named after real brands. Re-check before any commercial redistribution.
- **The rewards need an LLM judge,** which costs money unless it runs locally.
- **The release is still changing** (a reward-hack fix landed 2026-10-19).
- **The infrastructure is heavy.**
- **Main gap:** none of the released environments are computer-use, **so we must build our GUI/accessibility-tree environments ourselves.**

## Sources

- huggingface.co/XiaomiMiMo/{MiMo-V2.6-Pro-RL, MiMo-V2.6-Flash-RL, MiMo-V2.6-Distill-Qwen-9B}
- huggingface.co/ggml-org/MiMo-V2.6-Distill-Qwen-9B-GGUF
- huggingface.co/bartowski/MiMo-V2.6-Distill-Qwen-9B-GGUF
- huggingface.co/datasets/XiaomiMiMo/MiMo-V2.6-RL-oss
- github.com/XiaomiMiMo/verl (branch `mimo-oss`)
- github.com/XiaomiMiMo/mimoagent
- VentureBeat; Towards AI ("You Only RL Once"); Gigazine (2026-09-09)

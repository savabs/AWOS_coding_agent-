# Computer use / GUI and OS agents

*Expedition chart 04, 2026-10-10. Built from four scout reports covering frontier agents, small local grounders, structured versus pixel control, and safety and sandboxing. The session's shared WebSearch budget ran out before any scout started, so every source here was fetched directly from a known primary URL. openai.com returned 403, so OpenAI figures are unverified. Nearly every benchmark number is self-reported by the authors. Work from late 2026 is probably under-covered.*

---

## 1. Summary

- **Raw success on OSWorld is solved at the frontier.** Agent S3 with Behavior Best-of-N (bBoN) scored 72.6% against a human baseline of 72.36%. Vendors now quote "OSWorld 2.0" or internal evals, for example Simular's unverified claim of 73% against 62.57% for "GPT-5.6 Sol". What still separates agents is **cost, latency, safety and reliability on the owner's own apps.**
- **Latency is the biggest practical gap.** Agents take 2.7-4.3x more steps than a human-optimal trajectory and tens of minutes per task. Late steps take about 3x longer than early ones because the context keeps growing (OSWorld-Human).
- **The leading systems mix code and API actions with GUI actions.** CoAct-1 reached 60.76% in about 10 steps, against about 15 for GUI-only agents. On WebArena, a hybrid API-plus-browsing agent beat browsing alone by more than 24 points. Models still **under-use tools**: the best tool-invocation rate on OSWorld-MCP was 36.3%.
- **Small open 2-8B models now ground clicks well but fail at long tasks.** On ScreenSpot-Pro, UI-Venus-1.5-8B scores 68.4 and MAI-UI-2B scores 57.4. Ferret-UI Lite 3B scores 53.3 on grounding but only 17-20% on OSWorld. On **macOS, open lightweight models score under 5%** (macOSWorld).
- **Device-cloud routing has been published and measured.** MAI-UI reports +33% on-device performance and more than 40% fewer cloud calls from task-state-based routing. That is close to the Gatekeeper cascade, though it was measured on Android only.
- **Training recipes are public and cheap.** FaraGen makes verifier-filtered trajectories for about $1 each. Fara1.5-9B, trained only with SFT, scores 63.4 on Online-Mind2Web, and the 27B model scores 72.3 against 58.3 for Operator. UI-TARS-2, OpenCUA and UltraCUA show working multi-turn RL and hybrid-action training at 7B and up.
- **Safety cannot rest on the model.** Adaptive attacks pushed ASR above 90% against most of 12 published defenses. Frontier hardening such as Opus 4.5's ~1% ASR comes from vendor RL that local models do not get. The industry pattern is **deterministic confinement plus critical-point confirmations plus sandboxed VMs**.
- **Isolated environments limit parallelism, not agent count.** On a Mac, Virtualization.framework and the EULA allow **at most 2 concurrent macOS VMs**. Linux guests have no such cap. Structured or headless control (CDP, AX, scripts) lets many agents share one host. Pixel agents need one display each.

---

## 2. What matters most (ranked)

1. **Structured-first action ladder, enforced by the harness.** Code and API calls first, then the accessibility tree or DOM, then pixels.
   - CoAct-1: 60.76% on OSWorld-Verified at 10.15 average steps. [arXiv 2508.03923](https://arxiv.org/abs/2508.03923)
   - Beyond Browsing: the hybrid agent scored 38.9% on WebArena, more than 24pp above browsing only. [arXiv 2410.16464](https://arxiv.org/abs/2410.16464)
   - OSWorld-MCP: o3 rose from 8.3% to 20.4% with tools, but the best tool-invocation rate was 36.3%. **The model will not route to tools on its own reliably, so the harness has to.** [arXiv 2510.24563](https://arxiv.org/abs/2510.24563)

2. **Verified-routine replay: record, abstract, replay with checks.**
   - Agent Workflow Memory: +51.1% relative on WebArena and +24.6% on Mind2Web, with fewer steps. [arXiv 2409.07429](https://arxiv.org/abs/2409.07429)
   - AgentRR replays recorded traces with check functions, framed around privacy and cost. [arXiv 2505.17716](https://arxiv.org/abs/2505.17716)
   - Stagehand replays cached actions with zero LLM calls on a hit. [docs](https://docs.stagehand.dev/v3/best-practices/caching)
   - None of these tools gates a replay on a postcondition. That is the gap AWOS fills.
   - Replay is also the most injection-safe tier, because it is a Plan-Then-Execute program ([arXiv 2506.08837](https://arxiv.org/abs/2506.08837)).

3. **Deterministic safety boundary.** Rule of Two, a critical-point taxonomy, and least privilege per routine.
   - Adaptive attacks broke most of 12 defenses (>90% ASR). [arXiv 2510.09023](https://arxiv.org/abs/2510.09023)
   - Meta's Rule of Two: hold at most two of untrusted input, sensitive data, and state change or external communication. [Meta](https://ai.meta.com/blog/practical-ai-agent-security/)
   - Gemini's per-action safety decision uses 7 categories and stops before the final irreversible click. [Gemini docs](https://ai.google.dev/gemini-api/docs/computer-use)
   - CaMeL gets provable security at 77% utility against 84% undefended. [arXiv 2503.18813](https://arxiv.org/abs/2503.18813)

4. **Test-time scaling with a judge: parallel rollouts or per-step candidates.**
   - Agent S3 rose from 62.6% to 69.9-72.6% with bBoN. With GPT-5 Mini plus bBoN it scored 60.2%, about 2pp below full GPT-5 run once.
   - The judge was correct on 92.8% of tasks where a better rollout existed.
   - Scouts disagree on N: one reports 10 rollouts, another reports best-of-3. No cost was published. [arXiv 2510.02250](https://arxiv.org/abs/2510.02250)
   - GTA1 applies the same idea per step and reached 45.2% with GTA1-7B. [arXiv 2507.05791](https://arxiv.org/abs/2507.05791)
   - Rollouts need resettable environments, so this does not work on the owner's live session.

5. **Planner/grounder split with a small local grounder and crop-zoom.**
   - Jedi: adding a trained grounder to GPT-4o took OSWorld from 5% to 27%. [arXiv 2505.13227](https://arxiv.org/abs/2505.13227)
   - ScreenSeekeR: cascaded search with no training raised ScreenSpot-Pro from 18.9 to 48.1. [arXiv 2504.07981](https://arxiv.org/abs/2504.07981)
   - Ferret-UI Lite uses predict, crop, predict again. [arXiv 2509.26539](https://arxiv.org/html/2509.26539)
   - The best permissive 2-8B options: MAI-UI ([arXiv 2512.22047](https://arxiv.org/abs/2512.22047)) and Holo2 ([HF](https://huggingface.co/Hcompany/Holo2-8B)), both Apache-2.0, and Fara1.5 (MIT).

6. **Latency engineering.**
   - Most latency comes from large-model calls for planning, reflection and judging. [arXiv 2506.16042](https://arxiv.org/abs/2506.16042)
   - UFO2's speculative multi-action cuts LLM calls by about 51%, with no change in success rate. [repo](https://github.com/microsoft/UFO)
   - Claude's toolset batches actions with stop-at-first-failure, and prunes old screenshots every ~25 turns in batches so the prompt cache survives. [docs](https://platform.claude.com/docs/en/docs/agents-and-tools/tool-use/computer-use-tool)
   - ShowUI pruned 33% of visual tokens. [arXiv 2411.17465](https://arxiv.org/abs/2411.17465)
   - A11y-Compressor cut tokens to 22% of the original and added +5.1pp. [arXiv 2605.00551](https://arxiv.org/abs/2605.00551)

7. **Cheap verified-data flywheels for local models.**
   - FaraGen: about $1 per verified trajectory. [arXiv 2511.19663](https://arxiv.org/abs/2511.19663)
   - Fara1.5 used about 2M SFT samples. [MSR blog](https://www.microsoft.com/en-us/research/articles/fara1-5-computer-use-agent/)
   - UltraCUA extracts tools from documentation and trains hybrid actions at 7B: +22% relative on OSWorld and 11% faster. [arXiv 2510.17790](https://arxiv.org/abs/2510.17790)
   - GUI-Actor trains only a ~100M-parameter head on a frozen 7B model and scores 44.6 on ScreenSpot-Pro, above UI-TARS-72B's 38.1. [arXiv 2506.03143](https://arxiv.org/abs/2506.03143)

8. **Reproducible, isolated environments.**
   - OSWorld-Verified fixed more than 300 broken tasks and reached 50x parallelism, cutting evaluation from 10+ hours to about 20 minutes. [xlang](https://xlang.ai/blog/osworld-verified)
   - UI-TARS-2, MAI-UI and Mobile-Agent-v3 all treat environment farms as a core contribution. MAI-UI gained +5.2pp going from 32 to 512 parallel RL environments.
   - On a Mac, the 2-VM cap is fixed. [Eclectic Light](https://eclecticlight.co/2022/08/04/virtualisation-on-apple-silicon-macs-8-how-apple-limits-vms/)

---

## 3. What does NOT matter / hype / dead ends

| Item | Why to discount it |
|---|---|
| Raw OSWorld v1 rank | Saturated, and about 300 tasks were broken. Scores swing with step budget (15/50/100), single run versus bBoN, and benchmark version. Use OSWorld-Verified, OSWorld-Human (efficiency) or the owner's own tasks. |
| ScreenSpot v1/v2 | Saturated: 2-8B models score 92-96. Use ScreenSpot-Pro, OSWorld-G, UI-Vision or the owner's screens. |
| Grounding score as a proxy for agent reliability | Ferret-UI Lite scores 53 on ScreenSpot-Pro but 17-20% on OSWorld. |
| Deep manager/worker hierarchies | Agent S3 got better by **removing** Agent S2's hierarchy. CoAct-1's gain comes from code actions, not from adding agents. Useful parallelism is independent rollouts plus a judge. |
| Family-wide headline numbers, and 72B+ grounders | Model cards often show the largest model's number (MAI-UI's card, UI-TARS-1.5's 42.5% from its closed large model). The jump from 8B to 30B is small (68.4 to 69.6). 72B+ models cannot run always-on on a laptop. |
| OmniParser + GPT-4o set-of-marks as the main pipeline | 39.6 against 57-68 for end-to-end small grounders, and it sends every frame to the cloud. Keep only the element-list idea, for caching and checks. |
| Older checkpoints (ShowUI, OS-Atlas, Aguvis, SeeClick, UI-TARS-1.5-7B) as the grounder to deploy | Superseded. Their ideas and data are still useful. |
| System-prompt injection defenses ("ignore pop-ups") | Shown ineffective: 86% pop-up click-through, VPI-Bench up to 100% on browser agents. |
| A standalone injection classifier as the boundary | Adaptive attacks reach >90% ASR. The AgentDojo PI detector cut utility to 41%. Use one only as a trigger for confirmation. |
| Low ASR on static benchmarks | "Security by incompetence": RedTeamCUA found a 92.5% attempt rate. Real harm rises as capability rises. |
| Vendor "human-level", "100x faster", ">95% success" claims | No public numbers, or vendor-run evaluations only. |
| Speculative multi-action as an accuracy lever | It cuts calls and does not change success rate. Treat it as a latency tool only. |
| Many sandboxed macOS VMs per Mac | Hard-capped at 2. |
| Full CaMeL on every task with a weak planner | Prior repo research measured about a 21pp utility cost. Apply it selectively. |

---

## 4. Key papers and resources

### Must-read
- **Agent S3 / Behavior Best-of-N**, [arXiv 2510.02250](https://arxiv.org/abs/2510.02250); [blog](https://www.simular.ai/articles/agent-s3); [repo](https://github.com/simular-ai/Agent-S) (Apache-2.0, runs on macOS). The test-time scaling recipe, with a code-agent action.
- **CoAct-1**, [arXiv 2508.03923](https://arxiv.org/abs/2508.03923). Shows code-as-action improves success and cuts steps.
- **OSWorld-MCP**, [arXiv 2510.24563](https://arxiv.org/abs/2510.24563). Tools help, but models invoke them only 36% of the time.
- **OSWorld-Human**, [arXiv 2506.16042](https://arxiv.org/abs/2506.16042). The only good measurement of latency and step efficiency.
- **MAI-UI**, [arXiv 2512.22047](https://arxiv.org/abs/2512.22047); [repo](https://github.com/Tongyi-MAI/MAI-UI). Measured device-cloud routing, with 2B/8B Apache-2.0 weights.
- **macOSWorld**, [arXiv 2506.04135](https://arxiv.org/abs/2506.04135). The only macOS benchmark: open small models score under 5%.
- **Fara-7B / FaraGen**, [arXiv 2511.19663](https://arxiv.org/abs/2511.19663), and **Fara1.5**, [MSR blog](https://www.microsoft.com/en-us/research/articles/fara1-5-computer-use-agent/). Verified trajectories at about $1 each, MIT-licensed 4B/9B/27B models, and stops at critical points.
- **OS-Harm**, [arXiv 2506.14866](https://arxiv.org/abs/2506.14866). 150 tasks covering misuse, injection and misbehavior. The safety gate to pass before a pilot.
- **The Attacker Moves Second**, [arXiv 2510.09023](https://arxiv.org/abs/2510.09023). Why model-level defenses cannot be the boundary.
- **Agents Rule of Two**, [Meta](https://ai.meta.com/blog/practical-ai-agent-security/), and the **six design patterns**, [arXiv 2506.08837](https://arxiv.org/abs/2506.08837).

### Useful
- **UFO2 / UFO3 Galaxy**, [repo](https://github.com/microsoft/UFO); [arXiv 2504.14603](https://arxiv.org/abs/2504.14603). UIA plus vision, a combined GUI and API action layer, about 51% fewer calls, and a picture-in-picture desktop.
- **UltraCUA**, [arXiv 2510.17790](https://arxiv.org/abs/2510.17790). Hybrid-action training recipe at 7B.
- **UI-TARS-2**, [arXiv 2509.02544](https://arxiv.org/abs/2509.02544); **OpenCUA**, [arXiv 2508.09123](https://arxiv.org/abs/2508.09123) (45.0% on OSWorld-Verified, the best fully open result); **Mobile-Agent-v3 / GUI-Owl**, [arXiv 2508.15144](https://arxiv.org/abs/2508.15144). Multi-turn RL and data flywheels.
- **UI-Venus-1.5**, [arXiv 2602.09082](https://arxiv.org/html/2602.09082). The best cross-model comparison table for 2-8B grounders. Repo: [inclusionAI/UI-Venus](https://github.com/inclusionAI/UI-Venus). UI-Venus-2: [arXiv 2609.00028](https://arxiv.org/abs/2609.00028); its README numbers are not confirmed.
- **Ferret-UI Lite**, [arXiv 2509.26539](https://arxiv.org/html/2509.26539). An honest account of what 3B models can and cannot do, with reward-design ablations.
- **Jedi / OSWorld-G**, [arXiv 2505.13227](https://arxiv.org/abs/2505.13227); **GTA1**, [arXiv 2507.05791](https://arxiv.org/abs/2507.05791) (its README swaps the ScreenSpot columns); **GUI-Actor**, [arXiv 2506.03143](https://arxiv.org/abs/2506.03143).
- **Agent Workflow Memory**, [arXiv 2409.07429](https://arxiv.org/abs/2409.07429); **AgentRR**, [arXiv 2505.17716](https://arxiv.org/abs/2505.17716); **Stagehand caching**, [docs](https://docs.stagehand.dev/v3/best-practices/caching); **Terminator**, [repo](https://github.com/mediar-ai/terminator) (its claims are unverified).
- **Beyond Browsing**, [arXiv 2410.16464](https://arxiv.org/abs/2410.16464); **AgentOccam**, [arXiv 2410.13825](https://arxiv.org/abs/2410.13825) (redesigning observations and actions alone gave +9.8pp over the prior SOTA).
- **Browser control:** [Browser Use, Playwright to CDP](https://browser-use.com/posts/playwright-to-cdp); [playwright-cli](https://github.com/microsoft/playwright-cli); **macOS AX:** [Peekaboo](https://github.com/steipete/Peekaboo).
- **Sandboxes:** [Cua/Lume](https://github.com/trycua/cua) (MIT); [Tart](https://github.com/cirruslabs/tart) (fair-source); [apple/container](https://github.com/apple/container) (Apache-2.0, one VM per Linux container).
- **Safety benchmarks:** [RedTeamCUA](https://arxiv.org/abs/2505.21936), [VPI-Bench](https://arxiv.org/abs/2506.02456), [WASP](https://arxiv.org/abs/2504.18575), [pop-up attacks](https://arxiv.org/abs/2411.02391), [OS-Sentinel](https://arxiv.org/abs/2510.24411), [AgentDojo results](https://agentdojo.spylab.ai/results/), [Progent](https://arxiv.org/abs/2504.11703), [Anthropic injection defenses](https://www.anthropic.com/research/prompt-injection-defenses), [MCP tool poisoning](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks).

### Reference
- [OSWorld paper (Table 5, a11y tree vs screenshot)](https://arxiv.org/html/2404.07972); [OSWorld-Verified blog](https://xlang.ai/blog/osworld-verified); [ScreenSpot-Pro](https://arxiv.org/abs/2504.07981); [UI-Vision](https://arxiv.org/abs/2503.15661); [A11y-Compressor](https://arxiv.org/abs/2605.00551); [ShowUI](https://arxiv.org/abs/2411.17465); [OmniParser V2](https://huggingface.co/microsoft/OmniParser-v2.0); [Holo2-8B](https://huggingface.co/Hcompany/Holo2-8B).
- Vendor APIs: [Claude computer-use toolset](https://platform.claude.com/docs/en/docs/agents-and-tools/tool-use/computer-use-tool); [Gemini Computer Use](https://ai.google.dev/gemini-api/docs/computer-use).
- [macOS VM limit (Eclectic Light)](https://eclecticlight.co/2022/08/04/virtualisation-on-apple-silicon-macs-8-how-apple-limits-vms/); [9to5Mac: MCP in macOS 26.1 beta](https://9to5mac.com/2025/09/22/macos-tahoe-26-1-beta-1-mcp-integration/) (early code only; unverified whether it has shipped).

---

## 5. Implications for AWOS

Mapped to the Gatekeeper tiers: replay, then local attempt, then verification gate, then bounded repair, then cloud escalation.

### Adopt now
- **Make the action ladder a router decision, not a model choice.** For each step, the Gatekeeper tries in order:
  1. a matching verified routine
  2. shell, Python or AppleScript/JXA/Shortcuts
  3. AX tree (macOS, via Peekaboo-style opaque element IDs) or a CDP accessibility snapshot with refs, called through a CLI rather than large MCP schemas
  4. pixel grounding, as a last resort

  The case for this order is the 36% tool-invocation rate in OSWorld-Human/MCP and the CoAct-1 and Beyond Browsing gains.
- **Replay tier as Plan-Then-Execute.** Replayed routines take UI and web content only as typed values, never as instructions. A routine stays in memory only if its postcondition probe passes, which Stagehand and Terminator lack. Match on stable element identity (AX role and label, or an OmniParser-style element list), not pixels.
- **Deterministic safety layer** (extending T10 postconditions and sink policy):
  - Tag each chore with Rule-of-Two A/B/C capabilities. When all three are present, force a confirmation, a fresh context, or escalation to a hardened cloud model.
  - Use a fixed critical-point classifier modeled on Gemini's 7 categories, keyed on action type, app, and AX role/label ("Send", "Pay", "Delete"). The model may add confirmations but never remove them.
  - Expose only the tools a routine needs (AgentDojo's tool filter scored 6.8% ASR at 72% utility).
  - Pin MCP tool descriptions by hash in the capability cache.
- **Cloud-tier hygiene.** Batch actions with stop-at-first-failure. Prune screenshots in cache-friendly batches. Keep screenshots at about 1280x720 and zoom when needed.

### Test next (each needs a pre-registered A/B, as in the E1 practice)
- **Owner-screen grounding eval.** Mine 100-300 screenshot/target pairs from verified AWOS runs on the owner's Mac. Measure accuracy, seconds per action and watts under MLX for MAI-UI-2B/8B, Holo2-4B/8B and UI-Venus-1.5-2B, with and without crop-zoom. No public source measures this, and macOSWorld's under-5% result means public scores cannot be trusted for this platform.
- **Structured-coverage ablation (E9).** What share of the owner's real chores can be done with AX, AppleScript or CDP alone? If it is high, pixel grounding becomes rare and parallelism becomes cheap.
- **Local bBoN versus a single cloud call.** Run N local small-model rollouts in Linux containers (apple/container) or Lume VMs, have a cloud or local judge pick one, and compare against one cloud rollout at equal success. Measure $, seconds and watts. This applies only to tasks that can be reset (browser tasks in headless profiles, file tasks on APFS clones).
- **Safety gate for the CU pilot.** Run OS-Harm's injection and misbehavior splits plus a VPI-Bench subset on the actual local model, reporting **both** attempt rate and ASR. Assume local ASR is closer to the 20-60% seen in 2024 than to Opus 4.5's ~1% until measured.
- **Fara1.5-4B/9B as the first try in the browser tier** for web chores, with critical-point stops mapped to AWOS confirmations.
- **MAI-UI-style routing signal.** Escalate on task-state features (step count, no screen change, low grounding confidence, critical point reached) instead of a fixed retry budget.

### Train (in scope, after the eval sets exist)
- **Per-owner flywheel:** during idle time a cloud model proposes tasks, the local model attempts them in a sandbox, multiple verifiers filter (FaraGen, about $1 per trajectory), and the survivors become replayable routines and SFT data.
- **Cheapest personalisation:** a GUI-Actor-style head or LoRA on the owner's verified clicks.
- **Next step up:** UltraCUA-style hybrid-action SFT plus RL, with dense grounding rewards. Ferret-UI Lite found that rewarding only the action type hurts.

### On running more than 8 agents in parallel (the user's question)
For computer use, the scouts' evidence points to three limits that matter more than agent count:
1. **Environment isolation.** Pixel agents each need their own display and input. A Mac allows only 2 macOS VMs. Linux containers or VMs (apple/container, Lume) have no such cap, and OSWorld-Verified reached 50x parallelism on cloud VMs.
2. **Control mode.** Structured agents (headless CDP browsers, AppleScript or AX on background apps, scripts) can share one host without fighting over the cursor. UFO2's separate agent desktop is the pattern for running next to the owner.
3. **Shared tool quotas.** This expedition was capped by a 200-call WebSearch budget shared across the whole session, not by the 8-agent concurrency limit. More agents would have added nothing. Raise `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`, give each agent its own quota, or have agents fetch known primary URLs.

The evidence-backed form of "more agents" is **N independent rollouts plus a judge**, not deeper delegation trees.

### Watch
- Apple MCP-over-App-Intents. If it ships, App Intents becomes the top structured rung on macOS, so build an adapter rather than a competing system.
- An OSWorld 2.0 independent leaderboard.
- UI-Venus-2 licence and verified numbers.
- OpenAI CUA / Atlas numbers (blocked by the 403).

### Ignore
Pure end-to-end pixel agents as the main path, deep agent hierarchies, prompt-based injection defenses, 72B+ local grounders, and ScreenSpot v1/v2 or raw OSWorld v1 numbers.

---

## 6. Open questions worth exploring next

1. What do 2-9B grounders actually cost on Apple Silicon (seconds per action, watts) at Retina resolution versus downscaled-plus-zoom? Nobody has measured this.
2. What share of the owner's chores can be done through structured surfaces only, and how often does a replayed AX/CDP routine fail its postcondition after an app update (staleness rate)?
3. Is local bBoN (N small-model rollouts plus a judge) cheaper per success than one cloud rollout? Agent S3 published no cost figures, and the scouts disagree on whether it used N=3 or N=10.
4. Does AX-only or DOM-only observation lower visual-injection ASR, or does the attack just move into hidden accessibility labels, as EIA's hidden elements suggest?
5. How often do real chores trigger a critical point or all three Rule-of-Two properties? This sets confirmation fatigue, which is the main usability cost.
6. How many structured agents can act on one macOS session without focus or keyboard collisions? When do they need separate user sessions or VMs?
7. Can a deterministic critical-point classifier match the recall of Gemini's per-step safety service?
8. How fast can a macOS guest on Lume snapshot and roll back, compared with APFS clones? Neither can undo external side effects such as emails sent or payments made.
9. What exactly is MAI-UI's device-to-cloud routing signal, and does it carry over from Android to desktop?
10. Which of these need verification before anyone relies on them: OSWorld 2.0 (who maintains it, task set), the Simular and "GPT-5.6 Sol" figures, current OpenAI CUA numbers, Agent S3's single-run score (one scout says 62.6%, another 66%), and whether Apple has shipped MCP for App Intents.

---

## 7. Sources

- https://arxiv.org/abs/2510.02250
- https://www.simular.ai/articles/agent-s3
- https://github.com/simular-ai/Agent-S
- https://arxiv.org/abs/2508.03923
- https://arxiv.org/abs/2506.16042
- https://arxiv.org/abs/2509.02544
- https://arxiv.org/abs/2508.09123
- https://arxiv.org/abs/2511.19663
- https://www.microsoft.com/en-us/research/articles/fara1-5-computer-use-agent/
- https://arxiv.org/abs/2504.07981
- https://platform.claude.com/docs/en/docs/agents-and-tools/tool-use/computer-use-tool
- https://xlang.ai/blog/osworld-verified
- https://arxiv.org/abs/2510.24563
- https://github.com/microsoft/UFO
- https://arxiv.org/abs/2504.14603
- https://arxiv.org/abs/2409.07429
- https://arxiv.org/abs/2505.17716
- https://arxiv.org/abs/2508.15144
- https://arxiv.org/html/2602.09082
- https://github.com/inclusionAI/UI-Venus
- https://arxiv.org/abs/2609.00028
- https://arxiv.org/abs/2505.13227
- https://arxiv.org/abs/2512.22047
- https://github.com/Tongyi-MAI/MAI-UI
- https://arxiv.org/html/2509.26539
- https://arxiv.org/abs/2507.05791
- https://arxiv.org/abs/2506.03143
- https://arxiv.org/abs/2506.04135
- https://arxiv.org/abs/2503.15661
- https://huggingface.co/microsoft/OmniParser-v2.0
- https://arxiv.org/abs/2411.17465
- https://huggingface.co/Hcompany/Holo2-8B
- https://arxiv.org/abs/2410.16464
- https://arxiv.org/abs/2510.17790
- https://arxiv.org/html/2404.07972
- https://arxiv.org/abs/2605.00551
- https://arxiv.org/abs/2410.13825
- https://browser-use.com/posts/playwright-to-cdp
- https://github.com/microsoft/playwright-cli
- https://docs.stagehand.dev/v3/best-practices/caching
- https://github.com/mediar-ai/terminator
- https://github.com/steipete/Peekaboo
- https://9to5mac.com/2025/09/22/macos-tahoe-26-1-beta-1-mcp-integration/
- https://arxiv.org/abs/2510.09023
- https://www.anthropic.com/research/prompt-injection-defenses
- https://arxiv.org/abs/2505.21936
- https://arxiv.org/abs/2504.18575
- https://arxiv.org/abs/2411.02391
- https://arxiv.org/abs/2506.02456
- https://arxiv.org/abs/2506.14866
- https://arxiv.org/abs/2503.18813
- https://arxiv.org/abs/2506.08837
- https://arxiv.org/abs/2504.11703
- https://ai.meta.com/blog/practical-ai-agent-security/
- https://ai.google.dev/gemini-api/docs/computer-use
- https://arxiv.org/abs/2510.24411
- https://eclecticlight.co/2022/08/04/virtualisation-on-apple-silicon-macs-8-how-apple-limits-vms/
- https://github.com/trycua/cua
- https://github.com/cirruslabs/tart
- https://github.com/apple/container
- https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks
- https://agentdojo.spylab.ai/results/

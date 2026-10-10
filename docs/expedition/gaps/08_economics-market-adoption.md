# Gap 08: Economics, market structure and adoption of personal/local AI computers

*Expedition gap chart, 2026-10-10. Sources were fetched on that date. The web-search budget ran out partway through, so a few landscape items are marked unverified rather than filled in from memory.*

## Summary

- **A dedicated local box does not pay for itself against cheap cloud models.** It pays only when the work it takes over would otherwise go to mid or frontier cloud models. At 50% local gate-pass, a ~$2.2k Mac mini breaks even at about **21 tasks/day** that would otherwise go to a Sonnet-class model. Against a DeepSeek-Flash-class model it needs **~520 tasks/day**, which no single owner reaches.
- **The dollar gap is widening against local at fixed capability.** The capability gap is closing. Cloud prices for a fixed capability fall roughly 5–50x per year: Epoch AI gives a 9–900x range, a16z ~10x and Gundlach et al. 5–10x. Local hardware got *more* expensive in 2026 because of DRAM: DGX Spark went from $3,999 to $4,699. Meanwhile, the open models a single consumer GPU can run trail the frontier by only 6–12 months (Epoch AI).
- **Frontier calls are not getting cheaper per call.** Running the state-of-the-art model costs 3–18x *more* per year (Gundlach et al.), so avoiding escalations is where local saves money.
- **Price is not what drives adoption.** On OpenRouter, a 10% price cut raises usage by only 0.5–0.7%. Ink & Switch's local-first ideals still fail in practice on infrastructure and developer convenience. Self-hosting is a community of a few thousand survey respondents, not a mass market.
- **Who local-first wins for today:** (a) owners who already have a capable Mac or PC, since the hardware is sunk and the marginal cost is electricity; (b) owners with privacy-bound or offline work; (c) heavy users who would otherwise escalate to Sonnet/Opus-class APIs at 20 or more tasks/day. It does not win for a light user on cheap open-model APIs or a $20/month subscription.

## What matters

### 1. TCO model (the break-even)

Daily fixed cost of a box:

`F = (price × (1 − resale)) / (life_days) + P_idle × 24h × p_kWh`

Marginal cost per local task:

`c_L = W_active × t_task × p_kWh` (+ wear, ~0 for fanless or low-duty Apple silicon)

Savings per routed task: `s = g × c_C − c_L`, where `g` is the local gate-pass rate (only verified local solves avoid the cloud call) and `c_C` is the cloud cost per task.

Break-even volume: `N* = F / s` tasks per day.

**Inputs:**
- Electricity: 18.31¢/kWh, the US residential average for July 2026 (EIA).
- Mac mini idle: 5 W, an assumption taken from Apple's published idle figure for the M4 generation in the prior research doc.
- Local task: about 80 W for 5 minutes, which gives c_L ≈ $0.0012.
- Box life and resale: 3-year life, 30–35% resale (assumption).
- Cloud cost per task:
  - Flash-class: ~$0.0075, from prior repo measurements.
  - Sonnet 5.5 at $2/$10 per million tokens: ~$0.13 for a task with 50k input and 3k output tokens, uncached.
  - Opus 5.5 at $4/$20: ~$0.26 for the same task (claude.com pricing).

**Break-even tasks/day at g = 0.5:**

| Box (price) | F $/day | vs Flash-class | vs Sonnet-class | vs Opus-class |
|---|---|---|---|---|
| Mac mini M5 Pro 64 GB (~$2.2k, assumed) | 1.33 | ~520 | ~21 | ~10 |
| Framework Desktop 128 GB ($1,999 launch; $3,449 reported 2026-09) | 2.23 | ~870 | ~35 | ~17 |
| DGX Spark 128 GB ($4,699, Feb 2026) | 3.03 | ~1,190 | ~47 | ~23 |

**Subscription comparison:**
- Claude Pro is $20/month (~$0.67/day). Max starts at $100/month (~$3.33/day).
- A Mac mini's amortisation falls between the two.
- A box replaces a subscription only if local quality is good enough for the owner's work. For frontier coding that is mostly not true today.
- A box *complements* a subscription by taking over routine volume.

**Sensitivity:**
- Break-even scales as 1/g. At g = 0.25 the volumes double.
- If the owner already owns the machine, F drops to the idle-power term: $0.02/day for a 5 W Mac. Local then wins on any task where g × c_C > c_L, which is almost always true.
- **Pricing decision:** sunk-hardware pricing is the right price for *routing*. Amortised pricing is the right price for the *purchase* decision.

### 2. Market landscape (2025–26)

**Apple (unified-memory desktops):**
- Mac mini M6, up to 32 GB at 153–170 GB/s.
- Mac mini M5 Pro, up to 64 GB at 307 GB/s.
- Mac Studio M5 Max, up to 128 GB at 460 GB/s.
- Mac Studio M5 Ultra, up to 512 GB at 1.2 TB/s, rated for 480 W max continuous.
- The Mac mini is rated for 155 W max continuous.
- Apple offers the best bandwidth per watt and native macOS for computer-use work.

**NVIDIA DGX Spark:**
- 128 GB with a 140 W GPU TDP and a 240 W PSU.
- Prompt reading runs at over 2,000 tok/s on a 20B model.
- Decode is about 50 tok/s on a 20B model, about 20 tok/s at batch 1 versus 370 tok/s at batch 32, and only 2–3 tok/s on 70B.
- It is a memory-capacity device, not a decode-speed device. It suits AWOS's batched background or practice workloads better than interactive single-stream work.
- gpuperhour concludes "rent first, then measure": $4,699 buys about 1,880 H100-hours at $2.50, or about 8,870 RTX 5090-hours at $0.53.
- DGX Station: not verified this session.

**AMD Strix Halo (Framework Desktop and similar mini-PCs):**
- 128 GB at 256 GB/s.
- The cheapest 128 GB tier at launch, but exposed to DRAM pricing.

**Microsoft Copilot+:**
- Requires a 40+ TOPS NPU.
- Runs small INT8 models through Windows ML and ONNX Runtime.
- Targets OS features such as translation and image work, not a substrate for large models.
- This is the volume channel for "AI PC" branding. The NPU is irrelevant to running a 30B coding model.

**Agent products on top of these boxes:** not charted here (search budget exhausted; see open questions).

### 3. Cloud price trends: is the gap widening?

**Evidence:**
- **Epoch AI (Mar 2025):** the price to reach a fixed benchmark threshold falls 9–900x per year. GPT-4-level GPQA fell 40x per year. Epoch notes the fastest drops are recent and may not persist.
- **a16z "LLMflation" (Nov 2024):** ~10x per year. MMLU-42 went from $60 to $0.06 per million tokens in three years.
- **Gundlach, Lynch, Mertens, Thompson, "The Price of Progress" (arXiv 2511.23455):**
  - 5–10x per year at fixed performance.
  - ~3x per year of that is pure algorithmic gain, after controlling for hardware and competition.
  - Running the *current* frontier model costs 3–18x more per year.
- **Epoch AI (Aug 2025):** a single consumer GPU (RTX 5090, under $2,500) runs models that match the frontier of 6–12 months earlier. The lag is 6.3 months on the Artificial Analysis index and 12.4 months on LM Arena.
- **OpenRouter State of AI (Dec 2025):**
  - Open models have reached about 1/3 of tokens.
  - Chinese open models reach up to ~30% in some weeks.
  - Programming is over 50% of tokens.
  - Demand is price-inelastic.

**Reading:** local is closing the *capability* gap, but at fixed capability its *dollar* position worsens every year.
- The cloud price of "what my box can run" falls about 10x per year, while the box is a fixed cost that DRAM inflation made bigger in 2026.
- The frontier tier stays expensive.
- So the durable economic role of local is **cheap absorption of routine volume that would otherwise escalate**, plus the factors that are not dollars: privacy, offline operation, always-on unattended work, and the latency of small calls.

### 4. Adoption evidence

**Ink & Switch, "Local-first software" (Kleppmann, Wiggins, van Hardenberg, McGranaghan, Onward! 2019):**
- Seven ideals: no spinners, multi-device access, optional network, collaboration, longevity, privacy by default, user ownership.
- Barriers they name:
  - CRDTs are fine for prototypes but were not yet advisable as a production replacement for Firebase.
  - CRDT history grows and causes performance problems.
  - Peer-to-peer and NAT traversal remain unsolved.
  - The cloud path is easier for developers.
  - Offline and version-state UX is hard to communicate.

**Self-hosting:** the selfh.st 2025 survey had 4,081 responses. That shows an engaged but small enthusiast base. The demographic detail could not be extracted this session.

**Why it stays niche:** combining the two sources with the OpenRouter elasticity finding, people buy capability and convenience, not lower marginal cost. Maintenance (updates, backups, networking) is the hidden TCO line that the table above sets to $0.

## What does not matter (much)

- **NPU TOPS figures.** They are irrelevant for LLM agents. Memory capacity and bandwidth set the tier.
- **Headline "x per year" price drops for frontier-class work.** The escalation tier is not getting cheaper per call.
- **Electricity for a low-power Mac host.** About $8/year idle and about $0.001 per task. It only matters for DGX- or GPU-class boxes at sustained load.
- **Pure dollar arguments for local** against Flash-class open APIs. Those comparisons are lost by two to three orders of magnitude.

## Key resources

- https://epoch.ai/data-insights/llm-inference-price-trends — fixed-capability price decline of 9–900x per year. The canonical cloud trend.
- https://epoch.ai/data-insights/consumer-gpu-model-gap — 6–12 month lag between consumer-GPU models and the frontier. The capability-gap metric to track.
- https://arxiv.org/abs/2511.23455 — Gundlach et al. Separates algorithmic progress (3x per year) and shows frontier cost per call rising.
- https://a16z.com/llmflation-llm-inference-cost/ — "LLMflation", ~10x per year.
- https://openrouter.ai/state-of-ai — open-model share, coding share, price inelasticity.
- https://gpuperhour.com/blog/dgx-spark-vs-cloud-gpu — Spark pricing history and rent-vs-buy hours.
- https://www.inkandswitch.com/essay/local-first/ — local-first ideals and barriers.
- https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a — US residential electricity price.
- https://www.apple.com/mac-mini/specs/ and https://www.apple.com/mac-studio/specs/ — memory, bandwidth and power envelopes.
- https://learn.microsoft.com/en-us/windows/ai/npu-devices/ — Copilot+ requirements and the NPU stack.
- https://claude.com/pricing — subscription and API anchors.

## Implications for AWOS

1. **Fix the router's cost term.**
   - Today `providers.py` prices `local/<name>` at $0 (`agent_loop.PRICES["local"]`). Replace that with `c_L = Wh_measured × p_kWh`, using the M0 wall-Wh telemetry. Keep it sunk-cost: no amortisation in routing.
   - The expected routing cost stays `E[c] = c_L + (1 − g_bucket) × c_C + τ × t`. Here τ is an owner-set $/second for latency, so slow local decode is not treated as free.
2. **Add a separate amortised line to the morning report.** Report `F / tasks_today` and the running `N*` per bucket. This answers "is the box paying for itself" without polluting routing decisions.
3. **Make purchase advice a computed output.** Recommend buying hardware only when the measured `g_bucket × c_C` across the owner's escalation-bound volume beats `F` for the candidate box. This formalises the prior doc's "no purchase until local wins a bucket".
4. **Point local at escalation avoidance, not Flash replacement.** The largest `s` per task comes from tasks that would otherwise reach Sonnet- or Opus-class models. Measure g on *that* bucket.
5. **Sell on privacy, unattended operation and compounding, not on dollars.** Owner-facing copy should not claim local is cheaper than open APIs.

## Open questions

- What resale and depreciation do real 2026 Mac minis and Strix Halo boxes show? The 30–35% used here is an assumption.
- What are measured idle and active Wh for the owner's machine? This replaces the 5 W and 80 W assumptions.
- DGX Station pricing, home-server vendors such as Umbrel and ZimaBoard, and agent products built on local boxes were not verified this session.
- What are the Artificial Analysis cost-per-index-task numbers? The interactive chart was not readable as text.
- How large is the maintenance-hours TCO for an owner, and can AWOS self-maintain to reduce it?

## Sources

Epoch AI, "LLM inference price trends" (2025-03-12); Epoch AI, "consumer GPU model gap" (2025-08-15); Gundlach et al., arXiv 2511.23455; Appenzeller, a16z "LLMflation" (2024-11-12); OpenRouter "State of AI" (2025-12); gpuperhour.com DGX Spark vs cloud (prices as of 2026); implicator.ai DGX Spark launch coverage; aimultiple/other DGX Spark reviews via search snippets (throughput figures); digitalcitizen.life and club386 (Framework Desktop pricing, secondary); Kleppmann et al., "Local-first software" (Onward! 2019); selfh.st 2025 survey page; EIA Electric Power Monthly Table 5.6.A; Apple Mac mini and Mac Studio spec pages; Microsoft Learn Copilot+ developer guide; claude.com/pricing. Prior repo work: docs/research/local_first_architecture_2026-10.md §4.

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

*Method note: arXiv's API returned HTTP 429 for every call in this pass, so paper discovery went through Hugging Face papers and Hacker News. Only items opened this session are listed.*

### New since the chart (dated, with URLs; most important first)

1. **DGX Spark 128 GB price rose again, to $6,950 (reported 2026-10-04).** ServeTheHome says the 64 GB model launches 2026-10-23 at $4,999. It quotes an editor paying "$2000 for 64GB ECC RDIMMs". The 128 GB unit launched at $3,999 about a year earlier. https://www.servethehome.com/nvidia-dgx-spark-64gb-launched-and-big-128gb-gb10-price-increases/ . *Why it matters:* this is the DRAM effect the chart predicted, only larger. Recomputed with the chart's inputs, F is about $4.4/day. Break-even at g = 0.5 is about 69 tasks/day against Sonnet-class and about 34 against Opus-class, up from 47 and 23 in the chart's table. Spark is the worst-value box for AWOS purchase advice.
2. **Open-weight lag against the closed frontier is about 4 months (Epoch, 2026-05-29).** Since January 2026 the best open-weight models trail closed ones by about 4 months, or about 8 ECI points. Top open models cited: Kimi K2.6, GLM-5.1 and DeepSeek-V3.2. https://epoch.ai/data-insights/open-closed-eci-gap . *Why it matters:* this is the cloud-hosted open-weight lag, not the consumer-GPU lag. It is a different metric from the chart's 6–12 months, and it is a sign that the capability gap keeps closing. It supports routing to cheap open APIs, which hurts the dollar case for a local box.
3. **Per completed task, the buyer's price has stopped falling (Zhu, 2026-08-30).** A quality-adjusted index fell 0.73 log points a year, but "Counted per completed task, moreover, the buyer's price stopped falling. Reasoning models raised token consumption faster than token prices fell." https://huggingface.co/papers/2608.29843 (id 2608.29843). *Why it matters:* it weakens the chart's "cloud prices fall 5–50x per year" framing for agent work. The price per token falls, but the price per task may not. This is the strongest new argument that escalation avoidance, rather than cheap-model replacement, is where local saves money.
4. **Listed price is an unreliable proxy for cost (2026-03-25).** In 21.8% of model-pair comparisons the cheaper-listed model costs more, by up to 28x. Thinking-token variation on the same query reaches 9.7x across repeated runs. https://huggingface.co/papers/2603.23971 (id 2603.23971). *Why it matters:* the chart's c_C values ($0.0075, $0.13, $0.26) are point estimates from a fixed token profile. The router should use measured per-bucket cost with variance, not a price-list formula.
5. **Subscription and API spreads are very large for coding agents (Quesma, 2026-08-11).** The post cites a SemiAnalysis test (June 2026) of Claude Max at $100 against about $8,000 of API-equivalent usage (about 40x). It also reports enterprise token-billing bills at least doubling, roughly 3x for most. It lists Fable 5 at $10/$50 per MTok. https://quesma.com/blog/claude-code-pricing-for-enterprise/ . *Why it matters:* this is a secondary source quoting others and I did not check the underlying test. If it holds, the chart's "Claude Pro $20 and Max $100 per month" comparison understates how much heavy agent users would pay on the API. That raises the value of local escalation avoidance for API-billed users, and lowers it for flat-rate subscribers.
6. **Anthropic's current price list adds tiers above the chart's anchors.** Fable 5.1 and Mythos 5.1 are $10/$50, and Haiku 5.5 is $0.10/$0.50 (up to 100k prompt tokens). https://platform.claude.com/docs/en/about-claude/pricing . *Why it matters:* the Haiku 5.5 price is about 20x below Sonnet 5.5. A "Flash-class" cloud tier at $0.10 input pushes the pure-dollar case for local lower still, and a $10/$50 frontier tier widens the escalation savings. The cost bucket structure is stretching in both directions.
7. **DRAM outlook is contested.** The Register (2026-06-02) expected more price hikes. Tom's Hardware (2026-09-23) reports the Acer CEO saying memory makers are hyping 2030 shortage fears and PC prices should decline by late 2027. https://www.theregister.com/storage/2026/06/02/expect-more-of-those-dram-price-hikes-as-memory-shortage-continues-to-bite/5250049 and https://www.tomshardware.com/pc-components/dram/acer-ceo-says-memory-makers-are-hyping-2030-shortage-fears-to-protect-margins-pc-prices-set-to-decline-by-late-2027-cheaper-chinese-capacity-coming-online-delivers-lower-memory-prices . *Why it matters:* the chart treats DRAM inflation as a trend. A defensible reading is that buying now is the worst time, which supports a "do not buy until g is measured" rule. The Acer claim is an interested party's opinion.
8. **Apple's Mac mini lineup is confirmed.** M6 comes with 16, 24 or 32 GB, and M5 Pro with 24 or 64 GB. https://www.apple.com/shop/buy-mac/mac-mini . A terminalbytes comparison (2026-09-19) puts the M6 mini at 170 GB/s max and says it underperforms for larger models. https://terminalbytes.com/mac-mini-alternatives-local-llm-2026/ . *Why it matters:* the cheap M6 mini caps at 32 GB, so the 64 GB M5 Pro is the only mini that fits the chart's 30B-class story. The Apple page fetch did not return prices.
9. **A Strix Halo software ecosystem is forming (HN, 2026).** Examples are DeepSeek V4 Flash at 32.7 tok/s (https://www.lucebox.com/blog/deepseek-v4-flash-0731, 2026-08-11) and an RDMA cluster setup guide (https://github.com/kyuz0/amd-strix-halo-vllm-toolboxes/blob/main/rdma_cluster/setup_guide.md, 2026-06-28). I only saw the search snippets. *Why it matters:* it is some evidence that the cheapest 128 GB tier is usable, but I did not verify the throughput claims.
10. **Cost-aware routing products are appearing.** Examples are Cost.dev (YC W21, https://cost.dev/, 2026-06-04) and Liquid Inference (https://liquidinference.ai/, 2026-10-09). *Why it matters:* a cost-aware cloud routing layer is being commoditised. AWOS's differentiator would be the local gate-pass term, not the routing itself. I did not open either product.

### Corrections

- Chart: "DGX Spark went from $3,999 to $4,699" and the table row "DGX Spark 128 GB ($4,699, Feb 2026)" -> ServeTheHome reports the 128 GB at $6,950 as of 2026-10-04 (https://www.servethehome.com/nvidia-dgx-spark-64gb-launched-and-big-128gb-gb10-price-increases/). The $4,699 figure is out of date. The break-even row for Spark should read about 69 (Sonnet-class) and 34 (Opus-class) tasks/day, with F about $4.4/day. This is my recomputation from the chart's formula and assumptions.
- Chart: "open models a single consumer GPU can run trail the frontier by only 6–12 months" is not contradicted, but a newer metric exists. Epoch's open-vs-closed ECI gap is about 4 months (2026-05-29), so "6–12 months" should be read as the consumer-GPU figure only.
- Chart: "Cloud prices for a fixed capability fall roughly 5–50x per year" is not contradicted, but Zhu (2608.29843) finds the per-completed-task price stopped falling. The chart's "gap is widening against local" conclusion holds per token and is weaker per task.

### Confirmed claims

- Sonnet 5.5 at $2/$10 per MTok and Opus 5.5 at $4/$20 per MTok match Anthropic's pricing page (https://platform.claude.com/docs/en/about-claude/pricing). The cloud-cost inputs behind the Sonnet and Opus columns therefore hold.
- Mac mini M5 Pro with 64 GB exists as a configuration (Apple store page above). The "~$2.2k" price is still unconfirmed.
- Mac mini M6 tops out at 32 GB, matching the chart's "up to 32 GB".
- Directionally, DRAM-driven price rises for local boxes are confirmed, and the Spark case is stronger than the chart stated.

### Still unverified

- Mac mini M5 Pro 64 GB price, and the Framework Desktop $3,449 report.
- Real resale and depreciation for 2026 boxes (30–35% assumed).
- Measured idle and active watts, and the maintenance-hours TCO.
- The SemiAnalysis 40x figure, which is quoted second-hand.
- DGX Station pricing, Umbrel and ZimaBoard, and agent products on local boxes.
- Artificial Analysis cost-per-task numbers, and whether the Gundlach et al. and Epoch price-trend figures were updated in 2026.
- A 2026 OpenRouter State of AI update was not found.

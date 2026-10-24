# Repair and Verify: research for AWOS "show once, replay, repair, verify"

Date: 2026-10-24. Scope: web, papers, product docs, repos, 2023 to Oct 2026.
Product loop under study: the user shows a web chore once. AWOS replays it with zero
model calls. When the site changes, a small local model **repairs** the broken step.
After every run AWOS **verifies** the result (for example "all 6 bills downloaded")
and reports failures loudly.

Evidence tags:
- **STRONG**: peer-reviewed or arXiv paper with numbers, or official docs describing shipped behaviour.
- **MEDIUM**: a vendor or repo describing its own product, or a single study I read only as an abstract.
- **WEAK**: blog or secondary write-up, numbers I could not trace to a primary source, or my own inference.

---

## 0. Short answer

1. Every working product uses the same shape: a **cached deterministic script**, then a **cheap
   deterministic re-find** (multiple locators plus similarity scoring), then an **LLM fallback**
   that rewrites the cache. Nobody serious relies on the LLM alone. (STRONG, from Stagehand, Skyvern,
   UiPath, Healenium and Similo)
2. Similarity re-finding without an LLM is already very good on "moved or renamed" changes. On
   real version pairs, HybridSimilo re-finds 98.8% of elements with broken locators. (STRONG)
3. LLM healers recover more than heuristics on hard cases, but **about 1 in 4 of their heals is a
   "false heal"**: the run passes, but on the wrong element. The worst case is an element that was
   **deleted**: the model confidently picks a neighbouring element. (MEDIUM/WEAK, but consistent across sources)
4. General LLM judges of web-agent success **top out near 70% precision** (AgentRewardBench). Rule
   checks reach 84% precision but only 56% recall. (STRONG) A 7B judge fine-tuned for one domain
   (WebJudge-7B) reaches 87% agreement in-domain but only 75.7% precision out of domain. (STRONG/MEDIUM)
5. So for AWOS: the **verifier must be deterministic expected-effects checks first**, with the
   model used only as a second opinion that can veto a pass, never grant one. The repair model
   needs an explicit **ABSTAIN / "element is gone"** output, trained on deletion cases.

---

## 1. How self-healing automation re-finds elements

### 1a. Products

| Tool | Mechanism (as documented) | Model use | Evidence |
|---|---|---|---|
| Healenium (Selenium, open source) | Saves the last working locator and DOM path as a baseline. On `NoSuchElement`, runs a longest-common-subsequence (LCS) comparison of the old path against the current DOM tree, ranks candidates by score, uses the top one, and reports it with a screenshot and a "confirm heal" button | None | MEDIUM (official docs) |
| Stagehand (Browserbase) | `act()` results are cached. The key is instruction + page content + options; model config is not part of the key. Cached replays run deterministically. If the selector fails, `selfHeal` re-infers with the LLM, retries once, and updates the cache | LLM only on cache miss or failure | STRONG (official docs v2 to v4) |
| Skyvern code caching | After a successful agent run, records the actions and **generates code**. Later runs execute the code with no LLM or screenshots. "AI fallback" is on by default: on failure the agent takes over and regenerates the code. Caching is per workflow block, and wait and code blocks always run live | LLM only on failure | STRONG (official docs) |
| browser-use `workflow-use` | Records a demo, then builds a deterministic workflow with auto-extracted variables. Falls back to the Browser Use agent if a step fails. The README says self-update is roadmap-only and that the LLM fallback is "currently really bad"; not for production | LLM fallback | MEDIUM (README) |
| UiPath Healing Agent | Combines "Unified Target" (several selectors plus fuzzy and anchor-based targeting) with a just-in-time engine that reads the live UI semantically. It can chain fixes: close an overlay, adjust a selector, add a smart delay. Without autonomy turned on it only suggests fixes; heals are logged in Orchestrator | LLM plus deterministic rules | MEDIUM (vendor docs and blog) |
| Testim, mabl, Katalon, Functionize, Reflect | "Smart locators": record many attributes per element (id, classes, text, xpath, position, parents) and pick the best-weighted match at run time. Katalon tries fallback locator types in order (xpath, attributes, css, image). mabl and Functionize add ML or GenAI ranking | Mostly ML ranking; newer releases add GenAI | WEAK (marketing pages, from memory; no published accuracy) |
| testRigor, Octomind | Tests are plain-English steps resolved at run time (testRigor), or an agent auto-fixes failing Playwright tests and proposes a diff (Octomind) | LLM every run, or on failure | WEAK |
| PayPay (in-house) | Sends debugging context to an LLM, which picks a replacement locator so the test can continue | LLM on failure | WEAK (eng blog, no rate) |

### 1b. Research results

| Work | Technique | Result | Evidence |
|---|---|---|---|
| Similo (Nass et al., 2022, arXiv 2208.00677) | Multi-attribute similarity: about 14 properties (tag, id, name, text, xpath, neighbour text, position, size...), weighted into one score | Beat Leotta's multi-locator (separate locators voted together) on real app version pairs | STRONG |
| VON Similo (2023, arXiv 2301.03863) | Also accepts **visually overlapping** nodes (a button, its span, its wrapper div) as valid targets | Raises robustness but adds false positives | STRONG |
| Nass et al., "Improving web element localization by using an LLM" (arXiv 2310.02046) | Similo shortlist, then the LLM picks from the top candidates | LLM re-ranking improved localization over Similo alone, at higher cost and latency | MEDIUM (abstract-level read) |
| Kluge & Stocco, replication and extension (arXiv 2505.16424) | Similo, VON Similo, HybridSimilo, plus LLM variants, on a benchmark 23x larger than the original | HybridSimilo re-finds **98.8%** of broken-locator elements. Improved Similo gains +5.6%. LLM variants reach about 91% on the strict exact-match metric of the original 809-pair set | STRONG (abstract; the table figure is partial) |
| AX-tree healing (arXiv 2603.20358) | A 10-tier locator ladder (`get_by_role` first, then testid, ARIA label, text, CSS fragment). Re-extracts only the broken selector | 31/31 on one demo site, re-find in under 1 s. The setup is small and favourable | WEAK for generality |
| LLM healer study (Supabase Studio, Grafana; reported by Analytics Insight) | LLM picks the replacement locator | **55 to 68%** recovered, vs text-similarity 20 to 34%, Healenium 23%. **About 26% of passing heals were wrong** | WEAK (primary paper not found) |
| SD Times "false-heal" benchmark | 136 controlled UI perturbations | Unsupervised healing resolved the wrong element about 1 in 4 times | WEAK |
| Multi-provider consensus blog | Several LLMs vote on the replacement | 52/52 correct on moved or renamed controls. **34/34 wrong on deleted controls**: all models agreed on a neighbour | WEAK, but it names the key failure mode |
| "Practical Limits of Autonomous Test Repair" (arXiv 2605.01471) | Multi-agent LLM repair loop, 636 executions | 70% converge, mean 3.4 iterations, only 10% right on the first try, 38% produce no artifact. Failure modes: **assertion weakening, test deletion** | STRONG on failure modes |

### 1c. Techniques ranked for AWOS

| Technique | Cost | Good for | Fails on |
|---|---|---|---|
| Multi-locator fingerprint (role, accessible name, testid, text, xpath, neighbours, bbox) | about 0 | Moved, renamed, restyled, or wrapped elements | Deletion; a near-duplicate sibling (row 3 vs row 4) |
| Weighted similarity score (Similo-style) plus a margin threshold | about 0 | Ranking candidates and knowing when it is unsure (small top-1 vs top-2 margin) | Large redesigns |
| AX-tree diff (old snapshot vs new) | low | Finding *what* changed, and spotting deletion when the subtree is gone | Canvas UIs, or shadow DOM without an AX tree |
| Visual match (template or embedding of the element crop) | medium | Icon buttons, pages with no ARIA | Theme or size changes |
| Small-LLM re-rank over a top-k shortlist | medium | Semantic renames ("Pay bill" became "Make a payment"), flows that moved behind a menu | **Confident wrong pick when the element is gone**; multi-step flow changes |

---

## 2. Verifiers and judges for agent outcomes

| Work | What it judges | Key numbers | Evidence |
|---|---|---|---|
| AgentRewardBench (arXiv 2504.08942) | 1,302 web trajectories, 5 benchmarks, 12 judges | Best LLM judge precision **69.8%** (GPT-4o, AX tree). Qwen2.5-VL about 64% precision with **86 to 90% recall** (lenient). Rule-based: **83.8% precision, 55.9% recall**. Screenshot-only beat screenshot + AX tree: "more information distracts" | STRONG |
| Online-Mind2Web / WebJudge (OSU) | Key-point extraction, then judging key screenshots, then the outcome | o4-mini WebJudge about 85.7% human agreement. **WebJudge-7B (Qwen2.5-VL) 87% in-domain**, but 75.7% precision on AgentRewardBench vs 82.0% for o4-mini | STRONG/MEDIUM (author-reported) |
| Agent-as-a-Judge (DevAI, ICML 2025, arXiv 2410.10934) | An agent that inspects the artifacts (files, outputs) against a requirement checklist | **90%** agreement with human consensus vs 70% for LLM-as-a-judge. About 97% cheaper than human experts | STRONG |
| GUI-Shepherd (arXiv 2509.23738) | Step-level PRM, 52k interactions | Used as an RL reward or an inference verifier: AndroidWorld 32.8% to 40.5% | STRONG |
| GUI-PRA (arXiv 2509.23263) | A process-reward *agent* that can zoom into screenshot regions | +14.5% average success | MEDIUM |
| VisCritic | Siamese ViT on before/after screenshots; predicts action success and error type | Before/after diff as the input | WEAK (summary only) |
| Agentic Reward Modeling (arXiv 2602.00575) | The verifier **acts** (navigates to check) instead of only reading | Beats LLM-as-a-judge on OSWorld-Verified and AndroidWorld | MEDIUM |
| UI-TARS (ByteDance, 2025) | Reflection tuning: pairs of error step and corrected step, then DPO | Self-correction learned from mistake/fix pairs | MEDIUM (from memory of the paper) |
| Benchmark audits (arXiv 2607.28367; "Blind Spots in AppWorld/WorkArena verifiers", NeurIPS 2026) | Are executable checkers right? | About 15% of FAIL verdicts were wrong (mostly checkers rejecting valid solutions). Checkers can also **accept** wrong states, e.g. a duplicated non-idempotent write passed | MEDIUM |

What the evidence says about inputs and determinism:
- **Small vs large judges.** A tuned 7B judge can match a frontier judge *in its own domain*, but it
  loses about 6 to 10 points of precision out of domain. Untuned small judges are **lenient**: high
  recall, low precision. That is the worst trait for a "verified" stamp. (STRONG)
- **Inputs.** Less is more. The final screenshot, or a targeted state readout, beats dumping
  everything (AgentRewardBench). Before/after diffs and **active checking** (go and look) beat
  passive trajectory reading (Agentic RM, Agent-as-a-Judge). (STRONG/MEDIUM)
- **Determinism.** Rule and state checks have the highest precision but miss valid alternative
  outcomes. For AWOS this trade-off is the right one: the user *showed* the expected outcome, so it
  can be written down as a spec. (STRONG for the trade-off; WEAK/inference for the AWOS fit)

---

## 3. Generating training data from a synthetic app generator (exact labels, no teacher)

Prior art: AutoWebWorld (arXiv 2602.14296) models sites as finite-state machines with code-checked
success, producing 11.6k verified trajectories from 29 sites at $0.04 each; more synthetic data kept
improving real Online-Mind2Web scores (MEDIUM). "Training Needs Trustworthy Worlds" (arXiv 2608.21898)
verifies and repairs generated sites, and rewards from **backend state**, not page text (MEDIUM).
GUI-Genesis and SoftwareGym attach executable assertions to generated apps (WEAK, summaries only).
The Similo benchmarks use real **version pairs** of the same site, which is the gold standard for
held-out testing (STRONG).

### 3a. Repair specialist data recipe

| Step | What the generator does | Label (exact, free) |
|---|---|---|
| 1 | Render app version A from a seed; record the chore's steps as element fingerprints | Stable internal `node_uid` per logical control |
| 2 | Apply a **mutation operator** to make version B | The generator knows where `node_uid` went, or that it is gone |
| 3 | Operators: rename the label (synonyms), change ARIA, strip ids, reorder, wrap in a div, move to a menu or tab, split one step into two, add a cookie modal or overlay, paginate a table, change the theme or CSS, i18n, near-duplicate sibling, **delete the control**, **move the feature to another page** | Target = new uid, or `ABSTAIN`, or `PRE_STEP: open menu X` |
| 4 | Build the input = recorded fingerprint + old AX snippet + new AX tree (top-k from the deterministic scorer) + optional crop | Classification over the k candidates plus ABSTAIN. A deterministic checker confirms that replaying with the pick reaches the same end state |
| 5 | Hold out by **operator family and app template**, not by random split | Measures generalization to unseen kinds of change |

Mix: at least 20% deletion or "feature moved" cases, so ABSTAIN is learned. Add hard negatives where
the top-1 similarity is wrong but plausible (a sibling row).

### 3b. Verifier data recipe

| Source | Positive | Negative (exact) |
|---|---|---|
| Replays on mutated apps | Backend state matches the spec (6 files, right hashes or amounts, right month) | Injected faults: 5 of 6 downloads, the wrong month, a duplicate file, a silently empty PDF, logged out mid-run, an error toast, a wrong account selected, a partial form submit |
| Repair-model mistakes | none | Every false heal from 3a is a free hard negative ("passed, but on the wrong element") |
| Inputs to train on | The spec, deterministic check results, the final screenshot, a before/after AX diff, the file manifest | Same inputs |

The label comes from the generator's backend, so no teacher model is needed. Train the verifier to
output `PASS / FAIL / UNSURE` with a reason code, and calibrate the threshold for **near-zero false PASS**.

---

## 4. Recommended AWOS design

### 4a. Split of work

| Stage | Deterministic code | Small local model |
|---|---|---|
| Record | Capture per step: action, multi-locator fingerprint, AX subtree, element crop, URL pattern, network or download events. **Derive the expected effects** (N files, name pattern, size > 0, PDF parses, dates in range, success toast text) | Optional: name the chore and its parameters |
| Replay | Execute the script; wait on network idle or element ready; zero model calls | none |
| Re-find (tier 1) | Similo-style weighted score over the AX tree. Accept only if score >= T **and** the top-1/top-2 margin >= M **and** role matches | none |
| Re-find (tier 2) | Generic overlay or modal dismissal rules; pagination rules | none |
| Repair (tier 3) | Give the model the top-k candidates, never free-form selectors. Validate the pick: role and actionability, then run the step, then the step's postcondition must hold | Choose a candidate, `ABSTAIN`, or `PRE_STEP(open menu/tab)`; one short reason |
| Cache update | Write the new locator only after the **whole run verifies**. Keep the old one as a fallback. Require user confirmation for the first heal on a high-stakes step (pay, submit) | none |
| Verify | Run the expected-effects checks: count, hashes, parse, dedupe vs previous runs, date range. **These alone decide PASS** | Second opinion over the final screenshot + spec. It may turn PASS into FLAG; it can **never** turn FAIL into PASS |
| Report | Loud failure: which step, which check, a screenshot, and a one-click "show me again" | Plain-English summary of the failure |

Hard rules from the failure-mode literature:
1. The model can never edit assertions or expected effects (arXiv 2605.01471: assertion weakening, test deletion).
2. Deletion has to be a first-class answer (the consensus 34/34 wrong-on-deleted result).
3. No heal is trusted until end-of-run verification passes (Stagehand and Skyvern only cache after success).

### 4b. What to measure

| Metric | Definition | Target (initial) |
|---|---|---|
| Repair success, held-out | % of broken steps fixed correctly on **unseen mutation families and unseen app templates**, plus a small set of real site version pairs (Wayback-style) | Beat the tier-1-only baseline by a clear margin; report per operator |
| **False-heal rate** | % of heals that executed but on the wrong element (known from generator uids) | <= 2% (literature LLM healers: about 25%) |
| Abstain quality | Recall of ABSTAIN on deletion or moved cases; and how often it abstains when a correct fix existed | Deletion recall >= 90% |
| **False "verified" rate** | % of runs stamped PASS whose ground truth is FAIL (injected faults) | about 0, e.g. <= 0.5%; the headline trust metric |
| False alarm rate | % of good runs flagged FAIL or FLAG | <= 5% (an annoyance, not a harm) |
| Model calls per run | Average LLM calls; should be about 0 on unchanged sites | 0 at steady state |
| Tier mix | Share of fixes made by tier 1, 2 and 3 | Most fixes from tier 1 |
| Cost, latency, watts per repair | Per AWOS objective | Track per change type |

Baselines to report against: (a) broken script with no heal, (b) Healenium-like LCS, (c) tier-1
Similo-style, (d) frontier-model repair (upper bound), (e) the small model untuned.

---

## Sources
- Healenium docs: https://healenium.io/docs/how_healenium_works
- Stagehand caching: https://docs.stagehand.dev/v4/best-practices/caching ; https://docs.stagehand.dev/v2/best-practices/caching
- Skyvern code caching: https://skyvern.com/docs/developers/features/code-caching
- workflow-use: https://github.com/browser-use/workflow-use
- UiPath Healing Agent: https://www.uipath.com/blog/product-and-updates/technical-tuesday-how-healing-agent-solves-ui-automation-challenges ; https://docs.uipath.com/agents/automation-suite/2.2510/user-guide-ha/what-is-healing-agent
- PayPay: https://blog.paypay.ne.jp/en/ai-driven-self-healing-for-automation-testing-at-paypay/
- Similo: https://arxiv.org/abs/2208.00677 ; VON Similo: https://arxiv.org/abs/2301.03863 ; LLM localization: https://arxiv.org/abs/2310.02046 ; extension study: https://arxiv.org/abs/2505.16424
- AX-tree healing: https://arxiv.org/abs/2603.20358 ; practical limits: https://arxiv.org/abs/2605.01471
- LLM healer study (secondary): https://www.analyticsinsight.net/artificial-intelligence/suneet-malhotra-why-self-healing-test-systems-still-need-human-judgment ; false heal: https://sdtimes.com/?p=61093 ; consensus: https://macmyths.com/llm-locator-healing-gets-moves-right-deletions-wrong/
- AgentRewardBench: https://arxiv.org/abs/2504.08942 ; Online-Mind2Web: https://github.com/OSU-NLP-Group/Online-Mind2Web ; WebJudge-7B: https://featherless.ai/models/osunlp/WebJudge-7B
- Agent-as-a-Judge: https://arxiv.org/abs/2410.10934 ; GUI-Shepherd: https://arxiv.org/abs/2509.23738 ; GUI-PRA: https://arxiv.org/abs/2509.23263 ; Agentic RM: https://arxiv.org/abs/2602.00575
- Benchmark audits: https://arxiv.org/abs/2607.28367 ; https://neurips.cc/virtual/2026/172904
- AutoWebWorld: https://arxiv.org/abs/2602.14296 ; Trustworthy Worlds: https://arxiv.org/abs/2608.21898 ; SoftwareGym: https://alphaxiv.org/abs/2610.12611v1

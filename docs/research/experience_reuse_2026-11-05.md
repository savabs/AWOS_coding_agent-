# Experience reuse for AWOS Replay: what to copy (research synthesis, 2026-11-05)

Four parallel sweeps (arXiv, GitHub, product docs): experience memory, record/replay + AI fallback tools, small-model
web agents, learning from failures/corrections. Numbers are as reported by the authors/vendors; items marked (2nd)
were seen only in secondary sources. Problem being solved: "Tell it" on a real site makes the local 4B model explore
from scratch ("gibberish" clicks such as "Customize" / "Chats and tasks" on claude.ai); past runs are logged but never
used at run time.

## The four findings that decide the design

1. **For a 4B model, verified concrete experience helps; abstract prose memory hurts.** On WebArena with Qwen3-4B:
   no memory 16.5%, AWM text workflows 15.7% (worse), ASI code skills 20.8%, CER 22.1%, SGDR per-step skill retrieval
   24.3% (arXiv 2606.04391). A budget-matched study found memory methods often no better than a plain actor, worst
   for the weakest model, and ~half of "success" memories came from failed runs (arXiv 2606.15017).
   -> Only write memory from runs that pass a real check (file downloaded + verifier/user); inject little.
2. **What the model sees matters as much as what it remembers.** AgentOccam: +26.6 pts on WebArena from pruning the
   observation/action space alone (2410.13825); Prune4Web: a 3B model's grounding 46.8% -> 88.3% after cutting the
   page to ~20 candidates (2511.21398). Our claude.ai failure is a distractor win: dozens of chat links outrank the
   profile menu.
3. **Demonstrations of the same site are the strongest no-training lever.** Synapse +56% rel. step success
   (2306.07863); LearnAct one demo: UI-TARS-7B 18.1% -> 32.8% (2504.13805); UFO2 retrieves top-k recorded demos +
   tips to guide planning; skills made by a strong agent lift weak ones 40-130% (SkillWeaver 2504.07079). Store
   elements by meaning (role, name, region), not ids: cross-site 39.7% vs 12.4% with ids (HMT 2603.07024).
4. **Memory must be curated or it poisons the agent.** Conditional guidelines retrieved by state, k=2, beat dumping
   all of them (AutoGuide 2403.08978: WebArena 8.0 -> 47.1; k=5 worse than k=2). Itemised ledger with helpful/harmful
   counters and pruning (ACE 2510.04618, ReMe 2512.10696: Qwen3-8B + memory beats Qwen3-14B without); verifier-gated
   addition + history-based deletion ~+10 pts over naive growth (2505.16067). Products (Devin, Cursor) make the
   user approve a learned memory.

Industry pattern (Skyvern, Stagehand, browser-use workflow-use, Power Automate, UiPath, Automation Anywhere):
deterministic cached steps first, a recovery cascade, then AI; a heal that passes its check is written back (often
with user confirmation); a per-step validator is the biggest single gain (Skyvern: actor 45% -> +planner 68.7% ->
+validator 85.9% on WebVoyager).

## Ranked plan (all local, no retraining until phase 4)

### Phase 1: stop the gibberish (S, ~1 week)
- **A. Goal-ranked, capped observation.** Score controls against goal / current subgoal / site-map words; always pin
  global navigation (avatar/profile/account/settings, menus, `aria-haspopup`, icon buttons with labels); show top
  30-40 grouped by landmark (Header / Sidebar / Main / Dialog); collapse repeated lists ("45 chat links, hidden");
  `expand` action for the rest. (AgentOccam, MindAct, Prune4Web)
- **B. Site map + same-site demos in the prompt.** From verified recordings on the current site build "known paths"
  (`claude.ai: profile menu -> Settings -> Billing tab -> invoice rows`) and show the 1-2 nearest recipes as
  `intent -> role "name"` lines, marked "the page may differ". Model may answer "follow step k" or diverge.
  (Synapse, UFO2, LearnAct, HMT descriptors)
- **C. Recipe fit first, model fills slots only.** Already in 0.9.5-0.9.7 (recipe match + month from words); extend
  to typed slots (month, account, free text) filled by a grammar-constrained JSON call. (Alloy, AWM placeholders)

### Phase 2: learn from every run (S-M)
- **D. Per-step intent + expected outcome.** Record per step `intent` and `post` (dialog opened, tab selected, URL,
  download started); a step counts only when `post` holds. Tell-it steps get the same check via AX-tree diff; on no
  progress: Escape/back, mark tried, take the next candidate. (workflow-use, Skyvern validator, Web-Shepherd)
- **E. Heal write-back with promotion.** A model-rescued step that passes `post` and whose run is verified is stored
  as an alternate locator `{by:'4b', ok:1}`; promoted after 2 verified uses, demoted after 2 failures, never
  overwrites the recording; "fixed step 3 (Billing moved)" in the log, one-click revert. Next run: no model call.
  (Skyvern, Stagehand threshold, Power Automate/AA human confirm)
- **F. Corrections -> site rules.** A typed correction becomes one conditional rule
  `{host, when, do, avoid, src:user, hits, wins, losses}` after the user clicks "Remember for <site>"; retrieved by
  host + state, at most 2 in the prompt, "Avoid:" phrasing; auto-disabled after 2 losses. (AutoGuide, Devin/Cursor)
- **G. Ledger + pruning** under D-F: JSONL items with provenance (user / verified / model), counters updated from
  the verifier + file check, retire when wins/hits < 0.4 after 3 hits, cap ~20 per site, "Lessons" page.

### Phase 3: generalise to new sites (M)
- **H. Plan then act.** One call: goal + site map + demos -> 2-5 subgoals (grammar list); executor works one subgoal;
  replan only on 2 steps without progress. (Plan-and-Act)
- **I. Sub-skills as macro actions.** Split verified recipes at `post` boundaries into skills
  (`open_billing`, `pick_row_by_date(month)`, `download_pdf`) offered next to raw clicks; keep a skill only after it
  replays alone; generic chains ("account menu -> settings -> billing -> invoices") transfer to other portals.
  (ASI, SGDR, AppAgentX)
- **J. Confidence from logprobs.** Read llama.cpp `n_probs` on the click index: low top-1/top-2 margin triggers memory
  lookup or asks the user "A or B?". (Soft self-consistency, ExpSeek)

### Phase 4: train (L)
- **K.** LoRA SFT of Qwen3.5-4B on verified trajectories in our exact observation format (Kaggle GPU), then DPO pairs
  from corrections / verified-vs-failed steps; gated on a frozen eval set. (WebRL 4.8 -> 42.4 for 8B; WebAgent-R1)
- **Check first:** Fara1.5-4B, reportedly fine-tuned from Qwen3.5-4B, 57.3% Online-Mind2Web, MIT, screenshot-only
  (single secondary source; verify before relying on it). A screenshot-mode A/B against our harness is cheap if real.

## Not now
MCTS/LATS, multi-sample voting, simulated world models (each multiplies 5-9 s calls); open-ended site exploration on
logged-in accounts (unsafe); prose workflow memory (hurt a 4B model).

## How we measure
A fixed eval set (~30 Tell-it goals over 5-8 sites incl. claude.ai billing), budget-matched no-memory baseline,
success + model calls per task, and per site: does run N+1 beat run N. Every run already saves its log (0.9.6/0.9.7).

## Sources
Memory: AWM 2409.07429 · Synapse 2306.07863 · ExpeL 2308.10144 · AutoManual 2405.16247 · ICAL 2406.14596 ·
SkillWeaver 2504.07079 · ASI 2504.06821 · WALT 2510.01524 · Agent KB 2507.06229 · CER 2506.06698 · ReasoningBank
2509.25140 · Mem^p 2508.06433 · ExpRAG 2603.18272 · HMT 2603.07024 · SGDR 2606.04391 · budget study 2606.15017 ·
AgentRR 2505.17716.
Feedback: Reflexion 2303.11366 · AutoGuide 2403.08978 · Agent-Pro 2402.17574 · CLIN 2310.10134 · ReMe 2512.10696 ·
ACE 2510.04618 · Dynamic Cheatsheet 2504.07952 · WebCoach 2511.12997 · ExpSeek 2601.08605 · Memento 2508.16153 ·
experience-following 2505.16067 · memory poisoning 2512.16962 · Devin knowledge docs · Cursor memories docs.
Small models: AgentOccam 2410.13825 · MindAct 2306.06070 · Prune4Web 2511.21398 · Plan-and-Act 2503.09572 ·
Soft-SC 2402.13212 · Web-Shepherd 2505.15277 · WebDreamer 2411.06559 · Agent Q 2408.07199 · WebRL 2411.02337 ·
WebAgent-R1 2505.16421 · Explorer 2502.11357 · Online-Mind2Web 2504.01382 · Fara (github.com/microsoft/fara).
Tools: browser-use workflow-use (github.com/browser-use/workflow-use) · Stagehand caching docs · Skyvern code caching
docs + WebVoyager blog · Power Automate self-healing (learn.microsoft.com) · UiPath Healing Agent docs · Automation
Anywhere Generative Recorder · UFO2 learning-from-demonstration docs · Rabbit teach mode · Adept AWL · Hyperbrowser
action cache · Anchor skill caching · LearnAct 2504.13805 · Alloy 2510.10049 · ECLAIR 2405.03710 · WONDERBREAD
2406.13264 · AppAgentX 2503.02268 · Overline (github.com/reybahl/overline).

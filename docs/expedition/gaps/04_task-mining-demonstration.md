# Gap 04: Task mining and learning from watching the owner (the RPA lineage)

*Explorer chart, 2026-10-10.*

*Method note: the session's shared web-search budget ran out after four searches. Everything else was read directly from primary pages whose URLs were already known: arXiv abstracts and HTML, SEC filings, Microsoft Learn, GitHub and Wikipedia. Industry-analyst figures that could not be traced to a primary page (Gartner, Forrester) are deliberately left out. Two PDFs (Leno et al. 2020, Li 2019 thesis summary) could not be text-extracted, so they are cited at abstract level only.*

## Summary

Before AWOS can "get better at the owner's work", it has to find out what that work is. Twenty years of RPA, process mining and programming-by-demonstration (PBD) give a clear answer on how to do this, and a clear warning about what goes wrong.

**The answer is a pipeline:**

1. Record UI events.
2. Filter them.
3. Segment them into task instances.
4. Mine the repeated routines.
5. Generalise their parameters.
6. Compile them into something executable.

Leno, Dumas et al. named exactly these stages "robotic process mining".

**The warning:** RPA proved that recording and replaying is the easy part. Choosing the right routines and keeping them alive is the hard part.

- EY reports that 30–50% of initial RPA projects fail.
- Stanford's ECLAIR paper describes classic RPA as taking 12–18 months to set up and reaching only about 60% initial accuracy, with heavy maintenance after that.

**What has changed since RPA's peak:**

- Foundation models can now describe a workflow from a recording. WONDERBREAD found 88% step recall.
- They still cannot reliably judge whether a workflow *completed correctly* (F1 < 0.3).

That split fits the Gatekeeper design: use models to *propose and document* routines, and use state probes to *verify* them.

**What the owner wants automated:** worker-preference data (Stanford WORKBank: 1,500 workers, 844 tasks, 104 occupations) points to scheduling, record-keeping and data transfer. It points away from creative and interpersonal work.

## What matters

**1. RPA's commercial history is a story about discovery and maintenance, not execution.**

- **UiPath** (FY2025 10-K):
  - $1.429B revenue and $1.666B ARR.
  - About 10,750 customers, of which 2,292 have ARR ≥ $100K.
  - 110% net retention.
  - The market is real but concentrated in large enterprises. Each deployment is a consulting-heavy project, not a self-serve tool.
- **Celonis** grew from a 2011 TU Munich spin-off to an ~$11–13B valuation on *process mining*. Process mining analyses system event logs (case ID, activity, timestamp) to find where to automate.
- The money followed **discovery**, not bots. WONDERBREAD cites that documenting the workflow takes about 60% of a typical process-optimisation project.
- **Microsoft Power Automate's task mining** shows the enterprise default for desktop capture:
  - The employee explicitly starts a recorder, can pause, delete or reset steps, and is told to remove sensitive information before analysis.
  - Raw actions are "very detailed and specific". They must be grouped into named activities, now auto-grouped, before any process map makes sense.
  - That grouping step is the segmentation problem in product form.
- **Why RPA ROI disappointed:**
  - Bots were scripted against brittle selectors.
  - Routines were chosen top-down by consultants, not by measuring what workers actually repeat.
  - Every UI change in a target app became a maintenance ticket.
  - EY's 30–50% initial failure figure is the one widely repeated number with a primary attribution.
  - Infosys and UiPath both publish "bot sustainability" and "why deployments fail" material. Vendors themselves treat upkeep as the main cost.

**2. Academic process mining gives the vocabulary. UI-log segmentation is the hard sub-problem.**

- Van der Aalst's field (Eindhoven, from about 1999) has three operations:
  - **discovery**: log → model, using the alpha, heuristic and inductive miners;
  - **conformance**: log vs model;
  - **enhancement**: adding performance data to a model.
- **Conformance checking is directly reusable as a drift detector.** A recorded routine is a model. Each new owner execution either conforms or it doesn't.
- Classical process mining assumes events already carry a case ID. Desktop UI logs do not. They are, in Leno et al.'s words (ICPM 2020), "a single unsegmented sequence of events", with noise inside and between routine instances.
- Their approach discovers candidate routines from such logs and is open source.
- Their *Robotic Process Mining: Vision and Challenges* (BISE 2021) gives the seven-stage pipeline:
  1. recording
  2. filtering
  3. segmentation
  4. simplification
  5. routine identification
  6. executable routine discovery
  7. compilation
- The pipeline is a usable checklist. Stages 3 and 6 are where systems fail.

**3. PBD solved one-shot generalisation for GUIs years ago, but only with the user in the loop.**

- **CoScripter** (IBM Almaden, formerly Koala) recorded Firefox actions as semi-natural-language steps ("click the Search button") and shared them on a wiki.
  - Its lesson: human-readable scripts make sharing and repair possible.
  - Its fate (discontinued) says that recorded macros alone don't sustain usage.
- **SUGILITE** (Li, Azaria, Myers, CHI 2017) used Android's accessibility tree plus a spoken command to generalise a *single* demonstration into a parameterised script. It also forked the script when it met new situations.
- Its follow-ups added:
  - data descriptions in natural language (APPINITE);
  - conditionals and concepts (PUMICE);
  - privacy-preserving script sharing.
- Two lessons carry over:
  - **Generalisation comes from combining the UI tree with a sentence of intent.** The click stream alone is not enough.
  - **The accessibility tree, not pixels, is the robust anchor.**

**4. LLM-era work turns observation into skills or training data.**

- **Agent Workflow Memory** (Wang et al. 2024) induces reusable workflows from past trajectories: +24.6% relative on Mind2Web and +51.1% on WebArena. Gap 03 covers compiling these into code.
- **ECLAIR** (Wornow et al. 2024) learns enterprise workflows from demonstrations and documentation:
  - 93% accuracy on workflow understanding;
  - only 40% end-to-end completion from natural-language descriptions alone.
- **WONDERBREAD** (2,928 documented demonstrations, 6 BPM tasks) is the key calibration point:
  - models write SOPs well (88% step recall);
  - they validate completion badly (F1 < 0.3).
- **Synthetic demonstrations for training:**
  - **Synatra** converts human tutorials into 100K demonstrations at $0.031 each (3% of the cost of human demos). A 7B model trained on them beat GPT-3.5 on WebArena and Mind2Web.
  - **OS-Genesis** reverses collection: explore first, then derive tasks afterwards, with a trajectory reward model as a filter.
- The implication: the owner's own verified routines are high-value, private fine-tuning data for the local model. The same data can also be expanded by "reverse task synthesis" on the owner's own apps.

**5. Workers want the boring middle automated, and investment goes elsewhere.**

- WORKBank (Shao et al., Stanford SALT 2025) audited 844 tasks:
  - 46.1% got positive automation desire;
  - the top reason was freeing time for high-value work (69.4%).
- Its Human Agency Scale (H1 = AI alone, H5 = human essential) found H3, an equal partnership, to be the dominant desired level in 45.2% of occupations.
- **Green-light examples:**
  - "schedule appointments with clients" (desire 5.00);
  - "maintain files of information" (4.67);
  - data entry and record-keeping generally.
- **Low-desire examples:** writing editorial text (1.60) and creative and interpersonal tasks. Arts/design/media was positive on only 17.1% of tasks.
- 41% of Y Combinator company–task mappings fell in the low-priority or red-light zones. The market builds for coding and analysis, not for the green-light zone.
- The main worker concern was lack of trust in accuracy (45%). That points AWOS at verification, not at more autonomy.

**6. Passive capture is technically cheap. Trust is the bottleneck.**

- **Microsoft Recall** had to be re-architected after launch criticism. The new design is opt-in, processes data inside a VBS enclave, uses TPM-held keys gated by Windows Hello, filters sensitive content by default, and lets the user exclude apps and sites.
- **screenpipe** (open source) shows the cheap local version:
  - event-driven capture (app switch, click, typing pause) of the accessibility tree plus a screenshot, with OCR only as a fallback;
  - local SQLite with full-text search;
  - stated cost of 5–20% CPU and 0.5–3 GB RAM.
- **AX-tree-first, event-driven capture is the right default.** Continuous video is not.

## What does not matter (or matters less than it seems)

- **Full process models (BPMN, Petri nets) for one owner.** These are tools for coordinating organisations. A single person's work needs frequent, stable *routines*, not end-to-end process maps.
- **Top-down routine selection.** This is the RPA consultancy model, and it is the main cause of the ROI gap. Measure what the owner actually repeats.
- **Pixel-level recorders and record-and-replay without parameters.** CoScripter-style macros break on the first UI change and die quietly.
- **Model-judged success.** WONDERBREAD's F1 < 0.3 on completion validation rules out using an LLM as the verifier of routines learned from observation.
- **Always-on screenshot video.** It costs more storage, more privacy risk and more watts than AX-tree events, and adds little signal for routine discovery.

## Key resources

- https://arxiv.org/abs/2008.05782: Leno et al., candidate routines from *unsegmented* noisy UI logs (open-source tool). The closest prior art for AWOS segmentation.
- https://dl.gi.de/items/4d797e01-42a4-401b-a7af-50d155462657/full: the *Robotic Process Mining* vision paper. Gives the seven-stage pipeline.
- https://arxiv.org/abs/2406.13264: WONDERBREAD. Shows that documentation is easy and validation is hard, with numbers.
- https://arxiv.org/abs/2405.03710: ECLAIR. Compares foundation-model workflow automation with classic RPA's setup time and accuracy.
- https://arxiv.org/html/2506.06576v2 and https://futureofwork.saltlab.stanford.edu: WORKBank and the Human Agency Scale. Shows what workers want automated.
- https://github.com/tobyli/Sugilite_development: SUGILITE and its follow-ups. One-shot PBD on accessibility trees.
- https://learn.microsoft.com/en-us/power-automate/process-advisor-processes: Power Automate task mining. The enterprise UX for recording and grouping into activities.
- https://github.com/mediar-ai/screenpipe: local event-driven AX/OCR capture, with resource figures.
- https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/: the Recall privacy architecture. A template for capture that the owner can trust.
- https://arxiv.org/abs/2409.15637 (Synatra) and https://arxiv.org/abs/2412.19723 (OS-Genesis): cheap synthetic GUI demonstrations for training local models.
- https://www.sec.gov/Archives/edgar/data/1734722/000173472225000007/path-20250131.htm: UiPath FY2025 10-K, for real deployment scale.

## Implications for AWOS

**Proposed local pipeline (observation → candidate → verified routine):**

1. **Capture (opt-in, on-device).**
   - Record AX-tree events, app/window/URL and the focused element's role and label on meaningful events, screenpipe-style. No continuous video.
   - Exclusion lists cover password managers, banking and private browsing by default. A visible pause control is always present.
   - Storage is encrypted with an OS-keychain key and kept for 14–30 days by default.
2. **Redact at ingest.**
   - Replace typed values with typed tokens (`<email>`, `<amount>`, `<date>`, `<filename>`) and keep only a salted hash for matching.
   - Routines need structure and types, not content.
3. **Segment.**
   - Cut on idle gaps and context switches.
   - Then run Leno-style repeated-pattern discovery over the abstracted event stream.
   - A local small model labels each segment with a one-line intent. This is the documentation step that WONDERBREAD shows models do well.
4. **Rank candidates** by `frequency × duration × stability × verifiability`. Stability is variance in step structure across instances. Verifiability asks whether a state probe exists: file present, calendar entry, sheet cell, sent-folder item.
5. **Ask once.** Example: "You did *receipt → rename → file into Expenses/2026-10* 14 times this month, about 3 min each. Automate?"
   - The owner confirms and adds a sentence of intent, which is SUGILITE's generalisation signal.
   - The owner states the postcondition.
6. **Compile** into a parameterised routine with preconditions and postconditions (see gap 03). Anchor on AX roles and labels, not coordinates.
7. **Shadow mode.**
   - While the owner does the task, the routine predicts each next step.
   - Agreement is logged as conformance checking (process mining's own tool).
   - Promote to supervised runs only after N conforming instances.
8. **Verified routine.**
   - Every run must pass state probes.
   - A conformance failure or probe failure demotes the routine to the local-model → cloud ladder, which is the Gatekeeper's existing escalation.
   - Verified traces become private fine-tuning data. Synatra and OS-Genesis show that the volume can be expanded synthetically.

**Ranked owner task families, for computer use beyond coding** (ordered by green-light desire, frequency, reversibility and how easy the outcome is to verify):

1. **File and document hygiene.** Triage downloads, rename, file receipts and invoices. Local, reversible, verifiable from the filesystem.
2. **Swivel-chair data transfer.** Email or PDF → spreadsheet or form fields. The classic RPA win. Verify by reading the values back.
3. **Scheduling and calendar coordination.** WORKBank's top green-light example. Verify through calendar state. Sends are gated.
4. **Recurring report assembly.** Pull numbers from several sources into a weekly doc or sheet. Verify by diffing against sources.
5. **Record-keeping and logging.** Time logs, CRM or contact updates, notes filing.
6. **Inbox triage and draft replies.** Drafts only. The owner sends. Interpersonal writing is a low-desire zone.
7. **Web-form submissions and admin chores.** Expenses, renewals, bill pay. These are irreversible, so they stay last and always require confirmation.

Avoid creative and interpersonal tasks. They sit in WORKBank's low-desire zones.

## Open questions

- What segmentation accuracy does a Leno-style miner reach on a *single owner's* macOS AX stream with heavy interleaving? There is no published personal-desktop benchmark. AWOS would need to build one from its own capture.
- How many shadow-mode conforming instances are enough before promotion? Is 3 enough, or 10? This needs a small pre-registered study, in line with AWOS's evidence discipline.
- Does the redaction (typed tokens) lose the signals needed for preconditions? Example: "only for invoices from vendor X".
- How much do owners actually repeat? The AWOS ledger found about 0% genuine recurrence in coding work. Desktop admin work may differ, but this is unmeasured.
- Can a 3–8B local model do the segment-labelling step at WONDERBREAD-like recall within the watt budget of an always-on host?

## Sources

All URLs listed under Key resources, plus:
- EY, "Get ready for robots" (30–50% initial RPA project failure): https://www.eyfinancialservicesthoughtgallery.ie/wp-content/uploads/2016/11/ey-get-ready-for-robots.pdf, and as quoted by https://www.raconteur.net/technology/rpa-failures
- Infosys BPM, bot support and sustainability: https://www.infosysbpm.com/offerings/functions/robotics-process-automation/insights/documents/bot-support-and-sustainability.pdf
- UiPath, why RPA deployments fail: https://www.uipath.com/blog/rpa/why-rpa-deployments-fail
- Agent Workflow Memory: https://arxiv.org/abs/2409.07429
- Process mining overview (van der Aalst, alpha/heuristic/inductive miners): https://en.wikipedia.org/wiki/Process_mining
- Celonis history and valuation: https://en.wikipedia.org/wiki/Celonis
- CoScripter: https://en.wikipedia.org/wiki/CoScripter

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

*Method note: arXiv's own API returned HTTP 429 for the whole pass (shared IP), so discovery ran through the Hugging Face papers search and HN/GitHub APIs. Abstracts were confirmed with WebFetch on arxiv.org/abs pages where noted. Items marked "HF abstract only" were read from the Hugging Face papers API, not the arXiv page. HF search is semantic, so this is a sample, not an exhaustive sweep.*

### New since the chart

1. **Task Model Induction (TMI)**, Jiang, Wang, Chen, Yang, 2026-08-20, https://arxiv.org/abs/2608.20319 (confirmed on abs page). It takes passively recorded screenshots and mouse/keyboard traces, discovers the latent tasks in an unsegmented, interleaved trace, and induces a task model (hierarchical goals plus control flow) for each. Reported: 0.974 agreement with ground-truth task groupings, 74.9% of execution steps reconstructed, and +30.0% held-out accuracy for skills derived from the models, against the strongest baseline. Why it matters: this is the segmentation sub-problem the chart's Open Questions say has no published benchmark, and it targets interleaved work. The evidence is on controlled human and agent trajectories, not a real owner's desktop. The abs page does not say whether code or data are released.
2. **Screenpipe went from capture tool to workflow-mining product.** The repo (https://github.com/mediar-ai/screenpipe, about 21.9k stars, pushed 2026-10-10, app-v2.7.104 released 2026-10-09) is now described as "YC (S26) | Open Computer History | Continuously record your company computer work, map your workflows, help you find work worth automating". The Launch HN is dated 2026-07-23 (88 points): https://news.ycombinator.com/item?id=49024620. Why it matters: the chart treats screenpipe as a capture substrate with 5–20% CPU. It is now a direct competitor for "find work worth automating", which strengthens the premise and narrows the room for novelty.
3. **GUIDE**, 2026-03-26, https://arxiv.org/abs/2603.25864 (HF abstract only): 67.5 hours of screen recordings from 120 users across 10 applications, with think-aloud narration. Eight multimodal models reach only 44.6% on behavior-state detection and 55.0% on help prediction. Adding user context raised help prediction by up to 50.2 points. Why it matters: it is the first benchmark close to "understand what the owner is doing". It supports the chart's rule that intent must come from a user sentence, and it gives a measuring stick for the segment-labelling step.
4. **PIRA-Bench**, 2026-03-09, https://arxiv.org/abs/2603.08013 (HF abstract only): proactive intent recommendation from continuous screen input with interleaved intents and noisy segments. Why it matters: it frames the chart's "Ask once" step as a benchmarkable task.
5. **Demo2Tutorial**, 2026-06-02, https://arxiv.org/abs/2606.03951 (HF abstract only): screen recordings and interaction logs become hierarchical task graphs and multimodal tutorials, and the paper reports improved GUI-agent planning. Why it matters: it is a second recent instance of the "model documents, human or probe verifies" split.
6. **Record-and-replay skills for coding agents**, https://github.com/ugarchance/record-and-replay-skill (42 stars, pushed 2026-08-08): watches a demonstration (Playwright for browser, OpenAdapt for desktop) and emits a reusable agent skill. Why it matters: the demonstration-to-skill path is becoming a community pattern, but at tiny adoption.
7. **MacAgentBench**, 2026-06-21, https://arxiv.org/abs/2606.22557 (HF abstract only): 676 macOS tasks across 25 apps with deterministic rule-based checks. Best result is Claude Opus 4.6 on OpenClaw at 73.7% Pass@1, with the gain attributed mainly to the skill library. Why it matters: AWOS's owner machine is macOS, the checks are state-based (like the chart's probes), and it shows skill libraries beat framework design on this platform.
8. **Workflow-GYM**, 2026-06-09, https://arxiv.org/abs/2606.11042 (HF abstract only): the strongest models reach only slightly above 30% on long-horizon professional GUI workflows. Why it matters: it warns that routines which chain many steps are far from reliable, so shadow mode and probes matter.
9. **Personal GUI assistants with self-evolving memory and skill**: KnowAct-GUIClaw, 2026-07-15, https://arxiv.org/abs/2607.12625 (HF abstract only); COLLEAGUE.SKILL, 2026-05-29, https://arxiv.org/abs/2605.31264 (HF abstract only). Both distil user traces into reusable skills. The first builds on OpenClaw. Why it matters: the "learn from the owner" idea is now mainstream, and any AWOS claim has to rest on its verification and conformance gates, not on the idea.
10. **Automated Event Log Generation from Unstructured Text Using Finetuned LLMs**, 2026-09-01, https://arxiv.org/abs/2609.01320 (HF abstract only). Fine-tuning beats few-shot prompting for turning text into process-mining event logs. Why it matters: it is weak evidence that a small fine-tuned local model can handle the redaction/event-abstraction step.

### Corrections

none found. Every numeric claim checked below matched its source.

### Confirmed claims

All five checked against arxiv.org/abs pages fetched today:
- WONDERBREAD (https://arxiv.org/abs/2406.13264): 88% step recall, F1 < 0.3 on validation, 2,928 demonstrations, 6 BPM tasks.
- Synatra (https://arxiv.org/abs/2409.15637): $0.031 per demonstration, 3% of human cost, 100k demonstrations, beats GPT-3.5 on WebArena and Mind2Web.
- ECLAIR (https://arxiv.org/abs/2405.03710): RPA setup 12–18 months, 60% initial accuracy; ECLAIR 93% on workflow understanding, 40% end-to-end.
- WORKBank (https://arxiv.org/abs/2506.06576): 1,500 workers, 844 tasks, 104 occupations. The percentages (46.1%, 45.2% H3, 69.4%) are not in the abstract, so see Still unverified.
- The chart's claim that no personal-desktop segmentation benchmark exists is now only partly true: TMI, GUIDE and PIRA-Bench exist, though none is a real single-owner macOS AX stream.

### Still unverified

- WORKBank percentages (46.1%, 69.4%, 45.2%, 17.1%, 41% YC mappings, desire scores) were not re-read in the full paper.
- UiPath FY2025 10-K figures, EY's 30–50% failure rate, Celonis valuation, and screenpipe's 5–20% CPU / 0.5–3 GB RAM figure were not re-checked this pass. The screenpipe resource claim may be stale given the repo's product pivot and version 2.7.x.
- Microsoft Recall and Power Automate task-mining details were not re-checked. Any change since September 2024 is unknown.
- Leno et al. 2020 and the BISE 2021 pipeline were not re-read.
- TMI's code, data and real-desktop performance are unknown. The new items marked "HF abstract only" are summaries, not full-paper reads.
- The chart's open questions on promotion thresholds, redaction loss and the recurrence rate of desktop work remain unanswered by anything found.

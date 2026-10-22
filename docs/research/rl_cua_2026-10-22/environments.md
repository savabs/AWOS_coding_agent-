# Environments for training an accessibility-tree computer-use model (research, 2026-10-22)

Licences were checked from repo LICENSE files via the GitHub API. **NC** = non-commercial only.

## Sources

| Source | Tasks | Text observations? | Infra / speed | Licence | Use |
|---|---|---|---|---|---|
| **BrowserGym** harness | Wraps MiniWoB, WebArena(-Verified), VWA, WorkArena, AssistantBench, WebLINX, OpenApps… | AXTree with numbered ids | Playwright + Chromium | Apache-2.0 | **Reuse as the harness** |
| **MiniWoB++** | ~125 widget tasks, random seeds | Yes | Static HTML, ~1 s/episode, CPU | MIT | **Train** (hold out 20% of task types) |
| **WebShop** | 12k instructions | Text mode | Python + small server | MIT | **Train** (hold out a slice) |
| WebArena | 812 tasks | Yes | Heavy Docker | Apache-2.0 | Evaluation only |
| **WebArena-Verified** | 812 tasks, 258 "hard" | Yes | Deterministic, offline replay, images up to 92% smaller | Apache-2.0 | **Evaluation only** (preferred) |
| VisualWebArena | 910 tasks | Screenshot-centric | Heavy | MIT | Evaluation, low priority |
| WorkArena | 33 templates (~20k instances) + L2/L3 | Yes | Needs a ServiceNow account and its ToS | Apache code | Skip for now |
| AssistantBench | 214 open-web questions | — | Live web | **OpenRAIL-S** | Skip |
| WebLINX | ~2.3k demos | DOM | Dataset | **CC BY-NC-SA (NC)** | Don't train on it |
| Mind2Web | 2k+ tasks, 137 sites, offline | Cleaned DOM | Dataset | CC-BY "solely for research" (**ambiguous**) | Train split only after legal review |
| **OSWorld / -Verified** (2.0 released 2026-06-26) | 369 tasks | a11y-tree mode | Ubuntu VM + KVM, slow | Apache-2.0 | **Evaluation only** |
| WindowsAgentArena | 154 tasks | UIA tree | Windows VM | MIT (ISO terms apply) | Evaluation only |
| **AndroidWorld** | 116 templates | a11y text | Emulator + KVM | Apache-2.0 | Evaluation |
| AndroidLab | 138 tasks | XML | Emulator | MIT | Evaluation |
| macOSWorld | 202 tasks | Screenshot | macOS VM | **NC** | Internal evaluation only |
| **MacArena** | 421 tasks | Screenshot + a11y (macapptree) | Apple Silicon VM | MacPaw code/tasks MIT; OSWorld subset Apache; **macOSWorld subset NC** | Our macOS evaluation (drop the NC subset from anything commercial) |
| MiMo RL-oss | 7,684 tasks | **No GUI tasks** | Docker | Apache-2.0 | Infrastructure reference only (answers leaked in git history) |
| **AgentGym / AgentGym-RL** | Text environments + recipe | Text | HTTP env servers | MIT | Warm-up and recipe reference |
| OpenApps (Meta) | 6 apps with configurable look and content | AXTree | Single CPU | **NC** | Borrow the idea only. Key finding: **success swings more than 50% across app variants** |
| 2026 environment factories | "Verified Synthetic Web Environments" (2608.21898): 500 sites, feasibility 48.6% → 94.8%, transfers to WebArena, WebShop and MiniWoB. Also WildAWM (2610.26828), MintAct (2609.22083), EnvFactory, RecreationWorld | — | — | Unverified | Design references |

## Contamination

- **High:** anything we both train on and report.
- **Template suites:** new seeds still share templates with the scored tasks.
- **MacArena reuses OSWorld tasks:** never train on OSWorld while MacArena is our score.

## (1) Train vs evaluation split

**Train on:**
- **our own app factory** (the main source);
- MiniWoB++ (20% of task types held out);
- the WebShop train split;
- Mind2Web train only after legal review;
- AgentGym text environments as a warm-up.

**Evaluate only, never train or tune prompts on:**
- WebArena-Verified (hard set);
- OSWorld-Verified in a11y mode;
- AndroidWorld;
- MacArena (permissive subsets published; NC subset internal only);
- OpenApps as an internal robustness check.

**Hygiene:**
- Hash evaluation task strings and block n-gram overlap with factory templates.
- No clones of benchmark sites.
- Keep a frozen factory "out-of-domain" split: unseen component families and themes.

## (2) App factory design

**Pipeline:** seed → `AppSpec` JSON → plain HTML+JS, with a Python state model as the ground truth.

**Components:**
- **Inputs:** text, number, date and time fields; masked fields; autocomplete; combobox; multi-select; radio and checkbox; slider; toggle; rich text; file-picker dialog; drag-to-reorder.
- **Structure:** nav, sidebar, tabs, accordion, breadcrumbs, pagination, infinite scroll, virtualised table (sort, filter, inline edit, bulk), tree, cards, search results.
- **Flows:** wizards with validation, settings ("Apply" vs auto-save), modals, toasts, confirm dialogs, context menus, menubars with submenus and shortcuts, undo, spinners, stale state, popups (cookie banners, newsletters).
- **Checkpoints:** login, OTP, captcha, 2FA, re-enter password, session expired. The correct action is **PAUSE_FOR_HUMAN**; proceeding is penalised.
- **Traps:**
  - pay, delete, transfer, empty trash and delete account placed next to benign buttons;
  - look-alike decoys ("Save" vs "Save & send");
  - pre-checked upsells;
  - destructive defaults.
- **Prompt injection:** in page text, table cells, emails, tooltips, alt text and file names. Acting on it gives reward 0 plus a safety penalty.

**Verifiers:**
- a predicate over the final state, **including "nothing else changed"**;
- a safety predicate over the event log;
- optional dense sub-goal progress;
- exact-match answers for information-seeking tasks;
- **a scripted oracle must solve each task before it is admitted.** That is the step that took feasibility from 49% to 95%.

**Diversity:**
- domain data (CRM, mail, calendar, files, HR, invoices, tickets; multilingual);
- layout grammar;
- synonym and paraphrase label pools;
- **ARIA quality levels** (good, missing labels, div-buttons, duplicates, unlabeled icons);
- themes;
- timing and noise;
- composing 1–6 subtasks across 1–3 apps;
- distractors;
- LLM-proposed new component families, admitted only after the oracle and verifier gates, with the verifiers red-teamed.

## (3) Keeping the sim-to-real gap small

- **Render real HTML in headless Chromium**, not pure synthetic trees.
- Take observations through the **same code path as the product**: CDP `Accessibility.getFullAXTree` or Playwright `ariaSnapshot()`, then AWOS's own numbering and serialiser, so training and deployment observations are byte-compatible.
- One Chromium per worker, contexts reset per episode, no screenshots: roughly 50–200 ms per step on CPU (estimate).
- Pure synthetic trees only as a ~1 ms stage for learning the grammar and format.
- **Desktop look:** macOS/Windows-style chrome in HTML, with AXUIElement/UIA role vocabularies mapped onto the same serialiser and randomly perturbed.
- **Calibrate:** record real macOS accessibility dumps on the evaluation runners, compare tree statistics with factory output, and adjust the generator's priors.

## Bottom line

Train on factory apps + MiniWoB + WebShop with a Playwright a11y driver identical to the product. Evaluate on WebArena-Verified, OSWorld-Verified (a11y), AndroidWorld and MacArena's permissive subsets. Treat all NC material as evaluation-only or inspiration only.

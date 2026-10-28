# Real-web training data: Mind2Web and MolmoWeb (2026-10-25)

**Goal:** real-web demonstrations for the 4B web agent, in its own observation and action format. The format is
the `web_tools.outline()` AX observation through the Chromium harness, `MicroAgent._prompt`, and the GBNF verbs. The
agent is evaluated on Online-Mind2Web (docs/research/small_cua_2026-10-25/PLAN.md), so none of its 300 test tasks may
reach training.

**Code:** `scaffold/rl/train/mind2web_data.py`. Tests: `tests/rl/test_mind2web_data.py`. The output is an
`--extra-rows` input of `scaffold/rl/train/dataset.py`.

## 1. Sources (checked 2026-10-25)

### Mind2Web: [HF osunlp/Mind2Web](https://huggingface.co/datasets/osunlp/Mind2Web), [paper 2306.06070](https://arxiv.org/abs/2306.06070), [GitHub](https://github.com/OSU-NLP-Group/Mind2Web)

**Licence.** Data is CC BY 4.0 and code is MIT.
- The card says the data was released "solely for research purposes".
- The GitHub README asks that the unzipped test files not be redistributed.
- We found no canary string and no "do not train" clause.
- **We train only on the train split, keep the attribution, and do not commit or redistribute the data.**

**Size.**
- Train has 1,009 tasks on 73 websites.
- The test splits are cross-task (252), cross-website (177) and cross-domain (912). They ship only as `test.zip`,
  password-protected with the password given in the README, so the zip cannot be read by accident.
- Train is 11 JSON files, `data/train/train_{0..10}.json`. Shards 0–9 are 520–650 MB each and `train_10` is 28 MB,
  about 5.9 GB in total. The files are JSON, not parquet; HF's auto-parquet exists but is broken for the viewer.
- [osunlp/Multimodal-Mind2Web](https://huggingface.co/datasets/osunlp/Multimodal-Mind2Web) is the same tasks as
  per-action parquet with screenshots.

**Fields.**
- Per task: `annotation_id`, `website`, `domain`, `subdomain`, `confirmed_task`, `action_reprs`, `actions[]`.
- Per action:
  - `action_uid`, `raw_html`, `cleaned_html`
  - `operation{op ∈ CLICK/TYPE/SELECT, original_op (+HOVER, ENTER), value}`
  - `pos_candidates` / `neg_candidates` (`backend_node_id`, `tag`, attributes as a JSON string)

**How snapshots are stored** (verified on the files).
- `raw_html` is a serialised DOM of the page **before** the action. There is no script, no stylesheet, no `href`
  and no title on most pages (9 of 49 pages had a `<title>`).
- Every element carries `backend_node_id` and `bounding_box_rect`. The rect is `-1,-1,-1,-1` when the element was not
  rendered: 1,194 of 3,020 elements on the first page.
- The ground-truth element is marked `data_pw_testid_buckeye="<action_uid>"`, even when `pos_candidates` is empty.
- Attribute names are rewritten: `aria_label` for `aria-label`, `input_value` and `input_checked`.
- Empty elements are serialised self-closing (`<span/>`, `<div/>`, `<button/>`). An HTML parser reads these as open
  tags, which makes pages thousands of levels deep.
- MHTML, DOM snapshots, Playwright traces, HAR and screenshots, including an **after** snapshot per action, exist only
  in the raw dump on Globus. The dump needs a login and its size is not published.

### Online-Mind2Web: [HF osunlp/Online-Mind2Web](https://huggingface.co/datasets/osunlp/Online-Mind2Web), [paper 2504.01382](https://arxiv.org/abs/2504.01382), [GitHub](https://github.com/OSU-NLP-Group/Online-Mind2Web)

- 300 tasks on 136 sites, CC BY 4.0.
- The dataset is gated (auto-approve; an anonymous download returns 401).
- Fields: `task_id`, `confirmed_task`, `website`, `reference_length`, `level`.
- Tasks change over time. 36 were replaced on 2025-11-03 (their ids gain a date suffix) and more after that.
- About 225 of the 300 tasks come from Mind2Web (paper §2.2): 167 as is, 24 rewritten, and 34 via Mind2Web-Live.

Public copies of the task file, used here without an HF token:
- the June 2025 version in [microsoft/fara](https://github.com/microsoft/fara/blob/main/webeval/data/om2w/Online_Mind2Web_06042025.json)
- the post-November-2025 version in [allenai/molmoweb](https://github.com/allenai/molmoweb/blob/main/benchmarks/jsons/online_mind2web.json)

Their union is 346 distinct task texts on 163 site keys.

### MolmoWeb (Ai2): [paper 2604.08516](https://arxiv.org/abs/2604.08516), [blog](https://allenai.org/blog/molmoweb), HF `allenai/MolmoWeb-*`

**Licence.**
- The data is ODC-BY 1.0. Four repos are tagged `odc-by`; HumanTrajs and SyntheticTrajs state ODC-BY 1.0 in their
  README text.
- Instructions were generated with Gemini and GPT, under Google and OpenAI terms.

**Size.**

| Dataset | Trajectories | Steps | Repo size |
|---|---|---|---|
| HumanTrajs | 36K | 623K | ~183 GB |
| SyntheticTrajs | 108K | — | ~284 GB |

SyntheticTrajs subsets:

| Subset | Trajectories |
|---|---|
| `from_template` | 12.5K |
| `task_seeded_wv` | 17.5K |
| `task_seeded_om2w` | 29.6K |
| `multi_agent` | 32.9K |
| `node_traversal` | 15.9K |

**Per step.**
- Each step stores a screenshot PNG, `other_obs{url, page_index, open_pages_titles, open_pages_urls}`, the action
  and its description.
- **There is no HTML, DOM or AX tree.** `node_traversal` references an `axtree_NNN.txt` that did not ship in the
  sampled rows (unverified beyond one row).

**Actions.**
- Human steps are **pixel coordinates**, e.g. `mouse_click(x=311.77, y=205.09)`, plus a bbox.
- Synthetic steps are BrowserGym **element ids** (`click(bid='1173')`) plus a pixel bbox and
  `node_properties{role, value}`. Ai2 converted the bids to coordinates for training.

**Contamination.** Human and synthetic tasks were LLM-generated with *every Online-Mind2Web task of the site as
in-context examples* (paper App. B.2.4 / C.1.2). `task_seeded_om2w` is seeded from the benchmark itself.

## 2. What converts

| Source | Converts faithfully? | Why |
|---|---|---|
| Mind2Web train | **Yes**, offline and cheap | The DOM before each action is stored with stable ids and the target is marked. We render it in Chromium and read it through the product's own observation path. |
| Mind2Web raw dump (Globus) | Later | Its after-action MHTML would give the missing final page (done rows) and real titles and URLs. It needs a Globus login. |
| MolmoWeb (all) | **No offline conversion** | There is no DOM or AX tree per step, so our observation cannot be computed from a screenshot. |
| MolmoWeb (replay) | Possible, live and costly | See §5. |

## 3. The converter (`mind2web_data.py`)

**Per Mind2Web action:**
1. Load `raw_html` into headless Chromium. The harness's `Chromium` launcher maps every host to NOTFOUND.
   - The context has **JavaScript off** and blocks service workers.
   - A route fulfils the snapshot URL from memory and **aborts every other request**.
   - The HTML is untrusted data: it is laid out and read, never run.
2. Repair the dump. This is our code, run through CDP; page scripts stay disabled.
   - Close self-closing non-void tags (`repair_html`). Without this, 36 of 419 sample pages exceeded CDP's CBOR
     nesting limit in `DOM.getDocument`; with it, none do. A flat `DOMSnapshot` fallback stays in place.
   - Hide elements recorded as not rendered: rect `-1,-1,-1,-1` with no rendered descendant (there is no CSS).
   - Hide dialogs that do not contain the target: either closed (no area), or rendered but acted around by the
     annotator. Without this, WebAX grounding refuses everything "behind" a cookie banner or a `role=dialog` header
     panel.
   - Keep the target's own unrendered region visible: a menu dumped before it opened is shown whole (5 sample pages).
   - Restore `aria_*` → `aria-*`, `input_value` / `input_checked`, and `href="#"` on `<a>` elements that contain no
     other control.
   - Put back the values the trajectory already typed or selected on the page, as the product's page shows them.
3. Observe exactly as `ChromiumBackend._snap/_load` does:
   - CDP `Accessibility.getFullAXTree` + `DOM.getDocument` → `axmap.snapshot_from_cdp` → `driver.to_micro_page`.
   - The prompt is `MicroAgent._prompt` with the product wiring (BrokerSecretStore masking, WebAXGrounder).
   - It uses `SYSTEM`, `Step i of 12`, the history lines in the product's wording (`N. click(3) -> ok; now on
     "<title>" (<live text>)`), "Pages so far" and the injection warning.
   - The text is rendered by `chat.render_prompt` / `render_completion`.
4. Locate the target among **our** numbered controls. Try in order:
   - the marked element, then `pos_candidates`;
   - the element itself or its nearest control ancestor (e.g. a span inside a link);
   - a radio option of a radiogroup;
   - the control a `<label>` names;
   - the one compatible control inside the element.
5. Apply the product's checks before writing the row. Any failure means the step is unmapped:
   - `guard_page` (CAPTCHA, MFA, passkey and HTTP walls);
   - WebAXGrounder must resolve the control (no unnamed or ambiguous control);
   - no destructive control;
   - the fill value must be one MicroAgent accepts (≤ 200 chars, not credential-like, not a password or code field);
   - for SELECT, the option must be offered.
6. Emit in the grammar: `click(n)`, `fill(n, "text")` or `select(n, "option")`.
   - A target past the 40-control window first gets `scroll(down)` rows, as the product must.
   - The window resets after a click and is kept after a fill.
   - The step cap is 12 (`--max-steps`).

**Rules for particular steps:**
- **No-ops** (no row, no history line): HOVER, a click on a text box before typing (focus), and a click on a
  `<select>` before SELECT.
- **ENTER:** Mind2Web maps original_op ENTER to CLICK on the text box. **The product has no Enter verb, and the
  harness agent's new actions are not merged.** ENTER therefore becomes `click(<the form's only button>)` (map
  `submit`). If the form does not have exactly one button, the step is unmapped (`enter_no_submit`). When an
  Enter / submit verb lands, emit it here instead (`locate`, branch `orig == "ENTER"`).
- **Final `done("...")`:** emitted only when the page after the last action is known. That is the case when the last
  action is a fill or select: the same snapshot with the value applied. Mind2Web has no page after a final click
  (`no_final_page`). Mind2Web tasks have no answers, so the summary is `Completed: <task>`.
- **After an unmapped step**, the task's rows stop by default (`--after-skip stop`). Later prompts would show a
  history the agent never produced. `--after-skip continue` keeps going, with the page state reset. Every step is
  still located, for the metric.
- **Title:** most dumps have no `<title>`, so the page title falls back to the Mind2Web website name (e.g. `kohls`),
  and the URL path is `/`. Real titles and paths need the raw dump.

### Sample run

- **Data:** first 120 MB of `train_0` and of `train_5`, plus all of `train_10`. That is 288 MB downloaded to `/tmp`,
  not committed.
- **Scope:** 45 tasks, 419 steps.
- **Time:** 106 s on a laptop, about 0.25 s per step.

| Metric | `--after-skip stop` | `continue` |
|---|---|---|
| **Mapping rate** (mapped / non-no-op steps) | **61.3%** (233 / 380) | 61.3% |
| No-op steps | 39 (27 focus clicks, 10 hovers, 2 select-clicks) | 39 |
| Rows | 126 (31 tasks with rows) | 301 |
| Rows per task | mean 2.8, p50 2, p90 9, max 12 | mean 6.7, p50 6 |
| Verbs | click 72, scroll 35, fill 18, select 1 | click 155, scroll 104, fill 38, select 2, done 2 |
| Prompt chars (system excluded) | p50 6.9K, p90 8.7K, max 9.6K (≈ 1.7K / 2.2K / 2.4K tokens) | p50 6.7K, max 12.7K |
| Rows on Online-Mind2Web websites | 51 / 126 | — |

Unmapped steps, by reason (stop mode):

| Reason | Steps | Meaning |
|---|---|---|
| `grounding_refused` | 62 | 32 ambiguous (`link "Details"` × 5 on one page), about 25 unnamed controls, a few `not_found`. The product would stop with needs_user here too. |
| `target_not_semantic` | 58 | The target is in the AX tree, but neither it nor its ancestors is a control we number. Examples: a clickable `<li>` / `<div>` / `<span>` with a JS handler (autocomplete suggestions, jQuery-UI menus, date cells); **links inside headings** (`driver.read_web` returns at a heading without walking its children); native inputs hidden behind styled labels. |
| `too_far` | 9 | Target past 7 windows (280 controls) |
| `target_not_in_ax` | 8 | |
| `select_option_missing` | 7 | |
| `ambiguous_descendants` | 2 | |
| `destructive` | 1 | |

Example rows (observation trimmed):

```text
Goal: Search for the flight status for the flight 12345678 leaving on april 7
Step 3 of 12.
Current page: "aa" (path /); main heading "American Airlines"; form to fill: ...
Your previous actions and results:
1. click(21) -> ok; now on "aa" (Travel information and updates: ...)
2. fill(25, "12345678") -> ok; now on "aa" (Travel information and updates: ...)
<observation untrusted="true"> ... Controls (66 total, showing 1-40): ... </observation>
>>> select(26, "Friday, April 7")

Goal: Book a winery tour in Napa Valley ... for 4 guests on April 15, 10 am in a outdoor setup.
Step 5 of 12.
Current page: "exploretock" (path /); ...; a dialog is open: "Where are you located?"; its buttons: [44] "Close", [46] "Use current location"; ...
1. click(9) -> ok; now on "exploretock" (Where are you located?)
2. scroll(down) -> ok; ...   3. click(46) -> ok; ...   4. scroll(down) -> ok; ...
... Controls (46 total, showing 41-46): ... [45] textbox "Find a location" (dialog) ...
>>> fill(45, "NAPA VALLEY")

Goal: Find the results of the most recent NFL games.
Step 3 of 12.
1. click(25) -> ok; now on "sports.yahoo"
2. scroll(down) -> ok; now on "sports.yahoo"
... Controls (165 total, showing 41-80): ... [62] link "Scores/Schedule" ...
>>> click(62)
```

### Full-set estimate

- Train is 1,009 tasks and about 7.7K actions. At the sample rates that gives about **2.8K rows in stop mode** and
  **6.7K in continue mode**.
- At 0.25 s per step that is about 35 min of CPU in one process. The download (5.9 GB) dominates, so use one CI job
  per shard (11 jobs).
- Each shard is about 600 MB; download, convert, then delete.

### Gaps in the product's observation (for the harness owners; `scaffold/` web code was not edited here)

These gaps cost the agent on the live web too, not only in this converter:
- **Controls inside headings are dropped.** `driver.read_web` handles `heading` and returns, so `<h2><a>Product</a></h2>`
  is text with no link. This is common on result pages.
- **JS-clickable generics are not controls.** A clickable `<div>` / `<li>` / `<span>` (autocomplete options, menus,
  calendar cells) cannot be acted on. Mind2Web's `is_clickable` attribute shows how common they are.
- **`ChromiumBackend._snap` fails on very deep pages.** `DOM.getDocument(depth=-1)` raises "CBOR: stack limit
  exceeded" (36 of 419 unrepaired dumps hit it). `DOMSnapshot.captureSnapshot` → `flat_document()` is a drop-in fix that gives the same
  `dom_index`.
- **Grounding refuses ambiguous and unnamed controls.** This is about 15% of real steps, e.g. repeated "Details" or
  "Seats" links per result row, and icon buttons.
- **The action set lacks Enter / submit**, as PLAN.md says. `fill` also does not re-observe, so autocomplete lists
  that appear after typing are invisible until the next click.

**Fixed in the shared reader (2026-10-26, branch `claude/web-reader`).** The same code path serves the live agent,
this converter and the MiniWoB / factory harness (`harness.chromium.read_cdp` → `axmap` → `driver.read_web`):
- links and buttons inside headings are controls (the heading stays one text line);
- ARIA `option` (outside `<select>`), `menuitem` / `menuitemradio`, `menuitemcheckbox` (as `checkbox`), `treeitem`,
  focusable / clickable `gridcell`, and elements Chromium marks clickable (DOMSnapshot `isClickable`) or that start
  a `cursor:pointer` run are controls (`option`, `menuitem`, `treeitem`, `gridcell`, `clickable`); `click(n)` acts on
  them with a full pointer / mouse event sequence;
- the DOM index comes from one `DOMSnapshot.captureSnapshot` (`flat_document`, any depth); plain wrappers below
  depth 96 are spliced, so very deep pages read;
- unnamed controls get a derived name ("cart (unnamed)", "link to account/settings (unnamed)", "button after
  Quantity (unnamed)", "button 3 (unnamed)"); look-alike controls get their item as context
  (`button "Add to cart" (in "Desk Lamp")`, `link "Details" (in "B-200 | $30")`, `gridcell "12" (in "May 2026")`);
- `ChromiumBackend.fill` waits (≥ 150 ms, then settle) and returns the re-read page.

Sample rerun (same 45 tasks / 419 steps, stop mode): **mapping rate 61.3% → 80.5% (306 / 380)**; `grounding_refused`
62 → 0; `target_not_semantic` 58 → 42 (the rest are JS-clickable `<div>` / `<li>` / `<label>` targets a static dump
cannot reveal: it has no listeners and no CSS); rows 126 → 238. Fixtures: `tests/rl/test_web_reader.py`.

## 4. Contamination rules

**Hard rules, enforced in code:**
1. **The exclusion list is required.** `--exclude` takes Online_Mind2Web.json or any copy, as a JSON list or JSONL
   with `confirmed_task` / `task_description` / `task`. Pass **every version you have**; the union is excluded. The CLI
   refuses to run (exit 2) without the list or with an empty one.
2. **Task text matches are excluded.** A Mind2Web task matches when, after normalisation (lower case, alphanumerics),
   its SequenceMatcher ratio is ≥ 0.75 **or** its token Jaccard is ≥ 0.7 against any benchmark task. Matched tasks
   are dropped and listed in stats (`excluded`).
3. **Every written row is re-checked.** `check_row` raises `ContaminationError`, a subclass of
   `dataset.EvalSplitError`; the build aborts and the rows file is deleted. `meta.split` must be `train`.
   `dataset.py --extra-rows` re-validates every row again.
4. **Website overlap is reported, not excluded by default.** It is tracked per row in `meta.om2w_website_overlap`, and
   `--exclude-overlap-websites` drops those tasks. The benchmark tests live sites; training on the same site with
   *different* tasks is the normal Mind2Web "cross-task" setting. Report the overlap next to every score.
5. **MolmoWeb:** drop `task_seeded_om2w` entirely. Run every other MolmoWeb task through the same fuzzy check, because
   the human tasks were generated with benchmark tasks as examples.

**Result on the whole Mind2Web train split.** The task text of all 1,009 tasks was read through parquet column
projection on Multimodal-Mind2Web: 2 MB fetched, no HTML (`--report-only`).
- **0 of 1,009 train tasks match any of the 346 benchmark task texts.** The closest pair has ratio 0.743 and
  Jaccard 0.55, and is unrelated: "Search for Romantic Audio fiction books with the lowest price" vs "Search for
  NordicTrack with the lowest price".
- So the roughly 225 Mind2Web-derived benchmark tasks evidently come from Mind2Web's *test* splits, which we never
  open.
- **Website overlap:**
  - 37 of 73 train websites, covering **517 of 1,009 train tasks**, are Online-Mind2Web sites. These include united,
    spothero, uniqlo, gamestop, imdb, espn, steam, rei, carnival, mta, rottentomatoes, carmax, cvs, cargurus,
    megabus, amtrak, kohls, ign, eventbrite, amazon, ryanair, qatarairways, kbb, marriott, ikea, soundcloud,
    flightaware, seatgeek, foxsports, target, booking, boardgamegeek, nfl, expedia, apple, airbnb and discogs.
  - Brand only: sports.yahoo → finance.yahoo.com (17 tasks).

## 5. MolmoWeb plan: replay, not convert

MolmoWeb stores no DOM, so our observation can only be produced **live**. To replay a trajectory:
1. Open `other_obs.url` of step 0 in the live-web harness (`scaffold/rl/web/`, once merged).
2. For each step, map the stored action to one of our controls on the live page:
   - **Synthetic (bid):** match on `node_properties{role, value}` plus the accessible name. Break ties with the bbox
     centre against `DOM.getBoxModel` of our candidate controls, at the 1280-wide viewport.
   - **Human (pixels):** use `DOM.getNodeForLocation(x, y)` at the recorded `image_w × image_h` viewport, then the
     same ancestor and descendant search as `locate()`.
3. Execute the step in our agent's verbs, re-observe, and check that the page still matches the next step: same URL
   path, and the next target is found. The trajectory stops at the first divergence. Every row is written through
   the same `check_row` and the product checks.
4. Do it cheaply:
   - free GitHub Actions runners with many workers;
   - cap at one attempt per trajectory;
   - measure and report the share of tasks blocked by bot checks, and never bypass a check.

**Estimated rows.**

| Pool | Steps | Note |
|---|---|---|
| Eligible after dropping `task_seeded_om2w` (≈ 0.33M steps) | ≈ 1.6M | human 623K, AX single-agent remainder ≈ 0.46M, multi-agent 438K, node-traversal 151K |

Survival assumptions:
- live replay survives about 30–40% of steps, given site drift since capture and first-divergence stops;
- our mapping rate is about 60% (as on Mind2Web);
- scroll rows add about 30%.

That gives roughly **0.3–0.5M rows from a full replay**. A **pilot of 2K trajectories (≈ 25K steps → ≈ 6–10K
rows)** should come first, to measure the real survival rate before any large run. At about 2–3 s per live step, the
pilot is about 15–20 runner-hours.

## 6. Commands

The CLI is built for CI. Downloads go to a fresh temp directory and are treated as untrusted data. The data is never
committed.

```bash
# exclusion list (any or all of these; HF needs HF_TOKEN after accepting the gate)
curl -L -H "Authorization: Bearer $HF_TOKEN" -o /tmp/om2w/hf.json \
  https://huggingface.co/datasets/osunlp/Online-Mind2Web/resolve/main/Online_Mind2Web.json
curl -L -o /tmp/om2w/fara.json \
  https://raw.githubusercontent.com/microsoft/fara/main/webeval/data/om2w/Online_Mind2Web_06042025.json
curl -L -o /tmp/om2w/molmoweb.json \
  https://raw.githubusercontent.com/allenai/molmoweb/main/benchmarks/jsons/online_mind2web.json

# one shard (CI matrix over N = 0..10); a ranged prefix (curl -r 0-125829119) is fine for smoke runs
curl -L -o /tmp/m2w/train_$N.json https://huggingface.co/datasets/osunlp/Mind2Web/resolve/main/data/train/train_$N.json
PYTHONPATH=. python -m scaffold.rl.train.mind2web_data --input /tmp/m2w/train_$N.json \
  --exclude /tmp/om2w/hf.json --exclude /tmp/om2w/fara.json --exclude /tmp/om2w/molmoweb.json \
  --out out/m2w_$N.jsonl            # + out/m2w_$N.jsonl.stats.json

# contamination report over task texts only
PYTHONPATH=. python -m scaffold.rl.train.mind2web_data --report-only --input tasks.jsonl --exclude ... --out rep.json

# into the SFT build
python -m scaffold.rl.train.dataset --input ... --extra-rows m2w=out/m2w_0.jsonl:1 ... --out ...
```

Chromium is the Playwright build that `harness.chromium.find_chromium()` resolves (`AWOS_RL_CHROMIUM` or the
Playwright cache).

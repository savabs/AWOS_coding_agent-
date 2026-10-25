# Live-web harness: Online-Mind2Web (M0)

Status: built 2026-10-25 on `claude/web-harness`. Part of milestone M0 in
`docs/research/small_cua_2026-10-25/PLAN.md`: an honest baseline of our models on real websites.

## What it does

`scaffold/rl/web/` runs our text-only agent on the public
[Online-Mind2Web](https://github.com/OSU-NLP-Group/Online-Mind2Web) benchmark. The benchmark has 300 tasks on 136 real
websites. The tasks come from the HF dataset `osunlp/Online-Mind2Web` (gated with auto-approve, CC-BY-4.0).

Each task runs as one episode on the live web. The path is the same one the RL harness uses:

1. CDP accessibility tree.
2. `axmap`.
3. `driver.to_micro_page`.
4. `web_tools.outline`.
5. The MicroAgent prompt, the GBNF grammar and the code guards, with three additional open-web verbs.

The harness writes two outputs:

- `trajectories.jsonl`, in the usual harness format.
- One folder per task in the layout the official WebJudge reads.

**The harness does not decide success.** The live web has no executable verifier, so WebJudge scores the
`judge/` folders afterwards (see `scaffold/rl/web_judge`, a separate workstream). Blocked episodes count as failures,
but they are reported separately.

| File | Role |
|---|---|
| `scaffold/rl/web/source.py` | `OnlineMind2WebSource` (a `contract.TaskSource`): loader, HF download, `all` / `dev` splits |
| `scaffold/rl/web/blocked.py` | Detects CAPTCHAs, Cloudflare pages, bot walls, 403 / 429 responses and broken loads |
| `scaffold/rl/web/episode.py` | One live episode: screenshots, page clipping, the judge layout, and a v2 validator |
| `scaffold/rl/web/runner.py` | CLI: `run`, `summarize`, `freeze-dev`, `validate`; resumable and shardable |
| `scaffold/rl/web/data/dev_ids.json` | The fixed 100-task dev split |
| `scaffold/rl/harness/chromium.py` | `Chromium(web=True)` / `ChromiumBackend(web=True)`: real hosts, the open-web verbs, screenshots |
| `scaffold/agent/desktop/micro_agent.py` | The new verbs in the parser (always on), and in the grammar and prompt (opt-in) |
| `.github/workflows/rl-web.yml`, `ci/rl_web.env`, `scripts/rl/web_matrix.py` | CI runs sharded across free runners |
| `tests/rl/test_web_harness.py` | 21 tests with no network: a fake site on 127.0.0.1 and a fake task file |

## How to run

```bash
# Tasks: --tasks-json FILE, or AWOS_OM2W_JSON, or the cache, or a download with HF_TOKEN
#        (pinned revision eacad896..., cached under ~/.cache/awos-rl/online-mind2web/)

# Local model (llama-server on 127.0.0.1:8080)
PYTHONPATH=. python -m scaffold.rl.web run --model local --split dev --shard 1/12 --out runs/web/4b-dev-s1/

# Claude via CloudTextModel. The AWOS_CLOUD_MAX_USD spend cap (default $5) is split across workers.
PYTHONPATH=. python -m scaffold.rl.web run --model cloud --split dev --n 5 --out runs/web/cloud-dev/

# Rewrite summary.md / summary.json for a run directory
python -m scaffold.rl.web summarize runs/web/4b-dev-s1/

# Check the judge folders against the schema v2 rules
python -m scaffold.rl.web validate runs/web/4b-dev-s1/judge/

# Write data/dev_ids.json from the dataset (then commit it)
python -m scaffold.rl.web freeze-dev
```

### Options

- `--max-steps 25`: the episode's step cap. MicroAgent's cap of 12 is raised for this agent only, through
  `max_steps_cap`.
- `--max-seconds 600`: the wall-clock cap per task (`max_seconds_cap`).
- `--workers N`: N processes, each with its own Chromium.
- `--guards product|raw`: the same switch as in the RL harness.
- `--allow-private`: tests only. See the URL policy under "Action design".

Runs are resumable: a task that already has a row without an infra error is skipped.

### CI

To start a run, push to `bench-cloud/rl-web-<anything>`, or dispatch `rl-web` with the inputs `models`, `split` and
`shards`. The `.github/workflows/ladder.yml` workflow excludes `bench-cloud/rl-**`, so these pushes never start the paid
ladder.

Each job does the following:

1. Serves one GGUF from `MODEL_SPECS` in `ci/rl_eval.env` (the same pins and sha256 checks as rl-eval) with
   llama-server on the CPU.
2. Installs Playwright Chromium.
3. Downloads the task file with the `HF_TOKEN` secret.
4. Runs one shard (`max-parallel: 12`).
5. Uploads `runs/web/<model>__web-<split>__shard<K>of<N>/`, which holds the trajectories, `judge/`, the summary and
   the llama-server log.

The summary job then merges the shards for each model, writes the merged summary and validation, and writes
`dev_ids.json` from the dataset so it can be committed.

The settings live in `ci/rl_web.env`: `MODELS`, `SPLIT`, `SHARDS`, `MAX_STEPS`, `MAX_SECONDS`, `NP` and
`CTX_PER_SLOT`. The file holds no secrets.

## Splits

- `all`: all 300 tasks.
- `dev`: a fixed set of 100 tasks, stratified by level (easy, medium, hard).
  - Each level gets a quota proportional to its share of the dataset, with the remainder going to the largest
    fractions.
  - Within each level, the tasks are drawn with `random.Random("online-mind2web-dev-v1:<level>")` from the sorted ids.
  - The ids are committed in `data/dev_ids.json`.
  - **That file is still empty.** No `HF_TOKEN` was available where this was built. Until it is filled, every run
    computes the same deterministic selection from the pinned dataset revision and records
    `dev_ids_source: "computed"` in `run.json`. The CI summary job produces the file to commit.

## Action design

The grammar keeps the original verbs: `click`, `fill`, `select`, `copy_to_secret`, `scroll`, `done` and `needs_user`.
Three verbs are added for the open web:

| Verb | Meaning | Chromium executor |
|---|---|---|
| `fill_enter(n, "text")` | Fill a text input, then press Enter in it | the usual fill (clear, `Input.insertText`, change), then focus and a trusted `keyboard.press("Enter")`, then settle and observe |
| `go_back()` | Browser back | `page.go_back`. Refused when there is no previous page |
| `goto("https://...")` | Open an http(s) URL | the URL policy check, then `open` |

**One design for Enter.** Enter is only ever pressed in the field that was just filled. A model that wants to
resubmit a field sends `fill_enter` again with the same text. There is no free-standing key action.

**Backward compatibility.**

- The parser always accepts the new verbs, so a reply from an older model still parses exactly as before.
- Control numbers may now have 4 digits, because real pages can have more than 999 controls.
- The grammar (`build_grammar(..., web=True)`) and the prompt (`SYSTEM + SYSTEM_WEB` and the web reply example) offer
  the new verbs only when `MicroAgent(web_verbs=True)`. Without it, the product's prompts and grammars are
  byte-identical to before, and `ACTION_SCHEMA` (the product's cloud JSON prompt) is unchanged.
- If a model emits a web verb while `web_verbs` is off, the action is rejected ("... is not available here").

**URL policy.** In web mode the URL policy (`check_web_url`) applies to `goto`, `open`, popups and the start URL:

- Only http and https are allowed. `file:`, `data:`, `javascript:` and `chrome:` are refused.
- Loopback, private and link-local hosts are refused, unless `--allow-private` (tests). This keeps a run from
  reaching the runner's own services, such as llama-server on 127.0.0.1:8080, or the cloud metadata endpoint.

**Other changes in web mode only:**

- A click first scrolls its element into view, so the screenshot shows what was clicked.
- After `scroll(down|up)`, MicroAgent calls the optional `browser.reveal(page, n)`, so the viewport follows the
  40-control window the model sees.
- A fill or select on a stale element is shown to the model as a rejected action, not an infra crash.

**Not added: hover, free key presses, tabs and coordinates.**

## Prompt budget

The prompt stays at about 6k tokens or less. The 40-controls window and `scroll` are unchanged, and the outline still
keeps 3000 characters of page text. On top of that, a page hook clips each observation:

- Control names: 150 characters or less.
- Each alert, status, region and dialog: 600 characters or less.
- At most 6 regions, 20 headings and 30 options.

`max_prompt_tokens_est` is recorded per episode and in the summary. On a synthetic page with 400 long links, 12 large
regions and 26k characters of text, the prompt stays under 6000 estimated tokens (tested). On real Wikipedia it was
2677.

## Output layout

```
OUT/
  run.json  summary.json  summary.md  trajectories.jsonl
  judge/<task_id>/result.json
  judge/<task_id>/trajectory/0000.png 0001.png ...      (1280x800 viewport PNGs)
```

`judge/` holds only task folders, as the official `src/run.py --trajectories_dir judge/` expects. `result.json` follows
the Online-Mind2Web submission schema v2 (`online-mind2web-v2`; `data/schema_v2` in their repo, which the leaderboard
uses). The official `run.py` reads v2 directly. The top-level fields are:

- `schema_version`: `online-mind2web-v2`.
- `task`: the task text.
- `task_id`: the dataset id.
- `agent_final_answer`: the text of `done("...")`, or null.
- `reference_length`: the human reference length from the dataset.
- `action_history`: a list of `{step, screenshot, url, action, action_status, thought}`.

The `action_history` steps are:

- **Step 0:** `page -> NAVIGATE -> open the task's start page <url> | SUCCESS`, with the screenshot after the load.
- **Step j (one per model step):** the action in Grammar A, for example
  `textbox "Search Wikipedia" -> TYPE -> fill_enter(4, "Alan Turing") then press Enter | SUCCESS`. Rejected or
  unparseable steps end in `| FAILED`. The screenshot shows the page after the action, and `thought` is the model's
  `# reason` line.
- **Last step:** `TASK_COMPLETE -> ANSWER: <text>` when the model replied `done`. Otherwise it is
  `TASK_COMPLETE -> STOPPED: <why>` and the answer is null.

`result.json` also carries three AWOS keys:

- `level`.
- `harness_status`: `ok`, `blocked` or `infra_error`.
- `harness_note`.

`scaffold/rl/web_judge` uses these keys to skip blocked episodes and count them as failures. The official judge
ignores extra keys. They are the only departure from the strict schema, and `validate` allows them.

Screenshots are taken outside the timed model call, so they do not count toward model latency. If a screenshot fails,
a 1x1 placeholder is written, because the schema needs a file for every step, and the step is listed in
`screenshots_missing`.

## Blocked and broken episodes

A hook runs on every observation and calls `detect_block`. A blocked page raises `UserNeeded` before the model sees
it, so the episode stops with `needs_user` and `stop_by = blocked:<kind>`. The episode records
`harness.blocked = {kind, evidence, url, status}`, writes `harness_status: "blocked"`, and the summary reports it under
`blocked_by_kind` and `blocked_tasks`.

| Kind | When |
|---|---|
| `cloudflare` | The title or main heading reads like "Just a moment..." or "Checking your browser...", or the page has challenge markup (`#challenge-form`, a `challenges.cloudflare.com` iframe) |
| `captcha` | A short or interstitial page asks for a CAPTCHA or human check (reCAPTCHA, hCaptcha, press-and-hold, "unusual traffic"), or carries DataDome / PerimeterX markup, or a visible reCAPTCHA / hCaptcha widget fills a near-empty page |
| `bot_wall` | "Access denied", "Pardon our interruption", "You have been blocked" and similar, on a short page or with HTTP 4xx/5xx |
| `http_<code>` | A 403 or 429 anywhere, or any status of 400 or more on the start page |
| `empty_page` | The start page rendered no title, no text and no controls |
| `load_failed` | The start URL could not be opened at all (DNS, TLS, timeout) |

Two cases are deliberately not counted as blocked:

- A 404 that the model reaches with its own `goto` is the model's failure.
- Text signals need an interstitial-looking page, so a Wikipedia article about CAPTCHA and the invisible reCAPTCHA
  badge do not count.

The harness never tries to solve or bypass a CAPTCHA or bot check. It does not fake the user agent and uses no stealth
plugins. Chromium runs as Playwright's headless shell with its default user agent.

## Smoke result (one live task)

The live check was one task on en.wikipedia.org with the local llama-server already running on this laptop:
"Search Wikipedia for Alan Turing and find the year he was born." It ran with `--max-steps 8`.

- **Result:** answered `done("found the answer: 1912")` in 4 steps and 43 s. The episode was not blocked and had no
  infra errors.
- **Actions:**
  1. `fill_enter(4, "Alan Turing")`
  2. a detour, `fill_enter(4, "1912")`
  3. `fill_enter(4, "Alan Turing")`
  4. `done(...)`
- **Model cost:** 3.6 s per step, 10.8k tokens, and the largest prompt was about 2.7k tokens.
- **Judge layout:** valid. The 5 screenshots show the Main Page, the Turing article, the article on the year 1912, and
  the Turing article again.

## Known limits

- **Headless shell from cloud IPs.** Expect a sizable `blocked` share on commercial sites. That share is what this
  baseline measures. Lowering it (a full Chromium build, a residential network such as the laptop or Kaggle) is a
  separate decision, and it never includes solving CAPTCHAs.
- **ARIA widgets.** `axmap` turns `role=option` and `menuitem` into text, not controls. Custom autocomplete lists and
  menus can only be reached through `fill_enter`, `click` on a link or button, or `goto`. Hover menus are not
  reachable at all.
- **Iframes are not read.** This matches the product.
- **Scroll pages the control list.** The viewport follows the window for screenshots, but content that loads lazily
  on scroll only shows up in the next observation after an action.
- **Product guards stay on by default (`--guards product`).**
  - Clicks on controls named buy, pay, purchase, remove, delete, sign out and similar are refused in code.
  - A visible password field ends the episode with needs_user (there are no credentials).
  - The product's CAPTCHA regex can stop an episode that `detect_block` did not count as blocked. It then shows up as
    `stop_by = guard:captcha`, not as blocked.
  - `--guards raw` turns these guards off.
- **`done` is never verified here.** The outcome is `completed_unverified`, and success comes only from WebJudge.
- **`dev_ids.json` is empty until the dataset is downloaded with `HF_TOKEN`** (see "Splits").

### Untested

- The CI workflow has not run yet. Its YAML, matrix and settings are covered by a unit test.
- The HF download path has not run, because there is no token here. Only the missing-token error is tested.
- The `cloud` model on the web has not run (no paid calls).
- Multi-worker runs (`--workers > 1`) have not been tested.

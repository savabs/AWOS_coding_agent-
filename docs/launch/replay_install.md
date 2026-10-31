# AWOS Replay: install, Chrome Web Store packaging, privacy policy

*Show it once, it does it forever.* AWOS Replay is a Chrome extension that records a web chore once and
replays it with checks. By default the model it asks for help runs on the user's own Mac. This page covers
three things: the one-command installer for that model, building the store zip, and the drafts the owner
needs to publish (store listing, permission justifications, privacy policy, checklist).

Files: `scripts/install_replay.sh`, `packaging/build_extension_zip.sh`,
`packaging/permission_justifications.json`, `tests/test_install_replay.py`.

---

## 1. Install the local models (Mac, Apple Silicon)

The command for the website:

```bash
curl -fsSL https://raw.githubusercontent.com/savabs/AWOS_coding_agent-/main/scripts/install_replay.sh | bash
```

(This works once the branch with the installer is merged to `main`. Before that, put
`refs/heads/<branch>` in place of `main` to test it.)

It sets up two local models, each behind its own llama-server on loopback:

| Server | launchd label | Address | Model | Used for |
|---|---|---|---|---|
| repair / verify | `ai.awos.replay-model` | `127.0.0.1:8080`, alias `local` | G7, Qwen3.5 2B Q4_K_M, 1.3 GB | Replay: finding a moved control, the verifier |
| Tell-it agent | `ai.awos.replay-agent` | `127.0.0.1:8081`, alias `qwen3.5-4b` / `qwen3.5-9b` | Qwen3.5-4B Q4_K_M, 2.7 GB (or 9B, 5.7 GB) | Tell it: doing a chore from your words |

The 2B cannot drive a page by itself, so **Tell it needs the agent server**.

| Command | What it does |
|---|---|
| `... \| bash` | Installs, or repairs an existing install. Safe to run again. Picks the agent model from RAM (below) |
| `... \| bash -s -- --agent 4b` | Agent model Qwen3.5-4B (the default on 8 GB+ Macs) |
| `... \| bash -s -- --agent 9b` | Agent model Qwen3.5-9B: stronger, for 16 GB+ Macs. Replaces a 4B this script downloaded |
| `... \| bash -s -- --agent none` | Removes only the agent server and its model; the 2B repair/verify server stays |
| `... \| bash -s -- --status` | Both servers: launchd agent installed and loaded, resident MB, up or down, the model files, whether 8081 really serves an agent model (not the 2B), and what install added |
| `... \| bash -s -- --uninstall` | Removes exactly what install added, both servers. Add `--keep-model` to keep the GGUFs |
| `AWOS_DRY_RUN=1 bash install_replay.sh --agent 9b` | Prints every action and changes nothing |

**Which agent model.** With no `--agent`, the installer keeps the agent model it installed earlier. On a
first install it picks **4b** when the Mac has 8 GB of RAM or more, and **none** below that. On a 16 GB+ Mac it
also prints that `--agent 9b` is stronger, but it never picks the 9B by itself (5.7 GB download and about
5.7 GB of RAM while loaded). It always prints the choice and how to change it:

```
Tell-it agent model: 4b (Qwen3.5-4B, 2.7 GB download; default for 16 GB RAM)
  Your Mac has 16 GB: --agent 9b is stronger (5.7 GB download, ~5.7 GB while loaded).
  Change it any time: install_replay.sh --agent 4b | --agent 9b | --agent none
```

`--agent 9b` on a Mac with less than 16 GB installs it anyway, with a warning.

**Pinned agent files.** These are the exact Hugging Face files CI evaluates (`ci/rl_eval.env` `MODEL_SPECS`).
A test keeps the installer and that file equal. The sha256 is the LFS oid from the HF API for that revision.

| `--agent` | Repo | File | Revision | Bytes | sha256 |
|---|---|---|---|---|---|
| `4b` | `unsloth/Qwen3.5-4B-GGUF` | `Qwen3.5-4B-Q4_K_M.gguf` | `e87f176479d0855a907a41277aca2f8ee7a09523` | 2,740,937,888 | `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4` |
| `9b` | `unsloth/Qwen3.5-9B-GGUF` | `Qwen3.5-9B-Q4_K_M.gguf` | `3885219b6810b007914f3a7950a8d1b469d598a5` | 5,680,522,464 | `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8` |

**What install does, and nothing else:**

1. Checks that the Mac runs macOS on Apple Silicon (it refuses Intel Macs and Rosetta shells). Then it checks
   free disk **before anything is downloaded or installed**. It needs room for every model not yet on disk,
   plus 1 GB of headroom: 2B + 4B is about 4 GB, then 1 GB more; 2B + 9B is about 7 GB, then 1 GB more. A
   half-downloaded `.part` counts as already there. If the models don't fit, it stops and suggests
   `--agent 4b` or `--agent none`.
2. If `llama-server` is missing, runs `brew install llama.cpp`. If Homebrew is missing too, it **asks first**
   (`Install Homebrew now? [y/N]`, read from the terminal). With no terminal it answers no, prints the brew.sh
   link and stops. The script never uses `sudo` itself. Homebrew's own installer may ask for the Mac password.
3. Downloads the repair/verify model (G7, Qwen3.5 2B Q4_K_M, 1.3 GB) from the public GitHub release
   `rl-qwen3.5-2b-g7-rv` into `~/.awos/models`. It uses the same file and sha256 as `scripts/awos_model.sh`
   (a test keeps the two in sync). Then it downloads the agent model from the pinned Hugging Face revision.
   Both downloads go to `<file>.part` with curl retries and resume (3 tries), and are renamed only when the
   sha256 matches, so a cut-off or wrong download never looks finished.
4. Writes two launchd **user** agents and loads each with `launchctl bootstrap gui/<uid>`. They start at
   login and restart if the server crashes (ThrottleInterval 30 s). Both have the same resource guard (below)
   and listen on **127.0.0.1 only**.
   - `~/Library/LaunchAgents/ai.awos.replay-model.plist` runs
     `llama-server -m <2B> --alias local --host 127.0.0.1 --port 8080 -c 4096 -np 1 --jinja --reasoning off
     --cache-prompt --cache-ram 0 -cms 256 --temp 0 --sleep-idle-seconds 300`.
     These are the EngineBench flags from `awos_model.sh` plus the idle sleep.
   - `~/Library/LaunchAgents/ai.awos.replay-agent.plist` runs
     `llama-server -m <4B|9B> --alias qwen3.5-4b|qwen3.5-9b --host 127.0.0.1 --port 8081 --jinja --reasoning off
     -c 12288 -np 1 --temp 0 --cache-ram 0 -cms 256 --sleep-idle-seconds 300`.
     12k context holds the page text plus the step history Tell it sends. Tell it constrains every answer
     with a GBNF `grammar`, which llama-server's `/v1/chat/completions` supports.
5. Records what it added in `~/.awos/replay/installed`, one line per item: directories it created, files it
   wrote or downloaded, `brew llama.cpp` if it installed that, and Homebrew if it installed that. The agent
   server's plist, log and model are recorded as `agentfile` lines, and `agent 4b|9b` records the choice.
   `--uninstall` reads this record. `--agent none` and a 4b↔9b switch remove only the `agentfile` items
   (a switch keeps the plist and log, which it rewrites). Anything that was already there before install
   (an existing model, llama.cpp, `~/.awos`) is not recorded, so uninstall leaves it alone. Directories are
   removed only if they are empty. Homebrew is never removed automatically, even when this script installed it,
   because other apps may depend on it by then; uninstall prints the official link to remove it.

The script never touches browser profiles, other apps, or anything outside `$HOME` apart from
`brew install llama.cpp`. The whole script is wrapped in `main "$@"` on its last line, so a cut-off
`curl | bash` download never runs half a script. Child processes never read the piped script from stdin.

Environment variables:
- `AWOS_MODEL_PORT` (default 8080) and `AWOS_AGENT_PORT` (default 8081). If you change one, set the same
  endpoint in the extension Options.
- `AWOS_REPLAY_MODEL` (`g7`, `g6` or `base2b`).
- `AWOS_REPLAY_AGENT` (`4b`, `9b` or `none`, same as `--agent`).
- `AWOS_SLEEP_IDLE` (seconds, default 300; `-1` keeps the models loaded).
- `AWOS_NONINTERACTIVE=1`.

If something else already serves 8080 or 8081 (for example `scripts/awos_model.sh` or a hand-started
llama-server in a terminal), install stops and says so instead of starting a second server.
`bash scripts/awos_model.sh --check` also checks the agent server. It fails if 8081 serves the 2B (alias
`local`, or a 2b / g6 / g7 name). If 8081 is down, it only prints a note, because Replay works without it.

**After install** (the script prints these steps):
1. Install the extension from the Chrome Web Store. For a dev build: `chrome://extensions` → Developer mode →
   Load unpacked → `extension/`.
2. Extension Options → endpoint `http://127.0.0.1:8080/v1` (this is the default) → Save.
3. Record a chore once (popup → Record), then Replay it.
4. Tell it: Options → agent endpoint `http://127.0.0.1:8081/v1`, model `qwen3.5-4b` (the defaults; with
   `--agent 9b`, set the model name to `qwen3.5-9b`).

### Resource guard: measured on an M-series MacBook Air (16 GB), llama.cpp b11146, G7 model

The owner asked that the server never slow the laptop down. The plist does four things for that:

- **ProcessType Background**: launchd applies its background resource limits (CPU scheduling and I/O throttling).
- **Nice 10**.
- **LowPriorityIO** and **LowPriorityBackgroundIO**.
- **`--sleep-idle-seconds 300`**: llama.cpp's built-in sleep, which unloads the model after 5 idle minutes and reloads it on the next request.

The installer adds the sleep flag only if `llama-server --help` lists it. Homebrew's current llama.cpp does.

| State | Resident (RSS) | Footprint (anonymous memory) | CPU |
|---|---|---|---|
| Loaded, idle | 817 MB | 190 MB | ~1% |
| Right after a request | 858 MB | 251 MB | — |
| **Asleep (after the idle timeout)** | **58 MB** | **59 MB** | ~0% |
| Woken by the next request | 849 MB | 260 MB | — |

Most of the 817 MB is the memory-mapped GGUF. Those pages are file-backed and clean, so macOS can drop them
under memory pressure without swapping. The anonymous footprint (KV cache, Metal buffers) is about 190 to
260 MB.

- **Waking from sleep:** the first request after sleep took 5.6 s (reload) instead of about 0.15 s. That fits
  inside the extension's model timeouts (20 s for a lookup, 60 s for verification).
- **`/health` keeps answering `ok` while asleep and does not wake the model.** The extension's "is it up?"
  checks therefore don't keep it loaded.
- **Background priority did not slow the model:** a repair-sized prompt took 3.60 to 3.66 s at normal
  priority and 3.39 to 3.65 s under `taskpolicy -b` + `nice 10`, because Metal does the work. So Background
  costs Replay nothing and protects foreground apps.

To re-measure: `bash install_replay.sh --status` shows each server's resident MB.

### The agent server's memory: estimated from file sizes, not measured

These numbers were **not measured** with the real 4B or 9B on a Mac. They are reasoned from the GGUF sizes and
the 2B measurements above. The plist has the same guard as the 2B: Background, nice 10, low-priority IO,
`--cache-ram 0 -cms 256`, and `--sleep-idle-seconds 300`.

| Agent model | GGUF | Loaded (memory-mapped weights + context) | Asleep (after 5 idle min) |
|---|---|---|---|
| 4B (`--agent 4b`) | 2.74 GB | ≈ 2.7 GB mapped, plus the 12k-token context cache and Metal buffers | ≈ 60 MB |
| 9B (`--agent 9b`) | 5.68 GB | ≈ 5.7 GB mapped, plus the context cache | ≈ 60 MB |

- **Why these estimates:** the 2B's 1.27 GB GGUF showed up as about 0.8 GB resident while loaded. Most of
  that was the file-backed mapping, which macOS can drop under memory pressure without swapping. Asleep, the
  2B was 58 MB, and llama-server's own footprint does not depend on the model. So the 4B's weights take about
  2.7 GB while loaded, and about 60 MB remain once it sleeps.
- **The context cache:** `-c 12288` is three times the 2B's 4096 and is allocated on load. Qwen3.5 mixes
  linear-attention layers with a few full-attention layers, so only those few hold a per-token KV cache.
  Measure it with `--status` on the first real install before quoting a number.
- **Both servers loaded:** Replay uses the 2B and Tell it uses the agent. A Tell-it session wakes only the
  agent server. Worst case, with both awake, is about 0.8 GB + 2.7 GB with the 4B, or 0.8 GB + 5.7 GB with the
  9B. That is why the installer never picks the 9B by itself and only suggests it on 16 GB+ Macs.
- **Waking from sleep:** expect a few seconds more than the 2B's 5.6 s, because there are more weights to
  map. Tell it waits up to 120 s per step, which covers that. Not yet checked: whether `/v1/models`, which Tell it
  probes with a 2.5 s timeout before starting, answers while the server is asleep, as `/health` does.

**Logs:** `~/.awos/replay/model-server.log` and `~/.awos/replay/agent-server.log`. On each install, a log is
reset if it has grown past 20 MB.

---

## 2. Build the Chrome Web Store zip

```bash
bash packaging/build_extension_zip.sh            # validate, then write dist/awos-replay-<version>.zip
bash packaging/build_extension_zip.sh --check    # validate and print permissions only, no zip
bash packaging/build_extension_zip.sh --strict   # also fail on store blockers: use this for the real upload
```

**What it checks in `extension/manifest.json`:**

- MV3.
- The version is in Chrome's format (1 to 4 dot-separated integers, each 0 to 65535, no leading zeros).
- Every file the manifest names exists (service worker, content scripts, popup, options, side panel, icons, web-accessible resources).
- There is no remote code: no `background.scripts`, and no http(s) sources in the CSP.

**What it prints:** every permission, host permission and content-script match.

**What fails the build:** any of those that has no justification in
`packaging/permission_justifications.json`. So when someone adds `alarms`, `downloads` or `notifications`,
the build forces a written reason. Reasons for those three, and for `scripting`, `activeTab`,
`contextMenus`, `sidePanel`, `offscreen`, `webNavigation`, `unlimitedStorage`, `[::1]` and
`api.anthropic.com`, are already written.

**Store blockers** are printed always and fail the build only with `--strict`:

- no `icons.128`
- "MVP" or "beta" in the name
- a description longer than 132 characters

**Output:**

- `dist/awos-replay-<version>.zip`. It is reproducible (sorted entries, fixed timestamps, mode 0644) and
  leaves out `tests/`, `docs/`, `dev/`, `node_modules/`, dotfiles, `*.md`, `*.map`, `*.test.js`, `*.ts` and
  `package.json`.
- `dist/awos-replay-<version>.permissions.md`, a table to paste into the store's *Privacy practices* tab.
- The zip's sha256.

**State of `extension/` at the time of writing (v0.3.0):** the manifest validates and the zip builds
(11 files, 29 KB). `--strict` fails on three store blockers that the extension owner must fix before upload:
1. The description is 139 characters; the limit is 132. Suggested replacement (≤132): *"Show it once, it
   does it forever. Records a web chore, then replays it with checks, an approval gate and a private local
   model."* (127 characters)
2. The name is "AWOS Replay (MVP)". Use "AWOS Replay".
3. There are no icons. Add `icons` with 16, 48 and 128 px PNGs.

Also consider `http://[::1]/*` in `host_permissions`: `model.js` accepts `[::1]` as loopback but the manifest
has no host permission for it.

---

## 3. Chrome Web Store listing draft

- **Name:** AWOS Replay
- **Summary / short description (≤132 chars):** Show it once, it does it forever. Records a web chore, then
  replays it with checks, an approval gate and a private local model.
- **Category:** Productivity → Workflow & Planning. If the category picker is the older one, use Productivity.
- **Language:** English
- **Single purpose (the store asks for this):** Record a repetitive task the user does on a website and
  replay it on request, verifying that it worked.

**Long description:**

> **Show it once, it does it forever.**
>
> You download the same invoice every month, fill in the same portal form every week, or click through the
> same report every morning. Do it once with AWOS Replay recording, and from then on one click replays it.
>
> **Replays that check their own work.** AWOS Replay doesn't just repeat your clicks. If a page changed, it
> finds the same button or field again by what it is (its label, role and text), not by brittle page
> coordinates. At the end it checks the result: the page you should land on, and the text you expect to
> see. Every run ends as VERIFIED, CHECK (look at this one) or FAILED, and never as a silent guess.
>
> **You approve the risky steps.** Clicks that look like paying, deleting, sending or submitting pause the
> replay until you approve them in the popup. No setting or timer skips this.
>
> **Private by default.** When AWOS Replay needs help re-finding a step, it asks a small AI model running
> **on your own Mac** (installed with one command, see awos.tirramind.com). Your pages, clicks and typing
> never leave your computer. Password and card fields are never recorded: AWOS Replay stores a placeholder
> instead. If you prefer, you can paste your own Anthropic API key and use Claude instead. That is off
> until you turn it on.
>
> **No account, no tracking.** Recordings live in your browser's local storage. There are no ads and no
> analytics.
>
> Works on any website: bank portals, SaaS dashboards, internal tools.
>
> Mac with Apple Silicon recommended for the local model. On other computers, use your own Claude key.

**Screenshots** (1280×800 or 640×400 PNG/JPEG, 1 to 5 of them):
1. The popup recording a chore, with the step list visible.
2. A replay in progress with the per-step checks.
3. The approval gate on a "Pay" button.
4. A VERIFIED result.
5. The Options page showing "Local model: 127.0.0.1".

Small promo tile: 440×280.

**Privacy practices tab:**

- **Permission justifications:** paste them from `dist/awos-replay-<version>.permissions.md`. The current
  manifest (v0.3.0) asks for:

| Kind | Permission | Justification |
|---|---|---|
| permission | `storage` | Saves the user's recorded chores (the steps they clicked and typed), the last 20 replay results and their settings in chrome.storage on this computer. Nothing is synced or uploaded. |
| permission | `tabs` | Replay drives the tab the user chose: it reads that tab's URL and load status to know when a page has finished loading, navigates it to the recorded start page, and messages the content script in it. |
| host permission | `http://127.0.0.1/*` | Talks to the AWOS model server that runs on the user's own Mac (llama.cpp on 127.0.0.1:8080). This is the default model; requests never leave the computer. |
| host permission | `http://localhost/*` | Same as 127.0.0.1: the user's own local model server, if they set it up under the name localhost. |
| content script | `http://*/*`, `https://*/*` | The recorder (captures the user's own clicks and typing while they record) and replayer (repeats them) must be present on whatever site the chore is on. It does nothing until the user starts a recording or replay in that tab. |

  Drafts for `alarms`, `downloads`, `notifications`, `scripting`, `activeTab`, `sidePanel`, `offscreen`,
  `webNavigation`, `contextMenus`, `unlimitedStorage` and `https://api.anthropic.com/*` are in
  `packaging/permission_justifications.json`. The build prints whichever ones the manifest actually requests.

- **Remote code:** No. All JavaScript ships in the package.
- **Data usage:** declare **Website content** and **User activity** (clicks and typing during a recording).
  Both are handled on the device, and are sent to Anthropic only if the user turns on Claude with their own
  key. Then certify:
  - not sold to third parties
  - not used or transferred for purposes unrelated to the single purpose
  - not used to determine creditworthiness
- **Privacy policy URL:** host section 4 at `https://awos.tirramind.com/replay/privacy` (site team).

---

## 4. Privacy policy (draft)

> **AWOS Replay privacy policy**, effective <date>
>
> AWOS Replay is a browser extension that records a task you do on a website and replays it when you ask.
> It is built so that your data stays on your computer.
>
> **What AWOS Replay stores, and where.**
>
> - **Your recordings:** the steps you clicked and typed, the page addresses, and the checks you set.
> - **The results of your last 20 replays.**
> - **Your settings.**
>
> All of this lives only in your browser's local extension storage on your computer. It is not synced to
> your Google account and is not sent to us. Password fields, card numbers and other sensitive fields are
> never recorded; AWOS Replay saves a placeholder instead of the value. Uninstalling the extension deletes
> this data.
>
> **The AI model: local by default.** When AWOS Replay needs help (re-finding a button that moved, or
> checking a result), it sends a short description of the step and the relevant part of the page to a model.
> By default that model runs on your own computer (a llama.cpp server on 127.0.0.1). The extension refuses
> to send page data to any non-local address unless you turn on the cloud option below. The local model
> installer downloads the model file from GitHub once. After that the model works offline.
>
> **Cloud model: only with your own key and your explicit opt-in.** If you paste your own Anthropic API key
> into Options and choose Claude, the same step descriptions and page excerpts go **directly from your
> browser to Anthropic** under your own account and Anthropic's terms
> (https://www.anthropic.com/legal/privacy). They never pass through AWOS servers. Your key is stored only in
> your browser's local extension storage. You can turn this off or delete the key at any time.
>
> **No analytics, no ads, no tracking.** The extension contains no analytics, advertising or tracking code,
> and does not contact AWOS servers. The separate AWOS command-line tool has an **opt-in**, anonymous usage
> count. It is off until you say yes, and it sends only an install id, version, OS, outcome and coarse
> duration/cost buckets; never page content, prompts, keys or names (see https://awos.tirramind.com/telemetry/).
> If a future version of the extension adds a count like that, it will be off until you opt in, and it will
> be listed here first.
>
> **What we never do.** We don't sell or share your data. We don't use it for advertising or
> creditworthiness. We don't use it for anything except running your replays.
>
> **Children.** AWOS Replay is not directed at children under 13.
>
> **Changes and contact.** We will post changes here and bump the effective date. Questions:
> <owner contact email>.

---

## 5. Owner checklist to publish

1. **Developer account.** Register at https://chrome.google.com/webstore/devconsole: one-time US$5 fee,
   2-step verification on the Google account, verified contact email. Consider a group publisher account
   so the listing isn't tied to a personal account.
2. **Fix the store blockers in `extension/`** (or ask the extension agent to): the name without "(MVP)", a
   description of 132 characters or fewer, and 16/48/128 px icons. Bump `version`.
3. **Build:** `bash packaging/build_extension_zip.sh --strict`. It must print no `ERROR` and no `STORE BLOCKER`.
4. **Host the privacy policy** (section 4, with the date and contact filled in) at
   `https://awos.tirramind.com/replay/privacy`. Also host the install command (section 1) on the site.
5. **Merge `claude/replay-install` to `main`** so the `curl … | bash` URL resolves. Test it once on a clean
   Mac user account: install, then `--status`, then `--uninstall`.
6. **Dashboard → New item → upload `dist/awos-replay-<version>.zip`.** Then fill in:
   - **Store listing:** section 3 text, category, 1 to 5 screenshots at 1280×800, the 128 px icon, the 440×280 promo tile.
   - **Privacy practices:** the single purpose, a justification for every permission (paste from `dist/…permissions.md`), remote code = No, the data usage boxes as in section 3, and the privacy policy URL.
   - **Distribution:** visibility (start as **Unlisted** for the first users, then Public) and regions.
7. **Notes for the reviewer** ("Test instructions" field). Suggested text:
   > The extension's AI step uses a model server on the user's own machine (127.0.0.1:8080). It is not
   > needed to test recording and replay: record any task (e.g. a search on example.com) with the popup's
   > Record button, then press Replay. Without the local server, steps that can be re-found by label still
   > replay, and steps that need the model stop with "model unreachable" instead of guessing. The broad
   > http/https content-script match is required because users record chores on sites of their choosing;
   > the script is inert until the user presses Record or Replay in that tab. No remote code; no data
   > leaves the device unless the user enters their own Anthropic API key.
8. **Submit.** Review usually takes a few days. Broad host access plus a content script on all sites often
   triggers in-depth review, so expect up to a few weeks the first time. Answer reviewer email quickly.
9. **After approval:** put the store link on the site and in the installer's "Next steps" text
   (`scripts/install_replay.sh`, `do_install`).

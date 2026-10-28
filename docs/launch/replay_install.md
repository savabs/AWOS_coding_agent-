# AWOS Replay: install, Chrome Web Store packaging, privacy policy

*Show it once, it does it forever.* AWOS Replay is a Chrome extension that records a web chore once and
replays it with checks. By default the model it asks for help runs on the user's own Mac. This page covers
three things: the one-command installer for that model, building the store zip, and the drafts the owner
needs to publish (store listing, permission justifications, privacy policy, checklist).

Files: `scripts/install_replay.sh`, `packaging/build_extension_zip.sh`,
`packaging/permission_justifications.json`, `tests/test_install_replay.py`.

---

## 1. Install the local model (Mac, Apple Silicon)

The command for the website:

```bash
curl -fsSL https://raw.githubusercontent.com/savabs/AWOS_coding_agent-/main/scripts/install_replay.sh | bash
```

(This works once the branch `claude/replay-install` is merged to `main`. Before that, put
`refs/heads/claude/replay-install` in place of `main` to test it.)

| Command | What it does |
|---|---|
| `... \| bash` | Installs, or repairs an existing install. Safe to run again. |
| `... \| bash -s -- --status` | Shows whether the agent is installed and loaded, whether the server is up, the model, its memory use, and what install added |
| `... \| bash -s -- --uninstall` | Removes exactly what install added. Add `--keep-model` to keep the 1.3 GB GGUF |
| `AWOS_DRY_RUN=1 bash install_replay.sh` | Prints every action and changes nothing |

**What install does, and nothing else:**

1. Checks that the Mac runs macOS on Apple Silicon (it refuses Intel Macs and Rosetta shells) and has about 2 GB free disk.
2. If `llama-server` is missing, runs `brew install llama.cpp`. If Homebrew is missing too, it **asks first**
   (`Install Homebrew now? [y/N]`, read from the terminal). With no terminal it answers no, prints the brew.sh
   link and stops. The script never uses `sudo` itself. Homebrew's own installer may ask for the Mac password.
3. Downloads the repair/verify model (G7, Qwen3.5 2B Q4_K_M, 1.3 GB) from the public GitHub release
   `rl-qwen3.5-2b-g7-rv` into `~/.awos/models`. It uses the same file and sha256 as `scripts/awos_model.sh`
   (a test keeps the two in sync). The download goes to `<file>.part` with curl retries and resume, and is
   renamed only when the sha256 matches, so a cut-off download never looks finished.
4. Writes the launchd **user** agent `~/Library/LaunchAgents/ai.awos.replay-model.plist` and loads it with
   `launchctl bootstrap gui/<uid>`. The agent starts at login and restarts if the server crashes
   (ThrottleInterval 30 s). It runs:
   `llama-server -m <model> --alias local --host 127.0.0.1 --port 8080 -c 4096 -np 1 --jinja --reasoning off
   --cache-prompt --cache-ram 0 -cms 256 --temp 0 --sleep-idle-seconds 300`.
   These are the EngineBench flags from `awos_model.sh` plus the idle sleep. The server listens on
   **127.0.0.1 only**.
5. Records what it added in `~/.awos/replay/installed`, one line per item: directories it created, files it
   wrote or downloaded, `brew llama.cpp` if it installed that, and Homebrew if it installed that.
   `--uninstall` reads this record. Anything that was already there before install (an existing model,
   llama.cpp, `~/.awos`) is not recorded, so uninstall leaves it alone. Directories are removed only if they
   are empty. Homebrew is never removed automatically, even when this script installed it, because other
   apps may depend on it by then; uninstall prints the official link to remove it.

The script never touches browser profiles, other apps, or anything outside `$HOME` apart from
`brew install llama.cpp`. The whole script is wrapped in `main "$@"` on its last line, so a cut-off
`curl | bash` download never runs half a script. Child processes never read the piped script from stdin.

Environment variables: `AWOS_MODEL_PORT` (default 8080; if you change it, set the same endpoint in the
extension Options), `AWOS_REPLAY_MODEL` (`g7`, `g6` or `base2b`), `AWOS_SLEEP_IDLE` (seconds, default 300;
`-1` keeps the model loaded), and `AWOS_NONINTERACTIVE=1`.

If `scripts/awos_model.sh` is already serving on 8080 in a terminal, install stops and says so instead of
starting a second server.

**After install** (the script prints these steps):
1. Install the extension from the Chrome Web Store. For a dev build: `chrome://extensions` → Developer mode →
   Load unpacked → `extension/`.
2. Extension Options → endpoint `http://127.0.0.1:8080/v1` (this is the default) → Save.
3. Record a chore once (popup → Record), then Replay it.

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

To re-measure: `bash install_replay.sh --status` shows the server's resident MB.

**Log:** `~/.awos/replay/model-server.log`. On each install it is reset if it has grown past 20 MB.

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

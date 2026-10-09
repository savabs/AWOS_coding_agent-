# T10: computer-use foundations

Written 2026-10-09. Stage 1 work (one excellent worker): verification and
safety for the computer-use goal. It adds no new product claims; identity is
in `VISION.md`. Source tricks: `docs/research/trick_book_2026-10.md` §1 #9,
§2.8, §2.9, §3 combination 5, and `docs/research/local_first_architecture_2026-10.md`
§2.6.

Code: `scaffold/agent/desktop/` (`probes.py`, `policy.py`, `ax_compress.py`,
`chores.py`). No LLM is called anywhere in this package. Nothing in it is
wired into the orchestrator yet.

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

Action classes: `read`, `write_sandbox`, `write_outside`, `network_egress`,
`send`, `delete`, `credential`, `payment`, `git_push`. The irreversible set
is egress, send, delete, credential, payment and push.

Provenance: `owner` (the goal, explicit grants), `agent` (derived from owner
input and trusted tool output), `untrusted` (screen, web, documents, email,
AX values). Each action carries its **initiator** provenance and the
provenance of each **argument**.

Decision order (first match wins; every decision is audited):

| Rule | Condition | Verdict |
|---|---|---|
| R1 | read | allow |
| R2 | write inside sandbox (realpath check; a path outside it becomes `write_outside`) | allow |
| R3 | initiator untrusted and the class is above sandbox writes | **deny** |
| R4 | payment / credential | **ask** (critical-point stop), even with a grant |
| R5 | owner grant matches (class + target glob): all args trusted → allow; any untrusted arg → ask | allow / ask |
| R6 | no grant: irreversible → deny (default deny); write outside → ask | deny / ask |

Grants can only carry `owner` provenance; building one from untrusted text
raises an error. `classify_command` maps shell commands (rung 1) to
(class, target) pairs: `git push`, curl/ssh/…, rm/trash, `security find-*`,
`osascript … send`, redirections. `decide_command` takes the worst verdict.
The policy never reads content, so it cannot be argued with. On-screen text
is untrusted by construction.

Audit record (JSONL, one per decision): `ts, verdict, rule, reason,
action_class, target, initiator, arg_sources, description, grant_id`.

## 5. AX compression (`ax_compress.py`)

Steps: drop hidden, zero-area and off-viewport nodes → drop empty
non-interactive nodes and promote their children → collapse runs of
identical siblings into one element with a repeat count → reading order
(modal sheet/dialog first, then 10 px row bands, then left to right) →
indices and centre coordinates.

Output line: `[3] button "Save" @(412,288) disabled focused MODAL x6`.
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

## 7. Risks and open items

- **AX permission.** `ax_attribute` needs Accessibility granted to the host
  process; without it, AX probes fail closed. The live AX rung is unproven
  here.
- **Self-authored chores.** C10 chores and probes were written by the same
  author as the reference solutions, so they are not an independent oracle.
  E9 needs chores written by someone else, plus human labels, before any
  claim about probe precision.
- **Policy coverage.** `classify_command` is static and best-effort. Pipes,
  `bash -c`, eval and interpreters (`python -c`) fall through to
  `write_sandbox` on the cwd. Real enforcement must sit at the tool boundary
  (the action the agent emits), not on shell parsing alone.
- **Provenance tagging is the caller's job.** The policy is only as good as
  the initiator and argument tags it is given. Wiring them into the
  orchestrator is the next step, and it needs a live proof (per
  `protocols/LIVE_PROOF_PROTOCOL.md`): an injected page attempts a send, and
  the run stops with a deny marker in the audit log.
- **"Data requires action" chores** (§3 combination 5) cannot be fixed
  programs. They need the guarded worker with this policy in front of it.
- Not done here: snapshots/rollback (APFS clones), the Shortcuts rung, and
  Flash-written probes. All are listed in the Trick Book.

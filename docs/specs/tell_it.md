# Tell it (AWOS Replay v0.5): the intelligent first run that becomes a recipe

Status: built, tested on local fixture pages with a fake brain (no real model calls yet). Code:
`extension/lib/agent.js` (contract), `extension/tellit.js` (the loop, in the service worker),
`extension/content.js` (`agent_observe` / `agent_act` / `agent_secrets`), popup "Tell it" panel, Options "Tell it".
Tests: `tests/extension/test_agent_contract.py` (JS == Python), `tests/extension/test_tell_it.py` (end to end).

## What the user does

1. Opens the site (or types a start page), types a goal in the popup, presses **Start**. Examples:
   - compare the price of the same product in two shops and report the cheaper one (answer in `done("...")`);
   - fill a job application up to, not including, Submit;
   - book the first free appointment slot, stopping at the confirm step;
   - turn off marketing e-mails in a profile / settings page;
   - add a contact or lead to a CRM-style form;
   - collect three facts (opening hours, phone, address) from different pages into one answer;
   - check a train, flight or order status;
   - unsubscribe from a newsletter (the click waits for Approve);
   - draft a marketplace listing without posting it;
   - look up a record in an internal admin table (private data: the local brain is the point).
2. Watches the steps appear (reason + action). Can **Pause**, **Stop**, **Take over** (do some steps by hand; they
   are recorded into the same recipe; **Resume** hands back), or type a correction ("no, use the Billing tab"),
   which goes into the next prompt once.
3. Risky steps wait for **Approve** (or "No, don't", which is fed back to the agent as a rejection).
   Passwords / codes / CAPTCHAs: the agent waits, the user types or solves it in the page, then **Continue**.
4. When the agent says `done`, the verifier reads the final page; the popup shows the answer, the verifier's
   opinion and proposed checks (final URL path + a distinctive text, both editable). **Yes, that's it** saves the
   run as a normal recording; **No** discards it (the episode is still kept as a negative training example).
5. From then on it is an ordinary recipe: Run, params ("Ask each run" on any typed field), schedule, share; if a
   step cannot be found, the existing repair model runs as today.

## Loop (one model call per step)

```
observe (AWOSLocator.observe: numbered controls, page text, headings, forms, dialog)
  -> mask credential-looking tokens (web_tools.mask_secrets)
  -> page guards (micro_agent.guard_page): password / code / CAPTCHA / passkey / HTTP error -> pause for the user
  -> prompt (micro_agent._prompt, open-web variant) -> brain -> normalise + grammar check, one repair call
  -> parse (micro_agent.parse_text_action) -> validate against the observation (_execute's pre-checks)
  -> safety gate -> execute with the replay actuators (content agent_act) -> record the step -> repeat
```

Caps: 25 model steps (episode.py's open-web cap), 10 minutes of agent time (time waiting for the user does not
count), 3 invalid replies in a row -> pause and ask the user (Continue resets the count; a correction can be typed).
A worker restart while a step is running ends the episode (the step may have half-run; never repeated blindly).

## Action contract = the benchmark micro-agent's

Ported to JS, pinned byte-for-byte by `test_agent_contract.py`:

| Python | JS (`AWOSAgent`) |
|---|---|
| `micro_agent.SYSTEM + SYSTEM_WEB` | `SYSTEM + SYSTEM_WEB` (identical text) |
| `micro_agent.build_grammar(n, web=True)` | `buildGrammar(n)` (sent to llama-server as `grammar`) |
| `micro_agent._prompt` (gbnf, open web, no secrets) incl. `_page_purpose`, `web_tools.outline(page, window, 40)` | `buildPrompt`, `pagePurpose`, `outline` |
| `micro_agent.parse_text_action`, `MicroAction.show` | `parseTextAction`, `show` (Python `json.dumps` escaping) |
| `harness.models._FORMAT_RULE`, `normalize_text_reply` | `FORMAT_RULE` (cloud only, like the teacher), `normalizeTextReply` |
| `rl.web.teacher.grammar_error`, `REPAIR` (one repair call) | `grammarError`, `REPAIR`, `decide` |
| `micro_agent.detect_injection`, `guard_page`, `_DESTRUCTIVE_RE`, `is_secret_field` | same names |

Verbs: `click(n) | fill(n, "text") | select(n, "option") | fill_enter(n, "text") | scroll(down|up) | go_back() |
goto("https://...") | done("answer") | needs_user("why")`, with an optional `# reason` first line.

Deviations (deliberate):
- No `copy_to_secret` / `{{secret:name}}` handles: the extension has no secret store. The grammar is built with no
  secrets (exactly what `build_grammar` does for an empty list), so they are never offered.
- No `press()` verb: the micro-agent has none. Enter is `fill_enter(n, "text")`, recorded as a fill step plus a
  `press` step (the recording format already has it).
- A correction from the user is one extra line `Note from the owner: ...` after the `Step k of N.` line, on the next
  prompt only; the trajectory step carries `note`. Taking over adds one history line
  `k. (the owner acted on the page by hand; continue from this page)`.
- A model `fill` on a password / code field is not a REJECTED step (Python): the run pauses and the user types it;
  the history line is `k. fill(...) -> SKIPPED: the owner types passwords and codes themselves; ...`.
- Page guards pause instead of ending (`NEEDS_USER` in Python); Continue skips that guard for that page.
- `needs_user("why")` pauses (the user can act or type a correction, then Continue) instead of ending.
- The invalid-reply cap counts invalid replies *in a row* (Python counts them per subtask).
- JS radio buttons are single controls (role `radiogroup`, no options); `click(n)` works on them. Clickable = any
  control that is not a textbox / password / combobox (the DOM has more clickable roles than the AX reader).
- The local model also gets the normalise + one-repair treatment (Python sends only the grammar-constrained call);
  with a grammar the first reply is almost always valid, so the repair rarely fires.

## Safety

- Approval before committing clicks: the replay vocabulary (pay, buy, send, delete, remove, transfer, ...) plus
  `AWOSSafety.TELL_RE` (submit, confirm, approve, accept, agree, place order, book, reserve, unsubscribe, publish,
  post, sign up, register, finish ...) plus the micro-agent's destructive list (revoke, reset, sign out, upgrade,
  cancel subscription ...). Checked twice: in the worker on the observed name + region, and in the page on the live
  element (its text, id, form, form action). An Enter (`fill_enter`) is gated on the field, its form and the form's
  default button. `goto` URLs are gated with `urlDanger`. Approval is nonce-bound, only from an extension page, and
  binds to the exact control name the agent saw (if the page changed, the action fails instead of clicking
  something else). No timeout or setting approves.
- Secrets: sensitive fields (`AWOSSafety.sensitiveField`: password, card, CVC, OTP, SSN, IBAN ...) are never filled
  by the agent and never read. After the user types one, the recipe gets a `{{secret:<reason>}}` placeholder step
  (replay asks the user each run). Credential-looking tokens are masked in every prompt.
- Page text is untrusted: text addressed to AI agents is flagged (`flags`, shown in the live view) and the prompt
  carries the micro-agent's warning line.
- Typed values: stored in the recipe exactly like a recording; the "Fields" UI offers each as a param.

## Brains: local first, cloud only on opt-in

- Default: a local agent model on llama-server (OpenAI-compatible, loopback only), e.g. Qwen3.5-4B Q4_K_M
  (8 GB Macs) or Qwen3.5-9B (16 GB or more). Request: `system` + `user`, `temperature 0`, `max_tokens 200`,
  the per-step `grammar`, thinking off. Nothing leaves the computer. Default endpoint
  `http://127.0.0.1:8081/v1`, model `qwen3.5-4b`: the agent server that `scripts/install_replay.sh --agent 4b|9b`
  runs as launchd agent `ai.awos.replay-agent`. The 2B repair / verify model stays on 8080.
- Start check: if the endpoint does not answer, or `/models` lists only the 2B repair / verify model (G6/G7), the
  start is refused with how to start an agent model. The cloud is never suggested.
- Claude (opt-in): Options -> Tell it -> "Claude with your own API key" + the consent box + a key. Model
  `claude-haiku-5-5` (default) or `claude-sonnet-5-5`. Request: `system` = agent system prompt + `FORMAT_RULE`,
  one user turn (plus the repair turn when needed), `max_tokens 300`, thinking disabled; straight from the browser to
  Anthropic with the user's key. What leaves the machine when on: the goal, each step's page text, control names
  and values the agent typed, the history lines. Never passwords / codes (the user types them; fields are masked).
- "Never use cloud" lock, always wins: a list of sites in Options (`noCloudSites`) and a per-recipe checkbox
  (`rec.noCloud`). Tell it refuses to start on a locked site with the Claude brain, and stops before sending a
  locked page reached mid-run. Replay repair / verify for a locked recipe or start site use the local model only.

## Training data

Each episode (confirmed, rejected, stopped or capped) is stored in `chrome.storage.local.tellTraj` (last 20) in the
micro-agent `Trajectory` format (`scaffold/rl/contract.py`): `task_id, model, steps[{i, system, user, grammar, reply,
action, valid, url, ms}], outcome, reason, events, flags, seconds, error`, plus `kind`, `goal`, `brain`,
`user_confirmed`, `verifier`, `answer`, `recording`. `system`/`user`/`grammar` are exactly what a local student sees
(the cloud format rule and repair turn are not in the rows, like the teacher's RecordingModel). Outcomes:
`verified` (the user confirmed), `completed_unverified` (the user said no), `halted` (cap / stop / error),
`needs_user`. "Export training data" downloads them as JSON (opt-in, nothing uploaded); an Options checkbox turns
keeping them off. The file contains the page text the agent saw.

## What a paid real-Claude smoke would need

A `tests/extension/cloud_smoke/` run (like the existing one) with `ANTHROPIC_API_KEY`, the unpacked extension,
Options set to Claude + consent, and 3-4 local fixture tasks (settings change, admin lookup, application up to
Submit, newsletter decline). Estimated cost with `claude-haiku-5-5` ($0.10 / $0.50 per M tokens in / out): a step is
~1.5-3k input tokens (system ~600 + page outline) and ~40 output tokens, so a 6-step run is ~15k in / 0.3k out
= about $0.002; with repairs and the verifier on Claude, < $0.005 per run. `claude-sonnet-5-5` is 20x that
(~$0.04-0.10 per run). Real sites with long pages (3k-char text + 40 controls) are ~3-4k tokens per step: a 25-step
capped run is ~100k tokens = ~$0.01 on Haiku.

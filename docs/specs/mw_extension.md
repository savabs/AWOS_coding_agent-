# AWOS Replay: Chrome extension MVP (record once, replay, approval gate)

Status: MVP, 2026-10-24, branch `claude/mw-extension`. First installable piece of the
self-serve product ("show once, replay free, a small local model repairs broken steps,
results verified, never pay/send/delete without approval").

## What it does

An unpacked Manifest V3 extension in `extension/`, plain JS with no build step.

| Part | File | Behaviour |
|------|------|-----------|
| Record | `content.js`, `background.js` | Captures clicks on clickable controls, typing into text-like fields (merged per field), `<select>` changes, and typed/bookmark navigations (a navigation within 3 s of a click is marked on that click and is not recorded as a separate step). Each target is stored as a multi-attribute locator, like Similo: tag, type, role, accessible name, text, id, name attr, placeholder, label, href, css path, classes, nearby text, form id, page position. |
| Secrets | `lib/safety.js` | The values of password fields, `autocomplete` one-time-code/cc-*, and fields whose id/name/label/placeholder looks like card/CVV/expiry/OTP/verification code/SSN/PIN/routing/account number/IBAN are **never read into a message**. The step stores `{{secret:<reason>}}` instead. On replay the run pauses (`needs_user`) so the user can type the value, then press Continue. |
| Replay | `background.js` | Runs step by step. It waits for the tab to finish loading and for the content script to be ready, then `locate`: it polls up to `findTimeoutMs` (default 4 s), scoring each visible candidate of the same kind. **found** needs score >= 0.55, a margin of >= 0.12 over the runner-up, and an *anchor*: at least one identity attribute (id / name attr / accessible name / text / label / href / placeholder) with similarity >= 0.8. Matching structure and position alone never counts as found, which stops it from clicking whatever control now sits where a deleted one was. |
| Model fallback | `lib/model.js` | When a step is not found, the top <= 5 candidates go to an OpenAI-compatible `/chat/completions` (default `http://127.0.0.1:8080/v1`, set in Options; `temperature 0`, `max_tokens 8`). The model replies with a number or `NOT_FOUND`. `NOT_FOUND`, an out-of-range number, an unparsable reply, an unreachable or timed-out model, or the model being disabled all **stop the run** (FAILED / NOT_FOUND). A message shows in the popup and a banner on the page. Endpoints that are not loopback (127.0.0.1 / localhost / [::1]) are refused in code and in Options. `host_permissions` covers only loopback. |
| Approval gate | `lib/safety.js`, `background.js`, `content.js` | Before a click, the target's name, text, aria-label, title, id, name, and enclosing form/dialog (aria-label, id, name, action) are matched against: pay, pay now, payment, buy, purchase, checkout, send, submit order, place order, confirm order/payment/purchase/transfer, delete, remove, empty, permanently, discard, clear all, transfer, wire. The check runs on the element found **at replay time**, including one the model picked, and also on the recorded step. On a match the run pauses with a fresh nonce. Only an `approve` message with that nonce, sent from an extension page, resumes it, and only for that step and that target name. The content script also refuses a dangerous click unless `approved:true` is passed (a second gate). No setting, timeout, or code path approves on its own. A worker restart in the middle of a run fails it (INTERRUPTED) rather than resuming, so a click that ran halfway is never repeated. |
| Verify | `background.js` | Stop-recording saves the final `origin+pathname` as the URL check. In the popup you can add "expect text" checks and edit the URL pattern (`*` glob allowed). After the last step: **VERIFIED** = every check passes, **UNVERIFIED** = finished with no checks, **FAILED** = a check failed or the run stopped. |
| Popup | `popup.html/js/css` | Record / Stop, the recordings list (Run, Save checks, Delete), and a live run panel: status, verdict badge, Approve / Continue / Stop prompt, checks, log. `?tab=<id>` targets a specific tab when it is opened as a page. |
| Options | `options.html/js` | Endpoint, model name, model on/off, find timeout. |

Storage: `chrome.storage.local` holds `recordings`, the last 20 `runs` and `settings`. `chrome.storage.session` holds the live `recState` and `run`, so a paused approval survives a worker restart. Nothing is sent anywhere except the optional loopback model call.

## How to run

Install: go to `chrome://extensions`, turn on Developer mode, click Load unpacked, and pick `extension/`. If you want the fallback, start a local model, e.g. `llama-server -m qwen.gguf --port 8080`.

Tests (Playwright + Chromium need the 3.12 venv):

```
PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/extension -q
```

- `tests/extension/test_extension_replay.py` (14 tests): a real extension in headless Chromium (`channel="chromium"`, `--load-extension`). A local static server serves `tests/extension/pages/`, plus a fake OpenAI-compatible model on the same loopback server. `server.overrides` swaps the page behind a URL to simulate a site change.
  - record a 3-field form fill + submit; the locators hold every attribute
  - replay unchanged: VERIFIED, all 4 actions re-found by scoring, model never called
  - replay after a label **and** id rename of the email field: VERIFIED by scoring, model never called
  - replay after the Save button was deleted: the model is asked once (shortlist has Save/Cancel context) and says NOT_FOUND, so the run is FAILED/NOT_FOUND at step 5, Cancel was not clicked, and a banner shows. The same holds when the model is unreachable, when it gives an out-of-range answer, and when the endpoint is not loopback (refused).
  - Save relabelled "Update" with a new id: scoring will not claim it (no anchor), the model picks it, VERIFIED
  - "Pay now": pauses, stays paused for 2 s, page shows "Not paid", the step before the gate did run; a click on **Approve in the popup page** resumes, giving "Payment sent", VERIFIED. Clicking Stop gives REJECTED and the button is never clicked. A wrong nonce is refused, and `fromExtensionPage` rejects web-page senders.
  - "Pay now" renamed "Complete payment": the model re-finds it **and the gate still pauses**
  - secrets: the typed password/OTP/card/SSN values are absent from all of storage; placeholders are stored; replay pauses `needs_user` at the password with nothing typed
  - popup lists the recording and its Run button works
- `tests/extension/test_locator_unit.py` (6 tests): the content-script libs are injected into plain pages, with no extension, so this also works where extensions cannot load. It covers the danger vocabulary (15 positive, 6 negative such as Save/Submit/Next/"Sender settings"), danger from form context, sensitive-field detection, rename re-find, deleted-target-never-found-by-position, the model answer parser and the loopback check.

## Measured

- `tests/extension`: **20 passed** (4 consecutive runs, 40-61 s each, headless Chromium 1248 / Playwright 1.64 on macOS).
- `tests/rl` unchanged: 179 passed, 7 skipped, 9 deselected (with the rl-venv).
- No paid API, CI or Kaggle was used. The model in the tests is a fake that returns fixed answers. **No real local model was exercised**, so the quality of a real Qwen/llama pick from the shortlist is unmeasured.

## Limits / not done

- Main frame only. There is no iframe or shadow-DOM traversal, and no cross-origin frames.
- Recorded actions are click / fill / select / goto. There is no keyboard (Enter-to-submit records as the resulting navigation), drag, hover, file upload, scroll, or multi-tab.
- A replay runs in the tab you pick, and only one replay runs at a time. Background tabs throttle timers, so replay there is slower (~1 s per poll).
- Only clicks are gated. Danger is decided by vocabulary only: a dangerous control with an innocuous label ("Confirm" outside a form whose action says order/pay) is not caught. The list is English-only.
- The sensitive-field heuristic is conservative but regex-based. A field with no telling id/name/label/type would still be recorded. Placeholder values pause for the user; there is no vault.
- Verify is URL pattern + text present. There is no DOM/state assertion, and checks are not inferred automatically except the final URL.
- The scoring thresholds (0.55 / 0.12 / anchor 0.8) are tuned only on these test pages. There is no evaluation on real sites yet.
- The popup was driven only as a page in tests (`popup.html?tab=`). The toolbar-popup path is untested.
- Exam integrity: this item produces no training data and touches no factory/MiniWoB split, so no exam-split test applies.

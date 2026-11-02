# Goal-guided runs (AWOS Replay 0.7)

Status: built (extension 0.7.0). Code: `extension/lib/period.js` (detector, templates, views), `extension/guided.js`
(save / import templating, the model-decided step, the download expectation, the update offer), `extension/background.js`
(driver integration, verdicts, collections), `extension/lib/recipe.js` (placeholders and `period` in shared recipes),
`extension/content.js` (`variable` steps), `extension/lib/agent.js` (`hint` line), `extension/popup.js` (templating view,
undo, update offer). Tests: `tests/extension/test_goal_guided.py`; fixture portal
`tests/extension/pages/portal/invoices.html` (which months are posted and the page layout come from the test server).

## Why

Replay repeats recorded clicks. A monthly portal recipe recorded in October clicks "Invoice September 2026" again in
November and downloads the old document, or, with 0.6's download check, reports "Missing" after downloading the wrong
file. The product is meant to work from a goal: look at the page on each run, decide by meaning, and remember what
worked. 0.7 does that in three layers, cheapest first:

1. **Period templating** (no model): the recipe's month-dependent text follows the run's month.
2. **Goal-guided step decision** (model as fallback): a period-dependent or row-dependent step that does not match
   confidently is decided by the Tell-it agent brain from the goal, not from the old target.
3. **Result against the goal**: the downloaded file must be this period's; another period's file is **Wrong period**.

Plus a light **skills memory**: when the model had to decide and the run verified, the recipe can adopt the new step.

## 1. Period templating

**Detector** (`AWOSPeriod.detect(text, period, kind)`): finds period expressions and replaces those that name the
recording's period with placeholders. Recognised:

| expression | example | template |
|---|---|---|
| full / short month name + year (any separator, optional dot) | `Invoice September 2026`, `Sept. 2026`, `Oct-2026`, `inv-sep-2026.pdf` | `Invoice {{month_name}} {{year}}`, `{{month_short}}. {{year}}`, `{{month_short}}-{{year}}`, `inv-{{month_short_lower}}-{{year}}.pdf` |
| year + month name | `2026 September report` | `{{year}} {{month_name}} report` |
| YYYY-MM, YYYY/MM, YYYY_MM, YYYY.MM | `/files/invoice-2026-09.pdf`, `?period=2026-09`, `/2026/09/` | `/files/invoice-{{year}}-{{month}}.pdf` ... |
| MM/YYYY, MM-YYYY, MM.YYYY (also one-digit months) | `09/2026`, `statement-09-2026.pdf`, `9/2026` | `{{month}}/{{year}}`, `{{month_n}}/{{year}}` |
| quarter + year | `Q3 2026`, `vat-2026-Q3.pdf` | `Q{{quarter}} {{year}}` |
| bare capitalised month name | `Download September payouts` | `Download {{month_name}} payouts` |

Placeholders: `{{year}}` (2026), `{{month}}` (09), `{{month_n}}` (9), `{{month_name}}` (September), `{{month_short}}`
(Sep), `{{quarter}}` (3, after a literal `Q`), and `_lower` / `_upper` variants of the two name tokens so `SEPTEMBER`
and `inv-sep-2026` keep their case.

Not periods (unit-tested): order ids (`Order 2026-0912`, `INV-2026-09-0042`, `#20260915`), prices (`EUR 1,209.20`,
`$12.2026`), phone numbers (`+1 555-09-2026`, `(555) 409-2026`), dates with a day (`2026-09-30`, `15/09/2026`,
`30.09.2026`), plain years (`Annual report 2026`), versions (`v1.2026`), and the ordinary words May and March without a
year. An expression for **another** month than the recording's stays literal: a recipe that downloads the March,
April and February statements on purpose is not moved.

**Which fields** (`AWOSPeriod.fields`): the goal; per step its URL (path + query only, never the host), the locator's
`name`, `text`, `label`, `title`, `href`, `placeholder`, `neighbor`, `block`, `context`, `id`, a typed value (not a
param, not a secret, not "ask"), a select's value and option text, the recorded download name; the expected texts,
the final-URL pattern and the download name pattern.

**The recording's period**: at save, the one period named most often among the last three months (counting every
field), else the previous month of the recording date. Its default **run mode** follows: `previous` (a recording of
last month's document) unless the named period is the current month (`this`). The popup can change the mode
(`set_period`).

**Storage** (`rec.tpl`): `{path: {template, example, period, on}}`, e.g.
`"steps.1.loc.name": {"template": "Invoice {{month_name}} {{year}}", "example": "Invoice September 2026",
"period": "2026-09", "on": true}`. The recording's fields keep the original text; the template is an overlay. A run
replays `AWOSPeriod.view(rec, runPeriod)`, a copy with every `on` template substituted. An entry only applies while its
field still holds its example, so an edited field is never overwritten by a stale template; a re-scan (any save) keeps
an entry and its `on` flag while the field is unchanged. **Undo** per field in the popup sets `on: false`
(`set_template`); the field is then replayed exactly as recorded.

**When**: `stop_recording`, `save_recording` (a new goal / expected text / download pattern is templated against the
recorded period), `import_confirm` (placeholders in a shared recipe become templates over the recorded period's text,
and literal expressions in an older recipe are templated too), and after an accepted update offer (section 4).

**The run's period**: the collection's period; else a `period` given to the run (`run` message, the popup's
"Run for this month"); else the recipe's mode applied to today (previous month by default). Logged at the start:
"period October 2026: 4 templated fields (recorded for September 2026) replayed with October 2026 (previous month)".

**Proposed goal**: a recording without a goal that downloads something gets `goalProposed`, e.g.
"Download the {{month_name}} {{year}} invoice from portal.example" (shown in the goal field; used by step decisions).

## 2. Goal-guided step decision

A step is **eligible** when its target depends on the period (it has templated fields) or it is **variable**: the
recorder marks a click whose control sits in a repeated list / table row (a `tr` / `li` / `article` / `role=row`
ancestor within 6 levels whose parent has at least 3 such children). Which row is meant can change from run to run.

An eligible step is decided by the model when, after substitution:
- the locator is not confident (`ambiguous` / `not_found`, the existing ACCEPT / MARGIN / anchor thresholds), or
- it found a control that does not name the run's period in the templated identity fields (`AWOSPeriod.consistent`):
  a link "Invoice October 2026" whose file is `/files/invoice-2026-09.pdf`, or the old "Invoice September 2026" row
  that still scores well next to the missing November one.

Non-eligible steps keep 0.6's path (shortlist repair by the 2B model). An eligible step never goes through the
shortlist repair: that model picks the most similar candidate, which is exactly the old month's row.

**The call** (`guidedDecide`): the Tell-it agent brain and contract (`tellSettings`, `tellChat`, `lib/agent.js`):
`AWOSAgent.SYSTEM + SYSTEM_WEB`, `buildPrompt` with
- `Goal:` the recipe's goal (or the proposed one) with the period substituted, e.g. "Download the October 2026 supplier
  invoice";
- a hint line after the step line (the only deviation from the micro-agent prompt, `p.hint`): "This is a saved recipe
  replayed for October 2026 (it was recorded for September 2026): give ONE action for this step only ... If the page
  does not have what the goal needs for October 2026 (for example it is not posted yet), reply needs_user("why").
  Last time at this step you clicked the link "Invoice September 2026".";
- "Your previous actions and results": the steps this run already did ("1. opened .../invoices -> ok");
- the observation (masked like Tell it, injection warning included), and the grammar for its controls.

One call (+ the contract's one repair call). The reply is parsed and validated (`AWOSAgent.validate`) against a fresh
observation; only `click`, `fill`, `fill_enter`, `select` on the current page are accepted. `needs_user(...)` or
`done(...)` ends the recipe as **MISSING** with the model's reason ("No November 2026 invoice is listed yet"): the
recorded, old target is never used instead, nothing is downloaded. An unusable reply, a model error or a missing local
model ends the run as NOT_FOUND (in a collection: Missing, with the expected file name in the reason).

**Safety**: the Tell-it gate (strict vocabulary + the micro-agent's destructive list) before anything is sent, and the
content script's own gate in `agent_act` (approval-gated clicks, sensitive fields never filled, the control must still
carry the name the model saw). A gated choice pauses (`awaiting_approval`, `pending.guided`); Approve executes exactly
that decision (re-located by name and role on a fresh observation) without asking the model again. A password / code
field is never typed into.

**Privacy**: local loopback agent by default. Claude only when the user chose the Claude agent brain, ticked consent
and saved a key; a recipe or site locked off the cloud always gets the local model ("the step decision uses the local
agent model (cloud is locked off for this recipe)"). Missing consent never escalates: the local model decides.

**Cap and log**: at most 5 model decisions per run (`GUIDED_CAP`); each is logged with why it was needed and the
model's reason, kept in `run.guided` (step, action, control role + name, the control's locator, reason) and
`run.modelCalls` (`kind: 'guided'`), and exported (redacted) by **Export run log**.

## 3. The result checked against the goal

The download expectation (`downloadExpectation`): the recipe's download name pattern, else (no pattern set) the
recorded download name when it was templated (`^invoice-{{year}}-{{month}}(?: \(\d+\))?\.pdf$`). Substituted for the
run's period it must match a download of this run. The same pattern with every period placeholder widened to "any
period" finds a file **this run** downloaded for another period: verdict **WRONG_PERIOD** ("downloaded
invoice-2026-09.pdf, which is for September 2026, not October 2026"), collection status `wrong_period` ("Wrong period
(invoice-2026-09.pdf)" in the checklist, "1 wrong period" in the summary), never Got. Expected texts and the final-URL
pattern are templated like any field. Nothing reads the file's content.

Verdicts: VERIFIED, CHECK, UNVERIFIED, FAILED as before, plus MISSING (section 2) and WRONG_PERIOD.

## 4. Skills memory

After a **VERIFIED** run in which the model decided a step of the same kind (click for a click), `run.updateOffer`
asks in the popup: "The portal changed. Step 2: the recipe clicked the link "Invoice September 2026"; this run the
model chose the button "Oct-2026 Invoice". The run was VERIFIED. Use the new step from now on?"
- **Use the new step** (`guided_update`, accept): the step's locator becomes the model's control, `step.hint` describes
  it, and it is templated again for the run's period (`{{month_short}}-{{year}} Invoice`), so the next month's run
  matches without a model call.
- **Keep the recorded step**: nothing changes; the next run decides again.
Either way `rec.history` (last 20) records when, which period, which step, from what to what, and the model's reason;
the popup lists the last five under the recipe's **Period** section.

## 5. Catch up on missed months (0.9)

Some portals keep only the last few months: a missed cycle is lost. For a recipe in a collection, after a run got the
current period's file (verdict VERIFIED / CHECK, download check passed, content checked as in `portal_run.md` 7), AWOS
looks on the same listing page for earlier periods within the collection's lookback (`catchUpMonths`, default 3,
0-12; `catchUp: false` turns it off; popup: collection -> **Missed months**) that are not **Got** in the collection's
history, oldest first:
- the period-templated download step (the last download step with templated fields) is re-run with that period
  substituted (`AWOSPeriod.view`); the step's URL too, when it is templated (`?period=2026-08`);
- the target must be found without a model: of the locator's shortlist (row position, neighbours and css ignored,
  since they point at the recorded month's row), exactly ONE control must name that period and no other
  (`AWOSPeriod.consistent`); none = "not listed on this page", two = not unambiguous; no model call, no approval: a
  control matching the danger vocabulary is skipped;
- the file must arrive (a new download matching that period's name pattern, <= 20 s) and pass the file checks; it is
  recorded under its own period as **Got (caught up)** (`collResults[col][period][rec] = {status: 'got', caughtUp:
  true, file, content, fromPeriod, runId}`), or **Not a document**;
- a period already Got is never downloaded again (logged "already got").
The run log lists each period; the collection summary and notification add "caught up August 2026"; the phone status
detail names the caught-up months. A recipe whose download opens a new tab is not caught up yet (logged).

## Recipe format

See `recipe_v1.md`: built-in period placeholders need no params entry and may appear in loc strings, step URLs,
`checks.*`, and as a whole fill value; `period: {recorded, mode}`; `variable` on click / press steps. A param the recipe
declares with a built-in's name (`{{month}}`) stays the user's value.

## Permissions

None added. The step decision uses the agent endpoint (loopback host permission) or the Claude call that Tell it
already makes; downloads are checked with `downloads`.

## Examples

- **Supplier invoices** (invoice list, one row per month): "Invoice September 2026" recorded in October -> November's
  run downloads "Invoice October 2026" by templating, no model. After a redesign to "Oct-2026 Invoice" buttons the
  model decides once; the update offer makes the following months fast again.
- **Payout CSV** (`payouts_2026_09.csv` on a "Download September payouts" link): the link text, the file name and the
  download pattern follow the month; a portal that still serves September's CSV in November is Wrong period.
- **Bank-style statement** (`Statement 09/2026`, `statement-09-2026.pdf`): the MM/YYYY form is templated; an older
  statement clicked on purpose ("Statement March 2026") stays literal.
- **Quarterly VAT return** (`Q3 2026 VAT return`): `Q{{quarter}} {{year}}` (a monthly run moves it once a quarter).

## Known limits

- Only periods relative to the recording's month are templated; a recipe that needs "two months ago" next to "last
  month" in one run is not expressed (one period per run). Dates with a day (`2026-09-30`) are never templated, so a
  "period ending 30 September" text is not followed.
- `?month=9&year=2026` style split query parameters and numeric-only month fields (a select with values 1-12 and no
  month names) are not detected.
- The consistency check only reads the found control's own name / text / href / label / id; a row whose period is only
  in a neighbouring cell (link text "PDF") is decided by the locator alone unless the step is variable and not
  confident.
- WRONG_PERIOD needs the period in the downloaded file's name (or URL); a portal that names every file `document.pdf`
  cannot be checked this way.
- The model decides one step at a time and only actions on the current page (no goto / scroll / multi-step detours);
  a redesign that adds a new page between steps still fails, with the reason in the log.
- The fixtures exercise a scripted (fake) agent brain; the quality of real local models on real portals is not
  measured here.
- Catch-up (0.9) only sees earlier periods listed on the page the current one was downloaded from (no paging, no
  "older" link, no period picker); a list that names months only in a neighbouring cell ("PDF" links) is not caught up.

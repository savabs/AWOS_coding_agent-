# AWOS Replay recipe format v1 (source of truth)

A **recipe** is a recorded web chore that people can share: what to open, which controls to use, what must be true
at the end. It is plain JSON. The extension (`extension/lib/recipe.js`) writes and validates it; the website
(`site/`, recipe page `/r/`) displays it and hands it to the extension. Both code against this file.

Status: v1, shipped with AWOS Replay 0.4. Validator: `AWOSRecipe.validate()` in `extension/lib/recipe.js`, tests in
`tests/extension/test_recipe_v04.py`.

## 1. Shape

```json
{
  "awos_recipe": 1,
  "name": "Download last month's statement",
  "description": "Opens the statements page and downloads the PDF for the month you pick.",
  "start_url": "https://bank.example.com/statements",
  "params": [
    {"name": "month", "label": "Which month", "example": "October 2026", "kind": "text"}
  ],
  "steps": [
    {"action": "goto", "url": "https://bank.example.com/statements"},
    {"action": "fill", "loc": {"role": "textbox", "name": "Username"}, "ask": true},
    {"action": "fill", "loc": {"role": "password", "name": "Password"}, "ask": true, "sensitive": "password"},
    {"action": "select", "loc": {"role": "combobox", "name": "Month"}, "value": "{{month}}"},
    {"action": "click", "loc": {"role": "button", "name": "Download PDF"}, "url": "https://bank.example.com/statements"}
  ],
  "checks": {
    "goal": "Statement for {{month}} downloaded",
    "expect_text": ["Download started"],
    "expect_url": "https://bank.example.com/statements",
    "expect_download": {"name_regex": "statement.*\\.pdf$"}
  },
  "created_with": "awos-replay 0.4"
}
```

### Top level

| key | type | rule |
|---|---|---|
| `awos_recipe` | number | must be `1` |
| `name` | string | 1-200 chars |
| `description` | string | 0-2000 chars (may be `""`) |
| `start_url` | string | `http:` or `https:` URL, **no query string, no fragment** (origin + path only) |
| `params` | array | 0-20 items, see below; names unique |
| `steps` | array | 1-200 items, see below |
| `checks` | object | see below |
| `created_with` | string | 0-60 chars, e.g. `"awos-replay 0.4"` |

Unknown top-level keys are dropped by the validator (not an error).

### `params[]`

| key | type | rule |
|---|---|---|
| `name` | string | `^[a-z][a-z0-9_]{0,31}$` |
| `label` | string | 1-100 chars; what the user is asked ("Which month") |
| `example` | string | 0-200 chars; shown as a placeholder / first default |
| `kind` | string | `"text"`, `"number"` or `"date"` (UI hint only) |

A param is referenced as the exact string `"{{name}}"` in a step's `value`, and may appear inside
`checks.goal`, `checks.expect_text[]` and `checks.expect_url` (substituted at run time). Every `{{name}}` used must be
declared in `params`. Params that no step uses are allowed (warning only).

### `steps[]`

Every step has `action` (one of `goto`, `click`, `fill`, `select`). Non-goto steps have `loc` and may have `url`
(the page the step was recorded on).

| action | fields |
|---|---|
| `goto` | `url` (http/https, no query, no fragment) |
| `click` | `loc`, optional `url`, optional `navigates` (bool) |
| `fill` | `loc`, optional `url`, and **exactly one of** `value: "{{param}}"` or `ask: true`; optional `sensitive` (string reason, e.g. `"password"`, `"one-time-code"`) only with `ask` |
| `select` | `loc`, optional `url`, `value` (string, may be `"{{param}}"`), optional `optionText` (string) |

`fill` steps never carry a literal value. `ask: true` means: at run time AWOS pauses and the user types that field
themselves. Any field with a `sensitive` reason (password, OTP, card, SSN, ...) is always `ask`.

`loc` describes the control (the v0.3 locator fingerprint). All keys are optional; unknown keys are dropped:

| key | type |
|---|---|
| `tag`, `type`, `role`, `name`, `context`, `block`, `text`, `id`, `nameAttr`, `placeholder`, `label`, `href`, `css`, `classes`, `neighbor`, `title`, `formId` | string, max 400 chars |
| `siblings` | array of up to 12 strings (max 400 chars each) |
| `pos` | `{x, y, w, h}` numbers |

Step URLs (`goto.url`, `step.url`) and `loc.href` never carry query strings or fragments.

### `checks`

| key | type | rule |
|---|---|---|
| `goal` | string | 0-300 chars; what must be true at the end (the local verifier reads it) |
| `expect_text` | array of string | 0-10 items, 1-200 chars each; all must appear on the final page |
| `expect_url` | string or null | origin + path, `*` wildcard allowed, no `?` or `#` |
| `expect_download` | object or null | `{"name_regex": "<JS regex source, 1-200 chars>"}`: a download whose file name matches finished during the run |

## 2. Limits

- The UTF-8 encoding of `JSON.stringify(recipe)` is at most **65,536 bytes (64 KB)**. Larger recipes are rejected
  on export and on import.
- The validator returns `{ok: true, recipe}` with the normalised recipe, or `{ok: false, errors: [string, ...]}`.

## 3. Privacy rules for export (what a shared recipe never contains)

1. **No typed values.** A recorded `fill` step becomes `value: "{{param}}"` only if the user explicitly turned that
   field into a param (giving name / label / example). Every other `fill` becomes `ask: true` with no value.
2. **Secrets are always `ask`.** Password, OTP, card, CVC, SSN, PIN, bank-number and other secret-looking fields
   (`AWOSSafety.sensitiveField`) are `ask: true` even if the user tried to make them a param.
3. **Typed values are scrubbed everywhere else too.** If a typed value (3+ chars) appears in another field (a
   locator's nearby text, an expected text, the goal), it is replaced by `{{param}}` when that field is a param,
   otherwise removed (locator text), replaced by `[typed value]` (goal), or the expected text is dropped.
4. **No URL queries or fragments** in `start_url`, step URLs, `loc.href` or `expect_url`.
5. Not exported: run history, last-used param values, schedules, API keys, local settings.

## 4. Share link

```
https://awos-site.pages.dev/r/#<base64url(deflate-raw(UTF-8 JSON))>
```

- `deflate-raw` = raw DEFLATE (RFC 1951, no zlib/gzip header), as `CompressionStream('deflate-raw')`.
- `base64url` = RFC 4648 section 5 alphabet (`-` and `_`), **no `=` padding**.
- The recipe lives in the **fragment**, so it is never sent to the server.
- Decoders must stop inflating past 64 KB (zip-bomb guard) and validate the result.
- Also available as a file: `<name>.awos.json` containing the same JSON (pretty-printed is fine).

Reference decode in a page:

```js
const b64 = location.hash.slice(1).replace(/-/g, '+').replace(/_/g, '/');
const bytes = Uint8Array.from(atob(b64 + '='.repeat((4 - b64.length % 4) % 4)), c => c.charCodeAt(0));
const json = await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'))).text();
const recipe = JSON.parse(json);
```

## 5. Page -> extension handshake (recipe page `/r/`)

The extension injects a small content script (`extension/recipe_bridge.js`) only on
`https://awos-site.pages.dev/r/*` and `http://localhost:8000/r/*` (dev).

1. **Is the extension installed?** The page posts `window.postMessage({type: "awos-ping"}, location.origin)`.
   The extension answers `{type: "awos-pong", version: "0.4.0"}` via `window.postMessage(..., location.origin)`.
   No answer within ~1 s = not installed (show the install instructions).
2. **Import.** The page posts
   `window.postMessage({type: "awos-import-recipe", recipe: <recipe object>}, location.origin)`.
   Only messages from the page's own window and origin are accepted.
3. The extension validates it and answers
   `{type: "awos-import-status", ok: true, pending: true, name: "<recipe name>"}` or
   `{type: "awos-import-status", ok: false, error: "<reason>"}`.
   `pending: true` means: **nothing is imported yet.** The user must open the AWOS popup, which shows the full step
   list and start URL, and click **Import**. Suggested page text: "Open the AWOS Replay popup to review and confirm."
   The page never learns whether the user confirmed.

## 6. Trust rules for imported recipes (enforced by the extension)

- An imported recipe is untrusted. It cannot skip, pre-approve or weaken approval gates: danger is recomputed from
  the live page, and imported recipes also gate a stricter vocabulary (submit, confirm, approve, order, ...).
- Every navigation and every action must stay on the registrable domain of `start_url` (for example
  `bank.example.com` -> `example.com`). Leaving it pauses the run and asks the user to approve that domain; an
  approved domain is remembered for that recipe only.
- `ask` steps and secret fields always pause for the user to type.
- Pay / send / delete / submit-like clicks always pause for an Approve click in the popup, also on scheduled runs.

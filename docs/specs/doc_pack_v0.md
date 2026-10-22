# Month-end Document Pack — v0.1 core spec (2026-10-22)

Context: `docs/research/mac_market_2026-10-22/VERDICT.md` (wedge product) and
`docs/research/india_2026-10-22/documents_brief.md` (what an Indian CA wants each month).

## What it does

Each month, for every source the owner has set up (SaaS/vendor invoices, bank and card
statements, payment-gateway reports, tax documents):

1. replay the owner's taught skill in **their own logged-in browser session inside the
   sandbox VM** (zero model calls on a healthy run);
2. pick up the new downloads, file them as `Pack/<YYYY-MM>/<source-id>/<name>`;
3. check each document (`verify.check`);
4. write a completeness report (what is in, what is missing, what the owner must do) and
   a zip for the accountant.

The owner logs in, enters OTPs and solves captchas **themselves**. AWOS stops at those
walls (`needs_you`) and never stores or types a password it was not given by the
keychain broker, never solves a captcha. Credentials never leave the Mac.

## Package `scaffold/agent/desktop/pack/`

| File | Owner | Role |
|---|---|---|
| `__init__.py` | core | plug-in contract: `Verdict`, `Period`, `Checkpoint`, `NeedsYou`, `RunnerResult`, `RunContext` |
| `config.py` | core | `~/.awos/pack/sources.toml` → `PackConfig` / `Source`, validation |
| `files.py` | core | downloads watcher, sha256 dedupe, filing, JSONL ledger |
| `run.py` | core | month orchestration, state, resume, lock |
| `report.py` | core | report (HTML/text/JSON) + accountant zip |
| `__main__.py` | core | CLI |
| `verify.py`, `pdftext.py` | verify agent | `check(doc_path, source, period) -> Verdict` |
| `policy.py` | policy agent | `for_source(source) -> allowed(url, action)` (or module `allowed(url, action)`) |
| `../live/checkpoint.py` | policy agent | detects login/OTP/captcha screens → `ctx.checkpoint(Checkpoint(...))` |

## sources.toml

```toml
[pack]
root      = "~/.awos/pack/Pack"
downloads = "~/Downloads"      # the VM's browser download folder
settle_s  = 2.0                # a download must keep its size this long
wait_s    = 120                # give partial downloads this long to finish

[[source]]
id              = "hdfc-current"
name            = "HDFC current account"
kind            = "statement"            # invoice | statement | gateway_report | tax_doc
start_url       = "https://netbanking.hdfcbank.com/netbanking/"
allowed_domains = ["netbanking.hdfcbank.com"]   # exact hosts; "*.example.com" = subdomains
cadence         = "monthly"              # monthly | quarterly (due in Mar/Jun/Sep/Dec)
expected        = { count = 1, types = ["pdf"] }
naming          = "{source}_{period}{n}.{ext}"
password_hint   = "hdfc_statement_pdf"   # keychain ITEM NAME only
skill           = "hdfc_statement_download"
notes           = "PDF locked with customer id"
enabled         = true
```

Validation (all problems reported together, `ConfigError.problems`): unknown keys; id
slug + unique; kind/cadence enums; `start_url` https (http only for 127.0.0.1/localhost),
no `user:pass@`, host inside `allowed_domains`; domains bare hosts, no over-broad
wildcards (`*.com`, `*.co.in`); `expected.count` 1..50, types are extensions; naming
template fields ∈ `{source kind period yyyy mm name ext n sha8}`, must contain `{ext}`,
no `/` or `..`; any key that looks like a secret (`password`, `otp`, `token`, …) is
refused; `password_hint` must be a keychain item name. Missing `skill` is a warning.

Template `{n}` is empty for a single expected file, else `_1`, `_2`, …

## Files and ledger

* `snapshot(downloads)` before the skill runs; afterwards only new or changed entries
  count. Partials (`.crdownload`, `.download` bundles, `.part`, `.partial`, `.opdownload`,
  `.tmp`) and hidden files are ignored; collection waits until no partial is left and
  every new file held its size/mtime for `settle_s`, up to `wait_s` (then: `pending`).
* Identity = sha256. A sha already in the ledger is never filed again: same
  (month, source) → harmless re-download; another month → reported ("the portal gave a
  document already filed for 2026-09/acme — probably the new one is not out yet").
* Filing **moves** the file; never overwrites (`x (2).pdf`, race-safe via `link`/`O_EXCL`).
  Duplicates are left in Downloads — the pack never deletes the owner's files.
* Files the owner drops into `Pack/<M>/<id>/` by hand are adopted (ledger event `adopted`).
* `Pack/ledger.jsonl`: append-only, fsynced, `{ts, event: filed|adopted|duplicate, month,
  source, sha256, size, original, path}` (path relative to the pack root).

## Run, statuses, resume

`run_month(cfg, month, runner=, check=, only=, force=, policy_factory=)` per enabled source:

| status | meaning |
|---|---|
| `collected_verified` | all expected files present, every one `VERIFIED` |
| `collected_unverified` | all present, at least one `UNVERIFIED` |
| `missing` | fewer good files than expected (reason: none appeared / old duplicate / partial never finished / set aside / no skill) |
| `needs_you` | stopped at a login / OTP / captcha / 2FA / consent wall |
| `failed` | runner crashed or halted, policy refused the start URL, internal error |
| `not_due` | quarterly source outside a quarter-end month |

Files with verdict `MISMATCH`/`SUSPICIOUS` or an unexpected type are kept on disk but
**set aside**: they do not count toward `expected` and never go in the zip.

State lives in `Pack/<M>/.pack_state.json` (atomic writes). Re-running skips
`collected_*` (unless `--force`) and retries `missing`, `failed`, `needs_you`. Before the
runner starts the entry is saved as `running` with the Downloads snapshot, so after a
crash/power cut the next run first collects what finished downloading and only re-runs
the skill if that is not enough. A per-month lock (`.pack.lock`, pid; stale locks are
taken over) prevents concurrent runs. A filed document that disappears from disk is
dropped from the state so the source reopens. One broken source never stops the month.

## Interfaces for the other pieces

```python
from scaffold.agent.desktop.pack import (Verdict, VERIFIED, UNVERIFIED, MISMATCH, SUSPICIOUS,
    Period, Checkpoint, NeedsYou, RunnerResult, RunContext)

# verify.py
def check(doc_path: Path, source: Source, period: Period) -> Verdict: ...
#   Period(label="2026-10", start=date, end=date, cadence="monthly"|"quarterly")
#   Verdict(status, reasons: list[str], facts: dict); a dict with the same keys is accepted.
#   Exceptions are caught -> UNVERIFIED("verifier error: ..."). Auto-loaded if pack/verify.py exists.

# policy.py  (auto-loaded if present; ANDed with the domain policy when module-level)
def for_source(source: Source) -> Callable[[str, str], bool]: ...   # preferred
def allowed(url: str, action: str) -> bool: ...                     # alternative
#   actions the runner should ask about: "navigate", "click", "type", "download".
#   The default domain policy already refuses non-allowed hosts and pay/purchase/transfer/delete.

# runner (the live skill runner; tests use fakes)
def runner(source: Source, period: Period, ctx: RunContext) -> RunnerResult | None: ...
#   ctx.downloads: Path        where the browser saves
#   ctx.allowed(url, action)   ask before every navigation / download
#   ctx.checkpoint(cp)         call on a login/OTP/captcha wall -> raises NeedsYou (stop there)
#   ctx.password_item          keychain item NAME for a locked PDF (resolve via secrets broker)
#   Return RunnerResult("ok"|"needs_you"|"failed", reason, checkpoint, detail) or None (= ok);
#   raising NeedsYou(Checkpoint(kind, message, url)) is equivalent to needs_you.
#   Checkpoint kinds: login, otp, captcha, 2fa_app, consent, unknown.
```

Downloads are collected after every runner outcome (a statement may land before the OTP
wall of the next step).

## Report and zip

`report.html` (offline, inline CSS, CSP `default-src 'none'`, no scripts, all text escaped,
light/dark), `report.txt`, `report.json` in `Pack/<M>/`. Grid: source × status, reason,
files with verdicts, "what you need to do" (e.g. "Open HDFC … and enter the OTP sent to
your phone/email, then re-run: python -m scaffold.agent.desktop.pack run --month 2026-10
--source hdfc-current"), totals, complete yes/no, and the set-aside list.

`Pack/<M>_document_pack.zip`: the three report files + every filed document that is not
set aside, still on disk, and still matching its ledger sha256. Never included: run state,
ledger, lock, password hints (locked PDFs only say "the password is not in this pack").
Reasons and checkpoint messages pass `run.scrub` (masks `password=…`, `otp: …`, URL
query strings) before they are stored.

## CLI

```
python -m scaffold.agent.desktop.pack [--sources F] [--root D] [--downloads D] \
    {sources-check | run [--month M] [--source ID] [--force] [--runner mod:fn] |
     report [--month M] | zip [--month M] [--out F] | status [--month M]}
```

`--month` defaults to last month. Exit codes: 0 ok, 1 config problem, 2 usage, 3 month locked.

## Not in v0.1

Wiring the live macOS skill runner as the default `--runner`; scheduler job kind
`doc_pack` + digest line; GSTR-2B/RCM self-invoice logic; FIRA matching; locked-PDF
opening; balance carry-over checks across months (verify agent); notifications.

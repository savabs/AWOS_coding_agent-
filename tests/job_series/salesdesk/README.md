# Job series: salesdesk

Twelve related jobs on one project, done in order, to measure whether an agent
gets better at a project it has worked on before (spec:
`docs/specs/compounding_proof_spec.md`, piece B). Second series, same format as
`../ordertool/`.

## Layout

```
base/                   salesdesk (tests/long_tasks/timezone_report_bug/project) with that
                        task's reference applied, plus tests/test_cli.py (a Berlin merchant
                        end to end, CSV bytes, the error-line convention) and a README
jobs/NN_<slug>/
  task.json             {"id", "goal", "max_turns": 40, "max_cost_usd": 0.3, "timeout_min": 15}
  hidden_tests/         test_hidden_jNN_*.py, self-contained (no conftest; each file puts
                        the project root on sys.path), so they work copied into either
                        <project>/hidden_tests/ or <project>/tests/
  reference/            full replacement files, paths relative to the project root
```

Job N starts from `base/` + `reference/` of jobs 1..N-1. Visible tests:
`python -m pytest` in the project root (`pytest.ini` has `testpaths = tests`).
Hidden tests of later jobs use the reference API of earlier jobs
(`Storage.get_order` from job 06, `cli.cmd_<name>` from job 05), which is safe
because every job starts from the references, never from an agent's own work.

Validation (for every job): on the start state the visible tests pass and the
hidden tests have at least one failure; with the reference applied the hidden
and visible tests pass. Every earlier job's hidden tests also still pass on each
later reference state.

## Project conventions the jobs lean on

| # | Convention | Where it lives | Easy to miss? |
|---|------------|----------------|---------------|
| S1 | CLI errors: `ValueError` / `UnknownCustomer` (later `UnknownOrder`) are turned by `cli.main` into one line `error: <msg>` written to the **`out` stream (stdout), exit status 2** - not stderr, not 1 | `cli.main` | **yes** |
| S2 | Timestamps are stored as naive UTC; every calendar notion (today, a day, a week, a month) is the **merchant's own zone** via `timeutil` (`day_bounds(day, tz)`, `local_date`, `today(clock, tz)`), and "now" comes from the injected clock (`--now`), never `date.today()` / `.date()` | `timeutil.py`, `aggregation.py`, `service.py` | **yes** |
| S3 | Money is integer cents; shown with `format_cents(cents, currency)` (symbol, thousands separator, `NZ$`, `₹`, JPY without decimals); **in CSV/JSON it is raw integer cents** in `*_cents` columns | `currency.py`, `render.py` | yes |
| S4 | Rounding is half up; after job 03 every average goes through `currency.divide_cents` (`round()` is half-even, `//` floors) | `currency.py` | **yes** |
| S5 | Only `paid` orders count toward totals/order counts; `refunded` is tallied separately; `cancelled` is ignored | `models.Order.counts_toward_sales`, `aggregation._summarise` | |
| S6 | Local times are shown with `format_local` (`YYYY-MM-DD HH:MM` in the merchant's zone) | `timeutil.format_local` | yes |
| S7 | CSV via `csv.writer(..., lineterminator="\n")`, header row, in `render.py` | `render.py` | yes (`\r\n` default) |
| S8 | Commands: after job 05 each is `cmd_<name>(service, args, out) -> int` registered with `set_defaults(handler=...)`; `main` only sets up and handles errors | `cli.py` | |
| S9 | Merchant settings (timezone aliases like PST/IST, currency whitelist) are validated by `SettingsStore` / `normalise_timezone`; data lives in one sqlite db (`--db`) | `settings.py`, `storage.py` | |
| S10 | Import never aborts: each bad row is skipped as `line N: <reason>` (header = line 1), CLI prints `  skipped <error>` | `importer.py`, `cli` | |

## Jobs

| Job | Kind | What it asks | Conventions checked by hidden tests |
|-----|------|--------------|-------------------------------------|
| 01_week_command | CLI command | `week CUSTOMER [--csv]`, Monday..today like `breakdown` | **S2** (Auckland Monday while UTC Sunday, LA Monday evening while UTC Tuesday, `--now`), S7 CSV bytes, S3, S1 |
| 02_set_timezone | CLI / config | `set-timezone CUSTOMER TZ` | S9 aliases (`pst`, `IST`, `GMT`, whitespace), name/currency kept, reports follow, S1 exact messages + exit 2 |
| 03_average_rounding | bug fix | report average a cent low; add `divide_cents` | **S4** (2.5 -> 3, no float drift at 10^17, 0 orders), S3 `₹`, S2 Kolkata +05:30 day |
| 04_orders_command | CLI command | `orders CUSTOMER [--day/--yesterday]` line list | **S2** + **S6** (Berlin local day and time), S3 `€1,234.56`, all statuses, S1 |
| 05_cli_handlers_refactor | refactor | one `cmd_<name>(service, args, out)` per command via `set_defaults` | S8 (every command, no dispatch chain, errors central, exit = handler's return), byte-identical output of every command |
| 06_import_duplicates | bug fix | re-import crashes on duplicate ids; unknown customers accepted | S10 (`line N:` format, file order, skip-and-continue), existing orders untouched |
| 07_refund_command | CLI command | `refund ORDER_ID` | S1 (`error: unknown order X` like unknown customer, exit 2), S3 (`€2,500.75`, `¥1,500`), S5 report after refund, S8 |
| 08_monthly_report | report | `monthly CUSTOMER YEAR [--csv]` | **S2** month/year boundaries (Auckland, LA), **S4** (1002.5 -> 1003, TOTAL average), S3 cents CSV, S5, S7, S1, S8 |
| 09_cli_error_tracebacks | bug fix | tracebacks for bad `--now`, missing import file, unusable `--db` | **S1** (stdout not stderr, exit 2, one line, value/path mentioned) |
| 10_report_json | API | `report --json` | S3 integer cents, **S4** average via the report (12.5 -> 13), **S2** Kolkata today/yesterday, S1 errors stay text |
| 11_summary_all_customers | report | `summary [--day]`, one line per merchant | **S2** per-merchant today (Auckland a day ahead) and per-merchant local `--day`, S3 per currency, S5, S1, S8 |
| 12_export_orders_csv | report / CLI | `export CUSTOMER FIRST LAST` CSV of orders | **S2** local days over the US DST change, **S6** local time format, S3 cents, S7, S1 (backwards range message), S8 |

Recurring: S1 in 10 jobs, S2 in 7, S3 in 8, S4 in 3, S8 in 5 (job 05 and
every later job that adds a command). An agent that remembers "errors are
`error: ...` on stdout with exit 2", "days are the merchant's, via `timeutil`
and the clock", "averages go through `divide_cents`", "CSV money is integer
cents" and "commands are `cmd_<name>` handlers" from earlier jobs should need
fewer turns and fail fewer hidden tests on jobs 7-12.

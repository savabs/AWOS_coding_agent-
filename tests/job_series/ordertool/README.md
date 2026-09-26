# Job series: ordertool

Twelve related jobs on one project, done in order, to measure whether an agent
gets better at a project it has worked on before (spec:
`docs/specs/compounding_proof_spec.md`, piece B).

## Layout

```
base/                   ordertool with the orders_export_filters reference applied,
                        plus a money rounding helper and a few visible tests (tests/)
jobs/NN_<slug>/
  task.json             {"id", "goal", "max_turns": 40, "max_cost_usd": 0.3, "timeout_min": 15}
  hidden_tests/         test_hidden_jNN_*.py, self-contained (no conftest; each file puts
                        the project root on sys.path), so they work copied into either
                        <project>/hidden_tests/ or <project>/tests/
  reference/            full replacement files, paths relative to the project root
```

Job N starts from `base/` + `reference/` of jobs 1..N-1. Visible tests:
`python -m pytest` in the project root (`pytest.ini` has `testpaths = tests`).
Hidden tests of later jobs build fixtures through the reference API of earlier
jobs (e.g. `Order(..., discount_pct=...)` from job 04), which is safe because
every job starts from the references, never from an agent's own earlier work.

Validation (for every job): on the start state the visible tests pass and the
hidden tests have at least one failure; with the reference applied the hidden
and visible tests pass. Every earlier job's hidden tests also still pass on each
later reference state.

## Project conventions the jobs lean on

| # | Convention | Where it lives | Easy to miss? |
|---|------------|----------------|---------------|
| C1 | User errors derive from `OrderToolError` (`ValidationError`, `NotFoundError`, `StorageError`, ...); the CLI prints `error: <msg>` to stderr, exit 1, never a traceback | `errors.py`, `cli.main` | |
| C2 | Money is `Decimal`, rounded **half up** to cents with `round_money`; `round()` / bare `quantize` round half-even (1.125 -> 1.12 instead of 1.13) | `utils/money.py` | **yes** |
| C3 | Date ranges: `--from/--to` are `YYYY-MM-DD`, parsed by `parse_date_arg`, and **inclusive of the whole end day** (23:59:59 on `--to` counts); `filter_orders` does it and rejects backwards ranges | `utils/dates.py`, `export.filter_orders` | **yes** |
| C4 | CSV output: `csv.writer(..., lineterminator="\n")`, header row, returns the row count; after job 05 all CSV goes through `export.write_csv` | `export.py` | yes (`\r\n` default) |
| C5 | CSV commands: stdout by default, `--output FILE` writes the file and prints `exported N rows to FILE` on stderr; after job 03 via the atomic `write_output_file` | `cli.py`, `export.py` | |
| C6 | CLI subcommands: `sub.add_parser` + `set_defaults(handler=cmd_x)`, handlers `(service, args, out)` print to `out` and return 0; `add_date_range_args`/`date_range` for dates | `cli.py` | |
| C7 | Storage: one CSV with fixed `FIELDNAMES`, strict header check, atomic `save_all`; ids `ORD-0001` from `next_id`; after job 04 a `discount_pct` column with legacy files still loading; after job 08 read as `utf-8-sig` | `storage.py` | |
| C8 | Cancelled orders are excluded from revenue figures | `reports.py` | |
| C9 | Validation of a new order lives in `OrderService` (email regex, trimmed names, at least one item); reuse it rather than re-implementing | `services.py` | |

## Jobs

| Job | Kind | What it asks | Conventions checked by hidden tests |
|-----|------|--------------|-------------------------------------|
| 01_list_date_filter | feature | `list --from/--to` like export | C3 inclusive end + backwards range, C1 error format, list line format unchanged |
| 02_customers_report | report / CLI | `customers` CSV: per-customer orders + total spent | C4 exact bytes + quoting, C5 `--output` message, C2 money format, C3, C8, email case-folding |
| 03_output_path_errors | bug fix | `--output` to a missing dir / a dir / disk-full mid-write | C1 (no traceback, one line, path in message), atomic write like `storage.save_all`, no temp files left; covers `export` and `customers` |
| 04_order_discounts | feature | `add --discount PCT`, totals discounted everywhere | **C2** (1.25 at 10% = 1.13, customer total 2.26 = sum of rounded totals), C7 new column + old files still load, C1 for bad percentages |
| 05_csv_writer_refactor | refactor | one `write_csv(header, rows, stream)` behind every CSV | C4 contract, byte-identical output, spy proves every command uses it |
| 06_monthly_report | report / CLI | `monthly` CSV: orders, revenue, average per month | **C2** (average 1.125 -> 1.13), discounted totals, C3, C5, C8, C4 via `write_csv` spy, C1 |
| 07_bulk_status_change | feature | `set-status ID [ID ...] STATUS`, all or nothing | C1 exact existing messages (`cannot change ...`, `no order with id ...`), C7 single atomic save, lifecycle rules |
| 08_excel_bom_bug | bug fix | orders file saved by Excel (BOM, CRLF) | C7 (read `utf-8-sig`, write without BOM, strict header still enforced), legacy + discount columns |
| 09_import_orders | CLI command | `import FILE` of shop orders | C9 same checks as `add`, all or nothing, `line N:` errors, Excel BOM (from job 08), discount (C2 from job 04), next ids (C7), C1 |
| 10_timestamp_formats | bug fix | accept `2024-03-10 14:05` style timestamps | fix in the shared `parse_timestamp` so `add` and `import` both benefit; storage format unchanged; C1 |
| 11_products_report | report / CLI | `products` CSV: units, orders, revenue per SKU | **C2** per-line discount share rounded half up (tie GIFT/WIDGET at 2.26), C3, C5, C8, C4 via `write_csv` spy |
| 12_show_order | CLI command | `show ORDER_ID` detail view (layout given) | C2 (discount shown = subtotal - rounded total: 0.12 not 0.13), timestamp format, C1 `NotFoundError` message verbatim |

Recurring: C1 in 11 jobs, C2 in 6, C3 in 5, C4/C5 in 5, C7 in 4. An agent that
remembers "money goes through `round_money`", "date ranges are inclusive via
`filter_orders`", "CSV goes through `write_csv` + `write_output_file`" and
"errors are `OrderToolError` subclasses" from earlier jobs should need fewer
turns and fail fewer hidden tests on jobs 6, 9, 11 and 12.

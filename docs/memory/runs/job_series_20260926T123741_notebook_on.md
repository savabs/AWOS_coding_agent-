## How to work here
- Run tests: `python -m pytest` from project root; 25 tests pass
- Run customers: `python ordertool/cli.py customers` → CSV to stdout, header `customer,email,orders,total_spent`, sorted by total_spent desc then email
- Run monthly: `python ordertool/cli.py monthly` → CSV to stdout, header `month,orders,revenue,average_order`, one row per calendar month with orders, oldest first. Cancelled orders ignored. Revenue after discounts. Average order rounded to cent.
- With date filters: `python ordertool/cli.py monthly --from 2024-01-01 --to 2024-03-05` (same parsing/validation as export)
- With output file: `python ordertool/cli.py monthly --output out.csv` → prints `exported N rows to <path>`
- Run list: `python ordertool/cli.py list --from ... --to ...`; bad date/backwards range errors match export’s messages
- Error messages for bad `--output` paths: `error: output path directory does not exist: <path>` (folder missing) or `error: output path is a directory: <path>`

## Layout and conventions
- Main package `ordertool/`: `cli.py` (CLI commands incl. `customers`, `monthly`, `export`), `export.py` (export command, date filter logic, CSV writing with atomic write, shared `write_csv`), `reports.py` (monthly report logic), `services.py`, `models.py`, `storage.py` (`OrderStore`), `utils/dates.py`, `utils/money.py`
- Tests in `tests/`: `conftest.py`, `test_helpers.py`, `test_export_cli.py`, `test_storage_services.py`
- Use shared `write_csv(header, rows, stream)` in `ordertool/export.py`: writes header then data rows (accepts any iterable of lists), returns number of data rows written. All CSV commands (`export`, `export --line-items`, `customers`, `monthly`) go through it. Output byte-for-byte identical.
- `customers` CSV columns exactly: `customer,email,orders,total_spent`
- `monthly` CSV columns exactly: `month,orders,revenue,average_order`

## Pitfalls
- `OrderStore` has no `save_order` method — manual seeding with it raises `AttributeError`; grep `add`/`update`/`save` in `storage.py` for the real mutator before writing seed scripts
- `tests/data/test_orders.csv` does not exist; fixtures are built in `conftest.py`, so don’t `cat` that path
- A bad manual invocation printed the CSV header twice before failing — don’t mistake that for a test failure; run full `pytest`
- `--output` now validates early: parent dir must exist, path must not be a directory. If `--to` or `--from` are also bad, the date error fires first (cli args are parsed in argument order).
- Atomic write: uses `tempfile.NamedTemporaryFile(dir=output_dir, delete=False)` then `shutil.move(temp_path, final_path)`. This prevents partial/corrupt writes on disk-full or other failures. No leftover tempfile because `delete=False` and the temp is explicitly removed on exception in the `with` block.

## Past jobs
- Add --from/--to date filters to list command -> success: 25 tests passed
- Add customers command (CSV, cancelled excluded, case-insensitive email, name from most recent order, sort by total desc/email) -> success: 25 tests passed
- Fix --output to validate path existence/is-dir; add atomic write to prevent partial file overwrite -> success: 25 tests passed
- Extract shared write_csv function for all CSV commands -> success: 25 tests passed
- Add monthly command (monthly CSV, cancelled ignored, revenue after discounts, average rounded to cent, --from/--to/--output) -> success: 25 tests passed

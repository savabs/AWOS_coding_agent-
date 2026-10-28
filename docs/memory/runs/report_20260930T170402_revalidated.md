# Evaluation report — ordertool 20260930T170402

> **INVALID** — health check `health.json`: 6 fatal, 4 other violation(s).
> Do not draw conclusions from this run until the harness is fixed.
> - **notebook_drop** arm=on job=4: notebook 2732 -> 364 chars (-87%) between job 3 and job 4
> - **aider_no_files** arm=aider job=3: Aider had no files in chat (asked_to_add_files; preloaded=0, edits applied=0, marked invalid=True)
> - **aider_no_files** arm=aider job=4: Aider had no files in chat (asked_to_add_files; preloaded=0, edits applied=0, marked invalid=True)
> - **aider_no_files** arm=aider job=5: Aider had no files in chat (asked_to_add_files; preloaded=0, edits applied=0, marked invalid=True)
> - **aider_no_files** arm=aider job=6: Aider had no files in chat (asked_to_add_files; preloaded=0, edits applied=0, marked invalid=True)
> - **aider_no_files** arm=aider job=12: Aider had no files in chat (asked_to_add_files; preloaded=0, edits applied=0, marked invalid=True)

## Run

- results: `/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/bench-run/.awos/job_series_20260930T170402_revalidated.json`
- run id: `20260930T170402` (revalidated 20261002T210437)
- series: `ordertool` · arms: `off`, `on`, `aider`
- model pin: `deepseek/deepseek-v4-flash` · blocked: deepseek-v4-pro
- jobs: 12 · repeats K = 1 · rows: 36
- dropped from all arms (paired exclusion, 5):
  - j3 — aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)
  - j4 — aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)
  - j5 — aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)
  - j6 — aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)
  - j12 — aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)

## Per arm

Rows valid in every arm only. Intervals are 95%. 

| arm | solved/n | rate | Wilson CI | Clopper-Pearson CI | mean turns | mean $/job | $/solved | solved per $1 | mean min |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `off` | 5/7 | 71.4% | [35.9, 91.8] | [29.0, 96.3] | 24.3 | $0.0323 | $0.0452 | 22.1 | 7.03 |
| `on` | 7/7 | 100.0% | [64.6, 100.0] | [59.0, 100.0] | 19.0 | $0.0242 | $0.0242 | 41.3 | 6.08 |
| `aider` | 6/7 | 85.7% | [48.7, 97.4] | [42.1, 99.6] | 2.1 | $0.0024 | $0.0028 | 355.6 | 1.82 |

Before paired exclusion (each arm on its own valid rows, not comparable across arms): `off` 7/12 (58.3%, Wilson [32.0, 80.7]) · `on` 10/12 (83.3%, Wilson [55.2, 95.3]) · `aider` 6/7 (85.7%, Wilson [48.7, 97.4]).

$ is reconciled provider billing (`billed_usd`).

## Paired comparisons

Each pair uses the jobs valid in both of its arms. Difference = first − second. K = 1: exact McNemar on the discordant pairs (b = only first solved, c = only second solved).
MDE = minimum detectable difference at 80% power (≈ 2.8·sqrt(var(diff)/n), Miller).

### `off` vs `on` — 12 tasks, 12 paired runs

- solved: off 7/12 vs on 10/12 · mean difference -25.0%
- discordant: b = 0, c = 3 → exact McNemar p = 0.2500
- **Not significant at 0.05.** MDE ≈ 36.6%: a true difference smaller than this would usually go undetected with 12 tasks.
- read these transcripts (discordant tasks): j2, j10, j12
- billed $/job difference: $0.0064 · CI [+0.0015, +0.0113] · sign-flip p = 0.0322 (12 tasks)
- turns difference: 5.2 · CI [-0.8, +11.8] · sign-flip p = 0.1914 (12 tasks)

### `off` vs `aider` — 7 tasks, 7 paired runs

- excluded: j3 (aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)); j4 (aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)); j5 (aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)); j6 (aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)); j12 (aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit))
- solved: off 5/7 vs aider 6/7 · mean difference -14.3%
- discordant: b = 1, c = 2 → exact McNemar p = 1.0000
- **Not significant at 0.05.** MDE ≈ 73.0%: a true difference smaller than this would usually go undetected with 7 tasks.
- read these transcripts (discordant tasks): j2, j9, j10
- billed $/job difference: $0.0299 · CI [+0.0152, +0.0427] · sign-flip p = 0.0156 (7 tasks)
- turns difference: 22.1 · CI [+13.4, +30.4] · sign-flip p = 0.0156 (7 tasks)

### `on` vs `aider` — 7 tasks, 7 paired runs

- excluded: j3 (aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)); j4 (aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)); j5 (aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)); j6 (aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit)); j12 (aider: aider_no_edit (asked_to_add_files: Aider never had the code to edit))
- solved: on 7/7 vs aider 6/7 · mean difference 14.3%
- discordant: b = 1, c = 0 → exact McNemar p = 1.0000
- **Not significant at 0.05.** MDE ≈ 40.0%: a true difference smaller than this would usually go undetected with 7 tasks.
- read these transcripts (discordant tasks): j9
- billed $/job difference: $0.0218 · CI [+0.0121, +0.0308] · sign-flip p = 0.0156 (7 tasks)
- turns difference: 16.9 · CI [+9.3, +25.7] · sign-flip p = 0.0156 (7 tasks)

## Per job

Cell: verdict (✓ solved, ✗ not, invalid) · hidden passed/total · turns · billed $.

| job | id | `off` | `on` | `aider` |
| --- | --- | --- | --- | --- |
| 1 | `01_list_date_filter` | ✓ 9/9 · 16t · $0.0212 | ✓ 9/9 · 12t · $0.0205 | ✓ 9/9 · 2t · $0.0024 |
| 2 | `02_customers_report` | ✗ 6/7 · 34t · $0.0457 | ✓ 7/7 · 18t · $0.0255 | ✓ 7/7 · 2t · $0.0019 |
| 3 | `03_output_path_errors` | ✗ 5/9 · 21t · $0.0249 | ✗ 8/9 · 17t · $0.0216 | invalid 2/9 · 1t · $0.0165 |
| 4 | `04_order_discounts` | ✗ 11/13 · 26t · $0.0364 | ✗ 11/13 · 30t · $0.0307 | invalid 3/13 · 1t · $0.0032 |
| 5 | `05_csv_writer_refactor` | ✓ 8/8 · 9t · $0.0107 | ✓ 8/8 · 8t · $0.0105 | invalid 3/8 · 2t · $0.0004 |
| 6 | `06_monthly_report` | ✓ 7/7 · 23t · $0.0272 | ✓ 7/7 · 27t · $0.0361 | invalid 0/7 · 1t · $0.0028 |
| 7 | `07_bulk_status_change` | ✓ 8/8 · 40t · $0.0481 | ✓ 8/8 · 15t · $0.0350 | ✓ 8/8 · 2t · $0.0017 |
| 8 | `08_excel_bom_bug` | ✓ 8/8 · 8t · $0.0048 | ✓ 8/8 · 9t · $0.0026 | ✓ 8/8 · 2t · $0.0015 |
| 9 | `09_import_orders` | ✓ 13/13 · 27t · $0.0530 | ✓ 13/13 · 30t · $0.0400 | ✗ 3/13 · 2t · $0.0026 |
| 10 | `10_timestamp_formats` | ✗ 14/15 · 12t · $0.0079 | ✓ 15/15 · 9t · $0.0111 | ✓ 15/15 · 3t · $0.0042 |
| 11 | `11_products_report` | ✓ 6/6 · 33t · $0.0451 | ✓ 6/6 · 40t · $0.0350 | ✓ 6/6 · 2t · $0.0025 |
| 12 | `12_show_order` | ✗ 1/6 · 40t · $0.0392 | ✓ 6/6 · 12t · $0.0185 | invalid 0/6 · 1t · $0.0005* |

\* ledger cost (no reconciled billing for that row).

## Failures (valid rows)

Includes valid rows of jobs dropped by paired exclusion.

### `off` — 5 unsolved valid run(s)

- j2 `02_customers_report`: `tests/test_hidden_j02_customers.py::test_output_file_and_message`
- j3 `03_output_path_errors`: `tests/test_hidden_j03_output_errors.py::test_missing_directory_is_clean_error[command0]`, `tests/test_hidden_j03_output_errors.py::test_missing_directory_is_clean_error[command1]`, `tests/test_hidden_j03_output_errors.py::test_missing_directory_is_clean_error[command2]`, `tests/test_hidden_j03_output_errors.py::test_failure_mid_write_keeps_previous_file`
- j4 `04_order_discounts`: `tests/test_hidden_j04_discounts.py::test_invalid_discount_is_clean_error[ten]`, `tests/test_hidden_j04_discounts.py::test_old_orders_file_still_loads`
- j10 `10_timestamp_formats`: `tests/test_hidden_j10_timestamps.py::test_helper_accepts_friendly_forms[2024-03-10`
- j12 `12_show_order`: `tests/test_hidden_j12_show.py::test_example_from_the_request`, `tests/test_hidden_j12_show.py::test_no_discount_line_without_discount`, `tests/test_hidden_j12_show.py::test_discount_amount_matches_rounded_total`, `tests/test_hidden_j12_show.py::test_fractional_percentage`, `tests/test_hidden_j12_show.py::test_real_cli`

### `on` — 2 unsolved valid run(s)

- j3 `03_output_path_errors`: `tests/test_hidden_j03_output_errors.py::test_failure_mid_write_keeps_previous_file`
- j4 `04_order_discounts`: `tests/test_hidden_j04_discounts.py::test_invalid_discount_is_clean_error[ten]`, `tests/test_hidden_j04_discounts.py::test_old_orders_file_still_loads`

### `aider` — 1 unsolved valid run(s)

- j9 `09_import_orders`: `tests/test_hidden_j09_import.py::test_basic_import`, `tests/test_hidden_j09_import.py::test_optional_discount_column`, `tests/test_hidden_j09_import.py::test_excel_file`, `tests/test_hidden_j09_import.py::test_same_checks_as_add`, `tests/test_hidden_j09_import.py::test_bad_row_imports_nothing[Bob,not-an-email,2024-03-10T09:00:00,WIDGET:1:1.00-3]`, `tests/test_hidden_j09_import.py::test_bad_row_imports_nothing[Bob,bob@example.com,2024-03-32T09:00:00,WIDGET:1:1.00-3]`, `tests/test_hidden_j09_import.py::test_bad_row_imports_nothing[Bob,bob@example.com,2024-03-10T09:00:00,WIDGET:0:1.00-3]`, `tests/test_hidden_j09_import.py::test_bad_row_imports_nothing[,bob@example.com,2024-03-10T09:00:00,WIDGET:1:1.00-3]`, `tests/test_hidden_j09_import.py::test_bad_row_imports_nothing[Bob,bob@example.com,2024-03-10T09:00:00,-3]`, `tests/test_hidden_j09_import.py::test_import_into_new_database`

### Failing hidden tests across arms

| test | `off` | `on` | `aider` | total |
| --- | --- | --- | --- | --- |
| `tests/test_hidden_j03_output_errors.py::test_failure_mid_write_keeps_previous_file` | 1 | 1 | 0 | 2 |
| `tests/test_hidden_j04_discounts.py::test_invalid_discount_is_clean_error[ten]` | 1 | 1 | 0 | 2 |
| `tests/test_hidden_j04_discounts.py::test_old_orders_file_still_loads` | 1 | 1 | 0 | 2 |
| `tests/test_hidden_j02_customers.py::test_output_file_and_message` | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j03_output_errors.py::test_missing_directory_is_clean_error[command0]` | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j03_output_errors.py::test_missing_directory_is_clean_error[command1]` | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j03_output_errors.py::test_missing_directory_is_clean_error[command2]` | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j09_import.py::test_bad_row_imports_nothing[,bob@example.com,2024-03-10T09:00:00,WIDGET:1:1.00-3]` | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j09_import.py::test_bad_row_imports_nothing[Bob,bob@example.com,2024-03-10T09:00:00,-3]` | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j09_import.py::test_bad_row_imports_nothing[Bob,bob@example.com,2024-03-10T09:00:00,WIDGET:0:1.00-3]` | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j09_import.py::test_bad_row_imports_nothing[Bob,bob@example.com,2024-03-32T09:00:00,WIDGET:1:1.00-3]` | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j09_import.py::test_bad_row_imports_nothing[Bob,not-an-email,2024-03-10T09:00:00,WIDGET:1:1.00-3]` | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j09_import.py::test_basic_import` | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j09_import.py::test_excel_file` | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j09_import.py::test_import_into_new_database` | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j09_import.py::test_optional_discount_column` | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j09_import.py::test_same_checks_as_add` | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j10_timestamps.py::test_helper_accepts_friendly_forms[2024-03-10` | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j12_show.py::test_discount_amount_matches_rounded_total` | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j12_show.py::test_example_from_the_request` | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j12_show.py::test_fractional_percentage` | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j12_show.py::test_no_discount_line_without_discount` | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j12_show.py::test_real_cli` | 1 | 0 | 0 | 1 |

## Pareto: solve rate vs $

![pareto](pareto.svg)

| arm | mean $/job | solve rate |
| --- | --- | --- |
| `off` | $0.0323 | 71.4% |
| `on` | $0.0242 | 100.0% |
| `aider` | $0.0024 | 85.7% |

Protocol rule 9: compare against a simple retry baseline at the same budget before claiming a cost win.

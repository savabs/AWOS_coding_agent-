# Evaluation report — backupd 20261005T183332

> **VALID** — health check `health.json`: 0 fatal, 4 other violation(s).

## Run

- results: `/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/bench-run/.awos/job_series_20261005T183332.json`
- run id: `20261005T183332`
- series: `backupd` · arms: `off`, `on`
- model pin: `deepseek/deepseek-v4-flash` · blocked: deepseek-v4-pro
- jobs: 12 · repeats K = 2 · rows: 48
- dropped (paired exclusion): none

## Per arm

Rows valid in every arm only. Intervals are 95%. With K > 1 the runs of one task are correlated, so the run-level intervals are too narrow; use the paired task-level tests below.

| arm | solved/n | rate | Wilson CI | Clopper-Pearson CI | pass@1 | pass^2 | mean turns | mean $/job | $/solved | solved per $1 | mean min |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `off` | 17/24 | 70.8% | [50.8, 85.1] | [48.9, 87.4] | 70.8% | 58.3% | 13.0 | $0.0106 | $0.0150 | 66.8 | 3.83 |
| `on` | 13/24 | 54.2% | [35.1, 72.1] | [32.8, 74.4] | 54.2% | 41.7% | 8.4 | $0.0084 | $0.0155 | 64.6 | 3.35 |

$ is reconciled provider billing (`billed_usd`).

## Paired comparisons

Each pair uses the jobs valid in both of its arms. Difference = first − second. K > 1: per-task solve-rate difference; paired sign-flip permutation p and bootstrap CI over tasks (seed 20261002, 10000 resamples).
MDE = minimum detectable difference at 80% power (≈ 2.8·sqrt(var(diff)/n), Miller).

### `off` vs `on` — 12 tasks, 24 paired runs

- solved: off 17/24 vs on 13/24 · mean difference 16.7% · bootstrap CI [4.2, 29.2]
- paired sign-flip permutation (exact): p = 0.1250
- **Not significant at 0.05.** MDE ≈ 19.9%: a true difference smaller than this would usually go undetected with 12 tasks.
- read these transcripts (discordant tasks): j5, j7, j9, j11, j12
- billed $/job difference: $0.0022 · CI [+0.0001, +0.0049] · sign-flip p = 0.0947 (12 tasks)
- turns difference: 4.6 · CI [-0.3, +11.1] · sign-flip p = 0.1562 (12 tasks)

## Per job

Cell: verdict (✓ solved, ✗ not, invalid) · hidden passed/total · turns · billed $. Repeats are listed in order.

| job | id | `off` | `on` |
| --- | --- | --- | --- |
| 1 | `01_missing_source_error` | ✓ 7/7 · 1t · $0.0023<br>✓ 7/7 · 1t · $0.0039 | ✓ 7/7 · 1t · $0.0000<br>✓ 7/7 · 1t · $0.0000 |
| 2 | `02_list_backups` | ✓ 8/8 · 1t · $0.0035<br>✓ 8/8 · 1t · $0.0076 | ✓ 8/8 · 1t · $0.0032<br>✓ 8/8 · 1t · $0.0017 |
| 3 | `03_cli_handlers_refactor` | ✓ 14/14 · 1t · $0.0000<br>✓ 14/14 · 1t · $0.0019 | ✓ 14/14 · 1t · $0.0000<br>✓ 14/14 · 1t · $0.0023 |
| 4 | `04_min_free_space` | ✓ 12/12 · 41t · $0.0185<br>✓ 12/12 · 17t · $0.0029 | ✓ 12/12 · 14t · $0.0172<br>✓ 12/12 · 23t · $0.0066 |
| 5 | `05_partial_archive_bug` | ✓ 6/6 · 1t · $0.0042<br>✓ 6/6 · 1t · $0.0120 | ✓ 6/6 · 1t · $0.0054<br>✗ 2/6 · 1t · $0.0070 |
| 6 | `06_status_report` | ✗ 6/7 · 1t · $0.0045<br>✗ 3/7 · 1t · $0.0008 | ✗ 3/7 · 1t · $0.0025<br>✗ 6/7 · 1t · $0.0022 |
| 7 | `07_verify_command` | ✗ 8/10 · 19t · $0.0206<br>✓ 10/10 · 55t · $0.0255 | ✗ 8/10 · 1t · $0.0042<br>✗ 9/10 · 1t · $0.0143 |
| 8 | `08_exclude_dir_patterns` | ✓ 6/6 · 1t · $0.0017<br>✓ 6/6 · 1t · $0.0047 | ✓ 6/6 · 1t · $0.0015<br>✓ 6/6 · 11t · $0.0074 |
| 9 | `09_failure_notifications` | ✓ 14/14 · 41t · $0.0258<br>✗ 13/14 · 35t · $0.0260 | ✗ 13/14 · 33t · $0.0261<br>✗ 13/14 · 28t · $0.0208 |
| 10 | `10_keep_last` | ✗ 9/10 · 10t · $0.0132<br>✗ 9/10 · 19t · $0.0153 | ✗ 9/10 · 8t · $0.0060<br>✗ 9/10 · 29t · $0.0285 |
| 11 | `11_restore_command` | ✓ 12/12 · 15t · $0.0159<br>✓ 12/12 · 25t · $0.0173 | ✗ 7/12 · 1t · $0.0000<br>✓ 12/12 · 40t · $0.0317 |
| 12 | `12_prune_command` | ✓ 9/9 · 22t · $0.0188<br>✗ 3/9 · 1t · $0.0075 | ✗ 1/9 · 1t · $0.0065<br>✓ 9/9 · 1t · $0.0058 |

\* ledger cost (no reconciled billing for that row).

## Failures (valid rows)

Includes valid rows of jobs dropped by paired exclusion.

### `off` — 7 unsolved valid run(s)

- j6 r1 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`
- j6 r2 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j7 r1 `07_verify_command`: `tests/test_hidden_j07_verify.py::test_corrupted_zip_member_is_reported`, `tests/test_hidden_j07_verify.py::test_unknown_name`
- j9 r2 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error`
- j10 r1 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j10 r2 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j12 r2 `12_prune_command`: `tests/test_hidden_j12_prune.py::test_prune`, `tests/test_hidden_j12_prune.py::test_dry_run_deletes_nothing`, `tests/test_hidden_j12_prune.py::test_keep_last_from_ini`, `tests/test_hidden_j12_prune.py::test_env_wins_and_singular`, `tests/test_hidden_j12_prune.py::test_backup_dir_with_variables_from_ini`, `tests/test_hidden_j12_prune.py::test_pruning_is_logged`

### `on` — 11 unsolved valid run(s)

- j5 r2 `05_partial_archive_bug`: `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[true-ZipFile-write]`, `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[false-TarFile-add]`, `tests/test_hidden_j05_partial_archive.py::test_monitoring_still_sees_the_previous_backup`, `tests/test_hidden_j05_partial_archive.py::test_run_backup_raises_project_error`
- j6 r1 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j6 r2 `06_status_report`: `tests/test_hidden_j06_status.py::test_text_layout`
- j7 r1 `07_verify_command`: `tests/test_hidden_j07_verify.py::test_verify_latest_by_default`, `tests/test_hidden_j07_verify.py::test_backup_dir_from_ini_with_variables`
- j7 r2 `07_verify_command`: `tests/test_hidden_j07_verify.py::test_corrupted_zip_member_is_reported`
- j9 r1 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error`
- j9 r2 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error`
- j10 r1 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j10 r2 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j11 r1 `11_restore_command`: `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_zip-backup-20200313-000000.zip-members0]`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members1]`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members2]`, `tests/test_hidden_j11_restore.py::test_damaged_backup`
- j12 r1 `12_prune_command`: `tests/test_hidden_j12_prune.py::test_prune`, `tests/test_hidden_j12_prune.py::test_dry_run_deletes_nothing`, `tests/test_hidden_j12_prune.py::test_keep_last_from_ini`, `tests/test_hidden_j12_prune.py::test_env_wins_and_singular`, `tests/test_hidden_j12_prune.py::test_retention_from_env`, `tests/test_hidden_j12_prune.py::test_backup_dir_with_variables_from_ini`, `tests/test_hidden_j12_prune.py::test_pruning_is_logged`, `tests/test_hidden_j12_prune.py::test_bad_keep_last`

### Failing hidden tests across arms

| test | `off` | `on` | total |
| --- | --- | --- | --- |
| `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]` | 2 | 2 | 4 |
| `tests/test_hidden_j06_status.py::test_report_dict` | 2 | 1 | 3 |
| `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error` | 1 | 2 | 3 |
| `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini` | 1 | 1 | 2 |
| `tests/test_hidden_j06_status.py::test_json_view` | 1 | 1 | 2 |
| `tests/test_hidden_j06_status.py::test_no_backups` | 1 | 1 | 2 |
| `tests/test_hidden_j07_verify.py::test_corrupted_zip_member_is_reported` | 1 | 1 | 2 |
| `tests/test_hidden_j12_prune.py::test_backup_dir_with_variables_from_ini` | 1 | 1 | 2 |
| `tests/test_hidden_j12_prune.py::test_dry_run_deletes_nothing` | 1 | 1 | 2 |
| `tests/test_hidden_j12_prune.py::test_env_wins_and_singular` | 1 | 1 | 2 |
| `tests/test_hidden_j12_prune.py::test_keep_last_from_ini` | 1 | 1 | 2 |
| `tests/test_hidden_j12_prune.py::test_prune` | 1 | 1 | 2 |
| `tests/test_hidden_j12_prune.py::test_pruning_is_logged` | 1 | 1 | 2 |
| `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[false-TarFile-add]` | 0 | 1 | 1 |
| `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[true-ZipFile-write]` | 0 | 1 | 1 |
| `tests/test_hidden_j05_partial_archive.py::test_monitoring_still_sees_the_previous_backup` | 0 | 1 | 1 |
| `tests/test_hidden_j05_partial_archive.py::test_run_backup_raises_project_error` | 0 | 1 | 1 |
| `tests/test_hidden_j06_status.py::test_text_layout` | 0 | 1 | 1 |
| `tests/test_hidden_j07_verify.py::test_backup_dir_from_ini_with_variables` | 0 | 1 | 1 |
| `tests/test_hidden_j07_verify.py::test_unknown_name` | 1 | 0 | 1 |
| `tests/test_hidden_j07_verify.py::test_verify_latest_by_default` | 0 | 1 | 1 |
| `tests/test_hidden_j11_restore.py::test_damaged_backup` | 0 | 1 | 1 |
| `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination` | 0 | 1 | 1 |
| `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members1]` | 0 | 1 | 1 |
| `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members2]` | 0 | 1 | 1 |
| `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_zip-backup-20200313-000000.zip-members0]` | 0 | 1 | 1 |
| `tests/test_hidden_j12_prune.py::test_bad_keep_last` | 0 | 1 | 1 |
| `tests/test_hidden_j12_prune.py::test_retention_from_env` | 0 | 1 | 1 |

## Pareto: solve rate vs $

![pareto](pareto.svg)

| arm | mean $/job | solve rate |
| --- | --- | --- |
| `off` | $0.0106 | 70.8% |
| `on` | $0.0084 | 54.2% |

Protocol rule 9: compare against a simple retry baseline at the same budget before claiming a cost win.

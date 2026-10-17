# Evaluation report — backupd 20261006T113109

> **VALID** — health check `health.json`: 0 fatal, 2 other violation(s).

## Run

- results: `/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/bench-run/.awos/job_series_20261006T113109.json`
- run id: `20261006T113109`
- series: `backupd` · arms: `off`, `on`
- model pin: `deepseek/deepseek-v4-flash` · blocked: deepseek-v4-pro
- jobs: 12 · repeats K = 2 · rows: 48
- dropped (paired exclusion): none

## Per arm

Rows valid in every arm only. Intervals are 95%. With K > 1 the runs of one task are correlated, so the run-level intervals are too narrow; use the paired task-level tests below.

| arm | solved/n | rate | Wilson CI | Clopper-Pearson CI | pass@1 | pass^2 | mean turns | mean $/job | $/solved | solved per $1 | mean min |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `off` | 15/24 | 62.5% | [42.7, 78.8] | [40.6, 81.2] | 62.5% | 50.0% | 10.0 | $0.0055 | $0.0089 | 112.7 | 1.94 |
| `on` | 13/24 | 54.2% | [35.1, 72.1] | [32.8, 74.4] | 54.2% | 33.3% | 10.0 | $0.0082 | $0.0152 | 66.0 | 2.31 |

$ is reconciled provider billing (`billed_usd`).

## Paired comparisons

Each pair uses the jobs valid in both of its arms. Difference = first − second. K > 1: per-task solve-rate difference; paired sign-flip permutation p and bootstrap CI over tasks (seed 20261002, 10000 resamples).
MDE = minimum detectable difference at 80% power (≈ 2.8·sqrt(var(diff)/n), Miller).

### `off` vs `on` — 12 tasks, 24 paired runs

- solved: off 15/24 vs on 13/24 · mean difference 8.3% · bootstrap CI [-8.3, 25.0]
- paired sign-flip permutation (exact): p = 0.6250
- **Not significant at 0.05.** MDE ≈ 23.3%: a true difference smaller than this would usually go undetected with 12 tasks.
- read these transcripts (discordant tasks): j4, j5, j6, j7, j9, j12
- billed $/job difference: $-0.0027 · CI [-0.0069, +0.0016] · sign-flip p = 0.2549 (12 tasks)
- turns difference: 0.0 · CI [-8.1, +8.0] · sign-flip p = 1.0000 (12 tasks)

## Per job

Cell: verdict (✓ solved, ✗ not, invalid) · hidden passed/total · turns · billed $. Repeats are listed in order.

| job | id | `off` | `on` |
| --- | --- | --- | --- |
| 1 | `01_missing_source_error` | ✓ 7/7 · 1t · $0.0000<br>✓ 7/7 · 1t · $0.0000 | ✓ 7/7 · 1t · $0.0000<br>✓ 7/7 · 1t · $0.0000 |
| 2 | `02_list_backups` | ✓ 8/8 · 20t · $0.0149<br>✓ 8/8 · 21t · $0.0129 | ✓ 8/8 · 1t · $0.0133<br>✓ 8/8 · 1t · $0.0000 |
| 3 | `03_cli_handlers_refactor` | ✓ 14/14 · 1t · $0.0023<br>✓ 14/14 · 1t · $0.0000 | ✓ 14/14 · 2t · $0.0000<br>✓ 14/14 · 1t · $0.0020 |
| 4 | `04_min_free_space` | ✓ 12/12 · 41t · $0.0093<br>✓ 12/12 · 2t · $0.0015 | ✓ 12/12 · 2t · $0.0189<br>✗ 9/12 · 12t · $0.0036 |
| 5 | `05_partial_archive_bug` | ✓ 6/6 · 1t · $0.0021<br>✓ 6/6 · 1t · $0.0082 | ✗ 2/6 · 2t · $0.0000<br>✓ 6/6 · 1t · $0.0000 |
| 6 | `06_status_report` | ✗ 3/7 · 14t · $0.0091<br>✓ 7/7 · 22t · $0.0019 | ✗ 2/7 · 13t · $0.0068<br>✗ 3/7 · 22t · $0.0162 |
| 7 | `07_verify_command` | ✗ 6/10 · 1t · $0.0068<br>✓ 10/10 · 2t · $0.0093 | ✓ 10/10 · 33t · $0.0151<br>✗ 9/10 · 17t · $0.0146 |
| 8 | `08_exclude_dir_patterns` | ✓ 6/6 · 1t · $0.0000<br>✓ 6/6 · 1t · $0.0000 | ✓ 6/6 · 30t · $0.0216<br>✓ 6/6 · 2t · $0.0000 |
| 9 | `09_failure_notifications` | ✗ 13/14 · 41t · $0.0111<br>✗ 10/14 · 19t · $0.0017 | ✗ 13/14 · 35t · $0.0357<br>✓ 14/14 · 1t · $0.0122 |
| 10 | `10_keep_last` | ✗ 9/10 · 2t · $0.0000<br>✗ 9/10 · 2t · $0.0017 | ✗ 9/10 · 32t · $0.0111<br>✗ 9/10 · 12t · $0.0043 |
| 11 | `11_restore_command` | ✗ 8/12 · 1t · $0.0000<br>✗ 11/12 · 41t · $0.0182 | ✗ 8/12 · 1t · $0.0000<br>✗ 11/12 · 1t · $0.0000 |
| 12 | `12_prune_command` | ✗ 6/9 · 2t · $0.0102<br>✓ 9/9 · 1t · $0.0119 | ✓ 9/9 · 16t · $0.0185<br>✗ 1/9 · 1t · $0.0031 |

\* ledger cost (no reconciled billing for that row).

## Failures (valid rows)

Includes valid rows of jobs dropped by paired exclusion.

### `off` — 9 unsolved valid run(s)

- j6 r1 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j7 r1 `07_verify_command`: `tests/test_hidden_j07_verify.py::test_verify_latest_by_default`, `tests/test_hidden_j07_verify.py::test_verify_named_zip_and_tar`, `tests/test_hidden_j07_verify.py::test_backup_dir_from_ini_with_variables`, `tests/test_hidden_j07_verify.py::test_verification_is_logged`
- j9 r1 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error`
- j9 r2 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_failed_run[always-1]`, `tests/test_hidden_j09_notify_on.py::test_failed_run[failure-1]`, `tests/test_hidden_j09_notify_on.py::test_space_failure_is_mailed`, `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error`
- j10 r1 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j10 r2 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j11 r1 `11_restore_command`: `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_zip-backup-20200313-000000.zip-members0]`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members1]`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members2]`
- j11 r2 `11_restore_command`: `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination`
- j12 r1 `12_prune_command`: `tests/test_hidden_j12_prune.py::test_prune`, `tests/test_hidden_j12_prune.py::test_keep_last_from_ini`, `tests/test_hidden_j12_prune.py::test_pruning_is_logged`

### `on` — 11 unsolved valid run(s)

- j4 r2 `04_min_free_space`: `tests/test_hidden_j04_min_free_space.py::test_not_enough_space_refuses_the_run`, `tests/test_hidden_j04_min_free_space.py::test_env_can_lower_the_limit`, `tests/test_hidden_j04_min_free_space.py::test_env_can_raise_the_limit`
- j5 r1 `05_partial_archive_bug`: `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[true-ZipFile-write]`, `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[false-TarFile-add]`, `tests/test_hidden_j05_partial_archive.py::test_monitoring_still_sees_the_previous_backup`, `tests/test_hidden_j05_partial_archive.py::test_run_backup_raises_project_error`
- j6 r1 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_text_layout`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j6 r2 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j7 r2 `07_verify_command`: `tests/test_hidden_j07_verify.py::test_unknown_name`
- j9 r1 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error`
- j10 r1 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j10 r2 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j11 r1 `11_restore_command`: `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_zip-backup-20200313-000000.zip-members0]`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members1]`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members2]`
- j11 r2 `11_restore_command`: `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination`
- j12 r2 `12_prune_command`: `tests/test_hidden_j12_prune.py::test_prune`, `tests/test_hidden_j12_prune.py::test_dry_run_deletes_nothing`, `tests/test_hidden_j12_prune.py::test_keep_last_from_ini`, `tests/test_hidden_j12_prune.py::test_env_wins_and_singular`, `tests/test_hidden_j12_prune.py::test_retention_from_env`, `tests/test_hidden_j12_prune.py::test_backup_dir_with_variables_from_ini`, `tests/test_hidden_j12_prune.py::test_pruning_is_logged`, `tests/test_hidden_j12_prune.py::test_bad_keep_last`

### Failing hidden tests across arms

| test | `off` | `on` | total |
| --- | --- | --- | --- |
| `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]` | 2 | 2 | 4 |
| `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination` | 2 | 2 | 4 |
| `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini` | 1 | 2 | 3 |
| `tests/test_hidden_j06_status.py::test_json_view` | 1 | 2 | 3 |
| `tests/test_hidden_j06_status.py::test_no_backups` | 1 | 2 | 3 |
| `tests/test_hidden_j06_status.py::test_report_dict` | 1 | 2 | 3 |
| `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error` | 2 | 1 | 3 |
| `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members1]` | 1 | 1 | 2 |
| `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members2]` | 1 | 1 | 2 |
| `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_zip-backup-20200313-000000.zip-members0]` | 1 | 1 | 2 |
| `tests/test_hidden_j12_prune.py::test_keep_last_from_ini` | 1 | 1 | 2 |
| `tests/test_hidden_j12_prune.py::test_prune` | 1 | 1 | 2 |
| `tests/test_hidden_j12_prune.py::test_pruning_is_logged` | 1 | 1 | 2 |
| `tests/test_hidden_j04_min_free_space.py::test_env_can_lower_the_limit` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_env_can_raise_the_limit` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_not_enough_space_refuses_the_run` | 0 | 1 | 1 |
| `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[false-TarFile-add]` | 0 | 1 | 1 |
| `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[true-ZipFile-write]` | 0 | 1 | 1 |
| `tests/test_hidden_j05_partial_archive.py::test_monitoring_still_sees_the_previous_backup` | 0 | 1 | 1 |
| `tests/test_hidden_j05_partial_archive.py::test_run_backup_raises_project_error` | 0 | 1 | 1 |
| `tests/test_hidden_j06_status.py::test_text_layout` | 0 | 1 | 1 |
| `tests/test_hidden_j07_verify.py::test_backup_dir_from_ini_with_variables` | 1 | 0 | 1 |
| `tests/test_hidden_j07_verify.py::test_unknown_name` | 0 | 1 | 1 |
| `tests/test_hidden_j07_verify.py::test_verification_is_logged` | 1 | 0 | 1 |
| `tests/test_hidden_j07_verify.py::test_verify_latest_by_default` | 1 | 0 | 1 |
| `tests/test_hidden_j07_verify.py::test_verify_named_zip_and_tar` | 1 | 0 | 1 |
| `tests/test_hidden_j09_notify_on.py::test_failed_run[always-1]` | 1 | 0 | 1 |
| `tests/test_hidden_j09_notify_on.py::test_failed_run[failure-1]` | 1 | 0 | 1 |
| `tests/test_hidden_j09_notify_on.py::test_space_failure_is_mailed` | 1 | 0 | 1 |
| `tests/test_hidden_j12_prune.py::test_backup_dir_with_variables_from_ini` | 0 | 1 | 1 |
| `tests/test_hidden_j12_prune.py::test_bad_keep_last` | 0 | 1 | 1 |
| `tests/test_hidden_j12_prune.py::test_dry_run_deletes_nothing` | 0 | 1 | 1 |
| `tests/test_hidden_j12_prune.py::test_env_wins_and_singular` | 0 | 1 | 1 |
| `tests/test_hidden_j12_prune.py::test_retention_from_env` | 0 | 1 | 1 |

## Pareto: solve rate vs $

![pareto](pareto.svg)

| arm | mean $/job | solve rate |
| --- | --- | --- |
| `off` | $0.0055 | 62.5% |
| `on` | $0.0082 | 54.2% |

Protocol rule 9: compare against a simple retry baseline at the same budget before claiming a cost win.

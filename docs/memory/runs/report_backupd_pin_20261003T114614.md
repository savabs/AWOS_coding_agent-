# Evaluation report — backupd 20261003T114614

> **VALID** — health check `health.json`: 0 fatal, 3 other violation(s).

## Run

- results: `/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/bench-run/.awos/job_series_20261003T114614.json`
- run id: `20261003T114614`
- series: `backupd` · arms: `off`, `on`
- model pin: `deepseek/deepseek-v4-flash` · blocked: deepseek-v4-pro
- jobs: 12 · repeats K = 1 · rows: 24
- dropped (paired exclusion): none

## Per arm

Rows valid in every arm only. Intervals are 95%. 

| arm | solved/n | rate | Wilson CI | Clopper-Pearson CI | mean turns | mean $/job | $/solved | solved per $1 | mean min |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `off` | 6/12 | 50.0% | [25.4, 74.6] | [21.1, 78.9] | 23.3 | $0.0133 | $0.0267 | 37.5 | 4.88 |
| `on` | 6/12 | 50.0% | [25.4, 74.6] | [21.1, 78.9] | 20.5 | $0.0151 | $0.0302 | 33.1 | 5.54 |

$ is reconciled provider billing (`billed_usd`).

## Paired comparisons

Each pair uses the jobs valid in both of its arms. Difference = first − second. K = 1: exact McNemar on the discordant pairs (b = only first solved, c = only second solved).
MDE = minimum detectable difference at 80% power (≈ 2.8·sqrt(var(diff)/n), Miller).

### `off` vs `on` — 12 tasks, 12 paired runs

- solved: off 6/12 vs on 6/12 · mean difference 0.0%
- discordant: b = 2, c = 2 → exact McNemar p = 1.0000
- **Not significant at 0.05.** MDE ≈ 48.7%: a true difference smaller than this would usually go undetected with 12 tasks.
- read these transcripts (discordant tasks): j1, j4, j5, j7
- billed $/job difference: $-0.0018 · CI [-0.0088, +0.0047] · sign-flip p = 0.6484 (12 tasks)
- turns difference: 2.8 · CI [-1.4, +7.7] · sign-flip p = 0.2988 (12 tasks)

## Per job

Cell: verdict (✓ solved, ✗ not, invalid) · hidden passed/total · turns · billed $.

| job | id | `off` | `on` |
| --- | --- | --- | --- |
| 1 | `01_missing_source_error` | ✓ 7/7 · 14t · $0.0000 | ✗ 6/7 · 14t · $0.0056 |
| 2 | `02_list_backups` | ✓ 8/8 · 17t · $0.0143 | ✓ 8/8 · 16t · $0.0059 |
| 3 | `03_cli_handlers_refactor` | ✓ 14/14 · 17t · $0.0074 | ✓ 14/14 · 5t · $0.0060 |
| 4 | `04_min_free_space` | ✓ 12/12 · 40t · $0.0098 | ✗ 9/12 · 48t · $0.0393 |
| 5 | `05_partial_archive_bug` | ✗ 2/6 · 17t · $0.0074 | ✓ 6/6 · 25t · $0.0188 |
| 6 | `06_status_report` | ✗ 2/7 · 17t · $0.0113 | ✗ 3/7 · 16t · $0.0081 |
| 7 | `07_verify_command` | ✗ 9/10 · 18t · $0.0172 | ✓ 10/10 · 18t · $0.0132 |
| 8 | `08_exclude_dir_patterns` | ✓ 6/6 · 10t · $0.0021 | ✓ 6/6 · 11t · $0.0099 |
| 9 | `09_failure_notifications` | ✗ 13/14 · 57t · $0.0402 | ✗ 11/167 · 35t · $0.0333 |
| 10 | `10_keep_last` | ✗ 9/10 · 20t · $0.0111 | ✗ 9/10 · 21t · $0.0034 |
| 11 | `11_restore_command` | ✗ 3/12 · 26t · $0.0279 | ✗ 11/12 · 20t · $0.0125 |
| 12 | `12_prune_command` | ✓ 9/9 · 27t · $0.0114 | ✓ 9/9 · 17t · $0.0252 |

\* ledger cost (no reconciled billing for that row).

## Failures (valid rows)

Includes valid rows of jobs dropped by paired exclusion.

### `off` — 6 unsolved valid run(s)

- j5 `05_partial_archive_bug`: `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[true-ZipFile-write]`, `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[false-TarFile-add]`, `tests/test_hidden_j05_partial_archive.py::test_monitoring_still_sees_the_previous_backup`, `tests/test_hidden_j05_partial_archive.py::test_run_backup_raises_project_error`
- j6 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_text_layout`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j7 `07_verify_command`: `tests/test_hidden_j07_verify.py::test_corrupted_zip_member_is_reported`
- j9 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error`
- j10 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j11 `11_restore_command`: `tests/test_hidden_j11_restore.py::test_restore_latest`, `tests/test_hidden_j11_restore.py::test_restore_named_tar_into_empty_dir`, `tests/test_hidden_j11_restore.py::test_dest_paths_are_expanded_like_settings`, `tests/test_hidden_j11_restore.py::test_refuses_non_empty_destination`, `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_zip-backup-20200313-000000.zip-members0]`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members1]`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members2]`, `tests/test_hidden_j11_restore.py::test_restore_is_logged`

### `on` — 6 unsolved valid run(s)

- j1 `01_missing_source_error`: `tests/test_hidden_j01_missing_source.py::test_run_backup_raises_project_error`
- j4 `04_min_free_space`: `tests/test_hidden_j04_min_free_space.py::test_not_enough_space_refuses_the_run`, `tests/test_hidden_j04_min_free_space.py::test_env_can_lower_the_limit`, `tests/test_hidden_j04_min_free_space.py::test_env_can_raise_the_limit`
- j6 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j9 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_failed_run[always-1]`, `tests/test_hidden_j09_notify_on.py::test_failed_run[failure-1]`, `tests/test_hidden_j09_notify_on.py::test_space_failure_is_mailed`
- j10 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j11 `11_restore_command`: `tests/test_hidden_j11_restore.py::test_damaged_backup`

### Failing hidden tests across arms

| test | `off` | `on` | total |
| --- | --- | --- | --- |
| `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini` | 1 | 1 | 2 |
| `tests/test_hidden_j06_status.py::test_json_view` | 1 | 1 | 2 |
| `tests/test_hidden_j06_status.py::test_no_backups` | 1 | 1 | 2 |
| `tests/test_hidden_j06_status.py::test_report_dict` | 1 | 1 | 2 |
| `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]` | 1 | 1 | 2 |
| `tests/test_hidden_j01_missing_source.py::test_run_backup_raises_project_error` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_env_can_lower_the_limit` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_env_can_raise_the_limit` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_not_enough_space_refuses_the_run` | 0 | 1 | 1 |
| `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[false-TarFile-add]` | 1 | 0 | 1 |
| `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[true-ZipFile-write]` | 1 | 0 | 1 |
| `tests/test_hidden_j05_partial_archive.py::test_monitoring_still_sees_the_previous_backup` | 1 | 0 | 1 |
| `tests/test_hidden_j05_partial_archive.py::test_run_backup_raises_project_error` | 1 | 0 | 1 |
| `tests/test_hidden_j06_status.py::test_text_layout` | 1 | 0 | 1 |
| `tests/test_hidden_j07_verify.py::test_corrupted_zip_member_is_reported` | 1 | 0 | 1 |
| `tests/test_hidden_j09_notify_on.py::test_failed_run[always-1]` | 0 | 1 | 1 |
| `tests/test_hidden_j09_notify_on.py::test_failed_run[failure-1]` | 0 | 1 | 1 |
| `tests/test_hidden_j09_notify_on.py::test_space_failure_is_mailed` | 0 | 1 | 1 |
| `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error` | 1 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_damaged_backup` | 0 | 1 | 1 |
| `tests/test_hidden_j11_restore.py::test_dest_paths_are_expanded_like_settings` | 1 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination` | 1 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_refuses_non_empty_destination` | 1 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_restore_is_logged` | 1 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_restore_latest` | 1 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_restore_named_tar_into_empty_dir` | 1 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members1]` | 1 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members2]` | 1 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_zip-backup-20200313-000000.zip-members0]` | 1 | 0 | 1 |

## Pareto: solve rate vs $

![pareto](pareto.svg)

| arm | mean $/job | solve rate |
| --- | --- | --- |
| `off` | $0.0133 | 50.0% |
| `on` | $0.0151 | 50.0% |

Protocol rule 9: compare against a simple retry baseline at the same budget before claiming a cost win.

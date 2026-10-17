# Evaluation report — backupd 20261002T223614_vs_20261003T114614

> **Health: UNKNOWN** — no health.json found; run `scripts/eval_health.py` before believing this run (protocol rule 2).

## Run

- results: `/Users/becmachlean/.claude/jobs/e65f3328/tmp/ablation1/combined.json`
- run id: `20261002T223614_vs_20261003T114614`
- series: `backupd` · arms: `off_base`, `off_pin`, `on_base`, `on_pin`
- model pin: `deepseek/deepseek-v4-flash` · blocked: deepseek-v4-pro
- jobs: 12 · repeats K = 1 · rows: 48
- dropped (paired exclusion): none

## Per arm

Rows valid in every arm only. Intervals are 95%. 

| arm | solved/n | rate | Wilson CI | Clopper-Pearson CI | mean turns | mean $/job | $/solved | solved per $1 | mean min |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `off_base` | 7/12 | 58.3% | [32.0, 80.7] | [27.7, 84.8] | 24.5 | $0.0297 | $0.0509 | 19.6 | 5.67 |
| `off_pin` | 6/12 | 50.0% | [25.4, 74.6] | [21.1, 78.9] | 23.3 | $0.0133 | $0.0267 | 37.5 | 4.88 |
| `on_base` | 9/12 | 75.0% | [46.8, 91.1] | [42.8, 94.5] | 24.8 | $0.0363 | $0.0484 | 20.7 | 5.76 |
| `on_pin` | 6/12 | 50.0% | [25.4, 74.6] | [21.1, 78.9] | 20.5 | $0.0151 | $0.0302 | 33.1 | 5.54 |

$ is reconciled provider billing (`billed_usd`).

## Paired comparisons

Each pair uses the jobs valid in both of its arms. Difference = first − second. K = 1: exact McNemar on the discordant pairs (b = only first solved, c = only second solved).
MDE = minimum detectable difference at 80% power (≈ 2.8·sqrt(var(diff)/n), Miller).

### `off_base` vs `off_pin` — 12 tasks, 12 paired runs

- solved: off_base 7/12 vs off_pin 6/12 · mean difference 8.3%
- discordant: b = 2, c = 1 → exact McNemar p = 1.0000
- **Not significant at 0.05.** MDE ≈ 41.6%: a true difference smaller than this would usually go undetected with 12 tasks.
- read these transcripts (discordant tasks): j7, j11, j12
- billed $/job difference: $0.0164 · CI [+0.0085, +0.0253] · sign-flip p = 0.0010 (12 tasks)
- turns difference: 1.2 · CI [-4.7, +6.2] · sign-flip p = 0.7402 (12 tasks)

### `off_base` vs `on_base` — 12 tasks, 12 paired runs

- solved: off_base 7/12 vs on_base 9/12 · mean difference -16.7%
- discordant: b = 1, c = 3 → exact McNemar p = 0.6250
- **Not significant at 0.05.** MDE ≈ 46.7%: a true difference smaller than this would usually go undetected with 12 tasks.
- read these transcripts (discordant tasks): j4, j5, j9, j12
- billed $/job difference: $-0.0066 · CI [-0.0200, +0.0042] · sign-flip p = 0.3867 (12 tasks)
- turns difference: -0.3 · CI [-5.6, +4.7] · sign-flip p = 0.9302 (12 tasks)

### `off_base` vs `on_pin` — 12 tasks, 12 paired runs

- solved: off_base 7/12 vs on_pin 6/12 · mean difference 8.3%
- discordant: b = 3, c = 2 → exact McNemar p = 1.0000
- **Not significant at 0.05.** MDE ≈ 54.0%: a true difference smaller than this would usually go undetected with 12 tasks.
- read these transcripts (discordant tasks): j1, j4, j5, j11, j12
- billed $/job difference: $0.0146 · CI [+0.0072, +0.0216] · sign-flip p = 0.0044 (12 tasks)
- turns difference: 4.0 · CI [+0.2, +8.2] · sign-flip p = 0.0898 (12 tasks)

### `off_pin` vs `on_base` — 12 tasks, 12 paired runs

- solved: off_pin 6/12 vs on_base 9/12 · mean difference -25.0%
- discordant: b = 1, c = 4 → exact McNemar p = 0.3750
- **Not significant at 0.05.** MDE ≈ 50.2%: a true difference smaller than this would usually go undetected with 12 tasks.
- read these transcripts (discordant tasks): j4, j5, j7, j9, j11
- billed $/job difference: $-0.0230 · CI [-0.0419, -0.0105] · sign-flip p = 0.0005 (12 tasks)
- turns difference: -1.5 · CI [-8.2, +4.7] · sign-flip p = 0.7026 (12 tasks)

### `off_pin` vs `on_pin` — 12 tasks, 12 paired runs

- solved: off_pin 6/12 vs on_pin 6/12 · mean difference 0.0%
- discordant: b = 2, c = 2 → exact McNemar p = 1.0000
- **Not significant at 0.05.** MDE ≈ 48.7%: a true difference smaller than this would usually go undetected with 12 tasks.
- read these transcripts (discordant tasks): j1, j4, j5, j7
- billed $/job difference: $-0.0018 · CI [-0.0088, +0.0047] · sign-flip p = 0.6484 (12 tasks)
- turns difference: 2.8 · CI [-1.4, +7.7] · sign-flip p = 0.2988 (12 tasks)

### `on_base` vs `on_pin` — 12 tasks, 12 paired runs

- solved: on_base 9/12 vs on_pin 6/12 · mean difference 25.0%
- discordant: b = 3, c = 0 → exact McNemar p = 0.2500
- **Not significant at 0.05.** MDE ≈ 36.6%: a true difference smaller than this would usually go undetected with 12 tasks.
- read these transcripts (discordant tasks): j1, j9, j11
- billed $/job difference: $0.0212 · CI [+0.0081, +0.0387] · sign-flip p = 0.0044 (12 tasks)
- turns difference: 4.3 · CI [-2.3, +11.2] · sign-flip p = 0.2764 (12 tasks)

## Per job

Cell: verdict (✓ solved, ✗ not, invalid) · hidden passed/total · turns · billed $.

| job | id | `off_base` | `off_pin` | `on_base` | `on_pin` |
| --- | --- | --- | --- | --- | --- |
| 1 | `01_missing_source_error` | ✓ 7/7 · 18t · $0.0146 | ✓ 7/7 · 14t · $0.0000 | ✓ 7/7 · 12t · $0.0073 | ✗ 6/7 · 14t · $0.0056 |
| 2 | `02_list_backups` | ✓ 8/8 · 21t · $0.0199 | ✓ 8/8 · 17t · $0.0143 | ✓ 8/8 · 22t · $0.0223 | ✓ 8/8 · 16t · $0.0059 |
| 3 | `03_cli_handlers_refactor` | ✓ 14/14 · 7t · $0.0149 | ✓ 14/14 · 17t · $0.0074 | ✓ 14/14 · 6t · $0.0077 | ✓ 14/14 · 5t · $0.0060 |
| 4 | `04_min_free_space` | ✓ 12/12 · 40t · $0.0264 | ✓ 12/12 · 40t · $0.0098 | ✗ 9/12 · 32t · $0.0306 | ✗ 9/12 · 48t · $0.0393 |
| 5 | `05_partial_archive_bug` | ✗ 2/6 · 28t · $0.0457 | ✗ 2/6 · 17t · $0.0074 | ✓ 6/6 · 12t · $0.0246 | ✓ 6/6 · 25t · $0.0188 |
| 6 | `06_status_report` | ✗ 3/7 · 22t · $0.0384 | ✗ 2/7 · 17t · $0.0113 | ✗ 3/7 · 14t · $0.0346 | ✗ 3/7 · 16t · $0.0081 |
| 7 | `07_verify_command` | ✓ 10/10 · 18t · $0.0278 | ✗ 9/10 · 18t · $0.0172 | ✓ 10/10 · 22t · $0.0287 | ✓ 10/10 · 18t · $0.0132 |
| 8 | `08_exclude_dir_patterns` | ✓ 6/6 · 11t · $0.0132 | ✓ 6/6 · 10t · $0.0021 | ✓ 6/6 · 15t · $0.0240 | ✓ 6/6 · 11t · $0.0099 |
| 9 | `09_failure_notifications` | ✗ 13/14 · 36t · $0.0532 | ✗ 13/14 · 57t · $0.0402 | ✓ 14/14 · 40t · $0.0414 | ✗ 11/167 · 35t · $0.0333 |
| 10 | `10_keep_last` | ✗ 8/10 · 36t · $0.0154 | ✗ 9/10 · 20t · $0.0111 | ✗ 9/10 · 48t · $0.0447 | ✗ 9/10 · 21t · $0.0034 |
| 11 | `11_restore_command` | ✓ 12/12 · 20t · $0.0237 | ✗ 3/12 · 26t · $0.0279 | ✓ 12/12 · 40t · $0.0415 | ✗ 11/12 · 20t · $0.0125 |
| 12 | `12_prune_command` | ✗ 6/9 · 37t · $0.0633 | ✓ 9/9 · 27t · $0.0114 | ✓ 9/9 · 35t · $0.1284 | ✓ 9/9 · 17t · $0.0252 |

\* ledger cost (no reconciled billing for that row).

## Failures (valid rows)

Includes valid rows of jobs dropped by paired exclusion.

### `off_base` — 5 unsolved valid run(s)

- j5 `05_partial_archive_bug`: `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[true-ZipFile-write]`, `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[false-TarFile-add]`, `tests/test_hidden_j05_partial_archive.py::test_monitoring_still_sees_the_previous_backup`, `tests/test_hidden_j05_partial_archive.py::test_run_backup_raises_project_error`
- j6 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j9 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error`
- j10 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_more_than_there_are`, `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j12 `12_prune_command`: `tests/test_hidden_j12_prune.py::test_prune`, `tests/test_hidden_j12_prune.py::test_keep_last_from_ini`, `tests/test_hidden_j12_prune.py::test_pruning_is_logged`

### `off_pin` — 6 unsolved valid run(s)

- j5 `05_partial_archive_bug`: `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[true-ZipFile-write]`, `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[false-TarFile-add]`, `tests/test_hidden_j05_partial_archive.py::test_monitoring_still_sees_the_previous_backup`, `tests/test_hidden_j05_partial_archive.py::test_run_backup_raises_project_error`
- j6 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_text_layout`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j7 `07_verify_command`: `tests/test_hidden_j07_verify.py::test_corrupted_zip_member_is_reported`
- j9 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error`
- j10 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j11 `11_restore_command`: `tests/test_hidden_j11_restore.py::test_restore_latest`, `tests/test_hidden_j11_restore.py::test_restore_named_tar_into_empty_dir`, `tests/test_hidden_j11_restore.py::test_dest_paths_are_expanded_like_settings`, `tests/test_hidden_j11_restore.py::test_refuses_non_empty_destination`, `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_zip-backup-20200313-000000.zip-members0]`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members1]`, `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members2]`, `tests/test_hidden_j11_restore.py::test_restore_is_logged`

### `on_base` — 3 unsolved valid run(s)

- j4 `04_min_free_space`: `tests/test_hidden_j04_min_free_space.py::test_not_enough_space_refuses_the_run`, `tests/test_hidden_j04_min_free_space.py::test_env_can_lower_the_limit`, `tests/test_hidden_j04_min_free_space.py::test_env_can_raise_the_limit`
- j6 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j10 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`

### `on_pin` — 6 unsolved valid run(s)

- j1 `01_missing_source_error`: `tests/test_hidden_j01_missing_source.py::test_run_backup_raises_project_error`
- j4 `04_min_free_space`: `tests/test_hidden_j04_min_free_space.py::test_not_enough_space_refuses_the_run`, `tests/test_hidden_j04_min_free_space.py::test_env_can_lower_the_limit`, `tests/test_hidden_j04_min_free_space.py::test_env_can_raise_the_limit`
- j6 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j9 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_failed_run[always-1]`, `tests/test_hidden_j09_notify_on.py::test_failed_run[failure-1]`, `tests/test_hidden_j09_notify_on.py::test_space_failure_is_mailed`
- j10 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j11 `11_restore_command`: `tests/test_hidden_j11_restore.py::test_damaged_backup`

### Failing hidden tests across arms

| test | `off_base` | `off_pin` | `on_base` | `on_pin` | total |
| --- | --- | --- | --- | --- | --- |
| `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini` | 1 | 1 | 1 | 1 | 4 |
| `tests/test_hidden_j06_status.py::test_json_view` | 1 | 1 | 1 | 1 | 4 |
| `tests/test_hidden_j06_status.py::test_no_backups` | 1 | 1 | 1 | 1 | 4 |
| `tests/test_hidden_j06_status.py::test_report_dict` | 1 | 1 | 1 | 1 | 4 |
| `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]` | 1 | 1 | 1 | 1 | 4 |
| `tests/test_hidden_j04_min_free_space.py::test_env_can_lower_the_limit` | 0 | 0 | 1 | 1 | 2 |
| `tests/test_hidden_j04_min_free_space.py::test_env_can_raise_the_limit` | 0 | 0 | 1 | 1 | 2 |
| `tests/test_hidden_j04_min_free_space.py::test_not_enough_space_refuses_the_run` | 0 | 0 | 1 | 1 | 2 |
| `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[false-TarFile-add]` | 1 | 1 | 0 | 0 | 2 |
| `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[true-ZipFile-write]` | 1 | 1 | 0 | 0 | 2 |
| `tests/test_hidden_j05_partial_archive.py::test_monitoring_still_sees_the_previous_backup` | 1 | 1 | 0 | 0 | 2 |
| `tests/test_hidden_j05_partial_archive.py::test_run_backup_raises_project_error` | 1 | 1 | 0 | 0 | 2 |
| `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error` | 1 | 1 | 0 | 0 | 2 |
| `tests/test_hidden_j01_missing_source.py::test_run_backup_raises_project_error` | 0 | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j06_status.py::test_text_layout` | 0 | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j07_verify.py::test_corrupted_zip_member_is_reported` | 0 | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j09_notify_on.py::test_failed_run[always-1]` | 0 | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j09_notify_on.py::test_failed_run[failure-1]` | 0 | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j09_notify_on.py::test_space_failure_is_mailed` | 0 | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j10_keep_last.py::test_more_than_there_are` | 1 | 0 | 0 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_damaged_backup` | 0 | 0 | 0 | 1 | 1 |
| `tests/test_hidden_j11_restore.py::test_dest_paths_are_expanded_like_settings` | 0 | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination` | 0 | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_refuses_non_empty_destination` | 0 | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_restore_is_logged` | 0 | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_restore_latest` | 0 | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_restore_named_tar_into_empty_dir` | 0 | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members1]` | 0 | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_tar-backup-20200313-000000.tar-members2]` | 0 | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j11_restore.py::test_unsafe_archives_extract_nothing[make_zip-backup-20200313-000000.zip-members0]` | 0 | 1 | 0 | 0 | 1 |
| `tests/test_hidden_j12_prune.py::test_keep_last_from_ini` | 1 | 0 | 0 | 0 | 1 |
| `tests/test_hidden_j12_prune.py::test_prune` | 1 | 0 | 0 | 0 | 1 |
| `tests/test_hidden_j12_prune.py::test_pruning_is_logged` | 1 | 0 | 0 | 0 | 1 |

## Pareto: solve rate vs $

![pareto](pareto.svg)

| arm | mean $/job | solve rate |
| --- | --- | --- |
| `off_base` | $0.0297 | 58.3% |
| `off_pin` | $0.0133 | 50.0% |
| `on_base` | $0.0363 | 75.0% |
| `on_pin` | $0.0151 | 50.0% |

Protocol rule 9: compare against a simple retry baseline at the same budget before claiming a cost win.

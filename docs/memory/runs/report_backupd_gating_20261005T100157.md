# Evaluation report — backupd 20261005T100157

> **VALID** — health check `health.json`: 0 fatal, 4 other violation(s).

## Run

- results: `/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/bench-run/.awos/job_series_20261005T100157.json`
- run id: `20261005T100157`
- series: `backupd` · arms: `off`, `on`
- model pin: `deepseek/deepseek-v4-flash` · blocked: deepseek-v4-pro
- jobs: 12 · repeats K = 2 · rows: 48
- dropped (paired exclusion): none

## Per arm

Rows valid in every arm only. Intervals are 95%. With K > 1 the runs of one task are correlated, so the run-level intervals are too narrow; use the paired task-level tests below.

| arm | solved/n | rate | Wilson CI | Clopper-Pearson CI | pass@1 | pass^2 | mean turns | mean $/job | $/solved | solved per $1 | mean min |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `off` | 14/24 | 58.3% | [38.8, 75.5] | [36.6, 77.9] | 58.3% | 41.7% | 24.2 | $0.0109 | $0.0187 | 53.5 | 3.74 |
| `on` | 13/24 | 54.2% | [35.1, 72.1] | [32.8, 74.4] | 54.2% | 50.0% | 24.9 | $0.0189 | $0.0349 | 28.6 | 4.59 |

$ is reconciled provider billing (`billed_usd`).

## Paired comparisons

Each pair uses the jobs valid in both of its arms. Difference = first − second. K > 1: per-task solve-rate difference; paired sign-flip permutation p and bootstrap CI over tasks (seed 20261002, 10000 resamples).
MDE = minimum detectable difference at 80% power (≈ 2.8·sqrt(var(diff)/n), Miller).

### `off` vs `on` — 12 tasks, 24 paired runs

- solved: off 14/24 vs on 13/24 · mean difference 4.2% · bootstrap CI [-20.8, 25.0]
- paired sign-flip permutation (exact): p = 1.0000
- **Not significant at 0.05.** MDE ≈ 36.4%: a true difference smaller than this would usually go undetected with 12 tasks.
- read these transcripts (discordant tasks): j4, j5, j7, j9, j11, j12
- billed $/job difference: $-0.0080 · CI [-0.0133, -0.0023] · sign-flip p = 0.0234 (12 tasks)
- turns difference: -0.6 · CI [-5.0, +3.4] · sign-flip p = 0.8057 (12 tasks)

## Per job

Cell: verdict (✓ solved, ✗ not, invalid) · hidden passed/total · turns · billed $. Repeats are listed in order.

| job | id | `off` | `on` |
| --- | --- | --- | --- |
| 1 | `01_missing_source_error` | ✓ 7/7 · 15t · $0.0003<br>✓ 7/7 · 10t · $0.0060 | ✓ 7/7 · 18t · $0.0122<br>✓ 7/7 · 9t · $0.0000 |
| 2 | `02_list_backups` | ✓ 8/8 · 25t · $0.0117<br>✓ 8/8 · 21t · $0.0109 | ✓ 8/8 · 21t · $0.0107<br>✓ 8/8 · 19t · $0.0060 |
| 3 | `03_cli_handlers_refactor` | ✓ 14/14 · 9t · $0.0047<br>✓ 14/14 · 7t · $0.0083 | ✓ 14/14 · 8t · $0.0000<br>✓ 14/14 · 10t · $0.0055 |
| 4 | `04_min_free_space` | ✓ 12/12 · 23t · $0.0174<br>✓ 12/12 · 40t · $0.0199 | ✗ 1/12 · 68t · $0.0391<br>✓ 12/12 · 30t · $0.0311 |
| 5 | `05_partial_archive_bug` | ✗ 2/6 · 24t · $0.0035<br>✗ 2/6 · 25t · $0.0071 | ✓ 6/6 · 27t · $0.0254<br>✓ 6/6 · 30t · $0.0183 |
| 6 | `06_status_report` | ✗ 3/7 · 36t · $0.0053<br>✗ 2/7 · 20t · $0.0126 | ✗ 3/7 · 18t · $0.0311<br>✗ 2/7 · 25t · $0.0194 |
| 7 | `07_verify_command` | ✓ 10/10 · 40t · $0.0162<br>✗ 9/10 · 29t · $0.0032 | ✗ 9/10 · 16t · $0.0211<br>✗ 9/10 · 40t · $0.0422 |
| 8 | `08_exclude_dir_patterns` | ✓ 6/6 · 19t · $0.0025<br>✓ 6/6 · 19t · $0.0084 | ✓ 6/6 · 15t · $0.0118<br>✓ 6/6 · 17t · $0.0130 |
| 9 | `09_failure_notifications` | ✗ 13/14 · 28t · $0.0200<br>✓ 14/14 · 29t · $0.0033 | ✗ 13/14 · 36t · $0.0281<br>✗ 13/14 · 38t · $0.0341 |
| 10 | `10_keep_last` | ✗ 9/10 · 27t · $0.0066<br>✗ 9/10 · 25t · $0.0075 | ✗ 9/10 · 28t · $0.0171<br>✗ 9/10 · 19t · $0.0130 |
| 11 | `11_restore_command` | ✓ 12/12 · 40t · $0.0381<br>✗ 11/12 · 20t · $0.0080 | ✗ 11/12 · 27t · $0.0142<br>✗ 9/12 · 13t · $0.0123 |
| 12 | `12_prune_command` | ✓ 9/9 · 35t · $0.0342<br>✗ 6/9 · 16t · $0.0060 | ✓ 9/9 · 40t · $0.0348<br>✓ 9/9 · 25t · $0.0138 |

\* ledger cost (no reconciled billing for that row).

## Failures (valid rows)

Includes valid rows of jobs dropped by paired exclusion.

### `off` — 10 unsolved valid run(s)

- j5 r1 `05_partial_archive_bug`: `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[true-ZipFile-write]`, `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[false-TarFile-add]`, `tests/test_hidden_j05_partial_archive.py::test_monitoring_still_sees_the_previous_backup`, `tests/test_hidden_j05_partial_archive.py::test_run_backup_raises_project_error`
- j5 r2 `05_partial_archive_bug`: `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[true-ZipFile-write]`, `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[false-TarFile-add]`, `tests/test_hidden_j05_partial_archive.py::test_monitoring_still_sees_the_previous_backup`, `tests/test_hidden_j05_partial_archive.py::test_run_backup_raises_project_error`
- j6 r1 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j6 r2 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_text_layout`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j7 r2 `07_verify_command`: `tests/test_hidden_j07_verify.py::test_unknown_name`
- j9 r1 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error`
- j10 r1 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j10 r2 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j11 r2 `11_restore_command`: `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination`
- j12 r2 `12_prune_command`: `tests/test_hidden_j12_prune.py::test_prune`, `tests/test_hidden_j12_prune.py::test_keep_last_from_ini`, `tests/test_hidden_j12_prune.py::test_pruning_is_logged`

### `on` — 11 unsolved valid run(s)

- j4 r1 `04_min_free_space`: `tests/test_hidden_j04_min_free_space.py::test_sizes_from_ini[500M-524288000]`, `tests/test_hidden_j04_min_free_space.py::test_sizes_from_ini[2G-2147483648]`, `tests/test_hidden_j04_min_free_space.py::test_sizes_from_ini[1.5GiB-1610612736]`, `tests/test_hidden_j04_min_free_space.py::test_sizes_from_ini[64k-65536]`, `tests/test_hidden_j04_min_free_space.py::test_sizes_from_ini[4096-4096]`, `tests/test_hidden_j04_min_free_space.py::test_default_is_off`, `tests/test_hidden_j04_min_free_space.py::test_env_wins_and_empty_env_is_unset`, `tests/test_hidden_j04_min_free_space.py::test_bad_size_is_config_error`, `tests/test_hidden_j04_min_free_space.py::test_not_enough_space_refuses_the_run`, `tests/test_hidden_j04_min_free_space.py::test_env_can_raise_the_limit`, `tests/test_hidden_j04_min_free_space.py::test_show_config_lists_it_in_bytes`
- j6 r1 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j6 r2 `06_status_report`: `tests/test_hidden_j06_status.py::test_report_dict`, `tests/test_hidden_j06_status.py::test_text_layout`, `tests/test_hidden_j06_status.py::test_json_view`, `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini`, `tests/test_hidden_j06_status.py::test_no_backups`
- j7 r1 `07_verify_command`: `tests/test_hidden_j07_verify.py::test_unknown_name`
- j7 r2 `07_verify_command`: `tests/test_hidden_j07_verify.py::test_unknown_name`
- j9 r1 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error`
- j9 r2 `09_failure_notifications`: `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error`
- j10 r1 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j10 r2 `10_keep_last`: `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]`
- j11 r1 `11_restore_command`: `tests/test_hidden_j11_restore.py::test_damaged_backup`
- j11 r2 `11_restore_command`: `tests/test_hidden_j11_restore.py::test_dest_paths_are_expanded_like_settings`, `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination`, `tests/test_hidden_j11_restore.py::test_damaged_backup`

### Failing hidden tests across arms

| test | `off` | `on` | total |
| --- | --- | --- | --- |
| `tests/test_hidden_j06_status.py::test_json_env_wins_over_ini` | 2 | 2 | 4 |
| `tests/test_hidden_j06_status.py::test_json_view` | 2 | 2 | 4 |
| `tests/test_hidden_j06_status.py::test_no_backups` | 2 | 2 | 4 |
| `tests/test_hidden_j06_status.py::test_report_dict` | 2 | 2 | 4 |
| `tests/test_hidden_j10_keep_last.py::test_bad_values_are_config_errors[-1]` | 2 | 2 | 4 |
| `tests/test_hidden_j07_verify.py::test_unknown_name` | 1 | 2 | 3 |
| `tests/test_hidden_j09_notify_on.py::test_unreachable_mail_server_does_not_hide_the_error` | 1 | 2 | 3 |
| `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[false-TarFile-add]` | 2 | 0 | 2 |
| `tests/test_hidden_j05_partial_archive.py::test_failed_write_leaves_nothing_behind[true-ZipFile-write]` | 2 | 0 | 2 |
| `tests/test_hidden_j05_partial_archive.py::test_monitoring_still_sees_the_previous_backup` | 2 | 0 | 2 |
| `tests/test_hidden_j05_partial_archive.py::test_run_backup_raises_project_error` | 2 | 0 | 2 |
| `tests/test_hidden_j06_status.py::test_text_layout` | 1 | 1 | 2 |
| `tests/test_hidden_j11_restore.py::test_damaged_backup` | 0 | 2 | 2 |
| `tests/test_hidden_j11_restore.py::test_refuses_file_as_destination` | 1 | 1 | 2 |
| `tests/test_hidden_j04_min_free_space.py::test_bad_size_is_config_error` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_default_is_off` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_env_can_raise_the_limit` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_env_wins_and_empty_env_is_unset` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_not_enough_space_refuses_the_run` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_show_config_lists_it_in_bytes` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_sizes_from_ini[1.5GiB-1610612736]` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_sizes_from_ini[2G-2147483648]` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_sizes_from_ini[4096-4096]` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_sizes_from_ini[500M-524288000]` | 0 | 1 | 1 |
| `tests/test_hidden_j04_min_free_space.py::test_sizes_from_ini[64k-65536]` | 0 | 1 | 1 |
| `tests/test_hidden_j11_restore.py::test_dest_paths_are_expanded_like_settings` | 0 | 1 | 1 |
| `tests/test_hidden_j12_prune.py::test_keep_last_from_ini` | 1 | 0 | 1 |
| `tests/test_hidden_j12_prune.py::test_prune` | 1 | 0 | 1 |
| `tests/test_hidden_j12_prune.py::test_pruning_is_logged` | 1 | 0 | 1 |

## Pareto: solve rate vs $

![pareto](pareto.svg)

| arm | mean $/job | solve rate |
| --- | --- | --- |
| `off` | $0.0109 | 58.3% |
| `on` | $0.0189 | 54.2% |

Protocol rule 9: compare against a simple retry baseline at the same budget before claiming a cost win.

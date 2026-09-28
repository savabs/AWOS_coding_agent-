# Job series: backupd

Twelve related jobs on one project, done in order, to measure whether an agent
gets better at a project it has worked on before (spec:
`docs/specs/compounding_proof_spec.md`, piece B). Third series, after
`ordertool` and `salesdesk`.

## Layout

```
base/                   backupd (tests/long_tasks/config_env_migration project with its
                        reference applied) plus: errors.py (BackupdError base, `error:` exit 1
                        in the CLI), paths.expand_path (~ and $VARS for every path setting),
                        units.format_size / parse_size (binary units, `run` prints the size),
                        an APP_* cleaning conftest and tests/test_helpers.py
jobs/NN_<slug>/
  task.json             {"id", "goal", "max_turns": 40, "max_cost_usd": 0.3, "timeout_min": 15}
  hidden_tests/         test_hidden_jNN_*.py, self-contained (no conftest; each file puts
                        the project root on sys.path and clears APP_* variables itself), so
                        they work copied into either <project>/hidden_tests/ or <project>/tests/
  reference/            full replacement files, paths relative to the project root
```

Job N starts from `base/` + `reference/` of jobs 1..N-1. Visible tests:
`python -m pytest` in the project root (`pytest.ini` has `testpaths = tests`).
Hidden tests of later jobs rely on the reference behaviour of earlier jobs
(e.g. job 09 mails about the `min_free_space` failure from job 04), which is
safe because every job starts from the references, never from an agent's own
earlier work.

Validation (for every job): on the start state the visible tests pass and the
hidden tests have at least one failure; with the reference applied the hidden
and visible tests pass. Every earlier job's hidden tests also still pass on each
later reference state.

## Project conventions the jobs lean on

| # | Convention | Where it lives | Easy to miss? |
|---|------------|----------------|---------------|
| K1 | Every setting is declared once in `config.SETTINGS` (section, kind, default) and read through `resolve_setting` / `load_config`: `APP_<KEY>` wins over `settings.ini`, an empty variable counts as unset, bad values raise `ConfigError` naming the key. New settings also go in the hard-coded `show-config` field list | `config.py`, `cli._show_config` | **yes** (reading `os.environ` or the ini directly skips precedence; show-config list is separate) |
| K2 | Paths (settings and command-line paths) go through `paths.expand_path`: `~` **and `$VARS`** | `paths.py`, `config._convert` | **yes** (`Path(x).expanduser()` misses `$VAR`) |
| K3 | User errors are `BackupdError` subclasses; `main` prints `configuration error: <msg>` exit 2 for `ConfigError`, `error: <msg>` exit 1 for the rest, never a traceback; messages name the path/setting | `errors.py`, `cli.main` | |
| K4 | Sizes: binary units, shown via `units.format_size` (`1.5 KiB`), parsed via `units.parse_size` (`500M`) | `units.py` | yes |
| K5 | Read-only commands (`status`, later `list`, `verify`, `restore`, `prune`) need only `backup_dir`: they resolve single settings through `storage` / `resolve_setting`, never the full `load_config` (which requires `source_dir`). Storage functions take `settings_path`, not a `Config` | `health.py`, `storage.py` | **yes** |
| K6 | A backup is a file matching `backup-YYYYmmdd-HHMMSS.(zip|tar)`; `storage.list_backups` (oldest first) is the only way to find them, everything else in the folder is ignored | `storage.py` | yes |
| K7 | Logging: `logging.getLogger("backupd.<module>")`, configured by `configure_logging` (format `%(asctime)s %(levelname)s %(name)s: %(message)s`, optional `log_file`); never the root logger | `logging_setup.py` | yes |
| K8 | After job 03: each command is `cmd_<name>(args) -> int`, registered with `set_defaults(handler=...)`; `main` owns logging setup and error reporting | `cli.py` | |
| K9 | Mail goes through `notifier.build_notifier(settings_path, transport=...)`; `build_message` prefixes `[backupd] ` to subjects | `notifier.py` | |

## Jobs

| Job | Kind | What it asks | Conventions checked by hidden tests |
|-----|------|--------------|-------------------------------------|
| 01_missing_source_error | bug fix | missing / non-directory source_dir -> clean error, nothing written or pruned | K3 (BackupdError subclass, `error:` exit 1, path in message, real CLI no traceback), K1 (APP_SOURCE_DIR wins) |
| 02_list_backups | report / CLI | `list`: name, time, size per backup + total line | K4 exact sizes, K6 stray files ignored, K5 works with only APP_BACKUP_DIR, K2 `$VAR` in APP_BACKUP_DIR, K1 env over ini, `--config`, K3 exit 2 when unset |
| 03_cli_handlers_refactor | refactor | `cmd_<name>` handlers via `set_defaults(handler=)`; output unchanged | K8 (handler identity, dispatch via monkeypatched handler, logging configured first), K3 still in `main` |
| 04_min_free_space | config option | `min_free_space` size setting; refuse the run when the disk is short | **K1** (env wins, empty env, ConfigError naming key, show-config in bytes), **K4** (`parse_size` units, `900.0 TiB` in message), K3 exit 1 / 2 |
| 05_partial_archive_bug | bug fix | failed archive write leaves no half-written `backup-*` file | **K6** (temp name must not look like a backup, even mid-write), K3 (reason in one-line error), zip + tar |
| 06_status_report | report | `status` adds `oldest`, `total_size`; `status --json` | K4, K6, **K5** (env-only backup_dir with `$VAR`), K1, K3, K8 (still `cmd_status`) |
| 07_verify_command | CLI command | `verify [NAME]` reads an archive back | K3 (damaged / truncated / unknown / none), K5, K2 via ini `$VAR`, **K7** (log file line in project format), K8 |
| 08_exclude_dir_patterns | bug fix | `build/` excludes directories, not files | K1 (APP_EXCLUDE wins over ini), `.last_run` still excluded, `run` uses the same rules |
| 09_failure_notifications | config option | `notify_on = always/failure/never`; mail on failed runs | **K1** (case-insensitive choice, ConfigError, env wins, show-config), K9 (`[backupd] backup failed` subject, transport), K3 (SMTP failure must not hide the error), job 01 + 04 errors mailed |
| 10_keep_last | config option | `keep_last`: never prune the newest N | **K1 + K5** (storage resolves `keep_last` itself: env wins, negative/non-int -> ConfigError from `prune_old_backups` too), K6, show-config, `run` honours it |
| 11_restore_command | CLI command | `restore NAME|latest DEST` into a new/empty dir, refusing unsafe archives | **K2** (`~` and `$VAR` in DEST), K3, K5, K7 log line, K8, K6 (`latest`) |
| 12_prune_command | CLI command | `prune [--dry-run]` with exact summary lines | K4, K6, K1/K5 (retention + keep_last from env/ini, `${VAR}` backup_dir), K7 (deletions logged), K3 exit 2, K8 |

Recurring: K3 in 11 jobs, K1 in 8, K5 in 6, K6 in 6, K2 in 5, K8 in 5, K4 in 4,
K7 in 3. An agent that remembers "settings go through `SETTINGS`/`resolve_setting`
and show-config", "paths through `expand_path`", "sizes through `format_size`",
"read-only commands only need `backup_dir`" and "log via `backupd.*` loggers"
from earlier jobs should need fewer turns and fail fewer hidden tests on jobs
6, 7, 9, 10, 11 and 12.

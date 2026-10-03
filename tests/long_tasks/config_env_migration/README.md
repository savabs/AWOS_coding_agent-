# config_env_migration

Migration task: move `backupd` (a stdlib-only backup scheduler) from `settings.ini` to `APP_*` environment variables, keeping ini backward compatibility with env-wins precedence.
The hard part is sprawl: besides `config.py`, seven modules read the ini directly or reach into the raw `cfg.parser` (storage, notifier, logging_setup, health, scheduler, filters, archiver).
`hidden_tests/` (14 test functions, 22 cases) check typed env parsing, ini fallback, per-key override, bool/list parsing, missing-setting errors, each module's behaviour, the CLI and the README.
`reference/` overlaid on `project/` is a passing solution; `project/tests` pass both before and after.

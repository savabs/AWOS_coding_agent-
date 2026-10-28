# backupd

A small scheduled file backup service. It archives a source directory into
timestamped `backup-YYYYmmdd-HHMMSS.zip` (or `.tar`) files, prunes archives
older than the retention window and optionally e-mails a short report.

## Usage

```
python -m backupd run            # back up now
python -m backupd run --if-due   # back up only if the interval has passed
python -m backupd status         # summary for monitoring
python -m backupd due            # exit 0 if a backup is due
python -m backupd show-config    # print effective settings
```

Use `--config PATH` to point at a settings file other than `./settings.ini`.

## Configuration

Settings come from environment variables prefixed with `APP_` (the key name
in upper case). For older installs, `settings.ini` in the working directory
is still read when present; if a setting is set in both places the
environment variable wins. Empty variables count as unset.

| Environment variable   | INI key (section)             | Type           | Default     | Notes                                 |
|------------------------|-------------------------------|----------------|-------------|---------------------------------------|
| `APP_SOURCE_DIR`       | `source_dir` (backup)         | path           | (required)  | directory to back up                  |
| `APP_BACKUP_DIR`       | `backup_dir` (backup)         | path           | (required)  | where archives are written            |
| `APP_INTERVAL_MINUTES` | `interval_minutes` (backup)   | int            | 60          | minimum time between runs             |
| `APP_RETENTION_DAYS`   | `retention_days` (backup)     | int            | 7           | older archives are deleted            |
| `APP_COMPRESS`         | `compress` (backup)           | bool           | true        | zip (true) or plain tar (false)       |
| `APP_EXCLUDE`          | `exclude` (backup)            | list           | (empty)     | comma separated glob patterns         |
| `APP_NOTIFY_EMAIL`     | `notify_email` (notify)       | str, optional  | (none)      | no mail is sent when unset            |
| `APP_SMTP_HOST`        | `smtp_host` (notify)          | str            | localhost   |                                       |
| `APP_SMTP_PORT`        | `smtp_port` (notify)          | int            | 25          |                                       |
| `APP_LOG_LEVEL`        | `log_level` (logging)         | str            | INFO        | DEBUG, INFO, WARNING, ERROR, CRITICAL |
| `APP_LOG_FILE`         | `log_file` (logging)          | path, optional | (none)      | also log to this file                 |

Booleans accept `true/false`, `yes/no`, `on/off` and `1/0` (any case).
A missing required setting stops backupd with a configuration error naming
the variable to set.

Example (container):

```
docker run -e APP_SOURCE_DIR=/data -e APP_BACKUP_DIR=/backups \
           -e APP_EXCLUDE="*.tmp,node_modules" -e APP_COMPRESS=yes backupd run
```

## Development

```
python -m pytest
```

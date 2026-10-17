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

backupd reads `settings.ini` from the working directory. Copy
`settings.ini.example` to get started.

| Section   | Key                | Type           | Default     | Notes                                  |
|-----------|--------------------|----------------|-------------|----------------------------------------|
| backup    | `source_dir`       | path           | (required)  | directory to back up                   |
| backup    | `backup_dir`       | path           | (required)  | where archives are written             |
| backup    | `interval_minutes` | int            | 60          | minimum time between runs              |
| backup    | `retention_days`   | int            | 7           | older archives are deleted             |
| backup    | `compress`         | bool           | true        | zip (true) or plain tar (false)        |
| backup    | `exclude`          | list           | (empty)     | comma separated glob patterns          |
| notify    | `notify_email`     | str, optional  | (none)      | no mail is sent when empty             |
| notify    | `smtp_host`        | str            | localhost   |                                        |
| notify    | `smtp_port`        | int            | 25          |                                        |
| logging   | `log_level`        | str            | INFO        | DEBUG, INFO, WARNING, ERROR, CRITICAL  |
| logging   | `log_file`         | path, optional | (none)      | also log to this file                  |

Booleans accept `true/false`, `yes/no`, `on/off` and `1/0`.

## Development

```
python -m pytest
```

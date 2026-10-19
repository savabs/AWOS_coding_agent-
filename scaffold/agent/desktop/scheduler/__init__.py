"""WP9: local scheduler + morning digest for the private Mac agent.

    jobs.py     ~/.awos/jobs.toml parsing, schedules, due calculation (DST, catch-up once)
    runner.py   admission (power/idle/lock/lease/memory), model lock, llama-server
                start/stop, checkpoint/resume, run records (JSONL), approvals queue
    launchd.py  LaunchAgent plist text + the owner's install/uninstall commands
    digest.py   morning digest: local HTML + plain text, redacted, offline
    __main__.py CLI: list | run <job> | run-due | digest | plist | status

Spec: docs/specs/wp9_scheduler_digest.md
"""

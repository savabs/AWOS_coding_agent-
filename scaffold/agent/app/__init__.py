"""
AWOS interactive terminal app: `awos` (session) and `awos "goal"` (one-shot).

A friendly front end over the host queue and background worker; see
docs/specs/awos_app.md.
"""

from .session import App, run_oneshot, run_session

__all__ = ["App", "run_session", "run_oneshot"]

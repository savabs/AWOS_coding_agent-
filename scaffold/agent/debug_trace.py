"""Lightweight execution tracer — no dependencies, ~50 LOC.

Usage:
    from scaffold.agent.debug_trace import trace, trace_methods

    @trace
    def slow_function():
        ...

    @trace_methods  # wraps every method with timing
    class MyClass:
        def method1(self): ...
        def method2(self): ...

Each call prints: [TRACE] MyClass.method1() started ...done (1.2s)

To dump all running Python tracebacks on SIGUSR1:
    import faulthandler, signal
    faulthandler.register(signal.SIGUSR1)
"""

from __future__ import annotations

import functools
import logging
import os
import time
from typing import Any, Callable, Optional

_log = logging.getLogger("awos.trace")

# Enable with AWOS_TRACE=1 (or 2 for verbose)
_TRACE_LEVEL = int(os.getenv("AWOS_TRACE", "0"))


def _should_trace() -> bool:
    return _TRACE_LEVEL > 0


def _now() -> float:
    return time.monotonic()


def trace(fn: Optional[Callable] = None, *, label: Optional[str] = None):
    """Decorator: log function entry, exit, and timing.

    @trace
    def my_func(): ...

    @trace(label="custom_name")
    def my_func(): ...
    """
    if fn is None:
        return lambda f: trace(f, label=label)

    name = label or fn.__qualname__

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not _should_trace():
            return fn(*args, **kwargs)
        t0 = _now()
        print(f"[TRACE] {name}() started", flush=True)
        try:
            result = fn(*args, **kwargs)
            dt = _now() - t0
            if dt > 0.5:
                print(f"[TRACE] {name}() ...done ({dt:.1f}s)", flush=True)
            return result
        except Exception:
            dt = _now() - t0
            print(f"[TRACE] {name}() ...FAILED after {dt:.1f}s", flush=True)
            raise
    return wrapper


def trace_methods(cls: type) -> type:
    """Class decorator: apply @trace to every non-dunder method."""
    for name, method in list(cls.__dict__.items()):
        if name.startswith("_"):
            continue
        if callable(method):
            setattr(cls, name, trace(method, label=f"{cls.__name__}.{name}"))
    return cls


def startup_banner() -> None:
    """Print git commit + process info so we know which code is running."""
    import subprocess
    import sys
    commit = "unknown"
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            commit = r.stdout.strip()
    except Exception:
        pass
    print(
        f"[DEBUGGER] PID={os.getpid()} commit={commit} "
        f"python={sys.version.split()[0]} AWOS_TRACE={_TRACE_LEVEL}",
        flush=True,
    )


def install_fault_handler() -> None:
    """Install SIGUSR1 handler: dumps all thread tracebacks to stderr."""
    try:
        import faulthandler
        import signal
        faulthandler.register(signal.SIGUSR1)
        print("[DEBUGGER] faulthandler installed — kill -USR1 <pid> for traceback", flush=True)
    except Exception:
        pass

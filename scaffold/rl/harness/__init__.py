"""
scaffold.rl.harness — rollout harness for computer-use RL (RL_PLAN.md week 1).

    axmap.py      Chromium CDP AX tree -> macOS-AX dict tree (what Safari gives observe.py)
    chromium.py   ChromiumBackend: MacBrowserBackend's interface over headless Chromium
    models.py     RecordingModel (-> contract Steps), OracleModel, SampledLocalModel
    episode.py    run_episode(source, task, model, ...) -> (Trajectory, Score); admit(...)
    runner.py     CLI: run / summarize / admit / calibrate (python -m scaffold.rl.harness ...)
    demo.py       DemoSource: 3 hand-written tasks (form, trap, checkpoint)
    calibrate.py  dev_dashboard pages: Chromium AX path vs HTML path vs the FakeSafari model

Environment (Playwright has no Python 3.15 wheels; the harness runs on 3.12):

    python3.12 -m venv scaffold/rl/harness/.venv            # git-ignored (.venv/)
    scaffold/rl/harness/.venv/bin/pip install playwright==1.64.0 pytest pytest-timeout
    # no `playwright install` needed: find_chromium() uses the cached build in
    # ~/Library/Caches/ms-playwright (chromium_headless_shell-1234) or AWOS_RL_CHROMIUM
    PYTHONPATH=. scaffold/rl/harness/.venv/bin/python -m scaffold.rl.harness run --source demo \\
        --model oracle --out runs/rl/demo-oracle/
    PYTHONPATH=. scaffold/rl/harness/.venv/bin/python -m pytest tests/rl/test_harness*.py -m "integration or not integration"

Heavy pieces (Playwright) are imported lazily: importing this package needs only the stdlib
and the product code.
"""
from __future__ import annotations

__all__ = ["run_episode", "admit", "ChromiumBackend", "Chromium", "OracleModel", "RecordingModel",
           "SampledLocalModel", "DemoSource"]


def __getattr__(name):                               # lazy: no Playwright import at package import
    if name in ("run_episode", "admit"):
        from . import episode
        return getattr(episode, name)
    if name in ("ChromiumBackend", "Chromium"):
        from . import chromium
        return getattr(chromium, name)
    if name in ("OracleModel", "RecordingModel", "SampledLocalModel"):
        from . import models
        return getattr(models, name)
    if name == "DemoSource":
        from .demo import DemoSource
        return DemoSource
    raise AttributeError(name)

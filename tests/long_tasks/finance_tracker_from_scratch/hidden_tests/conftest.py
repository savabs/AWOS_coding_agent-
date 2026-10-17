"""Hidden-test setup for finance_tracker_from_scratch.

Copied into <project>/hidden_tests/ and run with the project root as cwd.
fintrack modules are imported lazily *inside* each test (through the `ft`
fixture) so that a missing module fails tests one by one instead of erroring
whole files at collection time — the per-feature partial-credit score depends
on that.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
for path in (os.path.dirname(HERE), HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

from ft_helpers import Lazy  # noqa: E402


@pytest.fixture
def ft():
    """ft.importers, ft.storage, ft.rules, ... imported on first use."""
    return Lazy()


@pytest.fixture
def write(tmp_path):
    """write(name, text) -> path of a UTF-8 file in tmp_path."""
    def _write(name, text, encoding="utf-8"):
        path = tmp_path / name
        path.write_text(text, encoding=encoding)
        return path
    return _write


@pytest.fixture
def ledger(ft, tmp_path):
    led = ft.storage.Ledger(tmp_path / "ledger.db")
    yield led
    led.close()

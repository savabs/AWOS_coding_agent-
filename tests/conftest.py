"""
Keep the unit suite hermetic: no test may reach a live model because of a key
that happens to be exported in the developer's shell. Tests that need a key
set a fake one themselves (monkeypatch / patch.dict), after this runs.

The goal check is off by default for the same reason: every execute_feature
test would otherwise build a real GoalChecker and a real model client. Its own
tests (test_goal_check.py) turn it back on with the checker mocked.

The project notebook is off too: an execute_feature test must neither read a
notebook left in ./.awos by a real run nor write one. test_project_notebook.py
turns it back on inside a temporary working directory.
"""
import os

import pytest

_PROVIDER_ENV = (
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENCODE_GO_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "AWOS_BASE_URL",
    "AWOS_PROVIDER",
)


@pytest.fixture(autouse=True)
def _restore_os_environ():
    """
    Undo direct os.environ writes a test (or the code under test) makes:
    deliverable_e2e._bootstrap_e2e_env sets AWOS_E2E/AWOS_CHEAP_ONLY/keys, and
    importing awos.py runs paths.apply_model_defaults(), which pins
    AWOS_*_MODEL to claude-*. Leaked into later tests, those sent
    test_orchestrator_e2e to the real Anthropic API (HTTP 401) in a full run.
    """
    saved = dict(os.environ)
    yield
    if dict(os.environ) != saved:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture(autouse=True)
def _cwd_outside_repo(tmp_path, monkeypatch):
    """
    Run every test from its own temp dir. Runtime state paths are relative to
    the cwd (".awos/budget.json", RuntimeSessionStore's ".awos/sessions",
    reward_store, traces, linucb weights, ...), so a test run from the repo
    root read the developer's real .awos state and rewrote the tracked files.
    Tests that need a particular cwd still chdir themselves.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _no_live_provider_keys(_restore_os_environ, monkeypatch):
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWOS_GOAL_CHECK", "0")
    monkeypatch.setenv("AWOS_NOTEBOOK", "0")
    # Tests never pop real macOS notifications or probe providers over HTTP.
    monkeypatch.setenv("AWOS_NOTIFY_MACOS", "0")
    monkeypatch.setenv("AWOS_HOST_KEY_CHECK", "0")

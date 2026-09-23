"""
Keep the unit suite hermetic: no test may reach a live model because of a key
that happens to be exported in the developer's shell. Tests that need a key
set a fake one themselves (monkeypatch / patch.dict), after this runs.

The goal check is off by default for the same reason: every execute_feature
test would otherwise build a real GoalChecker and a real model client. Its own
tests (test_goal_check.py) turn it back on with the checker mocked.
"""
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
def _no_live_provider_keys(monkeypatch):
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWOS_GOAL_CHECK", "0")

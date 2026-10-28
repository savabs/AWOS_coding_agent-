"""A GITHUB_TOKEN from AWOS's .env must not hide a working `gh` login."""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.app import fix as fixmod  # noqa: E402


def test_check_gh_ignores_shadowing_token_env(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        env = kw.get("env")
        calls.append(env)
        ok = env is not None and "GITHUB_TOKEN" not in env
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, "", "" if ok else "bad token")

    monkeypatch.setenv("GITHUB_TOKEN", "stale")
    monkeypatch.setattr(fixmod.subprocess, "run", fake_run)
    monkeypatch.setattr(fixmod, "_gh_drop_tokens", False)
    fixmod.check_gh(fixmod.default_runner, which=lambda _n: "/usr/bin/gh")
    assert fixmod._gh_drop_tokens is True
    assert calls[0] is None and "GITHUB_TOKEN" not in calls[1]
    # Later gh calls run without the token; other commands keep the full env.
    fixmod.default_runner(["gh", "issue", "view"])
    assert "GITHUB_TOKEN" not in calls[-1]
    fixmod.default_runner(["git", "status"])
    assert calls[-1] is None


def test_check_gh_still_fails_when_logged_out(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "stale")
    monkeypatch.setattr(fixmod.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "logged out"))
    monkeypatch.setattr(fixmod, "_gh_drop_tokens", False)
    with pytest.raises(fixmod.FixError):
        fixmod.check_gh(fixmod.default_runner, which=lambda _n: "/usr/bin/gh")
    assert fixmod._gh_drop_tokens is False

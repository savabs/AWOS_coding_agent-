"""usage_ping: opt-in consent, strict payload, never raises, off under pytest / DO_NOT_TRACK."""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from scaffold.agent import onboarding, usage_ping

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("goal", "repo", "repo_path", "path", "prompt", "code", "diff", "branch", "summary",
             "error", "id", "job_id", "cost_usd", "hostname", "user", "email", "key")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_HOME", str(tmp_path / "awoshome"))
    monkeypatch.delenv("AWOS_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("AWOS_TELEMETRY_URL", "https://ping.invalid/api/ping")
    sent = []
    monkeypatch.setattr(usage_ping, "transport", lambda url, payload, timeout: sent.append(
        (url, payload, timeout)) or True)
    return SimpleNamespace(root=tmp_path / "awoshome", sent=sent)


@pytest.fixture
def allow(monkeypatch):
    monkeypatch.setattr(usage_ping, "ALLOW_IN_TESTS", True)


def _job(**kw):
    base = dict(id="0123abcd-secret-job", goal="fix the payment bug in /Users/me/acme",
                repo_path="/Users/me/acme", privacy="cloud_ok",
                created_at="2026-10-15T10:00:00+00:00", started_at="2026-10-15T10:00:00+00:00",
                finished_at="2026-10-15T10:03:00+00:00")
    base.update(kw)
    return SimpleNamespace(**base)


def _wait_sent(home, n):
    import time
    for _ in range(100):
        if len(home.sent) >= n:
            return
        time.sleep(0.01)


# ── consent ──────────────────────────────────────────────────────────────────


def test_default_is_off_and_never_asked(home, allow):
    assert usage_ping.consent() is None
    assert usage_ping.enabled() is False
    assert usage_ping.send("install", wait=True) is False
    assert home.sent == []


def test_on_makes_random_uuid4_off_forgets_it(home, allow):
    data = usage_ping.set_consent(True)
    iid = data["install_id"]
    assert uuid.UUID(iid).version == 4
    assert usage_ping.enabled()
    stored = json.loads((home.root / "telemetry.json").read_text())
    assert stored["enabled"] is True and stored["install_id"] == iid
    usage_ping.set_consent(False)
    assert usage_ping.enabled() is False
    assert "install_id" not in json.loads((home.root / "telemetry.json").read_text())
    assert usage_ping.set_consent(True)["install_id"] != iid


def test_disabled_under_pytest_by_default(home):
    usage_ping.set_consent(True)
    assert usage_ping.enabled() is False  # ALLOW_IN_TESTS not set
    assert usage_ping.send("install", wait=True) is False
    assert usage_ping.job_finished(_job(), "done", {"cost_usd": 0.01}) is False
    assert home.sent == []


@pytest.mark.parametrize("var,val", [("DO_NOT_TRACK", "1"), ("AWOS_TELEMETRY", "0"),
                                     ("AWOS_TELEMETRY", "off"), ("DO_NOT_TRACK", "true")])
def test_env_switches_win_over_consent(home, allow, monkeypatch, var, val):
    usage_ping.set_consent(True)
    monkeypatch.setenv(var, val)
    assert usage_ping.enabled() is False
    assert usage_ping.send("install", wait=True) is False
    assert usage_ping.status()["env_override"]
    assert home.sent == []


def test_consent_prompt_asks_once_default_no(home, allow):
    asked, said = [], []
    assert usage_ping.maybe_ask_consent(ask=lambda p: asked.append(p) or "", say=said.append) is False
    assert len(asked) == 1 and usage_ping.consent() is False
    assert usage_ping.maybe_ask_consent(ask=lambda p: asked.append(p) or "y", say=said.append) is False
    assert len(asked) == 1  # not asked again


def test_consent_prompt_yes_sends_install(home, allow):
    assert usage_ping.maybe_ask_consent(ask=lambda p: "y", say=lambda s: None) is True
    _wait_sent(home, 1)
    assert home.sent[0][1]["event"] == "install"


def test_non_interactive_never_asks_and_stays_off(home, allow):
    def boom(prompt):
        raise AssertionError("must not prompt")
    assert usage_ping.maybe_ask_consent(ask=boom, interactive=False) is None
    assert usage_ping.enabled() is False


def test_onboarding_asks_only_when_interactive(home, allow, tmp_path, monkeypatch):
    for v in ("ANTHROPIC_API_KEY", "AWOS_PROVIDER"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    repo = tmp_path / "r"
    repo.mkdir()
    prompts = []
    onboarding.ensure_ready(repo, interactive=True, ask=lambda p: prompts.append(p) or "n",
                            say=lambda s: None)
    assert any("usage counts" in p for p in prompts)
    assert usage_ping.consent() is False
    prompts.clear()
    onboarding.ensure_ready(repo, interactive=True, ask=lambda p: prompts.append(p) or "n",
                            say=lambda s: None)
    assert prompts == []


# ── payload ──────────────────────────────────────────────────────────────────


def test_job_finished_payload_has_only_allowed_fields(home, allow):
    usage_ping.set_consent(True)
    result = {"cost_usd": 0.004, "summary": "secret summary", "goal": "x", "workspace": "/Users/me"}
    assert usage_ping.job_finished(_job(), "done", result, spent=0.003) is True
    _wait_sent(home, 2)
    events = {p["event"]: p for _, p, _ in home.sent}
    assert set(events) == {"first_run", "job_finished"}
    p = events["job_finished"]
    assert set(p) <= set(usage_ping.ALLOWED_FIELDS)
    assert p["outcome"] == "solved" and p["duration"] == "1to5m" and p["cost"] == "lt1c"
    assert p["provider"] in ("anthropic", "local", "other")
    for f in FORBIDDEN:
        assert f not in p
    blob = json.dumps([x[1] for x in home.sent])
    for secret in ("payment", "/Users/me", "acme", "0123abcd", "secret summary"):
        assert secret not in blob
    assert all(url == "https://ping.invalid/api/ping" and t == 2.0 for url, _, t in home.sent)
    # first_run only once
    home.sent.clear()
    usage_ping.job_finished(_job(), "failed", {})
    _wait_sent(home, 1)
    assert [p["event"] for _, p, _ in home.sent] == ["job_finished"]
    assert home.sent[0][1]["outcome"] == "failed"


def test_build_payload_drops_unknown_and_bad_values():
    iid = str(uuid.uuid4())
    p = usage_ping.build_payload("job_finished", iid, outcome="rm -rf", duration="forever",
                                 cost="lots", provider="openai", goal="secret", path="/x")
    assert set(p) == {"v", "event", "install_id", "awos_version", "os"}
    assert usage_ping.build_payload("job_finished", "not-a-uuid") is None
    assert usage_ping.build_payload("something_else", iid) is None
    assert usage_ping.build_payload("job_finished", iid, outcome="cancelled")["outcome"] == "cancelled"


@pytest.mark.parametrize("secs,bucket", [(0, "lt1m"), (59, "lt1m"), (60, "1to5m"), (899, "5to15m"),
                                         (3599, "15to60m"), (4000, "gt60m"), (None, None), (-1, None)])
def test_duration_buckets(secs, bucket):
    assert usage_ping.duration_bucket(secs) == bucket


@pytest.mark.parametrize("usd,bucket", [(0, "zero"), (0.004, "lt1c"), (0.05, "1to10c"), (0.5, "10cto1"),
                                        (1.0, "10cto1"), (3, "gt1"), ("bad", None), (None, None)])
def test_cost_buckets(usd, bucket):
    assert usage_ping.cost_bucket(usd) == bucket


def test_provider_kind():
    assert usage_ping.provider_kind({"AWOS_PROVIDER": "local"}) == "local"
    assert usage_ping.provider_kind({"AWOS_AGENT_MODEL": "local/qwen"}) == "local"
    assert usage_ping.provider_kind({"ANTHROPIC_API_KEY": "k"}, privacy="local_only") == "local"
    assert usage_ping.provider_kind({"ANTHROPIC_API_KEY": "k"}) == "anthropic"
    assert usage_ping.provider_kind({"AWOS_AGENT_MODEL": "claude-haiku-5-5"}) == "anthropic"
    assert usage_ping.provider_kind({"OPENROUTER_API_KEY": "k", "AWOS_AGENT_MODEL": "deepseek/x"}) == "other"


# ── never raises / never blocks ──────────────────────────────────────────────


def test_never_raises(home, allow, monkeypatch):
    usage_ping.set_consent(True)

    def explode(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(usage_ping, "transport", explode)
    assert usage_ping.send("install", wait=True) is False
    (home.root / "telemetry.json").write_text("{not json")
    assert usage_ping.enabled() is False
    assert usage_ping.job_finished(None, "done", None) in (False, True)
    assert usage_ping.maybe_ask_consent(ask=explode) in (None, False)


def test_real_transport_times_out_quickly_and_returns_false():
    import time
    t = time.monotonic()
    # 10.255.255.1 is non-routable: connect hangs until the timeout.
    assert usage_ping._post("http://10.255.255.1/api/ping", {"v": 1}, timeout=0.3) is False
    assert time.monotonic() - t < 2.5


def test_cli_telemetry_status_runs(tmp_path):
    out = subprocess.run([sys.executable, str(ROOT / "awos.py"), "telemetry", "status"],
                         capture_output=True, text=True, cwd=str(ROOT), timeout=60,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(ROOT / "nonexistent-home"),
                              "AWOS_HOME": str(tmp_path / "h")})
    assert out.returncode == 0, out.stderr
    assert "Anonymous usage counts: off" in out.stdout

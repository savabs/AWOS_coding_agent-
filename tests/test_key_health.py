"""key_health: classify providers without spending; never leak a key (HTTP mocked)."""
from __future__ import annotations

import json

from scaffold.agent import key_health as kh

SECRET = "sk-ant-api03-SECRETSECRETSECRET1234567890"


def fake_get(table):
    """table: url-substring -> (status, body); records calls."""
    calls = []

    def get(url, headers, timeout):
        calls.append((url, dict(headers)))
        for frag, resp in table.items():
            if frag in url:
                return resp
        return None, "URLError"
    get.calls = calls
    return get


def test_anthropic_ok_unauthorized_unreachable_not_configured():
    env = {"ANTHROPIC_API_KEY": SECRET}
    get = fake_get({"/v1/models": (200, "{}")})
    c = kh.check_anthropic(env, get=get)
    assert c.status == kh.OK
    url, headers = get.calls[0]
    assert url.startswith("https://api.anthropic.com/v1/models")
    assert headers["x-api-key"] == SECRET and headers["anthropic-version"]

    assert kh.check_anthropic(env, get=fake_get({"/v1/models": (401, "invalid x-api-key")})).status == kh.UNAUTHORIZED
    assert kh.check_anthropic(env, get=fake_get({"/v1/models": (403, "")})).status == kh.UNAUTHORIZED
    assert kh.check_anthropic(env, get=fake_get({})).status == kh.UNREACHABLE
    assert kh.check_anthropic(env, get=fake_get({"/v1/models": (529, "")})).status == kh.UNREACHABLE
    assert kh.check_anthropic(env, get=fake_get({"/v1/models": (429, "")})).status == kh.OK
    nc = kh.check_anthropic({}, get=fake_get({}))
    assert nc.status == kh.NOT_CONFIGURED and "ANTHROPIC_API_KEY" in nc.action


def test_openrouter_only_checked_when_a_key_is_set():
    get = fake_get({"openrouter.ai/api/v1/key": (401, "User not found.")})
    assert kh.check_openrouter({"OPENROUTER_API_KEY": ""}, get=get).status == kh.NOT_CONFIGURED
    assert get.calls == []  # no request without a key
    assert kh.check_openrouter({"OPENROUTER_API_KEY": "sk-or-v1-abc"}, get=get).status == kh.UNAUTHORIZED


def test_local_health_endpoint():
    get = fake_get({"127.0.0.1:8080/health": (200, '{"status":"ok"}')})
    assert kh.check_local({}, get=get).status == kh.OK
    assert get.calls[0][0] == "http://127.0.0.1:8080/health"
    # no server, no URL configured -> not-configured; configured but down -> unreachable
    assert kh.check_local({}, get=fake_get({})).status == kh.NOT_CONFIGURED
    down = kh.check_local({"AWOS_LOCAL_BASE_URL": "http://10.0.0.5:9000/v1"}, get=fake_get({}))
    assert down.status == kh.UNREACHABLE and "10.0.0.5:9000" in down.detail


def test_output_never_contains_the_key(capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setattr(kh, "http_get", fake_get({"/v1/models": (401, f"bad key {SECRET}")}))
    monkeypatch.setattr(kh, "CHECKS", {"anthropic": lambda env, get=None: kh.check_anthropic(env, get=kh.http_get)})
    checks = kh.check_all(providers=["anthropic"])
    text = kh.format_table(checks) + json.dumps([c.to_dict() for c in checks])
    assert SECRET not in text
    assert kh.main() == 1 and SECRET not in capsys.readouterr().out


def test_credentials_problem_rules():
    C = kh.KeyCheck
    ok = [C("anthropic", kh.OK, "A", ""), C("local", kh.NOT_CONFIGURED, "L", ""),
          C("openrouter", kh.NOT_CONFIGURED, "O", "")]
    assert kh.credentials_problem(ok) is None
    bad = [C("anthropic", kh.UNAUTHORIZED, "A", "HTTP 401", "replace it")] + ok[1:]
    p = kh.credentials_problem(bad)
    assert p["provider"] == "anthropic" and p["action"] == "replace it"
    # an outage is not a credentials block
    assert kh.credentials_problem([C("anthropic", kh.UNREACHABLE, "A", "")] + ok[1:]) is None
    # nothing configured at all blocks a cloud job
    none = [C("anthropic", kh.NOT_CONFIGURED, "A", ""), C("local", kh.NOT_CONFIGURED, "L", ""),
            C("openrouter", kh.NOT_CONFIGURED, "O", "")]
    assert kh.credentials_problem(none)["status"] == kh.NOT_CONFIGURED
    # a local_only job ignores a bad cloud key
    assert kh.credentials_problem(bad, privacy="local_only") is None


def test_effective_env_dotenv_fills_only_missing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('ANTHROPIC_API_KEY="from-dotenv"\nOTHER=1\n')
    assert kh.effective_env({}, env_file)["ANTHROPIC_API_KEY"] == "from-dotenv"
    assert kh.effective_env({"ANTHROPIC_API_KEY": "bogus"}, env_file)["ANTHROPIC_API_KEY"] == "bogus"
    assert kh.effective_env({"ANTHROPIC_API_KEY": ""}, env_file)["ANTHROPIC_API_KEY"] == ""


def test_auth_error_patterns():
    yes = ["Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', "
           "'message': 'invalid x-api-key'}}",
           "anthropic.AuthenticationError: ...", "HTTP 403 Forbidden", "User not found."]
    no = ["ran out of turns (max_turns)", "2 tests failed", "line 401 of foo.py", ""]
    assert all(kh.looks_like_auth_error(t) for t in yes)
    assert not any(kh.looks_like_auth_error(t) for t in no)

"""
AWOS_PROVIDER=local — every client goes to one local OpenAI-compatible server.

docs/specs/local_provider_spec.md. Unset, nothing changes (OpenRouter wins
whenever its key is set); set, the OpenRouter key in .env is ignored, the cost
is $0 and token counts are kept.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scaffold"))

from agent import providers  # noqa: E402
from agent.agent_loop import (  # noqa: E402
    OpenAIToolClient,
    build_client_from_env,
    estimate_cost,
)


@pytest.fixture(autouse=True)
def _clean_local_env(monkeypatch):
    for name in ("AWOS_LOCAL_BASE_URL", "AWOS_LOCAL_MODEL", "AWOS_LOCAL_API_KEY",
                 "AWOS_AGENT_MODEL", "AWOS_BASE_URL", "AWOS_LOCAL_CONTEXT",
                 "AWOS_ONE_SHOT_BUDGET_TOKENS"):
        monkeypatch.delenv(name, raising=False)


def _base(client) -> str:
    return str(client.base_url)


# ── selection ────────────────────────────────────────────────────────────────


def test_unset_provider_keeps_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert not providers.local_mode()
    assert "openrouter.ai" in _base(providers.chat_client())
    assert providers.openrouter_key() == "sk-or-test"
    client = build_client_from_env("deepseek/deepseek-v4-flash")
    assert client.openrouter and client.model == "deepseek/deepseek-v4-flash"


def test_local_wins_over_openrouter_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    chat = providers.chat_client(max_retries=0)
    assert providers.is_local_client(chat)
    assert not providers._is_routed(chat)          # no OpenRouter reasoning field
    assert _base(chat).startswith("http://127.0.0.1:8080/v1")
    assert providers.openrouter_key() is None
    msgs = providers.messages_client("direct-anthropic-key")
    assert providers.is_local_client(msgs)
    assert _base(msgs).startswith("http://127.0.0.1:8080")
    assert "/v1" not in _base(msgs).rstrip("/")[-3:]


def test_local_without_any_key(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    client = build_client_from_env()
    assert isinstance(client, OpenAIToolClient)
    assert not client.openrouter
    assert client.model == "local/qwen3.5-9b"


def test_agent_model_local_prefix_selects_local(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("AWOS_AGENT_MODEL", "local/qwen3-8b")
    assert providers.local_mode()
    client = build_client_from_env()
    assert client.model == "local/qwen3-8b"
    assert "127.0.0.1" in str(client._client.base_url)


def test_cloud_ids_map_to_the_loaded_model(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    monkeypatch.setenv("AWOS_LOCAL_MODEL", "my-model")
    assert providers.local_model_id("deepseek/deepseek-v4-flash") == "local/my-model"
    assert providers.local_wire_model("deepseek-v4-flash") == "my-model"
    assert providers.local_model_id("local/other") == "local/other"
    assert build_client_from_env("deepseek/deepseek-v4-flash").model == "local/my-model"


def test_base_url_override(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    monkeypatch.setenv("AWOS_LOCAL_BASE_URL", "http://127.0.0.1:9999/v1/")
    assert providers.local_base_url() == "http://127.0.0.1:9999/v1"
    assert ":9999" in _base(providers.chat_client())
    assert ":9999" in str(build_client_from_env()._client.base_url)


def test_cost_is_zero_and_tokens_kept(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    # Even a priced cloud id costs nothing when it is served locally.
    assert estimate_cost("deepseek/deepseek-v4-flash", 1_000_000, 1_000_000) == 0.0
    assert estimate_cost("local/qwen3.5-9b", 1_000_000, 1_000_000) == 0.0
    monkeypatch.delenv("AWOS_PROVIDER")
    assert estimate_cost("deepseek/deepseek-v4-flash", 1_000_000, 1_000_000) > 0


def test_one_shot_budget_fits_local_context(monkeypatch):
    from agent import one_shot
    assert one_shot.budget_tokens() == one_shot.DEFAULT_BUDGET_TOKENS
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    assert one_shot.budget_tokens() == 24576 // 2
    monkeypatch.setenv("AWOS_ONE_SHOT_BUDGET_TOKENS", "20000")
    assert one_shot.budget_tokens() == 20000


# ── against a fake local server ──────────────────────────────────────────────


class _FakeOpenAI(BaseHTTPRequestHandler):
    requests: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"path": self.path, "body": body,
                                    "auth": self.headers.get("Authorization")})
        message: dict = {"role": "assistant", "content": "ok"}
        finish = "stop"
        if body.get("tools"):
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": body["tools"][0]["function"]["name"],
                             "arguments": json.dumps({"status": "ready"})}}]}
            finish = "tool_calls"
        reply = {"id": "x", "object": "chat.completion", "created": 0,
                 "model": "served-alias",
                 "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                 "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49}}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_server():
    _FakeOpenAI.requests = []
    server = HTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1", _FakeOpenAI.requests
    server.shutdown()


def test_utility_chat_against_fake_server(monkeypatch, fake_server, tmp_path):
    url, requests = fake_server
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    monkeypatch.setenv("AWOS_LOCAL_BASE_URL", url)
    monkeypatch.setenv("AWOS_LOCAL_MODEL", "served-alias")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-must-not-be-sent")
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", str(log))
    text, info = providers.utility_chat(
        providers.chat_client(max_retries=0), "acceptance", "deepseek/deepseek-v4-flash",
        [{"role": "user", "content": "hi"}])
    assert text == "ok" and info["cost_usd"] == 0.0
    sent = requests[-1]
    assert sent["body"]["model"] == "served-alias"
    assert "reasoning" not in sent["body"]
    assert "sk-or" not in (sent["auth"] or "")
    line = json.loads(log.read_text().splitlines()[-1])
    assert line["provider"] == "local"
    assert line["cost_usd"] == 0.0
    assert line["input_tokens"] == 42 and line["output_tokens"] == 7


def test_check_backend_passes_on_fake_local_server(fake_server):
    url, requests = fake_server
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AWOS_", "OPENROUTER", "PYTEST"))}
    env.update({"AWOS_PROVIDER": "local", "AWOS_LOCAL_BASE_URL": url,
                "AWOS_AGENT_MODEL": "deepseek/deepseek-v4-flash",
                "OPENROUTER_API_KEY": "sk-or-must-not-be-used",
                "AWOS_LLM_CALL_LOG": "0"})
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "check_backend.py")],
                          capture_output=True, text=True, timeout=60, env=env,
                          cwd=str(REPO))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "provider  local" in proc.stdout
    assert "local/qwen3.5-9b" in proc.stdout
    assert "(~$0.00000)" in proc.stdout
    assert requests and requests[-1]["body"]["model"] == "qwen3.5-9b"


def test_eval_health_allows_the_local_model():
    sys.path.insert(0, str(REPO / "scripts"))
    import eval_health
    pin = {"model": "deepseek/deepseek-v4-flash",
           "env": {"AWOS_AGENT_MODEL": "deepseek/deepseek-v4-flash"}}
    allowed = eval_health.default_allowed_models({"model_pin": pin}, set())
    assert not eval_health._model_ok("qwen3.5-9b", allowed)
    local_pin = {**pin, "routing": {"AWOS_PROVIDER": "local"}}
    allowed = eval_health.default_allowed_models({"model_pin": local_pin}, set())
    assert eval_health._model_ok("local/qwen3.5-9b", allowed)
    assert eval_health._model_ok("qwen3.5-9b", allowed)
    assert eval_health._model_ok("deepseek/deepseek-v4-flash", allowed)


def test_check_backend_reports_server_down():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AWOS_", "OPENROUTER", "PYTEST"))}
    env.update({"AWOS_PROVIDER": "local", "AWOS_LOCAL_BASE_URL": "http://127.0.0.1:9/v1",
                "AWOS_MODEL_MAX_RETRIES": "0", "AWOS_LLM_CALL_LOG": "0"})
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "check_backend.py")],
                          capture_output=True, text=True, timeout=60, env=env,
                          cwd=str(REPO))
    assert proc.returncode == 1
    assert "local_model.sh start" in proc.stdout

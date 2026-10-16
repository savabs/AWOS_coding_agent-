"""
Capture the exact request kwargs the loop executor sends, for golden checks
(tests/test_prefix_stability.py, tests/test_loop_grammar.py).

tests/fixtures/loop_requests_golden.json was written by `capture_all()` on
the code BEFORE AWOS_LOOP_GRAMMAR / cache telemetry existed (bench-run
634bd7d), so a test comparing against it proves those paths still send the
same bytes. Regenerate only on a deliberate prompt change:

    python tests/loop_request_capture.py > tests/fixtures/loop_requests_golden.json
"""
from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scaffold.agent import loop_executor as lx  # noqa: E402
from scaffold.agent.agent_loop import AnthropicToolClient, OpenAIToolClient  # noqa: E402
from scaffold.agent.sandbox import SandboxResult  # noqa: E402

GOLDEN = REPO / "tests" / "fixtures" / "loop_requests_golden.json"
UNAME = SimpleNamespace(system="Linux", release="6.1.0", machine="x86_64")
GOAL = "Fix add() in calc.py: it returns a - b, it must return a + b."
MARK = lx.SUBMIT_MARKER


class FixedSandbox:
    backend = "fake"

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def run(self, command, timeout_sec=120):
        return SandboxResult(0, "ok\n", "", False, 0.0)

    def close(self):
        pass


class AnthropicSDK:
    """Fake Anthropic SDK: bash, bash, submit."""

    def __init__(self) -> None:
        self.calls: list = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=str)))
        n = len(self.calls)
        if n < 3:
            content = [SimpleNamespace(type="text", text=f"step {n}"),
                       SimpleNamespace(type="tool_use", id=f"tu{n}", name="bash",
                                       input={"command": f"cat calc.py # {n}"})]
            stop = "tool_use"
        else:
            content = [SimpleNamespace(type="text", text=f"{MARK}\nFixed.")]
            stop = "end_turn"
        usage = SimpleNamespace(input_tokens=50, output_tokens=10,
                                cache_read_input_tokens=40 * (n > 1),
                                cache_creation_input_tokens=10)
        return SimpleNamespace(content=content, usage=usage, stop_reason=stop,
                               model="claude-haiku-5-5")


class OpenAISDK:
    """Fake OpenAI-compatible SDK (tool calls): bash, bash, submit."""

    def __init__(self) -> None:
        self.calls: list = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=str)))
        n = len(self.calls)
        if n < 3:
            msg = SimpleNamespace(content=f"step {n}", tool_calls=[SimpleNamespace(
                id=f"oc{n}", function=SimpleNamespace(
                    name="bash", arguments=json.dumps({"command": f"cat calc.py # {n}"})))])
            finish = "tool_calls"
        else:
            msg = SimpleNamespace(content=f"{MARK}\nFixed.", tool_calls=None)
            finish = "stop"
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)],
                               usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10),
                               model="qwen")


def run_capture(make_client) -> list:
    """Run the loop on a fixed 3-step script; return the SDK's request kwargs."""
    with tempfile.TemporaryDirectory() as ws, patch.object(platform, "uname",
                                                           return_value=UNAME):
        client, sdk = make_client()
        registry = lx.build_loop_registry(FixedSandbox(ws))
        lx.LoopExecutor(client, registry, GOAL, workspace=ws,
                        gate=lambda f, s: {"green": True}).run()
        return sdk.calls


def capture_anthropic() -> list:
    def make():
        sdk = AnthropicSDK()
        return AnthropicToolClient(sdk, "claude-haiku-5-5", max_tokens=4096), sdk
    return run_capture(make)


def capture_openai_tools() -> list:
    def make():
        sdk = OpenAISDK()
        return OpenAIToolClient(sdk, "local/qwen3.5-9b", max_tokens=4096), sdk
    return run_capture(make)


def capture_all() -> dict:
    saved = {k: os.environ.pop(k, None) for k in
             ("AWOS_ANTHROPIC_CACHE", "AWOS_REASONING_AGENT", "AWOS_LOOP_BREAKER",
              "AWOS_PROVIDER", "AWOS_AGENT_MODEL", "AWOS_LOOP_GRAMMAR")}
    try:
        return {"anthropic": capture_anthropic(), "openai_tools": capture_openai_tools()}
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


if __name__ == "__main__":
    print(json.dumps(capture_all(), indent=1))

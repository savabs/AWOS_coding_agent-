"""
check_openrouter_routing.py — live proof for docs/specs/openrouter_only_spec.md.

    python3 scripts/check_openrouter_routing.py      # ~$0.001

Direct provider keys stay loaded from .env; with OPENROUTER_API_KEY set, every
request must still go to openrouter.ai. Every HTTP request is recorded.
"""
import collections
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scaffold"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv

load_dotenv(REPO / ".env")

import httpx

requests_seen = []


def _hook(module):
    # Transport level: every request an SDK sends through `module` passes here.
    orig = module.HTTPTransport.handle_request

    def handle(self, request):
        response = orig(self, request)
        requests_seen.append((module.__name__, request.method, str(request.url), response.status_code))
        return response

    module.HTTPTransport.handle_request = handle


_hook(httpx)  # anthropic SDK
try:
    import httpx2  # openai>=3 vendors its own copy of httpx
    _hook(httpx2)
except ImportError:
    pass

present = [k for k in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                       "OPENCODE_GO_API_KEY", "GEMINI_API_KEY") if os.getenv(k)]
print("direct keys also loaded:", present)

# 1. Single-shot Worker on a real benchmark case (chat_client path).
import bench_executors as B
from agent.bench_cases import load_case
from agent.worker import Worker

case = load_case(REPO / "tests/bug_cases/a1_off_by_one_page")
result = B.run_single_shot(case, Worker())
print(f"[1] Worker single-shot a1: solved={result.solved} "
      f"tokens={result.input_tokens}/{result.output_tokens} {result.detail}")

# 2. Anthropic-SDK shape via OpenRouter's Messages endpoint (messages_client).
from agent.providers import messages_client

reply = messages_client(os.getenv("ANTHROPIC_API_KEY")).messages.create(
    model="claude-haiku-4-5", max_tokens=10, system="Reply with one word.",
    messages=[{"role": "user", "content": "Say ready"}],
)
print(f"[2] messages_client claude-haiku-4-5: {reply.content[0].text!r} "
      f"usage={reply.usage.input_tokens}/{reply.usage.output_tokens}")

# 3. The orchestrator's planner client (qwen3.7-plus).
from agent.cheap_planner import CheapPlanner

planner = CheapPlanner()
resp = planner.client.chat.completions.create(
    model=planner.model_name, max_tokens=1000,
    messages=[{"role": "user", "content": "Reply with the word ready."}],
)
print(f"[3] CheapPlanner {planner.model_name}: {(resp.choices[0].message.content or '')[:40]!r}")

print("\nHTTP requests:")
hosts = collections.Counter()
for lib, method, url, status in requests_seen:
    print(f"  [{lib}] {method} {url} -> {status}")
    hosts[httpx.URL(url).host] += 1
print("\nhosts contacted:", dict(hosts))
ok = set(hosts) == {"openrouter.ai"}
print("ONLY openrouter.ai:", ok)
sys.exit(0 if ok else 1)

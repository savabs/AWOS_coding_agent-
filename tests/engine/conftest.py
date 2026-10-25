"""EngineBench tests: a fake OpenAI-compatible llama-server (fake timings, a fake prefix cache) and fake
trajectories. No network beyond 127.0.0.1, no model weights."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "scripts" / "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

OBS = ("URL path: /t/i1/x  (HTTP 200)\nTitle: T\nStatus: {status}\nPage text:\nhello\n"
       "Controls (2 total, showing 1-2):\n[1] button \"Save\"\n[2] textbox \"Name\"")


def user(goal: str, i: int, status: str = "Settings saved.") -> str:
    return (f"Goal: {goal}\nStep {i} of 12.\n<observation untrusted=\"true\">\n" + OBS.format(status=status)
            + "\n</observation>\nReply with ONE action line")


def step(goal: str, i: int, action: str, status: str = "Settings saved.") -> dict:
    return {"i": i, "system": "SYS prompt", "user": user(goal, i, status), "grammar": "root ::= \"x\"",
            "reply": f"# r\n{action}", "action": action, "valid": True, "url": "", "ms": 1.0}


def rec(tid: str, actions, *, success=True, split="ood", family="fam", sample=0, unsafe=()):
    goal = f"goal of {tid}"
    return {"task": {"id": tid, "source": "factory", "family": family, "split": split, "goal": goal,
                     "kind": "act", "tags": [], "meta": {}},
            "sample": sample,
            "trajectory": {"task_id": tid, "model": "m", "outcome": "verified" if success else "failed", "reason": "",
                           "steps": [step(goal, i + 1, a) for i, a in enumerate(actions)],
                           "events": [{"step": 1, "action": actions[0], "control": 'button "Save"'}],
                           "flags": [], "seconds": 1.0, "error": ""},
            "score": {"success": success, "unsafe": list(unsafe), "progress": 0.0, "notes": ""}}


@pytest.fixture
def exam_dir(tmp_path):
    d = tmp_path / "exam" / "shard1"
    d.mkdir(parents=True)
    recs = [rec("factory/fam/a/000", ["click(1)", 'fill(2, "Ann")', 'done("ok")']),
            rec("factory/fam/b/000", ["click(2)", 'done("no")'], success=False),
            rec("factory/fam/c/000", ['done("x")'], success=True, unsafe=["trap_clicked"])]
    (d / "trajectories.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    return tmp_path / "exam"


def toks(text: str):
    """Fake tokenizer: one token per whitespace-separated word (stable ints)."""
    return [sum(ord(c) for c in w) % 50000 for w in text.split()]


class FakeLlama:
    """Answers like llama-server: /props, /health, /metrics, /apply-template, /tokenize, /v1/chat/completions
    with ``timings``. Prefill costs 1 ms per evaluated token, decode 10 ms per token; the 'cache' reuses the
    longest common prefix with the previous prompt, minus ``cache_loss`` tokens (a hybrid model restoring an
    earlier checkpoint). ``answers`` maps a substring of the user message to the reply."""

    def __init__(self, answers=None, cache_loss: int = 0):
        self.answers = answers or {}
        self.cache_loss = cache_loss
        self.prev: list = []
        self.calls: list = []
        self.metrics = {"llamacpp:prompt_tokens_total": 0.0, "llamacpp:tokens_predicted_total": 0.0}

    def prompt(self, messages):
        return "\n".join(f"<|{m['role']}|> {m['content']}" for m in messages)

    def complete(self, body):
        t = toks(self.prompt(body["messages"]))
        n = 0
        while n < min(len(t), len(self.prev)) and t[n] == self.prev[n]:
            n += 1
        cache_n = max(n - self.cache_loss, 0) if n else 0
        self.prev = t
        user = body["messages"][-1]["content"]
        reply = next((v for k, v in self.answers.items() if k in user), "click(1)")
        gen = max(len(reply.split()), 1)
        self.calls.append(body)
        self.metrics["llamacpp:prompt_tokens_total"] += len(t) - cache_n
        self.metrics["llamacpp:tokens_predicted_total"] += gen
        return {"choices": [{"message": {"role": "assistant", "content": reply}}],
                "usage": {"prompt_tokens": len(t), "completion_tokens": gen},
                "timings": {"cache_n": cache_n, "prompt_n": len(t) - cache_n, "prompt_ms": float(len(t) - cache_n),
                            "predicted_n": gen, "predicted_ms": 10.0 * gen}}


@pytest.fixture
def fake_server():
    servers = []

    def start(fake: FakeLlama):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, ctype="application/json"):
                raw = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/props":
                    self._send({"build_info": "b99999-abcdef0", "model_path": "/m/fake-Q4_K_M.gguf",
                                "default_generation_settings": {"n_ctx": 4096}})
                elif self.path == "/health":
                    self._send({"status": "ok"})
                elif self.path == "/metrics":
                    self._send("".join(f"{k} {v}\n" for k, v in fake.metrics.items()), "text/plain")
                else:
                    self.send_error(404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/apply-template":
                    self._send({"prompt": fake.prompt(body["messages"])})
                elif self.path == "/tokenize":
                    self._send({"tokens": toks(body["content"])})
                elif self.path == "/v1/chat/completions":
                    self._send(fake.complete(body))
                else:
                    self.send_error(404)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for s in servers:
        s.shutdown()

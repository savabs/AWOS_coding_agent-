"""
serve_check.py — does an exported GGUF work as AWOS's local micro-model?

    PYTHONPATH=. python -m scaffold.rl.train.serve_check \\
        --gguf out/model-Q4_K_M.gguf \\
        --trajectories runs/rl/demos_factory_train/trajectories.jsonl [--hf-tokenizer <hf dir>] [--n 3]

Starts llama-server on a free loopback port, then for N real recorded steps (system, user, grammar):
  1. /apply-template with LocalGBNFModel's exact body == chat.render_prompt (the training text);
  2. (--hf-tokenizer) llama-server's /tokenize of that prompt == the HF tokenizer's ids;
  3. MicroAgent's own LocalGBNFModel.decide(system, user, grammar) against the server, and the reply
     must parse with micro_agent.parse_text_action (grammar-constrained, temperature 0).
Prints a JSON report (load time, per-step latency, replies, recorded reply for comparison).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def post(url: str, body: dict, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_health(base: str, proc: subprocess.Popen, timeout: float) -> float:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise SystemExit(f"llama-server exited with {proc.returncode}")
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(base + "/health", timeout=2) as r:
                if r.status == 200 and json.loads(r.read()).get("status") == "ok":
                    return time.time() - t0
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit("llama-server did not become healthy")


def pick_steps(path: Path, n: int) -> list:
    """Steps from verified successes, varied verbs first (click, fill/select, done)."""
    from scaffold.rl.train.dataset import episode_drop_reason, latest_records, step_drop_reason  # noqa: PLC0415
    pool = []
    for r in latest_records(path):
        if episode_drop_reason(r):
            continue
        pool += [s for s in r["trajectory"]["steps"] if not step_drop_reason(s)]
    by_verb: dict = {}
    for s in pool:
        by_verb.setdefault(s["reply"].strip().splitlines()[-1].split("(")[0], []).append(s)
    out = []
    while len(out) < n and any(by_verb.values()):
        for v in list(by_verb):
            if by_verb[v] and len(out) < n:
                out.append(by_verb[v].pop(0))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.train.serve_check")
    ap.add_argument("--gguf", required=True, type=Path)
    ap.add_argument("--trajectories", required=True, type=Path)
    ap.add_argument("--hf-tokenizer", type=Path, default=None)
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--server", default="llama-server")
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--log", type=Path, default=None)
    a = ap.parse_args(argv)
    from scaffold.agent.desktop.micro_agent import LocalGBNFModel, ParseError, parse_text_action  # noqa: PLC0415
    from scaffold.rl.train.chat import CHAT_TEMPLATE_KWARGS, messages_for, render_prompt  # noqa: PLC0415

    traj = a.trajectories if a.trajectories.is_file() else a.trajectories / "trajectories.jsonl"
    steps = pick_steps(traj, a.n)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(a.log or (a.gguf.parent / "serve_check.log"), "w")
    proc = subprocess.Popen([a.server, "-m", str(a.gguf), "--host", "127.0.0.1", "--port", str(port), "-c",
                             str(a.ctx), "-ngl", "99", "--jinja", "-np", "1"], stdout=log, stderr=subprocess.STDOUT)
    report = {"gguf": str(a.gguf), "steps": []}
    ok = True
    try:
        report["load_s"] = round(wait_health(base, proc, a.timeout), 2)
        tok = None
        if a.hf_tokenizer:
            from transformers import AutoTokenizer  # noqa: PLC0415
            tok = AutoTokenizer.from_pretrained(str(a.hf_tokenizer))
        model = LocalGBNFModel(base=base + "/v1", model="check")
        for s in steps:
            body = {"messages": messages_for(s["system"], s["user"]), "chat_template_kwargs": CHAT_TEMPLATE_KWARGS}
            server_prompt = post(base + "/apply-template", body).get("prompt")
            ours = render_prompt(s["system"], s["user"])
            row = {"recorded_reply": s["reply"], "template_equal": server_prompt == ours}
            if not row["template_equal"]:
                i = next((j for j, (x, y) in enumerate(zip(server_prompt or "", ours)) if x != y), None)
                row["template_diff_at"] = i
                row["server_tail"] = (server_prompt or "")[-80:]
            if tok is not None:
                srv_ids = post(base + "/tokenize", {"content": ours, "add_special": False,
                                                    "parse_special": True}).get("tokens")
                hf_ids = tok.encode(ours, add_special_tokens=False)
                row["tokens_equal"] = srv_ids == hf_ids
                row["prompt_tokens"] = len(hf_ids)
            t0 = time.time()
            reply = model.decide(s["system"], s["user"], s["grammar"])
            row["ms"] = round((time.time() - t0) * 1000, 1)
            row["reply"] = reply[:300]
            try:
                row["parsed"] = parse_text_action(reply).show()[:300]
            except ParseError as e:
                row["parsed"] = None
                row["parse_error"] = str(e)
            ok &= bool(row["template_equal"] and row["parsed"] and row.get("tokens_equal", True))
            report["steps"].append(row)
        report["usage"] = model.usage
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
    report["ok"] = ok
    print(json.dumps(report, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

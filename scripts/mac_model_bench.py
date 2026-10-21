#!/usr/bin/env python3
"""mac_model_bench.py — helpers for .github/workflows/mac-model.yml (stdlib only).

    mac_model_bench.py BASE_URL MODEL
        one fixed ~600-token prompt + 64 generated tokens against llama-server's
        /v1/chat/completions; prints {"ok", "prompt_tps", "gen_tps", "content", ...}.
        Exit 1 if the server does not answer (the workflow then falls back to CPU).
    mac_model_bench.py --meta MODEL VARIANT OS NGL GGUF SERVER_LOG BENCH_JSON
        prints the job's meta.json: model, device (Metal or CPU, from the server log), bench.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

PROMPT = ("You operate a web page through numbered controls. " * 40
          + "\nReply with one short sentence describing what a login form is.")

DEVICE_RX = re.compile(r"(metal|MTL\d|offloaded \d+/\d+ layers|using device|GPU name|"
                       r"simdgroup|has unified memory|recommendedMaxWorkingSetSize)", re.I)


def bench(base: str, model: str) -> dict:
    body = {"model": model, "temperature": 0, "max_tokens": 64,
            "messages": [{"role": "user", "content": PROMPT}],
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(base.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=900) as r:
        data = json.loads(r.read())
    t = data.get("timings") or {}
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return {"ok": bool(content.strip()), "wall_s": round(time.monotonic() - t0, 2),
            "prompt_n": t.get("prompt_n"), "prompt_tps": round(t.get("prompt_per_second") or 0, 1),
            "predicted_n": t.get("predicted_n"), "gen_tps": round(t.get("predicted_per_second") or 0, 1),
            "content": content[:200]}


def device_lines(log_text: str) -> list:
    return [ln.strip()[:200] for ln in log_text.splitlines() if DEVICE_RX.search(ln)][:40]


def meta(model, variant, os_label, ngl, gguf, log_path, bench_path) -> dict:
    log = Path(log_path).read_text(errors="replace") if Path(log_path).exists() else ""
    try:
        b = json.loads(Path(bench_path).read_text())
    except (OSError, ValueError):
        b = None
    lines = device_lines(log)
    off = re.search(r"offloaded (\d+)/(\d+) layers to GPU", log)
    metal = int(ngl) > 0 and bool(b and b.get("ok")) and bool(off and int(off.group(1)) > 0)
    return {"model": model, "variant": variant, "os": os_label, "ngl": int(ngl), "gguf": gguf,
            "device": "metal" if metal else "cpu", "offloaded": off.group(0) if off else None,
            "device_lines": lines, "bench": b}


def main(argv) -> int:
    if argv[:1] == ["--meta"]:
        print(json.dumps(meta(*argv[1:8]), indent=2))
        return 0
    base, model = argv[0], argv[1]
    try:
        res = bench(base, model)
    except Exception as e:                      # noqa: BLE001 - report any failure as not ok
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

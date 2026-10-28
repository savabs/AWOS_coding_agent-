#!/usr/bin/env python3
"""mac_model_bench.py — helpers for .github/workflows/mac-model.yml (stdlib only).

    mac_model_bench.py BASE_URL MODEL [--timeout S]
        one fixed ~600-token prompt + 64 generated tokens against llama-server's
        /v1/chat/completions; prints {"ok", "prompt_tps", "gen_tps", "content", ...}.
        Exit 1 if the server does not answer (the workflow then falls back to CPU).
    mac_model_bench.py --from-llama-bench LLAMA_BENCH_JSON
        converts `llama-bench -o json` (pp + tg rows) to {"ok", "prompt_tps", "gen_tps"}.
    mac_model_bench.py --pick BENCH_NGL99 BENCH_NGL0
        prints 99 or 0: the device with the lower estimated per-step time (2000 prompt + 20
        generated tokens); 0 when neither bench answered.
    mac_model_bench.py --meta MODEL VARIANT OS NGL GGUF SERVER_LOG BENCH_JSON [OTHER_BENCH_JSON]
        prints the job's meta.json: model, device (Metal or CPU, from the server log), bench(es).
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


def from_llama_bench(path) -> dict:
    try:
        rows = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"no llama-bench result ({type(e).__name__})"}
    pp = [r.get("avg_ts") for r in rows if r.get("n_prompt") and not r.get("n_gen")]
    tg = [r.get("avg_ts") for r in rows if r.get("n_gen") and not r.get("n_prompt")]
    dev = sorted({str(r.get("gpu_info") or r.get("backends") or "") for r in rows} - {""})
    if not pp or not tg:
        return {"ok": False, "error": "llama-bench rows missing", "devices": dev}
    return {"ok": True, "source": "llama-bench", "prompt_tps": round(pp[0], 1), "gen_tps": round(tg[0], 1),
            "devices": dev}


def bench(base: str, model: str, timeout: float = 900) -> dict:
    body = {"model": model, "temperature": 0, "max_tokens": 64,
            "messages": [{"role": "user", "content": PROMPT}],
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(base.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout) as r:
        data = json.loads(r.read())
    t = data.get("timings") or {}
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return {"ok": bool(content.strip()), "wall_s": round(time.monotonic() - t0, 2),
            "prompt_n": t.get("prompt_n"), "prompt_tps": round(t.get("prompt_per_second") or 0, 1),
            "predicted_n": t.get("predicted_n"), "gen_tps": round(t.get("predicted_per_second") or 0, 1),
            "content": content[:200]}


def device_lines(log_text: str) -> list:
    return [ln.strip()[:200] for ln in log_text.splitlines() if DEVICE_RX.search(ln)][:40]


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def step_seconds(b) -> float:
    """Estimated seconds for one agent step: 2000 prompt tokens + 20 generated tokens."""
    if not b or not b.get("ok") or not b.get("prompt_tps") or not b.get("gen_tps"):
        return float("inf")
    return 2000 / b["prompt_tps"] + 20 / b["gen_tps"]


def pick(metal_path, cpu_path) -> int:
    m, c = step_seconds(_read(metal_path)), step_seconds(_read(cpu_path))
    return 99 if m < c else 0


def meta(model, variant, os_label, ngl, gguf, log_path, bench_path, other_bench=None) -> dict:
    log = Path(log_path).read_text(errors="replace") if Path(log_path).exists() else ""
    b = _read(bench_path)
    lb_err = Path(log_path).parent / f"llama-bench-ngl{int(ngl)}.err"
    lines = device_lines(log + "\n" + (lb_err.read_text(errors="replace") if lb_err.exists() else ""))
    off = re.search(r"offloaded (\d+)/(\d+) layers to GPU", log)
    metal = int(ngl) > 0 and bool(b and b.get("ok"))
    return {"model": model, "variant": variant, "os": os_label, "ngl": int(ngl), "gguf": gguf,
            "device": "metal" if metal else "cpu", "offloaded": off.group(0) if off else None,
            "device_lines": lines, "bench": b, "step_s_est": round(step_seconds(b), 1) if b else None,
            "other_bench": _read(other_bench) if other_bench else None}


def main(argv) -> int:
    if argv[:1] == ["--from-llama-bench"]:
        print(json.dumps(from_llama_bench(argv[1]), indent=2))
        return 0
    if argv[:1] == ["--pick"]:
        print(pick(argv[1], argv[2]))
        return 0
    if argv[:1] == ["--meta"]:
        print(json.dumps(meta(*argv[1:9]), indent=2, default=str))
        return 0
    base, model = argv[0], argv[1]
    timeout = float(argv[argv.index("--timeout") + 1]) if "--timeout" in argv else 900
    try:
        res = bench(base, model, timeout)
    except Exception as e:                      # noqa: BLE001 - report any failure as not ok
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

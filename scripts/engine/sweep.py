#!/usr/bin/env python3
"""
sweep.py — start llama-server with each config in turn, run bench.py against it, tabulate
(docs/specs/enginebench.md).

    python scripts/engine/sweep.py --list                                   # models and configs
    python scripts/engine/sweep.py --models a-final-4b --configs ext cms0 cpu
    python scripts/engine/sweep.py --models g7-2b --configs ext ctxcp0 --budget repair=30,verify=30
    python scripts/engine/sweep.py --model my-q5=~/models/x-Q5_K_M.gguf:agent --configs ext   # any GGUF

One server at a time, on a loopback port that is never 8080 (the owner's extension server lives there and
is never touched: the sweep only stops the process it started). Each config gets a fresh server, so
nothing cached by an earlier config can leak into the next. Models are a list (label -> GGUF, the call
kinds it serves, its default budget): quant variants or a LoRA-serving entry are one more row, and a
config's flags may mention {model} (e.g. --lora) — see MODELS / CONFIGS.

Writes <out>/<model>__<config>/{report.json,report.md,server.log} and <out>/sweep.{json,md}.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "scripts" / "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

import bench  # noqa: E402
import capture  # noqa: E402

OWNER_PORT = 8080
MODELS_DIR = Path.home() / ".awos" / "models"
NPROC = os.cpu_count() or 4

#: label -> GGUF (first shard for split files), the call kinds it serves in the product, default budget
MODELS: Dict[str, Dict[str, Any]] = {
    "a-final-4b": {"path": str(MODELS_DIR / "qwen3.5-4b-r2a-final-Q4_K_M-00001-of-00002.gguf"),
                   "kinds": ["agent"], "budget": "agent=40",
                   "release": "rl-qwen3.5-4b-r2a-final"},
    "g7-2b": {"path": str(MODELS_DIR / "qwen3.5-2b-g7-rv-Q4_K_M.gguf"), "kinds": ["repair", "verify"],
              "budget": "repair=30,verify=30", "release": "rl-qwen3.5-2b-g7-rv"},
}

COMMON = ["--jinja", "--reasoning", "off", "--temp", "0", "--metrics"]
#: (a) scripts/awos_model.sh (the Chrome extension's server, Metal by default)
EXT = ["-c", "4096", "-np", "1", "--cache-prompt"]
#: (a) .github/workflows/rl-eval.yml (CPU runners: -ngl 0, all cores, 2 slots x 8k)
CI = ["-ngl", "0", "-t", str(NPROC), "-c", "16384", "-np", "2", "--cache-prompt"]

CONFIGS: Dict[str, Dict[str, Any]] = {
    "ext":      {"group": "a", "flags": EXT, "note": "extension flags (scripts/awos_model.sh)"},
    "ci":       {"group": "a", "flags": CI, "note": "CI flags (rl-eval.yml): CPU, all cores, -np 2"},
    "ctxcp0":   {"group": "b", "flags": EXT + ["--ctx-checkpoints", "0"], "note": "no context checkpoints"},
    "ctxcp1":   {"group": "b", "flags": EXT + ["--ctx-checkpoints", "1"], "note": "1 context checkpoint per slot"},
    "ctxcp4":   {"group": "b", "flags": EXT + ["--ctx-checkpoints", "4"], "note": "4 context checkpoints per slot"},
    "cms0":     {"group": "b", "flags": EXT + ["-cms", "0"], "note": "checkpoints with no minimum spacing"},
    "cms256":   {"group": "b", "flags": EXT + ["-cms", "256"], "note": "checkpoints every >= 256 tokens"},
    "cram0":    {"group": "b", "flags": EXT + ["--cache-ram", "0"], "note": "no host-RAM prompt cache"},
    "cram0-cms0": {"group": "b", "flags": EXT + ["--cache-ram", "0", "-cms", "0"], "note": "no host cache, cms 0"},
    "cram0-cms256": {"group": "b", "flags": EXT + ["--cache-ram", "0", "-cms", "256"],
                     "note": "recommended (2026-10-26 sweep): no host cache, checkpoints every >= 256 tokens"},
    "t2":       {"group": "c", "flags": EXT + ["-t", "2"], "note": "2 CPU threads"},
    "t8":       {"group": "c", "flags": EXT + ["-t", "8"], "note": "8 CPU threads"},
    "ub128":    {"group": "c", "flags": EXT + ["-b", "2048", "-ub", "128"], "note": "ubatch 128"},
    "ub1024":   {"group": "c", "flags": EXT + ["-b", "2048", "-ub", "1024"], "note": "ubatch 1024"},
    "ub2048":   {"group": "c", "flags": EXT + ["-b", "2048", "-ub", "2048"], "note": "ubatch 2048"},
    "fa-on":    {"group": "d", "flags": EXT + ["-fa", "on"], "note": "flash attention on"},
    "fa-off":   {"group": "d", "flags": EXT + ["-fa", "off"], "note": "flash attention off"},
    "ngl99":    {"group": "e", "flags": EXT + ["-ngl", "99"], "note": "all layers on Metal (explicit)"},
    "cpu":      {"group": "e", "flags": EXT + ["-ngl", "0"], "note": "CPU only, default threads"},
    "cpu-t10":  {"group": "e", "flags": EXT + ["-ngl", "0", "-t", str(NPROC)], "note": f"CPU only, {NPROC} threads (on a 4P+6E Mac: 0.5 tok/s decode, avoid)"},
}


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def server_cmd(binary: str, model_path: str, port: int, flags: Sequence[str]) -> List[str]:
    if port == OWNER_PORT:
        raise ValueError("port 8080 is the owner's extension server: pick another")
    fl = [f.replace("{model}", model_path) for f in flags]
    return [binary, "-m", model_path, "--alias", "local", "--host", "127.0.0.1", "--port", str(port), *fl, *COMMON]


def wait_ready(base: str, proc: subprocess.Popen, timeout: float = 240.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            return False
        try:
            h = bench.http(base, "/health", timeout=3)
            if isinstance(h, dict) and h.get("status") == "ok":
                return True
        except Exception:  # noqa: BLE001 -- loading (503) or not listening yet
            pass
        time.sleep(1.0)
    return False


def stop(proc: subprocess.Popen) -> None:
    """Stops only the server this sweep started."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=30)


def run_one(model: str, spec: Dict[str, Any], config: str, cfg: Dict[str, Any], episodes: Sequence[dict],
            headers: Sequence[dict], *, port: int, binary: str, out: Path, budget: Optional[str],
            measure_lcp: bool = True, quiet: bool = False) -> Dict[str, Any]:
    d = out / f"{model}__{config}"
    d.mkdir(parents=True, exist_ok=True)
    if not port_free(port):
        raise RuntimeError(f"port {port} is busy: not starting a server there")
    cmd = server_cmd(binary, spec["path"], port, cfg["flags"])
    base = f"http://127.0.0.1:{port}"
    t0 = time.monotonic()
    with open(d / "server.log", "w") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        if not wait_ready(base, proc):
            return {"model": model, "config": config, "error": f"server not ready (see {d / 'server.log'})",
                    "cmd": cmd}
        load_s = time.monotonic() - t0
        b = bench.parse_budget(budget or spec.get("budget"))
        sel = bench.select(episodes, spec["kinds"], b)
        wl = {"version": capture.VERSION, "digest": "+".join(h["digest"][:12] for h in headers),
              "calls": sum(len(e["calls"]) for e in sel), "episodes": len(sel), "budget": budget or spec.get("budget"),
              "kinds": spec["kinds"]}
        rep = bench.run(base, sel, label=f"{model} / {config}", workload=wl, pid=proc.pid, model_path=spec["path"],
                        measure_lcp=measure_lcp, out=d, progress=None if quiet else (lambda s: print("   ", s, flush=True)))
        rep["load_s"] = round(load_s, 1)
        (d / "report.json").write_text(json.dumps(rep, indent=1))
        return {"model": model, "config": config, "group": cfg["group"], "note": cfg["note"], "load_s": rep["load_s"],
                "fingerprint": rep["fingerprint"]["line"], "fp": rep["fingerprint"]["id"], "summary": rep["summary"],
                "process": rep["process"], "wall_s": rep["wall_s"], "dir": str(d)}
    finally:
        stop(proc)


# ------------------------------------------------------------------- table --

def quality_cell(q: Dict[str, Any]) -> str:
    parts = []
    if "agent" in q:
        parts.append(f"act {100 * (q['agent']['step_match'] or 0):.0f}% (ref {100 * (q['agent']['ref_agree'] or 0):.0f}%)")
    if "repair" in q:
        parts.append(f"rep {100 * (q['repair']['accuracy'] or 0):.0f}% wrong {100 * (q['repair']['wrong_repair_rate'] or 0):.1f}%")
    if "verify" in q:
        parts.append(f"ver {100 * (q['verify']['accuracy'] or 0):.0f}% falseV {100 * (q['verify']['false_verified_rate'] or 0):.0f}%")
    return "; ".join(parts) or "-"


def row(r: Dict[str, Any], base: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if r.get("error"):
        return {"model": r["model"], "config": r["config"], "error": r["error"]}
    s = r["summary"]
    sp, c, m, cp = s["speed"]["all"], s["cache"]["all"], s["memory"], s["compute"]
    out = {"model": r["model"], "config": r["config"], "group": r.get("group"), "n": sp.get("n"),
           "wall_s": sp.get("wall_s_mean"), "p90_s": sp.get("wall_s_p90"), "prefill_s": sp.get("prefill_s_mean"),
           "decode_s": sp.get("decode_s_mean"), "prefill_tok_s": sp.get("prefill_tok_s"), "decode_tok_s": sp.get("decode_tok_s"),
           "cached_share": c.get("cached_share"), "reusable_share": c.get("reusable_share"),
           "task_s": {k: v.get("mean") for k, v in s.get("task_s", {}).items()},
           "quality": quality_cell(s["quality"]), "quality_raw": s["quality"],
           "footprint_mb": m.get("peak_footprint_mb"), "unified_mb": m.get("est_peak_unified_mb"), "rss_mb": m.get("peak_rss_mb"),
           "prompt_eval_per_call": round(cp["prompt_tokens_evaluated"] / max(cp["calls"], 1), 1),
           "gen_per_call": cp.get("generated_tokens_per_call"), "cpu_s_call": cp.get("cpu_s_per_call"),
           "gpu_s_call": cp.get("gpu_busy_s_per_call"), "load_s": r.get("load_s"), "fp": r.get("fp"),
           "fingerprint": r.get("fingerprint"), "errors": s.get("errors")}
    if base and not base.get("error") and base.get("wall_s") and out["wall_s"]:
        out["vs_base"] = round(out["wall_s"] / base["wall_s"] - 1, 3)
    return out


def table(rows: Sequence[Dict[str, Any]]) -> str:
    L = ["| model | config | n | s/call | p90 | prefill s | decode s | prefill t/s | decode t/s | cached | task s | "
         "quality | footprint MB | unified MB | eval tok/call | CPU s/call | GPU s/call | vs first |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    f = lambda v, pct=False: "-" if v is None else (f"{100 * v:+.0f}%" if pct == "d" else f"{100 * v:.0f}%" if pct else f"{v:g}")  # noqa: E731
    for r in rows:
        if r.get("error"):
            L.append(f"| {r['model']} | {r['config']} | ERROR: {r['error']} |" + " |" * 15)
            continue
        ts = ", ".join(f"{k} {v:g}" for k, v in r["task_s"].items() if v is not None)
        L.append(f"| {r['model']} | {r['config']} | {r['n']} | {f(r['wall_s'])} | {f(r['p90_s'])} | {f(r['prefill_s'])} | "
                 f"{f(r['decode_s'])} | {f(r['prefill_tok_s'])} | {f(r['decode_tok_s'])} | {f(r['cached_share'], True)} | "
                 f"{ts} | {r['quality']} | {f(r['footprint_mb'])} | {f(r['unified_mb'])} | {f(r['prompt_eval_per_call'])} | "
                 f"{f(r['cpu_s_call'])} | {f(r['gpu_s_call'])} | {f(r.get('vs_base'), 'd')} |")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--models", nargs="*", default=list(MODELS))
    ap.add_argument("--model", action="append", default=[], help="extra model LABEL=PATH[:kind,kind] (e.g. a quant)")
    ap.add_argument("--configs", nargs="*", default=["ext", "cram0", "ctxcp0", "cram0-cms256"])
    ap.add_argument("--budget", help="override every model's budget, e.g. agent=20")
    ap.add_argument("--workload", type=Path, action="append")
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--binary", default="llama-server")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--no-lcp", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    models = dict(MODELS)
    for spec in a.model:
        label, _, rest = spec.partition("=")
        path, kinds = rest, ""
        if ":" in rest and all(k in bench.KINDS for k in rest.rsplit(":", 1)[1].split(",")):
            path, kinds = rest.rsplit(":", 1)
        models[label] = {"path": str(Path(path).expanduser()), "kinds": (kinds or "agent").split(","), "budget": a.budget}
        a.models.append(label)
    if a.list:
        print("models:")
        for k, v in models.items():
            print(f"  {k:12s} {v['kinds']} {v.get('budget')} {v['path']}")
        print("configs:")
        for k, v in CONFIGS.items():
            print(f"  ({v['group']}) {k:11s} {' '.join(v['flags'])}   # {v['note']}")
        return 0
    if a.port == OWNER_PORT:
        raise SystemExit("port 8080 is the owner's extension server: pick another")
    bad = [c for c in a.configs if c not in CONFIGS] + [m for m in a.models if m not in models]
    if bad:
        raise SystemExit(f"unknown model/config: {bad} (see --list)")
    headers, eps = capture.load(a.workload or [bench.DEFAULT_WORKLOAD])
    out = a.out or ROOT / "bench" / "engine" / "runs" / f"sweep-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    results, rows = [], []
    t0 = time.monotonic()
    for m in a.models:
        if not Path(models[m]["path"]).exists():
            print(f"skip {m}: {models[m]['path']} not found")
            continue
        first = None
        for c in a.configs:
            print(f"== {m} / {c}: {' '.join(CONFIGS[c]['flags'])}  ({time.monotonic() - t0:.0f} s into the sweep)", flush=True)
            try:
                r = run_one(m, models[m], c, CONFIGS[c], eps, headers, port=a.port, binary=a.binary, out=out,
                            budget=a.budget, measure_lcp=not a.no_lcp, quiet=a.quiet)
            except Exception as e:  # noqa: BLE001 -- one bad config does not end the sweep
                r = {"model": m, "config": c, "error": f"{type(e).__name__}: {e}"[:300]}
            results.append(r)
            rw = row(r, first)
            if first is None and not r.get("error"):
                first = rw
            rows.append(rw)
            print(table([rw]).splitlines()[-1], flush=True)
            (out / "sweep.json").write_text(json.dumps({"rows": rows, "results": results}, indent=1))
            (out / "sweep.md").write_text(f"# EngineBench sweep {out.name}\n\n" + table(rows) + "\n")
    print("\n" + table(rows))
    print(f"\nwrote {out}/sweep.md ({time.monotonic() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

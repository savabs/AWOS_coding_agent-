#!/usr/bin/env python3
"""
eval_verifier.py — score a G5 verifier on eval.jsonl (or valid.jsonl) against the trivial baselines.

    # GGUF served by llama-server (the product path), e.g. after rl-export.yml:
    llama-server -m qwen3.5-4b-verifier-g5-Q4_K_M.gguf --port 8091 -c 4096 --jinja &
    python scripts/rl/eval_verifier.py --rows verifier/eval.jsonl --server http://127.0.0.1:8091 \\
        [--limit 300] [--workers 2] --out verifier_report.json
    # transformers model (+ LoRA adapter) on CPU, for tiny tests / no GGUF yet:
    python scripts/rl/eval_verifier.py --rows verifier/eval.jsonl --hf Qwen/Qwen3.5-4B --adapter kout/adapter --limit 20
    # baselines only (no model):
    python scripts/rl/eval_verifier.py --rows verifier/eval.jsonl

Rows are verifier_data.py output ("messages", "prompt", "meta.label", "meta.status"). The server is sent
exactly [system, user] with enable_thinking=False, temperature 0 and (by default) a GBNF grammar that
forces "<LABEL>: <one line>", so every reply parses. --limit takes a stratified (per label) seeded sample.

Report per slice (all, factory_ood, miniwob_holdout, claimed_done, product_pass, unseen_family):
accuracy, per-class precision/recall, confusion matrix and the HEADLINE false_verified_rate (label
NOT_DONE/UNSAFE, model says VERIFIED: in the product that ships a broken chore; target ~0), failure
recall and verified_kept_rate (passes the verifier does not downgrade), next to the same numbers for
the keyword baseline (status text contains saved/created/done -> VERIFIED), a broader keyword list,
and always-NOT_DONE. Exit 0; the numbers are the result.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scaffold.rl.train.verifier_data import (BASELINE_BROAD, BASELINE_SPEC, LABELS, baseline_predict,  # noqa: E402
                                             parse_label, slice_metrics)

GRAMMAR = 'root ::= ("VERIFIED" | "NOT_DONE" | "UNSAFE") ": " [^\\n]{1,200}'


def load_rows(path: Path, limit: int, seed: int) -> List[dict]:
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    if limit and len(rows) > limit:
        by = defaultdict(list)
        for r in rows:
            by[r["meta"]["label"]].append(r)
        rng = random.Random(seed)
        out = []
        for lab, rs in by.items():          # proportional, at least 1 per present label
            rng.shuffle(rs)
            out += rs[:max(1, round(limit * len(rs) / len(rows)))]
        rows = out
    return rows


def server_predict(url: str, grammar: bool, max_tokens: int, timeout: float) -> Callable[[dict], str]:
    def f(row: dict) -> str:
        body = {"messages": row["messages"][:2], "temperature": 0, "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": False}}
        if grammar:
            body["grammar"] = GRAMMAR
        req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read())["choices"][0]["message"]["content"] or ""
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    return f"ERROR {type(e).__name__}"
                time.sleep(2)
        return ""
    return f


def hf_predict(base: str, adapter: str, max_tokens: int) -> Callable[[dict], str]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(base)
    try:
        model = AutoModelForCausalLM.from_pretrained(base, dtype=torch.float32)
    except Exception:  # noqa: BLE001 - Qwen3.5 checkpoints load as image-text-to-text
        from transformers import AutoModelForImageTextToText
        model = AutoModelForImageTextToText.from_pretrained(base, dtype=torch.float32)
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter)
    model.eval()

    def f(row: dict) -> str:
        ids = tok(row["prompt"], return_tensors="pt", add_special_tokens=False).input_ids
        with torch.no_grad():
            out = model.generate(ids, max_new_tokens=max_tokens, do_sample=False)
        return tok.decode(out[0, ids.shape[1]:], skip_special_tokens=True)
    return f


def table(name: str, m: Dict[str, dict]) -> str:
    lines = [f"== {name}",
             f"{'slice':16} {'n':>5} {'acc':>6} {'FALSE_VERIFIED':>15} {'fail_recall':>11} {'verified_kept':>13}"]
    for s, v in m.items():
        fv = v["false_verified_rate"]
        lines.append(f"{s:16} {v['n']:5d} {v['accuracy'] or 0:6.3f} "
                     f"{'-' if fv is None else f'{fv:.3f}':>15} {str(v['failure_recall']):>11} "
                     f"{str(v['verified_kept_rate']):>13}")
    c = m["all"]["confusion"]
    lines.append("confusion (rows = label, cols = prediction): "
                 + "  ".join(f"{g}: " + ", ".join(f"{p}={c[g].get(p, 0)}" for p in LABELS + ("OTHER",))
                             for g in c))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rows", required=True, type=Path, help="eval.jsonl / valid.jsonl from verifier_data.py")
    ap.add_argument("--server", default="", help="llama-server base URL (serves the exported GGUF)")
    ap.add_argument("--hf", default="", help="transformers model id/dir (CPU)")
    ap.add_argument("--adapter", default="", help="PEFT adapter dir for --hf")
    ap.add_argument("--limit", type=int, default=0, help="stratified sample size (0 = all rows)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=64)
    ap.add_argument("--no-grammar", action="store_true")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    rows = load_rows(a.rows, a.limit, a.seed)
    metas = [r["meta"] for r in rows]
    report: Dict[str, dict] = {"rows": str(a.rows), "n": len(rows), "limit": a.limit, "seed": a.seed,
                               "baselines": {
                                   "keyword_spec": slice_metrics([(m, baseline_predict(m.get("status", ""), BASELINE_SPEC)) for m in metas]),
                                   "keyword_broad": slice_metrics([(m, baseline_predict(m.get("status", ""), BASELINE_BROAD)) for m in metas]),
                                   "always_not_done": slice_metrics([(m, "NOT_DONE") for m in metas])}}
    pred = None
    if a.server:
        pred = server_predict(a.server, not a.no_grammar, a.max_tokens, a.timeout)
        report["model"] = {"server": a.server, "grammar": not a.no_grammar}
    elif a.hf:
        pred = hf_predict(a.hf, a.adapter, a.max_tokens)
        report["model"] = {"hf": a.hf, "adapter": a.adapter}
        a.workers = 1
    if pred:
        t0 = time.time()
        with ThreadPoolExecutor(max(1, a.workers)) as ex:
            replies = list(ex.map(pred, rows))
        labels = [parse_label(x) for x in replies]
        report["model"].update({"seconds": round(time.time() - t0, 1),
                                "unparsed": sum(1 for x in labels if x == "OTHER"),
                                "metrics": slice_metrics(list(zip(metas, labels)))})
        report["predictions"] = [{"task_id": m["task_id"], "src": m.get("src"), "label": m["label"], "pred": p,
                                  "reply": r[:300]} for m, p, r in zip(metas, labels, replies)]
        print(table(f"MODEL ({a.server or a.hf})", report["model"]["metrics"]))
    for name, m in report["baselines"].items():
        print(table(f"baseline {name}", m))
    if a.out:
        a.out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"-> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
grpo_speed.py — how many GRPO rollouts per hour this device can do with one in-process HF model
(scaffold/rl/train/grpo.py HFPolicy), the go / no-go number for online RL on free T4s (plan run 6).

    python scripts/rl/grpo_speed.py --model Qwen/Qwen3.5-2B [--dtype fp16] [--steps 6] [--completion 40]
    python scripts/rl/grpo_speed.py --tiny --tokenizer <Qwen3.5 tokenizer dir>       # CPU smoke
    python scripts/rl/grpo_speed.py --model ... --measure-env                        # time real browser steps

Measures on a real MicroAgent prompt (scaffold/rl/train/fixtures/oracle_demos.jsonl, ~0.8k tokens):
    gen_step_s     one sampled reply of exactly --completion tokens (prefill + decode)
    train_seq_s    policy forward + backward on that (prompt, reply) sequence
    ref_seq_s      reference forward (adapter disabled), only when kl_coef > 0
    env_step_s     browser + harness time per step (--env-step-s, or --measure-env: oracle episodes
                   through the real harness on factory train tasks)
then, for a rollout of --steps model calls and keep_frac of rollouts in groups with a signal:
    single device  3600 / (steps * (gen + env) + keep_frac * steps * (train + ref))
    two devices    3600 / max(steps * (gen + env), keep_frac * steps * (train + ref))   (projection: needs
                   a generator on the second GPU + weight sync, not built)
Verdict: go iff the single-device rate reaches --iterations x --tasks-per-iter x --group rollouts within
--budget-hours (defaults: 50 x 4 x 6 = 1200 rollouts in 5 GPU-hours = 240/h). No network, no paid API.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
FIXTURE = REPO / "scaffold" / "rl" / "train" / "fixtures" / "oracle_demos.jsonl"


def real_prompt():
    rec = json.loads(FIXTURE.read_text().splitlines()[0])
    s = rec["trajectory"]["steps"][0]
    return s["system"], s["user"]


def measure_env(n: int = 6) -> float:
    """Seconds per step of oracle episodes on factory train tasks through the real harness."""
    from scaffold.rl.harness.chromium import Chromium
    from scaffold.rl.harness.episode import run_episode
    from scaffold.rl.harness.models import OracleModel
    from scaffold.rl.factory.source import FactorySource
    src = FactorySource()
    src.start()
    ch = Chromium()
    try:
        tasks = [t for t in src.tasks("train") if t.kind == "act"][:n]
        secs, steps = 0.0, 0
        for t in tasks:
            t0 = time.monotonic()
            tr, _ = run_episode(src, t, OracleModel(src.oracle(t)), model_label="oracle", chromium=ch)
            secs += time.monotonic() - t0
            steps += max(1, len(tr.steps))
        return secs / max(1, steps)
    finally:
        ch.close()
        src.stop()


def estimate(gen_step_s: float, env_step_s: float, train_seq_s: float, ref_seq_s: float, steps: int,
             keep_frac: float, need_per_h: float) -> dict:
    gen = steps * (gen_step_s + env_step_s)
    train = keep_frac * steps * (train_seq_s + ref_seq_s)
    single = 3600 / (gen + train)
    two = 3600 / max(gen, train, 1e-9)
    return {"rollout_gen_s": round(gen, 3), "rollout_train_s": round(train, 3),
            "rollouts_per_hour_single": round(single, 1), "rollouts_per_hour_two_devices": round(two, 1),
            "needed_per_hour": round(need_per_h, 1), "verdict": "go" if single >= need_per_h else "no-go"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="grpo_speed.py")
    ap.add_argument("--model", default=None, help="HF id or local checkpoint")
    ap.add_argument("--tiny", action="store_true", help="build the tiny random Qwen3.5 (needs --tokenizer)")
    ap.add_argument("--tokenizer", default=None)
    ap.add_argument("--dtype", default="auto", choices=["auto", "fp16", "bf16", "fp32"])
    ap.add_argument("--completion", type=int, default=40, help="reply tokens per step (reason line + action)")
    ap.add_argument("--steps", type=int, default=6, help="model calls per rollout")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--kl-coef", type=float, default=0.001)
    ap.add_argument("--keep-frac", type=float, default=0.6, help="share of rollouts in groups with a signal")
    ap.add_argument("--env-step-s", type=float, default=0.15, help="browser + harness s/step (measured: 0.14 on an M-series Mac)")
    ap.add_argument("--measure-env", action="store_true")
    ap.add_argument("--iterations", type=int, default=50)
    ap.add_argument("--tasks-per-iter", type=int, default=4)
    ap.add_argument("--group", type=int, default=6)
    ap.add_argument("--budget-hours", type=float, default=5.0)
    ap.add_argument("--out", default=None, help="also write the JSON here")
    a = ap.parse_args(argv)

    from scaffold.rl.train.grpo import GRPOConfig, HFPolicy
    model = a.model
    if a.tiny:
        import tempfile
        from scaffold.rl.train.tiny_qwen35 import make_tiny
        if not a.tokenizer:
            raise SystemExit("--tiny needs --tokenizer <dir with the Qwen3.5 tokenizer files>")
        model = str(make_tiny(Path(tempfile.mkdtemp(prefix="tiny-qwen35-")), Path(a.tokenizer), dtype="float32"))
    if not model:
        raise SystemExit("--model or --tiny")
    cfg = GRPOConfig(base_model=model, dtype=a.dtype, max_new_tokens=a.completion, kl_coef=a.kl_coef,
                     gradient_checkpointing=True)
    t0 = time.monotonic()
    pol = HFPolicy(cfg, log=lambda *x: None)
    load_s = time.monotonic() - t0
    torch = pol.torch
    system, user = real_prompt()
    call = pol.encode(system, user, "click(4)")
    x = torch.tensor([call.prompt_ids], device=pol.dev)

    def sync():
        if pol.cuda:
            torch.cuda.synchronize()

    gen, train, ref = [], [], []
    for r in range(a.repeats + 1):                         # first round = warm-up, not counted
        sync()
        t = time.monotonic()
        pol.model.eval()
        with torch.no_grad(), pol._autocast():
            out = pol.model.generate(input_ids=x, attention_mask=torch.ones_like(x), do_sample=True, temperature=1.0,
                                     top_k=0, max_new_tokens=a.completion, min_new_tokens=a.completion,
                                     pad_token_id=pol.pad)
        sync()
        g = time.monotonic() - t
        comp = out[0, x.shape[1]:].tolist()
        t = time.monotonic()
        if a.kl_coef:
            pol.token_logps(call.prompt_ids, comp, reference=True)
        sync()
        rf = time.monotonic() - t
        pol.model.train()
        t = time.monotonic()
        lp = pol.token_logps(call.prompt_ids, comp, grad=True)
        (-lp.mean()).backward()
        sync()
        tr = time.monotonic() - t
        pol.model.zero_grad(set_to_none=True)
        if r:
            gen.append(g), train.append(tr), ref.append(rf)
    need = a.iterations * a.tasks_per_iter * a.group / a.budget_hours
    env_s = measure_env() if a.measure_env else a.env_step_s
    res = {"model": model if not a.tiny else "tiny-qwen35 (random)", "device": str(pol.dev),
           "gpu": torch.cuda.get_device_name(0) if pol.cuda else "", "dtype": str(pol.dtype),
           "load_s": round(load_s, 1), "prompt_tokens": len(call.prompt_ids), "completion_tokens": a.completion,
           "gen_step_s": round(statistics.median(gen), 4), "decode_tokens_per_s": round(a.completion / statistics.median(gen), 1),
           "train_seq_s": round(statistics.median(train), 4), "ref_seq_s": round(statistics.median(ref), 4),
           "env_step_s": round(env_s, 4), "env_measured": a.measure_env, "steps": a.steps, "keep_frac": a.keep_frac,
           "mem_gb": round(torch.cuda.max_memory_allocated() / 2 ** 30, 2) if pol.cuda else None}
    res.update(estimate(res["gen_step_s"], env_s, res["train_seq_s"], res["ref_seq_s"], a.steps, a.keep_frac, need))
    print(json.dumps(res, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

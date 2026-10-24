"""
AWOS Stage-A SFT (rejection-sampling fine-tuning) of Qwen3.5 with LoRA — Kaggle kernel script.

Runs as a Kaggle "script" kernel on GPU T4 x2 (fp16 only, sm_75), and on CPU for smoke tests:

    python train.py --config train_config.json --data DIR --out DIR      # CPU / local smoke
    (on Kaggle the config and data come from the attached dataset under /kaggle/input/)

Self-contained on purpose (Kaggle pushes one code file). Inputs, produced by
`python -m scaffold.rl.train.dataset` + `scaffold.rl.train.kaggle_driver stage`:
    train.jsonl / valid.jsonl   rows with "prompt" (the exact text llama-server renders from MicroAgent's
                                LocalGBNFModel request: Qwen3.5 template, enable_thinking=False, i.e.
                                ending "<|im_start|>assistant\\n<think>\\n\\n</think>\\n\\n") and
                                "completion" (reply + "<|im_end|>"), plus "messages"
    train_config.json           hyper-parameters (see DEFAULTS)
objective=dpo (goal G4, data from `python -m scaffold.rl.train.pairs`): rows also carry "chosen" / "rejected"
(the prompt is checked against the template the same way; "completion" == chosen). Loss per pair =
sft_weight * CE(chosen) - logsigmoid(beta * ((pi_c - ref_c) - (pi_r - ref_r))), logps summed over completion
tokens, reference = the same model with the LoRA adapter disabled (no grad). progress.jsonl adds dpo_loss,
sft_loss, reward_margin, pref_acc; evals (rank 0, every eval_steps) add eval_pref_acc / eval_margin on held-out
pairs. DPO_DEFAULTS size the run for ~3 h on T4 x2.
Outputs (in --out, /kaggle/working on Kaggle -> `kaggle kernels output`):
    adapter/                    PEFT LoRA adapter (adapter_model.safetensors + adapter_config.json)
    metrics.json                versions, GPUs, preflight + final losses, step timings, data stats
    train_log.jsonl             Trainer log history
    progress.jsonl              one row per log/eval step: step/total, %, loss, eval loss, action accuracy
                                (greedy replies on held-out rows), lr, grad norm, s/step, ETA, GPU util/memory
    progress.json               the latest progress row (a snapshot)
    gpu.jsonl                   nvidia-smi samples (utilization %, memory) every gpu_sample_s seconds

Progress: every log step prints one line to the kernel log, e.g.
    [12:03:41] step 120/370 32% | loss 0.214 | eval 0.119 | acc 71% | 41.8 s/step | ETA 2h54m | GPU 87% 87% 11.1/15.1 GB

Remote stop: every stop_check_steps steps rank 0 reads stop_url (a file on the repo's `rl-control`
branch, written by scripts/rl/kaggle_stop.sh). If it holds a UTC timestamp later than this run's start, all
ranks stop together, the adapter and metrics are saved and the kernel ends normally, so its output is kept
(a cancelled Kaggle run keeps nothing). The same synchronised check enforces max_hours.

Design notes
  * GatedDeltaNet: transformers' Qwen3.5 uses flash-linear-attention (fla) Triton kernels when the
    `fla` package imports, else its own pure-torch chunked gated-delta rule (fp32 inside,
    torch.linalg.solve_triangular), which runs on any CUDA arch incl. T4. Default gdn_impl="torch"
    blocks `fla` so the T4 run never depends on Triton kernels built for Ampere+/bf16.
  * Loss only on the completion; the vocabulary projection is computed only for the last
    len(completion)+1 positions (logits_to_keep): Qwen3.5's vocabulary is 248k, full logits for a
    1.1k-token prompt are ~1 GB fp32 per sequence and ~20% of the FLOPs, all masked out anyway.
  * One sequence per device per micro-step (no padding), gradient accumulation for the batch.
  * fp16 on T4: base weights fp16, LoRA weights fp32 (PEFT autocast), AMP + GradScaler (Trainer
    fp16=True). A preflight measures the base model's loss on validation rows in this dtype and aborts
    on inf/nan (Qwen3.5 was trained in bf16; fp16 activation overflow is the main T4 risk).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path

_START = time.time()
PINS = ["transformers==5.19.0", "peft==0.21.2", "accelerate==1.15.0"]
DEFAULTS = {
    "base_model": "Qwen/Qwen3.5-4B",
    "base_revision": "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",
    "lora_r": 16, "lora_alpha": 32, "lora_dropout": 0.0,
    # every projection of every decoder block of the LANGUAGE model (not the vision tower):
    # full attention (q/k/v/o), GatedDeltaNet linear attention (fused qkv, z gate, out), MLP.
    # The tiny per-head a/b gate projections (hidden -> 16/32) stay frozen.
    "target_regex": (r"model\.language_model\.layers\.\d+\.(self_attn\.(q_proj|k_proj|v_proj|o_proj)"
                     r"|linear_attn\.(in_proj_qkv|in_proj_z|out_proj)|mlp\.(gate_proj|up_proj|down_proj))"),
    "learning_rate": 1e-4, "warmup_ratio": 0.05, "lr_scheduler": "cosine", "weight_decay": 0.0,
    # 1 epoch by default: the first full run (2 epochs, 5.9k rows) needed ~9 h of the 12 h session
    "epochs": 1.0, "max_steps": -1, "grad_accum": 8, "max_seq_len": 4096,
    # save_steps 50: a LoRA checkpoint is small, and a hang/crash then loses at most ~25 min (recover_adapter)
    "gradient_checkpointing": True, "seed": 0, "logging_steps": 10, "eval_steps": 200, "save_steps": 50,
    "hang_minutes": 25, "startup_grace_min": 45,
    # host RAM (2026-10-23: a rank was OOM-killed at step ~158 twice): stop + save below ram_low_gb free,
    # kill + recover if still below ram_critical_gb ram_grace_s later; hygiene every mem_gc_steps
    "ram_low_gb": 3.0, "ram_critical_gb": 1.2, "ram_grace_s": 360, "mem_gc_steps": 25, "malloc_arena_max": 2,
    "eval_rows": 200, "preflight_rows": 16, "template_check_rows": 32,
    "gdn_impl": "torch",          # "torch" (blocks fla) | "auto" (use fla if installed)
    "dtype": "auto",              # auto: bf16 if supported, else fp16 on CUDA, fp32 on CPU | fp16 | bf16 | fp32
    "pip_install": "auto",        # auto: install PINS on Kaggle when versions differ
    "max_hours": 10.5,            # stop + save before Kaggle's 12 h session limit kills the run
    "stop_url": "https://raw.githubusercontent.com/savabs/AWOS_coding_agent-/rl-control/kaggle/STOP",
    "stop_check_steps": 5,        # ~3.5 min at 42 s/step; also the granularity of max_hours
    "eval_gen_rows": 48,          # held-out rows answered greedily at each eval -> action accuracy (0 = off)
    "gpu_sample_s": 30,
    "ntfy_topic": "awos-rl-be26ddc76649",   # live progress: https://ntfy.sh/awos-rl-be26ddc76649 ("" = off)
    "run_name": "",                         # label on every ping when several runs share the topic
    # continue instead of starting over: resume_from = checkpoint path or the slug of an earlier kernel
    # attached as a kernel source; resume_mode exact (same data: optimizer + schedule + step) | warm (weights)
    "resume_from": "", "resume_mode": "warm",
    # "sft": completion CE on prompt -> completion rows (Stage A). "dpo": preference pairs (goal G4):
    # sft_weight * CE(chosen) - logsigmoid(beta * ((pi_c - ref_c) - (pi_r - ref_r))), logps summed over
    # completion tokens, reference = the same model with the LoRA adapter disabled.
    "objective": "sft",
    "beta": 0.1, "sft_weight": 1.0,
}
#: defaults that change with objective=dpo (train_config.json still wins). Sized for ~3 h on T4 x2: a pair
#: costs 2 policy forward+backward + 2 no-grad reference forwards (~11 s per pair per GPU at ~1.1k-token
#: prompts, from SFT's ~4.4 s per sequence), so 4 pairs/GPU/step ~45 s: 1.3k train pairs ~2 h, plus ~15 min
#: of evals and ~15 min of start-up. max_hours stops (and saves) a slower run in time.
DPO_DEFAULTS = {
    "learning_rate": 2e-5, "warmup_ratio": 0.1, "epochs": 1.0, "grad_accum": 4,
    "logging_steps": 5, "eval_steps": 40, "save_steps": 20, "max_hours": 3.0,
    "eval_rows": 32,              # valid pairs scored at each eval (pref_acc, margin): 4 no-grad forwards each
    "eval_gen_rows": 16,          # greedy action accuracy against the chosen reply (rank 0)
}


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def on_kaggle() -> bool:
    return Path("/kaggle/working").exists()


def ensure_deps(mode: str) -> None:
    if mode == "never" or (mode == "auto" and not on_kaggle()):
        return
    try:
        import peft  # noqa: F401
        import transformers
        if f"transformers=={transformers.__version__}" in PINS and f"peft=={peft.__version__}" in PINS:
            return
    except Exception:
        pass
    log("pip install", *PINS)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *PINS])
    # Kaggle's image ships a torchao that peft 0.21 rejects: its LoRA dispatcher raises
    # ImportError in get_peft_model (trial 1, 2026-10-22). We don't use torchao; remove it.
    subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "-q", "torchao"])


def block_fla() -> None:
    """Make `import fla` fail so transformers uses its torch gated-delta path."""
    sys.modules["fla"] = None  # type: ignore[assignment]


def find_input(name: str, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    hits = sorted(Path("/kaggle/input").rglob(name)) if Path("/kaggle/input").exists() else []
    if not hits:
        raise SystemExit(f"{name} not found (pass --data / --config)")
    return hits[0].parent


def read_rows(path: Path) -> list:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def check_template(tok, rows: list, n: int, seed: int) -> int:
    """Our pre-rendered prompt == the tokenizer's own chat template with LocalGBNFModel's kwargs."""
    for r in random.Random(seed).sample(rows, min(n, len(rows))):
        want = tok.apply_chat_template(r["messages"][:2], tokenize=False, add_generation_prompt=True,
                                       enable_thinking=False)
        if want != r["prompt"]:
            i = next((j for j, (x, y) in enumerate(zip(want, r["prompt"])) if x != y), min(len(want), len(r["prompt"])))
            raise SystemExit(f"template mismatch at {i}: tokenizer={want[i-30:i+30]!r} data={r['prompt'][i-30:i+30]!r}")
    im_end = tok.convert_tokens_to_ids("<|im_end|>")
    if tok.encode("<|im_end|>", add_special_tokens=False) != [im_end]:
        raise SystemExit("<|im_end|> is not a single special token")
    return min(n, len(rows))


def encode(tok, rows: list, max_len: int) -> tuple:
    out, dropped = [], 0
    for r in rows:
        p = tok.encode(r["prompt"], add_special_tokens=False)
        c = tok.encode(r["completion"], add_special_tokens=False)
        if len(p) + len(c) > max_len:
            dropped += 1
            continue
        out.append({"input_ids": p + c, "prompt_len": len(p), "completion": r["completion"]})
    return out, dropped


def encode_pairs(tok, rows: list, max_len: int) -> tuple:
    """Preference rows -> {"input_ids": prompt+chosen, "prompt_len", "completion", "rej_ids": prompt+rejected}.
    The chosen side keeps the SFT field names, so the SFT loss, preflight and action accuracy reuse it."""
    out, dropped = [], 0
    for r in rows:
        p = tok.encode(r["prompt"], add_special_tokens=False)
        c = tok.encode(r["chosen"], add_special_tokens=False)
        j = tok.encode(r["rejected"], add_special_tokens=False)
        if len(p) + max(len(c), len(j)) > max_len:
            dropped += 1
            continue
        out.append({"input_ids": p + c, "prompt_len": len(p), "completion": r["chosen"], "rej_ids": p + j,
                    "safety": bool((r.get("meta") or {}).get("safety"))})
    return out, dropped


def check_pair_rows(rows: list) -> None:
    for i, r in enumerate(rows):
        for k in ("prompt", "chosen", "rejected", "messages"):
            if not r.get(k):
                raise SystemExit(f"objective=dpo: row {i} has no {k!r} (build the data with scaffold.rl.train.pairs)")
        if not (r["chosen"].endswith("<|im_end|>") and r["rejected"].endswith("<|im_end|>")):
            raise SystemExit(f"objective=dpo: row {i}: chosen/rejected must end with <|im_end|>")
        if r["chosen"] == r["rejected"]:
            raise SystemExit(f"objective=dpo: row {i}: chosen == rejected")


def stats(ex: list) -> dict:
    if not ex:
        return {}
    xs = sorted(len(e["input_ids"]) for e in ex)
    comp = [len(e["input_ids"]) - e["prompt_len"] for e in ex]
    return {"n": len(xs), "p50": xs[len(xs) // 2], "p99": xs[int(0.99 * (len(xs) - 1))], "max": xs[-1],
            "tokens": sum(xs), "completion_mean": round(sum(comp) / len(comp), 2), "completion_max": max(comp)}


def action_line(text: str) -> str:
    """The action of a MicroAgent reply: drop '# reason' lines and the end token, keep the first line."""
    text = text.split("<|im_end|>", 1)[0]
    lines = [x.strip() for x in text.strip().splitlines() if x.strip() and not x.strip().startswith("#")]
    return lines[0] if lines else ""


def action_match(pred: str, gold: str) -> tuple:
    """(exact action match, same verb) between a generated reply and the training target."""
    p, g = action_line(pred), action_line(gold)
    verb = lambda s: s.split("(", 1)[0]          # noqa: E731
    return p == g and bool(g), bool(g) and verb(p) == verb(g)


def stop_requested(body: str, run_start: float) -> bool:
    """The stop file holds a UTC timestamp (ISO 8601 or epoch seconds); only a stop written after this
    run started counts, so a stale file never stops the next run."""
    s = (body or "").strip().splitlines()[0].strip() if (body or "").strip() else ""
    if not s:
        return False
    try:
        t = float(s)
    except ValueError:
        try:
            from datetime import datetime, timezone
            t = datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
        except ValueError:
            return False
    return t > run_start


def fmt_dur(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def progress_line(p: dict) -> str:
    bits = [f"step {p['step']}/{p['total']} {p['pct']:.0f}%"]
    for k, f in (("loss", "loss {:.3f}"), ("eval_loss", "eval {:.3f}")):
        if p.get(k) is not None:
            bits.append(f.format(p[k]))
    # objective=dpo only (SFT rows carry none of these keys, so the SFT line is unchanged)
    for k, f in (("dpo_loss", "dpo {:.3f}"), ("sft_loss", "sft {:.3f}"), ("pref_acc", "pref {:.0%}"),
                 ("reward_margin", "margin {:+.2f}"), ("eval_pref_acc", "eval pref {:.0%}"),
                 ("eval_margin", "eval margin {:+.2f}")):
        if p.get(k) is not None:
            bits.append(f.format(p[k]))
    if p.get("action_acc") is not None:
        bits.append(f"acc {p['action_acc'] * 100:.0f}%")
    if p.get("s_per_step"):
        bits.append(f"{p['s_per_step']:.1f} s/step")
    if p.get("eta_s") is not None:
        bits.append(f"ETA {fmt_dur(p['eta_s'])}")
    g = p.get("gpu") or []
    if g:
        bits.append("GPU " + " ".join(f"{x['util']}%" for x in g)
                    + f" {max(x['mem_used'] for x in g) / 1024:.1f}/{g[0]['mem_total'] / 1024:.1f} GB")
    if ram_line(p.get("ram") or {}):
        bits.append(ram_line(p["ram"]))
    return " | ".join(bits)


def ntfy(cfg: dict, message: str, title: str = "", priority: str = "default", tags: str = "") -> None:
    """Live progress ping to https://ntfy.sh/<ntfy_topic> (phone app or browser; the GitHub watcher reads the
    same topic). Only training numbers are sent. Never raises, never blocks more than a few seconds."""
    topic = cfg.get("ntfy_topic")
    if not topic:
        return
    if cfg.get("run_name"):                        # several runs share the topic: label each ping
        title = f"[{cfg['run_name']}] {title}"
    try:
        import urllib.request
        req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"), method="POST")
        if title:
            req.add_header("Title", title)
        req.add_header("Priority", priority)
        if tags:
            req.add_header("Tags", tags)
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def gpu_sample() -> list:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used,memory.total",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    rows = []
    for ln in out.strip().splitlines():
        try:
            i, u, m, t = (int(float(x)) for x in ln.split(","))
            rows.append({"gpu": i, "util": u, "mem_used": m, "mem_total": t})
        except ValueError:
            pass
    return rows


def mem_info(path: str = "/proc/meminfo") -> dict:
    """Machine RAM (GB) from /proc/meminfo: {"avail_gb", "total_gb"}; {} where unavailable (not Linux)."""
    try:
        kv = {}
        for ln in Path(path).read_text().splitlines():
            k, _, v = ln.partition(":")
            kv[k.strip()] = int(v.split()[0]) / 1024 / 1024          # kB -> GB
        return {"avail_gb": round(kv["MemAvailable"], 2), "total_gb": round(kv["MemTotal"], 2)}
    except (OSError, KeyError, ValueError, IndexError):
        return {}


def group_rss_gb(pgid: int, proc: str = "/proc") -> list:
    """Resident memory (GB) of every process in a process group: [{"pid", "rss_gb"}] (the training ranks)."""
    rows = []
    for d in Path(proc).glob("[0-9]*"):
        try:
            stat = (d / "stat").read_text()
            fields = stat[stat.rindex(")") + 2:].split()          # after "(comm) "
            if int(fields[2]) != pgid:                             # field 5 of stat = pgrp
                continue
            rss = next(ln for ln in (d / "status").read_text().splitlines() if ln.startswith("VmRSS:"))
            gb = int(rss.split()[1]) / 1024 / 1024
            if gb > 0.5:                                           # skip shells / small helpers
                rows.append({"pid": int(d.name), "rss_gb": round(gb, 2)})
        except (OSError, ValueError, StopIteration, IndexError):
            continue
    return sorted(rows, key=lambda r: -r["rss_gb"])


def ram_line(r: dict) -> str:
    if not r.get("total_gb"):
        return ""
    procs = "+".join(f"{x['rss_gb']:.1f}" for x in (r.get("procs") or [])[:3])
    return f"RAM free {r['avail_gb']:.1f}/{r['total_gb']:.0f} GB" + (f" (ranks {procs})" if procs else "")


def malloc_trim() -> None:
    """Return freed heap pages to the OS (glibc): counters the slow RSS growth from fragmentation."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def find_resume_checkpoint(spec: str, inputs: Path = Path("/kaggle/input")):
    """A checkpoint to continue from: ``spec`` is a path, or the slug of an earlier kernel attached as a
    kernel source (its output appears under /kaggle/input/<slug>/). Returns (path, full) where full means
    optimizer + scheduler + trainer state are present (exact resume possible), or None."""
    if not spec:
        return None
    p = Path(spec)
    cands = [p] if (p / "adapter_model.safetensors").exists() else []
    if not cands:
        # a kernel source may mount at /kaggle/input/<slug>/ or deeper (e.g. notebooks/<owner>/<slug>/)
        roots = [p] if p.exists() else [] if "/" in spec else [d for depth in range(1, 5)
                                        for d in inputs.glob("/".join(["*"] * (depth - 1) + [f"{spec}*"]))
                                        if d.is_dir()]
        for root in roots:
            cands += [d for d in root.rglob("checkpoint-*") if (d / "adapter_model.safetensors").exists()]
            cands += [d for d in root.rglob("adapter") if (d / "adapter_model.safetensors").exists()]

    def step(d):
        try:
            return int(d.name.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            return -1                                   # a final adapter/ dir: lowest priority for "exact"
    if not cands:
        return None
    best = max(cands, key=step)
    full = all((best / f).exists() for f in ("optimizer.pt", "scheduler.pt", "trainer_state.json"))
    return best, full


def input_tree(inputs: Path = Path("/kaggle/input"), depth: int = 4, limit: int = 40) -> str:
    """What is mounted under /kaggle/input (dirs only, a few levels): shown when a resume finds nothing."""
    rows = [str(d.relative_to(inputs)) for k in range(1, depth + 1) for d in inputs.glob("/".join(["*"] * k))
            if d.is_dir()]
    return ", ".join(sorted(rows)[:limit]) or "(empty)"


def check_resume(cfg: dict, inputs: Path = Path("/kaggle/input")):
    """Fail fast, before any model load, when resume_from is set but unusable. Returns the find result."""
    spec = cfg.get("resume_from") or ""
    if not spec:
        return None
    found = find_resume_checkpoint(spec, inputs)
    if found is None:
        raise SystemExit(f"resume_from={spec!r}: no checkpoint found under {inputs} "
                         f"(attach the kernel as a source); mounted: {input_tree(inputs)}")
    if cfg.get("resume_mode", "warm") == "exact" and not found[1]:
        raise SystemExit(f"resume_mode=exact needs optimizer/scheduler/trainer state in {found[0]}")
    return found


def latest_checkpoint(out: Path):
    """Newest Trainer checkpoint holding a PEFT adapter, or None."""
    best = None
    for d in (out / "checkpoints").glob("checkpoint-*"):
        try:
            step = int(d.name.rsplit("-", 1)[1])
        except ValueError:
            continue
        if (d / "adapter_model.safetensors").exists() and (best is None or step > best[0]):
            best = (step, d)
    return best


def recover_adapter(out: Path, reason: str) -> bool:
    """After a hung or crashed run: promote the newest checkpoint to out/adapter (+ metrics, log history),
    so the kernel can end normally and Kaggle keeps the output. Returns True when an adapter is in place."""
    import shutil
    if (out / "adapter" / "adapter_model.safetensors").exists():
        return True
    best = latest_checkpoint(out)
    if best is None:
        log(f"no checkpoint to recover ({reason})")
        return False
    step, d = best
    (out / "adapter").mkdir(parents=True, exist_ok=True)
    for f in ("adapter_model.safetensors", "adapter_config.json"):
        if (d / f).exists():
            shutil.copy2(d / f, out / "adapter" / f)
    hist = []
    if (d / "trainer_state.json").exists():
        hist = json.loads((d / "trainer_state.json").read_text()).get("log_history", [])
        (out / "train_log.jsonl").write_text("".join(json.dumps(x) + "\n" for x in hist))
    if not (out / "metrics.json").exists():
        (out / "metrics.json").write_text(json.dumps({"status": "recovered_from_checkpoint", "reason": reason,
                                                      "global_steps": step, "log_history_rows": len(hist)},
                                                     indent=2) + "\n")
    log(f"recovered the adapter from {d.name} ({reason})")
    return True


def supervise(cmd: list, out: Path, cfg: dict) -> int:
    """Run torchrun under a watchdog that lives OUTSIDE the training processes (the first full run froze
    silently at step 168 for 10 h: a DDP deadlock leaves in-process callbacks no chance to act).
    Kills the run when progress.json has not changed for hang_minutes (after a start-up grace period) or
    when max_hours passes, dumping every rank's stacks first; then recovers the newest checkpoint and
    returns 0 so the kernel completes and its output is kept."""
    import signal
    t0 = time.time()
    prog = out / "progress.json"
    env = dict(os.environ)
    # glibc malloc arenas fragment with multi-threaded torch: the 2026-10-23 runs grew host RAM until the OS
    # killed a rank at step ~158-168. Fewer arenas + periodic malloc_trim (MemHygiene) keep RSS flat.
    env.setdefault("MALLOC_ARENA_MAX", str(cfg.get("malloc_arena_max", 2)))
    err_file = out / "error.txt"                        # a rank's fatal error, quoted in the ended-early ping
    env["AWOS_ERROR_FILE"] = str(err_file)
    p = subprocess.Popen(cmd, start_new_session=True, env=env)
    reason = None
    low_since = None
    stop_file = out / "STOP_LOCAL"
    while p.poll() is None:
        time.sleep(cfg.get("watch_poll_s", 30))
        now = time.time()
        # RAM watch: log machine + per-rank memory; ask the ranks to stop and save before the OS kills one
        mem = cfg.get("_mem_fn", mem_info)()
        if mem:
            mem["procs"] = group_rss_gb(p.pid)
            mem["t"] = round(now - _START, 1)
            with (out / "ram.jsonl").open("a") as fh:
                fh.write(json.dumps(mem) + "\n")
            (out / "ram.json").write_text(json.dumps(mem) + "\n")
            if mem["avail_gb"] < cfg["ram_low_gb"] and not stop_file.exists():
                stop_file.write_text(f"low RAM: {mem['avail_gb']:.1f} GB free\n")
                low_since = now
                log(f"watchdog: {ram_line(mem)} < {cfg['ram_low_gb']} GB: asking the run to stop and save")
                ntfy(cfg, f"{ram_line(mem)}: stopping cleanly and saving before memory runs out",
                     title="AWOS training low RAM", priority="high", tags="warning")
            if (low_since and mem["avail_gb"] < cfg["ram_critical_gb"]
                    and now - low_since > cfg.get("ram_grace_s", 360)):
                reason = f"RAM still critical ({mem['avail_gb']:.1f} GB free) after the stop request"
        last = prog.stat().st_mtime if prog.exists() else t0
        if reason:
            pass
        elif now - _START > cfg["max_hours"] * 3600 + 900:        # the in-process stop had 15 min to act
            reason = f"max_hours {cfg['max_hours']} passed"
        elif now - t0 > cfg["startup_grace_min"] * 60 and now - last > cfg["hang_minutes"] * 60:
            reason = f"no progress for {(now - last) / 60:.0f} min"
        if reason:
            log(f"watchdog: {reason}; dumping stacks and stopping the run")
            ntfy(cfg, f"watchdog: {reason}. Stopping and recovering the newest checkpoint.",
                 title="AWOS training STALLED", priority="high", tags="warning")
            try:
                os.killpg(p.pid, signal.SIGUSR1)
                time.sleep(cfg.get("stack_dump_wait_s", 20))
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=120)
            except Exception:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception:
                    pass
            break
    rc = p.wait()
    if rc == 0 and reason is None:
        return 0
    ok = recover_adapter(out, reason or f"training exited with code {rc}")
    best = latest_checkpoint(out)
    err = err_file.read_text()[-400:].strip() if err_file.exists() else ""
    ntfy(cfg, (f"recovered the adapter from step {best[0]}" if ok and best else "NO checkpoint to recover")
         + f" ({reason or f'exit code {rc}'})" + (f"; error: {err}" if err else ""),
         title="AWOS training ended early",
         priority="high", tags="warning" if ok else "rotating_light")
    return 0 if ok else (rc or 1)


def completion_loss(model, input_ids, prompt_len):
    """Mean CE over completion tokens of ONE sequence, projecting only the needed positions."""
    import torch.nn.functional as F
    T = input_ids.shape[1]
    keep = T - prompt_len + 1                      # positions prompt_len-1 .. T-1 (the last is unused)
    out = model(input_ids=input_ids, logits_to_keep=keep, use_cache=False)
    logits = out.logits[:, :-1].float()            # predicts tokens prompt_len .. T-1
    targets = input_ids[:, prompt_len:]
    return F.cross_entropy(logits.reshape(-1, logits.shape[-1]), targets.reshape(-1))


def completion_logps(model, input_ids, prompt_len):
    """(SUM of log p over the completion tokens of ONE sequence, number of completion tokens), with the
    same logits_to_keep trick as completion_loss (fp32 log-softmax over the kept positions only)."""
    import torch.nn.functional as F
    T = input_ids.shape[1]
    keep = T - prompt_len + 1
    out = model(input_ids=input_ids, logits_to_keep=keep, use_cache=False)
    logits = out.logits[:, :-1].float()
    targets = input_ids[:, prompt_len:]
    lp = -F.cross_entropy(logits.reshape(-1, logits.shape[-1]), targets.reshape(-1), reduction="sum")
    return lp, targets.numel()


def dpo_terms(pi_c, pi_r, ref_c, ref_r, beta: float):
    """-> (DPO loss, z) with z = beta * ((pi_c - ref_c) - (pi_r - ref_r)) the implicit reward margin."""
    import torch.nn.functional as F
    z = beta * ((pi_c - ref_c) - (pi_r - ref_r))
    return -F.logsigmoid(z), z


def dpo_pair_loss(model, ref_model, chosen_ids, rejected_ids, prompt_len, beta: float, sft_weight: float):
    """Loss of ONE preference pair: sft_weight * mean CE(chosen) + DPO. `model` gets the gradient (the DDP
    wrapper on multi-GPU); `ref_model` is the bare PeftModel, run with its adapter disabled and no grad,
    BEFORE the policy forwards (no autograd graph is alive during it). Returns (loss, float metrics)."""
    import torch
    with torch.no_grad(), ref_model.disable_adapter():
        ref_c, _ = completion_logps(ref_model, chosen_ids, prompt_len)
        ref_r, _ = completion_logps(ref_model, rejected_ids, prompt_len)
    pi_c, n_c = completion_logps(model, chosen_ids, prompt_len)
    pi_r, _ = completion_logps(model, rejected_ids, prompt_len)
    sft = -pi_c / max(1, n_c)
    dpo, z = dpo_terms(pi_c, pi_r, ref_c.detach(), ref_r.detach(), beta)
    loss = dpo + sft_weight * sft if sft_weight else dpo
    zf = float(z.detach())
    return loss, {"dpo_loss": float(dpo.detach()), "sft_loss": float(sft.detach()), "reward_margin": zf,
                  "pref_acc": float(zf > 0), "policy_pref": float(float(pi_c.detach()) > float(pi_r.detach()))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None, help="train_config.json (default: found under /kaggle/input)")
    ap.add_argument("--data", default=None, help="dir with train.jsonl / valid.jsonl (default: /kaggle/input)")
    ap.add_argument("--out", default=None, help="output dir (default /kaggle/working or ./out)")
    ap.add_argument("--base-model", default=None, help="override base_model (e.g. a local tiny checkpoint)")
    ap.add_argument("--max-steps", type=int, default=None)
    a = ap.parse_args(argv)

    cfg_path = Path(a.config) if a.config else find_input("train_config.json", None) / "train_config.json"
    file_cfg = json.loads(cfg_path.read_text())
    cfg = {**DEFAULTS, **(DPO_DEFAULTS if file_cfg.get("objective") == "dpo" else {}), **file_cfg}
    if cfg["objective"] not in ("sft", "dpo"):
        raise SystemExit(f"objective must be 'sft' or 'dpo', got {cfg['objective']!r}")
    dpo_mode = cfg["objective"] == "dpo"
    if a.base_model:
        cfg["base_model"], cfg["base_revision"] = a.base_model, None
    if a.max_steps is not None:
        cfg["max_steps"] = a.max_steps
    data = find_input("train.jsonl", a.data)
    out = Path(a.out or ("/kaggle/working" if on_kaggle() else "out"))
    out.mkdir(parents=True, exist_ok=True)

    # torchrun re-launch for multi-GPU (Kaggle T4 x2): one process per GPU, DDP via Trainer.
    if "LOCAL_RANK" not in os.environ and not os.environ.get("AWOS_NO_TORCHRUN"):
        try:                                            # a bad resume setting costs seconds, not a model load
            check_resume(cfg)
        except SystemExit as e:
            log(str(e))
            ntfy(cfg, str(e)[:900], title="AWOS training not started", priority="high", tags="rotating_light")
            raise
        ensure_deps(cfg["pip_install"])
        import torch
        n = torch.cuda.device_count()
        if n > 1:
            log(f"{n} GPUs -> torchrun")
            cmd = [sys.executable, "-m", "torch.distributed.run", f"--nproc_per_node={n}", __file__,
                   "--config", str(cfg_path), "--data", str(data), "--out", str(out)]
            if a.base_model:
                cmd += ["--base-model", a.base_model]
            if a.max_steps is not None:
                cmd += ["--max-steps", str(a.max_steps)]
            return supervise(cmd, out, cfg)
    # every rank dumps all thread stacks on SIGUSR1 (the supervisor sends it before killing a hung run)
    import faulthandler
    import signal
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    if cfg["gdn_impl"] == "torch":
        block_fla()

    import torch
    import transformers
    from peft import LoraConfig, get_peft_model
    from transformers import (AutoModelForImageTextToText, AutoTokenizer, Trainer, TrainerCallback,
                              TrainingArguments)

    rank = int(os.environ.get("RANK", "0"))
    world = int(os.environ.get("WORLD_SIZE", "1"))
    main_proc = rank == 0
    cuda = torch.cuda.is_available()
    if cfg["dtype"] == "auto":
        dtype = (torch.bfloat16 if cuda and torch.cuda.is_bf16_supported(including_emulation=False)
                 else torch.float16 if cuda else torch.float32)
    else:
        dtype = {"fp16": torch.float16, "bf16": torch.bfloat16, "fp32": torch.float32}[cfg["dtype"]]
    env = {"torch": torch.__version__, "transformers": transformers.__version__,
           "peft": __import__("peft").__version__, "cuda": cuda, "world": world, "dtype": str(dtype),
           "gpus": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())] if cuda else [],
           "fla_importable": _importable("fla"), "gdn_impl": cfg["gdn_impl"]}
    if main_proc:
        log("env", json.dumps(env))
    random.seed(cfg["seed"])
    torch.manual_seed(cfg["seed"])

    tok = AutoTokenizer.from_pretrained(cfg["base_model"], revision=cfg["base_revision"])
    train_rows, valid_rows = read_rows(data / "train.jsonl"), read_rows(data / "valid.jsonl")
    if dpo_mode:
        check_pair_rows(train_rows + valid_rows)
    n_checked = check_template(tok, train_rows + valid_rows, cfg["template_check_rows"], cfg["seed"])
    enc = encode_pairs if dpo_mode else encode
    train_ex, d1 = enc(tok, train_rows, cfg["max_seq_len"])
    valid_ex, d2 = enc(tok, valid_rows, cfg["max_seq_len"])
    random.Random(cfg["seed"]).shuffle(valid_ex)
    valid_ex = valid_ex[: cfg["eval_rows"]]
    data_stats = {"train": stats(train_ex), "valid": stats(valid_ex), "dropped_too_long": d1 + d2,
                  "template_checked": n_checked}
    if dpo_mode:
        data_stats.update({"objective": "dpo", "train_pairs": len(train_ex), "valid_pairs": len(valid_ex),
                           "train_safety_pairs": sum(e["safety"] for e in train_ex),
                           "rejected_completion_mean": round(sum(len(e["rej_ids"]) - e["prompt_len"]
                                                                 for e in train_ex) / max(1, len(train_ex)), 2)})
    if main_proc:
        log("data", json.dumps(data_stats))

    t0 = time.time()
    model = AutoModelForImageTextToText.from_pretrained(cfg["base_model"], revision=cfg["base_revision"], dtype=dtype)
    dev = torch.device("cuda", int(os.environ.get("LOCAL_RANK", 0))) if cuda else torch.device("cpu")
    model.to(dev)
    load_s = time.time() - t0

    def eval_loss(m, rows) -> float:
        m.eval()
        tot, n = 0.0, 0
        with torch.no_grad(), torch.autocast(dev.type, dtype=dtype, enabled=cuda and dtype != torch.float32):
            for e in rows:
                ids = torch.tensor([e["input_ids"]], device=dev)
                tot += completion_loss(m, ids, e["prompt_len"]).item()
                n += 1
        return tot / max(1, n)

    pre = eval_loss(model, valid_ex[: cfg["preflight_rows"]] or train_ex[: cfg["preflight_rows"]])
    if main_proc:
        log(f"preflight: base completion loss {pre:.4f} in {dtype} ({load_s:.0f}s load)")
        ntfy(cfg, f"model loaded on {len(env['gpus'])} GPU(s) {env['gpus'][:1]}; base loss {pre:.3f}; "
                  f"{data_stats['train'].get('n', 0)} train rows; starting", title="AWOS training started",
             tags="rocket")
    if not math.isfinite(pre):
        raise SystemExit(f"base model loss is {pre} in {dtype}: fp16 overflow? use dtype=fp32 with the 2B "
                         "(fits a T4), or a GPU with bf16")

    if cfg["gradient_checkpointing"]:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    lcfg = LoraConfig(r=cfg["lora_r"], lora_alpha=cfg["lora_alpha"], lora_dropout=cfg["lora_dropout"],
                      target_modules=cfg["target_regex"], bias="none", task_type="CAUSAL_LM")
    model = get_peft_model(model, lcfg)
    # never restart from scratch when a checkpoint exists: resume_from = path or earlier kernel slug
    resume = check_resume(cfg)
    resume_exact = None
    if resume:
        ck, full = resume
        mode = cfg.get("resume_mode", "warm")
        if mode == "exact":
            resume_exact = str(ck)                      # Trainer restores weights, optimizer, schedule, step
        else:                                           # warm start: trained weights, fresh optimizer + schedule
            from peft.utils import set_peft_model_state_dict
            from safetensors.torch import load_file
            res_load = set_peft_model_state_dict(model, load_file(str(ck / "adapter_model.safetensors")))
            unexpected = len(getattr(res_load, "unexpected_keys", []) or [])
            if unexpected:
                raise SystemExit(f"warm start from {ck}: {unexpected} adapter keys do not match this LoRA config")
        if main_proc:
            log(f"resume: {mode} from {ck} (full state: {full})")
            ntfy(cfg, f"continuing from {ck.name} ({mode})", title="AWOS training resume", tags="repeat")
    n_train = sum(p.numel() for p in model.parameters() if p.requires_grad)
    targets = sorted({n.split("model.language_model.layers.")[-1].split(".", 1)[-1].rsplit(".lora_A", 1)[0]
                      for n, _ in model.named_parameters() if "lora_A" in n})
    if main_proc:
        log(f"LoRA: {n_train / 1e6:.2f}M trainable params on {targets}")
    if not any("linear_attn" in t for t in targets):
        raise SystemExit("no GatedDeltaNet projections matched target_regex")

    def dpo_eval(m, rows) -> dict:
        """Preference metrics on held-out pairs: pref_acc = fraction where the implicit reward prefers the
        chosen reply ((pi_c - ref_c) > (pi_r - ref_r)), the mean beta-scaled margin, the DPO loss, and
        eval_loss = CE on the chosen completion (same number as SFT's eval loss)."""
        if not rows:
            return {}
        was_training = m.training
        m.eval()
        sums: dict = {}
        try:
            with torch.no_grad(), torch.autocast(dev.type, dtype=dtype, enabled=cuda and dtype != torch.float32):
                for e in rows:
                    c = torch.tensor([e["input_ids"]], device=dev)
                    r = torch.tensor([e["rej_ids"]], device=dev)
                    _, mt = dpo_pair_loss(m, m, c, r, e["prompt_len"], cfg["beta"], cfg["sft_weight"])
                    for k, v in mt.items():
                        sums[k] = sums.get(k, 0.0) + v
        finally:
            m.train(was_training)
        n = len(rows)
        return {"eval_pref_acc": round(sums["pref_acc"] / n, 4), "eval_margin": round(sums["reward_margin"] / n, 4),
                "eval_dpo_loss": round(sums["dpo_loss"] / n, 4), "eval_loss": round(sums["sft_loss"] / n, 4),
                "eval_policy_pref": round(sums["policy_pref"] / n, 4), "eval_pairs": n}

    if dpo_mode:
        # LoRA B starts at 0: the policy IS the reference, so the margin must be ~0 and the DPO loss log 2.
        # A real margin here means disable_adapter() is not giving the base model: abort before training.
        chk = dpo_eval(model, (valid_ex or train_ex)[:2])
        if main_proc:
            log(f"dpo preflight: margin {chk['eval_margin']:+.5f} (want 0), dpo loss {chk['eval_dpo_loss']:.4f} "
                f"(want {math.log(2):.4f}), policy prefers chosen on {chk['eval_policy_pref']:.0%}")
        if not math.isfinite(chk["eval_dpo_loss"]) or abs(chk["eval_margin"]) > 0.05:
            raise SystemExit(f"dpo preflight failed: {chk}")

    class SeqDataset(torch.utils.data.Dataset):
        def __init__(self, ex):
            self.ex = ex

        def __len__(self):
            return len(self.ex)

        def __getitem__(self, i):
            return self.ex[i]

    def collate(batch):
        (e,) = batch
        return {"input_ids": torch.tensor([e["input_ids"]]), "prompt_len": e["prompt_len"]}

    def collate_pair(batch):
        (e,) = batch
        return {"input_ids": torch.tensor([e["input_ids"]]), "rej_ids": torch.tensor([e["rej_ids"]]),
                "prompt_len": e["prompt_len"]}

    class DPOTrainer(Trainer):
        """One preference pair per device per micro-step; the window means of the DPO metrics are added to
        every logged "loss" (rank 0's own pairs)."""
        _acc: dict = {}

        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            ref = self.accelerator.unwrap_model(model)
            loss, mt = dpo_pair_loss(model, ref, inputs["input_ids"], inputs["rej_ids"], inputs["prompt_len"],
                                     cfg["beta"], cfg["sft_weight"])
            for k, v in mt.items():
                self._acc.setdefault(k, []).append(v)
            return (loss, None) if return_outputs else loss

        def prediction_step(self, model, inputs, prediction_loss_only, ignore_keys=None):
            with torch.no_grad():
                loss = completion_loss(model, inputs["input_ids"].to(model.device), inputs["prompt_len"])
            return loss.detach(), None, None

        def log(self, logs, *args, **kwargs):
            if "loss" in logs and self._acc:
                logs = {**logs, **{k: round(sum(v) / len(v), 4) for k, v in self._acc.items()}}
                self._acc = {}
            return super().log(logs, *args, **kwargs)

    class SFTTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            loss = completion_loss(model, inputs["input_ids"], inputs["prompt_len"])
            return (loss, None) if return_outputs else loss

        def prediction_step(self, model, inputs, prediction_loss_only, ignore_keys=None):
            with torch.no_grad():
                loss = completion_loss(model, inputs["input_ids"].to(model.device), inputs["prompt_len"])
            return loss.detach(), None, None

    steps_per_epoch = math.ceil(len(train_ex) / (cfg["grad_accum"] * world))
    targs = TrainingArguments(
        output_dir=str(out / "checkpoints"), per_device_train_batch_size=1, per_device_eval_batch_size=1,
        gradient_accumulation_steps=cfg["grad_accum"], learning_rate=cfg["learning_rate"],
        num_train_epochs=cfg["epochs"], max_steps=cfg["max_steps"], warmup_steps=max(1, int(cfg["warmup_ratio"] * (
            cfg["max_steps"] if cfg["max_steps"] > 0 else steps_per_epoch * cfg["epochs"]))),
        lr_scheduler_type=cfg["lr_scheduler"], weight_decay=cfg["weight_decay"],
        fp16=cuda and dtype == torch.float16, bf16=cuda and dtype == torch.bfloat16,
        logging_steps=cfg["logging_steps"], eval_strategy="steps" if valid_ex and not dpo_mode else "no",
        eval_steps=cfg["eval_steps"], save_strategy="steps", save_steps=cfg["save_steps"], save_total_limit=2,
        report_to=[], remove_unused_columns=False, dataloader_num_workers=0, seed=cfg["seed"],
        ddp_find_unused_parameters=False, gradient_checkpointing=False, use_cpu=not cuda,
        # rank 0 answers eval_gen_rows prompts after each eval while rank 1 waits in the next all-reduce
        ddp_timeout=7200)
    deadline = _START + cfg["max_hours"] * 3600          # wall clock since the script started
    stop_reason = {"why": None}

    def remote_stop() -> bool:
        if not cfg.get("stop_url"):
            return False
        try:
            import urllib.request
            with urllib.request.urlopen(f"{cfg['stop_url']}?t={int(time.time())}", timeout=10) as r:
                return stop_requested(r.read().decode("utf-8", "replace"), _START)
        except Exception:                                # 404 = no stop file; network errors never stop a run
            return False

    class StopControl(TrainerCallback):
        """max_hours and the remote stop switch, decided by rank 0 and broadcast so every rank stops at
        the same step (ranks deciding on their own clocks could stop one step apart and hang DDP)."""
        def on_step_end(self, args, state, control, **kw):
            if state.global_step % max(1, cfg["stop_check_steps"]):
                return control
            why = 0
            if main_proc:
                why = (1 if time.time() > deadline else 3 if (out / "STOP_LOCAL").exists()
                       else 2 if remote_stop() else 0)
            if world > 1 and torch.distributed.is_initialized():
                t = torch.tensor([why], device=dev)
                torch.distributed.broadcast(t, src=0)
                why = int(t.item())
            if why:
                stop_reason["why"] = {1: "max_hours", 2: "remote_stop", 3: "low_ram"}[why]
                if main_proc:
                    log(f"{stop_reason['why']} at step {state.global_step}: stopping and saving")
                control.should_training_stop = True
            return control

    class MemHygiene(TrainerCallback):
        """Every mem_gc_steps: collect garbage, drop cached CUDA blocks, return freed heap to the OS."""
        def on_step_end(self, args, state, control, **kw):
            if cfg.get("mem_gc_steps") and state.global_step % cfg["mem_gc_steps"] == 0:
                import gc
                gc.collect()
                if cuda:
                    torch.cuda.empty_cache()
                malloc_trim()
            return control

    gpu_last: list = []

    def gpu_loop():
        while True:
            rows = gpu_sample()
            if rows:
                gpu_last[:] = rows
                with (out / "gpu.jsonl").open("a") as fh:
                    fh.write(json.dumps({"t": round(time.time() - _START, 1), "gpus": rows}) + "\n")
            time.sleep(max(5, cfg["gpu_sample_s"]))

    if main_proc and cuda:
        import threading
        threading.Thread(target=gpu_loop, daemon=True).start()

    def action_accuracy(m) -> dict:
        """Greedy replies for held-out prompts vs the training targets: the number that matters (does
        it pick the right action?), which the loss only hints at."""
        rows = valid_ex[: cfg["eval_gen_rows"]]
        if not rows:
            return {}
        im_end = tok.convert_tokens_to_ids("<|im_end|>")
        exact = verb = 0
        was_training = m.training
        m.eval()
        try:
            with torch.no_grad(), torch.autocast(dev.type, dtype=dtype, enabled=cuda and dtype != torch.float32):
                for e in rows:
                    ids = torch.tensor([e["input_ids"][: e["prompt_len"]]], device=dev)
                    gen = m.generate(input_ids=ids, max_new_tokens=64, do_sample=False, eos_token_id=im_end,
                                     pad_token_id=im_end, use_cache=True)
                    text = tok.decode(gen[0, ids.shape[1]:], skip_special_tokens=False)
                    x, v = action_match(text, e["completion"])
                    exact += x
                    verb += v
        finally:
            m.train(was_training)
        return {"action_acc": round(exact / len(rows), 4), "verb_acc": round(verb / len(rows), 4),
                "acc_rows": len(rows)}

    class Progress(TrainerCallback):
        """One readable line per log step in the kernel log, plus progress.jsonl / progress.json."""
        t_train = None
        last_eval: dict = {}

        def on_train_begin(self, args, state, control, **kw):
            self.t_train = time.time()

        def on_evaluate(self, args, state, control, metrics=None, model=None, **kw):
            if not main_proc:
                return
            self.last_eval = {"eval_loss": (metrics or {}).get("eval_loss")}
            if cfg["eval_gen_rows"] and model is not None:
                try:
                    self.last_eval.update(action_accuracy(model))
                except Exception as ex:                     # a metric must never kill the run
                    log(f"action accuracy skipped: {type(ex).__name__}: {ex}")
            self._emit(state, kind="eval")

        def on_step_end(self, args, state, control, model=None, **kw):
            # objective=dpo: Trainer eval is off; rank 0 scores the held-out pairs itself every eval_steps
            # (rank 1 waits in the next all-reduce, as it does during action_accuracy)
            if not (dpo_mode and main_proc and valid_ex) or state.global_step % max(1, cfg["eval_steps"]):
                return
            try:
                self.last_eval = dpo_eval(model, valid_ex)
                if cfg["eval_gen_rows"]:
                    self.last_eval.update(action_accuracy(model))
            except Exception as ex:                         # a metric must never kill the run
                log(f"dpo eval skipped: {type(ex).__name__}: {ex}")
            self._emit(state, kind="eval")

        def on_log(self, args, state, control, logs=None, **kw):
            if main_proc and logs and "loss" in logs:
                self._emit(state, kind="train", logs=logs)

        def _emit(self, state, kind, logs=None):
            logs = logs or {}
            step, total = state.global_step, max(1, state.max_steps)
            el = time.time() - (self.t_train or _START)
            sps = el / step if step else None
            p = {"kind": kind, "t": round(time.time() - _START, 1), "step": step, "total": total,
                 "pct": round(100 * step / total, 1), "epoch": round(state.epoch or 0, 3),
                 "loss": logs.get("loss"), "lr": logs.get("learning_rate"), "grad_norm": logs.get("grad_norm"),
                 "s_per_step": round(sps, 2) if sps else None,
                 "eta_s": round(sps * (total - step)) if sps else None,
                 "gpu": list(gpu_last), **self.last_eval}
            try:                                     # the supervisor's latest RAM sample (both ranks)
                p["ram"] = json.loads((out / "ram.json").read_text())
            except (OSError, ValueError):
                pass
            if dpo_mode and kind == "train":
                p.update({k: logs.get(k) for k in ("dpo_loss", "sft_loss", "reward_margin", "pref_acc",
                                                   "policy_pref")})
            if kind == "train" and p["loss"] is None:
                return
            log(progress_line(p))
            ntfy(cfg, progress_line(p), title="AWOS eval" if kind == "eval" else "AWOS training",
                 tags="bar_chart" if kind == "eval" else "")
            with (out / "progress.jsonl").open("a") as fh:
                fh.write(json.dumps(p) + "\n")
            (out / "progress.json").write_text(json.dumps(p, indent=1) + "\n")

    trainer = (DPOTrainer if dpo_mode else SFTTrainer)(
        model=model, args=targs, train_dataset=SeqDataset(train_ex),
        eval_dataset=SeqDataset(valid_ex) if valid_ex and not dpo_mode else None,
        data_collator=collate_pair if dpo_mode else collate, callbacks=[StopControl(), MemHygiene(), Progress()])
    t1 = time.time()
    res = trainer.train(resume_from_checkpoint=resume_exact) if resume_exact else trainer.train()
    train_s = time.time() - t1
    if not main_proc:
        return 0
    post = eval_loss(model, valid_ex[: cfg["preflight_rows"]] or train_ex[: cfg["preflight_rows"]])
    model.save_pretrained(out / "adapter")
    (out / "train_log.jsonl").write_text("".join(json.dumps(x) + "\n" for x in trainer.state.log_history))
    metrics = {"env": env, "config": cfg, "data": data_stats, "load_s": round(load_s, 1),
               "train_s": round(train_s, 1), "global_steps": res.global_step,
               "s_per_step": round(train_s / max(1, res.global_step), 2),
               "sequences_per_s": round(res.global_step * cfg["grad_accum"] * world / max(1e-9, train_s), 3),
               "tokens_per_s": round(res.global_step * cfg["grad_accum"] * world / max(1e-9, train_s)
                                     * data_stats["train"]["tokens"] / max(1, data_stats["train"]["n"]), 1),
               "preflight_loss": pre, "final_loss": post, "train_loss": res.training_loss,
               "trainable_params": n_train, "lora_targets": targets, "stopped_by": stop_reason["why"],
               "final_action_accuracy": action_accuracy(model) if cfg["eval_gen_rows"] else None,
               "peak_mem_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2) if cuda else None}
    if dpo_mode:
        metrics["final_dpo"] = dpo_eval(model, valid_ex)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    acc = (metrics.get("final_action_accuracy") or {}).get("action_acc")
    fd = metrics.get("final_dpo") or {}
    ntfy(cfg, f"done: {metrics['global_steps']} steps in {fmt_dur(metrics['train_s'])}; loss {pre:.3f} -> {post:.3f}"
              + (f"; held-out pref {fd['eval_pref_acc'] * 100:.0f}% margin {fd['eval_margin']:+.2f}" if fd else "")
              + (f"; held-out action accuracy {acc * 100:.0f}%" if acc is not None else "")
              + (f"; stopped by {metrics['stopped_by']}" if metrics.get("stopped_by") else ""),
         title="AWOS training finished", tags="white_check_mark")
    log("done", json.dumps({k: metrics[k] for k in ("train_s", "global_steps", "s_per_step", "preflight_loss",
                                                     "final_loss", "final_action_accuracy", "stopped_by",
                                                     "peak_mem_gb")}))
    return 0


def _importable(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ValueError, ImportError):
        return False


def _record_fatal(e: BaseException) -> None:
    """Rank 0's fatal error -> AWOS_ERROR_FILE, so the supervisor's ping says why the run died."""
    path = os.environ.get("AWOS_ERROR_FILE")
    if not path or os.environ.get("RANK", "0") != "0" or (isinstance(e, SystemExit) and not e.code):
        return
    try:
        Path(path).write_text(f"{type(e).__name__}: {e}"[-2000:])
    except OSError:
        pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException as e:
        _record_fatal(e)
        raise

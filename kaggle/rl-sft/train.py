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
    return " | ".join(bits)


def ntfy(cfg: dict, message: str, title: str = "", priority: str = "default", tags: str = "") -> None:
    """Live progress ping to https://ntfy.sh/<ntfy_topic> (phone app or browser; the GitHub watcher reads the
    same topic). Only training numbers are sent. Never raises, never blocks more than a few seconds."""
    topic = cfg.get("ntfy_topic")
    if not topic:
        return
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
    p = subprocess.Popen(cmd, start_new_session=True)
    reason = None
    while p.poll() is None:
        time.sleep(cfg.get("watch_poll_s", 30))
        now = time.time()
        last = prog.stat().st_mtime if prog.exists() else t0
        if now - _START > cfg["max_hours"] * 3600 + 900:          # the in-process stop had 15 min to act
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
    ntfy(cfg, (f"recovered the adapter from step {best[0]}" if ok and best else "NO checkpoint to recover")
         + f" ({reason or f'exit code {rc}'})", title="AWOS training ended early",
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None, help="train_config.json (default: found under /kaggle/input)")
    ap.add_argument("--data", default=None, help="dir with train.jsonl / valid.jsonl (default: /kaggle/input)")
    ap.add_argument("--out", default=None, help="output dir (default /kaggle/working or ./out)")
    ap.add_argument("--base-model", default=None, help="override base_model (e.g. a local tiny checkpoint)")
    ap.add_argument("--max-steps", type=int, default=None)
    a = ap.parse_args(argv)

    cfg_path = Path(a.config) if a.config else find_input("train_config.json", None) / "train_config.json"
    cfg = {**DEFAULTS, **json.loads(cfg_path.read_text())}
    if a.base_model:
        cfg["base_model"], cfg["base_revision"] = a.base_model, None
    if a.max_steps is not None:
        cfg["max_steps"] = a.max_steps
    data = find_input("train.jsonl", a.data)
    out = Path(a.out or ("/kaggle/working" if on_kaggle() else "out"))
    out.mkdir(parents=True, exist_ok=True)

    # torchrun re-launch for multi-GPU (Kaggle T4 x2): one process per GPU, DDP via Trainer.
    if "LOCAL_RANK" not in os.environ and not os.environ.get("AWOS_NO_TORCHRUN"):
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
    n_checked = check_template(tok, train_rows + valid_rows, cfg["template_check_rows"], cfg["seed"])
    train_ex, d1 = encode(tok, train_rows, cfg["max_seq_len"])
    valid_ex, d2 = encode(tok, valid_rows, cfg["max_seq_len"])
    random.Random(cfg["seed"]).shuffle(valid_ex)
    valid_ex = valid_ex[: cfg["eval_rows"]]
    data_stats = {"train": stats(train_ex), "valid": stats(valid_ex), "dropped_too_long": d1 + d2,
                  "template_checked": n_checked}
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
    n_train = sum(p.numel() for p in model.parameters() if p.requires_grad)
    targets = sorted({n.split("model.language_model.layers.")[-1].split(".", 1)[-1].rsplit(".lora_A", 1)[0]
                      for n, _ in model.named_parameters() if "lora_A" in n})
    if main_proc:
        log(f"LoRA: {n_train / 1e6:.2f}M trainable params on {targets}")
    if not any("linear_attn" in t for t in targets):
        raise SystemExit("no GatedDeltaNet projections matched target_regex")

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
        logging_steps=cfg["logging_steps"], eval_strategy="steps" if valid_ex else "no",
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
                why = 1 if time.time() > deadline else 2 if remote_stop() else 0
            if world > 1 and torch.distributed.is_initialized():
                t = torch.tensor([why], device=dev)
                torch.distributed.broadcast(t, src=0)
                why = int(t.item())
            if why:
                stop_reason["why"] = "max_hours" if why == 1 else "remote_stop"
                if main_proc:
                    log(f"{stop_reason['why']} at step {state.global_step}: stopping and saving")
                control.should_training_stop = True
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
            if kind == "train" and p["loss"] is None:
                return
            log(progress_line(p))
            ntfy(cfg, progress_line(p), title="AWOS eval" if kind == "eval" else "AWOS training",
                 tags="bar_chart" if kind == "eval" else "")
            with (out / "progress.jsonl").open("a") as fh:
                fh.write(json.dumps(p) + "\n")
            (out / "progress.json").write_text(json.dumps(p, indent=1) + "\n")

    trainer = SFTTrainer(model=model, args=targs, train_dataset=SeqDataset(train_ex),
                         eval_dataset=SeqDataset(valid_ex) if valid_ex else None, data_collator=collate,
                         callbacks=[StopControl(), Progress()])
    t1 = time.time()
    res = trainer.train()
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
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    acc = (metrics.get("final_action_accuracy") or {}).get("action_acc")
    ntfy(cfg, f"done: {metrics['global_steps']} steps in {fmt_dur(metrics['train_s'])}; loss {pre:.3f} -> {post:.3f}"
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


if __name__ == "__main__":
    sys.exit(main())

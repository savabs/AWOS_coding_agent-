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
    "epochs": 2.0, "max_steps": -1, "grad_accum": 8, "max_seq_len": 4096,
    "gradient_checkpointing": True, "seed": 0, "logging_steps": 10, "eval_steps": 200, "save_steps": 200,
    "eval_rows": 200, "preflight_rows": 16, "template_check_rows": 32,
    "gdn_impl": "torch",          # "torch" (blocks fla) | "auto" (use fla if installed)
    "dtype": "auto",              # auto: bf16 if supported, else fp16 on CUDA, fp32 on CPU | fp16 | bf16 | fp32
    "pip_install": "auto",        # auto: install PINS on Kaggle when versions differ
    "max_hours": 10.5,            # stop + save before Kaggle's 12 h session limit kills the run
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
        out.append({"input_ids": p + c, "prompt_len": len(p)})
    return out, dropped


def stats(ex: list) -> dict:
    if not ex:
        return {}
    xs = sorted(len(e["input_ids"]) for e in ex)
    comp = [len(e["input_ids"]) - e["prompt_len"] for e in ex]
    return {"n": len(xs), "p50": xs[len(xs) // 2], "p99": xs[int(0.99 * (len(xs) - 1))], "max": xs[-1],
            "tokens": sum(xs), "completion_mean": round(sum(comp) / len(comp), 2), "completion_max": max(comp)}


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
            return subprocess.call(cmd)
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
        ddp_find_unused_parameters=False, gradient_checkpointing=False, use_cpu=not cuda)
    deadline = _START + cfg["max_hours"] * 3600          # wall clock since the script started

    class Deadline(TrainerCallback):
        def on_step_end(self, args, state, control, **kw):
            if time.time() > deadline:
                log(f"max_hours={cfg['max_hours']} reached at step {state.global_step}: stopping and saving")
                control.should_training_stop = True
            return control

    trainer = SFTTrainer(model=model, args=targs, train_dataset=SeqDataset(train_ex),
                         eval_dataset=SeqDataset(valid_ex) if valid_ex else None, data_collator=collate,
                         callbacks=[Deadline()])
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
               "trainable_params": n_train, "lora_targets": targets,
               "peak_mem_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2) if cuda else None}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    log("done", json.dumps({k: metrics[k] for k in ("train_s", "global_steps", "s_per_step", "preflight_loss",
                                                     "final_loss", "peak_mem_gb")}))
    return 0


def _importable(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ValueError, ImportError):
        return False


if __name__ == "__main__":
    sys.exit(main())

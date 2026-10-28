"""
merge_lora.py — merge a PEFT LoRA adapter into the ORIGINAL Hugging Face checkpoint, shard by shard, on CPU.

    python -m scaffold.rl.train.merge_lora --base <HF checkpoint dir> --adapter <dir with adapter_model.safetensors>
        --out <dir> [--in-place]

Every tensor is copied bit-for-bit from the base shards except the LoRA targets, which become

    W' = W + (lora_alpha / r) * B @ A         (computed in fp32, cast back to W's dtype, e.g. bf16)

Why not peft's merge_and_unload + save_pretrained: the Kaggle run holds the base in fp16 (T4 has no
bf16), and transformers' Qwen3.5 class does not load the checkpoint's MTP head (mtp.*), so a
save_pretrained copy would be fp16-rounded and missing tensors. Merging into the untouched bf16 shards
gives exactly the official checkpoint + the LoRA delta, which llama.cpp's convert_hf_to_gguf.py
handles the same way as the GGUFs we evaluate. Peak RAM ~ one shard (4B: 5.3 GB). Needs torch +
safetensors (the llama.cpp convert environment has both).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, Tuple


def peft_to_hf_key(k: str) -> Tuple[str, str]:
    """'base_model.model.model.language_model.layers.0.mlp.up_proj.lora_A.weight'
       -> ('model.language_model.layers.0.mlp.up_proj.weight', 'A')"""
    if k.startswith("base_model.model."):
        k = k[len("base_model.model."):]
    for ab in ("A", "B"):
        tag = f".lora_{ab}.weight"
        if k.endswith(tag):
            base = k[: -len(tag)]
            if base.startswith("model.layers."):                  # trained as Qwen3_5ForCausalLM (text only)
                base = "model.language_model." + base[len("model."):]
            return base + ".weight", ab
    raise ValueError(f"not a LoRA tensor: {k}")


def load_adapter(adapter: Path) -> Tuple[Dict[str, dict], float, dict]:
    from safetensors.torch import load_file  # noqa: PLC0415
    cfg = json.loads((adapter / "adapter_config.json").read_text())
    if cfg.get("use_dora") or cfg.get("use_rslora"):
        raise SystemExit("DoRA / rsLoRA adapters are not supported by this merger")
    scale = float(cfg["lora_alpha"]) / float(cfg["r"])
    mods: Dict[str, dict] = {}
    for k, v in load_file(str(adapter / "adapter_model.safetensors")).items():
        hk, ab = peft_to_hf_key(k)
        mods.setdefault(hk, {})[ab] = v
    bad = [k for k, v in mods.items() if set(v) != {"A", "B"}]
    if bad:
        raise SystemExit(f"incomplete LoRA pairs: {bad[:3]}")
    return mods, scale, cfg


def merge(base: Path, adapter: Path, out: Path) -> dict:
    import torch  # noqa: PLC0415
    from safetensors import safe_open  # noqa: PLC0415
    from safetensors.torch import save_file  # noqa: PLC0415
    base, out = base.expanduser().resolve(), out.expanduser()
    in_place = out.resolve() == base
    mods, scale, cfg = load_adapter(adapter.expanduser())
    idx = base / "model.safetensors.index.json"
    shards = sorted(set(json.loads(idx.read_text())["weight_map"].values())) if idx.exists() else ["model.safetensors"]
    out.mkdir(parents=True, exist_ok=True)
    done, rel, t0 = set(), [], time.time()
    for shard in shards:
        tensors, meta = {}, None
        with safe_open(str(base / shard), "pt") as f:
            meta = f.metadata()
            for k in f.keys():
                t = f.get_tensor(k)
                if k in mods:
                    A, B = mods[k]["A"].float(), mods[k]["B"].float()
                    delta = scale * (B @ A)
                    if delta.shape != t.shape:
                        raise SystemExit(f"{k}: delta {tuple(delta.shape)} != weight {tuple(t.shape)}")
                    rel.append((delta.norm() / (t.float().norm() + 1e-12)).item())
                    t = (t.float() + delta).to(t.dtype)
                    done.add(k)
                tensors[k] = t.contiguous()
        tmp = out / (shard + ".tmp")
        save_file(tensors, str(tmp), metadata=meta or {"format": "pt"})
        tmp.replace(out / shard)
        del tensors
    if done != set(mods):
        raise SystemExit(f"applied {len(done)}/{len(mods)} LoRA deltas; not in the base: {sorted(set(mods) - done)[:3]}")
    if not in_place:
        for f in base.iterdir():
            if f.is_file() and not f.name.endswith(".safetensors"):
                shutil.copy2(f, out / f.name)            # follows HF-cache symlinks
    info = {"base": str(base), "adapter": str(adapter), "scale": scale, "r": cfg.get("r"),
            "tensors_modified": len(done), "rel_delta_norm_mean": round(sum(rel) / max(1, len(rel)), 6),
            "rel_delta_norm_max": round(max(rel or [0.0]), 6), "seconds": round(time.time() - t0, 1)}
    (out / "awos_merge.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.train.merge_lora", description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, type=Path)
    ap.add_argument("--adapter", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="may equal --base to merge in place (saves disk)")
    a = ap.parse_args(argv)
    print(json.dumps(merge(a.base, a.adapter, a.out), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
tiny_qwen35.py — a random-weight, Qwen3.5-architecture checkpoint for CPU smoke tests of the training
and export path (Kaggle train.py on CPU, LoRA merge, convert_hf_to_gguf, llama-quantize, llama-server).

    python -m scaffold.rl.train.tiny_qwen35 --tokenizer <dir with Qwen3.5 tokenizer files> --out /tmp/tiny-qwen35

Same class (Qwen3_5ForConditionalGeneration), same key names, same GatedDeltaNet + gated full
attention layout (3 linear-attention layers then 1 full-attention layer), same head dims (256 attn,
128 linear) and the real 248k tokenizer, so every name/shape-dependent code path is exercised; only
the widths and depth are tiny (~70M params, mostly the embedding). Weights are random: replies are
junk, but grammar-constrained decoding still yields a parseable action.
Needs torch + transformers>=5 (the Kaggle / CI training env).
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt", "chat_template.jinja")


def make_tiny(out: Path, tokenizer_dir: Path, seed: int = 0, dtype: str = "bfloat16") -> Path:
    import torch  # noqa: PLC0415
    from transformers import Qwen3_5Config, Qwen3_5ForConditionalGeneration  # noqa: PLC0415

    torch.manual_seed(seed)
    text = dict(hidden_size=256, intermediate_size=512, num_hidden_layers=4, full_attention_interval=4,
                layer_types=["linear_attention"] * 3 + ["full_attention"], num_attention_heads=2,
                num_key_value_heads=1, head_dim=256, linear_num_key_heads=2, linear_num_value_heads=2,
                linear_key_head_dim=128, linear_value_head_dim=128, linear_conv_kernel_dim=4,
                vocab_size=248320, max_position_embeddings=8192, rms_norm_eps=1e-6, tie_word_embeddings=True,
                attn_output_gate=True, mtp_num_hidden_layers=0, eos_token_id=248044,
                rope_parameters={"rope_type": "default", "rope_theta": 10000000, "partial_rotary_factor": 0.25,
                                 "mrope_interleaved": True, "mrope_section": [11, 11, 10]})
    vision = dict(depth=1, hidden_size=64, intermediate_size=128, num_heads=2, out_hidden_size=256,
                  patch_size=16, spatial_merge_size=2, temporal_patch_size=2, in_channels=3,
                  num_position_embeddings=2304, deepstack_visual_indexes=[])
    cfg = Qwen3_5Config(text_config=text, vision_config=vision, tie_word_embeddings=True,
                        image_token_id=248056, video_token_id=248057, vision_start_token_id=248053,
                        vision_end_token_id=248054)
    model = Qwen3_5ForConditionalGeneration(cfg).to(getattr(torch, dtype))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out)
    for f in TOKENIZER_FILES:
        src = Path(tokenizer_dir) / f
        if src.exists():
            shutil.copy(src, out / f)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.train.tiny_qwen35")
    ap.add_argument("--tokenizer", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    print(make_tiny(a.out, a.tokenizer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Training/export math on a tiny random Qwen3.5 (CPU, seconds). Needs torch + transformers>=5 + peft
+ safetensors; skipped otherwise (the harness venv has none of them; CI's rl-export smoke job does).

  * kaggle/rl-sft/train.py completion_loss (logits only for the completion) == full-logit masked CE
  * LoRA targets include the GatedDeltaNet projections and only language-model layers
  * merge_lora (into the original shards) == peft merge_and_unload, and untouched tensors are bit-equal
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")
peft = pytest.importorskip("peft")
pytest.importorskip("safetensors")
if not hasattr(transformers, "Qwen3_5ForConditionalGeneration"):
    pytest.skip("transformers without Qwen3.5", allow_module_level=True)

REPO = Path(__file__).resolve().parents[2]
sys.modules["fla"] = None        # same as train.py gdn_impl=torch


def load_train():
    spec = importlib.util.spec_from_file_location("kaggle_train", REPO / "kaggle/rl-sft/train.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    from scaffold.rl.train.tiny_qwen35 import make_tiny
    return make_tiny(tmp_path_factory.mktemp("tiny"), Path("/nonexistent"), dtype="float32")


@pytest.mark.timeout(120)
def test_completion_loss_equals_masked_full_loss(tiny):
    T = load_train()
    m = transformers.Qwen3_5ForConditionalGeneration.from_pretrained(tiny, dtype=torch.float32).eval()
    g = torch.Generator().manual_seed(0)
    ids = torch.randint(0, 248000, (1, 150), generator=g)
    p = 141                                                  # 9 completion tokens
    with torch.no_grad():
        ours = T.completion_loss(m, ids, p)
        logits = m(input_ids=ids, use_cache=False).logits[0, :-1]
        ref = torch.nn.functional.cross_entropy(logits[p - 1:], ids[0, p:])
    assert torch.allclose(ours, ref, atol=1e-5), (ours, ref)


@pytest.mark.timeout(120)
def test_lora_targets_and_merge_match_peft(tiny, tmp_path):
    from peft import LoraConfig, get_peft_model
    from safetensors.torch import load_file
    from scaffold.rl.train.merge_lora import merge
    T = load_train()
    base = transformers.Qwen3_5ForConditionalGeneration.from_pretrained(tiny, dtype=torch.float32)
    cfg = LoraConfig(r=4, lora_alpha=8, target_modules=T.DEFAULTS["target_regex"], init_lora_weights=False)
    pm = get_peft_model(base, cfg)
    names = [n for n, _ in pm.named_parameters() if "lora_A" in n]
    assert any(".linear_attn.in_proj_qkv." in n for n in names) and any(".self_attn.q_proj." in n for n in names)
    assert not any("visual" in n for n in names)
    pm.save_pretrained(tmp_path / "adapter")
    merged_ref = pm.merge_and_unload().state_dict()
    info = merge(Path(tiny), tmp_path / "adapter", tmp_path / "merged")
    assert info["tensors_modified"] == len(names) == 25      # 3 GDN layers x 6 + 1 attention layer x 7
    ours = load_file(str(tmp_path / "merged" / "model.safetensors"))
    orig = load_file(str(Path(tiny) / "model.safetensors"))
    changed = 0
    for k, v in ours.items():
        ref = merged_ref.get(k)
        if ref is None:
            continue                                          # tied lm_head etc.
        assert torch.allclose(v, ref, atol=1e-5), k
        changed += int(not torch.equal(v, orig[k]))
    assert changed == 25
    assert json.loads((tmp_path / "merged" / "awos_merge.json").read_text())["scale"] == 2.0

"""
scaffold.rl.train — Stage-A training (rejection-sampling SFT) for the computer-use RL stack.
Cloud first: data + export on GitHub Actions (CPU), LoRA training on Kaggle (T4 x2).

    dataset.py       trajectories.jsonl -> SFT JSONL (stdlib only; GitHub Actions or any python3)
    chat.py          the exact Qwen3.5 prompt text LocalGBNFModel produces at inference
    kaggle_driver.py stage the Kaggle dataset + kernel, push / wait / fetch via the kaggle CLI
    merge_lora.py    merge the PEFT adapter into the original HF bf16 shards (CPU)
    serve_check.py   llama-server smoke: load the GGUF, template/token equality, one grammar step
    tiny_qwen35.py   random tiny Qwen3.5 checkpoint for CPU smoke tests of the whole path
    ../../../kaggle/rl-sft/train.py   the Kaggle kernel (transformers + PEFT LoRA SFT)

Pipeline and commands: docs/research/rl_cua_2026-10-22/TRAINING.md.
"""

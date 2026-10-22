"""Kaggle plumbing without Kaggle: staging validates rows and writes the metadata; push/fetch dry-runs
print the CLI calls and need no credentials. Stdlib only.

    PYTHONPATH=. scaffold/rl/harness/.venv/bin/python -m pytest tests/rl/test_train_kaggle.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scaffold.rl.train import chat, kaggle_driver as kd


def row(split="train", rendered=True):
    r = {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                      {"role": "assistant", "content": "click(1)"}],
         "meta": {"split": split, "task_id": "t/1"}}
    if rendered:
        r["prompt"], r["completion"] = chat.render_prompt("s", "u"), chat.render_completion("click(1)")
    return r


def data_dir(tmp: Path, rows, valid=()) -> Path:
    d = tmp / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "train.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "valid.jsonl").write_text("".join(json.dumps(r) + "\n" for r in valid))
    return d


def test_stage_writes_dataset_and_kernel(tmp_path):
    d = data_dir(tmp_path, [row()] * 3, [row()])
    info = kd.stage(d, tmp_path / "st", "4B", "alice", {"epochs": 1, "lora_r": 32})
    ds, kn = tmp_path / "st" / "dataset", tmp_path / "st" / "kernel"
    assert info["rows"] == {"train.jsonl": 3, "valid.jsonl": 1}
    cfg = json.loads((ds / "train_config.json").read_text())
    assert cfg["base_model"] == "Qwen/Qwen3.5-4B" and len(cfg["base_revision"]) == 40
    assert cfg["epochs"] == 1 and cfg["lora_r"] == 32
    assert json.loads((ds / "dataset-metadata.json").read_text())["id"] == "alice/awos-rl-sft-data"
    meta = json.loads((kn / "kernel-metadata.json").read_text())
    assert meta["id"] == "alice/awos-rl-sft" and meta["dataset_sources"] == ["alice/awos-rl-sft-data"]
    assert meta["enable_gpu"] is True and meta["enable_internet"] is True and meta["is_private"] is True
    assert meta["machine_shape"] == "NvidiaTeslaT4" and meta["code_file"] == "train.py"
    assert (kn / "train.py").read_text() == kd.KERNEL_SRC.read_text()


@pytest.mark.parametrize("split", ["ood", "holdout", None])
def test_stage_refuses_eval_rows(tmp_path, split):
    d = data_dir(tmp_path, [row(), row(split=split)])
    with pytest.raises(SystemExit, match="not trainable"):
        kd.stage(d, tmp_path / "st", "2B", "alice", {})


def test_stage_refuses_unrendered_rows(tmp_path):
    d = data_dir(tmp_path, [row(rendered=False)])
    with pytest.raises(SystemExit, match="rendered"):
        kd.stage(d, tmp_path / "st", "2B", "alice", {})


def test_push_dry_run_needs_no_credentials(tmp_path, monkeypatch, capsys):
    for k in ("KAGGLE_USERNAME", "KAGGLE_KEY", "KAGGLE_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    kd.stage(data_dir(tmp_path, [row()]), tmp_path / "st", "2B", "alice", {})
    out = kd.push(tmp_path / "st", dry=True)
    printed = capsys.readouterr().out
    assert "kaggle datasets create -p" in printed and "kaggle kernels push -p" in printed
    assert out == {"dataset": "alice/awos-rl-sft-data", "kernel": "alice/awos-rl-sft", "dry_run": True}
    assert kd.wait("alice/awos-rl-sft", 1, dry=True) == "complete"
    kd.fetch("alice/awos-rl-sft", tmp_path / "o", dry=True)
    assert "kaggle kernels output alice/awos-rl-sft" in capsys.readouterr().out


def test_real_push_requires_credentials(tmp_path, monkeypatch):
    for k in ("KAGGLE_USERNAME", "KAGGLE_KEY", "KAGGLE_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit, match="credentials"):
        kd.run(["kaggle", "datasets", "status", "x/y"], dry=False)


def test_kernel_template_matches_train_defaults():
    import ast
    src = kd.KERNEL_SRC.read_text()
    tree = ast.parse(src)
    defaults = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "DEFAULTS")
    d = ast.literal_eval(defaults.value)
    assert d["base_model"] == kd.SIZES["4B"]["base_model"] and d["base_revision"] == kd.SIZES["4B"]["base_revision"]
    assert "linear_attn" in d["target_regex"] and d["gdn_impl"] == "torch"

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


def test_access_token_in_kaggle_key_is_passed_as_api_token(monkeypatch):
    from scaffold.rl.train import kaggle_driver as kd
    monkeypatch.setenv("KAGGLE_USERNAME", "someone")
    monkeypatch.delenv("KAGGLE_API_TOKEN", raising=False)
    monkeypatch.setenv("KAGGLE_KEY", "KGAT_" + "x" * 32)          # newer access-token format
    env = kd._kaggle_env()
    assert env["KAGGLE_API_TOKEN"].startswith("KGAT_") and "KAGGLE_KEY" not in env
    monkeypatch.setenv("KAGGLE_KEY", "0123456789abcdef" * 2)      # legacy 32-hex key stays
    env = kd._kaggle_env()
    assert env["KAGGLE_KEY"] == "0123456789abcdef" * 2 and "KAGGLE_API_TOKEN" not in env


def test_wait_files_waits_until_the_new_version_is_listed(tmp_path, monkeypatch):
    """"ready" can come while a big version still processes: wait until a staged file (or its unpacked folder)
    is listed. Not every file: the CLI's listing is one page, so with hundreds of files most never appear."""
    import subprocess
    (tmp_path / "web_eval_src.tar.gz").write_bytes(b"x" * 10)
    (tmp_path / "model.gguf").write_bytes(b"y" * 20)
    (tmp_path / "dataset-metadata.json").write_text("{}")
    listings = iter([
        "name,size,creationDate\n",                                                  # nothing processed yet
        "name,size,creationDate\nother.json,3,2026-10-25\n",                         # an older version's file
        "name,size,creationDate\nmodel.gguf,20,2026-10-25\n",                        # a staged file: go
    ])
    calls = []

    def fake_run(cmd, dry, check=True):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, next(listings), "")
    monkeypatch.setattr(kd, "run", fake_run)
    monkeypatch.setattr(kd.time, "sleep", lambda s: None)
    kd.wait_files("me/ds", tmp_path, wait_s=60)
    assert len(calls) == 3 and calls[0][:3] == ["kaggle", "datasets", "files"]

    monkeypatch.setattr(kd, "run", lambda cmd, dry, check=True: subprocess.CompletedProcess(cmd, 0, "name,size\n", ""))
    t = iter(range(0, 10_000, 100))
    monkeypatch.setattr(kd.time, "time", lambda: next(t))
    with pytest.raises(SystemExit, match="no staged file listed"):
        kd.wait_files("me/ds", tmp_path, wait_s=150)


def test_wait_files_counts_an_unpacked_archive(tmp_path, monkeypatch):
    """Kaggle lists web_eval_src.tar.gz as web_eval_src/<members>: that is present, not missing."""
    import subprocess
    (tmp_path / "web_eval_src.tar.gz").write_bytes(b"x" * 10)
    (tmp_path / "model.gguf").write_bytes(b"y" * 20)
    out = ("name,size,creationDate\nweb_eval_src/scaffold/__init__.py,0,2026-10-25\n"
           "web_eval_src/scaffold/rl/web/runner.py,999,2026-10-25\nmodel.gguf,20,2026-10-25\n")
    monkeypatch.setattr(kd, "run", lambda cmd, dry, check=True: subprocess.CompletedProcess(cmd, 0, out, ""))
    monkeypatch.setattr(kd.time, "sleep", lambda s: None)
    kd.wait_files("me/ds", tmp_path, wait_s=60)                  # returns at once
    assert kd._stem("a.tar.gz") == "a" and kd._stem("b.zip") == "b" and kd._stem("m.gguf") == "m.gguf"


def test_dataset_files_accepts_human_sizes(monkeypatch):
    import subprocess
    out = 'name,size,creationDate\n"model.gguf",4GB,2026-10-25\nweb_eval_src.tar.gz,1234,2026-10-25\n'
    monkeypatch.setattr(kd, "run", lambda cmd, dry, check=True: subprocess.CompletedProcess(cmd, 0, out, ""))
    assert kd.dataset_files("me/ds") == {"model.gguf": None, "web_eval_src.tar.gz": 1234}

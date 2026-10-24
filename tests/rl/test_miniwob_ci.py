"""Tests for the rl-eval CI plumbing: matrix builder, baseline merger, workflow YAML."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "rl" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ci_matrix = _load("ci_matrix")
merge = _load("merge_baselines")


def test_env_parses_and_pins_models():
    env = ci_matrix.parse_env(ROOT / "ci" / "rl_eval.env")
    specs = ci_matrix.model_specs(env)
    # base models + our trained GGUFs (gh-release:, split in shards: <name>-Q4_K_M-00001-of-0000N.gguf)
    assert {"qwen3.5-2b", "qwen3.5-4b", "qwen3.5-9b", "mimo-v2.6-9b", "a-final"} <= set(specs)
    assert specs["qwen3.5-9b"]["bytes"] == 5680522464          # == the laptop's file
    assert all(re.search(r"Q4_K_M(-00001-of-\d{5})?\.gguf$", s["file"]) for s in specs.values())
    assert env["LLAMA_TAG"] == "b11146"
    text = (ROOT / "ci" / "rl_eval.env").read_text()
    assert not re.search(r"hf_[A-Za-z0-9]{20,}|(?i:token|api_key|secret)\s*=", text)


def test_matrix_defaults_and_overrides():
    env = ci_matrix.parse_env(ROOT / "ci" / "rl_eval.env")
    matrix, models = ci_matrix.build(env)
    inc = matrix["include"]
    specs = ci_matrix.model_specs(env)
    shards = sum(specs[m]["shards"] for m in env["MODELS"].split())     # default MODELS only
    assert len(inc) == shards * 2                               # miniwob + factory
    assert {(r["source"], r["split"]) for r in inc} == {("miniwob", "holdout"), ("factory", "ood")}
    assert len(models["include"]) == 4
    m2, _ = ci_matrix.build(env, models="qwen3.5-2b", sources="miniwob", split="train", n="20")
    assert [(r["model"], r["split"], r["shard"], r["n"]) for r in m2["include"]] == \
        [("qwen3.5-2b", "train", 1, "20"), ("qwen3.5-2b", "train", 2, "20")]
    with pytest.raises(SystemExit):
        ci_matrix.build(env, models="nope")


def _write_run(root: Path, name: str, records):
    d = root / f"rl-{name}" / name
    d.mkdir(parents=True)
    model, source, split = name.split("__")[:3]
    (d / "run_meta.json").write_text(json.dumps({"model": model, "source": source, "split": split}))
    (d / "episodes.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_merge_baselines(tmp_path: Path):
    def ep(tid, success, unsafe=(), outcome="verified", steps=3, secs=6.0, error=""):
        return {"task": {"id": tid, "source": "miniwob", "split": "holdout"},
                "trajectory": {"task_id": tid, "model": "x", "steps": [{}] * steps, "outcome": outcome,
                               "seconds": secs, "error": error},
                "score": {"success": success, "unsafe": list(unsafe)}}
    _write_run(tmp_path, "qwen3.5-2b__miniwob__holdout__shard1of2", [
        ep("a", True), ep("b", False, outcome="needs_user", steps=1, secs=2.0),
        ep("c", True, unsafe=["trap_clicked"]), ep("d", False, error="browser crashed")])
    _write_run(tmp_path, "qwen3.5-2b__miniwob__holdout__shard2of2", [
        ep("a", False),                          # duplicate task id: first wins
        {"task_id": "e", "success": True, "unsafe": [], "outcome": "verified", "steps": 5, "seconds": 5.0,
         "split": "holdout", "source": "miniwob"}])
    eps = merge.collect(tmp_path)
    rows = merge.aggregate(eps)
    assert len(rows) == 1
    r = rows[0]
    assert r["model"] == "qwen3.5-2b" and r["n"] == 4 and r["infra_errors"] == 1
    assert r["successes"] == 2 and r["pass1"] == 0.5             # a, e (c is unsafe)
    assert r["unsafe"] == {"trap_clicked": 1}
    assert r["needs_user_rate"] == 0.25
    assert r["steps_per_success"] == 4.0                          # (3 + 5) / 2
    assert r["s_per_step"] == pytest.approx((6 + 2 + 6 + 5) / (3 + 1 + 3 + 5))
    md = merge.markdown(rows, len(eps))
    assert "| qwen3.5-2b | miniwob | holdout | 4 | 50.0% (2/4) | trap_clicked:1 |" in md


def test_workflow_yaml_valid():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "rl-eval.yml").read_text())
    on = wf.get("on") or wf.get(True)
    assert on["push"]["branches"] == ["bench-cloud/rl-eval-**"]
    assert set(on["workflow_dispatch"]["inputs"]) == {"models", "sources", "split", "n"}
    run = wf["jobs"]["run"]
    assert run["runs-on"] == "ubuntu-latest"
    assert run["strategy"]["max-parallel"] == 16
    assert "secrets." not in (ROOT / ".github" / "workflows" / "rl-eval.yml").read_text()

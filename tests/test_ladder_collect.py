"""scripts/ladder_collect.py: stage one CI shard's small files, merge shards for `combine`."""
import importlib.util
import json
from pathlib import Path

import pytest

_S = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _S / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lc = _load("ladder_collect")
ab = _load("ablation_awos_vs_aider")


def _series(root: Path, n: int) -> Path:
    for i in range(n):
        (root / f"fresh_{i}" / "jobs" / "01_x").mkdir(parents=True)
    (root / "READY").write_text("ok")
    return root


def _fake_shard_out(out: Path, issue: str, spent=0.1, ts="20261015T000000", arms=("off", "mini")):
    """What one driver run leaves for an issue: results, run dir with arm dirs."""
    d = out / "r1" / issue
    run = d / "job_series" / ts
    for arm in arms:
        (run / arm / "project" / "pkg").mkdir(parents=True)
        (run / arm / "project" / "pkg" / "big.py").write_text("x" * 100)
        (run / arm / "project" / "run.log").write_text("a project file named run.log")
        (run / arm / "state" / ".awos").mkdir(parents=True)
        (run / arm / "state" / ".awos" / "llm_calls.jsonl").write_text('{"cost_usd": 0.01}\n')
        (run / arm / "state" / ".awos" / "budget.json").write_text("[]")
        (run / arm / "state" / "report.json").write_text("{}")
        (run / arm / "state" / "mini.traj.json").write_text("{}")
        (run / arm / "run.log").write_text(f"{arm} log")
    (run / "report.md").write_text("# report")
    (run / "health.json").write_text("{}")
    (d / "driver.log").write_text("driver")
    (d / f"job_series_{ts}.json").write_text(json.dumps(
        {"results": [{"arm": a, "job": 1, "solved": a == "off", "spent_usd": spent} for a in arms]}))


def test_keep_rules():
    assert lc.keep(Path("r1/a/job_series_1.json"))
    assert lc.keep(Path("r1/a/job_series/t/off/state/.awos/llm_calls.jsonl"))
    assert lc.keep(Path("r1/a/job_series/t/off/state/report.json"))
    assert lc.keep(Path("driver_stdout.log")) and lc.keep(Path("shard.json"))
    assert not lc.keep(Path("r1/a/job_series/t/off/project/run.log"))
    assert not lc.keep(Path("r1/a/job_series/t/off/project/report.md"))
    assert not lc.keep(Path("r1/a/job_series/t/off/state/.awos/budget.json"))
    assert not lc.keep(Path("r1/a/job_series/t/off/project/tests/report.json"))


def _stage_shard(tmp_path, series, k, n, issues_done, spent=0.1):
    out = tmp_path / f"run{k}"
    for issue in issues_done:
        _fake_shard_out(out, issue, spent)
    out.mkdir(parents=True, exist_ok=True)
    (out / "driver_stdout.log").write_text(f"shard {k}")
    dest = tmp_path / "dl" / f"ladder-shard-{k}"
    m = lc.stage(out, dest, series, f"{k}/{n}", None, "off,mini")
    return dest, m


def test_stage_copies_small_files_only(tmp_path):
    series = _series(tmp_path / "series", 4)
    dest, m = _stage_shard(tmp_path, series, 1, 2, ["fresh_1"])
    files = {str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()}
    assert not any("/project/" in f for f in files)
    assert not any(f.endswith(("budget.json", "mini.traj.json")) for f in files)
    assert "r1/fresh_1/job_series_20261015T000000.json" in files
    assert "r1/fresh_1/driver.log" in files and "driver_stdout.log" in files
    assert "r1/fresh_1/job_series/20261015T000000/off/state/.awos/llm_calls.jsonl" in files
    assert m["assigned"] == ["fresh_1", "fresh_3"] and m["done"] == ["fresh_1"]
    assert round(m["spent_usd"], 4) == 0.2


def test_merge_then_combine(tmp_path, capsys):
    series = _series(tmp_path / "series", 4)
    _stage_shard(tmp_path, series, 0, 2, ["fresh_0", "fresh_2"])
    _stage_shard(tmp_path, series, 1, 2, ["fresh_1"])          # fresh_3 never ran
    out = tmp_path / "merged"
    summary = lc.merge(out, [tmp_path / "dl"], "off,mini")
    assert summary["done"] == ["fresh_0", "fresh_1", "fresh_2"]
    assert summary["not_done"] == ["fresh_3"]
    assert round(summary["spent_usd"], 4) == 0.6
    assert (out / "_shards" / "ladder-shard-0" / "driver_stdout.log").read_text() == "shard 0"
    assert "NOT DONE (1): fresh_3" in capsys.readouterr().out
    # `combine` reads the merged dir like a single-machine out dir
    assert ab.main(["combine", "--out", str(out), "--arms", "off,mini"]) == 0
    data = json.loads((out / "combined.json").read_text())
    assert data["issues"] == ["fresh_0", "fresh_1", "fresh_2"]
    assert {(r["issue"], r["job"], r["arm"]) for r in data["results"]} == {
        (f"fresh_{i}", i + 1, a) for i in range(3) for a in ("off", "mini")}


def test_merge_is_idempotent_and_flags_conflicts(tmp_path):
    series = _series(tmp_path / "series", 2)
    dest0, _ = _stage_shard(tmp_path, series, 0, 2, ["fresh_0"])
    out = tmp_path / "merged"
    lc.merge(out, [dest0])
    again = lc.merge(out, [dest0])
    assert again["copied"] == 0 and again["identical_skipped"] > 0
    # a different results file at the same path in another shard is an error
    other = tmp_path / "other" / "ladder-shard-9"
    (other / "r1" / "fresh_0").mkdir(parents=True)
    (other / "r1" / "fresh_0" / "job_series_20261015T000000.json").write_text('{"results": []}')
    with pytest.raises(lc.MergeConflict):
        lc.merge(out, [other])
    assert lc.main(["merge", "--out", str(out), str(other)]) == 2


def test_merge_accepts_single_artifact_download_layout(tmp_path):
    # `gh run download -n ladder-shard-0 -D dir` extracts straight into dir
    series = _series(tmp_path / "series", 2)
    dest0, _ = _stage_shard(tmp_path, series, 0, 2, ["fresh_0"])
    assert lc.find_shards([dest0]) == [dest0]
    assert lc.find_shards([dest0.parent]) == [dest0]
    with pytest.raises(lc.MergeConflict):
        lc.merge(tmp_path / "m", [tmp_path / "series"])


def test_ci_config_defaults_and_smoke(tmp_path):
    repo_cfg = lc.read_config(Path(__file__).resolve().parent.parent / "ci" / "ladder.env")
    assert repo_cfg["shards"] == "[0, 1, 2, 3, 4, 5, 6, 7]" and repo_cfg["cap_shard"] == "0.6250"
    assert repo_cfg["arms"] == "off,mini" and repo_cfg["limit"] == ""
    smoke = tmp_path / "ladder.env"
    smoke.write_text("ARMS=off,mini\nN_SHARDS=8\nSHARDS=0  # smoke\nCAP=5\nLIMIT=1\n")
    cfg = lc.read_config(smoke)
    assert cfg["shards"] == "[0]" and cfg["limit"] == "1"
    for bad in ("SHARDS=8\nCAP=5\n", "CAP=0\n", "CAP=5\nLIMIT=0\n", "CAP=5\nFOO=1\n",
                "CAP=5\nARMS=off\n"):
        smoke.write_text(bad)
        with pytest.raises(ValueError):
            lc.read_config(smoke)
    assert lc.main(["ci-config", str(smoke)]) == 2

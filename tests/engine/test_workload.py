"""EngineBench workload: capture from trajectories, file format, digest, loader checks, budget subsets."""
from __future__ import annotations

import gzip
import json

import pytest

import bench
import capture


def test_agent_episodes_keep_step_order_and_expected_only_on_success(exam_dir):
    eps = {e["meta"]["task_id"]: e for e in capture.agent_episodes(exam_dir, "factory-ood", None)}
    assert set(eps) == {"factory/fam/a/000", "factory/fam/b/000", "factory/fam/c/000"}
    a = eps["factory/fam/a/000"]
    assert [c["step"] for c in a["calls"]] == [1, 2, 3]
    assert [c["expected"] for c in a["calls"]] == ["click(1)", 'fill(2, "Ann")', 'done("ok")']
    assert a["calls"][0]["messages"][0] == {"role": "system", "content": "SYS prompt"}
    assert a["calls"][0]["max_tokens"] == capture.AGENT_MAX_TOKENS and a["calls"][0]["grammar"]
    b = eps["factory/fam/b/000"]                     # failed: no expected answer, the CI reply kept as reference
    assert [c["expected"] for c in b["calls"]] == [None, None]
    assert b["calls"][0]["reference"] == "click(2)"
    assert eps["factory/fam/c/000"]["calls"][0]["expected"] is None      # unsafe "success" is not a known answer


def test_episode_order_is_fixed_and_limit_is_a_prefix(exam_dir):
    full = [e["id"] for e in capture.agent_episodes(exam_dir, "factory-ood", None)]
    assert full == [e["id"] for e in capture.agent_episodes(exam_dir, "factory-ood", None)]
    assert [e["id"] for e in capture.agent_episodes(exam_dir, "factory-ood", 2)] == full[:2]


def test_verify_episodes_from_exam_rows(exam_dir):
    eps = capture.verify_episodes(exam_dir, None)
    labels = sorted(e["calls"][0]["expected"] for e in eps)
    assert labels == ["NOT_DONE", "UNSAFE", "VERIFIED"]
    c = eps[0]["calls"][0]
    assert c["grammar"].startswith("root ::=") and "VERIFIED" in c["grammar"] and c["max_tokens"] == 64
    assert "Goal:" in c["messages"][1]["content"]


def test_verify_refuses_train_split(tmp_path):
    from conftest import rec
    from scaffold.rl.train.dataset import EvalSplitError
    d = tmp_path / "x"
    d.mkdir()
    (d / "trajectories.jsonl").write_text(json.dumps(rec("factory/fam/t/000", ["click(1)"], split="train")) + "\n")
    with pytest.raises(EvalSplitError):
        capture.verify_episodes(tmp_path, None)


def test_write_load_roundtrip_and_digest(tmp_path, exam_dir):
    eps = capture.agent_episodes(exam_dir, "factory-ood", None)
    p = tmp_path / "w.jsonl.gz"
    hd = capture.write(p, eps, {"note": "t"})
    assert hd["calls"] == 6 and hd["episodes"] == 3 and hd["by_source"]["agent:factory-ood"]["with_expected"] == 3
    b1 = p.read_bytes()
    capture.write(p, eps, {"note": "t"})
    assert p.read_bytes() == b1                      # byte-identical rebuild (gzip mtime=0)
    headers, back = capture.load([p])
    assert headers[0]["digest"] == hd["digest"] == capture.digest(back)
    assert [e["id"] for e in back] == [e["id"] for e in eps]


def test_load_rejects_tampered_or_wrong_version(tmp_path, exam_dir):
    eps = capture.agent_episodes(exam_dir, "factory-ood", None)
    p = tmp_path / "w.jsonl.gz"
    capture.write(p, eps, {})
    lines = gzip.open(p, "rt").read().splitlines()
    ep = json.loads(lines[1])
    ep["calls"][0]["expected"] = "click(9)"
    bad = tmp_path / "bad.jsonl"
    bad.write_text("\n".join([lines[0], json.dumps(ep)] + lines[2:]) + "\n")
    with pytest.raises(ValueError, match="digest"):
        capture.load([bad])
    hd = json.loads(lines[0])
    hd["header"]["version"] = "enginebench-v0"
    old = tmp_path / "old.jsonl"
    old.write_text("\n".join([json.dumps(hd)] + lines[1:]) + "\n")
    with pytest.raises(ValueError, match="version"):
        capture.load([old])


def test_select_whole_episodes_per_kind_budget():
    eps = [{"id": f"a{i}", "kind": "agent", "source": "s", "calls": [{}] * 3} for i in range(4)] + \
          [{"id": f"r{i}", "kind": "repair", "source": "s", "calls": [{}]} for i in range(5)]
    sel = bench.select(eps, ["agent", "repair"], {"agent": 5, "repair": 2})
    assert [e["id"] for e in sel] == ["a0", "a1", "r0", "r1"]        # 6 agent calls: episodes are never cut
    assert [e["id"] for e in bench.select(eps, ["repair"], {})] == [f"r{i}" for i in range(5)]
    assert bench.parse_budget("agent=40, verify=3") == {"agent": 40, "verify": 3}
    with pytest.raises(ValueError):
        bench.parse_budget("nope=1")


def test_norm_action_drops_reason_and_rejects_garbage():
    assert capture.norm_action('# because\nfill(16, "Flight")') == 'fill(16, "Flight")'
    assert capture.norm_action("click( 3 )") in (None, "click(3)")
    assert capture.norm_action("I think click 3") is None


def test_repair_capture_small_family():
    eps = capture.repair_episodes(None, families=("contacts",), max_tasks=2)
    calls = [c for e in eps for c in e["calls"]]
    assert calls, "the rule tier should hand some shortlists to the model"
    for c in calls:
        assert c["expected"] == "NOT_FOUND" or c["expected"].startswith("PICK ")
        if c["expected"].startswith("PICK "):
            assert 1 <= int(c["expected"].split()[1]) <= c["meta"]["shortlist"]
        assert c["messages"][1]["content"].startswith("The step used:") and c["max_tokens"] == 64


def test_committed_workload_loads():
    p = bench.DEFAULT_WORKLOAD
    if not p.exists():
        pytest.skip("no committed workload")
    headers, eps = capture.load([p])
    assert headers[0]["version"] == capture.VERSION and headers[0]["calls"] == sum(len(e["calls"]) for e in eps)
    kinds = {e["kind"] for e in eps}
    assert kinds == {"agent", "repair", "verify"}
    assert all(e["source"] != "web-dev" for e in eps)              # live-web pages never go in the committed file

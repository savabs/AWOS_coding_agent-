"""scaffold.rl.train.repair_data: repair SFT rows are what the model sees, labelled from ground truth, and
never come from the benchmark's test families or the exam families."""
import json

import pytest

from scaffold.agent.desktop.repair.model import REPLY_RE, SYSTEM
from scaffold.rl.train import repair_data as R


def test_refuses_held_out_families():
    for fam in ("docs_hub", "shop", "wizard", "calendar"):
        with pytest.raises(SystemExit):
            R.build([fam], max_tasks=1)


@pytest.mark.timeout(120)
def test_rows_parse_and_label_from_truth(tmp_path):
    train, valid, stats = R.build(["contacts"], seeds=2, val_frac=0.2, max_tasks=6)
    rows = train + valid
    assert rows and stats["train"] == len(train) and stats["valid"] == len(valid)
    assert {e.meta["task"] for e in train}.isdisjoint({e.meta["task"] for e in valid})
    for e in rows:
        assert e.system == SYSTEM and e.meta["family"] == "contacts" and e.meta["split"] == "train"
        m = REPLY_RE.match(e.reply)
        assert m, e.reply
        assert (m.group(2) is not None) == (e.meta["label"] == "NOT_FOUND")
        if e.meta["mutation"] in ("delete", "delete_decoy"):
            assert e.meta["label"] == "NOT_FOUND"        # the control is gone: never a pick
        if m.group(1):
            assert int(m.group(1)) <= e.user.count("\n") # the picked number is one of the listed candidates
    j = rows[0].to_json()
    assert set(j) >= {"messages", "prompt", "completion", "meta"} and j["completion"].strip()


@pytest.mark.timeout(120)
def test_append_mixes_into_another_set(tmp_path):
    other = tmp_path / "verifier"
    other.mkdir()
    (other / "train.jsonl").write_text(json.dumps({"prompt": "p", "completion": "c", "meta": {}}) + "\n")
    R.main(["--out", str(tmp_path / "repair"), "--families", "contacts", "--max-tasks", "4", "--seeds", "1",
            "--append-to", str(other)])
    n_rep = sum(1 for _ in open(tmp_path / "repair" / "train.jsonl"))
    lines = (other / "train.jsonl").read_text().splitlines()
    assert len(lines) == 1 + n_rep and any('"kind": "repair"' in x for x in lines)

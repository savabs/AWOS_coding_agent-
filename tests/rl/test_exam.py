"""Bigger exam (factory ``ood300``) and the exam report (docs/specs/mw_exam.md).

Exam integrity: the frozen 99 ``ood`` tasks are unchanged and the first 99 of ood300; every
extension task is a new instance of an ood family/template, passes oracle admission over HTTP,
and is refused by the training-set split guard. Report: Wilson / McNemar / bootstrap values,
slices, family breakdown, gate, CLI outputs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from scaffold.rl.factory import FactorySource
from scaffold.rl.factory.app import FAMILIES
from scaffold.rl.factory.catalog import EXT_TAG, Catalog, _content_key
from scaffold.rl.factory.html import OOD_THEMES

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("exam_report", ROOT / "scripts" / "rl" / "exam_report.py")
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

#: sha256 of sorted (id, goal) of the 99 ``ood`` tasks at commit 5a2c6c7 (before ood300 existed)
OOD99_SHA = "58c3a1f0c6568b7e37821736aaae499c48565f78eb3dd6b303c77d02ce6b1675"

#: building ood300 (and replaying oracles) takes seconds; the 10 s default in pytest.ini times out
#: on a loaded machine, so this module gets its own ceiling
pytestmark = pytest.mark.timeout(120)

_CAT = Catalog()


def _sha(ts):
    return hashlib.sha256(json.dumps(sorted((t.id, t.goal) for t in ts)).encode()).hexdigest()


# --------------------------------------------------------------- ood300 --

def test_ood99_unchanged_and_first_in_ood300():
    ood = _CAT.tasks("ood")
    assert len(ood) == 99 and _sha(ood) == OOD99_SHA
    big = _CAT.tasks("ood300")
    assert [t.id for t in big[:99]] == [t.id for t in ood]
    assert _sha(big[:99]) == OOD99_SHA
    assert all("exam" not in t.meta for t in big[:99])
    # building the extension changes neither tasks() nor tasks("ood")
    assert len(_CAT.tasks()) == len(_CAT.entries) and _sha(_CAT.tasks("ood")) == OOD99_SHA
    assert not set(_CAT.ext) & set(_CAT.entries)


def test_ood300_only_unseen_families_and_themes():
    big = _CAT.tasks("ood300")
    ext = big[99:]
    assert 280 <= len(big) <= 320, len(big)
    assert len({t.id for t in big}) == len(big)
    train_fams = {t.family for t in _CAT.tasks("train")}
    ood_tmpls = {(t.family, t.meta["template"]) for t in _CAT.tasks("ood")}
    for t in ext:
        assert FAMILIES[t.family].ood and t.family not in train_fams
        assert (t.family, t.meta["template"]) in ood_tmpls          # same templates, new instances
        assert t.split == "ood" and t.meta["exam"] == EXT_TAG
        assert t.meta["theme"] in OOD_THEMES
        fam, tmpl, k = t.id.split("/")[1:]
        assert int(k) >= next(x.count for x in FAMILIES[fam].templates() if x.name == tmpl)
    keys = [_content_key(_CAT.entry(t.id)) for t in big]
    assert len(set(keys)) == len(keys), "duplicate exam questions"


def test_ood300_deterministic():
    other = Catalog(families=sorted(n for n, f in FAMILIES.items() if f.ood))
    assert [t.id for t in other.tasks("ood300")] == [t.id for t in _CAT.tasks("ood300")]
    assert _sha(other.tasks("ood300")) == _sha(_CAT.tasks("ood300"))


def test_training_guards_refuse_every_exam_task():
    from scaffold.rl.train.dataset import TRAIN_SPLITS, EvalSplitError, check_split
    assert "ood300" not in TRAIN_SPLITS
    for t in _CAT.tasks("ood300"):
        with pytest.raises(EvalSplitError):
            check_split(t.to_dict())
    exam = {t.id for t in _CAT.tasks("ood300")}
    assert not exam & {t.id for t in _CAT.tasks("train")}
    assert not exam & {t.id for t in _CAT.tasks()} - {t.id for t in _CAT.tasks("ood")}


def _replay_sample(ext):
    """First and last extension task of every (family, template): covers every template and the
    highest seeds while keeping the test a few seconds. All 198 stay checked by
    `python -m scaffold.rl.factory check --split ood300` (and by score() at catalog build)."""
    by = {}
    for t in ext:
        by.setdefault((t.family, t.meta["template"]), []).append(t)
    out = []
    for ts in by.values():
        out += [ts[0]] + ([ts[-1]] if len(ts) > 1 else [])
    return out


def test_ood300_oracles_replay_over_http():
    """The same admission check as `python -m scaffold.rl.factory check`, on a sample of the new tasks."""
    from scaffold.rl.factory.__main__ import check
    src = FactorySource(families=sorted(n for n, f in FAMILIES.items() if f.ood))
    try:
        ext = src.tasks("ood300")[99:]
        sample = _replay_sample(ext)
        assert {(t.family, t.meta["template"]) for t in sample} == {(t.family, t.meta["template"]) for t in ext}
        res = check(src, sample)
    finally:
        src.stop()
    assert res["checked"] == len(sample) and res["admitted"] == len(sample), res["failed"]


def test_fresh_source_resolves_ext_ids_lazily():
    """Harness workers build their own source and get task dicts: lookups must not need tasks("ood300")."""
    from scaffold.rl.contract import TaskSpec
    from scaffold.rl.factory.catalog import oracle_traj
    tid = next(t.id for t in reversed(_CAT.tasks("ood300")) if t.kind == "act" and t.meta.get("expect"))
    src = FactorySource(families=[tid.split("/")[1]])
    assert src.catalog.ext is None
    t = TaskSpec.from_dict(_CAT.entry(tid).task.to_dict())
    src.reset(t)
    assert src.oracle(t) == _CAT.entry(tid).steps
    sc = src.outcome(t, oracle_traj(tid, src.oracle(t)))
    assert not sc.success                      # nothing was executed on the fresh instance
    with pytest.raises(KeyError):
        src.catalog.entry("factory/calendar/create_event/999")


def test_ci_matrix_passes_ood300_through():
    spec = importlib.util.spec_from_file_location("ci_matrix", ROOT / "scripts" / "rl" / "ci_matrix.py")
    M = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(M)
    mat, _ = M.build(M.parse_env(ROOT / "ci" / "rl_eval.env"), "qwen3.5-4b", "factory", "ood300", "")
    assert {j["split"] for j in mat["include"]} == {"ood300"}


# --------------------------------------------------------------- report --

def test_stats_functions():
    lo, hi = R.wilson(5, 10)
    assert abs(lo - 0.2366) < 1e-3 and abs(hi - 0.7634) < 1e-3
    lo, hi = R.wilson(0, 20)
    assert lo == 0.0 and abs(hi - 0.1611) < 1e-3
    assert R.mcnemar_exact(0, 5) == pytest.approx(0.0625)
    assert R.mcnemar_exact(3, 3) == 1.0 and R.mcnemar_exact(0, 0) == 1.0
    assert R.mcnemar_exact(1, 10) == pytest.approx(2 * 12 / 2 ** 11)
    lo, hi = R.bootstrap_diff([(0.0, 1.0)] * 30 + [(0.0, 0.0)] * 70)
    assert 0.2 < lo < 0.3 < hi < 0.4
    assert R.bootstrap_diff([(1.0, 1.0)] * 10) == (0.0, 0.0)


def _rec(tid, ok, *, split="ood", source="factory", family="calendar", ext=False, unsafe=(), sample=0,
         model="m", error=None):
    meta = {"exam": EXT_TAG} if ext else {}
    return {"task": {"id": tid, "source": source, "split": split, "family": family, "meta": meta},
            "sample": sample, "trajectory": {"model": model, "error": error, "steps": []},
            "score": {"success": ok, "unsafe": list(unsafe)}}


def _write(path: Path, recs):
    path.mkdir(parents=True, exist_ok=True)
    (path / "trajectories.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))


def _arms(tmp_path):
    base, tr = [], []
    for i in range(99):                                 # ood99: base 50, trained 60
        tid = f"factory/calendar/x/{i:03d}"
        base.append(_rec(tid, i < 50))
        tr.append(_rec(tid, i < 60))
    for i in range(198):                                # ext: base 100, trained 120, 2 unsafe each
        tid = f"factory/wizard/y/{i:03d}"
        base.append(_rec(tid, i < 100, family="wizard", ext=True, unsafe=("trap_clicked",) if i in (150, 151) else ()))
        tr.append(_rec(tid, i < 120, family="wizard", ext=True, unsafe=("trap_clicked",) if i in (160, 161) else ()))
    for i in range(10):                                 # miniwob holdout: trained loses one
        tid = f"miniwob/click-tab-2/{i}"
        base.append(_rec(tid, i < 5, source="miniwob", split="holdout", family="click-tab-2"))
        tr.append(_rec(tid, i < 4, source="miniwob", split="holdout", family="click-tab-2"))
    base.append(_rec("factory/calendar/x/900", False, error="llama-server died"))    # infra error, dropped
    tr.append(_rec("factory/calendar/x/901", True))                                 # unpaired
    _write(tmp_path / "base" / "shard1", base[:150])
    _write(tmp_path / "base" / "shard2", base[150:])
    _write(tmp_path / "trained", tr)
    return tmp_path / "base", tmp_path / "trained"


def test_report_slices_and_gate(tmp_path):
    b, t = _arms(tmp_path)
    r = R.report(R.load_arm([str(b)]), R.load_arm([str(t)]))
    assert r["paired_tasks"] == 99 + 198 + 10
    assert r["unpaired"] == {"base_only": 0, "trained_only": 1}
    assert r["infra_errors"] == {"base": 1, "trained": 0}
    s = r["by_split"]
    assert list(s)[:5] == ["all", "factory_ood300", "factory_ood99", "factory_ood300_ext", "miniwob_holdout"]
    assert s["factory_ood99"]["base"]["tasks"] == 99 and s["factory_ood300_ext"]["base"]["tasks"] == 198
    assert s["factory_ood99"]["base"]["success"] == round(50 / 99, 4)
    assert s["factory_ood300"]["diff"] == round(30 / 297, 4)
    assert s["factory_ood300"]["mcnemar"] == {"base_only": 0, "trained_only": 30,
                                              "p": round(R.mcnemar_exact(0, 30), 6)}
    # i=150,151 unsafe in base (would have failed anyway), 160,161 unsafe in trained
    lo, hi = s["factory_ood300"]["bootstrap95"]
    assert 0 < lo < s["factory_ood300"]["diff"] < hi
    assert s["miniwob_holdout"]["mcnemar"]["base_only"] == 1
    assert s["factory_ood300"]["trained"]["unsafe_by_code"] == {"trap_clicked": 2}
    assert set(r["by_family"]) == {"factory/calendar", "factory/wizard", "miniwob/click-tab-2"}
    assert r["gate"]["slice"] == "factory_ood300" and r["gate"]["pass"]
    r2 = R.report(R.load_arm([str(b)]), R.load_arm([str(t)]), gate_min=0.2)
    assert not r2["gate"]["pass"] and not r2["gate"]["diff_ok"]


def test_report_multi_sample_and_filter(tmp_path):
    recs = [_rec("factory/calendar/x/000", j < 2, sample=j, model="qwen-base") for j in range(4)]
    recs += [_rec("factory/calendar/x/000", True, sample=j, model="qwen-r3") for j in range(4)]
    _write(tmp_path / "mixed", recs)
    base = R.load_arm([str(tmp_path / "mixed")], "qwen-base")
    tr = R.load_arm([str(tmp_path / "mixed")], "qwen-r3")
    assert len(base["eps"]["factory/calendar/x/000"]) == 4
    r = R.report(base, tr)
    assert r["by_split"]["all"]["base"]["success"] == 0.5 and r["by_split"]["all"]["diff"] == 0.5
    assert r["by_split"]["all"]["mcnemar"]["trained_only"] == 1       # 0.5 is not a pass


def test_cli_writes_md_and_json(tmp_path, capsys):
    b, t = _arms(tmp_path)
    out = tmp_path / "rep" / "exam"
    assert R.main(["--base", str(b), "--trained", str(t), "--out", str(out), "--trained-label", "r3"]) == 0
    md = (tmp_path / "rep" / "exam.md").read_text()
    js = json.loads((tmp_path / "rep" / "exam.json").read_text())
    assert "| factory_ood99 | 99 |" in md and "## Gate (factory_ood300): PASS" in md and "## By family" in md
    assert js["labels"] == ["base", "r3"] and js["by_split"]["all"]["r3"]["tasks"] == 307
    assert "# Exam report: r3 vs base" in capsys.readouterr().out
    assert R.main(["--base", str(tmp_path / "nothing"), "--trained", str(t)]) == 2

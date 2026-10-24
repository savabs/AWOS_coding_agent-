"""Data cleaner (clean.py) through dataset.build(--clean), --extra-rows validation, and exam integrity:
no factory ood task / family and no MiniWoB holdout type may reach training data, even mislabelled.

    PYTHONPATH=.:tests python3 -m pytest tests/rl/test_train_clean.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scaffold.rl.train import chat, clean as cl, dataset as ds

SYSTEM = "You operate a web dashboard.\nRules:\n- one action per reply\n"
GRAMMAR = 'root ::= "click(" [1-9] ")"'


def user(goal: str, step: int, obs: str, inst: int = 1) -> str:
    return (f"Goal: {goal}\nStep {step} of 12.\n<observation untrusted=\"true\">\nURL path: /t/i{inst}/home\n"
            f"{obs}\n</observation>\nReply with ONE action line.")


def step(i, reply, obs="[1] tab \"A\"\n[2] tab \"B\"", goal="g", inst=1):
    return {"i": i, "system": SYSTEM, "user": user(goal, i, obs, inst), "grammar": GRAMMAR, "reply": reply,
            "action": reply.splitlines()[-1], "valid": True, "url": "", "ms": 1.0}


def rec(tid, steps, model="qwen", family="fam", split="train", source="factory", sample=0, success=True):
    return {"task": {"id": tid, "source": source, "family": family, "split": split, "goal": "g", "kind": "act",
                     "tags": [], "meta": {}},
            "sample": sample,
            "trajectory": {"task_id": tid, "model": model, "steps": steps, "outcome": "verified", "reason": "",
                           "events": [], "flags": [], "seconds": 1.0, "error": ""},
            "score": {"success": success, "unsafe": [], "progress": 1.0, "notes": ""}}


def write(tmp: Path, name: str, recs) -> Path:
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "trajectories.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    return d


def build(tmp, recs, **kw):
    kw.setdefault("val_frac", 0.0)
    return ds.build([ds.parse_input(f"a={write(tmp, 'a', recs)}")], **kw)


OBS_A = "[1] tab \"A\"\n[2] tab \"B\"\nshowing A"
OBS_B = "[1] tab \"A\"\n[2] tab \"B\"\nshowing B"


# -------------------------------------------------------------------- loops --

def test_tab_switching_loop_is_dropped_and_clean_episode_kept(tmp_path):
    loop = [step(1, "click(2)", OBS_A), step(2, "click(1)", OBS_B), step(3, "click(2)", OBS_A),
            step(4, "click(1)", OBS_B), step(5, 'done("x")', OBS_A)]
    ok = [step(1, "click(2)", OBS_A, goal="h"), step(2, 'done("y")', OBS_B, goal="h")]
    train, _, st = build(tmp_path, [rec("t/loop", loop), rec("t/ok", ok)], clean=True)
    assert {e.meta["task_id"] for e in train} == {"t/ok"}
    assert st["sources"]["a"]["episodes_dropped"] == {"looping": 1}
    assert st["clean"]["no_loops"] == {"unit": "episodes", "in": 2, "out": 1, "dropped": 1}
    # without --clean the loop survives (old behaviour unchanged)
    train2, _, _ = build(tmp_path, [rec("t/loop", loop), rec("t/ok", ok)])
    assert "t/loop" in {e.meta["task_id"] for e in train2}


def test_same_action_on_a_changed_page_is_not_a_loop():
    steps = [step(1, "click(1)", "[1] link \"Next\"\npage 1"), step(2, "click(1)", "[1] link \"Next\"\npage 2"),
             step(3, 'done("ok")', "page 3")]
    assert cl.loop_reason(steps) is None
    same = [step(1, "click(1)", OBS_A), step(2, "click(1)", OBS_A)]
    assert cl.loop_reason(same) == "looping"
    assert cl.loop_reason(same, max_repeats=2) is None


def test_instance_id_does_not_hide_a_loop():
    steps = [step(1, "click(2)", OBS_A, inst=3), step(2, "click(2)", OBS_A, inst=4)]
    assert cl.loop_reason(steps) == "looping"


# ------------------------------------------------------------ near dupes --

def test_near_duplicates_dropped_across_step_number_instance_and_reason(tmp_path):
    a = [step(1, "click(2)", OBS_A, inst=1), step(2, 'done("x")', OBS_B, inst=1)]
    b = [step(3, "# open tab B\nclick(2)", OBS_A, inst=7), step(4, 'done("x")', OBS_B, inst=7)]
    c = [step(1, "click(1)", OBS_A, inst=2)]                      # different action: kept
    train, _, st = build(tmp_path, [rec("t/a", a), rec("t/b", b), rec("t/c", c)], clean=True)
    assert len(train) == 3
    assert st["sources"]["a"]["steps_dropped"] == {"near_duplicate": 2}
    assert st["clean"]["near_dedupe"]["in"] == 5 and st["clean"]["near_dedupe"]["out"] == 3


# ---------------------------------------------------------- scripted cap --

def test_scripted_rows_capped_at_30_percent_and_report_counts(tmp_path):
    recs = [rec(f"t/o{i}", [step(1, "click(1)", f"oracle page {i}"), step(2, 'done("x")', f"oracle end {i}")],
                model="oracle", family="mail" if i < 8 else "shop") for i in range(10)]
    recs += [rec(f"t/q{i}", [step(1, "click(2)", f"model page {i}")], model="qwen3.5-4b", family="mail")
             for i in range(14)]
    train, _, st = build(tmp_path, recs, clean=True)
    scripted = [e for e in train if cl.is_scripted(e.meta["model"])]
    assert len(train) - len(scripted) == 14
    assert len(scripted) == int(0.3 / 0.7 * 14) == 6
    assert len(scripted) / len(train) <= 0.30
    # family-first: "shop" has no model rows, so its oracle rows are kept before more "mail" ones
    assert {e.meta["family"] for e in scripted} == {"mail", "shop"}
    assert sum(e.meta["family"] == "shop" for e in scripted) == 3
    cap = st["clean"]["scripted_cap"]
    assert (cap["in"], cap["out"]) == (34, 20)
    assert st["clean"]["verified_success"] == {"unit": "episodes", "in": 24, "out": 24, "dropped": 0}
    assert st["train_examples"] == 20 and sum(st["train_by"]["source"].values()) == 20


def test_cap_skipped_and_reported_when_only_scripted_rows(tmp_path):
    recs = [rec(f"t/o{i}", [step(1, "click(1)", f"p{i}")], model="oracle") for i in range(5)]
    train, _, st = build(tmp_path, recs, clean=True)
    assert len(train) == 5
    assert "skipped" in st["clean"]["scripted_cap_detail"]


def test_verified_success_counted(tmp_path):
    recs = [rec("t/ok", [step(1, "click(1)")]), rec("t/bad", [step(1, "click(1)", "x")], success=False)]
    _, _, st = build(tmp_path, recs, clean=True)
    assert st["clean"]["verified_success"] == {"unit": "episodes", "in": 2, "out": 1, "dropped": 1}


def test_cli_clean_prints_and_writes(tmp_path, capsys):
    d = write(tmp_path, "a", [rec(f"t/{i}", [step(1, "click(1)", f"p{i}")]) for i in range(4)])
    out = tmp_path / "out"
    assert ds.main(["--input", f"a={d}", "--out", str(out), "--val-frac", "0", "--clean"]) == 0
    assert "clean (before -> after)" in capsys.readouterr().out
    assert "clean" in json.loads((out / "stats.json").read_text())


# -------------------------------------------------------------- extra rows --

def row(tid, reply="click(1)", split="train", obs="extra page", **meta):
    e = ds.Example(SYSTEM, user("g", 1, obs), reply, dict({"task_id": tid, "split": split,
                                                           "task_source": "factory", "family": "fam",
                                                           "model": "correction"}, **meta))
    return e.to_json()


def write_rows(tmp: Path, name: str, rows) -> Path:
    p = tmp / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def test_extra_rows_are_included_deduped_and_split_by_task(tmp_path):
    p = write_rows(tmp_path, "corr.jsonl", [row("t/x"), row("t/x"), row("t/y", "# take B\nclick(2)", obs="o2")])
    d = write(tmp_path, "a", [rec("t/a", [step(1, "click(1)", "p")])])
    train, valid, st = ds.build([ds.parse_input(f"a={d}")], val_frac=0.0, extra=[ds.parse_extra(f"corr={p}:2")])
    got = [e for e in train if e.meta["src"] == "corr"]
    assert len(got) == 4                                        # 2 unique rows x weight 2
    assert st["sources"]["corr"]["steps_dropped"] == {"duplicate": 1}
    assert {e.reply for e in got} == {"click(1)", "# take B\nclick(2)"}
    for e in got:
        j = e.to_json()
        assert j["prompt"] == chat.render_prompt(e.system, e.user)


@pytest.mark.parametrize("bad, why", [
    ({"messages": []}, "messages"),
    (dict(row("t/x"), messages=[{"role": "user", "content": "u"}, {"role": "assistant", "content": "click(1)"}]),
     "system, user, assistant"),
    (dict(row("t/x"), meta={"split": "train"}), "task_id"),
    (row("t/x", reply="clik(1)"), "action"),
    (row("t/x", reply="# a\n# b\nclick(1)"), "reason"),
    (row("t/x", reply="# " + "x" * 200 + "\nclick(1)"), "reason"),
    (dict(row("t/x"), prompt="something else"), "prompt"),
])
def test_bad_extra_rows_abort_the_build(tmp_path, bad, why):
    p = write_rows(tmp_path, "bad.jsonl", [row("t/ok"), bad])
    with pytest.raises(ds.RowSchemaError, match=why):
        ds.build([], val_frac=0.0, extra=[ds.parse_extra(str(p))])
    assert ds.main(["--extra-rows", str(p), "--out", str(tmp_path / "o")]) == 2


@pytest.mark.parametrize("meta", [
    {"split": "ood"}, {"split": "holdout"}, {"split": None},
    {"family": "calendar"},                                         # factory ood family labelled train
    {"task_source": "miniwob", "family": "click-tab"},
])
def test_extra_rows_refuse_eval_tasks(tmp_path, meta):
    tid = "miniwob/click-tab-2/s3" if meta.get("task_source") == "miniwob" else "factory/x/y/000"
    r = row(tid)
    r["meta"].update(meta)
    p = write_rows(tmp_path, "evil.jsonl", [r])
    with pytest.raises(ds.EvalSplitError):
        ds.build([], val_frac=0.0, extra=[ds.parse_extra(str(p))])


# ---------------------------------------------------------- exam integrity --

def test_every_factory_ood_task_is_refused_even_if_labelled_train():
    from scaffold.rl.factory import FactorySource
    ood = list(FactorySource().tasks("ood"))
    assert len(ood) == 99
    for t in ood:
        with pytest.raises(ds.EvalSplitError):
            ds.check_split(t.to_dict())
        relabelled = dict(t.to_dict(), split="train")
        with pytest.raises(ds.EvalSplitError, match="exam"):
            ds.check_split(relabelled)
        assert cl.exam_reason(t.id, "train") is not None          # id alone is enough


def test_every_miniwob_holdout_type_is_refused_even_if_labelled_train():
    from scaffold.rl.miniwob import catalog
    held = catalog.frozen_holdout()
    assert len(held) == 15 and "click-tab-2" in held
    for name in held:
        fam = catalog.INCLUDED[name].family
        for task in ({"id": f"miniwob/{name}/s0", "split": "train", "source": "miniwob", "family": fam},
                     {"id": f"miniwob/{name}/s0", "split": "train"}):
            with pytest.raises(ds.EvalSplitError, match="exam"):
                ds.check_split(task)
    train_types = [n for n in catalog.INCLUDED if n not in set(held)]
    assert train_types
    for name in train_types:
        ds.check_split({"id": f"miniwob/{name}/s0", "split": "train", "source": "miniwob",
                        "family": catalog.INCLUDED[name].family})


def test_mislabelled_ood_trajectory_aborts_cli(tmp_path):
    d = write(tmp_path, "a", [rec("t/ok", [step(1, "click(1)")]),
                              rec("factory/calendar/x/000", [step(1, "click(1)", "c")], family="calendar")])
    assert ds.main(["--input", f"a={d}", "--out", str(tmp_path / "out"), "--clean"]) == 2
    assert not (tmp_path / "out").exists()

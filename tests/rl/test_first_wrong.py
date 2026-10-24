"""first_wrong.py: first-wrong-step finder + correction rows.

Fast tests run the finder against a tiny deterministic fake environment (no browser) that plays the
same prompt shape as MicroAgent (observation block with [n] role "name" lines, so the REAL
harness.models.OracleModel resolves the oracle). The integration test at the bottom replays real
factory trajectories through headless Chromium (skipped without Playwright / Chromium):
    PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/rl/test_first_wrong.py -q
"""
import json
import re

import pytest

from scaffold.rl.contract import TaskSpec
from scaffold.rl.train import dataset as D
from scaffold.rl.train import first_wrong as FW
from scaffold.rl.train.chat import render_completion, render_prompt

SYS = "You are a web agent."
CTRLS = [(1, "button", "A"), (2, "button", "B"), (3, "button", "Delete all"), (4, "link", "Help"),
         (5, "textbox", "Name")]
ORACLE = [{"op": "click", "role": "button", "name": "A"}, {"op": "click", "role": "button", "name": "B"},
          {"op": "done", "text": "A and B done."}]
ORACLE_FILL = [{"op": "fill", "role": "textbox", "name": "Name", "value": "Ann"}, {"op": "done", "text": "ok"}]


class FakeEnv(FW.Env):
    """A=1 then B=2 completes; B before A breaks the app; 'Delete all' breaks it and is unsafe; Help is a no-op;
    Name (5) locks after the first fill. done succeeds iff the goal holds and nothing broke."""

    def __init__(self, goal="Click A then B.", want_name=None, cap=8, drift_at=0, need_a=False):
        self.goal, self.want_name, self.cap, self.drift_at, self.need_a = goal, want_name, cap, drift_at, need_a
        self.episodes = 0

    def _user(self, st, i, hist, cap):
        lines = [f'[{n}] {r} "{nm}"' for n, r, nm in CTRLS]
        state = f"a={st['a']} b={st['b']} name={st['name']!r}"
        if self.drift_at and self.episodes > self.drift_at:
            state += " (drifted)"
        h = ("\nYour previous actions and results:\n" + "\n".join(hist[-6:])) if hist else ""
        return (f"Goal: {self.goal}\nStep {i} of {cap}.{h}\n<observation untrusted=\"true\">\nURL path: /t/i{self.episodes}/home\n"
                f"{state}\nControls ({len(CTRLS)} total, showing 1-{len(CTRLS)}):\n" + "\n".join(lines) + "\n</observation>")

    def run(self, model, cap=None):
        cap = cap or self.cap
        self.episodes += 1
        st = {"a": 0, "b": 0, "name": "", "broken": False}
        steps, hist, unsafe, outcome, success = [], [], [], "halted", False
        for i in range(1, cap + 1):
            user = self._user(st, i, hist, cap)
            reply = model.decide(SYS, user, "root ::= x")
            act = reply.strip().splitlines()[-1] if reply.strip() else ""
            m = re.fullmatch(r'(click|fill|done|needs_user)\((.*)\)', act)
            steps.append({"i": i, "system": SYS, "user": user, "grammar": "root ::= x", "reply": reply,
                          "action": act if m else "", "valid": bool(m), "url": "", "ms": 0.0})
            if not m:
                hist.append(f"{i}. (unparseable reply) -> rejected")
                continue
            verb, arg = m.groups()
            hist.append(f"{i}. {act} -> ok")
            if verb == "done":
                outcome = "completed_unverified"
                ok = st["a"] and st["b"] if self.want_name is None else \
                    st["name"] == self.want_name and (st["a"] or not self.need_a)
                success = bool(ok) and not st["broken"]
                break
            if verb == "needs_user":
                outcome = "needs_user"
                break
            n = int(arg.split(",")[0])
            if verb == "click":
                if n == 1 and not st["b"]:
                    st["a"] = 1
                elif n == 2:
                    st["b"], st["broken"] = 1, st["broken"] or not st["a"]
                elif n == 3:
                    st["broken"] = True
                    unsafe.append("trap_clicked")
            elif verb == "fill" and n == 5:
                if st["name"]:                                  # locked: rejected like a MicroAgent repeat
                    steps[-1]["valid"] = False
                    hist[-1] = f"{i}. {act} -> SKIPPED"
                else:
                    st["name"] = json.loads(arg.split(",", 1)[1].strip())
        return steps, success and not unsafe, unsafe, outcome

    def check(self, task, replies, suffix, *, guards, extended=False):
        self.goal = task.goal
        steps, ok, unsafe, outcome = self.run(FW.PrefixThenOracle(replies, suffix),
                                              cap=FW.EXTENDED_STEPS if extended else None)
        return FW.Replay(steps=steps, success=ok, unsafe=unsafe, outcome=outcome)


class Replies:
    def __init__(self, replies):
        self.r, self.i = list(replies), 0

    def decide(self, system, user, grammar=None):
        self.i += 1
        return self.r[self.i - 1] if self.i <= len(self.r) else 'needs_user("out")'


def task_dict(tid="factory/fake/t/000", split="train", source="factory", kind="act", max_steps=8, goal=None):
    return TaskSpec(id=tid, source=source, family="fake", split=split, goal=goal, kind=kind,
                    max_steps=max_steps).to_dict()


def record(env, replies, **kw):
    kw.setdefault("goal", env.goal)
    steps, ok, unsafe, outcome = env.run(Replies(replies))
    assert not ok, "test trajectory must fail"
    return {"task": task_dict(**kw), "sample": 0,
            "trajectory": {"task_id": "x", "model": "q4b", "steps": steps, "outcome": outcome,
                           "events": [{"event": "harness", "guards": "product"}], "error": ""},
            "score": {"success": False, "unsafe": unsafe}}


def first(env, replies, oracle=ORACLE, **kw):
    rec = record(env, replies)
    return rec, FW.find_first_wrong(rec, env, oracle, **kw)


# ------------------------------------------------------------- the finder --

def test_wrong_control_found_at_the_breaking_step_with_oracle_correction():
    env = FakeEnv()
    rec, v = first(env, ["click(4)", "click(2)", 'done("x")'])        # Help is harmless; B before A breaks it
    assert v.verdict == "labelled" and v.step == 2 and v.category == "wrong_control"
    assert v.wrong_action == "click(2)" and v.correct_reply == "click(1)" and v.correct_action == "click(1)"


def test_unsafe_gave_up_misread_categories():
    env = FakeEnv()
    _, v = first(env, ["click(1)", "click(3)", 'done("x")'])
    assert (v.step, v.category, v.correct_action) == (2, "unsafe", "click(2)") and "trap_clicked" in v.rule
    _, v = first(env, ["click(1)", 'needs_user("stuck")'])
    assert (v.step, v.category, v.correct_action) == (2, "gave_up_early", "click(2)")
    _, v = first(env, ["click(1)", 'done("all done")'])
    assert (v.step, v.category) == (2, "gave_up_early")
    envf = FakeEnv(goal="Set name to Ann.", want_name="Ann")
    _, v = first(envf, ['fill(5, "Bob")', 'done("ok")'], oracle=ORACLE_FILL)
    assert (v.step, v.category, v.correct_action) == (1, "misread_goal", 'fill(5, "Ann")')


def test_loop_and_budget():
    env = FakeEnv(cap=4)                                                 # oracle needs 3 steps
    _, v = first(env, ["click(4)"] * 4)
    assert v.verdict == "labelled" and v.step == 2 and v.category == "loop"
    assert "same action" in v.rule and v.correct_action == "click(1)"
    env = FakeEnv(cap=4)
    _, v = first(env, ["click(1)", "click(4)", "click(4)", "click(4)"])  # wasted a step, then ran out
    assert v.step == 3 and v.category == "loop"


def test_invalid_reply_category_and_shortcut_saves_checks():
    env = FakeEnv(cap=4)
    _, v = first(env, ["click(1)", "click(2)", "blah", "blah"])
    # s_3 is still recoverable (done fits in the last step); replaying the 2nd invalid reply uses up the cap
    assert v.step == 4 and v.category == "invalid_action" and v.correct_action.startswith("done(")
    # steps 1-2 equal the oracle's own actions: proven recoverable by the first check, no new episodes
    assert v.checks <= 7


def test_rejected_oracle_move_is_replaced_by_an_accepted_plan():
    env = FakeEnv(goal="Set name to Ann and click A.", want_name="Ann", need_a=True)
    orc = [{"op": "fill", "role": "textbox", "name": "Name", "value": "Ann"},
           {"op": "click", "role": "button", "name": "A"}, {"op": "done", "text": "ok"}]
    _, v = first(env, ['fill(5, "Ann")', "click(4)", 'needs_user("?")'], oracle=orc)
    # from s_2 the first passing plan re-fills Name (rejected there); the accepted correction is click(1)
    assert v.verdict == "labelled" and v.step == 3 and v.correct_action == "click(1)" and v.category == "gave_up_early"


def test_plans_add_popup_dismissal_before_later_suffixes():
    o = [{"op": "click", "name": "No thanks"}, {"op": "click", "name": "X"}, {"op": "click", "name": "Y"},
         {"op": "done", "text": ""}]
    t = TaskSpec(id="x", source="factory", family="f", split="train", goal="g", tags=["popup"])
    plans = FW.plans_of(o, t)
    assert plans[:4] == [o, o[1:], o[2:], o[3:]] and plans[4:] == [o[:1] + o[2:], o[:1] + o[3:]]
    assert len(FW.plans_of(o, TaskSpec(id="x", source="factory", family="f", split="train", goal="g"))) == 4


def test_divergence_is_reported_not_labelled():
    env = FakeEnv(drift_at=1)                                            # every replay sees a different page
    rec = record(env, ["click(4)", "click(2)"])
    v = FW.find_first_wrong(rec, env, ORACLE)
    assert v.verdict == "diverged" and "prompt differs" in v.reason
    assert FW.find_first_wrong(rec, env, []).verdict == "no_oracle"


def test_check_budget():
    env = FakeEnv()
    _, v = first(env, ["click(4)", "click(2)"], max_checks=1)
    assert v.verdict == "check_budget"


def test_state_key_modes():
    a = {"system": "s", "user": "Goal: g\nStep 1 of 8.\nhist A\n<observation>\n/t/i3/x [1] button \"A\"\n</observation>"}
    b = {"system": "s", "user": "Goal: g\nStep 1 of 40.\nhist B\n<observation>\n/t/i9/x [1] button \"A\"\n</observation>"}
    assert FW.state_key(a) != FW.state_key(b)
    assert FW.state_key(a, "observation") == FW.state_key(b, "observation")
    c = dict(a, user=a["user"].replace("hist A", "hist B"))
    assert FW.state_key(c, extended=True) == FW.state_key(b, extended=True)
    mw = {"system": "s", "user": "Goal: g\n(path /miniwob/x.html?awos_seed=8&awos_ep=c9ded5a1&awos_aria=1)"}
    assert FW.state_key(mw) == FW.state_key(dict(mw, user=mw["user"].replace("c9ded5a1", "0b7e")))


# --------------------------------------------------------- rows + integrity --

class Lookup(FW.TaskLookup):
    def __init__(self):
        super().__init__({"factory/fake/t/000": "train", "factory/fake/ood/000": "ood"}, ["click-tab-2"])


def _write(tmp_path, recs, name="trajectories.jsonl"):
    p = tmp_path / name
    p.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return D.parse_input(f"q4b={p}")


def test_rows_have_exactly_dataset_schema(tmp_path):
    env = FakeEnv()
    rec = record(env, ["click(4)", "click(2)"])
    good = {"task": task_dict(), "sample": 1, "score": {"success": True, "unsafe": []},
            "trajectory": {"steps": rec["trajectory"]["steps"][:1], "outcome": "verified", "error": "", "model": "o"}}
    src = _write(tmp_path, [rec, good])
    train, valid, pairs, verdicts, st = FW.build([src], lambda s: env, lambda t: ORACLE, lookup=Lookup(),
                                                 val_frac=0.0, log=lambda *a: None)
    assert len(train) == 1 and not valid and len(pairs) == 1 and st["by_category"]["wrong_control"] == 1
    assert st["skipped"] == {"success": 1}
    ds_train, _, _ = D.build([src], val_frac=0.0)
    ref = ds_train[0].to_json()
    row = train[0]
    assert set(row) == set(ref) and set(ref["meta"]) <= set(row["meta"])
    assert [m["role"] for m in row["messages"]] == ["system", "user", "assistant"]
    s2 = rec["trajectory"]["steps"][1]
    assert row["prompt"] == render_prompt(s2["system"], s2["user"]) and row["completion"] == render_completion("click(1)")
    assert row["meta"]["correction"] and row["meta"]["wrong_action"] == "click(2)" and row["meta"]["split"] == "train"
    p = pairs[0]
    assert p["chosen"] == row["completion"] and p["rejected"] == render_completion("click(2)")
    assert p["meta"]["rule"] == "first_wrong"
    FW.write(tmp_path / "out", train, valid, pairs, verdicts, st)
    assert (tmp_path / "out" / "summary.md").read_text().count("| wrong_control | 1 |") == 1
    assert json.loads((tmp_path / "out" / "verdicts.jsonl").read_text().splitlines()[0])["verdict"] == "labelled"


def test_val_split_by_task_id_like_dataset(tmp_path):
    env = FakeEnv()
    recs = []
    lk = Lookup()
    for k in range(30):
        tid = f"factory/fake/t/{k:03d}"
        lk.factory[tid] = "train"
        env.goal = f"Click A then B ({k})."                 # distinct prompts: rows are deduped by prompt
        r = record(env, ["click(4)", "click(2)"], tid=tid)
        recs.append(r)
    train, valid, *_ = FW.build([_write(tmp_path, recs)], lambda s: env, lambda t: ORACLE, lookup=lk,
                                val_frac=0.2, seed=0, log=lambda *a: None)
    assert train and valid
    for r in train + valid:
        assert D.task_is_val(r["meta"]["task_id"], 0.2, 0) == (r in valid)


@pytest.mark.parametrize("bad", [dict(split="ood"), dict(split="holdout", source="miniwob"),
                                 dict(tid="factory/fake/ood/000"),            # record says train, catalog says ood
                                 dict(tid="miniwob/click-tab-2/s3", source="miniwob"),   # holdout type
                                 dict(tid="factory/fake/unknown/000"), dict(source="webshop")])
def test_eval_tasks_are_a_hard_error(tmp_path, bad):
    env = FakeEnv()
    ok = record(env, ["click(4)", "click(2)"])
    evil = record(env, ["click(4)", "click(2)"], **bad)
    with pytest.raises(D.EvalSplitError):
        FW.build([_write(tmp_path, [ok, evil])], lambda s: env, lambda t: ORACLE, lookup=Lookup(),
                 log=lambda *a: None)


def test_assert_train_only_on_rows():
    lk = Lookup()
    FW.assert_train_only([{"meta": {"task_id": "factory/fake/t/000", "task_source": "factory", "split": "train"}}], lk)
    for m in ({"task_id": "factory/fake/ood/000", "task_source": "factory", "split": "train"},
              {"task_id": "factory/fake/t/000", "task_source": "factory", "split": "ood"},
              {"task_id": "miniwob/click-tab-2/s0", "task_source": "miniwob", "split": "train"}):
        with pytest.raises(D.EvalSplitError):
            FW.assert_train_only([{"meta": m}], lk)


@pytest.mark.timeout(120)
def test_real_exam_never_admitted_by_default_lookup():
    """The real exam: every factory ood task and every MiniWoB holdout type is refused by the live lookup."""
    from scaffold.rl.miniwob import catalog as mwc
    lk = FW.TaskLookup.default({"factory", "miniwob"})
    ood = [t for t, s in lk.factory.items() if s == "ood"]
    assert len(ood) >= 99 and "click-tab-2" in lk.mw_holdout and len(lk.mw_holdout) == 15
    for tid in ood:
        with pytest.raises(D.EvalSplitError):
            lk.check({"id": tid, "source": "factory", "split": "train"})
    for typ in mwc.frozen_holdout():
        with pytest.raises(D.EvalSplitError):
            lk.check({"id": f"miniwob/{typ}/s0", "source": "miniwob", "split": "train", "meta": {"type": typ}})
    train_id = next(t for t, s in lk.factory.items() if s == "train")
    lk.check({"id": train_id, "source": "factory", "split": "train"})


@pytest.mark.timeout(120)
def test_cli_dry_run_and_refusal(tmp_path, capsys):
    env = FakeEnv()
    from scaffold.rl.factory.catalog import Catalog
    cat = Catalog()
    tid = next(t for t, e in cat.entries.items() if e.task.split == "train")
    ood = next(t for t, e in cat.entries.items() if e.task.split == "ood")
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(record(env, ["click(4)", "click(2)"], tid=tid)) + "\n")
    assert FW.main(["--policy", f"q={p}", "--out", str(tmp_path / "o"), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["failed_episodes"] == 1
    p.write_text(json.dumps(record(env, ["click(4)", "click(2)"], tid=ood)) + "\n")
    assert FW.main(["--policy", f"q={p}", "--out", str(tmp_path / "o"), "--dry-run"]) == 2


# ------------------------------------------------------- real Chromium replay --

def _chromium_or_skip():
    pytest.importorskip("playwright")
    from scaffold.rl.harness.chromium import find_chromium
    if not find_chromium():
        pytest.skip("no cached Chromium build")


@pytest.mark.timeout(600)
def test_real_factory_replay_finds_injected_mistakes():
    """Record failing runs on real factory train tasks (oracle with one step replaced), then let the finder
    replay them through MicroAgent + Chromium and locate the replaced step."""
    _chromium_or_skip()
    from scaffold.rl.factory import FactorySource
    from scaffold.rl.harness.chromium import Chromium
    from scaffold.rl.harness.episode import run_episode
    from scaffold.rl.harness.runner import record as mkrec
    src = FactorySource()
    src.start()
    tasks = [t for t in src.tasks("train") if t.kind == "act" and len(src.oracle(t)) >= 3][:2]
    try:
        with Chromium() as ch:
            env = FW.HarnessEnv(src, ch)
            for t in tasks:
                orc = src.oracle(t)
                # give up right before the final done(): the first wrong step is the last oracle step
                bad = orc[:-1] + [{"op": "needs_user", "text": "not sure"}]
                from scaffold.rl.harness.models import OracleModel
                traj, sc = run_episode(src, t, OracleModel(bad), model_label="bad", chromium=ch)
                rec = json.loads(json.dumps(mkrec(t, 0, traj, sc)))
                assert D.episode_drop_reason(rec) in ("act_ended_needs_user", "not_success") and not traj.error
                v = FW.find_first_wrong(rec, env, orc)
                assert v.verdict == "labelled", (t.id, v)
                assert v.step == len(traj.steps) and v.category == "gave_up_early"
                assert v.correct_action.startswith("done(")
    finally:
        src.stop()

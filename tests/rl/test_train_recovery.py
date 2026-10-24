"""scaffold/rl/train/recovery.py: anti-loop / recovery rows (plan change 4).

Unit tests run on synthetic MicroAgent-shaped prompts (no browser). The integration test at the bottom
runs the real harness (MicroAgent + headless Chromium) on factory train tasks and is skipped without
Playwright / a Chromium build (use ~/.awos/rl-venv/bin/python).
"""
import json

import pytest

from scaffold.rl.contract import Score, Step, TaskSpec, Trajectory
from scaffold.rl.train import recovery as R
from scaffold.rl.train.chat import render_completion, render_prompt
from scaffold.rl.train.dataset import EvalSplitError


def obs(ctrls, title="Time off policy - Team Handbook", heading="Policy handbook", total=None, a=1, text=""):
    """A user prompt in MicroAgent's shape: header, page purpose, <observation> with numbered controls."""
    total = total if total is not None else len(ctrls)
    lines = [f'[{i}] {line}' for i, line in enumerate(ctrls, a)]
    return "\n".join(["Goal: g", "Step 1 of 12.",
                      f'Current page: "{title}" (path /t/i1/policies); main heading "{heading}".',
                      '<observation untrusted="true">', f"Title: {title}", "Page text:", text,
                      f"Controls ({total} total, showing {a}-{a + len(ctrls) - 1}):", *lines, "</observation>",
                      "Reply with ONE action line."])


TABS = ['link "Time off" (in "Time off policy")', 'link "Expenses" (in "Time off policy")',
        'link "Travel" (in "Time off policy")', 'button "Annual holiday" (in "Time off policy")',
        'link "Overview"', 'link "Policies"']
TABS2 = ['link "Time off" (in "Expenses policy")', 'link "Expenses" (in "Expenses policy")',
         'link "Travel" (in "Expenses policy")', 'button "Approvals" (in "Expenses policy")',
         'link "Overview"', 'link "Policies"']
ORACLE = [{"op": "click", "role": "link", "name": "Expenses"}, {"op": "click", "role": "button", "name": "Approvals"},
          {"op": "done", "text": "$1600"}]


def drive(model, prompts):
    return [model.decide("SYS", u, "root ::= x") for u in prompts]


# ------------------------------------------------------------------ parsing --

def test_parse_controls_window_and_page():
    u = obs(['textbox "Rows per page" (in "Display") = "20"',
             'combobox "Language" (in "Display") options=English|German|French',
             'button "OK" (in "Confirm") (dialog)'], total=50, a=41)
    cs = R.controls(u)
    assert [(c.n, c.role, c.name, c.context) for c in cs] == [(41, "textbox", "Rows per page", "Display"),
                                                              (42, "combobox", "Language", "Display"),
                                                              (43, "button", "OK", "Confirm")]
    assert cs[0].value == "20" and cs[1].options == ["English", "German", "French"] and cs[2].dialog
    assert R.window(u) == (50, 41, 43)
    assert R.page_of(u) == ("Time off policy - Team Handbook", "Policy handbook")


# -------------------------------------------------------------------- model --

def test_clean_plan_model_is_the_oracle():
    m = R.PlanModel(ORACLE)
    assert drive(m, [obs(TABS), obs(TABS2), obs(TABS2)]) == ["click(2)", "click(4)", 'done("$1600")']
    assert [e.phase for e in m.emitted] == ["prefix"] * 3 and not m.failure
    acts = R.clean_actions(m)
    assert R.applicable_ks("wrong_tab", acts) == [0]
    assert R.applicable_ks("pingpong", acts) == [1]
    assert R.applicable_ks("repeat", acts) == [1, 2]
    assert R.applicable_ks("wrong_field", acts) == [] and R.applicable_ks("wrong_option", acts) == []
    assert R.applicable_ks("scroll_away", acts) == [0, 1, 2]


def test_wrong_tab_then_resume():
    m = R.PlanModel(ORACLE, R.Plan(ptype="wrong_tab", k=0, seed=3, label="resume"))
    out = drive(m, [obs(TABS), obs(TABS), obs(TABS2), obs(TABS2)])
    assert out[0] in ("click(1)", "click(3)")                 # a sibling tab, never the oracle's Expenses
    assert out[1:] == ["click(2)", "click(4)", 'done("$1600")']
    assert [e.phase for e in m.emitted] == ["perturb", "resume", "resume", "resume"]


def test_repeat_and_pingpong_build_loops():
    m = R.PlanModel(ORACLE, R.Plan(ptype="repeat", k=1, reps=3, label="resume"))
    out = drive(m, [obs(TABS), obs(TABS2), obs(TABS2), obs(TABS2), obs(TABS2)])
    assert out[:4] == ["click(2)"] * 4 and out[4] == "click(4)"          # oracle + 3 repeats, then break out
    assert m.n_perturb == 3
    m = R.PlanModel(ORACLE, R.Plan(ptype="pingpong", k=1, reps=2, seed=0, label="resume"))
    out = drive(m, [obs(TABS), obs(TABS2), obs(TABS), obs(TABS2)])
    assert out[0] == "click(2)" and out[1] in ("click(1)", "click(3)") and out[2] == "click(2)"
    assert out[3] == "click(4)"                                # the loop ends on the right tab; next = different


def test_repeat_not_applicable_when_control_gone():
    m = R.PlanModel(ORACLE, R.Plan(ptype="repeat", k=1, reps=2))
    out = drive(m, [obs(TABS), obs(['button "Approvals"'])])
    assert out[1].startswith("needs_user(") and m.inapplicable


def test_wrong_field_records_undo_and_wrong_option():
    form = ['textbox "First name" (in "Profile") = "Ada"', 'textbox "Last name" (in "Profile") = "Lovelace"',
            'combobox "Country" (in "Profile") options=India|France|Japan', 'button "Save" (in "Profile")']
    steps = [{"op": "fill", "role": "textbox", "name": "Last name", "value": "Byron"},
             {"op": "select", "role": "combobox", "name": "Country", "option": "France"},
             {"op": "click", "role": "button", "name": "Save"}, {"op": "done", "text": "saved"}]
    m = R.PlanModel(steps, R.Plan(ptype="wrong_field", k=0, recovery=[{"op": "__undo"}], label="undo"))
    out = drive(m, [obs(form)] * 4)
    assert out[:3] == ['fill(1, "Byron")', 'fill(1, "Ada")', 'fill(2, "Byron")']   # mistake, restore, right field
    assert [e.phase for e in m.emitted[:3]] == ["perturb", "recover", "resume"]
    m = R.PlanModel(steps, R.Plan(ptype="wrong_option", k=1, seed=1))
    out = drive(m, [obs(form)] * 3)
    assert out[1] in ('select(3, "India")', 'select(3, "Japan")') and out[2] == 'select(3, "France")'


def test_scroll_away_scrolls_back_up():
    many = [f'link "Item {i}"' for i in range(1, 41)]
    steps = [{"op": "click", "role": "link", "name": "Item 3"}, {"op": "done", "text": "ok"}]
    m = R.PlanModel(steps, R.Plan(ptype="scroll_away", k=0))
    later = obs([f'link "Item {i}"' for i in range(41, 51)], total=50, a=41)
    out = drive(m, [obs(many, total=50), later, obs(many, total=50)])
    assert out == ["scroll(down)", "scroll(up)", "click(3)"]
    assert m.emitted[1].auto and m.emitted[1].phase == "resume"


def test_nav_back_and_seek():
    m = R.PlanModel([], R.Plan())
    m.info.update(prev_title="Invoices page 1 - Team Handbook", prev_heading="Invoices")
    text, _ = m._resolve("recover", {"op": "__nav_back"}, obs(['link "Overview"', 'link "Invoices"']))
    assert text == "click(2)"
    seek = [{"op": "seek", "role": "link", "name": "dignissim", "via": "tab"}, {"op": "done", "text": "x"}]
    m = R.PlanModel(seek)
    closed = obs(['tab "Section #1"', 'tab "Section #2"', 'tab "Section #3"'])
    s1 = obs(['tab "Section #1"', 'link "quisque."', 'tab "Section #2"', 'tab "Section #3"'])
    s2 = obs(['tab "Section #1"', 'tab "Section #2"', 'link "dignissim"', 'tab "Section #3"'])
    assert drive(m, [closed, s1, s2, s2]) == ["click(1)", "click(3)", "click(3)", 'done("x")']


def test_candidates_shortest_first():
    m = R.PlanModel(ORACLE)
    drive(m, [obs(TABS), obs(TABS2), obs(TABS2)])
    acts = R.clean_actions(m)
    labels = [lab for lab, _ in R.candidates("wrong_click", 2, acts)]
    assert labels[0] == "resume" and "nav_back" in labels and len(labels) <= R.MAX_CANDIDATES
    assert [lab for lab, _ in R.candidates("repeat", 1, acts)] == ["resume"]
    assert [lab for lab, _ in R.candidates("pingpong", 1, acts)] == ["resume"]
    assert R.candidates("wrong_field", 0, acts)[0][0] == "undo"


# --------------------------------------------------------------- extraction --

def _task(**kw):
    d = dict(id="factory/docs_hub/policy_answer/003", source="factory", family="docs_hub", split="train",
             goal="g", kind="answer")
    d.update(kw)
    return TaskSpec(**d)


def _traj(model, prompts):
    steps = [Step(i=i + 1, system="SYS", user=u, grammar="root ::= x", reply=e.text, action=e.text)
             for i, (e, u) in enumerate(zip(model.emitted, prompts))]
    return Trajectory(task_id="t", model=R.MODEL_LABEL, steps=steps, outcome="verified")


def _run(plan, prompts, oracle=ORACLE):
    m = R.PlanModel(oracle, plan)
    drive(m, prompts)
    return m, _traj(m, prompts)


def test_extract_rows_schema_and_meta():
    prompts = [obs(TABS), obs(TABS2), obs(TABS2), obs(TABS2), obs(TABS2, text="Approval above: $1600")]
    m, traj = _run(R.Plan(ptype="repeat", k=1, reps=3, label="resume"), prompts)
    att = R.extract_rows(_task(), traj, Score(success=True), m)
    assert att.ok, att.why
    (row,) = att.rows                                         # the break-out step only (no prefix, no mistake)
    j = row.to_json()
    assert set(j) == {"messages", "prompt", "completion", "meta"}
    assert j["prompt"] == render_prompt("SYS", prompts[4]) and j["completion"] == render_completion("click(4)")
    assert [x["role"] for x in j["messages"]] == ["system", "user", "assistant"]
    meta = j["meta"]
    assert meta["kind"] == "recovery" and meta["perturbation"] == "repeat" and meta["stop_loop"] is True
    assert meta["task_kind"] == "answer" and meta["split"] == "train" and meta["perturb_actions"] == ["click(2)"] * 3
    R.assert_train_rows([j])
    tmpl = R.extract_rows(_task(), traj, Score(success=True), m, reason="template").rows[0]
    assert tmpl.reply == R.REASONS["repeat"] + "\nclick(4)" and tmpl.meta["reason_line"]


def test_extract_rows_drops():
    prompts = [obs(TABS), obs(TABS2), obs(TABS2), obs(TABS2), obs(TABS2)]
    plan = R.Plan(ptype="repeat", k=2, reps=2, label="resume")       # repeat Approvals, then done
    m, traj = _run(plan, prompts)
    assert R.extract_rows(_task(), traj, Score(success=True), m).why == "answer_not_on_page"
    assert R.extract_rows(_task(kind="act"), traj, Score(success=True), m).ok
    assert R.extract_rows(_task(kind="act"), traj, Score(success=False), m).why == "not_success"
    assert R.extract_rows(_task(kind="act"), traj, Score(success=True, unsafe=["side_effect"]), m).why == "unsafe"
    traj.steps[-1].valid = False
    assert R.extract_rows(_task(kind="act"), traj, Score(success=True), m).why == "row_step_rejected"
    # a recovery whose first move is the loop's own action would teach the loop
    m, traj = _run(R.Plan(ptype="repeat", k=1, reps=2, recovery=[dict(ORACLE[0])], label="replay1"),
                   [obs(TABS), obs(TABS2), obs(TABS2), obs(TABS2), obs(TABS2)])
    assert R.extract_rows(_task(kind="act"), traj, Score(success=True), m).why == "recovery_repeats_mistake"


# ---------------------------------------------------------- exam integrity --

def test_check_train_task_refuses_exam_tasks():
    R.check_train_task(_task())
    with pytest.raises(EvalSplitError):
        R.check_train_task(_task(split="ood"))
    for fam in R._factory_ood_families():
        with pytest.raises(EvalSplitError):
            R.check_train_task(_task(family=fam))
    from scaffold.rl.miniwob import catalog
    held = catalog.frozen_holdout()
    assert "click-tab-2" in held and "click-tab" in held
    for typ in held:
        with pytest.raises(EvalSplitError):           # even if someone relabels the split as train
            R.check_train_task(TaskSpec(id=f"miniwob/{typ}/s1000", source="miniwob", family=catalog.INCLUDED[typ].family,
                                        split="train", goal="g", meta={"type": typ, "seed": 1000}))
    ok = TaskSpec(id="miniwob/click-collapsible-2/s1000", source="miniwob", family="click-collapsible", split="train",
                  goal="g", meta={"type": "click-collapsible-2", "seed": 1000})
    R.check_train_task(ok)
    with pytest.raises(EvalSplitError):
        R.check_train_task(TaskSpec(**{**ok.to_dict(), "meta": {"type": "click-collapsible-2", "seed": 3}}))
    with pytest.raises(EvalSplitError):
        R.check_train_task(_task(source="webshop"))


def test_miniwob_types_and_extra_oracles_are_train_only():
    from scaffold.rl.miniwob import catalog
    held = set(catalog.frozen_holdout())
    held_fams = {catalog.INCLUDED[t].family for t in held}
    types = R.miniwob_train_types()
    assert types and not (set(types) & held) and not ({catalog.INCLUDED[t].family for t in types} & held_fams)
    assert "click-collapsible-2" in types
    for t in R.EXTRA_MINIWOB_ORACLES:
        assert t in catalog.INCLUDED and t not in held and catalog.INCLUDED[t].family not in held_fams
    steps = R.EXTRA_MINIWOB_ORACLES["click-collapsible-2"]('Expand the sections below, to find and click on the link "sed".')
    assert steps[0] == {"op": "seek", "role": "link", "name": "sed", "via": "tab"}
    assert R.EXTRA_MINIWOB_ORACLES["enter-text-2"](
        'Type "Ada" in all upper case letters in the text input and press Submit.')[0]["value"] == "ADA"


def test_assert_train_rows_refuses_holdout_and_ood():
    base = {"split": "train", "kind": "recovery", "task_source": "miniwob", "family": "click-tab",
            "task_id": "miniwob/click-tab-2/s1000"}
    with pytest.raises(EvalSplitError):
        R.assert_train_rows([{"meta": base}])
    fam = sorted(R._factory_ood_families())[0]
    with pytest.raises(EvalSplitError):
        R.assert_train_rows([{"meta": {**base, "task_source": "factory", "family": fam, "task_id": f"factory/{fam}/x/000"}}])
    with pytest.raises(EvalSplitError):
        R.assert_train_rows([{"meta": {**base, "task_source": "factory", "family": "docs_hub", "split": "ood"}}])


def test_cli_refuses_eval_seeds_and_dry_run(tmp_path, capsys):
    assert R.main(["--out", str(tmp_path / "r.jsonl"), "--miniwob-seeds", "0-9"]) == 2
    assert R.main(["--out", str(tmp_path / "r.jsonl"), "--sources", "factory", "--dry-run", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "[factory] 5 train tasks" in out and not (tmp_path / "r.jsonl").exists()


def test_unlabeled_field_fallback():
    steps = [{"op": "fill", "role": "textbox", "name": "", "value": "Tula"}, {"op": "click", "role": "button", "name": "Submit"}]
    u = obs(['textbox "field after into the text field and press Submit. (unlabeled)"', 'button "Submit"'])
    assert drive(R.PlanModel(steps), [u, u]) == ['fill(1, "Tula")', "click(2)"]


def test_round_robin_covers_families():
    ts = [_task(id=f"factory/{f}/t/{i:03d}", family=f) for f in ("a", "b", "c") for i in range(3)]
    assert [t.family for t in R.round_robin(ts, 4)] == ["a", "b", "c", "a"]


# --------------------------------------------------------- real harness --

def _chromium_or_skip():
    pytest.importorskip("playwright")
    from scaffold.rl.harness.chromium import Chromium, find_chromium
    if not find_chromium():
        pytest.skip("no cached Chromium build (set AWOS_RL_CHROMIUM)")
    return Chromium


@pytest.mark.timeout(240)
def test_generate_on_real_harness_factory_tab_task():
    Chromium = _chromium_or_skip()
    from scaffold.rl.factory import FactorySource
    src = FactorySource()
    src.start()
    ch = Chromium()
    try:
        task = next(t for t in src.tasks("train") if t.id == "factory/docs_hub/policy_answer/003")
        rows, st = R.generate_task(src, task, ch, types=("wrong_tab", "repeat", "pingpong", "scroll_away"))
    finally:
        ch.close()
        src.stop()
    assert st["task:used"] == 1 and rows, dict(st)
    kinds = {r.meta["perturbation"] for r in rows}
    assert {"wrong_tab", "pingpong"} <= kinds, dict(st)
    for r in rows:
        j = r.to_json()
        assert j["meta"]["kind"] == "recovery" and j["prompt"].startswith("<|im_start|>system")
        assert "Your previous actions and results:" in r.user                # the mistake is in the history
        assert not any(r.reply == a for a in r.meta["perturb_actions"]) or r.meta["recovery"] == "undo"
    R.assert_train_rows([r.to_json() for r in rows])
    json.dumps([r.to_json() for r in rows])

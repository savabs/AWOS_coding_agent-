"""MacArena harness (scripts/macarena): evaluator semantics, sharding, guards, agent hooks.
No MacArena data needed: synthetic tasks only."""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "scripts" / "macarena"
sys.path.insert(0, str(HERE))

import ma_agents as A        # noqa: E402
import ma_inventory as INV   # noqa: E402
import run_tasks as R     # noqa: E402
import ma_tasks as T         # noqa: E402


def fake_runner(results):
    calls = []

    def run(cmd, timeout):
        calls.append(cmd)
        return results[len(calls) - 1]
    run.calls = calls
    return run


def test_evaluate_first_true_100_check_wins():
    run = fake_runner([(0, "False\n", ""), (0, "True\n", "")])
    ev = T.evaluate([["a", 100], ["b", 100], ["c", 50]], runner=run, home="/Users/runner")
    assert ev["score"] == 1.0 and run.calls == ["a", "b"] and ev["partial_diag"] == 100


def test_evaluate_partial_checks_are_diagnostic_only():
    run = fake_runner([(0, "False", ""), (0, "True", "")])
    ev = T.evaluate([["a", 100], ["b", 50]], runner=run, home="/Users/runner")
    assert ev["score"] == 0.0 and ev["partial_diag"] == 50


def test_evaluate_nonzero_exit_stops_with_error():
    run = fake_runner([(1, "boom", ""), (0, "True", "")])
    ev = T.evaluate([["a", 100], ["b", 100]], runner=run, home="/Users/runner", diagnostics=False)
    assert ev["score"] == 0.0 and ev["error"] and run.calls == ["a"]


def test_evaluate_true_substring_case_insensitive_like_macarena():
    # osascript prints "true" for a boolean AppleScript result
    ev = T.evaluate([["x", 100]], runner=fake_runner([(0, "true\n", "")]), home="/Users/runner")
    assert ev["score"] == 1.0


def test_evaluate_osworld_dict_unsupported():
    assert T.evaluate({"func": "x"})["score"] is None


def test_run_bash_check_real_bash():
    rc, out, err = T.run_bash_check('echo "True"', timeout=10)
    assert rc == 0 and "True" in out and not err


def test_rewrite_home():
    assert T.rewrite_home("cp /Users/admin/Desktop/a ~/b", "/Users/runner") == "cp /Users/runner/Desktop/a ~/b"
    assert T.rewrite_home("ls /Users/administrator", "/Users/runner") == "ls /Users/administrator"
    assert T.rewrite_home("open /Users/admin", "/Users/runner") == "open /Users/runner"


def test_select_shard_round_robin():
    ids = [str(i) for i in range(5)]
    assert R.select_shard(ids, "0/2") == ["0", "2", "4"]
    assert R.select_shard(ids, "1/2") == ["1", "3"]
    with pytest.raises(ValueError):
        R.select_shard(ids, "2/2")


def test_guard_refuses_bare_metal(monkeypatch):
    monkeypatch.setattr(R.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(R, "in_vm", lambda: False)
    assert "bare-metal" in R.guard()
    monkeypatch.setattr(R, "in_vm", lambda: True)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("AWOS_MACARENA_ALLOW_GUEST", raising=False)
    assert R.guard()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert R.guard() is None


def test_load_task_and_inventory(tmp_path):
    d = tmp_path / "macarena" / "system_apps"
    d.mkdir(parents=True)
    task = {"id": "t1", "instruction": "x", "pre_command": "open -a Calculator",
            "evaluator": [["osascript -e 'tell application \"Notes\" to count notes'", 100]],
            "related_apps": ["TextEdit"], "before_action_delay_seconds": 2, "before_grading_delay_seconds": 5}
    (d / "t1.json").write_text(json.dumps(task))
    t = T.load_task(tmp_path, "t1")
    assert t.family == "macarena" and t.list_evaluator and t.before_grading_delay == 5
    assert {"TextEdit", "Calculator", "Notes"} <= INV.apps_of(t.raw)
    row = INV.classify(dict(task, _family="macarena", _category="system_apps", _path="x"))
    assert row["feasibility"] == "maybe" and row["flags"]["appleevents_private_data"]
    keynote = dict(task, related_apps=["Keynote"], _family="macarena", _category="c", _path="x")
    assert INV.classify(keynote)["feasibility"] == "infeasible"


def test_noop_and_oracle_agents(tmp_path):
    ctx = A.Ctx(out_dir=tmp_path, deadline=0)
    t = T.Task("i", "macarena", "c", "do x", "", [], [], 0, 0, raw={})
    assert A.NoopAgent().run(t, ctx)["status"] == "noop"
    assert A.ScriptedOracleAgent().run(t, ctx)["status"] == "skipped_no_reference"
    assert A.reference_solution(T.Task("i", "f", "c", "", "", [], [], 0, 0, raw={"solution": ["true"]})) == ["true"]


def test_parse_action_verbs():
    assert A.parse_action("press(3)") == {"verb": "press", "n": 3}
    assert A.parse_action('type(2, "a \\"b\\"")')["text"] == 'a "b"'
    assert A.parse_action('menu("File", "New Window")')["path"] == ["File", "New Window"]
    assert A.parse_action('open_app("Calculator")')["name"] == "Calculator"
    assert A.parse_action("scroll(down)")["direction"] == "down"
    assert A.parse_action("done()") == {"verb": "done"}
    assert A.parse_action("rm -rf /") is None
    assert "root ::=" in A.build_grammar(10)


def test_model_selection_has_no_cloud_path(monkeypatch):
    monkeypatch.setenv("AWOS_MACARENA_MODEL", "anthropic")
    with pytest.raises(ValueError):
        A.make_model()


def test_scripted_model(tmp_path, monkeypatch):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(["press(1)"]))
    monkeypatch.setenv("AWOS_MACARENA_MODEL", f"scripted:{f}")
    m = A.make_model()
    assert m.decide("", "") == "press(1)" and m.decide("", "") == "done()"


def test_pilot_list_is_ids_only():
    pilot = json.loads((HERE / "pilot.json").read_text())
    assert 10 <= len(pilot["tasks"]) <= 20
    for t in pilot["tasks"]:
        assert set(t) <= {"id", "family", "app", "check"}

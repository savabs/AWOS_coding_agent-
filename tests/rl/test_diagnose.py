"""Offline tests for scripts/rl/diagnose_failures.py (no network, fake batch client)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("diagnose_failures", ROOT / "scripts" / "rl" / "diagnose_failures.py")
D = importlib.util.module_from_spec(_spec)
sys.modules["diagnose_failures"] = D
_spec.loader.exec_module(D)

AGENT_SYSTEM = "You operate a web dashboard for the owner, one action per reply, through numbered controls."
LONG_TEXT = "\n".join(f"Paragraph line {i} " + "lorem ipsum " * 8 for i in range(40))


def user_prompt(goal: str, controls: list, text: str = "Welcome", step: int = 1) -> str:
    ctl = "\n".join(f'[{i + 1}] {c}' for i, c in enumerate(controls))
    return (f"Goal: {goal}\nStep {step} of 12.\n"
            f'Current page: "Home - App" (path /t/i1/home); main heading "Home"; form to fill: "Details".\n'
            "Your previous actions and results:\n1. click(1) -> ok\n"
            '<observation untrusted="true">\nURL path: /t/i1/home  (HTTP 200)\nTitle: Home - App\n'
            "Regions: Main: " + ("region words " * 60) + "\n"
            f"Page text:\n{text}\nControls ({len(controls)} total, showing 1-{len(controls)}):\n{ctl}\n"
            "</observation>\nReply with ONE action line, e.g. click(3).")


def rec(task_id: str, *, model="qwen3.5-4b", source="factory", split="ood", family="wizard", success=False,
        unsafe=None, actions=("click(1)", "done(\"ok\")"), error="", stop_by="model", reason="why",
        text="Welcome", sample=0, outcome="needs_user"):
    goal = f"Do the thing for {task_id}"
    steps = []
    for i, a in enumerate(actions, 1):
        steps.append({"i": i, "system": AGENT_SYSTEM, "grammar": None, "reply": a, "action": a, "valid": True,
                      "url": "http://127.0.0.1:1/t/i1/home", "ms": 10.0,
                      "user": user_prompt(goal, ['button "Start"', 'textbox "Email"'], text, i)})
    events = [{"step": i, "action": a, "control": 'button "Start"'} for i, a in enumerate(actions, 1)]
    events.append({"event": "harness", "stop_by": stop_by, "destructive_attempts": [], "model_needs_user": False})
    return {"task": {"id": task_id, "source": source, "family": family, "split": split, "goal": goal,
                     "kind": "act", "tags": ["aria_poor"], "meta": {"template": "t"}, "secrets_in": [],
                     "secrets_out": []},
            "sample": sample,
            "trajectory": {"task_id": task_id, "model": model, "steps": steps, "outcome": outcome, "reason": reason,
                           "events": events, "flags": [], "seconds": 1.0, "error": error},
            "score": {"success": success, "unsafe": unsafe or [], "notes": "state differs", "reward": 0.0}}


def write_run(base: Path, run_id: str, artifact: str, rows: list) -> None:
    d = base / run_id / artifact / "run"
    d.mkdir(parents=True, exist_ok=True)
    (d / "trajectories.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def arts(tmp_path):
    base = tmp_path / "artifacts"
    write_run(base, "39817774382", "rl-qwen3.5-4b-factory-ood-s1of1", [
        rec("factory/wizard/a/000"),                                         # failure
        rec("factory/wizard/a/000", sample=1),                                # same task + same actions -> dupe
        rec("factory/wizard/a/001", success=True),                            # pass -> excluded
        rec("factory/wizard/a/002", success=True, unsafe=["trap_clicked"]),   # unsafe -> failure
        rec("factory/wizard/a/003", error="BrowserError: boom"),              # infra -> excluded
        rec("factory/wizard/a/004", stop_by="model_error", reason="model call failed: Timeout"),  # infra
        rec("factory/wizard/a/005", actions=("click(2)",), text=LONG_TEXT),  # failure, long page text
    ])
    write_run(base, "39817774382", "rl-qwen3.5-2b-miniwob-holdout-s1of1", [
        rec("miniwob/click-tab/s1", model="qwen3.5-2b", source="miniwob", split="holdout", family="click-tab"),
        rec("miniwob/click-tab/s2", model="qwen3.5-2b", source="miniwob", split="holdout", family="click-tab"),
    ])
    write_run(base, "39864014413", "rl-qwen3.5-4b-miniwob-train-s1of1", [
        rec("miniwob/click-tab/s1", source="miniwob", split="train", family="click-tab"),
    ])
    return base


# ------------------------------------------------------------- selection --

def test_select_filters_dedupes_and_prioritises(arts, tmp_path):
    out = tmp_path / "diag"
    st = D.select([arts], out, log=lambda *a: None)
    rows = D.read_jsonl(out / "failures.jsonl")
    ids = [(r["task_id"], r["model"]) for r in rows]
    assert st["records"] == 10
    assert st["infra_excluded"] == 2
    assert st["failed"] == 9
    assert st["duplicates_merged"] == 2          # same task+actions: sample 0/1, and 2b vs 4b on click-tab/s1
    assert st["selected"] == 5
    assert ("factory/wizard/a/001", "qwen3.5-4b") not in ids
    dup = next(r for r in rows if r["task_id"] == "factory/wizard/a/000")
    assert len(dup["covers"]) == 2 and {c["sample"] for c in dup["covers"]} == {0, 1}
    cross = next(r for r in rows if r["task_id"] == "miniwob/click-tab/s1")
    assert cross["model"] == "qwen3.5-4b" and {c["model"] for c in cross["covers"]} == {"qwen3.5-2b", "qwen3.5-4b"}
    # qwen3.5-4b eval first, then qwen3.5-4b train, then other models
    assert rows[0]["model"] == "qwen3.5-4b" and rows[0]["split"] == "ood"
    first_other = next(i for i, r in enumerate(rows) if r["model"] != "qwen3.5-4b")
    assert all(r["model"] == "qwen3.5-4b" for r in rows[:first_other])
    assert any(r["split"] == "train" for r in rows[:first_other])
    assert {r["run"] for r in rows} == {"39817774382", "39864014413"}
    assert st["agent_system_prompt"] == AGENT_SYSTEM
    assert all(len(r["custom_id"]) <= 64 and r["custom_id"].replace("-", "").isalnum() for r in rows)
    assert len({r["custom_id"] for r in rows}) == len(rows)


def test_select_caps(arts, tmp_path):
    st = D.select([arts], tmp_path / "a", max_episodes=2, log=lambda *a: None)
    assert st["selected"] == 2 and st["skipped_max_episodes"] == 3
    st = D.select([arts], tmp_path / "b", max_per_cluster=1, log=lambda *a: None)
    # clusters = (model, source, family, reason): 4b/factory/wizard, 4b/miniwob, 2b/miniwob
    assert st["selected"] == 3 and st["skipped_cluster_cap"] == 2


def test_select_budget_and_refusal(arts, tmp_path):
    st = D.select([arts], tmp_path / "a", budget_usd=0.015, log=lambda *a: None)
    assert 0 < st["selected"] < 5 and st["skipped_budget"] > 0
    assert st["est_cost_usd"] <= 0.015
    with pytest.raises(SystemExit) as e:
        D.select([arts], tmp_path / "b", budget_usd=100, max_usd=0.001, log=lambda *a: None)
    assert e.value.code == 2


def test_latest_row_wins_for_resumed_runs(tmp_path):
    base = tmp_path / "art"
    write_run(base, "123456789", "rl-x", [rec("factory/wizard/a/000"), rec("factory/wizard/a/000", success=True)])
    st = D.select([base], tmp_path / "o", log=lambda *a: None)
    assert st["records"] == 1 and st["selected"] == 0


# ------------------------------------------------------------- rendering --

def test_render_trims_page_text_keeps_controls():
    r = rec("factory/wizard/a/005", actions=("click(2)", "click(1)"), text=LONG_TEXT)
    p = D.render_episode(r, "1", AGENT_SYSTEM)
    assert "Goal: Do the thing" in p and p.count("Goal:") == 1
    assert "page text trimmed" in p
    assert len(LONG_TEXT) > 2000 and len(p) < len(LONG_TEXT) + 1500
    assert '[1] button "Start"' in p and '[2] textbox "Email"' in p
    assert "Reply with ONE action line" not in p and "Your previous actions" not in p
    assert "(page purpose and page text unchanged" in p and "(control list unchanged)" in p
    assert ">> model reply: 'click(2)'" in p and "valid: True" in p
    assert "outcome: needs_user" in p and "verifier: success=False" in p
    assert "family: wizard" in p and "tags: aria_poor" in p
    assert "different agent system prompt" not in p


def test_trim_observation_regions_and_controls_cap():
    ctl = [f'button "B{i}"' for i in range(100)]
    o = D.trim_observation(user_prompt("g", ctl, "short"))
    assert o.startswith("Current page:")
    regions = next(ln for ln in o.splitlines() if ln.startswith("Regions:"))
    assert len(regions) <= D.REGIONS_CHARS + 1
    assert "[60] button" in o and "[61] button" not in o and "40 more control lines trimmed" in o


def test_render_changed_controls_only():
    r = rec("factory/wizard/a/000", actions=("fill(2, \"a@b.c\")", "click(1)"))
    r["trajectory"]["steps"][1]["user"] = r["trajectory"]["steps"][1]["user"].replace(
        '[2] textbox "Email"', '[2] textbox "Email" = "a@b.c"')
    p = D.render_episode(r, "1", AGENT_SYSTEM)
    assert "changed lines:" in p and '[2] textbox "Email" = "a@b.c"' in p


# --------------------------------------------------------- cost / params --

def test_cost_math():
    assert D.est_tokens("x" * 35) == 10
    assert D.est_tokens("x" * 36) == 11
    assert D.cost_usd(1_000_000, 0) == pytest.approx(1.0)        # batch input $1/MTok
    assert D.cost_usd(0, 1_000_000) == pytest.approx(5.0)        # batch output $5/MTok
    assert D.cost_usd(0, 0, 1_000_000, 1_000_000) == pytest.approx(1.35)


def test_estimate_matches_formula(arts, tmp_path):
    out = tmp_path / "d"
    st = D.select([arts], out, log=lambda *a: None)
    sys_t = D.est_tokens(D.system_text(AGENT_SYSTEM))
    rows = D.read_jsonl(out / "failures.jsonl")
    want = sum(D.cost_usd(sys_t + D.est_tokens(r["prompt"]), D.MODES["fast"]["est_out"]) * D.EST_MARGIN for r in rows)
    assert st["est_cost_usd"] == pytest.approx(want, abs=1e-4)
    assert st["est_input_tokens"] == sum(r["est_input_tokens"] for r in rows)


def test_params_shape():
    p = D.build_params("hello", AGENT_SYSTEM, "fast")
    assert p["model"] == "claude-sonnet-5-5"
    assert p["thinking"] == {"type": "between_tools"}
    assert p["output_config"]["format"]["type"] == "json_schema"
    assert p["output_config"]["format"]["schema"] is D.VERDICT_SCHEMA
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in p
    assert AGENT_SYSTEM in p["system"][0]["text"] and "{agent_system}" not in p["system"][0]["text"]
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    t = D.build_params("hello", AGENT_SYSTEM, "think")
    assert "thinking" not in t and t["output_config"]["effort"] == "low"


def test_schema_valid():
    s = D.VERDICT_SCHEMA
    assert s["additionalProperties"] is False
    assert set(s["required"]) == set(s["properties"])
    assert s["properties"]["root_cause"]["enum"] == D.ROOT_CAUSES and len(D.ROOT_CAUSES) == 13
    assert s["properties"]["fix_owner"]["enum"] == D.FIX_OWNERS and len(D.FIX_OWNERS) == 6
    assert s["properties"]["first_bad_step"]["type"] == "integer"

    def walk(o):                     # constraints structured outputs does not support
        if isinstance(o, dict):
            for k in ("minLength", "maxLength", "minimum", "maximum", "multipleOf"):
                assert k not in o
            for v in o.values():
                walk(v)
    walk(s)
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(s)
    jsonschema.validate({"root_cause": "other", "first_bad_step": 1, "explanation": "x", "suggested_fix": "y",
                         "fix_owner": "harness"}, s)


def test_validate_verdict():
    good = {"root_cause": "model_wrong_control", "first_bad_step": 2, "explanation": "e" * 500,
            "suggested_fix": "f" * 500, "fix_owner": "model_training"}
    v, err = D.validate_verdict(good)
    assert not err and len(v["explanation"]) == 300 and len(v["suggested_fix"]) == 200
    assert D.validate_verdict({**good, "root_cause": "nope"})[0] is None
    assert D.validate_verdict({**good, "fix_owner": "nope"})[0] is None
    assert D.validate_verdict({"root_cause": "other"})[0] is None


# ------------------------------------------------- fake batch round trip --

def _msg(text, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, stop_details=None,
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=100,
                                                 cache_creation_input_tokens=2000, cache_read_input_tokens=0))


class FakeBatches:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.created = None
        self.polls = 0
        self.canceled = False

    def create(self, requests):
        self.created = list(requests)
        return SimpleNamespace(id="msgbatch_fake", processing_status="in_progress")

    def retrieve(self, bid):
        self.polls += 1
        status = "ended" if self.polls >= 2 else "in_progress"
        return SimpleNamespace(id=bid, processing_status=status, request_counts=SimpleNamespace(
            processing=0, succeeded=1, errored=0, expired=0, canceled=0))

    def cancel(self, bid):
        self.canceled = True

    def results(self, bid):
        reqs = self.created
        out = []
        for i, r in enumerate(reqs):
            cid = r["custom_id"]
            if i == len(reqs) - 1:
                out.append(SimpleNamespace(custom_id=cid, result=SimpleNamespace(
                    type="errored", error=SimpleNamespace(type="error", error=SimpleNamespace(
                        type="overloaded_error", message="busy")))))
            elif i == len(reqs) - 2:
                out.append(SimpleNamespace(custom_id=cid, result=SimpleNamespace(type="expired")))
            else:
                out.append(SimpleNamespace(custom_id=cid, result=SimpleNamespace(
                    type="succeeded", message=_msg(json.dumps(self.verdicts[i % len(self.verdicts)])))))
        return reversed(out)                              # results arrive in any order


def test_submit_wait_collect_report(arts, tmp_path):
    out = tmp_path / "d"
    D.select([arts], out, log=lambda *a: None)
    vs = [{"root_cause": "product_observation_missing_or_wrong", "first_bad_step": 1,
           "explanation": "Icon-only Start button had no name.", "suggested_fix":
           "Give icon-only buttons an accessible name from the title attribute in the AX renderer",
           "fix_owner": "product_observation"},
          {"root_cause": "model_gave_up_early", "first_bad_step": 2, "explanation": "Asked the owner needlessly.",
           "suggested_fix": "Add SFT examples that press Next after filling the last field", "fix_owner": "model_training"},
          {"root_cause": "product_observation_missing_or_wrong", "first_bad_step": 0,
           "explanation": "Unnamed buttons.", "suggested_fix":
           "Accessible name for icon-only buttons from title attribute", "fix_owner": "product_observation"}]
    fb = FakeBatches(vs)
    client = SimpleNamespace(messages=SimpleNamespace(batches=fb))
    info = D.submit(out, client=client, log=lambda *a: None)
    assert info["id"] == "msgbatch_fake" and info["requests"] == 5
    assert all(r["params"]["model"] == "claude-sonnet-5-5" for r in fb.created)
    # second submit is a no-op (no double spend)
    assert D.submit(out, client=SimpleNamespace(), log=lambda *a: None)["id"] == "msgbatch_fake"
    assert D.wait(out, client=client, interval=0, sleep=lambda s: None, log=lambda *a: None) == "ended"
    st = D.collect(out, client=client, log=lambda *a: None)
    assert st == {"succeeded": 3, "expired": 1, "errored": 1}
    diags = {d["custom_id"]: d for d in D.read_jsonl(out / "diagnoses.jsonl")}
    assert set(diags) == {r["custom_id"] for r in fb.created}
    err = next(d for d in diags.values() if d["status"] == "errored")
    assert "overloaded_error" in err["error"]
    text = D.report(out, log=lambda *a: None)
    assert (out / "report.md").read_text() == text
    assert "product_observation_missing_or_wrong" in text and "model_gave_up_early" in text
    assert "### root_cause x model" in text and "qwen3.5-4b" in text
    assert "### root_cause x source/split" in text and "### root_cause x family" in text
    assert "## By fix owner" in text and "product_observation" in text
    assert "## Top suggested fixes" in text
    assert "## Examples per top cause" in text and "factory/wizard/a/000" in text
    # 3 successes x (1000 in + 100 out + 2000 cache write) at batch prices
    want = D.cost_usd(3000, 300, 6000, 0)
    assert f"${want:.2f}" in text
    assert "Requests without a verdict" in text


def test_wait_timeout_cancels(arts, tmp_path):
    out = tmp_path / "d"
    out.mkdir()
    (out / "batch.json").write_text(json.dumps({"id": "b1"}))

    class Never(FakeBatches):
        def retrieve(self, bid):
            return SimpleNamespace(processing_status="canceling" if self.canceled else "in_progress",
                                   request_counts=SimpleNamespace(processing=1, succeeded=0, errored=0, expired=0,
                                                                  canceled=0))
    fb = Never([])
    t = [0.0]

    def sleep(s):
        t[0] += 600
    st = D.wait(out, client=SimpleNamespace(messages=SimpleNamespace(batches=fb)), timeout=1200, interval=600,
                sleep=sleep, clock=lambda: t[0], log=lambda *a: None)
    assert fb.canceled and st == "canceling"


def test_parse_result_refusal_and_bad_json():
    r = D.parse_result({"custom_id": "e1", "result": {"type": "succeeded", "message": {
        "content": [], "stop_reason": "refusal", "stop_details": {"category": "cyber"},
        "usage": {"input_tokens": 5, "output_tokens": 0}}}})
    assert r["status"] == "refusal" and r["error"] == "cyber" and r["usage"]["input_tokens"] == 5
    r = D.parse_result({"custom_id": "e2", "result": {"type": "succeeded", "message": {
        "content": [{"type": "text", "text": "{not json"}], "stop_reason": "max_tokens", "usage": {}}}})
    assert r["status"] == "parse_error" and "max_tokens" in r["error"]


def test_report_aggregation_from_fake_files(tmp_path):
    out = tmp_path / "r"
    out.mkdir()
    fails, diags = [], []
    causes = ["model_wrong_control"] * 6 + ["product_guard_blocked_valid_action"] * 3 + ["task_or_verifier_bug"]
    for i, c in enumerate(causes):
        cid = f"e{i:05d}-x"
        fails.append({"custom_id": cid, "task_id": f"factory/fam{i % 3}/t/{i:03d}", "sample": 0, "run": "1",
                      "model": "qwen3.5-4b" if i % 2 else "qwen3.5-9b", "source": "factory" if i < 7 else "miniwob",
                      "split": "ood", "family": f"fam{i % 3}", "kind": "act", "tags": [], "outcome": "needs_user",
                      "reason": "why", "covers": [{}] * (2 if i == 0 else 1)})
        fix = ("Relax ambiguity guard when twins share a context" if c.startswith("product")
               else "Add SFT examples choosing the labelled Save control")
        diags.append({"custom_id": cid, "status": "succeeded", "error": "",
                      "usage": {"input_tokens": 100, "output_tokens": 10, "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 50},
                      "verdict": {"root_cause": c, "first_bad_step": 1, "explanation": f"exp {i}",
                                  "suggested_fix": fix, "fix_owner": "product_agent" if c.startswith("product")
                                  else "model_training"}})
    D.write_jsonl(out / "failures.jsonl", fails)
    D.write_jsonl(out / "diagnoses.jsonl", diags)
    text = D.report(out, log=lambda *a: None)
    assert "| model_wrong_control | 6 | 7 | 60% |" in text          # weighted by duplicates covered
    assert "| product_guard_blocked_valid_action | 3 | 3 | 30% |" in text
    assert "| model_training | 7 | 70% |" in text
    assert "| product_agent | 3 | 30% |" in text
    fixes = text.split("## Top suggested fixes")[1].split("## Examples")[0]
    rows = [ln for ln in fixes.splitlines() if ln.startswith("| 1 ") or ln.startswith("| 2 ")]
    assert len(rows) == 2 and rows[0].startswith("| 1 | 7 | model_training (7)") and rows[1].startswith("| 2 | 3 |")
    ex = text.split("### model_wrong_control")[1].split("###")[0]
    assert 1 <= ex.count("\n- `") <= 5
    assert f"${D.cost_usd(1000, 100, 0, 500):.2f}" in text


def test_cluster_fixes_groups_similar():
    items = [{"suggested_fix": s, "fix_owner": "product_observation", "weight": 1} for s in (
        "Give icon-only buttons an accessible name from title",
        "Icon-only buttons need accessible name from title attribute",
        "Expose checkbox checked state in the observation")]
    cl = D.cluster_fixes(items)
    assert [len(c["items"]) for c in cl] == [2, 1]

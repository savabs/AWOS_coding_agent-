"""Stage-A SFT data: filtering, eval-split guard, dedupe, task-level val split, and that the
training text equals what LocalGBNFModel sends at inference. Fast, no model weights.

    PYTHONPATH=. scaffold/rl/harness/.venv/bin/python -m pytest tests/rl/test_train_dataset.py
    (with jinja2 installed the Jinja-template equality tests run too; they are skipped otherwise)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scaffold.rl.train import chat, dataset as ds

SYSTEM = "You operate a web dashboard.\nRules:\n- one action per reply\n"
GRAMMAR = 'root ::= "click(" [1-9] ")"'


def user(task_id: str, step: int) -> str:
    return f"Goal: do {task_id}\nStep {step} of 12.\n<observation untrusted=\"true\">\n[1] button \"Go\"\n</observation>\n"


def step(i, task_id, reply="click(1)", valid=True, grammar=GRAMMAR, u=None):
    return {"i": i, "system": SYSTEM, "user": u or user(task_id, i), "grammar": grammar, "reply": reply,
            "action": reply if valid else "", "valid": valid, "url": "http://127.0.0.1:1/x", "ms": 1.0}


def rec(task_id, *, split="train", kind="act", success=True, unsafe=(), outcome="verified", steps=None,
        sample=0, family="fam", error="", source="factory"):
    steps = steps if steps is not None else [step(1, task_id), step(2, task_id, 'done("ok")')]
    return {"task": {"id": task_id, "source": source, "family": family, "split": split, "goal": "g",
                     "kind": kind, "tags": [], "meta": {}},
            "sample": sample,
            "trajectory": {"task_id": task_id, "model": "m", "steps": steps, "outcome": outcome, "reason": "",
                           "events": [], "flags": [], "seconds": 1.0, "error": error},
            "score": {"success": success, "unsafe": list(unsafe), "progress": 1.0, "notes": "",
                      "reward": 1.0 if success and not unsafe else (-1.0 if unsafe else 0.0)}}


def write(tmp: Path, name: str, recs) -> Path:
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "trajectories.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    return d


def build(tmp, recs, **kw):
    src = ds.parse_input(f"a={write(tmp, 'a', recs)}")
    kw.setdefault("val_frac", 0.0)
    return ds.build([src], **kw)


# ------------------------------------------------------------------ filters --

def test_keeps_verified_success_and_drops_failures_and_unsafe(tmp_path):
    recs = [rec("t/ok"), rec("t/fail", success=False), rec("t/unsafe", unsafe=["trap_clicked"]),
            rec("t/both", success=True, unsafe=["side_effect"]), rec("t/infra", error="chromium died")]
    train, valid, st = build(tmp_path, recs)
    assert {e.meta["task_id"] for e in train} == {"t/ok"}
    assert len(train) == 2 and not valid
    dropped = st["sources"]["a"]["episodes_dropped"]
    assert dropped == {"not_success": 1, "unsafe": 2, "infra_error": 1}


@pytest.mark.parametrize("split", ["ood", "holdout", "test", "weird-new-split", None])
def test_eval_or_unknown_split_is_a_hard_error(tmp_path, split):
    recs = [rec("t/ok"), rec("t/eval", split=split, success=False)]   # even a failure must not slip by
    with pytest.raises(ds.EvalSplitError):
        build(tmp_path, recs)


def test_cli_refuses_eval_split(tmp_path):
    d = write(tmp_path, "mixed", [rec("t/ok"), rec("m/hold", split="holdout", source="miniwob")])
    assert ds.main(["--input", f"x={d}", "--out", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out" / "train.jsonl").exists()


def test_invalid_and_grammarless_steps_dropped(tmp_path):
    steps = [step(1, "t/a", "clik(1)", valid=False), step(2, "t/a"), step(3, "t/a", '{"verb":"click"}', grammar=None),
             step(4, "t/a", "   ", valid=True), step(5, "t/a", 'done("ok")')]
    train, _, st = build(tmp_path, [rec("t/a", steps=steps)])
    assert [e.reply for e in train if True] and sorted(e.meta["step"] for e in train) == [2, 5]
    assert st["sources"]["a"]["steps_dropped"] == {"invalid": 1, "no_grammar": 1, "empty_reply": 1}


def test_pause_needs_a_real_stop_and_act_must_not_end_in_needs_user(tmp_path):
    recs = [rec("t/p-ok", kind="pause", outcome="needs_user", steps=[step(1, "t/p-ok", 'needs_user("login")')]),
            rec("t/p-bad", kind="pause", outcome="verified"),
            rec("t/act-nu", kind="act", outcome="needs_user"),
            rec("t/p-guard", kind="pause", outcome="needs_user", steps=[])]   # guard stopped before any call
    train, _, st = build(tmp_path, recs)
    assert {e.meta["task_id"] for e in train} == {"t/p-ok"}
    assert st["sources"]["a"]["episodes_dropped"] == {"pause_not_stopped": 1, "act_ended_needs_user": 1,
                                                      "no_steps": 1}


def test_exact_duplicates_dropped_across_sources(tmp_path):
    a = write(tmp_path, "oracle", [rec("t/a")])
    b = write(tmp_path, "model", [rec("t/a", sample=0), rec("t/a", sample=1,
                                                             steps=[step(1, "t/a"), step(2, "t/a", "click(2)")])])
    train, _, st = ds.build([ds.parse_input(f"oracle={a}"), ds.parse_input(f"model={b}")], val_frac=0.0)
    pairs = [(e.user, e.reply) for e in train]
    assert len(pairs) == len(set(pairs)) == 3          # click(1), done, click(2) on step 2
    assert st["sources"]["model"]["steps_dropped"] == {"duplicate": 3}
    assert st["sources"]["oracle"]["examples_written"] == 2


def test_latest_row_per_task_sample_wins(tmp_path):
    recs = [rec("t/a", error="boom"), rec("t/a")]          # resumed run: retry appended after the error row
    train, _, _ = build(tmp_path, recs)
    assert len(train) == 2


def test_max_per_task_prefers_short_episodes(tmp_path):
    long = [step(i, "t/a", f"click({i})", u=user("t/a", i) + "long") for i in range(1, 6)]
    recs = [rec("t/a", sample=0, steps=long), rec("t/a", sample=1), rec("t/a", sample=2,
                                                                         steps=[step(1, "t/a", "click(3)")])]
    train, _, st = build(tmp_path, recs, max_per_task=2)
    assert st["sources"]["a"]["episodes_dropped"] == {"over_task_cap": 1}
    assert not any(e.meta["sample"] == 0 for e in train)


def test_weights_mix_sources(tmp_path):
    a = write(tmp_path, "a", [rec(f"t/{i}") for i in range(10)])
    b = write(tmp_path, "b", [rec(f"u/{i}") for i in range(10)])
    train, _, st = ds.build([ds.parse_input(f"a={a}:2"), ds.parse_input(f"b={b}:0")], val_frac=0.0)
    assert st["train_by"]["source"] == {"a": 40}
    assert all(e.meta["src"] == "a" for e in train)


def test_validation_split_is_by_task_id(tmp_path):
    recs = [rec(f"t/{i}", sample=j, steps=[step(1, f"t/{i}", f"click({j + 1})"), step(2, f"t/{i}", 'done("x")')])
            for i in range(60) for j in range(3)]
    train, valid, st = build(tmp_path, recs, val_frac=0.25, seed=7)
    tr, va = {e.meta["task_id"] for e in train}, {e.meta["task_id"] for e in valid}
    assert tr and va and not (tr & va)
    assert 5 <= len(va) <= 25
    assert st["valid_tasks"] == len(va)
    again, valid2, _ = build(tmp_path, recs, val_frac=0.25, seed=7)
    assert {e.meta["task_id"] for e in valid2} == va                   # deterministic


def test_output_files_are_mlx_chat_format(tmp_path):
    d = write(tmp_path, "a", [rec(f"t/{i}") for i in range(20)])
    out = tmp_path / "out"
    assert ds.main(["--input", f"oracle={d}", "--out", str(out), "--val-frac", "0.2"]) == 0
    rows = [json.loads(x) for x in (out / "train.jsonl").read_text().splitlines()]
    assert rows and all([m["role"] for m in r["messages"]] == ["system", "user", "assistant"] for r in rows)
    for r in rows:                       # pre-rendered inference text for the trainer
        sys_m, user_m, asst_m = r["messages"]
        assert r["prompt"] == chat.render_prompt(sys_m["content"], user_m["content"])
        assert r["completion"] == chat.render_completion(asst_m["content"])
    assert json.loads((out / "stats.json").read_text())["train_examples"] == len(rows)


def test_input_dir_is_searched_recursively(tmp_path):
    write(tmp_path / "artifact", "shard-1", [rec("t/1")])
    write(tmp_path / "artifact", "shard-2", [rec("t/2")])
    src = ds.parse_input(f"q4b={tmp_path / 'artifact'}:0.5")
    assert len(src.paths) == 2 and src.weight == 0.5


# ------------------------------------------------- inference text equality --

def captured_request(system: str, user_txt: str, grammar: str) -> dict:
    """The JSON body LocalGBNFModel.decide POSTs (urllib intercepted, no server)."""
    import urllib.request
    from scaffold.agent.desktop import micro_agent

    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "click(1)"}}], "usage": {}}).encode()

    class Opener:
        def open(self, req, timeout=None):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data)
            return Resp()

    orig = urllib.request.build_opener
    urllib.request.build_opener = lambda *a, **k: Opener()
    try:
        micro_agent.LocalGBNFModel(base="http://127.0.0.1:8080/v1", model="m").decide(system, user_txt, grammar)
    finally:
        urllib.request.build_opener = orig
    return seen["body"]


def test_training_messages_equal_inference_request():
    body = captured_request(SYSTEM, user("t/a", 1), GRAMMAR)
    assert body["messages"] == chat.messages_for(SYSTEM, user("t/a", 1))
    assert body["chat_template_kwargs"] == chat.CHAT_TEMPLATE_KWARGS == {"enable_thinking": False}
    assert body["grammar"] == GRAMMAR


def test_render_prompt_is_qwen35_non_thinking_chatml():
    p = chat.render_prompt("  sys\n", "\nhello  \n")
    assert p == ("<|im_start|>system\nsys<|im_end|>\n<|im_start|>user\nhello<|im_end|>\n"
                 "<|im_start|>assistant\n<think>\n\n</think>\n\n")
    assert chat.render_completion("# why\nclick(3)\n") == "# why\nclick(3)<|im_end|>"


def test_vendored_template_is_qwen35():
    t = chat.TEMPLATE_PATH.read_text()
    assert "'<think>\\n\\n</think>\\n\\n'" in t and "enable_thinking" in t


def test_render_prompt_equals_jinja_template_with_inference_kwargs():
    pytest.importorskip("jinja2")
    body = captured_request(SYSTEM, user("t/a", 1), GRAMMAR)
    want = chat.render_template(body["messages"], add_generation_prompt=True, **body["chat_template_kwargs"])
    assert chat.render_prompt(SYSTEM, user("t/a", 1)) == want
    # thinking on would differ: proves the kwarg matters and is honoured
    assert chat.render_template(body["messages"], add_generation_prompt=True, enable_thinking=True) != want


def test_loss_target_matches_template_assistant_turn():
    """prompt + completion is a prefix of the template's rendering of the full exchange (which only
    adds the '\\n' after <|im_end|> that generation never produces)."""
    pytest.importorskip("jinja2")
    reply = "# open the lead\nclick(4)"
    msgs = chat.messages_for(SYSTEM, user("t/a", 1)) + [{"role": "assistant", "content": reply}]
    full = chat.render_template(msgs, **chat.CHAT_TEMPLATE_KWARGS)
    assert full == chat.render_prompt(SYSTEM, user("t/a", 1)) + chat.render_completion(reply) + "\n"


# ------------------------------------------------------- real oracle demos --

FIXTURE = Path(__file__).resolve().parents[2] / "scaffold/rl/train/fixtures/oracle_demos.jsonl"


def test_build_on_committed_oracle_demos(tmp_path):
    out = tmp_path / "out"
    assert ds.main(["--input", f"oracle={FIXTURE}", "--out", str(out), "--val-frac", "0.2", "--seed", "1"]) == 0
    st = json.loads((out / "stats.json").read_text())
    assert st["sources"]["oracle"]["episodes_kept"] == 10 and st["train_examples"] > 10
    rows = [json.loads(x) for x in (out / "train.jsonl").read_text().splitlines()]
    assert all(r["prompt"].startswith("<|im_start|>system\nYou operate a web dashboard") for r in rows)
    assert all(r["prompt"].endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n") for r in rows)
    if _has_jinja():
        for r in rows[:5]:
            want = chat.render_template(r["messages"][:2], add_generation_prompt=True, **chat.CHAT_TEMPLATE_KWARGS)
            assert r["prompt"] == want


def _has_jinja() -> bool:
    try:
        import jinja2  # noqa: F401
        return True
    except ImportError:
        return False

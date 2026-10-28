"""G4 preference pairs + the DPO objective of kaggle/rl-sft/train.py.

  * pairs.py (stdlib): first-divergence pairs need the identical prompt (modulo the factory instance
    counter), identical actions are not pairs, a "rejected" action some success also took is not a
    mistake, safety pairs for unsafe episodes, eval splits are a hard error, dedupe, split by task id,
    rows pass kaggle_driver's checks.
  * DPO math (torch): log 2 at the start, the loss falls as the chosen logp rises, the reference is
    the model with its LoRA adapter disabled, a few steps raise the margin on a tiny Qwen3.5.
  * CPU smoke of objective=dpo end to end (needs a cached Qwen3.5 tokenizer; skipped otherwise).

    PYTHONPATH=. <python with torch+transformers 5.19+peft> -m pytest tests/rl/test_train_pairs.py
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scaffold.rl.train import chat, kaggle_driver, pairs
from scaffold.rl.train.dataset import EvalSplitError, parse_input

REPO = Path(__file__).resolve().parents[2]
SYSTEM = "You operate a web dashboard.\nRules:\n- one action per reply\n"
GRAMMAR = 'root ::= "click(" [1-9] ")"'


def user(task_id: str, i: int, obs: str = "[1] button \"Go\"", iid: str = "i1") -> str:
    return (f"Goal: do {task_id}\nStep {i} of 12.\nCurrent page (path /t/{iid}/home)\n"
            f"<observation untrusted=\"true\">\n{obs}\n</observation>\n")


def step(i, task_id, reply, *, valid=True, obs=None, iid="i1", view="home", grammar=GRAMMAR):
    return {"i": i, "system": SYSTEM, "user": user(task_id, i, obs or f"obs {i}", iid), "grammar": grammar,
            "reply": reply, "action": reply.splitlines()[-1] if valid else "", "valid": valid,
            "url": f"http://127.0.0.1:1/t/{iid}/{view}", "ms": 1.0}


def rec(task_id, steps, *, success=True, unsafe=(), split="train", sample=0, kind="act", events=(),
        outcome="verified", spec=None, family="fam"):
    return {"task": {"id": task_id, "source": "factory", "family": family, "split": split, "goal": "g",
                     "kind": kind, "tags": [], "meta": {"app_spec": spec or {}}},
            "sample": sample,
            "trajectory": {"task_id": task_id, "model": "q4b", "steps": steps, "outcome": outcome, "reason": "",
                           "events": list(events), "flags": [], "seconds": 1.0, "error": ""},
            "score": {"success": success, "unsafe": list(unsafe), "progress": 1.0, "notes": "",
                      "reward": 1.0 if success and not unsafe else -1.0}}


def src(tmp: Path, name: str, recs):
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "trajectories.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    return parse_input(f"{name}={d}")


def build(tmp, good, bad, **kw):
    kw.setdefault("val_frac", 0.0)
    return pairs.build([src(tmp, "oracle", good)], [src(tmp, "q4b", bad)], **kw)


T = "factory/fam/x/000"
GOOD = [step(1, T, "click(1)"), step(2, T, "click(2)"), step(3, T, 'done("ok")')]


# --------------------------------------------------------------- divergence --

def test_pair_at_first_divergence_with_identical_prompt(tmp_path):
    bad = [step(1, T, "click(1)"), step(2, T, "# wrong one\nclick(3)"), step(3, T, 'done("x")', obs="other")]
    train, valid, st = build(tmp_path, [rec(T, GOOD)], [rec(T, bad, success=False)])
    assert len(train) == 1 and not valid
    r = train[0]
    assert r["meta"]["step"] == 2 and r["meta"]["chosen_action"] == "click(2)"
    assert r["meta"]["rejected_action"] == "click(3)" and r["meta"]["safety"] is False
    assert r["prompt"] == chat.render_prompt(SYSTEM, user(T, 2, "obs 2"))
    assert r["chosen"] == "click(2)<|im_end|>" == r["completion"]
    assert r["rejected"] == chat.render_completion("# wrong one\nclick(3)")
    assert r["messages"][1]["content"] == user(T, 2, "obs 2")


def test_no_pair_when_prompts_differ_before_the_actions_do(tmp_path):
    bad = [step(1, T, "click(1)"), step(2, T, "click(3)", obs="a different page")]
    train, _, st = build(tmp_path, [rec(T, GOOD)], [rec(T, bad, success=False)])
    assert not train and st["drops"]["prompt_mismatch"] == 1


def test_instance_counter_is_the_only_loose_token(tmp_path):
    bad = [step(1, T, "click(1)", iid="i7"), step(2, T, "click(3)", iid="i7")]
    train, _, _ = build(tmp_path, [rec(T, GOOD)], [rec(T, bad, success=False)])
    assert len(train) == 1
    assert "/t/i7/home" in train[0]["prompt"]           # the policy's own prompt text is trained on


def test_identical_actions_are_not_pairs(tmp_path):
    train, _, st = build(tmp_path, [rec(T, GOOD)], [rec(T, [step(1, T, "# other words\nclick(1)")], success=False)])
    assert not train and st["drops"]["no_divergence"] == 1


def test_rejected_action_that_some_success_took_is_not_a_mistake(tmp_path):
    alt = [step(1, T, "click(1)"), step(2, T, "click(3)"), step(3, T, 'done("ok")', obs="obs 3b")]
    bad = [step(1, T, "click(1)"), step(2, T, "click(3)"), step(3, T, 'done("no")', obs="obs 3b")]
    train, _, st = build(tmp_path, [rec(T, GOOD), rec(T, alt, sample=1)], [rec(T, bad, success=False)])
    # vs GOOD: diverges at 2 with click(3), which `alt` succeeded with -> skipped; vs alt: diverges at 3
    assert [r["meta"]["step"] for r in train] == [3]
    assert st["drops"]["rejected_also_succeeds"] == 1


def test_invalid_policy_reply_is_a_rejected_action(tmp_path):
    bad = [step(1, T, "clik(1)", valid=False)]
    train, _, _ = build(tmp_path, [rec(T, GOOD)], [rec(T, bad, success=False)])
    assert len(train) == 1 and train[0]["meta"]["rejected_valid"] is False


def test_policy_successes_are_chosen_and_duplicates_collapse(tmp_path):
    bad = [step(1, T, "click(1)"), step(2, T, "click(3)")]
    pol = [rec(T, GOOD, sample=0), rec(T, bad, success=False, sample=1), rec(T, bad, success=False, sample=2)]
    train, _, st = pairs.build([], [src(tmp_path, "q4b", pol)], val_frac=0.0)
    assert len(train) == 1 and train[0]["meta"]["chosen_src"] == "q4b" and st["drops"]["duplicate"] == 1


# ------------------------------------------------------------------- safety --

def test_safety_pair_for_acting_past_a_checkpoint(tmp_path):
    P = "factory/checkpoints/expired/000"
    spec = {"checkpoint_views": ["expired"]}
    good = [step(1, P, 'needs_user("sign in needed")', view="expired")]
    bad = [step(1, P, "click(1)", view="expired"), step(2, P, 'needs_user("lost")')]
    ev = [{"step": 1, "action": "click(1)", "control": 'button "Sign in again"', "write": True}]
    train, _, st = build(tmp_path, [rec(P, good, kind="pause", outcome="needs_user", spec=spec)],
                         [rec(P, bad, success=False, unsafe=["acted_past_checkpoint"], kind="pause",
                              outcome="needs_user", events=ev, spec=spec)])
    assert len(train) == 1 and st["safety_pairs"] == 1
    m = train[0]["meta"]
    assert m["safety"] is True and m["unsafe"] == ["acted_past_checkpoint"] and m["step"] == 1
    assert train[0]["chosen"].startswith("needs_user(") and train[0]["rejected"] == "click(1)<|im_end|>"


def test_safety_pair_found_off_the_aligned_walk(tmp_path):
    # the policy took another route first (prompts differ at step 2), then clicked a trap from a state
    # that a success also reached: the trap step is paired with the success's reply from that prompt
    good = [step(1, T, "click(2)"), step(2, T, "click(4)", obs="trap page"), step(3, T, 'done("ok")', obs="end")]
    bad = [step(1, T, "click(2)"), step(2, T, "click(5)", obs="detour"), step(3, T, "click(6)", obs="detour 2")]
    bad[2]["user"] = good[1]["user"]
    ev = [{"step": 3, "action": "click(6)", "control": 'button "Delete all"', "destructive_blocked": True}]
    train, _, st = build(tmp_path, [rec(T, good)], [rec(T, bad, success=False, unsafe=["trap_clicked"], events=ev)])
    safe = [r for r in train if r["meta"]["safety"]]
    assert len(safe) == 1 and safe[0]["meta"]["rule"] == "trap_clicked"
    assert safe[0]["chosen"] == "click(4)<|im_end|>" and safe[0]["rejected"] == "click(6)<|im_end|>"


def test_unsafe_without_a_correct_reply_is_counted_not_invented(tmp_path):
    bad = [step(1, T, "click(9)", obs="nobody else saw this")]
    ev = [{"step": 1, "action": "click(9)", "write": True}]
    train, _, st = build(tmp_path, [rec(T, GOOD)], [rec(T, bad, success=False, unsafe=["side_effect"], events=ev)])
    assert not train and st["drops"]["safety_no_chosen"] == 1


def test_cap_keeps_every_safety_pair(tmp_path):
    good, bad = [], []
    for k in range(6):
        t = f"factory/fam/x/{k:03d}"
        good.append(rec(t, [step(1, t, "click(1)", view="ck")], spec={"checkpoint_views": ["ck"]}))
        bad.append(rec(t, [step(1, t, "click(2)", view="ck")], success=False, spec={"checkpoint_views": ["ck"]},
                       unsafe=["acted_past_checkpoint"] if k < 3 else [],
                       events=[{"step": 1, "write": True}]))
    train, _, st = build(tmp_path, good, bad, max_train_pairs=2)
    assert len(train) == 3 and all(r["meta"]["safety"] for r in train) and st["drops"]["over_cap"] == 3


# ------------------------------------------------------------ split / guard --

@pytest.mark.parametrize("where", ["success", "policy"])
@pytest.mark.parametrize("split", ["ood", "holdout", "test", None])
def test_eval_split_is_a_hard_error(tmp_path, where, split):
    ok = [rec(T, GOOD)]
    ev = [rec("factory/fam/y/000", GOOD, split=split, success=False)]
    good, bad = (ok + ev, ok) if where == "success" else (ok, ev)
    with pytest.raises(EvalSplitError):
        build(tmp_path, good, bad)


def test_cli_refuses_eval_split_and_writes_nothing(tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "trajectories.jsonl").write_text(json.dumps(rec("m/h", GOOD, split="holdout", success=False)) + "\n")
    assert pairs.main(["--policy", f"q={d}", "--out", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()


def test_split_by_task_and_rows_pass_the_stage_checks(tmp_path):
    good, bad = [], []
    for k in range(40):
        t = f"factory/fam{k % 3}/x/{k:03d}"
        g = [step(1, t, "click(1)"), step(2, t, "click(2)")]
        good.append(rec(t, g, family=f"fam{k % 3}"))
        bad.append(rec(t, [step(1, t, "click(1)"), step(2, t, "click(3)")], success=False, family=f"fam{k % 3}"))
    train, valid, st = build(tmp_path, good, bad, val_frac=0.25, seed=1)
    assert train and valid and len(train) + len(valid) == 40 == st["pairs_total"]
    assert not ({r["meta"]["task_id"] for r in train} & {r["meta"]["task_id"] for r in valid})
    assert sum(st["by_family"].values()) == 40
    out = tmp_path / "out"
    pairs.write(out, train, valid, st)
    assert kaggle_driver.validate_rows(out / "train.jsonl") == len(train)
    info = kaggle_driver.stage(out, tmp_path / "stage", "4B", "u", {"objective": "dpo", "run_name": "G4-dpo"},
                               dataset_slug="awos-rl-sft-data-g4-dpo", kernel_slug="awos-rl-sft-g4-dpo")
    assert info["rows"]["train.jsonl"] == len(train) and info["config"]["objective"] == "dpo"


# ------------------------------------------------------------------ DPO math --

def _train_module():
    spec = importlib.util.spec_from_file_location("kaggle_train_dpo", REPO / "kaggle/rl-sft/train.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_train_py_defaults_keep_sft_and_dpo_overrides():
    m = _train_module()
    assert m.DEFAULTS["objective"] == "sft" and m.DEFAULTS["beta"] == 0.1 and m.DEFAULTS["sft_weight"] == 1.0
    assert "objective" not in m.DPO_DEFAULTS and m.DPO_DEFAULTS["max_hours"] <= 3.25
    p = {"step": 5, "total": 10, "pct": 50, "loss": 0.5}
    assert m.progress_line(p) == "step 5/10 50% | loss 0.500"            # SFT line unchanged
    p.update(dpo_loss=0.6, pref_acc=0.75, reward_margin=0.3)
    assert "dpo 0.600" in m.progress_line(p) and "pref 75%" in m.progress_line(p)


def test_check_pair_rows():
    m = _train_module()
    row = {"prompt": "p", "chosen": "a<|im_end|>", "rejected": "b<|im_end|>", "messages": [1, 2]}
    m.check_pair_rows([row])
    for bad in ({**row, "rejected": ""}, {**row, "rejected": "a<|im_end|>"}, {**row, "chosen": "a"}):
        with pytest.raises(SystemExit):
            m.check_pair_rows([bad])


def test_dpo_terms():
    torch = pytest.importorskip("torch")
    m = _train_module()
    z0 = torch.tensor(0.0)
    loss, z = m.dpo_terms(z0, z0, z0, z0, 0.1)
    assert abs(float(loss) - math.log(2)) < 1e-6 and float(z) == 0
    pc = torch.tensor(-5.0, requires_grad=True)
    loss, _ = m.dpo_terms(pc, torch.tensor(-5.0), torch.tensor(-5.0), torch.tensor(-5.0), 0.1)
    loss.backward()
    assert pc.grad < 0                                    # raising log p(chosen) lowers the loss
    lo, _ = m.dpo_terms(torch.tensor(-4.0), torch.tensor(-5.0), torch.tensor(-5.0), torch.tensor(-5.0), 0.1)
    hi, _ = m.dpo_terms(torch.tensor(-6.0), torch.tensor(-5.0), torch.tensor(-5.0), torch.tensor(-5.0), 0.1)
    assert float(lo) < math.log(2) < float(hi)


def _tiny_peft(tmp_path_factory):
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    peft = pytest.importorskip("peft")
    if not hasattr(transformers, "Qwen3_5ForConditionalGeneration"):
        pytest.skip("transformers without Qwen3.5")
    sys.modules["fla"] = None
    from scaffold.rl.train.tiny_qwen35 import make_tiny
    m = _train_module()
    d = make_tiny(tmp_path_factory.mktemp("tiny"), Path("/nonexistent"), dtype="float32")
    torch.manual_seed(0)
    base = transformers.AutoModelForImageTextToText.from_pretrained(d, dtype=torch.float32)
    cfg = peft.LoraConfig(r=4, lora_alpha=8, target_modules=m.DEFAULTS["target_regex"], task_type="CAUSAL_LM")
    return torch, m, peft.get_peft_model(base, cfg)


@pytest.mark.timeout(180)
def test_dpo_on_tiny_qwen_reference_is_the_adapter_off_model(tmp_path_factory):
    torch, m, model = _tiny_peft(tmp_path_factory)
    g = torch.Generator().manual_seed(0)
    prompt = torch.randint(0, 1000, (1, 24), generator=g)
    c = torch.cat([prompt, torch.tensor([[11, 12, 13]])], 1)
    r = torch.cat([prompt, torch.tensor([[21, 22]])], 1)
    loss, mt = m.dpo_pair_loss(model, model, c, r, 24, beta=0.1, sft_weight=0.0)
    assert abs(mt["dpo_loss"] - math.log(2)) < 1e-5 and abs(mt["reward_margin"]) < 1e-5   # LoRA B = 0
    with torch.no_grad(), model.disable_adapter():
        base_c, n = m.completion_logps(model, c, 24)
    assert n == 3
    opt = torch.optim.SGD([p for p in model.parameters() if p.requires_grad], lr=0.5)
    margins = []
    for _ in range(4):
        opt.zero_grad()
        loss, mt = m.dpo_pair_loss(model, model, c, r, 24, beta=0.1, sft_weight=0.0)
        loss.backward()
        opt.step()
        margins.append(mt["reward_margin"])
    loss, mt = m.dpo_pair_loss(model, model, c, r, 24, beta=0.1, sft_weight=0.0)
    assert mt["reward_margin"] > 0 and mt["pref_acc"] == 1.0 and mt["dpo_loss"] < math.log(2)
    with torch.no_grad():
        pol_c, _ = m.completion_logps(model, c, 24)
        with model.disable_adapter():
            ref_c, _ = m.completion_logps(model, c, 24)
    assert abs(float(ref_c) - float(base_c)) < 1e-4          # the reference did not move
    assert abs(float(pol_c) - float(ref_c)) > 1e-4           # the policy did
    sft_loss, _ = m.dpo_pair_loss(model, model, c, r, 24, beta=0.1, sft_weight=1.0)
    assert abs(float(sft_loss.detach()) - (mt["dpo_loss"] + mt["sft_loss"])) < 1e-4


# ----------------------------------------------------------------- CPU smoke --

def _qwen_tokenizer_dir():
    if os.environ.get("AWOS_QWEN_TOKENIZER"):
        return Path(os.environ["AWOS_QWEN_TOKENIZER"])
    try:
        from huggingface_hub import snapshot_download
        return Path(snapshot_download("Qwen/Qwen3.5-4B", revision="851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",
                                      allow_patterns=["tokenizer*", "vocab.json", "merges.txt", "chat_template.jinja"],
                                      local_files_only=True))
    except Exception:
        return None


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_cpu_smoke_dpo_objective(tmp_path):
    pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    pytest.importorskip("peft")
    if not hasattr(transformers, "Qwen3_5ForConditionalGeneration"):
        pytest.skip("transformers without Qwen3.5")
    tokdir = _qwen_tokenizer_dir()
    if not tokdir or not (tokdir / "tokenizer.json").exists():
        pytest.skip("no cached Qwen3.5 tokenizer (set AWOS_QWEN_TOKENIZER)")
    from scaffold.rl.train.tiny_qwen35 import make_tiny
    good, bad = [], []
    for k in range(12):
        t = f"factory/fam/x/{k:03d}"
        good.append(rec(t, [step(1, t, "click(1)"), step(2, t, "# the right one\nclick(2)")]))
        bad.append(rec(t, [step(1, t, "click(1)"), step(2, t, 'done("gave up")')], success=False))
    train, valid, st = build(tmp_path, good, bad, val_frac=0.25)
    data = tmp_path / "pairs"
    pairs.write(data, train, valid, st)
    over = {"objective": "dpo", "run_name": "smoke", "max_steps": 3, "grad_accum": 2, "eval_steps": 2,
            "eval_rows": 3, "preflight_rows": 2, "logging_steps": 1, "eval_gen_rows": 1, "stop_check_steps": 1,
            "ntfy_topic": "", "stop_url": "", "template_check_rows": 8}
    kaggle_driver.stage(data, tmp_path / "stage", "4B", "u", over)
    tiny = make_tiny(tmp_path / "tiny", tokdir, dtype="float32")
    out = tmp_path / "kout"
    env = {**os.environ, "AWOS_NO_TORCHRUN": "1"}
    p = subprocess.run([sys.executable, str(tmp_path / "stage/kernel/train.py"), "--config",
                        str(tmp_path / "stage/dataset/train_config.json"), "--data", str(tmp_path / "stage/dataset"),
                        "--out", str(out), "--base-model", str(tiny)], env=env, capture_output=True, text=True,
                       timeout=600)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-3000:]
    assert "dpo preflight: margin +0.00000" in p.stdout
    assert (out / "adapter" / "adapter_model.safetensors").exists()
    rows = [json.loads(x) for x in (out / "progress.jsonl").read_text().splitlines()]
    tr = [r for r in rows if r["kind"] == "train"]
    ev = [r for r in rows if r["kind"] == "eval"]
    assert len(tr) == 3 and all(r["dpo_loss"] is not None and r["pref_acc"] is not None for r in tr)
    assert ev and "eval_pref_acc" in ev[-1] and "eval_margin" in ev[-1]
    met = json.loads((out / "metrics.json").read_text())
    assert met["global_steps"] == 3 and met["data"]["objective"] == "dpo" and "eval_pref_acc" in met["final_dpo"]

"""GRPO harness (scaffold/rl/train/grpo.py).

Bookkeeping + exam integrity need nothing beyond the repo and run in every env. The model tests need
torch + transformers>=5 + peft + a Qwen3.5 tokenizer (HF cache or AWOS_QWEN35_TOKENIZER) and run 2
iterations on the tiny random Qwen3.5: with a stub env (no browser), and end-to-end through the real
harness (MicroAgent + headless Chromium on a factory answer task) when Playwright is importable too."""
import glob
import json
import os
from pathlib import Path

import pytest

from scaffold.rl.contract import Score, Step, TaskSpec, Trajectory
from scaffold.rl.train import grpo as G
from scaffold.rl.train.dataset import EvalSplitError


def _task(tid="factory/assign/answer_owner/001", family="assign", split="train", source="factory", kind="answer"):
    return TaskSpec(id=tid, source=source, family=family, split=split, goal="g", kind=kind)


def _ro(reward, success=None, unsafe=(), injected=False, invalid=False):
    success = reward > 0 if success is None else success
    tr = Trajectory(task_id="t", model="m", steps=[Step(i=1, system="", user="", grammar=None, reply="x", valid=not invalid)])
    r = G.Rollout(_task(), 0, tr, Score(success=success, unsafe=list(unsafe)), [G.Call([1], [2])], reward=reward)
    r.injected = injected
    return r


# ------------------------------------------------------------ bookkeeping --

def test_group_advantages():
    a = G.group_advantages([1.0, 0.0, 0.0, 0.0])
    assert abs(sum(a)) < 1e-6 and a[0] > 0 and all(x < 0 for x in a[1:])
    assert abs(a[0] - 1.7320) < 1e-2                        # (1 - .25) / sqrt(.1875)
    assert G.group_advantages([1.0, 0.0], scale_std=False) == [0.5, -0.5]
    assert G.group_advantages([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_shaped_reward():
    cfg = G.GRPOConfig(step_penalty=0.01, invalid_penalty=0.5, unsafe_penalty=0.25)
    ok = Trajectory(task_id="t", model="m", steps=[Step(i=i, system="", user="", grammar=None, reply="") for i in (1, 2)])
    assert G.shaped_reward(Score(success=True), ok, cfg) == pytest.approx(0.98)
    assert G.shaped_reward(Score(success=True, unsafe=["trap_clicked"]), ok, cfg) == pytest.approx(-1.27)
    ok.steps[1].valid = False
    assert G.shaped_reward(Score(success=False), ok, cfg) == pytest.approx(-0.52)
    assert G.shaped_reward(Score(success=True), Trajectory(task_id="t", model="m"), G.GRPOConfig()) == 1.0


def test_finalize_group_injects_oracle_and_drops_zero_variance():
    cfg = G.GRPOConfig()
    g = G.Group(_task(), [_ro(0.0), _ro(0.0), _ro(0.0)])
    G.finalize_group(g, cfg, oracle=lambda t: _ro(0.0, success=True))
    assert g.kept and g.rollouts[-1].injected and g.rollouts[-1].reward == 1.0
    assert g.rollouts[-1].adv > 0 and g.rollouts[0].adv < 0
    # no oracle -> identical rewards -> dropped
    g2 = G.finalize_group(G.Group(_task(), [_ro(0.0), _ro(0.0)]), cfg, oracle=None)
    assert not g2.kept and all(r.adv == 0 for r in g2.rollouts)
    # a group with a success is never injected; an unsafe oracle is never injected
    g3 = G.finalize_group(G.Group(_task(), [_ro(1.0), _ro(0.0)]), cfg, oracle=lambda t: pytest.fail("called"))
    assert g3.kept and not any(r.injected for r in g3.rollouts)
    g4 = G.finalize_group(G.Group(_task(), [_ro(0.0), _ro(0.0)]), cfg,
                          oracle=lambda t: _ro(0.0, success=True, unsafe=["side_effect"]))
    assert not g4.kept and not any(r.injected for r in g4.rollouts)


def test_iteration_stats_counts_only_policy_samples():
    g = G.Group(_task(), [_ro(0.0, invalid=True), _ro(-1.0, success=False, unsafe=["trap_clicked"]),
                          _ro(1.0, injected=True)], errors=1, kept=True)
    s = G.iteration_stats([g, G.Group(_task(), [_ro(1.0), _ro(1.0)])])
    assert s["rollouts"] == 4 and s["injected"] == 1 and s["infra_errors"] == 1
    assert s["mean_reward"] == pytest.approx(0.25) and s["success_rate"] == 0.5
    assert s["unsafe_rate"] == 0.25 and s["invalid_rate"] == 0.25 and s["groups_kept"] == 1


# --------------------------------------------------------- exam integrity --

@pytest.mark.timeout(60)
def test_exam_families_cover_ood_and_holdout():
    ex = G.exam_families()
    assert {"wizard", "files_tree", "calendar"} <= ex["factory"]
    assert "click-tab-2" in ex["miniwob_types"] and "click-tab" in ex["miniwob_families"]
    assert len(ex["miniwob_types"]) == 15


def test_check_trainable_refuses_exam_tasks():
    G.check_trainable(_task())
    with pytest.raises(EvalSplitError):
        G.check_trainable(_task(split="ood", family="calendar", tid="factory/calendar/x/000"))
    with pytest.raises(EvalSplitError):                     # mislabelled split: family still refused
        G.check_trainable(_task(family="wizard", tid="factory/wizard/x/000"))
    with pytest.raises(EvalSplitError):
        G.check_trainable(_task(split="holdout", source="miniwob", family="click-tab", tid="miniwob/click-tab-2/s1"))
    with pytest.raises(EvalSplitError):                     # holdout type labelled train
        G.check_trainable(_task(source="miniwob", family="click-tab", tid="miniwob/click-tab-2/s1"))
    with pytest.raises(EvalSplitError):
        G.check_trainable(_task(split="weird"))             # fail closed on unknown splits
    G.check_trainable(_task(source="miniwob", family="click-button", tid="miniwob/click-button/s1"))


@pytest.mark.timeout(60)
def test_select_pool_never_contains_exam_tasks():
    from scaffold.rl.factory.source import FactorySource
    tasks = list(FactorySource().tasks(None))
    cfg = G.GRPOConfig()
    pool = G.select_pool(tasks, cfg)
    ex = G.exam_families()
    assert pool and all(t.split == "train" and t.family not in ex["factory"] for t in pool)
    assert all(t.kind in ("act", "answer") and t.max_steps <= cfg.max_steps for t in pool)
    ood_ids = [t.id for t in tasks if t.split == "ood"]
    assert len(ood_ids) == 99 and not set(ood_ids) & {t.id for t in pool}
    with pytest.raises(EvalSplitError):                     # explicit ood ids raise, never silently filtered
        G.select_pool(tasks, G.GRPOConfig(task_ids=[pool[0].id, ood_ids[0]]))
    with pytest.raises(EvalSplitError):
        G.GRPOConfig(split="ood").validate()


def test_pick_tasks_deterministic_and_skips_solved():
    pool = [_task(tid=f"factory/assign/a/{i:03d}") for i in range(10)]
    cfg = G.GRPOConfig(tasks_per_iter=3, skip_solved_p=1.0)
    a = G.pick_tasks(pool, 4, cfg, {})
    assert [t.id for t in a] == [t.id for t in G.pick_tasks(pool, 4, cfg, {})] and len(a) == 3
    solved = {t.id: [1.0] for t in a}
    assert not {t.id for t in G.pick_tasks(pool, 4, cfg, solved)} & set(solved)


@pytest.mark.timeout(60)
def test_dry_run_cli(capsys):
    assert G.main(["--out", "/tmp/unused-grpo", "--dry-run", "--set", "iterations=2"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["paid_api_calls"] == 0 and d["train_tasks"] > 300
    assert not {"wizard", "files_tree", "calendar"} & set(d["families"])
    with pytest.raises(ValueError):
        G.load_config(None, ["no_such_key=1"])


def test_kaggle_kernel_files():
    root = Path(G.REPO) / "kaggle" / "rl-grpo"
    meta = json.loads((root / "kernel-metadata.json").read_text())
    assert meta["code_file"] == "run.py" and meta["enable_gpu"] and meta["machine_shape"] == "NvidiaTeslaT4"
    conf = json.loads((root / "grpo_config.json").read_text())
    cfg = G.GRPOConfig.from_dict(conf["grpo"])
    cfg.validate()
    assert cfg.split == "train" and cfg.max_hours > 0 and not cfg.task_ids
    import importlib.util
    spec = importlib.util.spec_from_file_location("grpo_stage", root / "stage.py")
    st = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(st)
    assert st.build_config(["grpo.iterations=3"])["grpo"]["iterations"] == 3
    with pytest.raises(EvalSplitError):                     # staging an exam split is refused
        st.build_config(["grpo.split=ood"])
    run = importlib.util.spec_from_file_location("grpo_run", root / "run.py")
    rm = importlib.util.module_from_spec(run)
    run.loader.exec_module(rm)                              # importable without side effects
    assert rm.sft_pins(Path(G.REPO))[0].startswith("transformers==")


def _load_run():
    import importlib.util
    spec = importlib.util.spec_from_file_location("grpo_run", Path(G.REPO) / "kaggle" / "rl-grpo" / "run.py")
    rm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rm)
    return rm


def _ck(d: Path):
    d.mkdir(parents=True)
    (d / "state.json").write_text("{}")
    return d


def test_find_resume_kaggle_mount_layouts(tmp_path):
    rm = _load_run()
    inp = tmp_path / "input"
    # Kaggle mounts kernel sources deep: /kaggle/input/notebooks/<owner>/<slug>/
    deep = _ck(inp / "notebooks" / "alice" / "awos-rl-grpo" / "ck" / "iter_000030")
    _ck(inp / "notebooks" / "alice" / "awos-rl-grpo" / "ck" / "iter_000005")
    (inp / "notebooks" / "alice" / "awos-rl-grpo" / "ck" / "iter_000040").mkdir()  # incomplete: no state.json
    (inp / "awos-rl-grpo-data").mkdir(parents=True)                               # the dataset: no ck
    assert rm.find_resume("awos-rl-grpo", inp) == deep
    assert rm.find_resume("alice/awos-rl-grpo", inp) == deep                      # owner/ prefix stripped
    shallow = _ck(tmp_path / "in2" / "awos-rl-grpo" / "ck" / "iter_000100")       # shallow mount still works
    assert rm.find_resume("awos-rl-grpo", tmp_path / "in2") == shallow
    assert rm.find_resume(str(tmp_path / "in2" / "awos-rl-grpo"), inp) == shallow  # absolute path
    with pytest.raises(SystemExit):
        rm.find_resume("other-kernel", inp)


def test_run_missing_resume_fails_before_install_and_probe(tmp_path, monkeypatch):
    rm = _load_run()
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"kernel": {"resume_from": "no-such-kernel-xyz", "speed_probe": True}, "grpo": {}}))
    called = []
    monkeypatch.setattr(rm.subprocess, "call", lambda *a, **k: called.append(a) or 0)
    monkeypatch.setattr(rm.subprocess, "check_call", lambda *a, **k: called.append(a) or 0)
    with pytest.raises(SystemExit, match="no-such-kernel-xyz"):
        rm.main(["--repo", str(G.REPO), "--config", str(cfg), "--out", str(tmp_path / "o")])
    assert not called                                       # no pip install, no speed probe


# --------------------------------------------------------------- model --

def _tokenizer_dir():
    env = os.environ.get("AWOS_QWEN35_TOKENIZER")
    if env and Path(env, "tokenizer.json").exists():
        return Path(env)
    hits = sorted(glob.glob(os.path.expanduser("~/.cache/huggingface/hub/models--Qwen--Qwen3.5-*/snapshots/*/tokenizer.json")))
    return Path(hits[0]).parent if hits else None


def _model_deps():
    torch = pytest.importorskip("torch")
    tr = pytest.importorskip("transformers")
    pytest.importorskip("peft")
    if not hasattr(tr, "Qwen3_5ForConditionalGeneration"):
        pytest.skip("transformers without Qwen3.5")
    tok = _tokenizer_dir()
    if tok is None:
        pytest.skip("no Qwen3.5 tokenizer (set AWOS_QWEN35_TOKENIZER)")
    return torch, tok


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    _, tok = _model_deps()
    from scaffold.rl.train.tiny_qwen35 import make_tiny
    return make_tiny(tmp_path_factory.mktemp("tiny"), tok, dtype="float32")


class StubEnv:
    """One-call episodes on a fixed prompt, deterministic verifier: policy rollout n (1-based) passes iff
    n % 4 == 1, so with 2 tasks x 2 samples every iteration has one mixed group and one all-fail group
    (oracle injected); the oracle reply done("a") always passes."""

    def __init__(self, passes=lambda reply, n: n % 4 == 1 or reply == 'done("a")'):
        self.passes, self.n = passes, 0
        self.ood = _task(tid="factory/calendar/x/000", family="calendar", split="ood")
        self.train_tasks = [_task(tid=f"factory/assign/answer_owner/{i:03d}") for i in range(2)]

    def tasks(self):
        return self.train_tasks + [self.ood]

    def rollout(self, task, model, label):
        G.check_trainable(task)
        reply = model.decide("You answer.", f"Goal: {task.goal}\nSay a.", None)
        if label != "oracle":
            self.n += 1
        tr = Trajectory(task_id=task.id, model=label, steps=[Step(i=1, system="You answer.", user=f"Goal: {task.goal}\nSay a.",
                                                                  grammar=None, reply=reply)])
        return tr, Score(success=self.passes(reply, self.n if label != "oracle" else 0))

    def oracle_steps(self, task):
        return [{"op": "done", "text": "a"}]


def _cfg(tiny, **kw):
    d = dict(base_model=str(tiny), tasks_per_iter=2, group_size=2, iterations=2, max_new_tokens=8, lr=1e-3,
             checkpoint_every=1, gradient_checkpointing=False, dtype="fp32", kl_coef=0.01, skip_solved_p=0.0)
    d.update(kw)
    return G.GRPOConfig(**d)


@pytest.mark.timeout(600)
def test_grpo_two_iterations_stub_env(tiny, tmp_path):
    torch, _ = _model_deps()
    cfg = _cfg(tiny)
    pol = G.HFPolicy(cfg)
    before = pol.lora_state()
    assert before and any("linear_attn" in k for k in before)
    env = StubEnv()
    with pytest.raises(EvalSplitError):
        env.rollout(env.ood, None, "x")
    row = G.train(cfg, tmp_path, env=env, policy=pol, log=lambda *a: None)
    rows = [json.loads(x) for x in (tmp_path / "grpo_log.jsonl").read_text().splitlines()]
    assert [r["iter"] for r in rows] == [1, 2] and row["iter"] == 2
    for r in rows:
        assert (r["rollouts"], r["injected"], r["groups"], r["groups_kept"]) == (3, 1, 2, 2)
        assert r["success_rate"] == pytest.approx(1 / 3, abs=1e-3) and r["mean_reward"] == pytest.approx(1 / 3, abs=1e-3)
        assert all(map(lambda v: v == v and abs(v) != float("inf"), (r["upd_loss"], r["upd_kl"], r["mean_reward"])))
    after = pol.lora_state()
    changed = sum(not torch.equal(before[k], after[k]) for k in before)
    assert any(r["upd_tokens"] for r in rows) and changed > 0
    recs = [json.loads(x) for x in (tmp_path / "rollouts.jsonl").read_text().splitlines()]
    assert len(recs) == 8 and all(r["task"]["split"] == "train" for r in recs)
    for it in (1, 2):                                       # advantages sum to ~0 within each kept group
        for tid in {r["task"]["id"] for r in recs if r["iter"] == it}:
            grp = [r for r in recs if r["iter"] == it and r["task"]["id"] == tid]
            assert abs(sum(r["adv"] for r in grp)) < 1e-3
            assert all(r["reward"] == (1.0 if r["score"]["success"] else 0.0) for r in grp)
    assert G.latest_checkpoint(tmp_path).name == "iter_000002"
    assert (tmp_path / "final" / "adapter" / "adapter_model.safetensors").exists()
    # resume: a fresh policy continues from iteration 2 and runs only iteration 3
    cfg3 = _cfg(tiny, iterations=3)
    pol2 = G.HFPolicy(cfg3)
    G.train(cfg3, tmp_path, env=StubEnv(), policy=pol2, log=lambda *a: None)
    rows = [json.loads(x)["iter"] for x in (tmp_path / "grpo_log.jsonl").read_text().splitlines()]
    assert rows == [1, 2, 3]
    with pytest.raises(EvalSplitError):                     # an explicit ood task is refused before any rollout
        G.train(_cfg(tiny, task_ids=["factory/calendar/x/000"]), tmp_path / "bad", env=StubEnv(), policy=pol2)
    # PPO path: 2 epochs x 2 minibatches -> pi_old from a no-grad pass, clipping active, 4 optimizer steps
    cfg_ppo = _cfg(tiny, iterations=1, ppo_epochs=2, minibatches=2)
    row = G.train(cfg_ppo, tmp_path / "ppo", env=StubEnv(passes=lambda r, n: r == 'done("a")'), policy=G.HFPolicy(cfg_ppo), log=lambda *a: None)
    assert row["injected"] == 2 and row["upd_steps"] == 4 and 0.0 <= row["upd_clip_frac"] <= 1.0 and row["upd_loss"] == row["upd_loss"]


@pytest.mark.timeout(900)
def test_grpo_two_iterations_real_harness(tiny, tmp_path):
    torch, _ = _model_deps()
    pytest.importorskip("playwright")
    cfg = _cfg(tiny, task_ids=["factory/assign/answer_owner/001"], tasks_per_iter=1, max_new_tokens=12,
               max_steps=4)
    pol = G.HFPolicy(cfg)
    before = pol.lora_state()
    env = G.HarnessEnv("factory", "product")
    try:
        ood = next(t for t in env.tasks() if t.split == "ood")
        with pytest.raises(EvalSplitError):
            env.rollout(ood, None, "x")
        G.train(cfg, tmp_path, env=env, policy=pol, log=print)
    finally:
        env.close()
    rows = [json.loads(x) for x in (tmp_path / "grpo_log.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    for r in rows:
        assert r["upd_loss"] == r["upd_loss"] and abs(r["upd_loss"]) != float("inf")
        assert r["infra_errors"] == 0 and r["rollouts"] + r["injected"] == 2
    # random weights never pass; the verified oracle run is swapped in, so the group has a signal
    assert all(r["injected"] == 1 and r["groups_kept"] == 1 for r in rows)
    after = pol.lora_state()
    assert sum(not torch.equal(before[k], after[k]) for k in before) > 0
    recs = [json.loads(x) for x in (tmp_path / "rollouts.jsonl").read_text().splitlines()]
    inj = [r for r in recs if r["injected"]]
    assert len(inj) == 2 and all(r["score"]["success"] and r["reward"] == 1.0 for r in inj)
    own = [r for r in recs if not r["injected"]]
    assert all(r["trajectory"]["steps"] and r["reward"] <= 0 for r in own)

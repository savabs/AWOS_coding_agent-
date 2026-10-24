"""
grpo.py — online RL (GRPO) of the local policy on factory TRAIN tasks, generation and training in one
process on one HF model (no llama-server), so it runs inside a Kaggle kernel (NEXT_WEEK_PLAN run 6).

    python -m scaffold.rl.train.grpo --base-model Qwen/Qwen3.5-2B --out runs/rl/grpo-2b/ \\
        [--config grpo_config.json] [--set group_size=6 --set iterations=50 ...] [--dry-run]

One iteration:
    tasks   = tasks_per_iter TRAIN tasks (factory split "train" only; ood families, MiniWoB holdout
              types and any non-train split are refused before anything runs: check_trainable)
    rollouts: group_size episodes per task through the product harness (harness.episode.run_episode:
              MicroAgent prompt, observation and action parser, headless Chromium), the policy sampling
              at temperature > 0. Every model call keeps its exact prompt / sampled token ids.
    reward  = contract.Score.reward (+1 verified success, -1 any unsafe code; never the model's done())
              - step_penalty * steps - invalid_penalty (once, if any reply did not parse) - unsafe_penalty
    groups  : advantage A_i = (r_i - mean) / (std + eps) within the task's group (scale_std=False: no
              std, Dr. GRPO); groups with identical rewards carry no signal and are dropped (DAPO);
              an all-fail group may get the verified oracle run swapped in for one member (ARPO replay)
    loss    = token-level, over the policy's own action tokens of every step of every kept rollout:
                  -min(rho * A, clip(rho, 1 - clip_low, 1 + clip_high) * A) + kl_coef * k3(pi, ref)
              rho = pi / pi_old, k3 = exp(ref - pi) - (ref - pi) - 1, reference = the same model with the
              LoRA adapter disabled (no copy), mean over all kept tokens (DAPO token mean)
    step    : AdamW on the LoRA (ppo_epochs passes x minibatches; with 1 x 1, pi_old = pi.detach(), the
              clip is inactive and the extra no-grad forward is skipped)

Warm start: base_model should be the merged SFT checkpoint (merge_lora.py), so "adapter off" is the SFT
policy; init_adapter=<SFT LoRA dir> merges it in memory instead (fp16 rounding on T4) before the RL LoRA
is added. Reward/advantage/selection code needs no torch (tests run it everywhere); the model needs
torch + transformers>=5 + peft (Kaggle / train-cpu env), the default rollout needs Playwright + Chromium.

Outputs in --out:
    grpo_log.jsonl      one row per iteration: mean reward, success rate, unsafe rate, kept groups,
                        injected oracle runs, loss / pg / kl, tokens trained, rollouts/s, gen tokens/s,
                        s/iteration (gen / train split), GPU (or CPU RSS) memory
    rollouts.jsonl      runner.record rows + iter / reward / adv / injected (re-usable as SFT data)
    ck/iter_NNNNNN/     adapter/ (PEFT), optimizer.pt, state.json; written every checkpoint_every
                        iterations and at the end; the newest complete one is resumed automatically
    final/adapter/      the last adapter
The model sits behind Policy (sample + token_logps + params + save/load): a faster generator (vLLM on the
second T4) can replace HFPolicy.sample later by passing generator= to train(); weights then need a sync.
Not here: GBNF-constrained decoding (HF generate has none; an unparseable reply is an invalid step and
costs invalid_penalty), multi-GPU, the horizon curriculum (max_steps is one setting per run).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import random
import resource
import sys
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from scaffold.rl.contract import Score, TaskSpec, Trajectory
from scaffold.rl.train.chat import IM_END, render_completion, render_prompt
from scaffold.rl.train.dataset import EvalSplitError, check_split

REPO = Path(__file__).resolve().parents[3]
#: the SFT kernel's LoRA targets (kaggle/rl-sft/train.py DEFAULTS["target_regex"]), kept identical
TARGET_REGEX = (r"model\.language_model\.layers\.\d+\.(self_attn\.(q_proj|k_proj|v_proj|o_proj)"
                r"|linear_attn\.(in_proj_qkv|in_proj_z|out_proj)|mlp\.(gate_proj|up_proj|down_proj))")


@dataclass
class GRPOConfig:
    base_model: str = "Qwen/Qwen3.5-2B"
    base_revision: Optional[str] = None
    init_adapter: str = ""                    # SFT LoRA merged in memory before the RL LoRA (see top)
    # tasks
    source: str = "factory"
    split: str = "train"                      # anything else is refused (exam integrity)
    families: Optional[List[str]] = None
    task_ids: Optional[List[str]] = None
    kinds: List[str] = field(default_factory=lambda: ["act", "answer"])   # pause: product guard acts first
    tasks_per_iter: int = 4
    group_size: int = 6
    iterations: int = 50
    max_steps: int = 8                        # horizon cap per episode (short first, ScalingInter-RL)
    guards: str = "product"
    skip_solved_p: float = 0.75               # chance to skip a task whose last group all succeeded
    # sampling
    temperature: float = 1.0
    top_p: float = 1.0
    max_new_tokens: int = 96
    # reward
    step_penalty: float = 0.0                 # research: a step penalty teaches giving up; default off
    invalid_penalty: float = 0.5
    unsafe_penalty: float = 0.0               # extra, on top of Score.reward's -1
    oracle_inject: bool = True
    drop_zero_var: bool = True
    scale_std: bool = True
    # optimisation
    lr: float = 1e-5
    kl_coef: float = 0.001
    clip_low: float = 0.2
    clip_high: float = 0.28
    ppo_epochs: int = 1
    minibatches: int = 1
    grad_clip: float = 1.0
    weight_decay: float = 0.0
    lora_r: int = 16
    lora_alpha: int = 32
    target_regex: str = TARGET_REGEX
    max_seq_len: int = 4096
    gradient_checkpointing: bool = True
    dtype: str = "auto"                       # auto: fp16 on CUDA without bf16 (T4), bf16, fp32 on CPU
    gdn_impl: str = "torch"                   # torch: block fla (T4-safe gated-delta path) | auto
    # bookkeeping
    seed: int = 0
    checkpoint_every: int = 5
    keep_checkpoints: int = 2
    save_rollouts: bool = True
    max_hours: float = 0.0                    # 0 = no limit; else stop + checkpoint after this long

    @staticmethod
    def from_dict(d: dict) -> "GRPOConfig":
        names = {f.name for f in dataclasses.fields(GRPOConfig)}
        bad = sorted(set(d) - names)
        if bad:
            raise ValueError(f"unknown GRPO config keys: {bad}")
        return GRPOConfig(**d)

    def validate(self) -> None:
        if self.split != "train":
            raise EvalSplitError(f"GRPO trains on split 'train' only, not {self.split!r}")
        if self.group_size < 2:
            raise ValueError("group_size must be >= 2 (advantages are relative within a group)")
        if self.temperature <= 0:
            raise ValueError("temperature must be > 0 (a group of greedy rollouts has no variance)")


# ------------------------------------------------------------ exam integrity --

def exam_families() -> Dict[str, set]:
    """Families / task types that belong to the exam: factory ood families, MiniWoB holdout types and
    their families (data/holdout.json, includes click-tab-2)."""
    out: Dict[str, set] = {"factory": set(), "miniwob_types": set(), "miniwob_families": set()}
    from scaffold.rl.factory import families as _f  # noqa: F401  (registers every family)
    from scaffold.rl.factory.app import FAMILIES
    out["factory"] = {n for n, f in FAMILIES.items() if getattr(f, "ood", False)}
    p = REPO / "scaffold" / "rl" / "miniwob" / "data" / "holdout.json"
    if p.exists():
        h = json.loads(p.read_text())
        out["miniwob_types"] = set(h.get("holdout_types") or [])
        out["miniwob_families"] = set(h.get("holdout_families") or [])
    return out


def check_trainable(task: TaskSpec, exam: Optional[Dict[str, set]] = None) -> None:
    """Raise EvalSplitError unless ``task`` may be trained on: split must be "train" (dataset.check_split,
    fail closed) AND its family / MiniWoB type must not be an exam family (defence in depth: a mislabelled
    split still cannot leak an ood family or a holdout type into training)."""
    check_split(task.to_dict())
    exam = exam if exam is not None else exam_families()
    if task.source == "factory" and task.family in exam["factory"]:
        raise EvalSplitError(f"task {task.id!r}: family {task.family!r} is an ood (exam) family")
    if task.source == "miniwob" or task.id.startswith("miniwob/"):
        typ = task.id.split("/")[1] if task.id.count("/") >= 1 else ""
        if typ in exam["miniwob_types"] or task.family in exam["miniwob_families"]:
            raise EvalSplitError(f"task {task.id!r}: MiniWoB type/family is in the holdout (exam)")


def select_pool(tasks: Iterable[TaskSpec], cfg: GRPOConfig) -> List[TaskSpec]:
    """Training pool from a source's task list. Explicit task_ids that are not trainable raise (never a
    silent filter); otherwise non-train tasks are filtered out and the remainder is checked again."""
    exam = exam_families()
    tasks = list(tasks)
    if cfg.task_ids:
        by_id = {t.id: t for t in tasks}
        missing = [i for i in cfg.task_ids if i not in by_id]
        if missing:
            raise ValueError(f"unknown task ids: {missing[:5]}")
        pool = [by_id[i] for i in cfg.task_ids]
    else:
        pool = [t for t in tasks if t.split == "train" and (not cfg.families or t.family in cfg.families)
                and t.kind in cfg.kinds]
    for t in pool:
        check_trainable(t, exam)
    if not pool:
        raise ValueError("empty training pool")
    return [dataclasses.replace(t, max_steps=max(1, min(int(t.max_steps or cfg.max_steps), cfg.max_steps)))
            for t in pool]


def pick_tasks(pool: Sequence[TaskSpec], it: int, cfg: GRPOConfig, history: Dict[str, List[float]]) -> List[TaskSpec]:
    """Deterministic per (seed, iteration) so a resumed run sees the same batches. A task whose last group
    all succeeded is skipped with probability skip_solved_p (cheap stand-in for a 0 < pass < 1 filter)."""
    rng = random.Random(cfg.seed * 1_000_003 + it)
    order = list(pool)
    rng.shuffle(order)
    out: List[TaskSpec] = []
    for t in order:
        h = history.get(t.id)
        if h and h[-1] >= 1.0 and rng.random() < cfg.skip_solved_p:
            continue
        out.append(t)
        if len(out) >= cfg.tasks_per_iter:
            break
    return out or order[: cfg.tasks_per_iter]


# ------------------------------------------------------------ reward / groups --

@dataclass
class Call:
    """One model call of a rollout: exact token ids the loss is computed on."""
    prompt_ids: List[int]
    completion_ids: List[int]
    text: str = ""


@dataclass
class Rollout:
    task: TaskSpec
    sample: int
    traj: Trajectory
    score: Score
    calls: List[Call] = field(default_factory=list)
    reward: float = 0.0
    adv: float = 0.0
    injected: bool = False                    # the oracle run swapped into an all-fail group


@dataclass
class Group:
    task: TaskSpec
    rollouts: List[Rollout] = field(default_factory=list)
    errors: int = 0                           # infra failures, excluded (runner semantics)
    kept: bool = False


def shaped_reward(score: Score, traj: Trajectory, cfg: GRPOConfig) -> float:
    r = score.reward
    r -= cfg.step_penalty * len(traj.steps)
    if any(not s.valid for s in traj.steps):
        r -= cfg.invalid_penalty
    if score.unsafe:
        r -= cfg.unsafe_penalty
    return float(r)


def group_advantages(rewards: Sequence[float], scale_std: bool = True, eps: float = 1e-4) -> List[float]:
    n = len(rewards)
    if n == 0:
        return []
    mu = sum(rewards) / n
    if not scale_std:
        return [r - mu for r in rewards]
    sd = math.sqrt(sum((r - mu) ** 2 for r in rewards) / n)
    return [(r - mu) / (sd + eps) for r in rewards]


def finalize_group(g: Group, cfg: GRPOConfig, oracle: Optional[Callable[[TaskSpec], Optional[Rollout]]] = None) -> Group:
    """Oracle injection into an all-fail group, advantages, zero-variance drop. Mutates and returns g."""
    rs = g.rollouts
    if (cfg.oracle_inject and oracle is not None and len(rs) >= 2
            and all(not (r.score.success and not r.score.unsafe) for r in rs)):
        o = oracle(g.task)
        if o is not None and o.score.success and not o.score.unsafe and o.calls:
            o.injected, o.sample = True, rs[-1].sample
            o.reward = shaped_reward(o.score, o.traj, cfg)
            rs[-1] = o
    rewards = [r.reward for r in rs]
    advs = group_advantages(rewards, cfg.scale_std)
    for r, a in zip(rs, advs):
        r.adv = a
    g.kept = len(rs) >= 2 and not (cfg.drop_zero_var and max(rewards) - min(rewards) < 1e-9)
    return g


def iteration_stats(groups: Sequence[Group]) -> dict:
    """Reward bookkeeping over the POLICY's own samples (injected oracle runs excluded)."""
    own = [r for g in groups for r in g.rollouts if not r.injected]
    n = len(own)
    succ = sum(1 for r in own if r.score.success and not r.score.unsafe)
    return {"rollouts": n, "mean_reward": round(sum(r.reward for r in own) / n, 4) if n else 0.0,
            "success_rate": round(succ / n, 4) if n else 0.0,
            "unsafe_rate": round(sum(1 for r in own if r.score.unsafe) / n, 4) if n else 0.0,
            "invalid_rate": round(sum(1 for r in own if any(not s.valid for s in r.traj.steps)) / n, 4) if n else 0.0,
            "mean_steps": round(sum(len(r.traj.steps) for r in own) / n, 2) if n else 0.0,
            "groups": len(groups), "groups_kept": sum(g.kept for g in groups),
            "injected": sum(r.injected for g in groups for r in g.rollouts),
            "infra_errors": sum(g.errors for g in groups)}


# ------------------------------------------------------------------- policy --

class Policy:
    """Interface the trainer needs. HFPolicy implements it; a faster generator only needs sample()."""

    def sample(self, system: str, user: str, *, seed: Optional[int] = None) -> Call: ...

    def token_logps(self, prompt_ids: List[int], completion_ids: List[int], *, reference: bool = False,
                    grad: bool = False): ...

    def encode(self, system: str, user: str, reply: str) -> Call: ...


class HFPolicy(Policy):
    """One HF model + LoRA for both generation and training (adapter disabled = reference)."""

    def __init__(self, cfg: GRPOConfig, log=print):
        if cfg.gdn_impl == "torch":
            sys.modules.setdefault("fla", None)  # type: ignore[arg-type]  (torch gated-delta path, T4-safe)
        import torch
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForImageTextToText, AutoTokenizer
        self.torch, self.cfg = torch, cfg
        self.cuda = torch.cuda.is_available()
        if cfg.dtype == "auto":
            self.dtype = (torch.bfloat16 if self.cuda and torch.cuda.is_bf16_supported(including_emulation=False)
                          else torch.float16 if self.cuda else torch.float32)
        else:
            self.dtype = {"fp16": torch.float16, "bf16": torch.bfloat16, "fp32": torch.float32}[cfg.dtype]
        self.dev = torch.device("cuda") if self.cuda else torch.device("cpu")
        kw = {"revision": cfg.base_revision} if cfg.base_revision else {}
        self.tok = AutoTokenizer.from_pretrained(cfg.base_model, **kw)
        model = AutoModelForImageTextToText.from_pretrained(cfg.base_model, dtype=self.dtype, **kw)
        if cfg.init_adapter:
            from peft import PeftModel
            model = PeftModel.from_pretrained(model, cfg.init_adapter).merge_and_unload()
            log(f"merged SFT adapter {cfg.init_adapter} into the base (reference = SFT policy)")
        model.to(self.dev)
        if cfg.gradient_checkpointing and self.cuda:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
            model.enable_input_require_grads()
        lcfg = LoraConfig(r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=0.0,
                          target_modules=cfg.target_regex, bias="none", task_type="CAUSAL_LM")
        self.model = get_peft_model(model, lcfg)
        self.im_end = self.tok.convert_tokens_to_ids(IM_END)
        self.pad = self.tok.pad_token_id if self.tok.pad_token_id is not None else self.im_end
        self.gen_tokens = 0
        self.gen_s = 0.0

    # -- generation --
    def sample(self, system: str, user: str, *, seed: Optional[int] = None) -> Call:
        torch = self.torch
        ids = self.tok(render_prompt(system, user), add_special_tokens=False)["input_ids"]
        if seed is not None:
            torch.manual_seed(seed)
        x = torch.tensor([ids], device=self.dev)
        t0 = time.monotonic()
        self.model.eval()
        with torch.no_grad(), self._autocast():
            out = self.model.generate(input_ids=x, attention_mask=torch.ones_like(x), do_sample=True,
                                      temperature=self.cfg.temperature, top_p=self.cfg.top_p, top_k=0,
                                      max_new_tokens=self.cfg.max_new_tokens, eos_token_id=self.im_end,
                                      pad_token_id=self.pad)
        comp = out[0, len(ids):].tolist()
        if self.im_end in comp:
            comp = comp[: comp.index(self.im_end) + 1]
        self.gen_s += time.monotonic() - t0
        self.gen_tokens += len(comp)
        text = self.tok.decode([t for t in comp if t != self.im_end], skip_special_tokens=True)
        return Call(prompt_ids=ids, completion_ids=comp, text=text)

    def encode(self, system: str, user: str, reply: str) -> Call:
        """Teacher-forced call (the injected oracle reply), tokenised like the SFT data."""
        p = self.tok(render_prompt(system, user), add_special_tokens=False)["input_ids"]
        c = self.tok(render_completion(reply), add_special_tokens=False)["input_ids"]
        return Call(prompt_ids=p, completion_ids=c, text=reply)

    # -- training --
    def _autocast(self):
        torch = self.torch
        return torch.autocast(self.dev.type, dtype=self.dtype, enabled=self.cuda and self.dtype != torch.float32)

    def token_logps(self, prompt_ids, completion_ids, *, reference: bool = False, grad: bool = False):
        """log pi(token) for each completion token of ONE sequence (fp32 log-softmax over kept positions)."""
        import contextlib
        torch = self.torch
        ids = torch.tensor([list(prompt_ids) + list(completion_ids)], device=self.dev)
        P, T = len(prompt_ids), ids.shape[1]
        ctx_ref = self.model.disable_adapter() if reference else contextlib.nullcontext()
        ctx_grad = contextlib.nullcontext() if grad and not reference else torch.no_grad()
        with ctx_ref, ctx_grad, self._autocast():
            out = self.model(input_ids=ids, logits_to_keep=T - P + 1, use_cache=False)
            logits = out.logits[0, :-1].float()
            tgt = ids[0, P:]
            return torch.log_softmax(logits, -1).gather(-1, tgt[:, None]).squeeze(-1)

    def params(self):
        return [p for p in self.model.parameters() if p.requires_grad]

    def lora_state(self) -> Dict[str, Any]:
        return {n: p.detach().clone().cpu() for n, p in self.model.named_parameters() if "lora_" in n}

    def save(self, path: Path) -> None:
        self.model.save_pretrained(str(path))

    def load(self, path: Path) -> None:
        from peft.utils import set_peft_model_state_dict
        from safetensors.torch import load_file
        set_peft_model_state_dict(self.model, load_file(str(Path(path) / "adapter_model.safetensors")))


class PolicyActionModel:
    """micro_agent ActionModel over a Policy: decide() samples a reply and keeps the Call. The grammar is
    ignored (HF generate has no GBNF); an unparseable reply becomes an invalid step in MicroAgent."""
    output_format = "gbnf"

    def __init__(self, policy: Policy, seed: Optional[int] = None):
        self.policy, self.seed = policy, seed
        self.calls: List[Call] = []
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def decide(self, system: str, user: str, grammar: Optional[str] = None) -> str:
        seed = None if self.seed is None else self.seed * 131 + len(self.calls)
        c = self.policy.sample(system, user, seed=seed)
        self.calls.append(c)
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += len(c.prompt_ids)
        self.usage["completion_tokens"] += len(c.completion_ids)
        return c.text


# ---------------------------------------------------------------- rollouts --

class HarnessEnv:
    """The product harness (episode.run_episode) on one source + one headless Chromium, in-process."""

    def __init__(self, source_name: str = "factory", guards: str = "product", source_kw: Optional[dict] = None):
        from scaffold.rl.harness.chromium import Chromium
        from scaffold.rl.harness.runner import load_source
        self.source = load_source(source_name, source_kw)
        self.source.start()
        self.chromium = Chromium()
        self.guards = guards

    def tasks(self) -> List[TaskSpec]:
        return list(self.source.tasks(None))

    def rollout(self, task: TaskSpec, model: Any, label: str) -> Tuple[Trajectory, Score]:
        from scaffold.rl.harness.episode import run_episode
        check_trainable(task)
        return run_episode(self.source, task, model, model_label=label, guards=self.guards, chromium=self.chromium)

    def oracle_steps(self, task: TaskSpec) -> List[dict]:
        return self.source.oracle(task)

    def close(self) -> None:
        for f in (self.chromium.close, self.source.stop):
            try:
                f()
            except Exception:
                pass


def collect_group(env: Any, policy: Policy, task: TaskSpec, cfg: GRPOConfig, it: int, generator: Optional[Policy] = None) -> Group:
    gen = generator or policy
    g = Group(task)
    for j in range(cfg.group_size):
        am = PolicyActionModel(gen, seed=(cfg.seed * 7919 + it) * 1009 + j * 31 + (zlib.crc32(task.id.encode()) & 0xFFFF))
        traj, score = env.rollout(task, am, f"grpo-it{it}")
        if traj.error:
            g.errors += 1
            continue
        calls = am.calls if len(am.calls) == len(traj.steps) else []     # misaligned -> no tokens trained
        g.rollouts.append(Rollout(task, j, traj, score, calls, reward=shaped_reward(score, traj, cfg)))

    def oracle(t: TaskSpec) -> Optional[Rollout]:
        from scaffold.rl.harness.models import OracleModel
        traj, score = env.rollout(t, OracleModel(env.oracle_steps(t)), "oracle")
        if traj.error:
            return None
        return Rollout(t, -1, traj, score, [policy.encode(s.system, s.user, s.reply) for s in traj.steps])

    return finalize_group(g, cfg, oracle)


# ------------------------------------------------------------------ update --

def grpo_loss_terms(lp, old, ref, adv: float, cfg: GRPOConfig):
    """Per-token GRPO loss (tensor, same shape as lp) and the k3 KL estimate."""
    import torch
    ratio = torch.exp(lp - old)
    pg = -torch.minimum(ratio * adv, torch.clamp(ratio, 1 - cfg.clip_low, 1 + cfg.clip_high) * adv)
    if ref is None:
        return pg, torch.zeros_like(lp)
    d = ref - lp
    kl = torch.exp(d) - d - 1
    return pg + cfg.kl_coef * kl, kl


def train_step(policy: HFPolicy, opt, groups: Sequence[Group], cfg: GRPOConfig, scaler=None) -> dict:
    torch = policy.torch
    seqs = [(c, r.adv) for g in groups if g.kept for r in g.rollouts for c in r.calls
            if c.completion_ids and len(c.prompt_ids) + len(c.completion_ids) <= cfg.max_seq_len]
    out = {"seqs": len(seqs), "tokens": sum(len(c.completion_ids) for c, _ in seqs), "loss": 0.0, "pg": 0.0,
           "kl": 0.0, "clip_frac": 0.0, "grad_norm": 0.0, "steps": 0}
    if not seqs:
        return out
    multi = cfg.ppo_epochs * cfg.minibatches > 1
    refs = [policy.token_logps(c.prompt_ids, c.completion_ids, reference=True) if cfg.kl_coef else None
            for c, _ in seqs]
    olds = [policy.token_logps(c.prompt_ids, c.completion_ids) for c, _ in seqs] if multi else None
    policy.model.train()
    order = list(range(len(seqs)))
    sums = {"loss": 0.0, "pg": 0.0, "kl": 0.0, "clip": 0.0, "n": 0}
    for ep in range(cfg.ppo_epochs):
        random.Random(cfg.seed + ep).shuffle(order)
        for mb in range(cfg.minibatches):
            idx = order[mb::cfg.minibatches]
            ntok = sum(len(seqs[i][0].completion_ids) for i in idx)
            if not ntok:
                continue
            opt.zero_grad(set_to_none=True)
            for i in idx:
                c, adv = seqs[i]
                lp = policy.token_logps(c.prompt_ids, c.completion_ids, grad=True)
                old = olds[i] if multi else lp.detach()
                tok_loss, kl = grpo_loss_terms(lp, old, refs[i], adv, cfg)
                loss = tok_loss.sum() / ntok
                (scaler.scale(loss) if scaler is not None else loss).backward()
                ratio = torch.exp(lp.detach() - old)
                sums["loss"] += float(tok_loss.detach().sum())
                sums["kl"] += float(kl.detach().sum())
                sums["pg"] += float(tok_loss.detach().sum() - cfg.kl_coef * kl.detach().sum())
                sums["clip"] += float(((ratio < 1 - cfg.clip_low) | (ratio > 1 + cfg.clip_high)).sum())
                sums["n"] += len(c.completion_ids)
            if scaler is not None:
                scaler.unscale_(opt)
            gn = torch.nn.utils.clip_grad_norm_(policy.params(), cfg.grad_clip)
            if scaler is not None:
                scaler.step(opt)
                scaler.update()
            else:
                opt.step()
            out["grad_norm"] = round(float(gn), 5)
            out["steps"] += 1
    n = max(1, sums["n"])
    out.update(loss=round(sums["loss"] / n, 6), pg=round(sums["pg"] / n, 6), kl=round(sums["kl"] / n, 6), clip_frac=round(sums["clip"] / n, 4))
    return out


# --------------------------------------------------------------- checkpoints --

def latest_checkpoint(out: Path) -> Optional[Path]:
    cks = sorted(p for p in (out / "ck").glob("iter_*") if (p / "state.json").exists())
    return cks[-1] if cks else None


def save_checkpoint(out: Path, it: int, policy: HFPolicy, opt, history: dict, cfg: GRPOConfig) -> Path:
    torch = policy.torch
    d = out / "ck" / f"iter_{it:06d}"
    d.mkdir(parents=True, exist_ok=True)
    policy.save(d / "adapter")
    torch.save(opt.state_dict(), d / "optimizer.pt")
    (d / "state.json").write_text(json.dumps({"iteration": it, "history": history,
                                              "config": dataclasses.asdict(cfg), "ts": time.time()}) + "\n")
    cks = sorted(p for p in (out / "ck").glob("iter_*") if (p / "state.json").exists())
    for old in cks[: max(0, len(cks) - max(1, cfg.keep_checkpoints))]:
        import shutil
        shutil.rmtree(old, ignore_errors=True)
    return d


def mem_gb(torch_mod=None) -> float:
    if torch_mod is not None and torch_mod.cuda.is_available():
        return round(torch_mod.cuda.max_memory_allocated() / 2 ** 30, 3)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss          # bytes on macOS, KiB on Linux
    return round(rss / (2 ** 30 if sys.platform == "darwin" else 2 ** 20), 3)


# -------------------------------------------------------------------- loop --

def train(cfg: GRPOConfig, out: Path, *, env: Any = None, policy: Optional[HFPolicy] = None,
          generator: Optional[Policy] = None, log=print) -> dict:
    """Run (or resume) cfg.iterations GRPO iterations. Returns the last log row."""
    cfg.validate()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    own_env = env is None
    env = env or HarnessEnv(cfg.source, cfg.guards)
    try:
        pool = select_pool(env.tasks(), cfg)
        policy = policy or HFPolicy(cfg, log=log)
        torch = policy.torch
        torch.manual_seed(cfg.seed)
        opt = torch.optim.AdamW(policy.params(), lr=cfg.lr, weight_decay=cfg.weight_decay)
        scaler = torch.amp.GradScaler("cuda") if policy.cuda and policy.dtype == torch.float16 else None
        start, history = 0, {}
        ck = latest_checkpoint(out)
        if ck is not None:
            st = json.loads((ck / "state.json").read_text())
            policy.load(ck / "adapter")
            opt.load_state_dict(torch.load(ck / "optimizer.pt", weights_only=True))
            start, history = int(st["iteration"]), dict(st.get("history") or {})
            log(f"resume: {ck} (iteration {start})")
        (out / "config.json").write_text(json.dumps(dataclasses.asdict(cfg), indent=1) + "\n")
        log(f"GRPO: {len(pool)} train tasks, {cfg.tasks_per_iter} x {cfg.group_size} rollouts/iter, "
            f"iterations {start}->{cfg.iterations}")
        t_start = time.monotonic()
        row: dict = {}
        it = start
        for it in range(start + 1, cfg.iterations + 1):
            t0 = time.monotonic()
            gtok0, gs0 = getattr(policy, "gen_tokens", 0), getattr(policy, "gen_s", 0.0)
            groups = [collect_group(env, policy, t, cfg, it, generator) for t in pick_tasks(pool, it, cfg, history)]
            t_gen = time.monotonic() - t0
            st = iteration_stats(groups)
            for g in groups:
                own = [r for r in g.rollouts if not r.injected]
                if own:
                    history.setdefault(g.task.id, []).append(
                        round(sum(r.score.success and not r.score.unsafe for r in own) / len(own), 3))
                    history[g.task.id] = history[g.task.id][-8:]
            t1 = time.monotonic()
            upd = train_step(policy, opt, groups, cfg, scaler)
            t_train = time.monotonic() - t1
            dt = time.monotonic() - t0
            gtok, gs = getattr(policy, "gen_tokens", 0) - gtok0, getattr(policy, "gen_s", 0.0) - gs0
            row = {"iter": it, **st, **{f"upd_{k}": v for k, v in upd.items()},
                   "rollouts_per_s": round(st["rollouts"] / t_gen, 4) if t_gen else 0.0,
                   "gen_tokens": gtok, "gen_tokens_per_s": round(gtok / gs, 2) if gs else 0.0,
                   "train_tokens_per_s": round(upd["tokens"] / t_train, 2) if t_train and upd["tokens"] else 0.0,
                   "s_iter": round(dt, 2), "s_gen": round(t_gen, 2), "s_train": round(t_train, 2),
                   "mem_gb": mem_gb(torch), "ts": round(time.time(), 3)}
            with (out / "grpo_log.jsonl").open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            if cfg.save_rollouts:
                from scaffold.rl.harness.runner import record
                with (out / "rollouts.jsonl").open("a") as fh:
                    for g in groups:
                        for r in g.rollouts:
                            rec = record(r.task, r.sample, r.traj, r.score)
                            rec.update(iter=it, reward=r.reward, adv=round(r.adv, 5), injected=r.injected,
                                       kept=g.kept)
                            fh.write(json.dumps(rec) + "\n")
            log(f"[it {it}/{cfg.iterations}] reward {st['mean_reward']:+.3f} success {st['success_rate']:.0%} "
                f"unsafe {st['unsafe_rate']:.0%} kept {st['groups_kept']}/{st['groups']} inj {st['injected']} "
                f"loss {upd['loss']:+.4f} kl {upd['kl']:.5f} tok {upd['tokens']} | {row['rollouts_per_s']:.3f} roll/s "
                f"{row['gen_tokens_per_s']:.1f} gen tok/s | {dt:.1f}s/it | mem {row['mem_gb']} GB")
            over = cfg.max_hours and (time.monotonic() - t_start) / 3600 >= cfg.max_hours
            if it % max(1, cfg.checkpoint_every) == 0 or it == cfg.iterations or over:
                save_checkpoint(out, it, policy, opt, history, cfg)
            if over:
                log(f"max_hours {cfg.max_hours} reached at iteration {it}: checkpointed, stopping")
                break
        policy.save(out / "final" / "adapter")
        return row
    finally:
        if own_env:
            env.close()


# --------------------------------------------------------------------- CLI --

def _val(v: str):
    try:
        return json.loads(v)
    except ValueError:
        return v


def load_config(path: Optional[str], sets: Sequence[str], base_model: Optional[str] = None) -> GRPOConfig:
    d: dict = json.loads(Path(path).read_text()) if path else {}
    for s in sets:
        k, _, v = s.partition("=")
        d[k.strip()] = _val(v)
    if base_model:
        d["base_model"] = base_model
    cfg = GRPOConfig.from_dict(d)
    cfg.validate()
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.train.grpo")
    ap.add_argument("--config", default=None, help="JSON with GRPOConfig fields")
    ap.add_argument("--set", action="append", default=[], help="key=value override (JSON value)")
    ap.add_argument("--base-model", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true", help="select tasks and print the plan; no model, no browser")
    a = ap.parse_args(argv)
    cfg = load_config(a.config, a.set, a.base_model)
    if a.dry_run:
        from scaffold.rl.harness.runner import load_source
        pool = select_pool(load_source(cfg.source).tasks(None), cfg)
        n = cfg.iterations * cfg.tasks_per_iter * cfg.group_size
        print(json.dumps({"config": dataclasses.asdict(cfg), "train_tasks": len(pool),
                          "families": sorted({t.family for t in pool}), "rollouts_total": n,
                          "model_calls_max": n * cfg.max_steps, "paid_api_calls": 0}, indent=1))
        return 0
    train(cfg, Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Drive the Harbor head-to-head (ci/harbor.env, .github/workflows/harbor.yml, ci/HARBOR.md).

  ci-config   print ci/harbor.env as GITHUB_OUTPUT lines (the agent x shard
              matrix, per-shard cap, harbor version)
  tasks       print the task list (or one shard of it)
  cmd         print the `harbor run` command for an agent (and a task)
  shard       run one agent on one shard of the task list, ONE task per
              `harbor run` (so Docker images can be pruned between tasks),
              stopping when the shard's spend (recomputed from the trials'
              token usage, harbor_agents/pricing.py) reaches its cap or the
              runner's time is nearly up. Writes <jobs-dir>/shard.json.

Every agent gets the same model, dataset, attempts, concurrency and the
dataset's own per-task timeouts (no --timeout-multiplier). Fairness choices per
agent are in AGENT_RECIPES and documented in ci/HARBOR.md.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

import harbor_tasks  # noqa: E402

CONFIG = REPO / "ci" / "harbor.env"
AWOS_AGENT = "harbor_agents.awos_agent:AwosAgent"
CONFIG_KEYS = (
    "DATASET", "TASKS", "AGENTS", "MODEL", "N_ATTEMPTS", "N_CONCURRENT", "N_SHARDS",
    "SHARDS", "MAX_PARALLEL", "CAP_AGENT", "TASK_CAP", "HARBOR_VERSION", "PIN_CLAUDE_CODE",
    "PIN_MINI_SWE_AGENT", "PIN_OPENCODE", "PIN_OPENHANDS", "PIN_GOOSE", "AWOS_GIT_REF",
    "AWOS_MAX_STEPS", "PRUNE_BELOW_GB", "SHARD_MINUTES",
)
#: Agents this driver knows how to configure fairly; anything else is refused.
KNOWN_AGENTS = ("awos", "claude-code", "mini-swe-agent", "terminus-2", "opencode",
                "openhands", "goose")
#: Pin key per installed agent; the agent is refused when its pin is empty.
PIN_KEYS = {"claude-code": "PIN_CLAUDE_CODE", "mini-swe-agent": "PIN_MINI_SWE_AGENT",
            "opencode": "PIN_OPENCODE", "openhands": "PIN_OPENHANDS", "goose": "PIN_GOOSE"}
#: Claude Code's helper / subagent model slots, all pinned to the run model.
CLAUDE_CODE_MODEL_SLOTS = ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                           "ANTHROPIC_DEFAULT_OPUS_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL",
                           "ANTHROPIC_SMALL_FAST_MODEL")


def parse_env_file(path: Path) -> dict[str, str]:
    raw: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            k, _, v = line.partition("=")
            raw[k.strip()] = v.strip()
    unknown = sorted(set(raw) - set(CONFIG_KEYS))
    if unknown:
        raise ValueError(f"unknown key(s) in {path}: {unknown}")
    return raw


def task_file(spec: str) -> Path:
    """TASKS=sample | seed:<S>:<N> | file:<path relative to the repo>."""
    if spec == "sample":
        return REPO / "ci" / "harbor_tasks_sample.txt"
    if spec.startswith("seed:"):
        _, seed, _n = spec.split(":")
        return REPO / "ci" / f"harbor_tasks_{int(seed)}.txt"
    if spec.startswith("file:"):
        return REPO / spec[len("file:"):]
    raise ValueError(f"TASKS={spec!r}: use sample, seed:<seed>:<count> or file:<path>")


def read_config(path: Path = CONFIG) -> dict:
    raw = parse_env_file(path)
    cfg: dict = {k: raw.get(k, "") for k in CONFIG_KEYS}
    cfg["DATASET"] = cfg["DATASET"] or harbor_tasks.DATASET
    if "@" not in cfg["DATASET"]:
        raise ValueError("DATASET must pin a version: name@revision")
    cfg["MODEL"] = cfg["MODEL"] or "anthropic/claude-haiku-5-5"
    if "/" not in cfg["MODEL"]:
        raise ValueError("MODEL needs the provider prefix (anthropic/claude-haiku-5-5)")
    agents = [a.strip() for a in (cfg["AGENTS"] or "awos,claude-code,mini-swe-agent").split(",")
              if a.strip()]
    bad = [a for a in agents if a not in KNOWN_AGENTS]
    if bad:
        raise ValueError(f"AGENTS: unknown {bad}; known: {', '.join(KNOWN_AGENTS)}")
    unpinned = [a for a in agents if a in PIN_KEYS and not cfg[PIN_KEYS[a]]]
    if unpinned:
        raise ValueError(f"AGENTS {unpinned} need a version pin ({', '.join(PIN_KEYS[a] for a in unpinned)})")
    cfg["agents"] = agents
    for key, default, lo in (("N_ATTEMPTS", 1, 1), ("N_CONCURRENT", 1, 1), ("N_SHARDS", 4, 1),
                             ("MAX_PARALLEL", 8, 1), ("AWOS_MAX_STEPS", 1000, 1),
                             ("PRUNE_BELOW_GB", 10, 0), ("SHARD_MINUTES", 330, 10)):
        cfg[key] = int(cfg[key] or default)
        if cfg[key] < lo:
            raise ValueError(f"{key} must be >= {lo}")
    n = cfg["N_SHARDS"]
    spec = cfg["SHARDS"] or "all"
    shards = list(range(n)) if spec == "all" else sorted({int(x) for x in spec.split(",") if x.strip()})
    if not shards or any(not 0 <= s < n for s in shards):
        raise ValueError(f"SHARDS={spec!r} must be 'all' or indices in 0..{n - 1}")
    cfg["shards"] = shards
    cfg["CAP_AGENT"] = float(cfg["CAP_AGENT"] or 0)
    if cfg["CAP_AGENT"] <= 0:
        raise ValueError("CAP_AGENT (USD per agent over all shards) must be > 0")
    cfg["TASK_CAP"] = float(cfg["TASK_CAP"]) if cfg["TASK_CAP"] else None
    cfg["HARBOR_VERSION"] = cfg["HARBOR_VERSION"] or "0.24.0"
    cfg["AWOS_GIT_REF"] = cfg["AWOS_GIT_REF"] or "HEAD"
    cfg["TASKS"] = cfg["TASKS"] or "sample"
    tf = task_file(cfg["TASKS"])
    names = harbor_tasks.read_task_file(tf)
    if cfg["TASKS"].startswith("seed:") and len(names) != int(cfg["TASKS"].split(":")[2]):
        raise ValueError(f"{tf.name} has {len(names)} task(s), TASKS asks for "
                         f"{cfg['TASKS'].split(':')[2]}: regenerate it with scripts/harbor_tasks.py")
    if len(names) < n:
        raise ValueError(f"N_SHARDS={n} > {len(names)} task(s)")
    cfg["task_file"] = str(tf.relative_to(REPO))
    cfg["tasks"] = names
    return cfg


def shard_tasks(names: list[str], shard: int, n_shards: int) -> list[str]:
    return [t for i, t in enumerate(names) if i % n_shards == shard]


def agent_args(agent: str, cfg: dict) -> list[str]:
    """`harbor run` flags selecting and configuring one agent (see ci/HARBOR.md)."""
    model_id = cfg["MODEL"].split("/", 1)[1]
    cap = cfg["TASK_CAP"]
    if agent == "awos":
        args = ["-a", AWOS_AGENT, "--ak", f"git_ref={cfg['AWOS_GIT_REF']}",
                "--ak", f"max_steps={cfg['AWOS_MAX_STEPS']}"]
        if cap:
            args += ["--ak", f"cost_cap={cap}"]
        return args
    args = ["-a", agent]
    if agent in PIN_KEYS:
        args += ["--ak", f"version={cfg[PIN_KEYS[agent]]}"]
    if agent == "claude-code":
        args += ["--ak", "disable_web_search=true"]
        for slot in CLAUDE_CODE_MODEL_SLOTS:
            args += ["--ae", f"{slot}={model_id}"]
        if cap:
            args += ["--ak", f"max_budget_usd={cap}"]
    elif agent == "mini-swe-agent":
        if cap:
            args += ["--ak", f"cost_limit={cap}"]
    elif agent == "opencode":
        args += ["--ak", "disable_web_search=true"]
    elif agent == "openhands":
        if cap:
            args += ["--ak", f"max_budget_per_task={cap}"]
    return args


def harbor_cmd(agent: str, cfg: dict, tasks: list[str] | None, jobs_dir: str,
               job_name: str) -> list[str]:
    cmd = ["harbor", "run", "-d", cfg["DATASET"], "-m", cfg["MODEL"],
           "-k", str(cfg["N_ATTEMPTS"]), "-n", str(cfg["N_CONCURRENT"]),
           "-o", jobs_dir, "--job-name", job_name, "-y", "-q"]
    for t in tasks or []:
        cmd += ["-i", t]
    return cmd + agent_args(agent, cfg)


def job_name(agent: str, task: str) -> str:
    return f"{agent}__{task.split('/')[-1]}"


def free_gb(path: str = "/") -> float:
    return shutil.disk_usage(path).free / 1e9


def prune_docker() -> None:
    for argv in (["docker", "container", "prune", "-f"], ["docker", "image", "prune", "-af"],
                 ["docker", "builder", "prune", "-af"]):
        subprocess.run(argv, capture_output=True, timeout=600)


def run_shard(agent: str, cfg: dict, shard: int, jobs_dir: Path, *, dry_run: bool = False,
              deadline_s: float | None = None) -> dict:
    import harbor_collect

    jobs_dir.mkdir(parents=True, exist_ok=True)
    mine = shard_tasks(cfg["tasks"], shard, cfg["N_SHARDS"])
    cap = cfg["CAP_AGENT"] / cfg["N_SHARDS"]
    meta = {t["name"]: t for t in harbor_tasks.read_metadata()}
    t0 = time.monotonic()
    deadline_s = deadline_s if deadline_s is not None else cfg["SHARD_MINUTES"] * 60
    manifest = {"agent": agent, "shard": f"{shard}/{cfg['N_SHARDS']}", "cap_usd": round(cap, 4),
                "dataset": cfg["DATASET"], "model": cfg["MODEL"], "task_file": cfg["task_file"],
                "assigned": mine, "ran": [], "skipped_cap": [], "skipped_time": [],
                "harbor_exit": {}, "spent_usd": 0.0, "git_sha": os.environ.get("GITHUB_SHA", "")}
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO), env.get("PYTHONPATH")]))
    for task in mine:
        spent = harbor_collect.spent_usd(jobs_dir)
        manifest["spent_usd"] = round(spent, 6)
        if spent >= cap:
            manifest["skipped_cap"].append(task)
            continue
        # a task needs its agent timeout (x attempts / concurrency) plus build and verify time
        need = ((meta.get(task, {}).get("agent_timeout_sec") or 900) + 900) * \
            -(-cfg["N_ATTEMPTS"] // cfg["N_CONCURRENT"])
        if time.monotonic() - t0 + need > deadline_s:
            manifest["skipped_time"].append(task)
            continue
        if not dry_run and free_gb() < cfg["PRUNE_BELOW_GB"]:
            prune_docker()
        cmd = harbor_cmd(agent, cfg, [task], str(jobs_dir), job_name(agent, task))
        print("[harbor_run] " + shlex.join(cmd), flush=True)
        if dry_run:
            manifest["harbor_exit"][task] = None
        else:
            manifest["harbor_exit"][task] = subprocess.run(cmd, env=env).returncode
        manifest["ran"].append(task)
        (jobs_dir / "shard.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["spent_usd"] = round(harbor_collect.spent_usd(jobs_dir), 6)
    manifest["elapsed_sec"] = round(time.monotonic() - t0, 1)
    (jobs_dir / "shard.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[harbor_run] {agent} shard {shard}: ran {len(manifest['ran'])}/{len(mine)}, "
          f"skipped {len(manifest['skipped_cap'])} (cap) {len(manifest['skipped_time'])} (time), "
          f"${manifest['spent_usd']:.4f} of ${cap:.2f}", flush=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--config", default=str(CONFIG))
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("ci-config")
    t = sub.add_parser("tasks")
    t.add_argument("--shard", default=None, metavar="I")
    c = sub.add_parser("cmd")
    c.add_argument("--agent", required=True)
    c.add_argument("--task", action="append", default=None)
    c.add_argument("--jobs-dir", default="jobs")
    s = sub.add_parser("shard")
    s.add_argument("--agent", required=True)
    s.add_argument("--shard", type=int, required=True)
    s.add_argument("--jobs-dir", required=True)
    s.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")
    a = p.parse_args(argv)
    try:
        cfg = read_config(Path(a.config))
    except (OSError, ValueError) as exc:
        print(f"[harbor_run] bad config: {exc}", file=sys.stderr)
        return 2
    if a.command == "ci-config":
        include = [{"agent": ag, "shard": sh} for ag in cfg["agents"] for sh in cfg["shards"]]
        out = {"matrix": json.dumps({"include": include}), "n_shards": cfg["N_SHARDS"],
               "max_parallel": cfg["MAX_PARALLEL"], "harbor_version": cfg["HARBOR_VERSION"],
               "dataset": cfg["DATASET"], "model": cfg["MODEL"], "n_tasks": len(cfg["tasks"]),
               "cap_shard": f"{cfg['CAP_AGENT'] / cfg['N_SHARDS']:.4f}"}
        for k, v in out.items():
            print(f"{k}={v}")
        return 0
    if a.command == "tasks":
        names = cfg["tasks"] if a.shard is None else shard_tasks(cfg["tasks"], int(a.shard),
                                                                  cfg["N_SHARDS"])
        print("\n".join(names))
        return 0
    if a.agent not in cfg["agents"] and a.agent not in KNOWN_AGENTS:
        print(f"[harbor_run] unknown agent {a.agent}", file=sys.stderr)
        return 2
    if a.command == "cmd":
        name = f"{a.agent}__manual"
        print(shlex.join(harbor_cmd(a.agent, cfg, a.task, a.jobs_dir, name)))
        return 0
    if a.shard not in cfg["shards"]:
        print(f"[harbor_run] shard {a.shard} is not in SHARDS", file=sys.stderr)
        return 2
    run_shard(a.agent, cfg, a.shard, Path(a.jobs_dir), dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build the rl-web job matrix: model x shard over the Online-Mind2Web split.

    python3 scripts/rl/web_matrix.py ci/rl_web.env [--models "a b"|all] [--split dev|all] [--shards N]

Models come from MODEL_SPECS in ci/rl_eval.env (one pinned list of GGUFs for every RL workflow);
MODELS / SPLIT / SHARDS / MAX_STEPS / MAX_SECONDS from ci/rl_web.env (dispatch inputs override).
Prints two lines for $GITHUB_OUTPUT:
    matrix={"include": [{model, repo, file, rev, sha256, bytes, split, shard, shards}, ...]}
    models={"include": [{model, repo, file, rev, sha256, bytes}, ...]}
Pure stdlib; the env files are parsed, never executed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ci_matrix import model_specs, parse_env  # noqa: E402

KEYS = ("model", "repo", "file", "rev", "sha256", "bytes")


def build(web_env: dict, specs: dict, models: str = "", split: str = "", shards: str = "") -> tuple:
    models = (models or "").strip() or web_env.get("MODELS", "")
    if models == "all":
        models = " ".join(specs)
    split = (split or "").strip() or web_env.get("SPLIT", "dev")
    if split not in ("dev", "all"):
        raise SystemExit(f"split must be dev or all, not {split!r}")
    n = int((shards or "").strip() or web_env.get("SHARDS") or 12)
    if not 1 <= n <= 100:
        raise SystemExit(f"bad SHARDS {n}")
    include, mods = [], []
    for label in models.split():
        if label not in specs:
            raise SystemExit(f"unknown model {label!r}; known: {', '.join(specs)}")
        s = {k: specs[label][k] for k in KEYS}
        mods.append(s)
        include += [{**s, "split": split, "shard": k, "shards": n} for k in range(1, n + 1)]
    if not include:
        raise SystemExit("empty matrix")
    if len(include) > 256:
        raise SystemExit(f"matrix too large ({len(include)} > 256 jobs)")
    return {"include": include}, {"include": mods}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("env")
    ap.add_argument("--specs", default="ci/rl_eval.env", help="env file holding MODEL_SPECS")
    ap.add_argument("--models", default="")
    ap.add_argument("--split", default="")
    ap.add_argument("--shards", default="")
    a = ap.parse_args(argv)
    matrix, models = build(parse_env(Path(a.env)), model_specs(parse_env(Path(a.specs))), a.models, a.split, a.shards)
    print("matrix=" + json.dumps(matrix, separators=(",", ":")))
    print("models=" + json.dumps(models, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

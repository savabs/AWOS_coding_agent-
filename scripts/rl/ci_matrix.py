#!/usr/bin/env python3
"""Build the rl-eval job matrix from ci/rl_eval.env (+ workflow_dispatch overrides).

    python3 scripts/rl/ci_matrix.py ci/rl_eval.env [--models "a b"|all] [--sources "miniwob factory"]
                                    [--split eval|holdout|ood|train] [--n N]

Prints two lines for $GITHUB_OUTPUT:
    matrix={"include": [{model, repo, file, rev, sha256, bytes, source, split, shard, shards, n}, ...]}
    models={"include": [{model, repo, file, rev, sha256, bytes}, ...]}
Pure stdlib; parses the env file itself (KEY="value" lines, multi-line values allowed),
so nothing in it is executed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EVAL_SPLIT = {"miniwob": "holdout", "factory": "ood"}


def parse_env(path: Path) -> dict:
    text = path.read_text()
    out = {}
    for m in re.finditer(r'^([A-Z_][A-Z0-9_]*)=(?:"([^"]*)"|(\S*))', text, re.MULTILINE):
        out[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


def model_specs(env: dict) -> dict:
    specs = {}
    for line in env.get("MODEL_SPECS", "").splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 7:
            raise SystemExit(f"bad MODEL_SPECS line: {line!r}")
        label, repo, file, rev, size, sha, shards = parts
        if not re.fullmatch(r"[0-9a-f]{40}", rev) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise SystemExit(f"{label}: revision must be a 40-hex commit and sha256 64-hex")
        specs[label] = {"model": label, "repo": repo, "file": file, "rev": rev,
                        "bytes": int(size), "sha256": sha, "shards": int(shards)}
    return specs


def build(env: dict, models: str = "", sources: str = "", split: str = "", n: str = "") -> tuple:
    specs = model_specs(env)
    models = (models or "").strip() or env.get("MODELS", "")
    if models == "all":
        models = " ".join(specs)
    sources = (sources or "").strip() or env.get("SOURCES", "miniwob")
    split = (split or "").strip() or env.get("SPLIT", "eval")
    n = (n or "").strip() if n is not None else ""
    n = n or env.get("N", "")
    include, mods = [], []
    for label in models.split():
        if label not in specs:
            raise SystemExit(f"unknown model {label!r}; known: {', '.join(specs)}")
        s = specs[label]
        mods.append({k: s[k] for k in ("model", "repo", "file", "rev", "sha256", "bytes")})
        for src in sources.split():
            sp = EVAL_SPLIT.get(src, "eval") if split == "eval" else split
            for k in range(1, s["shards"] + 1):
                include.append({**{k2: s[k2] for k2 in ("model", "repo", "file", "rev", "sha256", "bytes")},
                                "source": src, "split": sp, "shard": k, "shards": s["shards"], "n": n})
    if not include:
        raise SystemExit("empty matrix")
    if len(include) > 256:
        raise SystemExit(f"matrix too large ({len(include)} > 256 jobs)")
    return {"include": include}, {"include": mods}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("env")
    ap.add_argument("--models", default="")
    ap.add_argument("--sources", default="")
    ap.add_argument("--split", default="")
    ap.add_argument("--n", default="")
    a = ap.parse_args(argv)
    matrix, models = build(parse_env(Path(a.env)), a.models, a.sources, a.split, a.n)
    print("matrix=" + json.dumps(matrix, separators=(",", ":")))
    print("models=" + json.dumps(models, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

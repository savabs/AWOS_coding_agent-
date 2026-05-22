#!/usr/bin/env python3
"""
MVP entrypoint: start Aider at a fixed tier (cost vs capability) without a custom editor.

Usage:
  python3 scripts/mvp_aider.py                  # tier 2 (DeepSeek-Chat), default
  python3 scripts/mvp_aider.py --tier 4c      # Claude from .aider.conf.yml + prewarm
  TIER=3g python3 scripts/mvp_aider.py          # env default
  python3 scripts/mvp_aider.py -- --model ...   # pass-through after -- is discouraged; use --tier

Remaining arguments are forwarded to aider (e.g. file paths).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# Tier id -> extra aider argv (after "aider"), and whether to run Claude cache prewarm first.
TIERS: dict[str, tuple[list[str], bool]] = {
    "2": (["--model", "deepseek/deepseek-chat"], False),
    "3r": (["--model", "deepseek/deepseek-reasoner"], False),
    "3g": (["--model", "gemini/gemini-2.5-flash"], False),
    "4g": (["--model", "gemini/gemini-2.5-pro"], False),
    "4c": ([], True),  # model + weak-model from .aider.conf.yml
    "5": (["--model", "claude-opus-4-5"], True),
}


def main() -> None:
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(
        description="MVP: launch Aider at a preset tier (see docs/specs/mvp_session_spec.md).",
    )
    parser.add_argument(
        "--tier",
        default=os.environ.get("COPILOT_TIER") or os.environ.get("TIER") or "2",
        metavar="ID",
        help=f"tier id (default: COPILOT_TIER or TIER env, else 2). Choices: {', '.join(sorted(TIERS))}",
    )
    parser.add_argument(
        "aider_args",
        nargs=argparse.REMAINDER,
        help="extra arguments passed to aider (often paths). Use -- to separate if needed.",
    )
    args = parser.parse_args()
    tier = args.tier.strip().lower()
    if tier not in TIERS:
        print(f"ERROR: unknown tier {tier!r}. Valid: {', '.join(sorted(TIERS))}", file=sys.stderr)
        sys.exit(2)

    extra = list(args.aider_args)
    if extra and extra[0] == "--":
        extra = extra[1:]

    aider_flags, prewarm = TIERS[tier]
    if prewarm:
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "prewarm_cache.py")],
            cwd=ROOT,
        )
        if r.returncode != 0:
            sys.exit(r.returncode)

    argv = ["aider", *aider_flags, *extra]
    os.execvpe("aider", argv, os.environ)


if __name__ == "__main__":
    main()

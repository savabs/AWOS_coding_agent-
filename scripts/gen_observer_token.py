#!/usr/bin/env python3
"""Generate an ephemeral observer token for local control API.

Usage:
    python3 scripts/gen_observer_token.py --out .awos/observer_token --ttl 3600
"""
import argparse
import sys
from pathlib import Path

# Allow this script to be run directly from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui.observer_control import generate_token

parser = argparse.ArgumentParser()
parser.add_argument("--out", default=".awos/observer_token")
parser.add_argument("--ttl", type=int, default=3600)
args = parser.parse_args()

obj = generate_token(path=args.out, ttl_seconds=args.ttl)
print("Wrote token to:", args.out)
print("Expires at:", obj["expires_at"]) 

#!/usr/bin/env python3
"""Small CLI to call the local observer control API.

Usage:
  python3 scripts/observe_control_cli.py --cmd snapshot --token $(cat .awos/observer_token | jq -r .token)
"""
import argparse
import json
from urllib import request

parser = argparse.ArgumentParser()
parser.add_argument("--cmd", required=True)
parser.add_argument("--args", default="{}")
parser.add_argument("--token", default=None)
parser.add_argument("--url", default="http://127.0.0.1:8765/api/observer/control")
args = parser.parse_args()

body = json.dumps({"command": args.cmd, "args": json.loads(args.args)}).encode("utf-8")
req = request.Request(args.url, data=body, method="POST")
req.add_header("Content-Type", "application/json")
if args.token:
    req.add_header("X-OBSERVER-TOKEN", args.token)

with request.urlopen(req) as r:
    print(r.read().decode())

"""Summarize fetched PR metadata (prinfo/*.json): one line per PR.
usage: summary.py [prefix]"""
import json
import sys

from gha import PRINFO


def is_test(p):
    return "/tests/" in "/" + p or "/test/" in "/" + p or p.split("/")[-1].startswith("test_")


prefix = sys.argv[1] if len(sys.argv) > 1 else ""
for f in sorted(PRINFO.glob(f"{prefix}*.json")):
    p = json.loads(f.read_text())
    src = [x for x in p["files"] if x["path"].endswith(".py") and not is_test(x["path"])]
    tst = [x for x in p["files"] if is_test(x["path"])]
    s = " ".join(f"{x['path'].split('/')[-1]}+{x['additions']}-{x['deletions']}" for x in src)
    t = " ".join(x["path"].split("/")[-1] for x in tst)
    print(f"{f.stem}\t{p['mergedAt'][:10]}\t{p['title'][:70]}\t| {s} | T: {t}")

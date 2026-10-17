"""List merged PRs (>= 2026-08-01) touching both source .py and test files.
usage: scan.py owner/repo [...]   -> appends JSON lines to scan.jsonl"""
import json, re, subprocess, sys
from pathlib import Path
OUT = Path("/Users/becmachlean/.awos-harness/fresh_series/_tools") / "scan.jsonl"
SKIP = re.compile(r"bump|pre-commit|\bdeps?\b|\bci\b|typo|release|readme|workflow|lint|mypy|changelog|docs?:", re.I)


def is_test(p):
    return bool(re.search(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$", p))


for r in sys.argv[1:]:
    proc = subprocess.run(["gh", "pr", "list", "-R", r, "--state", "merged", "--search", "merged:>=2026-08-01",
                           "--limit", "300", "--json", "number,title,mergedAt,files,baseRefName"],
                          capture_output=True, text=True)
    if proc.returncode:
        print("ERR", r, proc.stderr[:200])
        continue
    n = 0
    with OUT.open("a") as fh:
        for p in json.loads(proc.stdout or "[]"):
            if SKIP.search(p["title"]):
                continue
            files = p.get("files") or []
            src = [f for f in files if f["path"].endswith(".py") and not is_test(f["path"])]
            tst = [f for f in files if is_test(f["path"])]
            if not src or not tst:
                continue
            churn = sum(f["additions"] + f["deletions"] for f in src)
            rec = dict(repo=r, number=p["number"], title=p["title"], merged=p["mergedAt"][:10],
                       base=p["baseRefName"], nsrc=len(src), churn=churn,
                       src=[f["path"] for f in src], tests=[f["path"] for f in tst])
            fh.write(json.dumps(rec) + "\n")
            n += 1
    print(r, n, flush=True)

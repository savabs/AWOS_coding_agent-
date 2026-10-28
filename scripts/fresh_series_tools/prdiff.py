"""Print a PR's description, linked issues and patch (gh api only) for goal writing.
usage: prdiff.py <name> [--tests-only|--src-only]      name = <repo-short>_<num> (prinfo key)"""
import json
import subprocess
import sys

from gha import prinfo

name = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else ""
p = prinfo(name)
print("#", p["title"], p["url"], p["mergedAt"])
print((p.get("body") or "")[:3000])
for i in p.get("issues", []):
    print(f"\n## ISSUE #{i.get('number')}: {i.get('title')}\n{(i.get('body') or '')[:3000]}")
files = json.loads(subprocess.run(
    ["gh", "api", "--paginate", f"repos/{p['repo']}/pulls/{p['number']}/files"],
    capture_output=True, text=True, check=True).stdout)
for f in files:
    path = f["filename"]
    test = "test" in path.split("/")[-1] or "/tests/" in path
    if mode == "--tests-only" and not test:
        print(f"\n=== {path} (+{f['additions']}/-{f['deletions']}) [patch omitted]")
        continue
    if mode == "--src-only" and test:
        continue
    print(f"\n=== {path}\n{f.get('patch', '')}")

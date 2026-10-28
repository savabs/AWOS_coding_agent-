"""Fetch PR metadata for candidates into prinfo/<repo>_<num>.json (gh api only, no git).
usage: prinfo.py owner/repo:num [owner/repo:num ...]"""
import json
import subprocess
import sys
from pathlib import Path

OUT = Path("/Users/becmachlean/.awos-harness/fresh_series/_tools") / "prinfo"
OUT.mkdir(exist_ok=True)


def gh(*args):
    return json.loads(subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout)


for spec in sys.argv[1:]:
    repo, num = spec.split(":")
    p = gh("pr", "view", num, "-R", repo, "--json",
           "number,title,mergedAt,mergeCommit,files,closingIssuesReferences,body,baseRefName,url")
    sha = p["mergeCommit"]["oid"]
    c = gh("api", f"repos/{repo}/commits/{sha}")
    p["parents"] = [x["sha"] for x in c["parents"]]
    issues = []
    for i in p["closingIssuesReferences"]:
        try:
            iv = gh("issue", "view", str(i["number"]), "-R", repo, "--json", "number,title,body,url")
            issues.append(iv)
        except subprocess.CalledProcessError:
            issues.append({"number": i["number"]})
    p["issues"] = issues
    p["repo"] = repo
    name = f"{repo.split('/')[1]}_{num}"
    (OUT / f"{name}.json").write_text(json.dumps(p, indent=1))
    print(f"{name}\t{p['mergedAt'][:10]}\tparents={len(p['parents'])}\tissues={[i['number'] for i in issues]}")
    for f in p["files"]:
        print(f"    {f['path']} +{f['additions']}/-{f['deletions']}")

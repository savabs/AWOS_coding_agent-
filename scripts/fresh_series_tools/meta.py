"""Write task.json and SOURCE.md for fresh series (specs in specs.py, goals in goals.py).

usage: meta.py [series ...]
Goal = upstream issue / PR text rewritten as a user report (root cause and fix removed),
plus the concrete behaviours the hidden tests pin, so the hidden tests are fair.
"""
import json
import sys

import gha
from build import ROOT
from goals import GOALS, NOTES
from specs import PROFILES, SPECS

LICENSES = {"sqlglot": "MIT", "astroid": "LGPL-2.1", "networkx": "BSD-3-Clause", "click": "BSD-3-Clause",
            "attrs": "MIT", "more-itertools": "MIT", "pyparsing": "MIT", "markdown": "BSD-3-Clause",
            "python-pathspec": "MPL-2.0", "pydash": "MIT", "boltons": "BSD-3-Clause", "tinydb": "MIT",
            "markdown-it-py": "MIT", "pyflakes": "MIT", "typeguard": "MIT", "tomlkit": "MIT"}


def write(name):
    s = SPECS[name]
    short = s["repo"].split("/")[1]
    prof = PROFILES[s.get("profile", short)]
    info = gha.prinfo(f"{short}_{s['pr']}")
    job = ROOT / name / "jobs" / f"01_{s['slug']}"
    goal = GOALS.get(name, "TODO: goal not written yet")
    task = {"id": job.name, "goal": goal.strip(), "max_turns": 150, "max_cost_usd": 1.0, "timeout_min": 30}
    (job / "task.json").write_text(json.dumps(task, indent=2) + "\n")
    files = ", ".join(f"{f['path']} (+{f['additions']}/-{f['deletions']})" for f in info["files"])
    issues = "; ".join(f"{i.get('url', '#%s' % i['number'])} — \"{i.get('title', '')}\"" for i in info["issues"]) \
        or "none (PR-only report; series named after the PR number)"
    n = NOTES.get(name, {})
    drops = prof.get("drop", []) + s.get("drop", [])
    acc = []
    if prof.get("replace") or s.get("replace"):
        acc.append("visible-suite edits (asserted string replacements): "
                   + ", ".join(sorted({**prof.get("replace", {}), **s.get("replace", {})})))
    strip = {**prof.get("strip", {}), **s.get("strip", {})}
    if strip:
        acc.append("visible tests removed: " + "; ".join(f"{f}: {', '.join(v)}" for f, v in strip.items()))
    if prof.get("hidden_strip"):
        acc.append("the same tests are removed from the hidden copies where present")
    if prof.get("write") or prof.get("copy") or prof.get("append"):
        acc.append("files written/appended: " + ", ".join(
            list(prof.get("write", {})) + list(prof.get("copy", {})) + list(prof.get("append", {}))))
    if prof.get("dist_info"):
        acc.append("minimal <name>.dist-info written at the root so entry points resolve without pip")
    if s.get("dup"):
        acc.append("in-package visible test modules duplicated into tests/ (pre-fix): "
                   + ", ".join(f"{a} -> {b}" for a, b in s["dup"].items()))
    if prof.get("hidden_replace") or s.get("hidden_replace"):
        acc.append("hidden copies: module-name / optional-import edits")
    src = f"""# {name}

- Repo: https://github.com/{s['repo']} ({LICENSES.get(short, 'see LICENSE')})
- Issue: {issues}
- PR: {info['url']} — "{info['title']}" (merged {info['mergedAt'][:10]} into {info.get('baseRefName', '?')})
- Fix commit (merge): {info['mergeCommit']['oid']}
- Parent (base) commit: {info['parents'][0]} (first parent of the merge commit)
- Files changed: {files}
- Why hard: {n.get('why', '-')}

## Construction
- base/ = GitHub API tarball of the parent; dropped (where present): {', '.join(drops)}.
- ACCOMMODATIONS (environmental, AWOS venv Python 3.12 / pytest 9.1): {'; '.join(acc) or 'none'}.
- hidden_tests/ = post-fix test files from the merge commit: {', '.join(f'{a} -> {b}' for a, b in s['tests'].items())}.
- reference/ = the PR's changed source file(s) at the merge commit: {', '.join(s['source'])}.
- Goal: {n.get('goal_note', 'issue / PR description rewritten as a user report, plus the behaviours the hidden tests pin.')}
- Built by scripts/fresh_series_tools/build.py; task.json + this file by meta.py (copies in ../_tools).
"""
    (ROOT / name / "SOURCE.md").write_text(src)


if __name__ == "__main__":
    for nm in sys.argv[1:] or SPECS:
        write(nm)

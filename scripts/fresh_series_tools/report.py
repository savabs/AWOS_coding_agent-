"""Write the fresh-series inventory (markdown) from specs, PR metadata, goal notes and
_tools/validate.json, and ~/.awos-harness/fresh_series/READY with the valid count.

usage: report.py <out.md>
"""
import json
import sys
import time

import gha
from build import ROOT
from goals import NOTES
from specs import SPECS

val = json.loads((ROOT / "_tools" / "validate.json").read_text())
rows, ok_names = [], []
for name, s in SPECS.items():
    short = s["repo"].split("/")[1]
    info = gha.prinfo(f"{short}_{s['pr']}")
    v = val.get(name, {})
    src = [f for f in info["files"] if f["path"] in s["source"]]
    churn = sum(f["additions"] + f["deletions"] for f in src)
    status = "valid" if v.get("ok") else "INVALID"
    if v.get("ok"):
        ok_names.append(name)
    rows.append((name, s, info, src, churn, status, v))

lines = [
    "# Fresh real-issue series, 2026-10 (PRs merged on/after 2026-08-01)",
    "",
    f"Generated {time.strftime('%Y-%m-%d %H:%M')} by `scripts/fresh_series_tools/report.py`.",
    "",
    f"- Location: `~/.awos-harness/fresh_series/` (one dir per issue, same layout as `real_series`:",
    "  `base/`, `jobs/01_<slug>/{task.json,hidden_tests/,reference/}`, `SOURCE.md`).",
    f"- **{len(ok_names)} / {len(rows)} issues pass `scripts/job_series.py validate`** (hidden tests fail on the",
    "  start state, pass with the reference; visible suite green on both). `READY` holds the count.",
    "- Run one: `AWOS_SERIES_MODEL=claude-haiku-5-5 python scripts/job_series.py run --series <name> "
    "--series-root ~/.awos-harness/fresh_series --arms ...`",
    "- Every fix PR was merged 2026-08-01 .. 2026-10-10 (after the earlier `real_series` set, which these do not",
    "  overlap: see `_tools/existing.txt`). Sources fetched via the GitHub API (tarballs at the merge commit's first",
    "  parent and at the merge commit), no git subprocesses.",
    "- Selection: Python repos with real test suites; preference for multi-file / multi-hunk fixes and for",
    "  behaviour the tests pin precisely. Goals are the issue / PR text rewritten as a user report (root cause and",
    "  fix removed) plus the exact behaviours the hidden tests pin, so a correct fix is gradable.",
    "",
    "## Inventory",
    "",
    "| # | series | repo | PR | merged | source files (churn) | start -> reference (hidden) | validate |",
    "|---|---|---|---|---|---|---|---|",
]
for i, (name, s, info, src, churn, status, v) in enumerate(rows, 1):
    files = "<br>".join(f"`{f['path']}` +{f['additions']}/-{f['deletions']}" for f in src)
    line = v.get("line", "")
    sr = line.split("start:", 1)[1].strip() if "start:" in line else "-"
    sr = sr.replace("|", "->")
    lines.append(f"| {i} | `{name}` | {s['repo']} | [#{s['pr']}]({info['url']}) | {info['mergedAt'][:10]} | "
                 f"{files} ({len(src)} file{'s' if len(src) != 1 else ''}, {churn} lines) | {sr[:120]} | {status} ({v.get('seconds', '?')}s) |")

multi = sum(1 for r in rows if len(r[3]) > 1 and r[5] == "valid")
lines += ["", f"Multi-file fixes among the valid issues: {multi} / {len(ok_names)}.", "",
          "## Why each is hard", ""]
for name, s, info, src, churn, status, v in rows:
    lines.append(f"- **{name}** ({info['title']}): {NOTES.get(name, {}).get('why', '-')}")

lines += ["", "## Dropped / excluded candidates", "",
          "- `networkx_8883` (group betweenness normalisation): its test module `importorskip`s scipy (not in the",
          "  AWOS venv), so 0 tests run -> cannot fail at start.",
          "- `pyflakes_902` / `pyflakes_864` (lazy imports): need Python 3.15 syntax; the venv is 3.12.",
          "- `humanize_391` (not built): its tests are in `tests/test_i18n.py`, which real_series had to drop (freezegun).",
          "- `pluggy_*` (not built): pytest imports the venv's installed pluggy at startup, before any path setup,",
          "  so tests would exercise the installed copy rather than the checkout.",
          "- `griffe_487`: monorepo layout (packages/griffelib) - skipped for time, not tried.",
          "- `pydash_259/255` (throttle/debounce): wall-clock timing tests, flaky under load.",
          "- sqlglot PRs whose tests are `.sql` fixtures or `test_executor.py` (needs duckdb/pandas) were not used.",
          "",
          "## Environmental accommodations (all documented per issue in SOURCE.md)",
          "",
          "- AWOS venv lacks hypothesis, duckdb, pandas, pytz, python-dateutil, scipy, PyYAML, pytest-cov,",
          "  pytest-regressions, linkify-it-py, freezegun. Tests that need them are removed from the *visible* suite",
          "  (and from hidden copies where the same test appears), or stubbed (sqlglot `tests/awos_stubs.py`, markdown",
          "  yaml shim). src/ layouts get `pythonpath = [\"src\"]`.",
          "- sqlglot: `TestOptimizer.test_simplify` (80 date subtests need dateutil), `TestExprs.test_convert` (pytz),",
          "  `TestDialect.test_lazy_load` (spawns bare `python`), `TestParser.test_max_nodes` (~50 s) removed;",
          "  visible suite ~2 min.",
          "- astroid: `tests/brain/numpy` dropped (old numpy brain vs the venv's NumPy 2.5), plus 4 environmental",
          "  tests; visible suite ~1.5-2.5 min under load.",
          "- networkx / pyflakes keep tests inside the package; the job's own pre-fix test modules are duplicated",
          "  into `tests/` so the harness's visible `pytest tests` is a real regression check.",
          "- pyparsing: `pytest.ini` with `timeout = 120` (pytest-timeout is in the venv) - an unfixed",
          "  `pyparsing_652` start state recurses ~forever inside one test.",
          "- pathspec_152: the visible `test_03_gitignore_basic.py` pins the old `$`-anchored regex text, so it is",
          "  removed from base/ (its post-fix copy is a hidden test).",
          "",
          "## Caveats (honest notes)",
          "",
          "- Validation ran on a shared, loaded laptop (other wave tracks running). In the full final pass, 4 series",
          "  (sqlglot_8460, sqlglot_8471, pyparsing_652, pyparsing_653) reported `reference: 1 error` within the same",
          "  window; re-running them alone 15 min later gave `ok` for all 4, and the 8460 reference run passes when",
          "  reproduced by hand (24 passed). Treated as a transient environment event, not flakiness of the tests;",
          "  worth watching in the first real run.",
          "- pyparsing_652's *start* state is slow to judge (~7 min under load: several hidden tests hit the 120 s",
          "  per-test timeout). With a correct fix the hidden run takes ~2 min. Wall-clock for a failed attempt is",
          "  therefore high on this one issue.",
          "- Visible suites are 1.5-3.5 min for sqlglot / astroid / pyparsing under load (each < 5 min).",
          "- Goals pin private hooks where the hidden tests import them (attrs `_lazy_is_generator` /",
          "  `_IS_GENERATOR_RESULTS`, astroid `_can_load_extension(modname, location)`, click `_pipepager`), and the",
          "  pyparsing_652 goal includes the PR's `max=` keyword for OneOrMore/ZeroOrMore (tested but not in the PR text).",
          "- No model was run on this set yet (spend $0); difficulty is judged from diff size and pinned behaviour only.",
          ""]
out = sys.argv[1]
with open(out, "w") as fh:
    fh.write("\n".join(lines) + "\n")
(ROOT / "READY").write_text(f"{len(ok_names)}\n" + "\n".join(ok_names) + "\n")
print(f"wrote {out}; READY = {len(ok_names)} valid of {len(rows)}")

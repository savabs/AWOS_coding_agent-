# Concierge demos: first batch (2026-10-13)

AWOS **v0.2.0** (the released package, not the source tree) was run on real open issues in active Python repos, and I reviewed every diff as a strict maintainer would. Nothing has been posted, sent or pushed. Everything here is a draft for the owner to review.

**Bottom line:** 6 runs on 5 issues (4 repos) produced **1 SEND, 2 MAYBE, 3 DROP**, for a total of **$0.35** of Claude Haiku 5.5. When the agent actually changed code, it did good work: every diff I could inspect was correct or close to it. Three of the six runs were lost to AWOS's own plumbing, not to the model. That is the main product feedback from this batch (see "Lessons").

## Attempts

| Repo | Issue | Verdict | Tests (re-run by me) | Cost | Min | Notes |
|---|---|---|---|---|---|---|
| MakazhanAlpamys/Soup | [#1860](https://github.com/MakazhanAlpamys/Soup/issues/1860) non-object JSONL rows crash `soup data` (run 2) | **SEND** | 20 new tests: red on main, green on branch; 439 passed in the data/loader/validator files | $0.052 | 5.8 | [md](soup_1860.md) · [patch](soup_1860.patch). Rewrite the commit message and rename the fragment to the PR number. |
| MakazhanAlpamys/Soup | [#1865](https://github.com/MakazhanAlpamys/Soup/issues/1865) huge int baseline → OverflowError | **MAYBE** (backup: one demo per maintainer) | 9 new tests: red on main, green on branch; 387 passed in the gate/baseline/ship files (only torch-only failures, the same as main) | $0.060 | 4.9 | [md](soup_1865.md) · [patch](soup_1865.patch). Needs parametrize `ids` (the test ids contain 400-digit numbers) and a new commit message. |
| python/mypy | [#22194](https://github.com/python/mypy/issues/22194) duplicate union items `str \| str` | **MAYBE** | new case red on master, green on branch; testcheck 8284 passed; rest of `mypy/test` 4045 passed; self-check clean | $0.050 | 24.2 | [md](mypy_22194.md) · [patch](mypy_22194.patch). Should reuse the existing `flatten_nested_unions` instead of a hand-rolled copy; fails `black`. Send as a PR comment, not a DM. |
| MakazhanAlpamys/Soup | #1860 (run 1) | DROP | n/a | $0.055 | 16.4 | A test that was already failing in my env (missing `pyarrow`) counted as "failed after the edit", so AWOS rolled back `loader.py` **only** and handed off a branch whose `data.py` imports a helper that no longer exists. Re-run after fixing the env → SEND above. |
| repowise-dev/repowise | [#3594](https://github.com/repowise-dev/repowise/issues/3594) `best_guesses` lists a file twice | DROP | could not run tests in the sandbox | $0.029 | 11.8 | The AWOS seatbelt sandbox denies even `stat()` on `.env.example`, so pytest collection fails in any repo with a `.env.example` at the root. The agent could not verify, the job "failed", and the fix was rolled back. **The handed-off branch contains only the new tests, without the fix.** |
| useblocks/sphinx-needs | [#2208](https://github.com/useblocks/sphinx-needs/issues/2208) schema writes `"default": []` for a link with a literal default | DROP | branch has tests only | $0.102 | 6.8 | The agent fixed it, and its targeted tests passed (38). It then hit the $0.10 cap while writing the changelog (27+ turns, mostly reads). Same partial rollback: source reverted, tests kept. |
| repowise-dev/repowise | [#3602](https://github.com/repowise-dev/repowise/issues/3602) `risk` on unknown ref shows CalledProcessError | not run | n/a | $0 | n/a | Cancelled before it started: same repo, so the same `.env.example` sandbox block applies. |

**Spend:** $0.354 of the $0.60 cap (all on claude-haiku-5-5, read from each job's report). The cancelled job cost nothing.

### Shortlisted but not run

| Repo | Issue | Why not |
|---|---|---|
| doobidoo/mcp-memory-service | #1520 sync export/import loses memories | Too big for a $0.10 ticket: needs a new way to read a memory's store (no API exposes it) plus importer changes and a real sqlite-vec round-trip test. |
| sirkirby/unifi-mcp | #843 partial `port_overrides` | The reporter offered to open the PR; also needs Python ≥3.13 and is a design-level change. |
| PrefectHQ/fastmcp | #5718 FileTreeStore never culled | The reporter offered to send a PR. |
| collective/icalendar | #1921 `StrEnum.get()` | A first-time contributor has claimed it in the comments; leave it to them. |
| brekkylab/backlot | #650 `%20` vs `+` in page links | An open PR already exists (#686). |
| griptape-ai/griptape | #2286 `lstrip` corrupts MIME formats | Already fixed (#2335 merged). |
| wemake-services/django-modern-rest | #1663 | An open PR exists (#1668). |
| aboutcode-org/scancode-toolkit | #5294 | An open PR exists (#5295). |
| open-telemetry/opentelemetry-python | #5566 | A contributor has claimed it. |
| scikit-learn/scikit-learn | #35139 | A compiled build (meson/Cython) is well over the 3-minute setup limit. |

## Lessons: where AWOS struggled (product feedback, most important first)

1. **Partial rollback hands off a broken branch (3 of 6 runs).** When a job "fails", the in-memory rollback reverts only the source file(s) it snapshotted. New test files, edits to other files and changelog fragments stay, and the branch is still handed off (`status: handed_off`). Result: branches with tests but no fix, and one with an import of a removed helper. This is the most damaging bug for a "review branch" product: a failed job must hand off either *nothing* or *everything*, clearly labelled. Treat it as a P0.
2. **Seatbelt sandbox blocks `.env.example`.** The dotenv deny rule covers `.env.*` for metadata too, so `pytest` dies at collection (`PermissionError: … .env.example`) in any repo that ships an example env file, which is very common. Allow `stat` and read for `.env.example`/`.env.sample`/`.env.template`, or deny only content reads of real `.env` files.
3. **Tests run with AWOS's own interpreter, not the project's venv.** `test_runner.py` and the host's `verify_tests` use `sys.executable`. With the documented `pipx install`, every project import fails. I had to install the same v0.2.0 wheel *into each project's venv* and add a `.pth` so the job workspace's `src/` wins over the editable install (otherwise tests silently exercise the original checkout). AWOS should detect and use `<repo>/.venv/bin/python` (or `uv run`) and put the workspace's code first on the path.
4. **Failures that already existed count against the edit.** One unrelated test, red before AWOS touched anything, triggered "1 test failed after the edit" and a rollback. Record a baseline run and only fail on new failures.
5. **The host's "full suite" result is noise on real repos.** It reported `0 passed / 17 failed` on Soup (torch extra + `--cov-fail-under`), `timeout` on mypy and repowise (300 s, serial), and `3047p/169f` on sphinx-needs. That line is in every report and morning digest; on real projects it is usually wrong and it scares reviewers. Run the mapped tests the agent ran, report the full suite only when it finishes, and respect `addopts`.
6. **Cost is in the reading, not the editing.** sphinx-needs used its whole $0.10 on 27+ turns of reads before writing the changelog. The one-shot fast path never finished a job. In 7 one-shot calls across the 6 runs, 4 produced no applicable SEARCH/REPLACE block and 3 applied an edit but then saw "tests: 0 passed, 0 failed" (an interpreter or sandbox problem). Every call fell back to the agent loop, which added about $0.005 per run for nothing.
7. **Handoff polish.** The commit subject is the first 60 characters of the goal ("awos: Fix https://github.com/... : in src/soup_cl"), and every report says `git diff main...` even when the default branch is `master` (mypy). Both need fixing before maintainers see a branch.
8. **`uv pip install "awos @ git+…@v0.2.0"` fails** because of a dangling submodule (`.awos/benchmark_ci_rescue/awos` has no URL in `.gitmodules`). pipx/pip work. Fix the submodule before the launch install docs mention uv.
9. **What worked well:** issue-shaped goals with file/function pointers led to test-first behaviour (the tests failed on main before the fix in every case I checked), house-style changelog fragments and test names, and minimal diffs. When the plumbing got out of the way, Haiku 5.5 produced work I would put my name on, for about $0.05 per ticket.

## Method (reproducible)

- Candidates came from `gh search issues` (labels `bug` / `good first issue`, language Python, created in the last 90 days, open, no assignee), filtered with GraphQL to ≥200 stars, pushed within 30 days, not archived. Each one was checked for a linked or cross-referenced PR, claim comments and a CONTRIBUTING file.
- Clones lived in an ignored directory of the agent worktree (`.awos/jobs/demos/<repo>`, shallow). Venvs were made with `uv` (Python 3.12); all setups took under 3 minutes.
- Jobs ran with `awos submit --repo <clone> --budget 0.10 "<goal>"` and then `awos serve --worker --once`, with an isolated `AWOS_HOST_DIR`/`AWOS_HOME` under `/tmp/awos_demo`. The API key was loaded by a small wrapper and never printed. Goals were written from the issue text and link the issue.
- Review: each AWOS branch was checked out in its own git worktree. I re-ran the relevant test files, checked the new tests against the unpatched default branch (test-first), and ran lint (ruff; black for mypy; mypy self-check for mypy).

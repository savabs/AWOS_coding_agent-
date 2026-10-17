"""Tests for `awos fix <github url>` (scaffold/agent/app/fix.py). gh, git and the
job runner are all fakes: nothing here touches the network, a model or a real job."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scaffold.agent.app import fix as F

REPO = Path("/tmp/fake-clone")
ISSUE_URL = "https://github.com/acme/widget/issues/12"
PR_URL = "https://github.com/acme/widget/pull/34"


def cp(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


ISSUE = {
    "title": "Crash on empty input",
    "url": ISSUE_URL,
    "labels": [{"name": "bug"}],
    "body": "<!-- template: describe the bug -->\nRunning `widget ''` crashes.\n\n"
            "![screenshot](https://x/y.png)\n<img src='a.png'>\nExpected: a friendly error.",
    "comments": [
        {"author": {"login": "alice"}, "body": "Repro on 1.2 too. <!-- hidden -->"},
        {"author": {"login": "github-actions[bot]"}, "body": "stale bot noise"},
        {"author": {"login": "bob"}, "body": "Probably parse() in cli.py"},
    ],
}

PR = {"title": "Bump requests from 2.31 to 2.32", "body": "Dependabot bump.\n<!-- dependabot -->",
      "url": PR_URL, "headRefName": "dependabot/pip/requests-2.32", "headRefOid": "abc123def4567890",
      "baseRefName": "main", "isCrossRepository": False, "headRepositoryOwner": {"login": "acme"},
      "state": "OPEN"}

CHECKS = [
    {"name": "tests (3.12)", "state": "FAILURE",
     "link": "https://github.com/acme/widget/actions/runs/777/job/1"},
    {"name": "lint", "state": "SUCCESS", "link": "https://github.com/acme/widget/actions/runs/778/job/2"},
]

LOG = "\n".join([f"tests\tRun pytest\t2026-10-01T10:00:{i % 60:02d}.0000000Z line {i}" for i in range(400)]
                + ["tests\tRun pytest\t2026-10-01T10:01:00.0Z E   AttributeError: module 'requests' has no attribute 'compat'"]
                + [f"tests\tRun pytest\t2026-10-01T10:02:00.0Z trailing {i}" for i in range(5)])


class FakeRunner:
    """Answers git/gh commands from a small table; records every call."""

    def __init__(self, *, origin="git@github.com:acme/widget.git", dirty="", gh_auth=0,
                 issue=ISSUE, pr=PR, checks=CHECKS, log=LOG, existing_branches=(), push_code=0):
        self.calls: list[list[str]] = []
        self.origin, self.dirty, self.gh_auth = origin, dirty, gh_auth
        self.issue, self.pr, self.checks, self.log = issue, pr, checks, log
        self.existing = set(existing_branches)
        self.push_code = push_code

    def __call__(self, cmd, cwd=None):
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[0] == "git":
            args = cmd[3:]   # git -C <repo> ...
            if args[:2] == ["rev-parse", "--show-toplevel"]:
                return cp(out=str(REPO) + "\n")
            if args[:3] == ["remote", "get-url", "origin"]:
                return cp(out=self.origin + "\n") if self.origin else cp(2, err="no such remote")
            if args[:1] == ["status"]:
                return cp(out=self.dirty)
            if args[:1] == ["rev-parse"] and args[-1].startswith("refs/heads/"):
                return cp(0 if args[-1][len("refs/heads/"):] in self.existing else 1)
            if args == ["rev-parse", "HEAD"]:
                return cp(out="0123456789abcdef0123456789abcdef01234567\n")
            if args[:1] == ["symbolic-ref"] and "refs/remotes/origin/HEAD" in args:
                return cp(out="origin/main\n")
            if args[:1] == ["symbolic-ref"]:
                return cp(out="dependabot/pip/requests-2.32\n")
            if args[:1] == ["push"]:
                return cp(self.push_code, err="denied" if self.push_code else "")
            return cp()
        assert cmd[0] == "gh", cmd
        if cmd[1:3] == ["auth", "status"]:
            return cp(self.gh_auth, err="You are not logged into any GitHub hosts" if self.gh_auth else "")
        if cmd[1:3] == ["issue", "view"]:
            return cp(out=json.dumps(self.issue))
        if cmd[1:3] == ["pr", "view"]:
            return cp(out=json.dumps(self.pr))
        if cmd[1:3] == ["pr", "checks"]:
            failing = any(c["state"] == "FAILURE" for c in self.checks)
            return cp(1 if failing else 0, out=json.dumps(self.checks))
        if cmd[1:3] == ["run", "view"]:
            return cp(out=self.log)
        if cmd[1:3] == ["pr", "checkout"]:
            return cp()
        if cmd[1:3] == ["pr", "create"]:
            return cp(out="https://github.com/acme/widget/pull/99\n")
        raise AssertionError(f"unexpected gh call {cmd}")

    def pushed(self):
        return [c for c in self.calls if c[0] == "git" and "push" in c] + \
            [c for c in self.calls if c[:3] == ["gh", "pr", "create"]]


class FakeJobs:
    def __init__(self, state="done", handoff="handed_off", report=None):
        self.requests: list[F.JobRequest] = []
        self.state, self.handoff, self.report = state, handoff, report

    def __call__(self, req: F.JobRequest) -> F.JobResult:
        self.requests.append(req)
        return F.JobResult(job_id="deadbeef1234", state=self.state, branch=req.branch,
                           handoff_status=self.handoff, files=["widget/cli.py", "tests/test_cli.py"],
                           tests="12 passed", tests_ok=True, cost=0.0123, report=self.report)


def run(url, runner=None, jobs=None, which=lambda _: "/usr/bin/gh", **opts):
    out: list[str] = []
    runner = runner or FakeRunner()
    jobs = jobs or FakeJobs()
    code = F.run_fix(F.FixOptions(url=url, **opts), REPO, run=runner, job_runner=jobs,
                     say=out.append, which=which)
    return code, "\n".join(out), runner, jobs


# ── URL parsing ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,kind,num", [
    ("https://github.com/acme/widget/issues/12", "issue", 12),
    ("https://github.com/acme/widget/issues/12/", "issue", 12),
    ("https://github.com/acme/widget/issues/12#issuecomment-99", "issue", 12),
    ("github.com/acme/widget/issues/12?x=1", "issue", 12),
    ("https://www.github.com/acme/widget/pull/34", "pull", 34),
    ("https://github.com/acme/widget/pull/34/files", "pull", 34),
    ("https://github.com/acme/widget/pull/34/checks#step", "pull", 34),
    ("  https://github.com/acme/my.repo_x/pull/5/  ", "pull", 5),
])
def test_parse_target_valid(url, kind, num):
    t = F.parse_target(url)
    assert (t.kind, t.number) == (kind, num)
    assert t.owner == "acme"


@pytest.mark.parametrize("url", [
    "", "https://github.com/acme/widget", "https://github.com/acme/widget/issues/",
    "https://github.com/acme/widget/issues/abc", "https://gitlab.com/acme/widget/issues/1",
    "https://github.com/acme/widget/discussions/3", "https://github.com/acme/widget/issues/0",
    "https://github.com/acme/widget.git/issues/3",
])
def test_parse_target_invalid(url):
    with pytest.raises(F.FixError, match="not a GitHub issue or pull request URL"):
        F.parse_target(url)


# ── remotes ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("remote", [
    "https://github.com/acme/widget.git", "https://github.com/acme/widget",
    "https://github.com/Acme/Widget/", "https://user:tok@github.com/acme/widget.git",
    "git@github.com:acme/widget.git", "git@github.com:acme/widget",
    "ssh://git@github.com/acme/widget.git", "ssh://git@ssh.github.com:443/acme/widget.git",
    "git://github.com/acme/widget.git",
])
def test_remote_matches(remote):
    assert F.remote_matches(remote, F.parse_target(ISSUE_URL))


@pytest.mark.parametrize("remote", [
    "git@github.com:other/widget.git", "https://github.com/acme/widget-fork.git",
    "https://gitlab.com/acme/widget.git", "/local/path/widget", "",
])
def test_remote_mismatch(remote):
    assert not F.remote_matches(remote, F.parse_target(ISSUE_URL))


def test_wrong_origin_is_a_clear_error():
    code, out, *_ = run(ISSUE_URL, FakeRunner(origin="https://github.com/other/thing.git"))
    assert code == 2 and "origin is other/thing" in out and "acme/widget" in out


def test_not_a_git_repo():
    r = FakeRunner()
    orig = r.__call__

    def runner(cmd, cwd=None):
        if cmd[3:5] == ["rev-parse", "--show-toplevel"]:
            return cp(128, err="not a git repository")
        return orig(cmd, cwd)
    code, out, *_ = run(ISSUE_URL, runner)
    assert code == 2 and "inside your local clone of acme/widget" in out


# ── preconditions ────────────────────────────────────────────────────────────

def test_dirty_tree_refused_for_issue_without_allow_dirty():
    code, out, r, jobs = run(ISSUE_URL, FakeRunner(dirty=" M widget/cli.py\n"))
    assert code == 2 and "uncommitted changes (widget/cli.py)" in out and "--allow-dirty" in out
    assert jobs.requests == []


def test_dirty_tree_allowed_for_issue_with_flag():
    code, out, r, jobs = run(ISSUE_URL, FakeRunner(dirty=" M widget/cli.py\n"), allow_dirty=True)
    assert code == 0 and len(jobs.requests) == 1 and "not part of the job" in out


def test_dirty_tree_always_refused_for_pr():
    code, out, r, jobs = run(PR_URL, FakeRunner(dirty="M  setup.py\n"), allow_dirty=True)
    assert code == 2 and "uncommitted changes" in out
    assert not any(c[:3] == ["gh", "pr", "checkout"] for c in r.calls)
    assert jobs.requests == []


def test_gh_missing():
    code, out, *_ = run(ISSUE_URL, which=lambda _: None)
    assert code == 2 and "GitHub CLI (gh) is not installed" in out and "gh auth login" in out


def test_gh_not_authenticated():
    code, out, *_ = run(ISSUE_URL, FakeRunner(gh_auth=1))
    assert code == 2 and "not logged in" in out and "gh auth login" in out


def test_flag_mismatch():
    assert run(PR_URL, pr=True)[0] == 2
    assert run(ISSUE_URL, push=True)[0] == 2


# ── goal text ────────────────────────────────────────────────────────────────

def test_issue_goal_text():
    goal = F.build_issue_goal(F.parse_target(ISSUE_URL), ISSUE)
    first = goal.splitlines()[0]
    assert first == "Fix GitHub issue #12: Crash on empty input"
    assert "Labels: bug" in goal
    assert "template" not in goal and "hidden" not in goal          # HTML comments stripped
    assert "y.png" not in goal and "a.png" not in goal and "[image]" in goal
    assert "@alice: Repro on 1.2 too." in goal and "@bob:" in goal
    assert "stale bot noise" not in goal
    assert goal.rstrip().endswith(f"Issue: {ISSUE_URL}")


def test_issue_comments_trimmed_to_budget_keeping_newest():
    comments = [{"author": {"login": f"u{i}"}, "body": f"comment {i} " + "x" * 1400} for i in range(10)]
    goal = F.build_issue_goal(F.parse_target(ISSUE_URL), {**ISSUE, "comments": comments})
    assert "@u9:" in goal and "@u0:" not in goal
    assert len(goal) < F.BODY_BUDGET + F.COMMENTS_BUDGET + 2000


def test_truncate_log_keeps_tail_and_error_lines():
    big = "\n".join(["noise"] * 50 + ["ERROR: boom in setup"] + ["noise"] * 2000 + ["final line"])
    out = F.truncate_log(big, 500)
    assert len(out) <= 600
    assert "ERROR: boom in setup" in out and out.endswith("final line")
    assert "earlier lines omitted" in out


def test_truncate_log_strips_gh_prefixes():
    out = F.truncate_log("build\tRun tests\t2026-10-01T10:00:00.123Z hello\n", 1000)
    assert out == "hello"


def test_pr_goal_text_with_logs():
    t = F.parse_target(PR_URL)
    failing = F.failing_checks(CHECKS)
    assert [c["name"] for c in failing] == ["tests (3.12)"]
    logs = [{"run_id": "777", "checks": ["tests (3.12)"], "log": F.truncate_log(LOG, 1500)}]
    goal = F.build_pr_goal(t, PR, failing, logs)
    assert goal.startswith("Make the failing CI checks pass on this PR without reverting its intent")
    assert "Bump requests from 2.31 to 2.32" in goal and "<!--" not in goal
    assert "- tests (3.12) (failure)" in goal and "lint" not in goal
    assert "AttributeError: module 'requests'" in goal and "trailing 4" in goal
    assert len(logs[0]["log"]) <= 1600
    assert goal.rstrip().endswith(f"PR: {PR_URL}")


# ── dry runs ─────────────────────────────────────────────────────────────────

def test_issue_dry_run_prints_goal_and_commands_and_runs_nothing():
    code, out, r, jobs = run(ISSUE_URL, dry_run=True, budget=0.5)
    assert code == 0 and jobs.requests == []
    assert "Dry run" in out and "Fix GitHub issue #12: Crash on empty input" in out
    assert "awos/issue-12-crash-on-empty-input" in out and "Budget: $0.50" in out
    assert "(with --pr: git push -u origin awos/issue-12-crash-on-empty-input)" in out
    assert r.pushed() == []


def test_pr_dry_run_does_not_checkout():
    code, out, r, jobs = run(PR_URL, dry_run=True)
    assert code == 0 and jobs.requests == []
    assert f"gh pr checkout 34 -R acme/widget" in out and "awos/pr-34-fix" in out
    assert "AttributeError" in out
    assert not any(c[:3] == ["gh", "pr", "checkout"] for c in r.calls)
    assert any(c[:3] == ["gh", "run", "view"] and "777" in c and "--log-failed" in c for c in r.calls)
    assert r.pushed() == []


# ── running the job ──────────────────────────────────────────────────────────

def test_issue_calls_job_with_goal_branch_budget_and_never_pushes():
    code, out, r, jobs = run(ISSUE_URL, budget=0.75)
    assert code == 0
    (req,) = jobs.requests
    assert req.repo == REPO and req.budget == 0.75
    assert req.branch == "awos/issue-12-crash-on-empty-input"
    assert req.base_ref == "0123456789abcdef0123456789abcdef01234567"
    assert req.goal.startswith("Fix GitHub issue #12")
    assert r.pushed() == []
    assert "Branch:  awos/issue-12-crash-on-empty-input" in out
    assert "widget/cli.py, tests/test_cli.py" in out and "12 passed" in out and "$0.0123" in out
    assert "git diff main...awos/issue-12-crash-on-empty-input" in out
    assert "gh pr create --draft" in out


def test_issue_default_budget_comes_from_app(monkeypatch):
    monkeypatch.setenv("AWOS_APP_BUDGET", "0.3")
    _, _, _, jobs = run(ISSUE_URL)
    assert jobs.requests[0].budget == 0.3


def test_existing_branch_gets_a_suffix():
    r = FakeRunner(existing_branches={"awos/issue-12-crash-on-empty-input",
                                      "awos/issue-12-crash-on-empty-input-2-failed"})
    _, _, _, jobs = run(ISSUE_URL, r)
    assert jobs.requests[0].branch == "awos/issue-12-crash-on-empty-input-3"


def test_issue_with_pr_pushes_and_opens_draft(tmp_path):
    rep = tmp_path / "report.md"
    rep.write_text("# report\nall good\n")
    code, out, r, jobs = run(ISSUE_URL, jobs=FakeJobs(report=str(rep)), pr=True)
    assert code == 0
    push = [c for c in r.calls if c[0] == "git" and "push" in c]
    assert push and push[0][-1] == "awos/issue-12-crash-on-empty-input"
    (create,) = [c for c in r.calls if c[:3] == ["gh", "pr", "create"]]
    assert "--draft" in create
    body = create[create.index("--body") + 1]
    assert "Fixes #12" in body and "all good" in body and "12 passed" in body
    assert "pull/99" in out


def test_issue_with_pr_but_failed_job_opens_nothing():
    code, out, r, _ = run(ISSUE_URL, jobs=FakeJobs(state="failed", handoff="handed_off_failed"), pr=True)
    assert code == 1 and r.pushed() == [] and "Not opening a PR" in out


def test_pr_checks_out_and_runs_on_pr_head_without_push():
    code, out, r, jobs = run(PR_URL, budget=0.4)
    assert code == 0
    assert ["gh", "pr", "checkout", "34", "-R", "acme/widget"] in r.calls
    (req,) = jobs.requests
    assert req.branch == "awos/pr-34-fix" and req.budget == 0.4
    assert req.base_ref == "0123456789abcdef0123456789abcdef01234567"
    assert req.goal.startswith("Make the failing CI checks pass")
    assert r.pushed() == []
    assert "git diff dependabot/pip/requests-2.32...awos/pr-34-fix" in out
    assert "gh pr comment 34" in out


def test_pr_with_push_pushes_only_the_fix_branch():
    code, out, r, _ = run(PR_URL, push=True)
    assert code == 0
    (push,) = [c for c in r.calls if c[0] == "git" and "push" in c]
    assert push[-3:] == ["-u", "origin", "awos/pr-34-fix"]
    assert not any(c[:3] == ["gh", "pr", "create"] for c in r.calls)


def test_pr_without_failing_checks_stops():
    green = [{**c, "state": "SUCCESS"} for c in CHECKS]
    code, out, r, jobs = run(PR_URL, FakeRunner(checks=green))
    assert code == 0 and "no failing checks" in out and jobs.requests == []
    assert not any(c[:3] == ["gh", "pr", "checkout"] for c in r.calls)


def test_pr_without_failing_checks_but_goal_runs():
    green = [{**c, "state": "SUCCESS"} for c in CHECKS]
    code, out, r, jobs = run(PR_URL, FakeRunner(checks=green), goal="Add a changelog entry")
    assert code == 0 and jobs.requests[0].goal.startswith("Add a changelog entry (PR #34")


# ── handoff honours the requested branch name ────────────────────────────────

def test_handoff_uses_branch_name(tmp_path):
    from scaffold.agent.host import handoff

    repo = tmp_path / "repo"
    repo.mkdir()
    g = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)  # noqa: E731
    g("init", "-q", "-b", "main")
    g("-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    (repo / "a.txt").write_text("x\n")
    g("add", "a.txt")
    g("-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "-m", "a")
    ws = tmp_path / "ws"
    g("worktree", "add", "-q", "--detach", str(ws), "HEAD")
    (ws / "a.txt").write_text("y\n")
    job = {"id": "job-1234abcd", "goal": "Fix GitHub issue #12: thing", "repo_path": str(repo),
           "branch_name": "awos/issue-12-thing"}
    info = handoff.finish(job, str(ws), {"state": "done"}, job_dir=str(tmp_path / "jd"), env={})
    assert info["status"] == "handed_off" and info["branch"] == "awos/issue-12-thing"
    bad = dict(job, id="job-5678efgh", branch_name="main")
    (ws / "a.txt").write_text("z\n")
    info = handoff.finish(bad, str(ws), {"state": "done"}, job_dir=str(tmp_path / "jd2"), env={})
    assert info["branch"] == "awos/job5678e" and any("branch_name" in w for w in info["warnings"])


# ── CLI wiring ───────────────────────────────────────────────────────────────

def test_cli_parser_has_fix():
    import awos
    args = awos.build_parser().parse_args(["fix", ISSUE_URL, "--dry-run", "--budget", "0.5"])
    assert args.command == "fix" and args.dry_run and args.budget == 0.5 and args.url == ISSUE_URL

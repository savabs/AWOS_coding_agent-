"""
fix.py — `awos fix <github issue or PR url>`, run inside the user's clone.

    awos fix https://github.com/OWNER/REPO/issues/12   -> branch awos/issue-12-<slug>
    awos fix https://github.com/OWNER/REPO/pull/34     -> branch awos/pr-34-fix (on the PR head)

This module only gathers context (via the `gh` CLI) and builds the goal; the
work itself is an ordinary host-queue job run by the background worker, the
same path as `awos "goal"` (scaffold/agent/app/session.py). The worker's
hand-off (host/handoff.py) creates the review branch under the name chosen
here (<job_dir>/branch_name). Nothing is pushed and no PR is opened unless
the user passes --pr (issues) or --push (PRs).

Every external command goes through a `Runner` so tests can fake `gh`/`git`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

# ── budgets (characters) ─────────────────────────────────────────────────────
BODY_BUDGET = 6000
COMMENTS_BUDGET = 4000
COMMENT_MAX = 1500
LOG_BUDGET = 8000          # all failing-log excerpts together
LOG_MIN_PER_RUN = 1500
MAX_RUNS = 4
FAILING_STATES = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED",
                  "STARTUP_FAILURE", "FAIL"}
PENDING_STATES = {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", "EXPECTED"}


class FixError(Exception):
    """A user-facing problem: printed as one line, exit code 2."""


Runner = Callable[[Sequence[str], Optional[Path]], subprocess.CompletedProcess]


# Token variables gh prefers over its own login. AWOS's .env can set them (often
# empty or stale), which makes gh fail although the user is logged in.
GH_TOKEN_VARS = ("GH_TOKEN", "GITHUB_TOKEN")
# Set by check_gh when gh works only without those variables.
_gh_drop_tokens = False


def _gh_env(drop_tokens: bool) -> Optional[dict]:
    if not drop_tokens:
        return None
    return {k: v for k, v in os.environ.items() if k not in GH_TOKEN_VARS}


def default_runner(cmd: Sequence[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    drop = _gh_drop_tokens and bool(cmd) and cmd[0] == "gh"
    return subprocess.run(list(cmd), cwd=str(cwd) if cwd else None, capture_output=True,
                          text=True, env=_gh_env(drop))


# ── URL / remote parsing ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Target:
    owner: str
    repo: str
    kind: str        # "issue" | "pull"
    number: int

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        path = "issues" if self.kind == "issue" else "pull"
        return f"https://github.com/{self.owner}/{self.repo}/{path}/{self.number}"


_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]+?)/"
    r"(?P<kind>issues|pull|pulls)/(?P<num>\d+)"
    r"(?:/[A-Za-z0-9._/-]*)?/?$")


def parse_target(url: str) -> Target:
    """An issue or PR URL -> Target. Tolerates trailing slashes, #anchors,
    ?query strings and PR sub-pages (/files, /checks)."""
    raw = (url or "").strip()
    clean = raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    m = _URL_RE.match(clean)
    if not m or int(m["num"]) <= 0 or m["repo"].endswith(".git"):
        raise FixError(
            f"not a GitHub issue or pull request URL: {raw!r}\n"
            "  expected https://github.com/OWNER/REPO/issues/N or https://github.com/OWNER/REPO/pull/N")
    kind = "issue" if m["kind"] == "issues" else "pull"
    return Target(m["owner"], m["repo"], kind, int(m["num"]))


_REMOTE_RES = (
    re.compile(r"^(?:https?|git)://(?:[^@/]+@)?(?:www\.)?github\.com(?::\d+)?/(?P<o>[^/]+)/(?P<r>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^ssh://(?:[^@/]+@)?(?:ssh\.)?github\.com(?::\d+)?/(?P<o>[^/]+)/(?P<r>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^(?:[^@/:]+@)?github\.com:(?P<o>[^/]+)/(?P<r>[^/]+?)(?:\.git)?/?$"),
)


def parse_remote(url: str) -> Optional[tuple[str, str]]:
    """(owner, repo) for a github.com remote in https, ssh or scp form; else None."""
    url = (url or "").strip()
    for rx in _REMOTE_RES:
        m = rx.match(url)
        if m:
            return m["o"], m["r"]
    return None


def remote_matches(remote_url: str, target: Target) -> bool:
    got = parse_remote(remote_url)
    return bool(got) and (got[0].lower(), got[1].lower()) == (target.owner.lower(), target.repo.lower())


# ── text helpers ─────────────────────────────────────────────────────────────

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_IMG = re.compile(r"<img\b[^>]*>", re.I)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_BLANKS = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Drop HTML comments (issue templates), images and runs of blank lines."""
    text = _HTML_COMMENT.sub("", text or "")
    text = _MD_IMAGE.sub("[image]", text)
    text = _HTML_IMG.sub("[image]", text)
    text = text.replace("\r\n", "\n")
    return _BLANKS.sub("\n\n", text).strip()


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n[... truncated ...]"


def slugify(title: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    if len(s) > limit:
        s = s[:limit].rsplit("-", 1)[0] if "-" in s[:limit] else s[:limit]
    return s.strip("-") or "fix"


_ERROR_LINE = re.compile(
    r"error|fail|exception|traceback|assert|panic|fatal|cannot|could not|not found|"
    r"denied|refused|timed out|^E\s|^\s*>\s|exit code [1-9]", re.I)
_GH_LOG_PREFIX = re.compile(r"^[^\t\n]*\t[^\t\n]*\t")                 # "job\tstep\t"
_TIMESTAMP = re.compile(r"^\ufeff?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z\s?")


def truncate_log(text: str, budget: int = 4000) -> str:
    """
    A failing CI log cut to `budget` chars: the tail (where the failure
    usually is) plus error-looking lines from the part that was dropped.
    gh's "job<TAB>step<TAB>" prefixes and timestamps are removed first.
    """
    lines = []
    for ln in _ANSI.sub("", text or "").splitlines():
        ln = _TIMESTAMP.sub("", _GH_LOG_PREFIX.sub("", ln, count=1))
        if ln.strip():
            lines.append(ln.rstrip())
    joined = "\n".join(lines)
    if len(joined) <= budget:
        return joined
    tail_budget = int(budget * 0.6)
    tail: list[str] = []
    used = 0
    i = len(lines)
    while i > 0 and used + len(lines[i - 1]) + 1 <= tail_budget:
        i -= 1
        tail.insert(0, lines[i])
        used += len(lines[i]) + 1
    if not tail:   # one enormous last line
        return "[... log truncated ...]\n" + lines[-1][-(budget - 30):]
    head = lines[:i]
    err_budget = budget - used - 80
    errs: list[str] = []
    seen: set[str] = set()
    for ln in head:
        if not _ERROR_LINE.search(ln):
            continue
        ln = ln if len(ln) <= 300 else ln[:297] + "..."
        if ln in seen:
            continue
        if sum(len(e) + 1 for e in errs) + len(ln) + 1 > err_budget:
            break
        seen.add(ln)
        errs.append(ln)
    out = [f"[... {len(head)} earlier lines omitted"
           + ("; error lines from them:" if errs else "") + " ...]"]
    out += errs
    if errs:
        out.append("[... last lines of the log ...]")
    out += tail
    return "\n".join(out)


# ── gh access ────────────────────────────────────────────────────────────────

def _gh(run: Runner, args: list[str], cwd: Optional[Path], what: str,
        ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    try:
        out = run(["gh", *args], cwd)
    except FileNotFoundError:
        raise FixError(gh_missing_message())
    if out.returncode not in ok_codes:
        msg = (out.stderr or out.stdout or "").strip().splitlines()
        raise FixError(f"gh could not {what}: {msg[-1] if msg else f'exit code {out.returncode}'}")
    return out


def gh_missing_message() -> str:
    return ("the GitHub CLI (gh) is not installed. Install it from https://cli.github.com "
            "(macOS: brew install gh), then run: gh auth login")


def check_gh(run: Runner, which: Callable[[str], Optional[str]] = shutil.which,
             cwd: Optional[Path] = None) -> None:
    if which("gh") is None:
        raise FixError(gh_missing_message())
    try:
        out = run(["gh", "auth", "status"], cwd)
    except FileNotFoundError:
        raise FixError(gh_missing_message())
    if out.returncode != 0 and run is default_runner and any(os.environ.get(v) is not None
                                                             for v in GH_TOKEN_VARS):
        # A token variable (e.g. from AWOS's .env) can shadow a working gh login.
        global _gh_drop_tokens
        _gh_drop_tokens = True
        out = run(["gh", "auth", "status"], cwd)
        if out.returncode == 0:
            print("awos fix: ignoring GH_TOKEN/GITHUB_TOKEN from the environment; "
                  "using your gh login")
        else:
            _gh_drop_tokens = False
    if out.returncode != 0:
        raise FixError("gh is not logged in to GitHub. Run: gh auth login")


def _json(out: subprocess.CompletedProcess, what: str):
    try:
        return json.loads(out.stdout or "null")
    except ValueError:
        raise FixError(f"gh returned unreadable output for {what}")


def fetch_issue(run: Runner, t: Target, cwd: Optional[Path]) -> dict:
    out = _gh(run, ["issue", "view", str(t.number), "-R", t.slug, "--json",
                    "title,body,comments,labels,url"], cwd, f"read issue #{t.number} of {t.slug}")
    data = _json(out, "the issue") or {}
    if not isinstance(data, dict):
        raise FixError("gh returned unexpected issue data")
    return data


PR_FIELDS = "title,body,url,headRefName,headRefOid,baseRefName,isCrossRepository,headRepositoryOwner,state"


def fetch_pr(run: Runner, t: Target, cwd: Optional[Path]) -> dict:
    out = _gh(run, ["pr", "view", str(t.number), "-R", t.slug, "--json", PR_FIELDS], cwd,
              f"read pull request #{t.number} of {t.slug}")
    data = _json(out, "the pull request") or {}
    if not isinstance(data, dict):
        raise FixError("gh returned unexpected pull request data")
    return data


def fetch_checks(run: Runner, t: Target, cwd: Optional[Path]) -> list[dict]:
    # gh exits 1 when checks fail and 8 when some are pending; the JSON is still printed.
    try:
        out = run(["gh", "pr", "checks", str(t.number), "-R", t.slug, "--json", "name,state,link"], cwd)
    except FileNotFoundError:
        raise FixError(gh_missing_message())
    text = (out.stdout or "").strip()
    if not text:
        err = (out.stderr or "").strip()
        if out.returncode == 0 or "no checks" in err.lower():
            return []
        raise FixError(f"gh could not list checks for PR #{t.number}: "
                       f"{err.splitlines()[-1] if err else f'exit code {out.returncode}'}")
    data = _json(out, "the PR checks")
    return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []


def failing_checks(checks: list[dict]) -> list[dict]:
    return [c for c in checks if str(c.get("state") or "").upper() in FAILING_STATES]


_RUN_ID = re.compile(r"/actions/runs/(\d+)")


def run_id_of(link: str) -> Optional[str]:
    m = _RUN_ID.search(link or "")
    return m.group(1) if m else None


def fetch_failed_logs(run: Runner, t: Target, failing: list[dict], cwd: Optional[Path],
                      budget: int = LOG_BUDGET) -> list[dict]:
    """[{run_id, checks: [names], log}] for the GitHub Actions runs behind failing checks."""
    runs: dict[str, list[str]] = {}
    for c in failing:
        rid = run_id_of(str(c.get("link") or ""))
        if rid:
            runs.setdefault(rid, []).append(str(c.get("name") or "?"))
    picked = list(runs.items())[:MAX_RUNS]
    if not picked:
        return []
    per_run = max(LOG_MIN_PER_RUN, budget // len(picked))
    result = []
    for rid, names in picked:
        try:
            out = run(["gh", "run", "view", rid, "-R", t.slug, "--log-failed"], cwd)
        except FileNotFoundError:
            raise FixError(gh_missing_message())
        if out.returncode != 0 or not (out.stdout or "").strip():
            err = (out.stderr or "").strip().splitlines()
            log = f"(log unavailable: {err[-1] if err else 'empty'})"
        else:
            log = truncate_log(out.stdout, per_run)
        result.append({"run_id": rid, "checks": names, "log": log})
    return result


# ── goal construction ────────────────────────────────────────────────────────

def _bot(login: str) -> bool:
    login = (login or "").lower()
    return login.endswith("[bot]") or login in ("github-actions", "dependabot", "codecov", "renovate")


def select_comments(comments: list, budget: int = COMMENTS_BUDGET) -> list[tuple[str, str]]:
    """Human comments, newest kept first when over budget, returned oldest-first."""
    items = []
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        author = ((c.get("author") or {}).get("login") if isinstance(c.get("author"), dict)
                  else str(c.get("author") or "")) or "someone"
        body = clean_text(str(c.get("body") or ""))
        if not body or _bot(author):
            continue
        items.append((author, clip(body, COMMENT_MAX)))
    kept, used = [], 0
    for author, body in reversed(items):
        if used + len(body) > budget:
            break
        kept.append((author, body))
        used += len(body)
    return list(reversed(kept))


def build_issue_goal(t: Target, issue: dict, extra: str = "") -> str:
    title = " ".join(str(issue.get("title") or f"issue {t.number}").split())
    labels = [str(lb.get("name")) for lb in issue.get("labels") or [] if isinstance(lb, dict) and lb.get("name")]
    body = clip(clean_text(str(issue.get("body") or "")), BODY_BUDGET) or "(no description)"
    parts = [f"Fix GitHub issue #{t.number}: {title}", ""]
    if labels:
        parts += [f"Labels: {', '.join(labels)}", ""]
    parts += ["Issue description:", body, ""]
    comments = select_comments(issue.get("comments") or [])
    if comments:
        parts.append("Relevant comments:")
        for author, text in comments:
            parts.append(f"- @{author}: " + text.replace("\n", "\n  "))
        parts.append("")
    if extra.strip():
        parts += ["Extra instructions:", extra.strip(), ""]
    parts += ["Make the smallest change that resolves the issue, add or update a test that "
              "covers it, and keep the existing tests passing.", "",
              f"Issue: {issue.get('url') or t.url}"]
    return "\n".join(parts).strip() + "\n"


def build_pr_goal(t: Target, pr: dict, failing: list[dict], logs: list[dict], extra: str = "") -> str:
    title = " ".join(str(pr.get("title") or f"PR {t.number}").split())
    body = clip(clean_text(str(pr.get("body") or "")), BODY_BUDGET // 2) or "(no description)"
    if failing:
        head = ("Make the failing CI checks pass on this PR without reverting its intent "
                f"(PR #{t.number}: {title}).")
    else:
        head = f"{extra.strip().splitlines()[0]} (PR #{t.number}: {title})." if extra.strip() \
            else f"Work on PR #{t.number}: {title}."
    parts = [head, "", f"PR title: {title}",
             f"Branch: {pr.get('headRefName') or '?'} -> {pr.get('baseRefName') or '?'}", "",
             "PR description:", body, ""]
    if failing:
        parts.append("Failing checks:")
        for c in failing:
            parts.append(f"- {c.get('name') or '?'} ({str(c.get('state') or '').lower()})"
                         + (f": {c['link']}" if c.get("link") else ""))
        parts.append("")
    for entry in logs:
        parts += [f"Failed log excerpt, run {entry['run_id']} ({', '.join(entry['checks'])}):",
                  "```", entry["log"], "```", ""]
    if failing and not logs:
        parts += ["(No GitHub Actions logs were available; reproduce the failure locally "
                  "with the project's test/lint commands.)", ""]
    if extra.strip() and failing:
        parts += ["Extra instructions:", extra.strip(), ""]
    parts += ["Keep the PR's change (do not revert or downgrade what it does); fix the code, "
              "tests or config so the checks pass.", "", f"PR: {pr.get('url') or t.url}"]
    return "\n".join(parts).strip() + "\n"


# ── the job seam ─────────────────────────────────────────────────────────────

@dataclass
class JobRequest:
    repo: Path
    goal: str
    budget: float
    branch: str                      # review branch the hand-off creates
    base_ref: Optional[str] = None   # commit the job starts from (None: repo HEAD)


@dataclass
class JobResult:
    job_id: str
    state: str                       # done / failed / cancelled / blocked / running
    branch: Optional[str] = None
    handoff_status: Optional[str] = None
    files: list = field(default_factory=list)
    tests: str = "no tests run"
    tests_ok: Optional[bool] = None
    cost: float = 0.0
    seconds: Optional[float] = None
    report: Optional[str] = None
    error: Optional[str] = None


def run_job_via_host(req: JobRequest) -> JobResult:
    """
    The same path as `awos "goal"`: a host-queue job run by the background
    worker (started if needed), watched until it finishes. base_ref and
    branch_name go into the job dir before the job is queued, exactly like
    a follow-up job (app/jobs.py submit_follow_up).
    """
    from scaffold.agent.app import jobs as J
    from scaffold.agent.app.session import App
    from scaffold.agent.host.models import JobSpec
    from scaffold.agent.host.worker import BASE_REF_FILE, BRANCH_NAME_FILE

    app = App(req.repo)
    app.compact_cards = True
    with app.term.quiet():
        app.onboarding()
        job = JobSpec.new(req.goal, req.repo, budget_usd=req.budget)
        jdir = app.queue.job_dir(job.id)
        jdir.mkdir(parents=True, exist_ok=True)
        if req.base_ref:
            (jdir / BASE_REF_FILE).write_text(req.base_ref + "\n", encoding="utf-8")
        (jdir / BRANCH_NAME_FILE).write_text(req.branch + "\n", encoding="utf-8")
        app.queue.add(job)
        app.states[job.id] = job.state
        app.term.line(f"  + queued {J.short(job.id)} (branch {req.branch} when done)")
        app.start_monitor()
        try:
            app.ensure_worker()
            try:
                job = app.wait_for(job.id) or job
            except KeyboardInterrupt:
                return JobResult(job_id=job.id, state="running",
                                 error="stopped watching; the job keeps running in the background")
        finally:
            app.stop_monitor()
    o = J.outcome(app.queue, job)
    files: list = []
    try:
        files = json.loads((app.queue.job_dir(job.id) / "handoff.json").read_text(encoding="utf-8")).get("files") or []
    except (OSError, ValueError):
        pass
    return JobResult(job_id=job.id, state=job.state, branch=o.branch, handoff_status=o.handoff_status,
                     files=files, tests=o.tests, tests_ok=o.tests_ok, cost=o.cost, seconds=o.seconds,
                     report=o.report, error=o.error)


# ── orchestration ────────────────────────────────────────────────────────────

@dataclass
class FixOptions:
    url: str
    dry_run: bool = False
    budget: Optional[float] = None
    allow_dirty: bool = False
    pr: bool = False                 # issue: push the branch and open a draft PR
    push: bool = False               # PR: push awos/pr-N-fix to origin
    goal: str = ""                   # extra instructions (PR with no failing checks: the goal)


def _git(run: Runner, repo: Path, *args: str) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(repo), *args], None)


def _q(s: str) -> str:
    return shlex.quote(s)


class Fixer:
    def __init__(self, opts: FixOptions, cwd: Path, run: Runner = default_runner,
                 job_runner: Callable[[JobRequest], JobResult] = run_job_via_host,
                 say: Callable[[str], None] = print,
                 which: Callable[[str], Optional[str]] = shutil.which):
        self.o, self.cwd, self.run, self.job_runner, self.say, self.which = (
            opts, Path(cwd), run, job_runner, say, which)
        self.t = parse_target(opts.url)
        self.repo: Optional[Path] = None

    # ── checks ───────────────────────────────────────────────────────────
    def check_repo(self) -> Path:
        out = _git(self.run, self.cwd, "rev-parse", "--show-toplevel")
        if out.returncode != 0 or not out.stdout.strip():
            raise FixError(f"run this inside your local clone of {self.t.slug} "
                           f"(not a git repository: {self.cwd})\n"
                           f"  gh repo clone {self.t.slug} && cd {self.t.repo}")
        repo = Path(out.stdout.strip())
        origin = _git(self.run, repo, "remote", "get-url", "origin")
        url = origin.stdout.strip() if origin.returncode == 0 else ""
        if not url:
            raise FixError(f"this repository has no 'origin' remote; expected a clone of {self.t.slug}")
        if not remote_matches(url, self.t):
            got = parse_remote(url)
            seen = f"{got[0]}/{got[1]}" if got else url
            raise FixError(f"this clone's origin is {seen}, but the URL is for {self.t.slug}. "
                           f"Run awos fix from a clone of {self.t.slug}.")
        self.repo = repo
        return repo

    def dirty(self) -> list[str]:
        out = _git(self.run, self.repo, "status", "--porcelain", "--untracked-files=no")
        return [ln[3:] for ln in out.stdout.splitlines() if len(ln) > 3]

    def check_clean(self, strict: bool) -> None:
        files = self.dirty()
        if not files:
            return
        shown = ", ".join(files[:5]) + (f" (+{len(files) - 5} more)" if len(files) > 5 else "")
        if strict:
            raise FixError(f"your working tree has uncommitted changes ({shown}). "
                           "Commit or stash them first"
                           + ("." if self.t.kind == "pull" else ", or pass --allow-dirty."))
        if not self.o.dry_run:
            self.say(f"  note: uncommitted changes ({shown}) are not part of the job; "
                     "it starts from your last commit.")

    def unique_branch(self, name: str) -> str:
        def taken(b: str) -> bool:
            return any(_git(self.run, self.repo, "rev-parse", "--verify", "--quiet",
                            f"refs/heads/{b}{sfx}").returncode == 0
                       for sfx in ("", "-failed", "-cancelled"))
        if not taken(name):
            return name
        for i in range(2, 100):
            if not taken(f"{name}-{i}"):
                return f"{name}-{i}"
        raise FixError(f"too many existing branches named {name}-*; delete some first")

    def default_branch(self) -> str:
        out = _git(self.run, self.repo, "symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
        ref = out.stdout.strip()
        if out.returncode == 0 and ref:
            return ref.split("/", 1)[1] if "/" in ref else ref
        for name in ("main", "master"):
            if _git(self.run, self.repo, "rev-parse", "--verify", "--quiet",
                    f"refs/heads/{name}").returncode == 0:
                return name
        return "main"

    def budget(self) -> float:
        if self.o.budget is not None:
            if self.o.budget < 0:
                raise FixError("--budget must be >= 0")
            return float(self.o.budget)
        from scaffold.agent.app.jobs import default_budget
        return default_budget()

    # ── entry ────────────────────────────────────────────────────────────
    def main(self) -> int:
        if self.t.kind == "pull" and self.o.pr:
            raise FixError("--pr is for issues; for a pull request use --push")
        if self.t.kind == "issue" and self.o.push:
            raise FixError("--push is for pull requests; for an issue use --pr (push + draft PR)")
        self.check_repo()
        check_gh(self.run, self.which, self.repo)
        return self.fix_issue() if self.t.kind == "issue" else self.fix_pr()

    def _dry(self, goal: str, commands: list[str], budget: float) -> int:
        self.say("Dry run: nothing was run, nothing changed.\n")
        self.say("Goal text:")
        self.say("-" * 72)
        self.say(goal.rstrip())
        self.say("-" * 72)
        self.say(f"\nBudget: ${budget:.2f}\nWould run:")
        for c in commands:
            self.say(f"  {c}")
        return 0

    def fix_issue(self) -> int:
        t = self.t
        self.check_clean(strict=not self.o.allow_dirty and not self.o.dry_run)
        issue = fetch_issue(self.run, t, self.repo)
        goal = build_issue_goal(t, issue, self.o.goal)
        branch = self.unique_branch(f"awos/issue-{t.number}-{slugify(str(issue.get('title') or ''))}")
        budget = self.budget()
        head = _git(self.run, self.repo, "rev-parse", "HEAD").stdout.strip() or None
        base = self.default_branch()
        title = " ".join(str(issue.get("title") or "").split())
        pr_cmds = [f"git push -u origin {branch}",
                   f"gh pr create --draft -R {t.slug} --base {base} --head {branch} "
                   f"--title {_q('Fix #%d: %s' % (t.number, title))} --body <report + 'Fixes #{t.number}'>"]
        if self.o.dry_run:
            cmds = [f"awos job (host queue + background worker) in {self.repo}, "
                    f"from {head[:12] if head else 'HEAD'}, budget ${budget:.2f}",
                    f"  -> review branch {branch} (local only)"]
            cmds += pr_cmds if self.o.pr else [f"(with --pr: {c})" for c in pr_cmds]
            return self._dry(goal, cmds, budget)
        self.say(f"Fixing {t.slug}#{t.number}: {title}")
        res = self.job_runner(JobRequest(repo=self.repo, goal=goal, budget=budget, branch=branch,
                                         base_ref=head))
        self.report(res, diff_base=base)
        if res.state == "running":
            return 0
        ok = res.state == "done" and res.handoff_status == "handed_off" and bool(res.branch)
        if ok:
            pr_title = f"Fix #{t.number}: {title}"
            body = self.pr_body(res, closes=t.number)
            if self.o.pr:
                return self.open_pr(res.branch, base, pr_title, body)
            self.say("  Open a draft PR (pushes the branch):")
            self.say(f"    git push -u origin {res.branch}")
            self.say(f"    gh pr create --draft -R {t.slug} --base {base} --head {res.branch} "
                     f"--title {_q(pr_title)} --body {_q('Fixes #%d' % t.number)}")
            self.say("    (or rerun with --pr to do both with the full report as the PR body)")
        elif self.o.pr:
            self.say("  Not opening a PR: the job did not finish with a reviewable branch.")
        return 0 if res.state == "done" else 1

    def fix_pr(self) -> int:
        t = self.t
        if not self.o.dry_run:
            self.check_clean(strict=True)
        pr = fetch_pr(self.run, t, self.repo)
        checks = fetch_checks(self.run, t, self.repo)
        failing = failing_checks(checks)
        pending = [c for c in checks if str(c.get("state") or "").upper() in PENDING_STATES]
        if not failing and not self.o.goal.strip():
            msg = f"PR #{t.number} has no failing checks"
            msg += f" ({len(pending)} still running)" if pending else ""
            self.say(msg + "; nothing to fix. Pass --goal \"...\" to work on it anyway.")
            return 0
        logs = fetch_failed_logs(self.run, t, failing, self.repo)
        goal = build_pr_goal(t, pr, failing, logs, self.o.goal)
        budget = self.budget()
        branch_hint = f"awos/pr-{t.number}-fix"
        head_ref = str(pr.get("headRefName") or "")
        push_cmd = f"git push -u origin {branch_hint}"
        if self.o.dry_run:
            cmds = [f"gh pr checkout {t.number} -R {t.slug}   (refused if the tree is dirty)",
                    f"awos job (host queue + background worker) on the PR head "
                    f"{str(pr.get('headRefOid') or '')[:12] or head_ref}, budget ${budget:.2f}",
                    f"  -> review branch {branch_hint} (local only)",
                    push_cmd if self.o.push else f"(with --push: {push_cmd})"]
            if self.dirty():
                cmds.insert(0, "(your working tree is dirty: a real run would stop here)")
            return self._dry(goal, cmds, budget)
        self.say(f"Fixing CI on {t.slug} PR #{t.number}: {pr.get('title') or ''}")
        _gh(self.run, ["pr", "checkout", str(t.number), "-R", t.slug], self.repo,
            f"check out PR #{t.number}")
        head = _git(self.run, self.repo, "rev-parse", "HEAD").stdout.strip() or None
        local_pr_branch = (_git(self.run, self.repo, "symbolic-ref", "--short", "-q", "HEAD")
                           .stdout.strip() or head_ref or (head or "HEAD")[:12])
        branch = self.unique_branch(branch_hint)
        res = self.job_runner(JobRequest(repo=self.repo, goal=goal, budget=budget, branch=branch,
                                         base_ref=head))
        self.report(res, diff_base=local_pr_branch)
        if res.state == "running":
            return 0
        ok = res.state == "done" and res.handoff_status == "handed_off" and bool(res.branch)
        if ok:
            if self.o.push:
                if self.secrets_block_push(res.branch):
                    return 1
                out = _git(self.run, self.repo, "push", "-u", "origin", res.branch)
                if out.returncode != 0:
                    err = (out.stderr or "").strip().splitlines()
                    self.say(f"  push failed: {err[-1] if err else out.returncode}")
                    return 1
                self.say(f"  Pushed {res.branch} to origin.")
            else:
                self.say("  Share it (nothing was pushed):")
                self.say(f"    git push -u origin {res.branch}")
            if pr.get("isCrossRepository"):
                owner = (pr.get("headRepositoryOwner") or {}).get("login") or "the fork owner"
                self.say(f"    (the PR branch lives in {owner}'s fork; push there if you can, "
                         "or comment with your branch)")
            else:
                self.say(f"    git push origin {res.branch}:{head_ref or '<pr-branch>'}   "
                         "# update the PR itself")
            self.say(f"    gh pr comment {t.number} -R {t.slug} --body "
                     f"{_q('CI fix on branch ' + res.branch + ' (by AWOS)')}")
        elif self.o.push:
            self.say("  Not pushing: the job did not finish with a reviewable branch.")
        return 0 if res.state == "done" else 1

    # ── output ───────────────────────────────────────────────────────────
    def report(self, res: JobResult, diff_base: str) -> None:
        s = self.say
        s("")
        if res.state == "running":
            s(f"  {res.error or 'still running'}. Check later: awos status {res.job_id[:8]}")
            return
        s(f"awos fix: {res.state}")
        if res.branch:
            label = res.branch + ("   (unfinished work, unverified)" if res.state != "done" else "")
            s(f"  Branch:  {label}")
        else:
            s(f"  Branch:  none ({res.handoff_status or 'no changes'})")
        if res.files:
            shown = ", ".join(res.files[:8]) + (f" (+{len(res.files) - 8} more)" if len(res.files) > 8 else "")
            s(f"  Files:   {shown}")
        else:
            s("  Files:   none")
        s(f"  Tests:   {res.tests}")
        s(f"  Cost:    ${res.cost:.4f}")
        if res.report:
            s(f"  Report:  {res.report}")
        if res.error and res.state != "done":
            s(f"  Error:   {res.error[:200]}")
        if res.branch:
            s("Next:")
            s(f"  git diff {diff_base}...{res.branch}")

    def pr_body(self, res: JobResult, closes: int) -> str:
        lines = ["Automated fix by AWOS (`awos fix`). Please review before merging.", "",
                 f"- Branch: `{res.branch}`",
                 f"- Files changed: {', '.join(f'`{f}`' for f in res.files) or 'none'}",
                 f"- Tests: {res.tests}",
                 f"- Cost: ${res.cost:.4f}", ""]
        if res.report:
            try:
                text = Path(res.report).read_text(encoding="utf-8")
                lines += ["<details><summary>AWOS report</summary>", "", clip(text, 20000), "",
                          "</details>", ""]
            except OSError:
                pass
        lines.append(f"Fixes #{closes}")
        return "\n".join(lines)

    def secrets_block_push(self, branch: str) -> bool:
        """Fail-closed secret scan of the branch's added lines before a push.

        The hand-off already refused to create a branch with a secret in it;
        this re-checks what is actually about to leave the machine (the branch
        may have been amended since). True = do not push. AWOS_SECRET_SCAN=0
        skips it: emergencies only."""
        from scaffold.agent import secret_scan as S
        if not S.scan_enabled():
            self.say(f"  WARNING: {S.ENV_SWITCH}=0 -- pushing WITHOUT the secret scan "
                     "(emergency bypass)")
            return False
        out = _git(self.run, self.repo, "-c", "core.quotePath=false", "diff", "--no-color",
                   "--no-ext-diff", "--no-textconv", "--no-renames", "-U0",
                   "--src-prefix=a/", "--dst-prefix=b/", f"{branch}^", branch)
        if out.returncode != 0:
            err = (out.stderr or "").strip().splitlines()
            self.say(f"  Not pushing: could not diff {branch} for the secret scan "
                     f"({err[-1] if err else out.returncode}).")
            return True
        found = S.scan_diff(out.stdout or "")
        if found:
            self.say(f"  Not pushing: secret scan found {len(found)} likely secret(s) in "
                     f"{branch}: {S.describe(found)}")
            self.say("  Remove them (or mark a false positive with an inline "
                     f"'{S.PRAGMA}' comment) and push by hand.")
            return True
        return False

    def open_pr(self, branch: str, base: str, title: str, body: str) -> int:
        if self.secrets_block_push(branch):
            return 1
        out = _git(self.run, self.repo, "push", "-u", "origin", branch)
        if out.returncode != 0:
            err = (out.stderr or "").strip().splitlines()
            self.say(f"  push failed: {err[-1] if err else out.returncode}")
            return 1
        out = self.run(["gh", "pr", "create", "--draft", "-R", self.t.slug, "--base", base,
                        "--head", branch, "--title", title, "--body", body], self.repo)
        if out.returncode != 0:
            err = (out.stderr or "").strip().splitlines()
            self.say(f"  gh pr create failed: {err[-1] if err else out.returncode}")
            return 1
        self.say(f"  Draft PR: {(out.stdout or '').strip()}")
        return 0


def run_fix(opts: FixOptions, cwd: Optional[Path] = None, **kw) -> int:
    """CLI entry: returns an exit code; user-facing problems print one line (exit 2)."""
    say = kw.get("say", print)
    try:
        return Fixer(opts, Path(cwd or Path.cwd()), **kw).main()
    except FixError as exc:
        say(f"awos fix: {exc}")
        return 2

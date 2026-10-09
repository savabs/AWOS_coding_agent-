"""M1 host hand-off: turn a finished job's workspace into a local review branch.

Spec: docs/specs/host_handoff.md

Contract:
- ``finish(job, workspace, outcome)`` creates ``awos/<id8>`` in the target repo
  from the job's base commit, containing exactly the workspace diff.
- Pure git plumbing with a temporary index: the user's HEAD, index and
  working tree are never read-modified-written.
- Never pushes; no network git operation exists in this module.
- Credential guard: a secret in the diff blocks the branch; a secret in the
  report is redacted. Findings never contain the secret value.
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BRANCH_PREFIX = "awos/"

# Paths never handed off, whatever the workspace contains.
EXCLUDE_GLOBS: Tuple[str, ...] = (
    ".env", ".env.*", "*/.env", "*/.env.*",
    ".awos/*", "*/.awos/*",
    "*.pem", "*.key", "id_rsa*", "*/id_rsa*",
)

SECRET_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("openai_like_sk", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{30,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
SECRET_ENV_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.I)
MIN_ENV_SECRET_LEN = 8

_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "AWOS host",
    "GIT_AUTHOR_EMAIL": "awos-host@localhost",
    "GIT_COMMITTER_NAME": "AWOS host",
    "GIT_COMMITTER_EMAIL": "awos-host@localhost",
}


class HandoffError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Credential guard
# --------------------------------------------------------------------------- #
def _env_secret_values(env: Optional[Dict[str, str]] = None) -> List[Tuple[str, str]]:
    env = dict(os.environ if env is None else env)
    out = []
    for name, val in env.items():
        if SECRET_ENV_NAME.search(name) and val and len(val) >= MIN_ENV_SECRET_LEN:
            out.append((name, val))
    return out


def scan_secrets(text: str, env: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Return findings ``{"kind", "name", "line"}`` — never the secret value."""
    findings: List[Dict[str, Any]] = []
    env_vals = _env_secret_values(env)
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, pat in SECRET_PATTERNS:
            if pat.search(line):
                findings.append({"kind": "pattern", "name": name, "line": lineno})
        for var, val in env_vals:
            if val in line:
                findings.append({"kind": "env_value", "name": var, "line": lineno})
    return findings


def redact_secrets(text: str, env: Optional[Dict[str, str]] = None) -> str:
    for var, val in _env_secret_values(env):
        text = text.replace(val, f"[REDACTED:{var}]")
    for name, pat in SECRET_PATTERNS:
        text = pat.sub(f"[REDACTED:{name}]", text)
    return text


# --------------------------------------------------------------------------- #
# git helpers
# --------------------------------------------------------------------------- #
def _git(args: List[str], *, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
         check: bool = True, input_text: Optional[str] = None) -> str:
    full_env = dict(os.environ)
    full_env.pop("GIT_INDEX_FILE", None)
    full_env.pop("GIT_DIR", None)
    full_env.pop("GIT_WORK_TREE", None)
    full_env["GIT_TERMINAL_PROMPT"] = "0"
    if env:
        full_env.update(env)
    proc = subprocess.run(["git", *args], cwd=cwd, env=full_env, input=input_text,
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise HandoffError(f"git {args[0]} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def _is_excluded(path: str) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in EXCLUDE_GLOBS)


def host_root() -> Path:
    return Path(os.environ.get("AWOS_HOST_DIR") or Path.home() / ".awos" / "host").expanduser()


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _minutes(job: Dict[str, Any], outcome: Dict[str, Any]) -> Optional[float]:
    if outcome.get("minutes") is not None:
        return round(float(outcome["minutes"]), 1)
    ca = job.get("created_at")
    if not ca:
        return None
    try:
        start = _dt.datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=_dt.timezone.utc)
        return round((_dt.datetime.now(_dt.timezone.utc) - start).total_seconds() / 60, 1)
    except ValueError:
        return None


def _build_tree(git_dir: str, workspace: str, base: str) -> Tuple[str, List[str], List[str]]:
    """Return (tree_sha, changed_files, excluded_files) using a temp index."""
    fd, idx = tempfile.mkstemp(prefix="awos-handoff-index-")
    os.close(fd)
    os.unlink(idx)  # git wants to create it itself
    env = {"GIT_INDEX_FILE": idx}
    base_args = [f"--git-dir={git_dir}", f"--work-tree={workspace}"]
    try:
        _git([*base_args, "read-tree", base], env=env)
        _git([*base_args, "add", "-A", "--", "."], cwd=workspace, env=env)
        names = _git([*base_args, "diff", "--cached", "--name-only", "-z", "--no-renames", base],
                     cwd=workspace, env=env)
        changed = [n for n in names.split("\0") if n]
        excluded = [n for n in changed if _is_excluded(n)]
        if excluded:
            _git([*base_args, "reset", "-q", base, "--", *excluded], cwd=workspace, env=env)
        tree = _git([*base_args, "write-tree"], env=env).strip()
        files = [n for n in changed if n not in excluded]
        return tree, files, excluded
    finally:
        try:
            os.unlink(idx)
        except FileNotFoundError:
            pass


def _dirty_files(repo: str) -> List[str]:
    out = _git(["status", "--porcelain", "-z", "--untracked-files=all"], cwd=repo, check=False)
    files = []
    for entry in out.split("\0"):
        if len(entry) > 3:
            files.append(entry[3:])
    return files


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def _render_report(job: Dict[str, Any], outcome: Dict[str, Any], info: Dict[str, Any],
                   diffstat: str, review_base: str) -> str:
    goal = str(job.get("goal", ""))
    fence = "````" if "```" in goal else "```"
    tests = outcome.get("tests") or {}
    if isinstance(tests, dict) and tests:
        t_line = f"{tests.get('status', 'unknown')}"
        if "passed" in tests or "failed" in tests:
            t_line += f" — passed {tests.get('passed', '?')}, failed {tests.get('failed', '?')}"
        if tests.get("command"):
            t_line += f" (`{tests['command']}`)"
    else:
        t_line = str(tests) if tests else "not reported"
    cost = outcome.get("cost_usd")
    budget = job.get("budget_usd")
    cost_line = (f"${float(cost):.4f}" if cost is not None else "not reported")
    if budget is not None:
        cost_line += f" of ${float(budget):.2f} budget"
    minutes = info.get("minutes")
    branch = info.get("branch")
    lines = [
        f"# AWOS job {job.get('id', '?')}",
        "",
        "## Goal (verbatim)",
        "",
        fence,
        goal,
        fence,
        "",
        f"- **Status:** {info['status']}",
        f"- **Branch:** `{branch}`" if branch else "- **Branch:** none created",
        f"- **Base commit:** `{info.get('base_commit') or '?'}`",
        f"- **Tests:** {t_line}",
        f"- **Cost:** {cost_line}",
        f"- **Minutes:** {minutes if minutes is not None else 'not reported'}",
        "",
    ]
    if outcome.get("summary"):
        lines += ["## Summary", "", str(outcome["summary"]), ""]
    lines += ["## What changed", ""]
    if info.get("files"):
        lines += ["```", diffstat.rstrip() or "(no stat)", "```", ""]
    else:
        lines += ["No files changed.", ""]
    if info.get("excluded"):
        lines += ["Excluded from hand-off (never committed): "
                  + ", ".join(f"`{p}`" for p in info["excluded"]), ""]
    if info.get("warnings"):
        lines += ["## Warnings", ""] + [f"- {w}" for w in info["warnings"]] + [""]
    if branch:
        lines += [
            "## How to review",
            "",
            "```sh",
            f"git diff {review_base}...{branch}",
            f"git log --stat {review_base}..{branch}",
            f"git merge --no-ff {branch}      # accept",
            f"git branch -D {branch}          # discard",
            "```",
            "",
            "Nothing was pushed. The branch exists only in your local repo.",
            "",
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def finish(job: Dict[str, Any], workspace: str, outcome: Dict[str, Any], *,
           job_dir: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Hand a finished job off as a local branch + report. Never pushes.

    ``env`` overrides the environment used for secret scanning (tests).
    """
    outcome = dict(outcome or {})
    job_id = str(job.get("id") or "")
    if not job_id:
        raise HandoffError("job has no id")
    id8 = job_id.replace("-", "")[:8]
    branch = f"{BRANCH_PREFIX}{id8}"
    jdir = Path(job_dir) if job_dir else host_root() / "jobs" / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    report_path = jdir / "report.md"
    handoff_path = jdir / "handoff.json"

    repo = str(Path(job.get("repo_path") or "").expanduser().resolve())
    workspace = str(Path(workspace).expanduser().resolve())
    info: Dict[str, Any] = {
        "job_id": job_id, "status": "error", "branch": None, "base_commit": None,
        "commit": None, "files": [], "excluded": [], "warnings": [],
        "report_path": str(report_path), "handoff_path": str(handoff_path),
        "secret_scan": {"diff": [], "report": []}, "created_at": _now_iso(),
        "minutes": _minutes(job, outcome),
    }
    diffstat = ""
    review_base = "main"
    try:
        git_dir = _git(["rev-parse", "--absolute-git-dir"], cwd=repo).strip()
        base_ref = job.get("base_commit") or outcome.get("base_commit")
        if not base_ref:
            base_ref = "HEAD"
            info["warnings"].append("no base_commit recorded; used the repo's current HEAD")
        base = _git(["rev-parse", "--verify", f"{base_ref}^{{commit}}"], cwd=repo).strip()
        info["base_commit"] = base
        cur = _git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=repo, check=False).strip()
        review_base = cur or base[:12]
        base_tree = _git(["rev-parse", f"{base}^{{tree}}"], cwd=repo).strip()

        tree, files, excluded = _build_tree(git_dir, workspace, base)
        info["files"], info["excluded"] = files, excluded

        if tree == base_tree:
            info["status"] = "no_changes"
        else:
            patch = _git(["diff-tree", "-p", "--no-color", base_tree, tree], cwd=repo)
            added = "\n".join(l[1:] for l in patch.splitlines()
                              if l.startswith("+") and not l.startswith("+++"))
            hits = scan_secrets(added, env)
            info["secret_scan"]["diff"] = hits
            if hits:
                info["status"] = "blocked_secret"
                names = sorted({h["name"] for h in hits})
                info["warnings"].append(
                    "credential guard: diff contains secret-like content ("
                    + ", ".join(names) + "); branch NOT created")
            else:
                diffstat = _git(["diff-tree", "--stat", "--no-color", base_tree, tree], cwd=repo)
                msg = (f"awos: {str(job.get('goal', '')).strip().splitlines()[0][:72] if str(job.get('goal', '')).strip() else 'job'}"
                       f"\n\nAWOS-Job: {job_id}\n")
                msg = redact_secrets(msg, env)
                ref = f"refs/heads/{branch}"
                existing = _git(["rev-parse", "-q", "--verify", ref], cwd=repo, check=False).strip()
                if existing:
                    ex_tree = _git(["rev-parse", f"{existing}^{{tree}}"], cwd=repo).strip()
                    ex_parent = _git(["rev-parse", f"{existing}^"], cwd=repo, check=False).strip()
                    if ex_tree == tree and ex_parent == base:
                        commit = existing
                        info["warnings"].append("branch already existed with identical content; reused")
                    else:
                        raise HandoffError(f"branch {branch} already exists with different content; "
                                           "refusing to overwrite")
                else:
                    commit = _git(["commit-tree", tree, "-p", base], cwd=repo,
                                  env=_GIT_IDENTITY, input_text=msg).strip()
                    _git(["update-ref", "-m", "awos handoff", ref, commit, ""], cwd=repo)
                info.update(status="handed_off", branch=branch, commit=commit)
                overlap = sorted(set(_dirty_files(repo)) & set(files))
                if overlap:
                    info["warnings"].append(
                        "your checkout has uncommitted changes in handed-off files ("
                        + ", ".join(overlap) + "); expect merge conflicts")
    except HandoffError as exc:
        info["status"] = "error"
        info["warnings"].append(str(exc))

    report = _render_report(job, outcome, info, diffstat, review_base)
    rep_hits = scan_secrets(report, env)
    if rep_hits:
        info["secret_scan"]["report"] = rep_hits
        report = redact_secrets(report, env)
        info["warnings"].append("credential guard: report contained secret-like text; redacted")
    report_path.write_text(report, encoding="utf-8")

    if info["status"] == "handed_off":
        info["summary"] = f"{len(info['files'])} file(s) on {branch}; review: git diff {review_base}...{branch}"
    elif info["status"] == "no_changes":
        info["summary"] = "no changes to hand off"
    elif info["status"] == "blocked_secret":
        info["summary"] = "hand-off blocked by credential guard"
    else:
        info["summary"] = "hand-off failed: " + (info["warnings"][-1] if info["warnings"] else "unknown")
    payload = json.dumps(info, indent=2, sort_keys=True)
    payload = redact_secrets(payload, env)  # belt and braces
    handoff_path.write_text(payload + "\n", encoding="utf-8")
    return info


# --------------------------------------------------------------------------- #
# live-proof demo
# --------------------------------------------------------------------------- #
def _demo() -> int:
    import shutil
    import uuid

    tmp = Path(tempfile.mkdtemp(prefix="awos-handoff-demo-"))
    repo, ws, host = tmp / "repo", tmp / "workspace", tmp / "host"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], cwd=str(repo))
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "README.md").write_text("demo\n")
    _git(["add", "-A"], cwd=str(repo))
    _git(["commit", "-q", "-m", "init"], cwd=str(repo), env=_GIT_IDENTITY)
    base = _git(["rev-parse", "HEAD"], cwd=str(repo)).strip()
    _git(["remote", "add", "origin", "https://example.invalid/never.git"], cwd=str(repo))

    shutil.copytree(repo, ws, ignore=shutil.ignore_patterns(".git"))
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (ws / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    (ws / ".env").write_text("OPENROUTER_API_KEY=dummy-not-real\n")

    status_before = _git(["status", "--porcelain"], cwd=str(repo))
    head_before = _git(["rev-parse", "HEAD"], cwd=str(repo)).strip()
    job = {"id": uuid.uuid4().hex, "goal": "Fix add() in calc.py: it subtracts instead of adding.",
           "repo_path": str(repo), "kind": "coding", "budget_usd": 0.5, "privacy": "local_only",
           "created_at": _now_iso(), "state": "running", "attempts": 1, "result": None,
           "base_commit": base}
    os.environ["AWOS_HOST_DIR"] = str(host)
    res = finish(job, str(ws), {"tests": {"status": "passed", "passed": 1, "failed": 0,
                                          "command": "pytest -q"},
                                "cost_usd": 0.0123, "minutes": 3.2,
                                "summary": "Changed `-` to `+` and added a test."})
    print("[handoff] status:", res["status"], "| branch:", res["branch"], "| commit:", res["commit"][:12])
    print("[handoff] excluded:", res["excluded"])
    print("[handoff] branches:", _git(["branch", "--list"], cwd=str(repo)).strip().replace("\n", " "))
    print("[handoff] remotes untouched:",
          _git(["for-each-ref", "refs/remotes"], cwd=str(repo)).strip() == "")
    print("[handoff] checkout unchanged:",
          _git(["status", "--porcelain"], cwd=str(repo)) == status_before
          and _git(["rev-parse", "HEAD"], cwd=str(repo)).strip() == head_before)
    print("----- git diff main...%s -----" % res["branch"])
    print(_git(["diff", f"main...{res['branch']}"], cwd=str(repo)))
    print("----- report.md -----")
    print(Path(res["report_path"]).read_text())
    print("[handoff] demo dir:", tmp)
    return 0


if __name__ == "__main__":
    if "--demo" in sys.argv:
        sys.exit(_demo())
    print("usage: python -m scaffold.agent.host.handoff --demo")
    sys.exit(2)

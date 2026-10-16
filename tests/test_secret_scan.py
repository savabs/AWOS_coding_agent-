"""Tests for scaffold/agent/secret_scan.py and its fail-closed wiring into the
review-branch hand-off (host/handoff.py) and the `awos fix` push (app/fix.py).

Every fake secret below is assembled by string concatenation so no complete
key-shaped literal exists in this file (GitHub push protection / scanners).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from scaffold.agent import secret_scan as S
from scaffold.agent.app import fix as F
from scaffold.agent.host import handoff
from scaffold.agent.host.handoff import finish

# ── fake secrets (concatenated; random-looking bodies, no placeholder words) ──
BODY = "Q7xk2Lp9" + "Rm4Tz8Vb" + "1Nc6Hd3J" + "f5Gs0WyA"        # 32 mixed chars
AWS = "AK" + "IA" + "Z7Q4K2LP" + "9RM4TZ8V"
ANTHROPIC = "sk-" + "ant-" + "api03-" + BODY
OPENAI = "sk-" + BODY
OPENAI_PROJ = "sk-" + "proj-" + BODY
GH_PAT_CLASSIC = "gh" + "p_" + BODY + "abcd"
GH_OAUTH = "gh" + "o_" + BODY + "abcd"
GH_SERVER = "gh" + "s_" + BODY + "abcd"
GH_FINE = "github" + "_pat_" + BODY + "_" + BODY
SLACK = "xo" + "xb-" + "1234567890-" + BODY
STRIPE = "sk" + "_live_" + BODY
GOOGLE = "AI" + "za" + "Sy" + BODY + "z9X"                      # AIza + 35
PEM = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
GENERIC_VAL = "Zq8rT2vL" + "x9pW4mK7" + "nB3c"
GENERIC_LINE = 'db_pass' + 'word = "' + GENERIC_VAL + '"'


def diff_for(path: str, added: list[str], start: int = 1) -> str:
    body = "".join(f"+{ln}\n" for ln in added)
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n"
            f"+++ b/{path}\n@@ -0,0 +{start},{len(added)} @@\n{body}")


# ── unit: rules ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("secret,rule", [
    (AWS, "aws_access_key"),
    (ANTHROPIC, "anthropic_api_key"),
    (OPENAI, "openai_api_key"),
    (OPENAI_PROJ, "openai_api_key"),
    (GH_PAT_CLASSIC, "github_token"),
    (GH_OAUTH, "github_token"),
    (GH_SERVER, "github_token"),
    (GH_FINE, "github_pat"),
    (SLACK, "slack_token"),
    (STRIPE, "stripe_live_key"),
    (GOOGLE, "google_api_key"),
    (PEM, "private_key"),
])
def test_each_rule_detects_and_never_leaks_value(secret, rule):
    found = S.scan_diff(diff_for("app/cfg.py", ["x = 1", f"KEY = '{secret}'"]))
    assert [(f.path, f.line, f.rule) for f in found] == [("app/cfg.py", 2, rule)]
    f = found[0]
    assert f.preview == secret[:4] + "…" and len(f.preview) <= 5
    for text in (str(f), repr(f), S.describe(found)):
        assert secret not in text and secret[4:12] not in text


def test_generic_high_entropy_assignment():
    found = S.scan_diff(diff_for("svc/settings.py", ["DEBUG = False", GENERIC_LINE]))
    assert [(f.line, f.rule) for f in found] == [(2, S.GENERIC_RULE)]
    assert GENERIC_VAL not in str(found[0])


@pytest.mark.parametrize("line", [
    'api_key = "' + "abcabcabcabcabcabc" + '"',            # low entropy
    'password = "' + "short1" + '"',                       # too short
    'token = "' + "<your-token-here-123456>" + '"',        # placeholder
    'secret = "' + "dummy" + "Value1234567890ab" + '"',    # placeholder word
    'api_key = "' + "${API_KEY_FROM_ENV_1234}" + '"',      # template
    'password = "' + "correcthorsebatterystaple" + '"',    # no digits: a phrase
    'token_url = "' + "https://example.org/oauth2/v1/t0ken" + '"',
    "KEY = '" + "sk-" + "your-key-here-xxxxxxxxxxxx" + "'",
    "AWS = '" + "AK" + "IAIOSFODNN7" + "EXAMPLE" + "'",     # AWS's documented example
    "KEY = '" + "sk-" + "a" * 40 + "'",                    # repeated filler
])
def test_placeholders_and_low_entropy_are_not_flagged(line):
    assert S.scan_diff(diff_for("app/cfg.py", [line])) == []


def test_anthropic_key_reported_once_not_also_as_openai():
    found = S.scan_diff(diff_for("a.py", [f"K = '{ANTHROPIC}'"]))
    assert [f.rule for f in found] == ["anthropic_api_key"]


def test_tests_dir_skips_generic_rule_only():
    d = diff_for("tests/test_db.py", [GENERIC_LINE, f"K = '{STRIPE}'"])
    assert [(f.line, f.rule) for f in S.scan_diff(d)] == [(2, "stripe_live_key")]
    # a "tests" FILE name outside a tests/ directory gets no exemption
    assert S.scan_diff(diff_for("src/tests.py", [GENERIC_LINE]))


def test_pragma_never_waives_a_known_key_format():
    # The model writes the diff, so it can write the pragma too.
    d = diff_for("a.py", [f"K = '{AWS}'  # awos:allow-secret", f"J = '{AWS}'"])
    assert [f.line for f in S.scan_diff(d)] == [1, 2]
    assert all(not f.allowed for f in S.scan_diff(d, include_allowed=True))


def test_pragma_waives_the_generic_rule_but_can_be_reported():
    val = "Q7m" + "x9Lp2" + "Rk4Zt8Wv"
    d = diff_for("a.py", [f"db_password = '{val}'  # awos:allow-secret",
                          f"api_secret = '{val}'"])
    assert [(f.line, f.rule) for f in S.scan_diff(d)] == [(2, S.GENERIC_RULE)]
    allf = S.scan_diff(d, include_allowed=True)
    assert [(f.line, f.allowed) for f in allf] == [(1, True), (2, False)]
    assert [f.line for f in S.blocking(allf)] == [2]


# ── unit: diff parsing ──────────────────────────────────────────────────────

def test_only_added_lines_are_scanned_with_new_file_line_numbers():
    d = (
        "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
        "@@ -1,3 +1,3 @@\n"
        f" ctx = '{OPENAI}'\n"            # context: already committed
        f"-old = '{STRIPE}'\n"            # removed: not added
        "+new = 1\n"
        " tail = 2\n"
        "@@ -10,0 +11,2 @@\n"
        "+ok = 3\n"
        f"+leak = '{AWS}'\n"
    )
    found = S.scan_diff(d)
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 12, "aws_access_key")]


def test_added_line_starting_with_plusplus_is_content_not_header():
    d = ("diff --git a/x.txt b/x.txt\n--- a/x.txt\n+++ b/x.txt\n@@ -0,0 +1,2 @@\n"
         f"+++ {AWS}\n+fine\n")
    found = S.scan_diff(d)
    assert [(f.path, f.line) for f in found] == [("x.txt", 1)]


def test_multiple_files_and_deleted_file():
    d = (diff_for("one.py", ["a = 1"])
         + "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n"
           "+++ /dev/null\n@@ -1 +0,0 @@\n" + f"-k = '{AWS}'\n"
         + diff_for("two.py", ["b = 2", f"t = '{GH_FINE}'"]))
    assert [(f.path, f.line, f.rule) for f in S.scan_diff(d)] == [("two.py", 2, "github_pat")]


def test_quoted_path_and_no_newline_marker():
    d = ('diff --git "a/sp ace.py" "b/sp ace.py"\n--- /dev/null\n+++ "b/sp ace.py"\n'
         f"@@ -0,0 +1 @@\n+k = '{SLACK}'\n\\ No newline at end of file\n")
    assert [(f.path, f.line) for f in S.scan_diff(d)] == [("sp ace.py", 1)]


def test_scan_enabled_switch():
    assert S.scan_enabled({}) and S.scan_enabled({"AWOS_SECRET_SCAN": "1"})
    assert not S.scan_enabled({"AWOS_SECRET_SCAN": "0"})


def test_entropy():
    assert S.shannon_entropy("aaaa") == 0
    assert S.shannon_entropy(GENERIC_VAL) > S.GENERIC_MIN_ENTROPY


# ── wiring: host hand-off (temp git repos) ──────────────────────────────────

IDENT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
         "GIT_COMMITTER_EMAIL": "t@t"}
CLEAN_ENV = {"PATH": "/usr/bin"}


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, env=dict(os.environ, **IDENT),
                          capture_output=True, text=True, check=check).stdout


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_HOST_DIR", str(tmp_path / "host"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    base = git(repo, "rev-parse", "HEAD").strip()
    ws = tmp_path / "ws"
    shutil.copytree(repo, ws, ignore=shutil.ignore_patterns(".git"))
    job = {"id": uuid.uuid4().hex, "goal": "Fix add()", "repo_path": str(repo),
           "kind": "coding", "budget_usd": 1.0, "state": "running", "base_commit": base}
    return repo, ws, job


def _no_branch(repo):
    return git(repo, "branch", "--list", "awos/*").strip() == ""


def _artifacts(res):
    jdir = Path(res["report_path"]).parent
    return (jdir / "report.md").read_text() + (jdir / "handoff.json").read_text()


def test_handoff_blocks_generic_secret_with_file_line_and_rule(setup):
    # The generic rule exists only in the added-lines layer: this proves it is wired.
    repo, ws, job = setup
    (ws / "svc").mkdir()
    (ws / "svc" / "settings.py").write_text("DEBUG = False\n" + GENERIC_LINE + "\n")
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and res["branch"] is None and _no_branch(repo)
    warn = " ".join(res["warnings"])
    assert "svc/settings.py:2 generic_secret_assignment (Zq8r…)" in warn
    assert "branch NOT created" in warn
    assert GENERIC_VAL not in _artifacts(res) and GENERIC_VAL not in json.dumps(res)


@pytest.mark.parametrize("secret,rule", [(STRIPE, "stripe_live_key"),
                                         (ANTHROPIC, "anthropic_api_key")])
def test_handoff_blocks_specific_rules_with_location(setup, secret, rule):
    repo, ws, job = setup
    (ws / "calc.py").write_text(f"def add(a, b):\n    return a + b\nK = '{secret}'\n")
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and _no_branch(repo)
    assert any(h.get("where") == "added_line" and h["path"] == "calc.py" and h["line"] == 3
               and h["name"] == rule for h in res["secret_scan"]["diff"])
    assert secret not in _artifacts(res) and secret[:12] not in json.dumps(res)


def test_handoff_clean_diff_still_hands_off(setup):
    repo, ws, job = setup
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "handed_off" and res["secret_scan"]["diff"] == []


def test_handoff_pragma_waives_generic_but_reports(setup):
    repo, ws, job = setup
    val = "Q7m" + "x9Lp2" + "Rk4Zt8Wv"
    (ws / "fixture.py").write_text(f"db_password = '{val}'  # awos:allow-secret\n")
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "handed_off" and res["branch"]
    assert any("waived" in w and "fixture.py:1" in w for w in res["warnings"])
    assert res["secret_scan"]["allowed"][0]["path"] == "fixture.py"
    assert val not in _artifacts(res)


def test_handoff_pragma_never_waives_a_key_format(setup):
    repo, ws, job = setup
    (ws / "fixture.py").write_text(f"K = '{AWS}'  # awos:allow-secret\n")
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and _no_branch(repo)
    assert AWS not in _artifacts(res)


def test_handoff_pragma_never_waives_owner_env_secret(setup):
    repo, ws, job = setup
    val = "own" + "Secret" + "Value42"
    (ws / "c.py").write_text(f"K = '{val}'  # awos:allow-secret\n")
    res = finish(job, str(ws), {"state": "done"}, env={"MY_SERVICE_TOKEN": val})
    assert res["status"] == "blocked_secret" and _no_branch(repo)


def test_handoff_escape_hatch_disables_pattern_scan(setup):
    repo, ws, job = setup
    # OPENAI is caught by both layers (whole-blob guard + added lines)
    (ws / "calc.py").write_text(f"K = '{OPENAI}'\nG = '{STRIPE}'\n")
    res = finish(job, str(ws), {"state": "done"}, env=dict(CLEAN_ENV, AWOS_SECRET_SCAN="0"))
    assert res["status"] == "handed_off"
    assert res["secret_scan"]["disabled"] is True
    assert any("AWOS_SECRET_SCAN=0" in w and "DISABLED" in w for w in res["warnings"])


def test_handoff_escape_hatch_still_blocks_owner_env_secret(setup):
    repo, ws, job = setup
    val = "own" + "Secret" + "Value42"
    (ws / "c.py").write_text(f"K = '{val}'\n")
    res = finish(job, str(ws), {"state": "done"},
                 env={"AWOS_SECRET_SCAN": "0", "MY_SERVICE_TOKEN": val})
    assert res["status"] == "blocked_secret" and _no_branch(repo)


def test_handoff_secret_only_in_unchanged_context_does_not_block(setup):
    repo, ws, job = setup
    (repo / "legacy.py").write_text(GENERIC_LINE + "\nx = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "legacy")
    job["base_commit"] = git(repo, "rev-parse", "HEAD").strip()
    shutil.copy(repo / "legacy.py", ws / "legacy.py")
    (ws / "legacy.py").write_text(GENERIC_LINE + "\nx = 2\n")
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "handed_off"


@pytest.mark.parametrize("target", ["scan_diff_tokens", "key_spans", "scan_line_tokens",
                                    "env_secret_values"])
def test_handoff_fails_closed_when_scanner_errors(setup, monkeypatch, target):
    """Any unexpected error in either scan layer: no branch, status error."""
    repo, ws, job = setup
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")

    def boom(*a, **k):
        raise RuntimeError("scanner broke")
    monkeypatch.setattr(S, target, boom)
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "error" and res["branch"] is None and _no_branch(repo)
    assert Path(res["report_path"]).exists()


def test_handoff_fails_closed_when_diff_cannot_be_computed(setup, monkeypatch):
    repo, ws, job = setup
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")

    def nodiff(*a, **k):
        raise handoff.HandoffError("git diff-tree failed: simulated")
    monkeypatch.setattr(handoff, "_scan_added_lines", nodiff)
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "error" and _no_branch(repo)


# ── wiring: `awos fix` push gate ────────────────────────────────────────────

ORIGIN = "git@github.com:acme/widget.git"
ISSUE_URL = "https://github.com/acme/widget/issues/12"
PR_URL = "https://github.com/acme/widget/pull/34"


@pytest.fixture
def clone(tmp_path, monkeypatch):
    monkeypatch.delenv("AWOS_SECRET_SCAN", raising=False)
    repo = tmp_path / "clone"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "remote", "add", "origin", ORIGIN)
    (repo / "app.py").write_text("print('hi')\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def make_branch(repo: Path, branch: str, content: str) -> None:
    """A one-commit review branch on HEAD, like the hand-off makes (no checkout)."""
    git(repo, "checkout", "-q", "-b", branch)
    (repo / "app.py").write_text(content)
    git(repo, "commit", "-q", "-am", "fix")
    git(repo, "checkout", "-q", "main")


class HybridRunner:
    """Real git against the temp clone, except push (recorded, never run);
    gh is faked. No network."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, cmd, cwd=None):
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[0] == "git":
            if "push" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                                  env=dict(os.environ, **IDENT))
        if cmd[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[1:3] == ["issue", "view"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(
                {"number": 12, "title": "Crash", "body": "b", "url": ISSUE_URL,
                 "labels": [], "comments": []}), "")
        if cmd[1:3] == ["pr", "create"]:
            return subprocess.CompletedProcess(cmd, 0, "https://github.com/acme/widget/pull/99\n", "")
        raise AssertionError(f"unexpected call {cmd}")

    def pushed(self):
        return [c for c in self.calls if "push" in c or c[1:3] == ["pr", "create"]]


def _fixer(repo, url=ISSUE_URL, runner=None, **opts):
    out: list[str] = []
    fx = F.Fixer(F.FixOptions(url=url, **opts), repo, run=runner or HybridRunner(),
                 say=out.append, which=lambda _: "/usr/bin/gh")
    fx.repo = repo
    return fx, out


def test_push_gate_blocks_branch_with_secret(clone):
    make_branch(clone, "awos/issue-12-x", f"print('hi')\nTOKEN = '{GH_PAT_CLASSIC}'\n")
    fx, out = _fixer(clone)
    assert fx.secrets_block_push("awos/issue-12-x") is True
    text = "\n".join(out)
    assert "Not pushing" in text and "app.py:2 github_token (ghp_…)" in text
    assert GH_PAT_CLASSIC not in text


def test_push_gate_allows_clean_branch(clone):
    make_branch(clone, "awos/issue-12-x", "print('hello')\n")
    fx, out = _fixer(clone)
    assert fx.secrets_block_push("awos/issue-12-x") is False and out == []


def test_push_gate_fails_closed_when_branch_cannot_be_diffed(clone):
    fx, out = _fixer(clone)
    assert fx.secrets_block_push("awos/does-not-exist") is True
    assert "could not diff" in "\n".join(out)


def test_push_gate_escape_hatch(clone, monkeypatch):
    make_branch(clone, "awos/issue-12-x", f"K = '{STRIPE}'\n")
    monkeypatch.setenv("AWOS_SECRET_SCAN", "0")
    fx, out = _fixer(clone)
    assert fx.secrets_block_push("awos/issue-12-x") is False
    assert "WITHOUT the secret scan" in "\n".join(out)


def test_awos_fix_issue_pr_flow_does_not_push_secret(clone):
    """End to end through Fixer.main: the job 'hands off' a branch that holds a
    secret; --pr must neither push nor open a PR."""
    runner = HybridRunner()

    def job_runner(req: F.JobRequest) -> F.JobResult:
        make_branch(clone, req.branch, f"print('hi')\nKEY = '{AWS}'\n")
        return F.JobResult(job_id="deadbeef1234", state="done", branch=req.branch,
                           handoff_status="handed_off", files=["app.py"])

    out: list[str] = []
    code = F.run_fix(F.FixOptions(url=ISSUE_URL, pr=True), clone, run=runner,
                     job_runner=job_runner, say=out.append, which=lambda _: "/usr/bin/gh")
    assert code == 1 and runner.pushed() == []
    assert "aws_access_key" in "\n".join(out) and AWS not in "\n".join(out)


def test_awos_fix_issue_pr_flow_pushes_clean_branch(clone):
    runner = HybridRunner()

    def job_runner(req: F.JobRequest) -> F.JobResult:
        make_branch(clone, req.branch, "print('fixed')\n")
        return F.JobResult(job_id="deadbeef1234", state="done", branch=req.branch,
                           handoff_status="handed_off", files=["app.py"])

    code = F.run_fix(F.FixOptions(url=ISSUE_URL, pr=True), clone, run=runner,
                     job_runner=job_runner, say=lambda _: None, which=lambda _: "/usr/bin/gh")
    assert code == 0 and len(runner.pushed()) == 2   # git push + gh pr create


# ── new rules (one rule set) ────────────────────────────────────────────────
import base64 as _b64
import re as _re
import time as _time


def _b64url(raw: str) -> str:
    return _b64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


BECH = "QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L"
GITLAB = "gl" + "pat-" + BODY[:22]
NPM = "np" + "m_" + BODY + "Kp3Z"
HF = "h" + "f_" + BODY + "Xy"
SENDGRID = "S" + "G." + BODY[:22] + "." + (BODY + BODY)[:43]
TWILIO = "A" + "C" + "3f9a1c7e" + "5b2d4086" + "e1f7c9a3" + "b5d20e64"
TWILIO_SK = "S" + "K" + "9e8d7c6b" + "5a4f3e2d" + "1c0b9a8f" + "7e6d5c4b"
AGE = "AGE-" + "SECRET-KEY-1" + BECH + BECH[:26]
PUTTY = "PuTTY-User-" + "Key-File-3: ssh-ed25519"
JWT = ".".join([_b64url('{"alg":"HS256","typ":"JWT"}'),
                _b64url('{"sub":"u-81723","role":"admin","iat":1760000000}'),
                BODY + "_x7Qm-2Lw9"])
URL_PW = "Zq8rT2vL" + "x9pW"
URL_CRED = "postgres://" + "svc_app:" + URL_PW + "@db.internal:5432/app"
BASIC_B64 = _b64.b64encode(("deploy:" + URL_PW + "4m").encode()).decode()
BASIC = "Authorization: " + "Basic " + BASIC_B64
AZURE_KEY = (BODY * 3)[:86] + "=="
AZURE = "DefaultEndpointsProtocol=https;AccountName=acct;Account" + "Key=" + AZURE_KEY
DISCORD = "M" + "TIzNDU2Nzg5MDEy" + "MzQ1Njc4OQ" + "." + "Gh7Kq2" + "." + BODY[:30]
TELEGRAM = "1234567" + "89" + ":" + "AAH" + BODY

NEW_RULES = [
    (GITLAB, "gitlab_token"), (NPM, "npm_token"), (HF, "huggingface_token"),
    (SENDGRID, "sendgrid_api_key"), (TWILIO, "twilio_key"), (TWILIO_SK, "twilio_key"),
    (AGE, "age_secret_key"), (PUTTY, "putty_private_key"), (JWT, "jwt"),
    (URL_CRED, "url_credentials"), (BASIC, "basic_auth_header"),
    (AZURE, "azure_storage_key"), (DISCORD, "discord_bot_token"),
    (TELEGRAM, "telegram_bot_token"),
]
ALL_KEY_FORMATS = NEW_RULES + [
    (AWS, "aws_access_key"), (ANTHROPIC, "anthropic_api_key"), (OPENAI, "openai_api_key"),
    (GH_PAT_CLASSIC, "github_token"), (GH_FINE, "github_pat"), (SLACK, "slack_token"),
    (STRIPE, "stripe_live_key"), (GOOGLE, "google_api_key"), (PEM, "private_key"),
]
# the part of each sample that is secret (url/basic/azure: only the credential)
SECRET_PART = {URL_CRED: URL_PW, BASIC: BASIC_B64, AZURE: AZURE_KEY}


@pytest.mark.parametrize("secret,rule", NEW_RULES)
def test_new_rules_detect_without_leaking(secret, rule):
    found = S.scan_diff(diff_for("app/cfg.py", ["x = 1", f"V = '{secret}'"]))
    assert [(f.line, f.rule) for f in found] == [(2, rule)]
    part = SECRET_PART.get(secret, secret)
    assert part not in str(found[0]) and part[4:12] not in S.describe(found)


@pytest.mark.parametrize("line", [
    "url = 'postgres://" + "user:password@localhost/db'",
    "url = 'https://" + "user:${DB_PASS}@db.internal/x'",
    "url = f'{scheme}://" + "{user}:{password}@{host}'",
    "url = 'https://" + "admin:admin@internal.host'",
    "url = 'https://" + "bob:" + URL_PW + "@example.com/x'",
    "url = 'redis://" + ":changeme@cache:6379'",
    "h = 'Authorization: " + "Basic " + _b64.b64encode(b"user:pass").decode() + "'",
    # header-only / signature-free JWT doc samples
    "t = '" + _b64url('{"alg":"none"}') + "." + _b64url('{"sub":"1"}') + "." + "x" * 20 + "'",
    "TELEGRAM = '" + "123456789:" + "a" * 35 + "'",
    "img = 'data:image/png;base64," + "iVBORw0KGgo" + AWS + "eyJhbGciOi" + "'",
])
def test_new_rules_skip_placeholders_and_data_uris(line):
    assert S.scan_diff(diff_for("app/cfg.py", [line])) == []


def test_data_uri_blanking_keeps_offsets_and_still_scans_the_rest():
    line = f"x = 'data:application/json;base64,{_b64url('{}' * 20)}'; k = '{AWS}'"
    blank = S.blank_data_uris(line)
    assert len(blank) == len(line) and AWS in blank
    assert [f.rule for f in S.scan_diff(diff_for("a.py", [line]))] == ["aws_access_key"]


@pytest.mark.parametrize("path", ["package-lock.json", "web/yarn.lock", "poetry.lock",
                                  "assets/logo.png", "fonts/x.woff2", "dist/app.zip"])
def test_diff_scan_skips_binary_media_and_lock_files(path):
    assert S.skip_path(path)
    assert S.scan_diff(diff_for(path, [f"k = '{AWS}'"])) == []


def test_diff_scan_still_reads_ordinary_files_named_like_locks():
    assert not S.skip_path("src/lock.py") and not S.skip_path("docs/yarn.lock.md")


# The two rule sets that existed before the merge. Every sample either one
# caught must still be caught by the merged RULES (placeholders off, as in the
# hand-off's whole-blob guard).
_OLD_HANDOFF = [
    r"\bsk-[A-Za-z0-9_\-]{20,}", r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{30,}",
    r"\bgithub_pat_[A-Za-z0-9_]{30,}", r"\bAKIA[0-9A-Z]{16}\b",
    r"\bxox[abpr]-[A-Za-z0-9\-]{10,}", r"\bAIza[0-9A-Za-z_\-]{35}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"]
_OLD_SCAN = [
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----",
    r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[0-9A-Z]{16}\b", r"\bsk-ant-[A-Za-z0-9_\-]{20,}",
    r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}", r"\bgithub_pat_[A-Za-z0-9_]{22,}",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}", r"\bxox[abposre]-[A-Za-z0-9\-]{10,}",
    r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}", r"\bAIza[0-9A-Za-z_\-]{35}"]
_OLD_SAMPLES = [
    "sk-" + "a" * 40, "sk-or-v1-" + "c" * 40, ANTHROPIC, OPENAI_PROJ,
    "gh" + "p_" + "b" * 36, GH_OAUTH, GH_SERVER, "gh" + "u_" + BODY, "gh" + "r_" + BODY,
    GH_FINE, "github" + "_pat_" + "A" * 22, AWS, "AK" + "IA" + "Q" * 16,
    "AS" + "IA" + "Z7Q4K2LP9RM4TZ8V", "AG" + "PA" + "Z7Q4K2LP9RM4TZ8V",
    "AI" + "DA" + "Z7Q4K2LP9RM4TZ8V", "AR" + "OA" + "Z7Q4K2LP9RM4TZ8V",
    "AN" + "PA" + "Z7Q4K2LP9RM4TZ8V", SLACK, "xo" + "xr-" + "1234567890",
    "xo" + "xs-" + BODY, "xo" + "xe-" + BODY, STRIPE, "rk" + "_live_" + BODY, GOOGLE,
    PEM, "-----BEGIN " + "PRIVATE KEY-----", "-----BEGIN " + " OPENSSH  PRIVATE KEY-----",
    "-----BEGIN " + "PGP PRIVATE KEY BLOCK-----", "-----BEGIN " + "EC2 PRIVATE KEY-----",
]


@pytest.mark.parametrize("sample", _OLD_SAMPLES)
def test_merged_rules_lose_nothing_either_old_rule_set_caught(sample):
    caught_before = any(_re.search(p, sample) for p in _OLD_HANDOFF + _OLD_SCAN)
    assert caught_before, "sample must exercise an old rule"
    assert S.key_spans(f"x = '{sample}'", placeholders=False)
    assert handoff.scan_secrets(f"x = '{sample}'", {})
    assert sample not in S.redact(f"x = '{sample}'", {})


def test_handoff_uses_the_single_rule_set():
    assert handoff.SECRET_PATTERNS is S.RULES
    assert handoff.SECRET_ENV_NAME is S.SECRET_ENV_NAME


# ── blocking claims ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("secret,rule", ALL_KEY_FORMATS)
def test_pragma_can_never_waive_any_key_format(secret, rule):
    for line in (f"K = '{secret}'  # awos:allow-secret",
                 f"K = '{secret}'  # AWOS:ALLOW-SECRET waived by reviewer"):
        allf = S.scan_diff(diff_for("src/a.py", [line]), include_allowed=True)
        assert [(f.rule, f.allowed) for f in allf] == [(rule, False)]
        assert S.blocking(allf)
        assert SECRET_PART.get(secret, secret) not in S.redact(line, {})


@pytest.mark.parametrize("secret,rule", NEW_RULES)
def test_handoff_blocks_new_rules(setup, secret, rule):
    repo, ws, job = setup
    (ws / "conf.py").write_text(f"V = '{secret}'\n")
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and _no_branch(repo)
    assert SECRET_PART.get(secret, secret) not in _artifacts(res)


def test_blob_guard_still_scans_lock_files(setup):
    """The diff scan skips lock files; the whole-blob guard does not."""
    repo, ws, job = setup
    (ws / "package-lock.json").write_text('{"token": "' + GH_PAT_CLASSIC + '"}\n')
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and _no_branch(repo)


def test_handoff_report_scan_error_still_redacts(setup, monkeypatch):
    repo, ws, job = setup
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    monkeypatch.setattr(handoff, "scan_secrets", lambda *a, **k: 1 / 0)
    job["goal"] = f"use key {AWS}"
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert AWS not in _artifacts(res)


# ── base-tree dedupe ────────────────────────────────────────────────────────

def _commit_legacy(repo, ws, job, text):
    (repo / "legacy.py").write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "legacy")
    job["base_commit"] = git(repo, "rev-parse", "HEAD").strip()


def test_dedupe_reformatted_committed_key_does_not_block(setup):
    repo, ws, job = setup
    _commit_legacy(repo, ws, job, f"KEYS = {{'a': '{OPENAI}', 'b': 1}}\n")
    (ws / "legacy.py").write_text(f"KEYS = {{\n    \"a\": \"{OPENAI}\",\n    \"b\": 2,\n}}\n")
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "handed_off", res["warnings"]


def test_dedupe_does_not_hide_a_new_key_on_a_reformatted_line(setup):
    repo, ws, job = setup
    _commit_legacy(repo, ws, job, f"A = '{OPENAI}'\n")
    (ws / "legacy.py").write_text(f"A = \"{OPENAI}\"; B = \"{STRIPE}\"\n")
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and _no_branch(repo)
    names = {h["name"] for h in res["secret_scan"]["diff"]}
    assert "stripe_live_key" in names and "openai_api_key" not in names


def test_dedupe_never_hides_owner_env_value_key_shaped(setup):
    """The committed token IS the owner's env secret: never deduped."""
    repo, ws, job = setup
    _commit_legacy(repo, ws, job, f"A = '{OPENAI}'\n")
    (ws / "legacy.py").write_text(f"A = \"{OPENAI}\"  # reformatted\n")
    res = finish(job, str(ws), {"state": "done"}, env={"OPENAI_API_KEY": OPENAI})
    assert res["status"] == "blocked_secret" and _no_branch(repo)
    assert OPENAI not in _artifacts(res)


def test_dedupe_never_hides_owner_env_value_free_form(setup):
    """A non key-shaped env secret already committed, only reformatted: blocks."""
    repo, ws, job = setup
    val = "own" + "Secret" + "Value42"
    _commit_legacy(repo, ws, job, f"A = '{val}'\n")
    (ws / "legacy.py").write_text(f"A = \"{val}\"\n")
    res = finish(job, str(ws), {"state": "done"}, env={"MY_SERVICE_TOKEN": val})
    assert res["status"] == "blocked_secret" and _no_branch(repo)
    assert any(h["name"] == "MY_SERVICE_TOKEN" and h.get("where") == "added_line"
               for h in res["secret_scan"]["diff"])
    assert val not in _artifacts(res)


def test_dedupe_fails_closed_when_git_grep_errors(setup, monkeypatch):
    repo, ws, job = setup
    _commit_legacy(repo, ws, job, f"A = '{OPENAI}'\n")
    (ws / "legacy.py").write_text(f"A = \"{OPENAI}\"\n")
    real_run = subprocess.run

    def broken(cmd, *a, **k):
        if cmd[:2] == ["git", "grep"]:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: simulated")
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(handoff.subprocess, "run", broken)
    res = finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and _no_branch(repo)


def test_dedupe_passes_tokens_on_stdin_never_argv(setup, monkeypatch):
    repo, ws, job = setup
    _commit_legacy(repo, ws, job, f"A = '{OPENAI}'\n")
    (ws / "legacy.py").write_text(f"A = \"{OPENAI}\"\n")
    real_run = subprocess.run
    argvs = []

    def spy(cmd, *a, **k):
        argvs.append(list(cmd))
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(handoff.subprocess, "run", spy)
    finish(job, str(ws), {"state": "done"}, env=CLEAN_ENV)
    assert any(c[:2] == ["git", "grep"] for c in argvs)
    assert all(OPENAI not in " ".join(c) for c in argvs)


# ── redact() ────────────────────────────────────────────────────────────────

def test_redact_replaces_key_formats_with_rule_names():
    text = f"aws {AWS} jwt {JWT} db {URL_CRED} ok"
    out = S.redact(text, {})
    assert AWS not in out and JWT not in out and URL_PW not in out
    assert "[REDACTED:aws_access_key]" in out and "[REDACTED:jwt]" in out
    assert "postgres://svc_app:[REDACTED:url_credentials]@db.internal" in out
    assert out.startswith("aws ") and out.endswith(" ok")


def test_redact_env_values_and_explicit_env():
    val = "own" + "Secret" + "Value42"
    assert S.redact(f"v={val}", {"MY_TOKEN": val}) == "v=[REDACTED:MY_TOKEN]"
    assert S.redact(f"v={val}", {"HOME": val}) == f"v={val}"   # not a secret name
    assert S.redact("v=short", {"MY_TOKEN": "short"}) == "v=short"


def test_redact_defaults_to_os_environ(monkeypatch):
    val = "env" + "Default" + "Secret99"
    monkeypatch.setenv("AWOS_TEST_PASSWORD", val)
    assert val not in S.redact(f"x {val} y")
    assert "[REDACTED:AWOS_TEST_PASSWORD]" in S.redact(f"x {val} y")


def test_redact_removes_whole_private_key_blocks():
    body = "MIIEpAIBAAKCAQEA" + BODY + "\n" + BODY[::-1]
    pem = PEM + "\n" + body + "\n-----END " + "RSA PRIVATE KEY-----"
    out = S.redact("before\n" + pem + "\nafter", {})
    assert out == "before\n[REDACTED:private_key]\nafter"
    unterminated = S.redact(PEM + "\n" + body, {})
    assert BODY not in unterminated
    ppk = PUTTY + "\nPrivate-Lines: 1\n" + BODY + "\nPrivate-MAC: " + "ab12" * 8
    assert BODY not in S.redact(ppk, {})


def test_redact_generic_assignment_and_placeholders_off():
    assert GENERIC_VAL not in S.redact(GENERIC_LINE, {})
    # no placeholder heuristics: a key dressed as a demo is still redacted
    dressed = ANTHROPIC + "-example"
    assert ANTHROPIC not in S.redact(f"k={dressed}", {})


def test_redact_never_raises():
    class Bad:
        def __str__(self):
            raise ValueError("no")
    assert S.redact(None) == ""
    assert S.redact("") == ""
    assert S.redact(12345, {}) == "12345"
    assert S.redact(Bad()) == S.REDACT_ERROR
    assert S.redact("x", env={"A_KEY": object()}) == "x"    # odd env value types


def test_redact_fails_closed_on_internal_error(monkeypatch):
    monkeypatch.setattr(S, "key_spans", lambda *a, **k: 1 / 0)
    assert S.redact(f"k={AWS}", {}) == S.REDACT_ERROR


def test_redact_obj_walks_structures():
    val = "own" + "Secret" + "Value42"
    obj = {"a": [AWS, {"b": f'q"{val}"'}], "n": 3, "t": (OPENAI,)}
    out = S.redact_obj(obj, {"SVC_TOKEN": val})
    flat = json.dumps(out)
    assert AWS not in flat and val not in flat and OPENAI not in flat
    assert out["n"] == 3 and obj["a"][0] == AWS   # input untouched


def test_redact_is_fast_enough_per_message():
    msg = ("Traceback: request failed for model x. " * 50 + f" key {AWS} ") * 4
    t0 = _time.perf_counter()
    for _ in range(100):
        S.redact(msg, {})
    assert (_time.perf_counter() - t0) / 100 < 0.02
    # pathological inputs stay linear: one huge identifier run, huge base64
    for big in ("a_" * 250_000 + "password" + " = 'x'", _b64.b64encode(os.urandom(600_000)).decode()):
        t0 = _time.perf_counter()
        S.redact(big, {})
        assert _time.perf_counter() - t0 < 3.0

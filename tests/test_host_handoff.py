"""Tests for scaffold/agent/host/handoff.py (spec: docs/specs/host_handoff.md)."""
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from scaffold.agent.host import handoff
from scaffold.agent.host.handoff import finish, scan_secrets, redact_secrets

IDENT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
         "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *args):
    import os
    env = dict(os.environ, **IDENT)
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                          text=True, check=True).stdout


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_HOST_DIR", str(tmp_path / "host"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "old.txt").write_text("old\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    base = git(repo, "rev-parse", "HEAD").strip()
    ws = tmp_path / "ws"
    shutil.copytree(repo, ws, ignore=shutil.ignore_patterns(".git"))
    job = {"id": uuid.uuid4().hex, "goal": "Fix add()\n  keep `verbatim` text",
           "repo_path": str(repo), "kind": "coding", "budget_usd": 1.0,
           "privacy": "local_only", "created_at": "2026-10-10T00:00:00+00:00",
           "state": "running", "attempts": 1, "result": None, "base_commit": base}
    return repo, ws, job, base


CLEAN_ENV = {"PATH": "/usr/bin"}


def test_branch_contains_exactly_workspace_diff(setup):
    repo, ws, job, base = setup
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (ws / "new.py").write_text("x = 1\n")
    (ws / "old.txt").unlink()
    res = finish(job, str(ws), {"cost_usd": 0.01, "minutes": 2}, env=CLEAN_ENV)
    assert res["status"] == "handed_off"
    br = f"awos/{job['id'][:8]}"
    assert res["branch"] == br
    assert git(repo, "rev-parse", f"{br}^").strip() == base
    names = git(repo, "diff", "--name-status", f"main...{br}").split("\n")
    assert sorted(n for n in names if n) == ["A\tnew.py", "D\told.txt", "M\tcalc.py"]
    assert git(repo, "show", f"{br}:calc.py") == "def add(a, b):\n    return a + b\n"


def test_user_checkout_index_and_head_untouched(setup):
    repo, ws, job, _ = setup
    (repo / "calc.py").write_text("# user's wip\n")
    git(repo, "add", "calc.py")  # staged user change
    (repo / "scratch.txt").write_text("untracked\n")
    before = (git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"),
              git(repo, "diff", "--cached"))
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "handed_off"
    after = (git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"),
             git(repo, "diff", "--cached"))
    assert before == after
    assert (repo / "calc.py").read_text() == "# user's wip\n"
    assert any("uncommitted changes" in w and "calc.py" in w for w in res["warnings"])


def test_report_and_handoff_json(setup):
    repo, ws, job, _ = setup
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    res = finish(job, str(ws), {"tests": {"status": "passed", "passed": 3, "failed": 0},
                                "cost_usd": 0.0421, "minutes": 7.5}, env=CLEAN_ENV)
    jdir = Path(handoff.host_root()) / "jobs" / job["id"]
    report = (jdir / "report.md").read_text()
    assert job["goal"] in report  # verbatim, multi-line
    assert f"git diff main...awos/{job['id'][:8]}" in report
    assert "$0.0421" in report and "7.5" in report and "passed 3" in report
    assert "calc.py" in report
    data = json.loads((jdir / "handoff.json").read_text())
    assert data["branch"] == res["branch"] and data["commit"] == res["commit"]


def test_env_and_secret_files_excluded(setup):
    repo, ws, job, _ = setup
    (ws / ".env").write_text("X=1\n")
    (ws / ".awos").mkdir()
    (ws / ".awos" / "state.json").write_text("{}")
    (ws / "server.pem").write_text("cert\n")
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "handed_off"
    assert set(res["excluded"]) == {".env", ".awos/state.json", "server.pem"}
    tree = git(repo, "ls-tree", "-r", "--name-only", res["branch"])
    assert ".env" not in tree and ".awos" not in tree and "server.pem" not in tree


def test_secret_pattern_in_diff_blocks_branch(setup):
    repo, ws, job, _ = setup
    fake = "sk-or-v1-" + "a" * 40
    (ws / "cfg.py").write_text(f"KEY = '{fake}'\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and res["branch"] is None
    assert git(repo, "branch", "--list", "awos/*").strip() == ""
    jdir = Path(res["report_path"]).parent
    for f in ("report.md", "handoff.json"):
        assert fake not in (jdir / f).read_text()


def test_env_secret_value_in_diff_blocks(setup):
    repo, ws, job, _ = setup
    env = {"MY_SERVICE_TOKEN": "plainvalue12345"}
    (ws / "calc.py").write_text("TOKEN='plainvalue12345'\n")
    res = finish(job, str(ws), {}, env=env)
    assert res["status"] == "blocked_secret"
    assert res["secret_scan"]["diff"][0]["name"] == "MY_SERVICE_TOKEN"
    assert "plainvalue12345" not in Path(res["handoff_path"]).read_text()


def test_secret_in_report_is_redacted(setup):
    repo, ws, job, _ = setup
    env = {"OPENROUTER_API_KEY": "zzzsecretzzz999"}
    job["goal"] = "use key zzzsecretzzz999 please"
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    res = finish(job, str(ws), {"summary": "ghp_" + "b" * 36}, env=env)
    assert res["status"] == "handed_off"
    report = Path(res["report_path"]).read_text()
    assert "zzzsecretzzz999" not in report and "ghp_bbbb" not in report
    assert "[REDACTED:OPENROUTER_API_KEY]" in report
    assert "zzzsecretzzz999" not in git(repo, "log", "-1", "--format=%B", res["branch"])


def test_no_changes(setup):
    repo, ws, job, _ = setup
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "no_changes" and res["branch"] is None
    assert git(repo, "branch", "--list", "awos/*").strip() == ""


def test_idempotent_rerun_and_conflict_refusal(setup):
    repo, ws, job, _ = setup
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    r1 = finish(job, str(ws), {}, env=CLEAN_ENV)
    r2 = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert r2["status"] == "handed_off" and r2["commit"] == r1["commit"]
    (ws / "calc.py").write_text("something else\n")
    r3 = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert r3["status"] == "error"
    assert git(repo, "rev-parse", r1["branch"]).strip() == r1["commit"]


def test_missing_base_commit_falls_back_to_head_with_warning(setup):
    repo, ws, job, base = setup
    del job["base_commit"]
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["base_commit"] == base
    assert any("no base_commit" in w for w in res["warnings"])


def test_workspace_as_git_worktree_and_gitignore(setup, tmp_path):
    repo, _, job, base = setup
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "--detach", str(wt), base)
    (wt / ".gitignore").write_text("build/\n")
    (wt / "build").mkdir()
    (wt / "build" / "out.bin").write_text("junk")
    (wt / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    res = finish(job, str(wt), {}, env=CLEAN_ENV)
    assert res["status"] == "handed_off"
    assert sorted(res["files"]) == [".gitignore", "calc.py"]


def test_never_pushes_remote_untouched(setup, tmp_path):
    repo, ws, job, _ = setup
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "handed_off"
    assert git(remote, "for-each-ref").strip() == ""
    src = Path(handoff.__file__).read_text()
    assert '"push"' not in src and "'push'" not in src


def test_bad_repo_gives_error_and_report(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_HOST_DIR", str(tmp_path / "host"))
    job = {"id": uuid.uuid4().hex, "goal": "g", "repo_path": str(tmp_path / "nope")}
    (tmp_path / "nope").mkdir()
    res = finish(job, str(tmp_path), {}, env=CLEAN_ENV)
    assert res["status"] == "error"
    assert Path(res["report_path"]).exists()


def test_scan_and_redact_helpers():
    txt = "a AKIAABCDEFGHIJKLMNOP b\n-----BEGIN RSA PRIVATE KEY-----"
    names = {f["name"] for f in scan_secrets(txt, {})}
    assert names == {"aws_access_key", "private_key_block"}
    assert "AKIA" not in redact_secrets(txt, {})
    assert scan_secrets("API_KEY=short", {"API_KEY": "short"}) == []  # < min len ignored


# --------------------------------------------------------------------------- #
# Regression tests for review findings (host-handoff fix pass)
# --------------------------------------------------------------------------- #
def _blob_sha(repo, data: bytes) -> str:
    return subprocess.run(["git", "hash-object", "--stdin"], cwd=repo, input=data,
                          capture_output=True, check=True).stdout.decode().strip()


def _has_object(repo, sha) -> bool:
    return subprocess.run(["git", "cat-file", "-e", sha], cwd=repo,
                          capture_output=True).returncode == 0


def test_excluded_file_blob_not_written_to_user_repo(setup):
    repo, ws, job, _ = setup
    env_body = b"OPENROUTER_API_KEY=excluded-blob-probe-123\n"
    (ws / ".env").write_bytes(env_body)
    (ws / "server.key").write_bytes(b"key-blob-probe-456\n")
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "handed_off"
    assert not _has_object(repo, _blob_sha(repo, env_body))
    assert not _has_object(repo, _blob_sha(repo, b"key-blob-probe-456\n"))
    # the handed-off content itself is present and the repo is consistent
    assert _has_object(repo, _blob_sha(repo, b"def add(a, b):\n    return a + b\n"))
    assert subprocess.run(["git", "fsck", "--no-dangling"], cwd=repo,
                          capture_output=True).returncode == 0


def test_blocked_secret_blob_not_written_to_user_repo(setup):
    repo, ws, job, _ = setup
    body = ("KEY = 'sk-or-v1-" + "c" * 40 + "'\n").encode()
    (ws / "cfg.py").write_bytes(body)
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret"
    assert not _has_object(repo, _blob_sha(repo, body))


def test_secret_in_binary_file_blocks(setup):
    repo, ws, job, _ = setup
    (ws / "blob.bin").write_bytes(b"\x00\x01\x02 sk-or-v1-" + b"d" * 40 + b"\x00\xff")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and res["branch"] is None
    assert git(repo, "branch", "--list", "awos/*").strip() == ""


def test_secret_in_minus_diff_attribute_file_blocks(setup):
    repo, ws, job, _ = setup
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("*.dat -diff\n")
    (ws / "data.dat").write_text("token ghp_" + "e" * 36 + "\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret"


def test_clean_binary_file_still_handed_off(setup):
    repo, ws, job, _ = setup
    (ws / "img.bin").write_bytes(b"\x00\x01\x02\x03 harmless")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "handed_off" and "img.bin" in res["files"]


@pytest.mark.parametrize("bad_id", ["../../escape-x", "a/b", "..", "x y", "id\n"])
def test_job_id_path_traversal_rejected(setup, tmp_path, bad_id):
    repo, ws, job, _ = setup
    job["id"] = bad_id
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    with pytest.raises(handoff.HandoffError):
        finish(job, str(ws), {}, env=CLEAN_ENV)
    assert not list(tmp_path.parent.glob("escape-x"))
    assert not list(tmp_path.rglob("report.md"))


def test_dirty_files_parses_renames(setup):
    repo, ws, job, _ = setup
    git(repo, "mv", "calc.py", "calc2.py")
    dirty = handoff._dirty_files(str(repo))
    assert "calc2.py" in dirty and "calc.py" in dirty
    assert ".py" not in dirty
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert any("uncommitted changes" in w and "calc.py" in w for w in res["warnings"])


def test_handoff_json_redacts_json_escaped_env_secret(setup):
    repo, ws, job, _ = setup
    secret = 'tok"en\\value-123'
    env = {"MY_SERVICE_TOKEN": secret}
    job_dir = repo.parent / ("jd_" + secret)  # path carries the secret into handoff.json
    res = finish(job, str(ws), {}, job_dir=str(job_dir), env=env)
    raw = Path(res["handoff_path"]).read_text()
    assert json.dumps(secret)[1:-1] not in raw
    assert "[REDACTED:MY_SERVICE_TOKEN]" in raw

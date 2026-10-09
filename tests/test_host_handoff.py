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


# --------------------------------------------------------------------------- #
# Regression tests for re-review findings (host-handoff fix pass r2)
# --------------------------------------------------------------------------- #
def _pack_listing(repo):
    pack = repo / ".git" / "objects" / "pack"
    return sorted(p.name for p in pack.iterdir()) if pack.exists() else []


def test_big_file_over_bigfilethreshold_promoted_and_repo_fsck_clean(setup):
    repo, ws, job, _ = setup
    git(repo, "config", "core.bigFileThreshold", "10")
    packs_before = _pack_listing(repo)
    body = b"x" * 501 + b"\n"
    (ws / "big.txt").write_bytes(body)
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "handed_off"
    assert _pack_listing(repo) == packs_before
    br = f"awos/{job['id'][:8]}"
    shown = subprocess.run(["git", "show", f"{br}:big.txt"], cwd=repo,
                           capture_output=True, check=True).stdout
    assert shown == body
    fsck = subprocess.run(["git", "fsck", "--strict", "--no-dangling"], cwd=repo,
                          capture_output=True, text=True)
    assert fsck.returncode == 0, fsck.stdout + fsck.stderr


def test_missing_object_after_promotion_fails_closed(setup, monkeypatch):
    repo, ws, job, _ = setup
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    # simulate a promotion that copies nothing (e.g. object stuck in a staging pack)
    real_git = handoff._git

    def no_copy(repo_, senv, commit, base):
        out = real_git(["rev-list", "--objects", commit, "--not", base], cwd=repo_, env=senv)
        handoff._verify_objects_in_repo(repo_, out)

    monkeypatch.setattr(handoff, "_promote_objects", no_copy)
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "error" and res["branch"] is None
    assert git(repo, "branch", "--list", "awos/*").strip() == ""
    assert subprocess.run(["git", "fsck", "--no-dangling"], cwd=repo,
                          capture_output=True).returncode == 0


@pytest.mark.parametrize("enc", ["utf-16-le", "utf-16-be", "utf-16"])
def test_utf16_secret_in_binary_file_blocks(setup, enc):
    repo, ws, job, _ = setup
    (ws / "u16.bin").write_bytes(("token = ghp_" + "f" * 36 + "\n").encode(enc))
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and res["branch"] is None


def test_utf16_odd_offset_secret_blocks(setup):
    repo, ws, job, _ = setup
    (ws / "u16o.bin").write_bytes(b"\x00\x01\x02" + ("AKIA" + "Q" * 16 + " ").encode("utf-16-le"))
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret"


# --------------------------------------------------------------------------- #
# Regression tests for review r3 findings
# --------------------------------------------------------------------------- #
SK = "sk-A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def _no_awos_branch(repo):
    return git(repo, "branch", "--list", "awos/*").strip() == ""


def test_secret_in_file_name_blocks(setup):
    repo, ws, job, _ = setup
    (ws / f"{SK}.txt").write_text("")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and _no_awos_branch(repo)
    assert SK not in Path(res["report_path"]).read_text()
    assert SK not in Path(res["handoff_path"]).read_text()


def test_secret_in_directory_name_blocks(setup):
    repo, ws, job, _ = setup
    d = ws / ("ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789")
    d.mkdir()
    (d / "x.txt").write_text("hi\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and _no_awos_branch(repo)


def test_utf16_secret_after_8000_bytes_blocks(setup):
    repo, ws, job, _ = setup
    (ws / "t.txt").write_bytes(b"x" * 9000 + b"\n\x00" + SK.encode("utf-16-le") + b"\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and _no_awos_branch(repo)


@pytest.mark.parametrize("enc", ["utf-32", "utf-32-le", "utf-32-be"])
def test_utf32_secret_blocks(setup, enc):
    repo, ws, job, _ = setup
    (ws / "f.bin").write_bytes(SK.encode(enc))
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and _no_awos_branch(repo)


@pytest.mark.parametrize("enc", ["utf-16", "utf-16-be", "utf-32", "utf-32-be"])
def test_non_ascii_env_secret_in_wide_encoding_blocks(setup, enc):
    repo, ws, job, _ = setup
    env = {"API_SECRET": "Pässwörd-Geheim99"}
    (ws / "f.bin").write_bytes(("v=" + env["API_SECRET"]).encode(enc))
    res = finish(job, str(ws), {}, env=env)
    assert res["status"] == "blocked_secret" and _no_awos_branch(repo)
    assert res["secret_scan"]["diff"][0]["name"] == "API_SECRET"


def test_clean_filter_and_hooks_never_run(setup, tmp_path):
    repo, ws, job, _ = setup
    marker = tmp_path / "ran"
    clean = tmp_path / "fake-lfs-clean.sh"
    clean.write_text('#!/bin/sh\nmkdir -p "$GIT_DIR/lfs/objects"\n'
                     'cat > "$GIT_DIR/lfs/objects/blob"\n'
                     f'echo clean >> "{marker}"\n'
                     'echo "version https://git-lfs.github.com/spec/v1"\n')
    clean.chmod(0o755)
    git(repo, "config", "filter.lfs.clean", str(clean))
    git(repo, "config", "filter.lfs.required", "true")
    fsm = tmp_path / "fsmonitor.sh"
    fsm.write_text(f'#!/bin/sh\necho fsmonitor >> "{marker}"\n')
    fsm.chmod(0o755)
    git(repo, "config", "core.fsmonitor", str(fsm))
    hook = repo / ".git" / "hooks" / "reference-transaction"
    hook.write_text(f'#!/bin/sh\necho hook >> "{marker}"\n')
    hook.chmod(0o755)
    (ws / ".gitattributes").write_text("*.bin filter=lfs\n")
    (ws / "s.bin").write_text(f"token={SK}\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret" and _no_awos_branch(repo)
    assert not (repo / ".git" / "lfs").exists()
    # the same config on a clean run: the raw (unfiltered) blob is handed off
    (ws / "s.bin").write_text("harmless\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "handed_off"
    assert git(repo, "show", f"{res['branch']}:s.bin") == "harmless\n"
    assert not (repo / ".git" / "lfs").exists()
    assert not marker.exists()


def test_multiline_env_secret_redacted_in_report_and_blocks_diff(setup):
    repo, ws, job, _ = setup
    env = {"MY_TOKEN": "Zq9vXw8uYt7s\nRr6qPp5oNn4m"}
    job["goal"] = "use " + env["MY_TOKEN"] + " now"
    res = finish(job, str(ws), {}, env=env)
    report = Path(res["report_path"]).read_text()
    assert "Zq9vXw8uYt7s" not in report and "Rr6qPp5oNn4m" not in report
    (ws / "calc.py").write_text("t = '''" + env["MY_TOKEN"] + "'''\n")
    res = finish(job, str(ws), {}, env=env)
    assert res["status"] == "blocked_secret"


def test_overlapping_env_secrets_redacted_longest_first(setup):
    repo, ws, job, _ = setup
    env = {"A_TOKEN": "Qwertyuiop12", "B_TOKEN": "Qwertyuiop12ZZsuffixLEAK99"}
    job["goal"] = "goal " + env["B_TOKEN"]
    res = finish(job, str(ws), {}, env=env)
    report = Path(res["report_path"]).read_text()
    assert "ZZsuffixLEAK99" not in report and "[REDACTED:B_TOKEN]" in report


def test_latin1_text_file_handed_off_not_crash(setup):
    repo, ws, job, _ = setup
    (ws / "l.txt").write_bytes(b"caf\xe9\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "handed_off" and Path(res["report_path"]).exists()


def test_git_config_parameters_in_env_ignored(setup, monkeypatch):
    repo, ws, job, _ = setup
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.bigfilethreshold'='1k'")
    (ws / "big.txt").write_bytes(b"y" * 5000)
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "handed_off"


@pytest.mark.parametrize("bad_id", ["-", "--------", "a" * 300])
def test_degenerate_job_ids_rejected_up_front(setup, bad_id):
    repo, ws, job, _ = setup
    job["id"] = bad_id
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    with pytest.raises(handoff.HandoffError):
        finish(job, str(ws), {}, env=CLEAN_ENV)
    assert not _has_object(repo, _blob_sha(repo, b"def add(a, b):\n    return a + b\n"))


def test_split_index_config_writes_nothing_to_git_dir(setup):
    repo, ws, job, _ = setup
    git(repo, "config", "core.splitIndex", "true")
    before = sorted(p.name for p in (repo / ".git").iterdir())
    (ws / "calc.py").write_text(f"k = '{SK}'\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "blocked_secret"
    assert sorted(p.name for p in (repo / ".git").iterdir()) == before


def test_preexisting_secret_in_base_file_does_not_block(setup):
    repo, ws, job, _ = setup
    (repo / "legacy.py").write_text(f"OLD = '{SK}'\n")
    git(repo, "add", "legacy.py")
    git(repo, "commit", "-q", "-m", "legacy")
    job["base_commit"] = git(repo, "rev-parse", "HEAD").strip()
    (ws / "legacy.py").write_text(f"OLD = '{SK}'\nNEW = 1\n")
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "handed_off"


def test_symlink_stored_as_target_not_followed(setup, tmp_path):
    repo, ws, job, _ = setup
    outside = tmp_path / "outside.txt"
    outside.write_text(f"secret {SK}\n")
    (ws / "link").symlink_to(outside)
    res = finish(job, str(ws), {}, env=CLEAN_ENV)
    assert res["status"] == "handed_off"
    assert git(repo, "show", f"{res['branch']}:link") == str(outside)

"""scripts/ci_seal.sh + ci_unseal.sh: CI artifacts of this public repo are encrypted before upload."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")


def run(script, d, key):
    env = {k: v for k, v in os.environ.items() if k != "BENCH_DATA_KEY"}
    if key is not None:
        env["BENCH_DATA_KEY"] = key
    return subprocess.run(["bash", str(ROOT / "scripts" / script), str(d)], env=env, capture_output=True, text=True)


def make(d):
    (d / "m__web-dev" / "judge" / "t1").mkdir(parents=True)
    (d / "m__web-dev" / "judge" / "t1" / "result.json").write_text('{"task": "secret task text"}')
    (d / "m__web-dev" / "trajectories.jsonl").write_text("{}\n")


def test_round_trip(tmp_path):
    d = tmp_path / "art"; make(d)
    r = run("ci_seal.sh", d, "k1")
    assert r.returncode == 0, r.stderr
    assert [p.name for p in d.iterdir()] == ["sealed.tar.gz.enc"]
    assert b"secret task text" not in (d / "sealed.tar.gz.enc").read_bytes()
    # an artifact download puts the file under a folder of its own
    dl = tmp_path / "dl" / "rl-web-merged-m-dev"; dl.mkdir(parents=True)
    shutil.copy(d / "sealed.tar.gz.enc", dl)
    r = run("ci_unseal.sh", tmp_path / "dl", "k1")
    assert r.returncode == 0, r.stderr
    assert (dl / "m__web-dev" / "judge" / "t1" / "result.json").read_text() == '{"task": "secret task text"}'
    assert not (dl / "sealed.tar.gz.enc").exists()


def test_no_key_deletes_instead_of_uploading(tmp_path):
    d = tmp_path / "art"; make(d)
    r = run("ci_seal.sh", d, "")
    assert r.returncode == 1 and not d.exists()


def test_missing_folder_is_fine(tmp_path):
    assert run("ci_seal.sh", tmp_path / "none", "k1").returncode == 0


def test_wrong_key_fails(tmp_path):
    d = tmp_path / "art"; make(d)
    assert run("ci_seal.sh", d, "k1").returncode == 0
    assert run("ci_unseal.sh", tmp_path, "k2").returncode != 0


def test_unsealed_artifacts_left_alone(tmp_path):
    make(tmp_path)
    assert run("ci_unseal.sh", tmp_path, None).returncode == 0
    assert (tmp_path / "m__web-dev" / "trajectories.jsonl").exists()

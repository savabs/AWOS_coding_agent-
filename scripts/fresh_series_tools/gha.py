"""GitHub-API helpers for the fresh series (no git subprocesses: snapshots come from gh api)."""
import io
import json
import subprocess
import tarfile
from pathlib import Path

DATA = Path("/Users/becmachlean/.awos-harness/fresh_series/_tools")
CACHE = DATA / "tarballs"
PRINFO = DATA / "prinfo"


def prinfo(name):
    return json.loads((PRINFO / f"{name}.json").read_text())


def tarball(repo, sha):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{repo.replace('/', '__')}_{sha[:12]}.tar.gz"
    if not p.exists():
        data = subprocess.run(["gh", "api", f"repos/{repo}/tarball/{sha}"], capture_output=True, check=True).stdout
        p.write_bytes(data)
    return p


def extract(repo, sha, dest: Path):
    """Extract the snapshot at sha into dest, stripping the top-level dir."""
    with tarfile.open(tarball(repo, sha)) as tf:
        members = []
        for m in tf.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            m.name = parts[1]
            members.append(m)
        tf.extractall(dest, members=members, filter="data")


def show(repo, sha, path):
    """File text at sha (served from the cached tarball)."""
    with tarfile.open(tarball(repo, sha)) as tf:
        for m in tf.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) == 2 and parts[1] == path:
                return tf.extractfile(m).read().decode()
    raise FileNotFoundError(f"{repo}@{sha[:10]}:{path}")

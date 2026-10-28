"""
bump_version — a hand-written example Tool for the admission harness (spec §10.1).

Sets [project].version in pyproject.toml and __version__ in <pkg>/__init__.py, and
adds a "## [X.Y.Z]" heading to CHANGELOG.md. Zero LLM calls.

The harness contract (admit.ToolImpl):
  WRITE_SET, READ_SET          files the tool may write / reads (fingerprint)
  goal_for(params)             the goal text a user would type for these params
  run(params, ctx)             do the work; ctx = {"root": Path}
  probe(params, ctx)           {"ok": bool, "evidence": {...}}; deterministic
  preconditions(params, ctx)   (ok, reason); tool-specific guard, read-only
  gen_params(seed)             the maker's held-out generator
  indep_params(seed)           the independent generator (spec §5.3)
  near_misses(seed)            [{goal, params, mutate(root)|None, why}]
  mutations(params)            [(name, mutate(root))]: plausible wrong end states (A6)
  make_demo_repo(root)         a tiny golden repo for demos and tests
"""

from __future__ import annotations

import random
import re
import subprocess
import tomllib
from pathlib import Path

FAMILY = "bump_version"
PYPROJECT = "pyproject.toml"
CHANGELOG = "CHANGELOG.md"
INIT = "src/demo_pkg/__init__.py"
WRITE_SET = [PYPROJECT, CHANGELOG, INIT]
READ_SET = list(WRITE_SET)
SEMVER = r"^\d+\.\d+\.\d+$"
START_VERSION = "0.9.2"

PARAMS_SCHEMA = {
    "type": "object",
    "required": ["new_version"],
    "properties": {
        "new_version": {"type": "string", "pattern": SEMVER},
        "changelog_note": {"type": "string", "maxLength": 200, "x-taint": "data"},
    },
    "additionalProperties": False,
}
INTENT = {
    "summary": "Bump the package version and add a CHANGELOG heading",
    "triggers": ["bump version", "bump the version", "release <semver>", "set version"],
    "anti_triggers": ["dependency", "downgrade", "yank", "requirement"],
}


def _vt(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def current_version(root: Path) -> str:
    data = tomllib.loads((Path(root) / PYPROJECT).read_text(encoding="utf-8"))
    return data["project"]["version"]


def goal_for(params: dict) -> str:
    return f"bump version to {params['new_version']}"


def preconditions(params: dict, ctx: dict) -> tuple[bool, str]:
    root = Path(ctx["root"])
    for f in WRITE_SET:
        if not (root / f).is_file():
            return False, f"missing {f}"
    try:
        cur = current_version(root)
    except (KeyError, tomllib.TOMLDecodeError) as exc:
        return False, f"no [project].version: {exc}"
    if _vt(params["new_version"]) <= _vt(cur):
        return False, f"{params['new_version']} is not above current {cur}"
    if f"## [{params['new_version']}]" in (root / CHANGELOG).read_text(encoding="utf-8"):
        return False, "CHANGELOG already has this version"
    return True, "ok"


def run(params: dict, ctx: dict) -> dict:
    root = Path(ctx["root"])
    v = params["new_version"]
    py = root / PYPROJECT
    text = py.read_text(encoding="utf-8")
    text, n = re.subn(r'(?m)^(version\s*=\s*")[^"]*(")', rf"\g<1>{v}\g<2>", text, count=1)
    if n != 1:
        raise RuntimeError("version line not found")
    py.write_text(text, encoding="utf-8")
    init = root / INIT
    init.write_text(re.sub(r'__version__\s*=\s*"[^"]*"', f'__version__ = "{v}"',
                           init.read_text(encoding="utf-8")), encoding="utf-8")
    cl = root / CHANGELOG
    lines = cl.read_text(encoding="utf-8").splitlines(keepends=True)
    note = params.get("changelog_note") or "Version bump."
    entry = [f"## [{v}]\n", "\n", f"- {note}\n", "\n"]
    at = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), len(lines))
    cl.write_text("".join(lines[:at] + entry + lines[at:]), encoding="utf-8")
    return {"written": list(WRITE_SET)}


def probe(params: dict, ctx: dict) -> dict:
    root = Path(ctx["root"])
    v = params["new_version"]
    ev = {}
    try:
        ev["toml_value"] = current_version(root)
    except Exception as exc:  # noqa: BLE001
        ev["toml_value"] = f"error: {exc}"
    init = (root / INIT).read_text(encoding="utf-8") if (root / INIT).is_file() else ""
    m = re.search(r'__version__\s*=\s*"([^"]*)"', init)
    ev["init_value"] = m.group(1) if m else None
    cl = (root / CHANGELOG).read_text(encoding="utf-8") if (root / CHANGELOG).is_file() else ""
    ev["changelog_heading"] = f"## [{v}]" in cl
    ok = ev["toml_value"] == v and ev["init_value"] == v and ev["changelog_heading"]
    return {"ok": bool(ok), "evidence": ev}


def _rand_version(rng: random.Random) -> str:
    return f"{rng.randint(0, 3)}.{rng.randint(0, 20)}.{rng.randint(0, 30)}"


def gen_params(seed: int) -> dict:
    """The maker's generator: random semver above START_VERSION."""
    rng = random.Random(f"gen-{seed}")
    while True:
        v = _rand_version(rng)
        if _vt(v) > _vt(START_VERSION):
            out = {"new_version": v}
            if rng.random() < 0.5:
                out["changelog_note"] = rng.choice(["Fix parser.", "Faster start-up.",
                                                    "Docs: $(rm -rf /) is just text."])
            return out


def indep_params(seed: int) -> dict:
    """Independent generator: patch / minor / major increments of START_VERSION."""
    rng = random.Random(f"indep-{seed}")
    ma, mi, pa = _vt(START_VERSION)
    kind = ["patch", "minor", "major"][seed % 3]
    k = rng.randint(1, 9)
    v = {"patch": f"{ma}.{mi}.{pa + k}", "minor": f"{ma}.{mi + k}.0",
         "major": f"{ma + k}.0.0"}[kind]
    return {"new_version": v}


def _write(rel: str, text: str):
    def mutate(root: Path) -> None:
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return mutate


def _delete(rel: str):
    return lambda root: (Path(root) / rel).unlink()


def near_misses(seed: int) -> list[dict]:
    """Inputs the guard must refuse before any write."""
    return [
        {"goal": "bump the requests dependency version to 2.31.0",
         "params": {"new_version": "2.31.0"}, "mutate": None, "why": "a dependency, not the package"},
        {"goal": "bump version to 2.0", "params": {"new_version": "2.0"},
         "mutate": None, "why": "not semver"},
        {"goal": "bump version to 0.1.0", "params": {"new_version": "0.1.0"},
         "mutate": None, "why": "a downgrade"},
        {"goal": "yank release 0.9.2", "params": {"new_version": "0.9.2"},
         "mutate": None, "why": "yank, not bump"},
        {"goal": "bump version to 1.4.0", "params": {"new_version": "1.4.0"},
         "mutate": _delete(PYPROJECT), "why": "repo without pyproject"},
        {"goal": "bump version to 1.5.0", "params": {"new_version": "1.5.0"},
         "mutate": _write(PYPROJECT, '[tool.poetry]\nversion = "0.9.2"\n'),
         "why": "pyproject layout drifted (fingerprint)"},
        {"goal": "bump version to 1.6.0",
         "params": {"new_version": "1.6.0", "shell": "git push origin main"},
         "mutate": None, "why": "an extra, unschema'd argument"},
    ]


def mutations(params: dict) -> list[tuple[str, object]]:
    """Plausible wrong end states the probe must reject (A6)."""
    v = params["new_version"]
    ma, mi, pa = _vt(v)
    wrong = f"{ma}.{mi}.{pa + 1}"
    return [
        ("wrong toml version", lambda root: (Path(root) / PYPROJECT).write_text(
            (Path(root) / PYPROJECT).read_text().replace(f'"{v}"', f'"{wrong}"'))),
        ("changelog heading missing", lambda root: (Path(root) / CHANGELOG).write_text(
            (Path(root) / CHANGELOG).read_text().replace(f"## [{v}]", "## [Unreleased]"))),
        ("__init__ not updated", lambda root: (Path(root) / INIT).write_text(
            f'__version__ = "{START_VERSION}"\n')),
    ]


def make_demo_repo(root: Path) -> Path:
    """A tiny git repo at START_VERSION with one commit."""
    root = Path(root)
    (root / "src/demo_pkg").mkdir(parents=True, exist_ok=True)
    (root / PYPROJECT).write_text(
        f'[project]\nname = "demo-pkg"\nversion = "{START_VERSION}"\n'
        'requires-python = ">=3.11"\n', encoding="utf-8")
    (root / INIT).write_text(f'__version__ = "{START_VERSION}"\n', encoding="utf-8")
    (root / CHANGELOG).write_text(
        f"# Changelog\n\n## [{START_VERSION}]\n\n- Initial.\n", encoding="utf-8")
    (root / "requirements.txt").write_text("tomli>=2\n", encoding="utf-8")
    (root / "README.md").write_text("demo\n", encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "awos", "GIT_AUTHOR_EMAIL": "awos@example.invalid",
           "GIT_COMMITTER_NAME": "awos", "GIT_COMMITTER_EMAIL": "awos@example.invalid",
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    for argv in (["git", "init", "-q", "-b", "work"], ["git", "add", "-A"],
                 ["git", "commit", "-q", "-m", "init"]):
        subprocess.run(argv, cwd=root, check=True, env=env, capture_output=True)
    return root

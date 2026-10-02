#!/usr/bin/env python3
"""
job_series.py — does AWOS get better at one project's work over a series of jobs?

Contract: docs/specs/compounding_proof_spec.md (piece C). A series lives in
tests/job_series/<series>/:

    base/                   the project at the start (package + tests/)
    jobs/NN_<slug>/
        task.json           {"id","goal","max_turns","max_cost_usd","timeout_min"}
        hidden_tests/       test_*.py, copied into the project's tests/ to judge
        reference/          files that, copied over the project, solve the job

Job N starts from base + the references of jobs 1..N-1 (cumulative).

    python3 scripts/job_series.py validate [--series ordertool]
    python3 scripts/job_series.py run [--series ordertool] [--arms off,on] [--jobs 1-12]
    python3 scripts/job_series.py run --dry-run      # everything but the model calls
    python3 scripts/job_series.py run --arms off,aider --jobs 3,8 --repeat 5   # noise
    python3 scripts/job_series.py revalidate 20260929T131340 [--data-root DIR]

Evaluation instrument (docs/research/evaluation_first_principles_2026-10.md,
rules 1, 2, 8): `run` validates the selected jobs first (preflight; an INVALID
job aborts before any model spend; --skip-preflight is recorded), records each
arm's inputs per job (project tree + task hashes, masked command, pinned env,
Aider's chat files) and fails the run if arms of one job got different inputs,
stamps provenance (git sha/dirty, config hash, timestamps), then calls
scripts/eval_health.check_run (-> <run_dir>/health.json, results["valid"]) and
scripts/eval_report.write_report. `revalidate` calls the same two hooks.

`run` gives every arm its own empty state dir (the child's cwd, so every .awos
store starts empty and is private to that arm) and one persistent git project
(so the project id is stable across jobs). Arms interleave job by job
(off j1, on j1, off j2, ...), so a key dying mid-run leaves a paired prefix.
The only difference between arms is AWOS_NOTEBOOK=0|1; the model is pinned to
DeepSeek V4 Flash in both (see _pin_model).

Infrastructure failures (INVALID_MARKERS in the job's log, or 0 agent turns)
are not the agent's result: the arm's notebook is restored to its pre-job
snapshot, the backend is awaited, and the job retried (AWOS_SERIES_RETRIES,
default 2). A job still invalid is recorded with "invalid": true and dropped
from the comparison in every arm (paired exclusion).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERIES_ROOT = REPO / "tests" / "job_series"
PY = sys.executable

PINNED_MODEL = "deepseek/deepseek-v4-flash"
FRONTIER_MODEL = "anthropic/claude-sonnet-5.5"   # the aider-frontier arm only
# Escalation ladder ids hidden from the router inside the child, so every
# decision (heuristic, retry escalation, LinUCB, performance veto) lands on
# Flash. MODELS_UNAVAILABLE is the ladder's own "never pick this" filter.
BLOCKED_LADDER_IDS = ("deepseek-v4-pro",)

# Both arms get exactly these. AWOS_NOTEBOOK is the only per-arm variable.
PIN_ENV = {
    "AWOS_AGENT_MODEL": PINNED_MODEL,       # fallback id for build_client_from_env / check_backend
    "AWOS_NOTEBOOK_MODEL": PINNED_MODEL,    # the notebook's update call
    "AWOS_PLANNER_MODEL": PINNED_MODEL,     # every planner call (primary and fallback)
    "AWOS_GOAL_CHECK": "0",                 # hidden tests judge; saves ~$0.13/job
}

# One entry per arm: the child command and the env it adds. In "cmd", the
# placeholders {runner}, {job_dir} and {project} are filled per job; the child
# runs with cwd = the arm's state dir. --arms accepts any key here. Under
# --dry-run every arm runs the no-op `_dry_child` instead (with the arm's env).
# A child may write report.json in its cwd; see USAGE_KEYS for the usage contract.
ARM_CHILDREN: dict[str, dict] = {
    "off": {"cmd": [PY, "{runner}", "_child", "{job_dir}", "{project}"],
            "env": {"AWOS_NOTEBOOK": "0", **PIN_ENV}},
    "on": {"cmd": [PY, "{runner}", "_child", "{job_dir}", "{project}"],
           "env": {"AWOS_NOTEBOOK": "1", **PIN_ENV}},
    # Head-to-head: a market harness (Aider) with no project memory. Stock Aider
    # stops after 3 follow-up rounds per message; 10 is generous to it, so a
    # win for AWOS is not a win against a handicapped competitor.
    "aider": {"cmd": [PY, str(REPO / "scripts" / "harness_aider.py"), "{job_dir}", "{project}",
                      "--model", PINNED_MODEL, "--max-reflections", "10"],
              "env": {}},
    "aider-frontier": {"cmd": [PY, str(REPO / "scripts" / "harness_aider.py"), "{job_dir}",
                               "{project}", "--model", FRONTIER_MODEL,
                               "--max-reflections", "10"],
                       "env": {}},
}

# report.json may carry {"usage": {...}} with these keys; it then replaces the
# ledger/spans metrics for that job (for harnesses that don't write .awos/).
USAGE_KEYS = ("cost_usd", "turns", "input_tokens", "output_tokens", "models")

# Text in a job's log section that means the infrastructure broke, not the agent.
INVALID_MARKERS = (
    "APIConnectionError",       # network drop (openai/litellm client)
    "Connection error",         # the same error's message text
    "AuthenticationError",      # key rejected
    "API key expired",
    "Error code: 401",
)


# ── Series discovery and staging ──────────────────────────────────────────────


def series_dir(series: str, root: Path | None = None) -> Path:
    return (root or SERIES_ROOT) / series


def discover_jobs(sdir: Path) -> list[tuple[int, Path]]:
    """[(number, job_dir)] ordered by the NN_ prefix."""
    jobs = []
    for task in (sdir / "jobs").glob("*/task.json"):
        m = re.match(r"(\d+)", task.parent.name)
        if m:
            jobs.append((int(m.group(1)), task.parent))
    return sorted(jobs)


def load(job_dir: Path) -> dict:
    return json.loads((job_dir / "task.json").read_text(encoding="utf-8"))


def parse_jobs(spec: str | None, available: list[int]) -> list[int]:
    """'1-6,9' -> [1..6, 9], restricted to jobs that exist."""
    if not spec:
        return list(available)
    wanted: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            wanted.update(range(int(lo), int(hi) + 1))
        else:
            wanted.add(int(part))
    return [n for n in available if n in wanted]


def overlay(src: Path, dest: Path) -> None:
    for f in src.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts:
            target = dest / f.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)


def materialize(sdir: Path, jobs: list[tuple[int, Path]], number: int, dest: Path,
                with_own_reference: bool = False) -> Path:
    """Write job `number`'s start state (base + references 1..N-1) into dest.

    dest may already hold a checkout: everything except .git is removed first.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for child in dest.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    overlay(sdir / "base", dest)
    for n, job_dir in jobs:
        if n < number or (with_own_reference and n == number):
            overlay(job_dir / "reference", dest)
    return dest


def add_hidden_tests(job_dir: Path, project: Path) -> list[str]:
    """Copy hidden_tests/ into project/tests/; return the copied test paths."""
    tests = project / "tests"
    tests.mkdir(exist_ok=True)
    copied = []
    src_root = job_dir / "hidden_tests"
    for f in sorted(src_root.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            rel = f.relative_to(src_root)
            target = tests / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            if f.name.startswith("test_") and f.suffix == ".py":
                copied.append(str(Path("tests") / rel))
    return copied


_FAIL_LINE = re.compile(r"^(FAILED|ERROR) (\S+)")


def pytest(project: Path, *paths: str, timeout: int = 600) -> dict:
    """Run pytest -rf; return counts and the failing test ids."""
    try:
        proc = subprocess.run(
            [PY, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", *paths],
            cwd=project, capture_output=True, text=True, timeout=timeout,
        )
        out = proc.stdout + proc.stderr
        code = proc.returncode
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "errors": 0, "ok": False,
                "failing": [], "summary": "pytest timed out"}
    out = re.sub(r"\x1b\[[0-9;]*m", "", out)
    counts = {k: 0 for k in ("passed", "failed", "errors")}
    for num, word in re.findall(r"(\d+) (passed|failed|errors?)", out):
        counts["errors" if word.startswith("error") else word] += int(num)
    failing = []
    for ln in out.splitlines():
        m = _FAIL_LINE.match(ln.strip())
        if m and m.group(2) not in failing:
            failing.append(m.group(2))
    summary = next((ln for ln in reversed(out.strip().splitlines()) if ln.strip()), "")
    return {**counts, "ok": code == 0, "failing": failing, "summary": summary[:160]}


def judge(job_dir: Path, project: Path) -> tuple[dict, dict]:
    """(hidden, visible). Visible runs first, before the hidden tests exist."""
    visible = (pytest(project, "tests") if (project / "tests").is_dir()
               else {"ok": True, "passed": 0, "failed": 0, "errors": 0, "failing": [], "summary": "-"})
    hidden_paths = add_hidden_tests(job_dir, project)
    hidden = pytest(project, *hidden_paths) if hidden_paths else {
        "ok": False, "passed": 0, "failed": 0, "errors": 0, "failing": [], "summary": "no hidden tests"}
    return hidden, visible


# ── validate ──────────────────────────────────────────────────────────────────

TASK_KEYS = ("id", "goal", "max_turns", "max_cost_usd", "timeout_min")


def validate(series: str, root: Path | None = None) -> int:
    sdir = series_dir(series, root)
    if not (sdir / "base").is_dir():
        print(f"[job_series] no series at {sdir} (missing base/)")
        return 1
    jobs = discover_jobs(sdir)
    if not jobs:
        print(f"[job_series] {sdir}/jobs has no jobs")
        return 1
    bad = invalid = 0
    numbers = [n for n, _ in jobs]
    if numbers != list(range(1, len(jobs) + 1)):
        print(f"  jobs are not numbered 1..{len(jobs)}: {numbers}")
        bad += 1
    for n, job_dir in jobs:
        c = check_job(sdir, jobs, n, job_dir)
        _print_check(c)
        bad += not c["ok"]
        invalid += not c["ok"]
    print(f"[job_series] {len(jobs) - invalid}/{len(jobs)} jobs valid")
    return 1 if bad else 0


def check_job(sdir: Path, jobs: list[tuple[int, Path]], n: int, job_dir: Path) -> dict:
    """One job's harness checks (protocol rule 1): hidden tests fail on the start
    state (an empty patch scores nothing) and pass with the reference, and neither
    state breaks the visible tests. {"job","dir","ok","problems","start","reference"}."""
    problems: list[str] = []
    missing = [p for p in ("hidden_tests", "reference") if not (job_dir / p).is_dir()]
    if missing:
        problems.append(f"missing {missing}")
    try:
        spec = load(job_dir)
        absent = [k for k in TASK_KEYS if k not in spec]
        if absent:
            problems.append(f"task.json lacks {absent}")
    except (OSError, ValueError) as exc:
        problems.append(f"task.json unreadable: {exc}")
    out = {"job": n, "dir": job_dir.name, "start": None, "reference": None}
    if missing:
        return {**out, "ok": False, "problems": problems}
    with tempfile.TemporaryDirectory() as tmp:
        start = materialize(sdir, jobs, n, Path(tmp) / "start")
        h_start, v_start = judge(job_dir, start)
        solved = materialize(sdir, jobs, n, Path(tmp) / "solved", with_own_reference=True)
        h_solved, v_solved = judge(job_dir, solved)
    if h_start["ok"]:
        problems.append("hidden tests PASS on the start state (measures nothing)")
    if not v_start["ok"]:
        problems.append(f"visible tests fail on the start state ({v_start['summary']})")
    if not h_solved["ok"]:
        problems.append(f"hidden tests fail with the reference ({h_solved['summary']}; "
                        f"failing {h_solved['failing'][:5]})")
    if not v_solved["ok"]:
        problems.append(f"reference breaks the visible tests ({v_solved['summary']})")
    return {**out, "ok": not problems, "problems": problems,
            "start": h_start["summary"], "reference": h_solved["summary"]}


def _print_check(c: dict) -> None:
    line = f"  {c['job']:>2} {c['dir']:<30} {'ok' if c['ok'] else 'INVALID':<8}"
    if c["start"] is not None:
        line += f" start: {c['start']} | reference: {c['reference']}"
    print(line, flush=True)
    for p in c["problems"]:
        print(f"      - {p}")


def preflight(sdir: Path, jobs: list[tuple[int, Path]], numbers: list[int]) -> dict:
    """`validate` for the selected jobs, before any arm starts (no model calls).

    {"status": "ok"|"failed", "jobs": {"<n>": [problems]}, "invalid_jobs": [n], "seconds"}.
    """
    started = time.monotonic()
    by_n = dict(jobs)
    print(f"[job_series] preflight: validating jobs {numbers} "
          f"(hidden tests fail on the start state, pass with the reference)", flush=True)
    checks = [check_job(sdir, jobs, n, by_n[n]) for n in numbers]
    for c in checks:
        _print_check(c)
    bad = [c["job"] for c in checks if not c["ok"]]
    return {"status": "failed" if bad else "ok",
            "jobs": {str(c["job"]): c["problems"] for c in checks},
            "invalid_jobs": bad, "seconds": round(time.monotonic() - started, 1)}


# ── provenance, input manifests, end-of-run hooks ─────────────────────────────

# Not part of what an arm "received": VCS data, AWOS runtime state, bytecode.
_TREE_SKIP = {".git", ".awos", "__pycache__", ".pytest_cache", ".aider.tags.cache.v4"}
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)
_SECRET_VALUE = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,}|sk_[A-Za-z0-9_\-]{8,}|Bearer\s+\S+)")
_UTC = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> str:
    return time.strftime(_UTC, time.gmtime())


def sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(root: Path) -> tuple[str, int]:
    """(sha256 over sorted 'relpath\\0filehash\\n' lines, file count); skips _TREE_SKIP."""
    import hashlib
    lines = []
    for f in root.rglob("*"):
        rel = f.relative_to(root)
        if any(part in _TREE_SKIP for part in rel.parts) or not f.is_file():
            continue
        lines.append(f"{rel.as_posix()}\0{sha256_file(f)}\n")
    lines.sort()
    return hashlib.sha256("".join(lines).encode()).hexdigest(), len(lines)


def mask_secret(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if _SECRET_NAME.search(name):
        return "***" if value else ""
    return _SECRET_VALUE.sub("***", value)


def mask_cmd(cmd: list[str]) -> list[str]:
    """The child command with secret-looking values masked (--api-key X, K=V, sk-...)."""
    out: list[str] = []
    hide_next = False
    for part in cmd:
        if hide_next:
            out.append("***")
            hide_next = False
            continue
        if part.startswith("-") and _SECRET_NAME.search(part):
            if "=" in part:
                out.append(part.split("=", 1)[0] + "=***")
            else:
                out.append(part)
                hide_next = True
            continue
        if "=" in part and _SECRET_NAME.search(part.split("=", 1)[0]):
            out.append(part.split("=", 1)[0] + "=***")
            continue
        out.append(_SECRET_VALUE.sub("***", part))
    return out


def input_manifest(arm: str, job_dir: Path, project: Path, cmd: list[str], env: dict) -> dict:
    """What an arm receives for a job, recorded before its child starts (rule 1)."""
    proj_sha, n_files = tree_hash(project)
    keys = sorted(set(PIN_ENV) | set(ARM_CHILDREN[arm]["env"]) | {"AWOS_SERIES_ARM"})
    return {
        "project_sha256": proj_sha, "project_files": n_files,
        "task_sha256": sha256_file(job_dir / "task.json"),
        "cmd": mask_cmd(cmd),
        "env": {k: mask_secret(k, env.get(k)) for k in keys},
        "recorded_at": _now(),
    }


_ADD_FILES_BANNER = re.compile(
    r"\[harness_aider\] aider .*? add_files=(\S*?)\((\d+)(?: edit, (\d+) read-only)?")


def aider_files(report: dict, log_text: str) -> dict | None:
    """The files the Aider harness put in the chat, from report.json (else its banner)."""
    if isinstance(report, dict) and report.get("harness") == "aider":
        return {"source": "report",
                "selection": report.get("files_selection") or report.get("add_files"),
                "added": list(report.get("files_added") or []),
                "read_only": list(report.get("files_read") or []),
                "in_chat_final": report.get("files_in_chat")}
    m = _ADD_FILES_BANNER.search(_TS_PREFIX.sub("", log_text or ""))
    if m:
        return {"source": "log", "selection": m.group(1),
                "added_count": int(m.group(2)), "read_only_count": int(m.group(3) or 0)}
    return None


def manifest_violations(results: list[dict]) -> list[dict]:
    """Fatal when arms of one (repeat, job) received different project trees or
    task files, or when the Aider harness started with no file in the chat."""
    out: list[dict] = []
    groups: dict[tuple[int, int], list[dict]] = {}
    for r in results:
        if isinstance(r.get("inputs"), dict):
            groups.setdefault((_rep(r), r["job"]), []).append(r)
    for (rep, job), rows in sorted(groups.items()):
        for field, what in (("project_sha256", "project tree"), ("task_sha256", "task.json")):
            seen = {r["arm"]: r["inputs"].get(field) for r in rows}
            if len(set(seen.values())) > 1:
                out.append({"severity": "fatal", "check": "input_mismatch", "arm": None,
                            "job": job, "repeat": rep,
                            "detail": f"arms received different {what}: "
                                      + ", ".join(f"{a}={(h or '?')[:12]}"
                                                  for a, h in sorted(seen.items()))})
        for r in rows:
            af = r["inputs"].get("aider_files")
            if not af:
                continue
            n = (len(af.get("added", [])) + len(af.get("read_only", []))
                 if af.get("source") == "report"
                 else af.get("added_count", 0) + af.get("read_only_count", 0))
            if n == 0:
                out.append({"severity": "fatal", "check": "aider_no_files", "arm": r["arm"],
                            "job": job, "repeat": rep,
                            "detail": f"the harness put no file in Aider's chat "
                                      f"(selection={af.get('selection')})"})
    return out


def git_provenance(repo: Path = REPO) -> dict:
    """HEAD sha, branch, and whether code (anything outside .awos/) is modified."""
    def g(*args: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                  text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    status = [ln for ln in g("status", "--porcelain", "--untracked-files=no").splitlines()
              if ln.strip() and not ln[3:].startswith(".awos/")]
    return {"sha": g("rev-parse", "HEAD") or None,
            "branch": g("rev-parse", "--abbrev-ref", "HEAD") or None,
            "dirty": bool(status), "dirty_paths": [ln[3:] for ln in status][:20]}


def config_hash(series: str, sdir: Path, arms: list[str], numbers: list[int],
                repeat: int) -> dict:
    """A stable hash of everything that defines the run (rule 8: one change per run)."""
    import hashlib
    series_sha, _ = tree_hash(sdir) if sdir.is_dir() else (None, 0)
    config = {
        "series": series, "series_sha256": series_sha, "jobs": numbers, "repeat": repeat,
        "arms": {a: {"cmd": [c.replace(str(REPO), "<repo>").replace(PY, "<python>")
                             for c in ARM_CHILDREN[a]["cmd"]],
                     "env": ARM_CHILDREN[a]["env"]} for a in arms},
        "pin": {"model": PINNED_MODEL, "blocked_ladder_ids": list(BLOCKED_LADDER_IDS),
                "env": PIN_ENV},
    }
    blob = json.dumps(config, sort_keys=True).encode()
    return {"sha256": hashlib.sha256(blob).hexdigest(), "series_sha256": series_sha}


def _scripts_on_path() -> None:
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)


def _hook(module: str, attr: str):
    """module.attr from scripts/, or None (with a warning) while it doesn't exist yet."""
    import importlib
    _scripts_on_path()
    try:
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as exc:
        print(f"[job_series] WARNING: {module}.{attr} unavailable ({exc}); skipping", flush=True)
        return None


def finish(data: dict, results_path: Path, run_root: Path, out_dir: Path,
           own: list[dict]) -> dict:
    """Health check + report hooks (rule 2). Writes <out_dir>/health.json, sets
    data["valid"], rewrites results_path, then writes the report."""
    violations = list(own)
    check_run = _hook("eval_health", "check_run")
    health_ok: bool | None = None
    if check_run is None:
        violations.append({"severity": "warn", "check": "health_check_unavailable", "arm": None,
                           "job": None, "repeat": None,
                           "detail": "scripts/eval_health.py not found; only runner checks ran"})
    else:
        try:
            h = check_run(run_root, results_path) or {}
            health_ok = bool(h.get("ok"))
            violations.extend(h.get("violations") or [])
        except Exception as exc:  # noqa: BLE001 - a broken checker voids, not crashes, the run
            violations.append({"severity": "fatal", "check": "health_check_crashed", "arm": None,
                               "job": None, "repeat": None, "detail": repr(exc)[:300]})
    fatal = [v for v in violations if v.get("severity") == "fatal"]
    ok = (health_ok is not False) and not fatal
    out_dir.mkdir(parents=True, exist_ok=True)
    health_path = out_dir / "health.json"
    health_path.write_text(json.dumps({"ok": ok, "checked_at": _now(),
                                       "health_check": None if check_run is None else health_ok,
                                       "violations": violations}, indent=2), encoding="utf-8")
    data["valid"] = ok
    data["violations"] = violations
    data["health"] = {"path": str(health_path), "fatal": len(fatal),
                      "warn": len(violations) - len(fatal)}
    if not ok:
        bar = "!" * 78
        print(f"\n{bar}\n  INVALID RUN — {len(fatal)} fatal violation(s); do not draw conclusions "
              f"from it.", flush=True)
        for v in fatal[:20]:
            where = " ".join(f"{k}={v[k]}" for k in ("arm", "job", "repeat") if v.get(k) is not None)
            print(f"  - {v.get('check')} {where}: {v.get('detail')}")
        print(f"  details: {health_path}\n{bar}", flush=True)
    else:
        print(f"[job_series] health: ok ({len(violations)} warning(s)) — {health_path}")
    results_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    write_report = _hook("eval_report", "write_report")
    if write_report is not None:
        try:
            report_path = write_report(results_path, out_dir)
            data["report"] = str(report_path)
            results_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"[job_series] report: {report_path}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[job_series] WARNING: write_report failed: {exc!r}", flush=True)
    return data


# ── the child (runs with cwd = the arm's state dir) ───────────────────────────


def _pin_model() -> list[str]:
    """Block every ladder rung but Flash in whichever copies of the module load."""
    import importlib

    allowed: list[str] = []
    for name in ("scaffold.agent.escalation_engine", "escalation_engine"):
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        mod.MODELS_UNAVAILABLE.update(BLOCKED_LADDER_IDS)
        allowed = [s.model_id for s in mod.LADDER if s.model_id not in mod.MODELS_UNAVAILABLE]
    return allowed


def run_child(job_dir: Path, project: Path) -> None:
    """Hand the goal to a fresh Orchestrator, as scripts/long_tasks.py does."""
    # cwd is the arm's state dir, so the repo is put on the path explicitly.
    sys.path[:0] = [str(REPO), str(REPO / "scaffold"), str(REPO / "scaffold" / "agent")]
    from dotenv import load_dotenv

    load_dotenv(Path.cwd() / ".env")   # the arm's copy; never overrides the pins
    spec = load(job_dir)
    os.environ.update({
        "AWOS_EXECUTOR": "agent_loop",
        "AWOS_SAFE_TO_RUN_TESTS": "1",
        "AWOS_USE_WORKTREE": "false",
        "AWOS_ENABLE_CLARIFICATION": "false",
        "AWOS_ENABLE_PLAN_REVIEW": "false",
        "AWOS_MAX_RUN_COST": str(spec.get("max_cost_usd", 1.0)),
        "AWOS_AGENT_MAX_TURNS": str(spec.get("max_turns", 150)),
        "AWOS_SANDBOX": os.environ.get("AWOS_SANDBOX", "auto"),
        **PIN_ENV,
    })
    allowed = _pin_model()
    from scaffold.agent.orchestrator import Orchestrator

    allowed = _pin_model() or allowed  # again, in case the fallback import path loaded
    print(f"[job_series] child cwd={Path.cwd()} notebook={os.environ.get('AWOS_NOTEBOOK')} "
          f"goal_check={os.environ.get('AWOS_GOAL_CHECK')} ladder={allowed}", flush=True)
    report = Orchestrator().execute_feature(
        goal=spec["goal"],
        codebase_root=str(project),
        pre_planned_tasks=None,
        auto_approve_plan=True,
    )
    (Path.cwd() / "report.json").write_text(json.dumps({
        k: report.get(k) for k in ("success", "tasks_completed", "tasks_failed",
                                   "plan_source", "planner_errors", "errors")
    }), encoding="utf-8")


def run_dry_child(job_dir: Path, project: Path) -> None:
    """--dry-run stand-in for the orchestrator: touches nothing, calls nothing.

    Test hooks (env): AWOS_SERIES_DRY_FAIL="<arm>:<job>:<times>" makes the first
    <times> attempts of that arm's job scribble on the notebook and die with a
    connection error; AWOS_SERIES_DRY_USAGE=<json> is written as report.json's usage.
    """
    print(f"[job_series] dry-run child cwd={Path.cwd()} project={project} "
          f"notebook={os.environ.get('AWOS_NOTEBOOK')} goal_check={os.environ.get('AWOS_GOAL_CHECK')} "
          f"model={os.environ.get('AWOS_AGENT_MODEL')}", flush=True)
    fail = os.environ.get("AWOS_SERIES_DRY_FAIL", "")
    if fail:
        arm, job, times = fail.split(":")
        m = re.match(r"(\d+)", job_dir.name)
        if arm == os.environ.get("AWOS_SERIES_ARM") and m and int(m.group(1)) == int(job):
            counter = Path.cwd() / f".dry_fail_{job}"
            done = int(counter.read_text()) if counter.is_file() else 0
            if done < int(times):
                counter.write_text(str(done + 1))
                nb = Path.cwd() / ".awos" / "projects" / "dry" / "notebook.md"
                nb.parent.mkdir(parents=True, exist_ok=True)
                with open(nb, "a", encoding="utf-8") as fh:
                    fh.write("half-written by a broken job\n")
                print("openai.APIConnectionError: Connection error.", flush=True)
                return
    report: dict = {"success": None}
    if os.environ.get("AWOS_SERIES_DRY_USAGE"):
        report["usage"] = json.loads(os.environ["AWOS_SERIES_DRY_USAGE"])
    (Path.cwd() / "report.json").write_text(json.dumps(report), encoding="utf-8")


# ── run ───────────────────────────────────────────────────────────────────────


def find_env_file(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    candidates = [REPO / ".env"]
    try:
        common = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--git-common-dir"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        if common:
            candidates.append((REPO / common).resolve().parent / ".env")
    except (OSError, subprocess.SubprocessError):
        pass
    return next((c for c in candidates if c.is_file()), None)


def read_ledger(state: Path) -> list:
    try:
        data = json.loads((state / ".awos" / "budget.json").read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def read_spans(state: Path) -> list:
    """Per-task spans; attempt_count is the agent loop's turns for that task."""
    try:
        lines = (state / ".awos" / "spans.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    spans = []
    for line in lines:
        try:
            spans.append(json.loads(line))
        except ValueError:
            continue
    return spans


def ledger_metrics(entries: list) -> dict:
    return {
        "llm_calls": len(entries),
        "input_tokens": sum(int(e.get("input_tokens", 0) or 0) for e in entries),
        "output_tokens": sum(int(e.get("output_tokens", 0) or 0) for e in entries),
        "cost_usd": round(sum(float(e.get("cost", 0) or 0) for e in entries), 6),
        "notebook_cost_usd": round(sum(float(e.get("cost", 0) or 0) for e in entries
                                       if e.get("request_type") == "notebook"), 6),
        "models": sorted({str(e.get("model")) for e in entries if e.get("model")}),
    }


def notebook_chars(state: Path) -> int:
    return sum(len(p.read_text(encoding="utf-8", errors="replace"))
               for p in (state / ".awos" / "projects").glob("*/notebook.md"))


def backend_ok() -> bool:
    """One tiny model call on the pinned model; a dead key is not an agent failure."""
    proc = subprocess.run(
        [PY, str(REPO / "scripts" / "check_backend.py")],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONUNBUFFERED": "1", **PIN_ENV},
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-6:])
        print(f"[job_series] backend check failed:\n{tail}", flush=True)
        return False
    return True


def wait_for_backend(max_wait_s: float | None = None, check=None, sleep=time.sleep) -> bool:
    """Poll backend_ok with backoff (15s doubling, capped at 3 min) for up to max_wait_s.

    Default wait: AWOS_SERIES_BACKEND_WAIT_S or 15 minutes. False if still down.
    """
    check = check or backend_ok
    if max_wait_s is None:
        max_wait_s = float(os.environ.get("AWOS_SERIES_BACKEND_WAIT_S", 900))
    waited, delay = 0.0, 15.0
    while True:
        if check():
            return True
        if waited >= max_wait_s:
            print(f"[job_series] backend ({PINNED_MODEL}) still down after {waited:.0f}s; giving up",
                  flush=True)
            return False
        step = min(delay, max_wait_s - waited)
        print(f"[job_series] waiting {step:.0f}s for the backend ({PINNED_MODEL} via "
              f"scripts/check_backend.py; {waited:.0f}/{max_wait_s:.0f}s so far)", flush=True)
        sleep(step)
        waited += step
        delay = min(delay * 2, 180.0)


_HARNESS_AIDER = None


def _ha():
    """scripts/harness_aider.py as a module (it owns the no-edit rules)."""
    global _HARNESS_AIDER
    if _HARNESS_AIDER is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "harness_aider_for_job_series", Path(__file__).resolve().parent / "harness_aider.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _HARNESS_AIDER = mod
    return _HARNESS_AIDER


_TS_PREFIX = re.compile(r"^\+\d+:\d\d ", re.M)
_NO_EDIT_LINE = re.compile(r"\[harness_aider\] aider_no_edit reason=(\w+)")
_FINAL_LINE = re.compile(r"\[aider-final\] (\{.*\})")
_APPLIED_LINE = re.compile(r"^Applied edit to (\S+)", re.M)
# The harness banner: "add_files=none(0) " (old) / "add_files=auto:src+tests(12 edit, ...".
_PRELOADED = re.compile(r"\[harness_aider\] aider .*? add_files=\S*?\((\d+)")


def aider_no_edit(log_text: str) -> str | None:
    """For an Aider job's log section: why it made no edit, or None.

    None also when the section is not an Aider run. Uses the harness's own
    `aider_no_edit reason=` line when present; for logs from before that line
    existed, re-derives it from `[aider-final]`, "Applied edit to" lines and the
    model's "please add ... to the chat" requests.
    """
    if "[harness_aider]" not in log_text:
        return None
    text = _TS_PREFIX.sub("", log_text)
    m = _NO_EDIT_LINE.search(text)
    if m:
        return m.group(1)
    final: dict = {}
    for fm in _FINAL_LINE.finditer(text):
        try:
            final = json.loads(fm.group(1))
        except ValueError:
            pass
    pre = _PRELOADED.search(text)
    ha = _ha()
    return ha.no_edit_reason(final, _APPLIED_LINE.findall(text), ha.asked_to_add_files(text),
                             preloaded=bool(pre and int(pre.group(1)) > 0))


def detect_invalid(log_text: str, turns: int, dry_run: bool) -> str | None:
    """Why this job's result says nothing about the agent, or None if it is a fair result.

    Infrastructure markers in the job's log section (network drop, dead key), or a
    real run that recorded 0 agent turns (the planner/child crashed before acting),
    or an Aider run that never edited because it had no files in its chat / only
    asked for files to be added (the harness's setup, not the model's work).
    A timeout is only invalid when its log shows one of the markers.
    """
    for marker in INVALID_MARKERS:
        if marker in log_text:
            return f"log shows {marker!r}"
    no_edit = aider_no_edit(log_text)
    if no_edit in _ha().NO_EDIT_SETUP_REASONS:
        return f"aider_no_edit ({no_edit}: Aider never had the code to edit)"
    if no_edit is not None:
        # Aider had the code and answered without an edit (it counts 0 turns
        # then): the model's failure, not a crash. Excluding it once dropped a
        # job from every arm and quietly favoured Aider.
        return None
    if not dry_run and turns == 0:
        return "0 agent turns (child crashed before the agent acted)"
    return None


def snapshot_notebook(state: Path) -> Path | None:
    """Copy state/.awos/projects (the notebook) aside; None if it doesn't exist yet."""
    src = state / ".awos" / "projects"
    if not src.is_dir():
        return None
    dest = Path(tempfile.mkdtemp(prefix="job_series_nb_")) / "projects"
    shutil.copytree(src, dest)
    return dest


def restore_notebook(state: Path, snap: Path | None) -> None:
    """Put state/.awos/projects back exactly as snapshot_notebook saw it."""
    target = state / ".awos" / "projects"
    if target.exists():
        shutil.rmtree(target)
    if snap is not None:
        shutil.copytree(snap, target)


def drop_snapshot(snap: Path | None) -> None:
    if snap is not None:
        shutil.rmtree(snap.parent, ignore_errors=True)


def git(project: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(project), "-c", "user.email=bench@awos", "-c", "user.name=bench", *args],
        capture_output=True, text=True, check=check,
    )


def stream_child(cmd: list[str], cwd: Path, env: dict, log_path: Path, prefix: str,
                 timeout_s: float) -> bool:
    """Run cmd, teeing each line to log_path and stdout with prefix. True on timeout."""
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n===== {prefix} {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        log.flush()
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                errors="replace", start_new_session=True)

        started = time.monotonic()

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                # Elapsed time on every line shows where a job's minutes go.
                secs = int(time.monotonic() - started)
                line = f"+{secs // 60:02d}:{secs % 60:02d} {line}"
                log.write(line)
                log.flush()
                sys.stdout.write(f"{prefix} {line}")
                sys.stdout.flush()

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait()
        reader.join(timeout=10)
        if timed_out:
            msg = f"[job_series] hit the {timeout_s / 60:.0f}-minute limit\n"
            log.write(msg)
            sys.stdout.write(f"{prefix} {msg}")
    return timed_out


def key_usage(state: Path) -> float | None:
    """The OpenRouter key's lifetime spend, read with the arm's own key.

    Jobs run one at a time, so the change across a job is everything that job
    billed, whichever harness ran it and whether or not it wrote the ledger.
    """
    import urllib.request
    try:
        key = next((ln.split("=", 1)[1].strip().strip('"').strip("'")
                    for ln in (state / ".env").read_text(encoding="utf-8").splitlines()
                    if ln.startswith("OPENROUTER_API_KEY=")), "")
        if not key:
            return None
        req = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                     headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return float(json.loads(resp.read().decode())["data"]["usage"])
    except Exception:  # noqa: BLE001 - a missing figure must not break a run
        return None


def billed_since(state: Path, before: float | None, settle_s: float = 150.0,
                 min_wait_s: float = 30.0, stable_reads: int = 3,
                 every_s: float = 10.0) -> float | None:
    """Spend since `before`, once OpenRouter's usage figure has settled.

    The figure lags by tens of seconds; a charge counted late would land on the
    next job (often another arm's). So wait at least min_wait_s, then require
    `stable_reads` equal readings `every_s` apart.
    """
    if before is None:
        return None
    started = time.monotonic()
    readings: list[float] = []
    while True:
        now = key_usage(state)
        if now is not None:
            readings.append(now)
        waited = time.monotonic() - started
        tail = readings[-stable_reads:]
        if (waited >= min_wait_s and len(tail) == stable_reads
                and max(tail) - min(tail) < 1e-9):
            return round(tail[-1] - before, 6)
        if waited > settle_s:
            return round(readings[-1] - before, 6) if readings else None
        time.sleep(every_s)


def run_job(arm: str, number: int, job_dir: Path, sdir: Path, jobs: list, arm_dir: Path,
            dry_run: bool) -> dict:
    spec = load(job_dir)
    state, project = arm_dir / "state", arm_dir / "project"
    prefix = f"[{arm} j{number:02d}]"
    materialize(sdir, jobs, number, project)
    git(project, "add", "-A")
    git(project, "commit", "-q", "--allow-empty", "-m", f"job {number} start")
    report_path = state / "report.json"
    report_path.unlink(missing_ok=True)

    ledger_before = len(read_ledger(state))
    spans_before = len(read_spans(state))
    billed_before = None if dry_run else key_usage(state)
    child = ARM_CHILDREN[arm]
    env = {**os.environ, "PYTHONUNBUFFERED": "1", **child["env"], "AWOS_SERIES_ARM": arm}
    # Tags every line of the child's .awos/llm_calls.jsonl with what it served.
    rep = arm_dir.parent.name
    env.update({"AWOS_EVAL_ARM": arm, "AWOS_EVAL_JOB": str(number),
                "AWOS_EVAL_REPEAT": rep[1:] if rep[:1] == "r" and rep[1:].isdigit() else "1"})
    fill = {"{runner}": str(Path(__file__).resolve()), "{job_dir}": str(job_dir),
            "{project}": str(project)}
    cmd = ([PY, "{runner}", "_dry_child", "{job_dir}", "{project}"] if dry_run
           else list(child["cmd"]))
    for key, val in fill.items():
        cmd = [part.replace(key, val) for part in cmd]
    inputs = input_manifest(arm, job_dir, project, cmd, env)
    log_path = arm_dir / "run.log"
    log_start = log_path.stat().st_size if log_path.exists() else 0
    started = time.monotonic()
    timed_out = stream_child(
        cmd, cwd=state, env=env, log_path=log_path, prefix=prefix,
        timeout_s=float(spec.get("timeout_min", 30)) * 60,
    )
    minutes = round((time.monotonic() - started) / 60, 2)
    with open(log_path, "rb") as fh:
        fh.seek(log_start)
        log_text = fh.read().decode("utf-8", errors="replace")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    changed = git(project, "status", "--porcelain", check=False).stdout
    hidden, visible = judge(job_dir, project)
    reported = report.get("usage") if isinstance(report, dict) else None
    if isinstance(reported, dict) and all(k in reported for k in USAGE_KEYS):
        # The harness counted its own usage (it doesn't write the .awos ledger).
        usage = {
            "llm_calls": int(reported.get("llm_calls", 0) or 0),
            "input_tokens": int(reported["input_tokens"] or 0),
            "output_tokens": int(reported["output_tokens"] or 0),
            "cost_usd": round(float(reported["cost_usd"] or 0), 6),
            "notebook_cost_usd": round(float(reported.get("notebook_cost_usd", 0) or 0), 6),
            "models": sorted(str(m) for m in (reported["models"] or [])),
            "turns": int(reported["turns"] or 0),
            "usage_source": "report",
        }
    else:
        usage = ledger_metrics(read_ledger(state)[ledger_before:])
        # The ledger holds one entry per agent-loop task, not per model call.
        usage["turns"] = sum(int(s.get("attempt_count", 0) or 0)
                             for s in read_spans(state)[spans_before:])
        usage["usage_source"] = "ledger"
    usage["billed_usd"] = billed_since(state, billed_before)
    invalid_reason = detect_invalid(log_text, usage["turns"], dry_run)
    result = {
        "arm": arm, "job": number, "id": spec.get("id", job_dir.name), "dir": job_dir.name,
        "solved": bool(hidden["ok"] and visible["ok"]),
        "hidden_passed": hidden["passed"], "hidden_failed": hidden["failed"] + hidden["errors"],
        "failing_tests": hidden["failing"], "hidden_summary": hidden["summary"],
        "visible_tests_ok": visible["ok"], "visible_failing": visible["failing"],
        "orchestrator_success": report.get("success"),
        "plan_source": report.get("plan_source"),
        "files_changed": len([ln for ln in changed.splitlines() if ln.strip()]),
        **usage,
        "minutes": minutes, "timed_out": timed_out,
        "notebook_chars": notebook_chars(state),
        "invalid": invalid_reason is not None, "invalid_reason": invalid_reason, "attempts": 1,
        "inputs": inputs,
    }
    af = aider_files(report, log_text)
    if af:
        inputs["aider_files"] = af
    no_edit = aider_no_edit(log_text)
    if no_edit or "[harness_aider]" in log_text:
        result["aider_no_edit"] = no_edit
        if no_edit:
            print(f"{prefix} aider_no_edit: {no_edit} (files in chat: "
                  f"{len(report.get('files_in_chat') or [])})", flush=True)
    verdict = ("INVALID (" + invalid_reason + ")" if invalid_reason
               else "SOLVED" if result["solved"] else "not solved")
    print(f"{prefix} {verdict} — hidden "
          f"{hidden['passed']}/{hidden['passed'] + result['hidden_failed']} · own tests "
          f"{'ok' if visible['ok'] else 'BROKEN'} · {usage['turns']} turns · "
          f"${usage['cost_usd']:.4f} (billed "
          f"{'?' if usage['billed_usd'] is None else '$%.4f' % usage['billed_usd']}) · "
          f"{minutes} min · notebook {result['notebook_chars']} chars",
          flush=True)
    return result


def run_job_with_retries(arm: str, number: int, job_dir: Path, sdir: Path, jobs: list,
                         arm_dir: Path, dry_run: bool, retries: int) -> tuple[dict, str | None]:
    """run_job, but an infrastructure failure doesn't count as the agent's result.

    Before each attempt the arm's notebook (state/.awos/projects) is snapshotted;
    an invalid attempt restores it (so a broken job can't advance the notebook),
    waits for the backend, and retries up to `retries` times. Returns (result,
    stop_reason); stop_reason is set when the backend stayed down past the wait.
    Every attempt's metrics are measured from that attempt's own ledger/spans start.
    """
    state = arm_dir / "state"
    failed: list[dict] = []
    while True:
        snap = snapshot_notebook(state)
        try:
            result = run_job(arm, number, job_dir, sdir, jobs, arm_dir, dry_run)
            if result["invalid"]:
                restore_notebook(state, snap)
                result["notebook_chars"] = notebook_chars(state)
        finally:
            drop_snapshot(snap)
        result["attempts"] = len(failed) + 1
        if failed:
            result["failed_attempts"] = failed
        if not result["invalid"]:
            return result, None
        failed = failed + [{"reason": result["invalid_reason"], "cost_usd": result["cost_usd"],
                            "minutes": result["minutes"]}]
        if len(failed) > retries:
            print(f"[{arm} j{number:02d}] still invalid after {len(failed)} attempt(s); "
                  f"recording it as invalid and moving on", flush=True)
            return result, None
        print(f"[{arm} j{number:02d}] notebook restored; retry {len(failed)}/{retries} "
              f"once the backend answers", flush=True)
        if not dry_run and not wait_for_backend():
            return result, f"backend down after invalid {arm} job {number} ({result['invalid_reason']})"


def _rep(r: dict) -> int:
    """A row's repeat number (rows from before --repeat existed are repeat 1)."""
    return int(r.get("repeat", 1) or 1)


def dropped_jobs(results: list[dict], arms: list[str]) -> dict[tuple[int, int], str]:
    """{(repeat, job): why} for every job where any arm's row is invalid in that
    repeat (paired exclusion: the job leaves the comparison in every arm)."""
    out: dict[tuple[int, int], str] = {}
    for r in results:
        if r["arm"] in arms and r.get("invalid"):
            key = (_rep(r), r["job"])
            why = f"{r['arm']}: {r.get('invalid_reason')}"
            out[key] = f"{out[key]}; {why}" if key in out else why
    return dict(sorted(out.items()))


def _multi(results: list[dict]) -> bool:
    return any(_rep(r) > 1 for r in results)


def _drop_label(key: tuple[int, int], multi: bool) -> str:
    rep, job = key
    return f"r{rep}:{job}" if multi else str(job)


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _spread(xs: list[float]) -> dict:
    """mean, sample sd, min, max of per-repeat values (None values skipped)."""
    xs = [float(x) for x in xs if x is not None]
    if not xs:
        return {"n": 0, "mean": None, "sd": None, "min": None, "max": None}
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5 if len(xs) > 1 else 0.0
    return {"n": len(xs), "mean": round(m, 4), "sd": round(sd, 4),
            "min": round(min(xs), 4), "max": round(max(xs), 4)}


def _block(rows: list[dict]) -> dict:
    return {
        "jobs": len(rows),
        "solved": sum(r["solved"] for r in rows),
        "solve_rate": _mean([float(r["solved"]) for r in rows]),
        "mean_cost_usd": _mean([r["cost_usd"] for r in rows]),
        "mean_billed_usd": _mean([r["billed_usd"] for r in rows
                                  if r.get("billed_usd") is not None]),
        "mean_turns": _mean([float(r.get("turns", 0)) for r in rows]),
        "mean_minutes": _mean([r["minutes"] for r in rows]),
    }


def repeat_summary(results: list[dict], arms: list[str],
                   dropped: dict[tuple[int, int], str]) -> dict:
    """Noise across repeats: per job and arm, solves out of the valid runs; per
    arm, mean +- sd (and min/max) over repeats of solve rate, turns and cost."""
    reps = sorted({_rep(r) for r in results})
    per_job: dict = {}
    per_repeat: dict = {}
    overall: dict = {}
    for arm in arms:
        rows = [r for r in results if r["arm"] == arm]
        jobs: dict = {}
        for r in rows:
            j = jobs.setdefault(str(r["job"]), {"solved": 0, "runs": 0, "invalid": 0})
            if (_rep(r), r["job"]) in dropped:
                j["invalid"] += 1
                continue
            j["runs"] += 1
            j["solved"] += int(bool(r["solved"]))
        per_job[arm] = dict(sorted(jobs.items(), key=lambda kv: int(kv[0])))
        blocks = [{"repeat": rep, **_block([r for r in rows if _rep(r) == rep
                                           and (rep, r["job"]) not in dropped])}
                  for rep in reps]
        per_repeat[arm] = blocks
        overall[arm] = {
            "solve_rate": _spread([b["solve_rate"] for b in blocks]),
            "mean_turns": _spread([b["mean_turns"] for b in blocks]),
            "mean_billed_usd": _spread([b["mean_billed_usd"] for b in blocks]),
            "mean_cost_usd": _spread([b["mean_cost_usd"] for b in blocks]),
        }
    return {"repeats": reps, "per_job": per_job, "per_repeat": per_repeat, "overall": overall}


def summarize(results: list[dict], arms: list[str]) -> dict:
    # Paired exclusion: a job invalid in any arm leaves the comparison entirely
    # (per repeat, when there are repeats).
    dropped = dropped_jobs(results, arms)
    out: dict = {}
    for arm in arms:
        rows = [r for r in results if r["arm"] == arm and (_rep(r), r["job"]) not in dropped]
        out[arm] = {
            "all": _block(rows),
            "jobs_1_6": _block([r for r in rows if r["job"] <= 6]),
            "jobs_7_12": _block([r for r in rows if 7 <= r["job"] <= 12]),
        }
    multi = _multi(results)
    out["dropped_jobs"] = {_drop_label(k, multi): why for k, why in dropped.items()}
    if multi:
        out["repeats"] = repeat_summary(results, arms, dropped)
    return out


def _fmt(v, money=False) -> str:
    if v is None:
        return "-"
    return f"${v:.4f}" if money else (f"{v:.2f}" if isinstance(v, float) else str(v))


def _fmt_spread(s: dict, money: bool = False) -> str:
    if not s or s.get("mean") is None:
        return "-"
    f = (lambda v: f"${v:.4f}") if money else (lambda v: f"{v:.2f}")
    return f"{f(s['mean'])} ± {f(s['sd'])} [{f(s['min'])}..{f(s['max'])}]"


def print_report(results: list[dict], arms: list[str], summary: dict) -> None:
    by = {(r["arm"], _rep(r), r["job"]): r for r in results}
    dropped = dropped_jobs(results, arms)
    multi = _multi(results)
    keys = sorted({(_rep(r), r["job"]) for r in results} - set(dropped))
    width = 10 + 34 * len(arms)
    print("\n" + "=" * width)
    print("  job     " + "".join(f"{arm + ': verdict  turns  cost  min':<34}" for arm in arms))
    print("  " + "-" * (width - 2))
    for rep, n in keys:
        label = f"r{rep}:{n}" if multi else str(n)
        line = f"  {label:>5}   "
        for arm in arms:
            r = by.get((arm, rep, n))
            cell = ("-" if r is None else
                    f"{'SOLVED' if r['solved'] else 'no':<8} {r.get('turns', 0):>5}  "
                    f"${r['cost_usd']:.3f} {r['minutes']:>5}")
            line += f"{cell:<34}"
        print(line)
    print("  " + "-" * (width - 2))
    for arm in arms:
        for label, key in (("all", "all"), ("jobs 1-6", "jobs_1_6"), ("jobs 7-12", "jobs_7_12")):
            b = summary[arm][key]
            if not b["jobs"]:
                continue
            print(f"  {arm:<4} {label:<10} solved {b['solved']}/{b['jobs']} "
                  f"(rate {_fmt(b['solve_rate'])})  mean cost {_fmt(b['mean_cost_usd'], True)}  "
                  f"mean turns {_fmt(b['mean_turns'])}  "
                  f"mean billed {_fmt(b.get('mean_billed_usd'), True)}")
    rs = summary.get("repeats")
    if rs:
        print("  " + "-" * (width - 2))
        print(f"  across {len(rs['repeats'])} repeats (mean ± sd [min..max] of per-repeat values):")
        for arm in arms:
            o = rs["overall"][arm]
            print(f"  {arm:<4} solve rate {_fmt_spread(o['solve_rate'])}  "
                  f"turns {_fmt_spread(o['mean_turns'])}  "
                  f"billed {_fmt_spread(o['mean_billed_usd'], True)}")
            print(f"       per job solved/runs: " + "  ".join(
                f"j{j}:{v['solved']}/{v['runs']}" for j, v in rs["per_job"][arm].items()))
    for (rep, n), why in dropped.items():
        where = f"job {n} (repeat {rep})" if multi else f"job {n}"
        print(f"  dropped {where} from {'both' if len(arms) == 2 else 'all'} arms (invalid run — {why})")
    print("=" * width)


def _setup_arm_dirs(run_root: Path, arms: list[str], env_src: Path | None) -> dict[str, Path]:
    arm_dirs: dict[str, Path] = {}
    for arm in arms:
        arm_dir = run_root / arm
        (arm_dir / "state").mkdir(parents=True, exist_ok=True)
        (arm_dir / "project").mkdir(parents=True, exist_ok=True)
        if env_src:
            shutil.copy2(env_src, arm_dir / "state" / ".env")
        git(arm_dir / "project", "init", "-q")
        (arm_dir / "run.log").touch()
        arm_dirs[arm] = arm_dir
    return arm_dirs


def save_partial(out: Path, series: str, ts: str, arms: list[str], numbers: list[int],
                 repeat: int, provenance: dict, pre: dict, results: list[dict]) -> None:
    """The results so far, marked partial; the end of the run overwrites them."""
    try:
        out.write_text(json.dumps({
            "series": series, "timestamp": ts, "arms": arms, "jobs": numbers,
            "repeat": repeat, "partial": True, "provenance": provenance,
            "preflight": pre, "results": results,
        }, indent=2, default=str), encoding="utf-8")
    except OSError as exc:
        print(f"[job_series] could not save partial results: {exc}", flush=True)


def run(series: str, arms: list[str], job_spec: str | None, dry_run: bool,
        root: Path | None = None, out_root: Path | None = None,
        env_file: str | None = None, resume: str | None = None, repeat: int = 1,
        skip_preflight: bool = False) -> int:
    """Run the selected jobs `repeat` times. With repeat > 1 each repeat gets its
    own fresh arm dirs (run_root/r<k>/<arm>), so no state carries across repeats;
    every row carries its `repeat` number.

    Before any arm starts, the selected jobs are validated (preflight; skipped
    under --dry-run or --skip-preflight, and recorded as skipped). An INVALID job
    aborts the run before any model spend (exit 3). After the run, the health
    check and report hooks run (see finish)."""
    started_at = _now()
    sdir = series_dir(series, root)
    jobs = discover_jobs(sdir)
    numbers = parse_jobs(job_spec, [n for n, _ in jobs])
    if not numbers:
        print(f"[job_series] no jobs selected in {sdir}")
        return 1
    if repeat < 1:
        print("[job_series] --repeat must be >= 1")
        return 1
    if resume and repeat > 1:
        print("[job_series] --resume and --repeat > 1 can't be combined")
        return 1
    job_by_n = dict(jobs)
    out_root = out_root or (REPO / ".awos")
    ts = resume or time.strftime("%Y%m%dT%H%M%S")
    run_root = out_root / "job_series" / ts
    # Resume: reuse each arm's state (its notebook) and project, keep the earlier
    # results for jobs not selected now; selected jobs are rerun from scratch.
    kept: list[dict] = []
    if resume:
        if not run_root.is_dir():
            print(f"[job_series] nothing to resume at {run_root}")
            return 1
        try:
            prev = json.loads((out_root / f"job_series_{ts}.json").read_text(encoding="utf-8"))
            kept = [r for r in prev.get("results", []) if r["job"] not in numbers and r["arm"] in arms]
        except (OSError, ValueError):
            kept = []
        print(f"[job_series] resuming {ts}: keeping {len(kept)} earlier result(s)")

    retries = int(os.environ.get("AWOS_SERIES_RETRIES", 2))
    env_src = find_env_file(env_file)
    provenance = {"git": git_provenance(), "config": config_hash(series, sdir, arms, numbers, repeat),
                  "started_at": started_at}
    out_root.mkdir(parents=True, exist_ok=True)
    out = out_root / f"job_series_{ts}.json"

    if dry_run or skip_preflight:
        pre = {"status": "skipped", "reason": "dry-run" if dry_run else "--skip-preflight"}
        print(f"[job_series] preflight SKIPPED ({pre['reason']})", flush=True)
    else:
        pre = preflight(sdir, jobs, numbers)
    if pre["status"] == "failed":
        bar = "!" * 78
        print(f"\n{bar}\n  PREFLIGHT FAILED — job(s) {pre['invalid_jobs']} are INVALID (the harness "
              f"can't score them).\n  Aborting before any arm starts; no model calls were made.\n"
              f"  Fix the series, or check with: python scripts/job_series.py validate "
              f"--series {series}\n{bar}", flush=True)
        if resume:
            return 3   # keep the earlier results file as it was
        out.write_text(json.dumps({
            "series": series, "timestamp": ts, "arms": arms, "jobs": numbers, "dry_run": dry_run,
            "repeat": repeat, "provenance": {**provenance, "finished_at": _now()},
            "preflight": pre, "valid": False, "aborted": "preflight failed",
            "violations": [{"severity": "fatal", "check": "preflight", "arm": None, "job": n,
                            "repeat": None, "detail": "; ".join(pre["jobs"][str(n)])}
                           for n in pre["invalid_jobs"]],
            "results": [],
        }, indent=2), encoding="utf-8")
        print(f"  Saved {out}")
        return 3

    print(f"[job_series] series={series} jobs={numbers} arms={arms} dry_run={dry_run} "
          f"retries={retries} repeat={repeat}")
    print(f"[job_series] model pinned: {PINNED_MODEL} (ladder blocks {list(BLOCKED_LADDER_IDS)}; "
          f"env {sorted(PIN_ENV)})")
    print(f"[job_series] .env copied from: {env_src if env_src else 'none found'}")
    sys.stdout.flush()

    results: list[dict] = list(kept)
    stopped = None
    for rep in range(1, repeat + 1):
        rep_root = run_root if repeat == 1 else run_root / f"r{rep}"
        arm_dirs = _setup_arm_dirs(rep_root, arms, env_src)
        for arm, arm_dir in arm_dirs.items():
            print(f"[job_series] {arm} log{'' if repeat == 1 else f' (repeat {rep})'}:  "
                  f"tail -f {arm_dir / 'run.log'}")
        sys.stdout.flush()
        for i, n in enumerate(numbers):
            for arm in arms:
                # Before every job: a key dying mid-run otherwise reads as the agent failing.
                if not dry_run and not wait_for_backend():
                    stopped = f"backend check failed before {arm} job {n}"
                    break
                spec = load(job_by_n[n])
                rep_note = "" if repeat == 1 else f" repeat {rep}/{repeat}"
                print(f"\n########## [{arm}] job {n} ({i + 1}/{len(numbers)}){rep_note}: "
                      f"{spec.get('id')} ##########")
                print(f"GOAL: {spec['goal']}\n", flush=True)
                result, stopped = run_job_with_retries(arm, n, job_by_n[n], sdir, jobs,
                                                       arm_dirs[arm], dry_run, retries)
                result["repeat"] = rep
                results.append(result)
                # Saved after every job: a killed runner kept nothing, and
                # --resume then had no earlier results to keep.
                save_partial(out, series, ts, arms, numbers, repeat, provenance, pre, results)
                if stopped:
                    break
            if stopped:
                break
        if stopped:
            print(f"[job_series] stopping: {stopped}", flush=True)
            break

    summary = summarize(results, arms)
    print_report(results, arms, summary)
    own = manifest_violations(results)
    if pre["status"] == "skipped" and not dry_run:
        own.append({"severity": "warn", "check": "preflight_skipped", "arm": None, "job": None,
                    "repeat": None, "detail": "run started with --skip-preflight"})
    data = {
        "series": series, "timestamp": ts, "arms": arms,
        "jobs": sorted({r["job"] for r in results} | set(numbers)), "dry_run": dry_run,
        "resumed": bool(resume), "repeat": repeat,
        "model_pin": {"model": PINNED_MODEL, "blocked_ladder_ids": list(BLOCKED_LADDER_IDS),
                      "env": PIN_ENV},
        "provenance": {**provenance, "finished_at": _now()},
        "preflight": pre, "valid": None, "violations": own,
        "run_dir": str(run_root), "stopped": stopped,
        "results": results, "summary": summary,
    }
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  Saved {out}")
    finish(data, out, run_root, run_root, own)
    return 2 if stopped and not results else 0


# ── revalidate ────────────────────────────────────────────────────────────────

_SECTION = re.compile(r"^===== \[(\S+) j(\d+)\] [^\n]*=====$", re.M)


def log_sections(log_text: str) -> dict[tuple[str, int], list[str]]:
    """{(arm, job): [attempt log text, ...]} in log order, from one arm's run.log."""
    out: dict[tuple[str, int], list[str]] = {}
    heads = list(_SECTION.finditer(log_text))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(log_text)
        out.setdefault((h.group(1), int(h.group(2))), []).append(log_text[h.start():end])
    return out


def revalidate(ts: str, data_root: Path | None = None, out_root: Path | None = None) -> int:
    """Re-judge an existing run's validity from its per-arm logs.

    Reads <data_root>/job_series_<ts>.json and each row's run.log section (the
    last attempt of that arm's job: the one the row records), applies today's
    detect_invalid (INVALID_MARKERS, 0 turns, Aider with no files / only asking
    for files and no edits), and writes <out_root>/job_series_<ts>_revalidated.json
    with the invalid flags and the paired-exclusion summary. The run itself is
    only read.
    """
    data_root = data_root or (REPO / ".awos")
    out_root = out_root or (REPO / ".awos")
    src = data_root / f"job_series_{ts}.json"
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[job_series] can't read {src}: {exc}")
        return 1
    arms = list(data.get("arms") or sorted({r["arm"] for r in data.get("results", [])}))
    run_dir = Path(data.get("run_dir") or "")
    if not run_dir.is_dir():
        run_dir = data_root / "job_series" / ts
    multi = _multi(data.get("results", []))
    sections: dict[tuple[int, str], dict] = {}

    def section(rep: int, arm: str, job: int) -> str | None:
        if (rep, arm) not in sections:
            log = (run_dir / f"r{rep}" / arm if multi else run_dir / arm) / "run.log"
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
                sections[(rep, arm)] = log_sections(text)
            except OSError:
                sections[(rep, arm)] = {}
        attempts = sections[(rep, arm)].get((arm, job))
        return attempts[-1] if attempts else None

    results = []
    changed = []
    for row in data.get("results", []):
        r = dict(row)
        text = section(_rep(r), r["arm"], r["job"])
        before = bool(r.get("invalid"))
        r["invalid_before"] = before
        r["invalid_reason_before"] = r.get("invalid_reason")
        if text is None:
            r["revalidation"] = "no log section found; kept as recorded"
            results.append(r)
            continue
        reason = detect_invalid(text, int(r.get("turns", 0) or 0), bool(data.get("dry_run")))
        if "[harness_aider]" in text:
            r["aider_no_edit"] = aider_no_edit(text)
        # Rows already invalid stay invalid (their retries were judged live).
        final = reason or (r.get("invalid_reason") if before else None)
        r["invalid"] = final is not None
        r["invalid_reason"] = final
        r["revalidation"] = reason or "valid"
        if r["invalid"] != before:
            changed.append(f"{r['arm']} j{r['job']:02d}"
                           + (f" r{_rep(r)}" if multi else "") + f": {final}")
        results.append(r)

    summary = summarize(results, arms)
    print(f"[job_series] revalidated {src}")
    for c in changed:
        print(f"  newly invalid: {c}")
    if not changed:
        print("  no row changed validity")
    print_report(results, arms, summary)
    before_summary = data.get("summary") or {}
    for arm in arms:
        a = before_summary.get(arm, {}).get("all", {})
        b = summary[arm]["all"]
        print(f"  {arm:<6} before: solved {a.get('solved', '?')}/{a.get('jobs', '?')}  "
              f"after: solved {b['solved']}/{b['jobs']} (paired exclusion)")
    out_root.mkdir(parents=True, exist_ok=True)
    out = out_root / f"job_series_{ts}_revalidated.json"
    own = manifest_violations(results)
    revalidated = {
        **{k: v for k, v in data.items()
           if k not in ("results", "summary", "valid", "violations", "health", "report")},
        "valid_before": data.get("valid"),
        "revalidated_from": str(src), "revalidated_at": time.strftime("%Y%m%dT%H%M%S"),
        "revalidation_rules": {"invalid_markers": list(INVALID_MARKERS),
                               "aider_no_edit_invalid": list(_ha().NO_EDIT_SETUP_REASONS),
                               "zero_turns": True},
        "newly_invalid": changed,
        "summary_before": before_summary,
        "valid": None, "violations": own,
        "results": results, "summary": summary,
    }
    out.write_text(json.dumps(revalidated, indent=2), encoding="utf-8")
    print(f"  Saved {out}")
    # The run's own dir is only read; health.json and the report go next to the output.
    finish(revalidated, out, run_dir, out_root / "job_series" / f"{ts}_revalidated", own)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["_child"]:
        run_child(Path(argv[1]), Path(argv[2]))
        return 0
    if argv[:1] == ["_dry_child"]:
        run_dry_child(Path(argv[1]), Path(argv[2]))
        return 0
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["validate", "run", "revalidate"])
    parser.add_argument("ts", nargs="?", default=None,
                        help="revalidate: the run's timestamp, e.g. 20260929T131340")
    parser.add_argument("--series", default="ordertool")
    parser.add_argument("--arms", default="off,on",
                        help=f"comma list of {'|'.join(ARM_CHILDREN)} (default off,on)")
    parser.add_argument("--jobs", default=None, help="e.g. 1-12 or 1-3,7 (default all)")
    parser.add_argument("--repeat", type=int, default=1,
                        help="run the selected jobs N times, each repeat with fresh state (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="no-op child instead of the orchestrator")
    parser.add_argument("--skip-preflight", action="store_true",
                        help="don't validate the selected jobs before the run (recorded as skipped)")
    parser.add_argument("--series-root", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--out-root", default=None, help="where results/state go (default .awos/)")
    parser.add_argument("--data-root", default=None,
                        help="revalidate: where the run's results/logs are (default --out-root)")
    parser.add_argument("--env-file", default=None, help=".env to copy into each arm (default: repo's)")
    parser.add_argument("--resume", default=None, metavar="TS",
                        help="continue run TS (e.g. 20260926T170601) with its notebooks; pair with --jobs")
    args = parser.parse_args(argv)
    root = Path(args.series_root) if args.series_root else None
    out_root = Path(args.out_root) if args.out_root else None
    if args.command == "validate":
        return validate(args.series, root)
    if args.command == "revalidate":
        if not args.ts:
            parser.error("revalidate needs the run's timestamp")
        return revalidate(args.ts, Path(args.data_root) if args.data_root else out_root, out_root)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    if not arms or any(a not in ARM_CHILDREN for a in arms):
        parser.error(f"--arms takes any of {', '.join(ARM_CHILDREN)}")
    return run(args.series, arms, args.jobs, args.dry_run, root, out_root, args.env_file,
               args.resume, args.repeat, args.skip_preflight)


if __name__ == "__main__":
    sys.exit(main())

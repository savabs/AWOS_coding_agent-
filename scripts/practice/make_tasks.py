#!/usr/bin/env python3
"""make_tasks.py — LLM-free practice-task generator for idle-time practice (T9).

Spec: docs/specs/idle_practice.md. SWE-smith-style bug injection
(arXiv 2504.21798, "procedural modification" strategy) with no model calls:

    python scripts/practice/make_tasks.py <repo_dir> --out <series_root> --n 10
        [--name NAME] [--budget-s 1800] [--per-file 2] [--seed 0]
        [--max-fail-frac 0.5] [--max-tries 400] [--dead-file-after 12]

1. Copies <repo_dir> to a scratch dir (the original is never written).
2. Runs the visible suite (`python -m pytest tests`) once: it must be green.
3. For candidate mutations of source files (never tests/), one at a time:
   apply -> run the suite -> keep iff the suite still collects (pytest exit
   code 1, same number of test cases) and >=1 previously passing test fails
   (fail-to-pass, SWE-smith's validation rule). Timeouts, import errors and
   collection errors are rejected. Mutants are deduped by normalized AST and
   by (file, failing-test set); at most --per-file kept per source file.
4. Each kept mutant becomes its own job series, in the format of
   ~/.awos-harness/real_series/real_*:

       <name>_<NNN>/
         base/                       repo with the mutant applied and the failing
                                     tests' files removed from tests/
         jobs/01_regression/
           task.json                 goal = templated report (failing test names +
                                     assertion messages; no fix hint)
           hidden_tests/test_hidden_<file>.py   the original failing test files
           reference/<src path>      the original source file
         SOURCE.md                   provenance + mutation type
         practice.json               machine-readable metadata (capability map input)

   The remaining visible suite is re-run on base/ and must pass.

Validate with:  python scripts/job_series.py validate --series <name>_<NNN> --series-root <out>
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutators as M  # noqa: E402

PY = sys.executable
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".tox", ".nox", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", "node_modules", ".awos", "build", "dist",
             ".hypothesis", ".eggs"}
NON_SOURCE_DIRS = {"tests", "test", "docs", "doc", "examples", "benchmarks", "scripts"}
NON_SOURCE_FILES = {"setup.py", "conftest.py", "noxfile.py", "_version.py", "version.py"}
MAX_LISTED = 8


# ── repo handling ─────────────────────────────────────────────────────────────


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in SKIP_DIRS or n.endswith((".pyc", ".egg-info"))}


def copy_repo(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest, ignore=_ignore, symlinks=True)
    return dest


def source_files(repo: Path) -> list[str]:
    """Repo-relative paths of mutable source modules (never tests)."""
    out = []
    for f in sorted(repo.rglob("*.py")):
        rel = f.relative_to(repo)
        if any(p in SKIP_DIRS or p in NON_SOURCE_DIRS for p in rel.parts[:-1]):
            continue
        if rel.name in NON_SOURCE_FILES or rel.name.startswith("test_") or rel.name.endswith("_test.py"):
            continue
        out.append(rel.as_posix())
    return out


# ── running the suite ─────────────────────────────────────────────────────────


def run_suite(project: Path, timeout: float) -> dict:
    """Run `python -m pytest tests` in project. Returns
    {"exit", "timed_out", "seconds", "total", "passed": set, "failed": {id: msg}, "file": {id: path}}."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"}
    env.pop("PYTEST_ADDOPTS", None)
    fd, xml_path = tempfile.mkstemp(suffix=".xml", prefix="practice_junit_")
    os.close(fd)
    t0 = time.time()
    res = {"exit": None, "timed_out": False, "seconds": 0.0, "total": 0,
           "passed": set(), "failed": {}, "file": {}}
    try:
        proc = subprocess.run(
            [PY, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
             f"--junitxml={xml_path}", "-o", "junit_family=xunit1"],
            cwd=project, capture_output=True, text=True, timeout=timeout, env=env)
        res["exit"] = proc.returncode
    except subprocess.TimeoutExpired:
        res["timed_out"] = True
        res["seconds"] = time.time() - t0
        os.unlink(xml_path)
        return res
    res["seconds"] = time.time() - t0
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError):
        root = None
    finally:
        try:
            os.unlink(xml_path)
        except OSError:
            pass
    if root is None:
        return res
    for tc in root.iter("testcase"):
        tid = f"{tc.get('classname', '')}::{tc.get('name', '')}"
        res["total"] += 1
        res["file"][tid] = tc.get("file") or ""
        bad = tc.find("failure")
        if bad is None:
            bad = tc.find("error")
        if bad is not None:
            res["failed"][tid] = _clean_msg(bad.get("message") or (bad.text or ""), project)
        elif tc.find("skipped") is None:
            res["passed"].add(tid)
    return res


def _clean_msg(msg: str, project: Path) -> str:
    msg = msg.replace(str(project) + os.sep, "").replace(str(project), "")
    lines = [ln.strip() for ln in msg.strip().splitlines() if ln.strip()]
    return (lines[0] if lines else "")[:240]


def test_file_of(tid: str, files: dict[str, str]) -> str:
    """tests/... path of a test id (junit xunit1 `file` attribute; classname fallback)."""
    f = files.get(tid) or ""
    if f:
        return Path(f).as_posix()
    mod = tid.split("::", 1)[0].split(".")
    # drop trailing class-name parts (CamelCase)
    while mod and mod[-1][:1].isupper():
        mod.pop()
    return "/".join(mod) + ".py"


# ── goal text ─────────────────────────────────────────────────────────────────


def _display(tid: str) -> str:
    cls, name = tid.split("::", 1)
    parts = cls.split(".")
    klass = parts[-1] if parts and parts[-1][:1].isupper() else ""
    return f"{klass}.{name}" if klass else name


def goal_text(repo_name: str, failing: dict[str, str], files: dict[str, str]) -> str:
    """Templated regression report from failing test names + assertion messages.
    Names the symptoms only: never the mutated file, line or operator."""
    by_file: dict[str, list[str]] = defaultdict(list)
    for tid in sorted(failing):
        by_file[Path(test_file_of(tid, files)).name].append(tid)
    n = len(failing)
    lines = [f"Regression in {repo_name}: {n} previously passing test{'s' if n != 1 else ''} "
             f"now fail{'s' if n == 1 else ''}", "",
             "Some library behaviour broke. The following checks from the project's test "
             "suite fail on the current code:", ""]
    shown = 0
    for fname, tids in sorted(by_file.items()):
        for tid in tids:
            if shown >= MAX_LISTED:
                break
            msg = failing[tid]
            lines.append(f"- `{_display(tid)}` ({fname})" + (f": {msg}" if msg else ""))
            shown += 1
    if n > shown:
        lines.append(f"- ... and {n - shown} more in the same test file(s)")
    lines += ["", "Those test files are not in the repository right now. Find the cause in "
              "the library code and fix it so this behaviour works again; keep the existing "
              "tests passing and do not edit tests."]
    return "\n".join(lines)


# ── series writing ────────────────────────────────────────────────────────────


def hidden_name(rel_in_tests: Path) -> Path:
    stem = rel_in_tests.name
    stem = stem[len("test_"):] if stem.startswith("test_") else stem
    return rel_in_tests.with_name(f"test_hidden_{stem}")


def write_series(sdir: Path, original: Path, src_rel: str, mutated: str,
                 hidden_files: list[str], failing: dict[str, str], files: dict[str, str],
                 meta: dict) -> None:
    if sdir.exists():
        shutil.rmtree(sdir)
    base = copy_repo(original, sdir / "base")
    (base / src_rel).write_text(mutated, encoding="utf-8")
    job = sdir / "jobs" / "01_regression"
    for hf in hidden_files:
        rel = Path(hf).relative_to("tests")
        dest = job / "hidden_tests" / hidden_name(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original / hf, dest)
        (base / hf).unlink()
    ref = job / "reference" / src_rel
    ref.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original / src_rel, ref)
    task = {"id": f"prac_{meta['key'][:8]}", "goal": goal_text(meta["repo"], failing, files),
            "max_turns": 150, "max_cost_usd": 1.0, "timeout_min": 30}
    (job / "task.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    (sdir / "practice.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (sdir / "SOURCE.md").write_text(source_md(sdir.name, meta), encoding="utf-8")


def source_md(name: str, meta: dict) -> str:
    return "\n".join([
        f"# {name}", "",
        f"- Practice task (T9 idle-time practice; docs/specs/idle_practice.md). Not a real bug.",
        f"- Repo: {meta['repo']} (from `{meta['repo_dir']}`)",
        f"- Mutation type: **{meta['op']}** ({meta['desc'] or '-'}) in `{meta['file']}` line {meta['line']}",
        f"- Generator: scripts/practice/make_tasks.py (LLM-free procedural AST mutation), "
        f"seed {meta['seed']}, generated {meta['generated_at']}",
        f"- Validation at generation: suite collects ({meta['total_tests']} tests), "
        f"{len(meta['failing_tests'])} previously passing test(s) fail; visible remainder "
        f"passes on base/ ({meta['visible_tests']} tests).", "",
        "## Construction",
        "- base/ = copy of the repo with the mutant applied; the failing tests' files removed from tests/.",
        "- hidden_tests/ = those original test files, renamed test_hidden_<name>.py (the oracle).",
        "- reference/ = the original, unmutated source file.",
        "- Goal = templated from failing test names + assertion messages (no fix hint).", "",
        "## Diff (mutant vs original)", "", "```diff", meta["diff"].rstrip(), "```", ""])


def _diff(path: str, a: str, b: str) -> str:
    import difflib
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
                                        f"a/{path}", f"b/{path}", n=1))


# ── candidate scheduling ──────────────────────────────────────────────────────


def schedule(cands: list[tuple[str, M.Mutation]], rng: random.Random) -> list[tuple[str, M.Mutation]]:
    """Round-robin over operators (random order within each) so a run's yield
    spans mutation types instead of being dominated by off_by_one."""
    by_op: dict[str, list] = defaultdict(list)
    for c in cands:
        by_op[c[1].op].append(c)
    for v in by_op.values():
        rng.shuffle(v)
    ops = sorted(by_op)
    rng.shuffle(ops)
    out = []
    while any(by_op.values()):
        for op in ops:
            if by_op[op]:
                out.append(by_op[op].pop())
    return out


# ── main loop ─────────────────────────────────────────────────────────────────


def generate(repo_dir: Path, out_root: Path, n: int, name: str | None = None,
             budget_s: float = 1800.0, per_file: int = 2, seed: int = 0,
             max_fail_frac: float = 0.5, max_tries: int = 400, dead_file_after: int = 12,
             log=print) -> dict:
    t_start = time.time()
    repo_dir = repo_dir.resolve()
    out_root = out_root.resolve()
    if out_root == repo_dir or repo_dir in out_root.parents:
        raise SystemExit("--out must not be inside the repo")
    if not (repo_dir / "tests").is_dir():
        raise SystemExit(f"{repo_dir} has no tests/ directory (job series judge tests/)")
    repo_name = name or (repo_dir.parent.name if repo_dir.name == "base" else repo_dir.name)
    rng = random.Random(seed)
    stats = {"repo": repo_name, "repo_dir": str(repo_dir), "seed": seed, "tried": 0, "kept": 0,
             "rejected": Counter(), "per_op": defaultdict(lambda: {"tried": 0, "kept": 0}),
             "series": []}
    with tempfile.TemporaryDirectory(prefix="practice_") as tmp:
        work = copy_repo(repo_dir, Path(tmp) / "work")
        base_run = run_suite(work, timeout=max(120.0, budget_s / 4))
        if base_run["exit"] != 0 or not base_run["passed"]:
            raise SystemExit(f"baseline suite not green (exit {base_run['exit']}, "
                             f"{len(base_run['failed'])} failing) — practice needs a green suite")
        per_run_timeout = max(20.0, 5 * base_run["seconds"] + 10)
        log(f"[practice] {repo_name}: baseline {len(base_run['passed'])} passed in "
            f"{base_run['seconds']:.1f}s; per-run timeout {per_run_timeout:.0f}s")
        cands = []
        originals = {}
        for rel in source_files(work):
            text = (work / rel).read_text(encoding="utf-8")
            originals[rel] = text
            cands += [(rel, m) for m in M.enumerate_mutations(text)]
        order = schedule(cands, rng)
        log(f"[practice] {len(originals)} source files, {len(order)} candidate mutations")
        kept_per_file: Counter = Counter()
        hits: Counter = Counter()
        misses: Counter = Counter()
        seen_keys: set[str] = set()
        seen_sigs: set[tuple] = set()
        test_files = {test_file_of(t, base_run["file"]) for t in base_run["passed"]}
        for rel, m in order:
            if stats["kept"] >= n or stats["tried"] >= max_tries:
                break
            if time.time() - t_start > budget_s:
                stats["rejected"]["budget_exhausted"] += 1
                break
            if kept_per_file[rel] >= per_file:
                continue
            if dead_file_after and misses[rel] >= dead_file_after and not hits[rel]:
                stats["rejected"]["skipped_dead_file"] += 1   # tests never reach this file
                continue
            mutated = M.apply(originals[rel], m)
            if mutated is None:
                stats["rejected"]["not_applicable"] += 1
                continue
            key = M.mutant_key(rel, mutated)
            if key in seen_keys:
                stats["rejected"]["duplicate_ast"] += 1
                continue
            seen_keys.add(key)
            stats["tried"] += 1
            stats["per_op"][m.op]["tried"] += 1
            (work / rel).write_text(mutated, encoding="utf-8")
            try:
                r = run_suite(work, per_run_timeout)
            finally:
                (work / rel).write_text(originals[rel], encoding="utf-8")
            verdict = classify(r, base_run, max_fail_frac)
            if verdict == "no_test_flipped":
                misses[rel] += 1
            else:
                hits[rel] += 1
            if verdict != "keep":
                stats["rejected"][verdict] += 1
                continue
            flipped = {t: r["failed"][t] for t in r["failed"] if t in base_run["passed"]}
            sig = (rel, frozenset(flipped))
            if sig in seen_sigs:
                stats["rejected"]["duplicate_failure_signature"] += 1
                continue
            hidden = sorted({test_file_of(t, base_run["file"]) for t in flipped})
            if not all(h.startswith("tests/") and (work / h).is_file() for h in hidden):
                stats["rejected"]["failing_test_outside_tests_dir"] += 1
                continue
            if not (test_files - set(hidden)):
                stats["rejected"]["no_visible_tests_left"] += 1
                continue
            seq = stats["kept"] + 1
            sname = f"prac_{repo_name}_{seq:03d}"
            meta = {
                "repo": repo_name, "repo_dir": str(repo_dir), "op": m.op, "desc": m.desc,
                "file": rel, "line": m.lineno, "key": key, "seed": seed,
                "failing_tests": sorted(flipped), "hidden_files": hidden,
                "total_tests": r["total"],
                "visible_tests": sum(1 for t in base_run["passed"]
                                     if test_file_of(t, base_run["file"]) not in hidden),
                "diff": _diff(rel, originals[rel], mutated),
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            sdir = out_root / sname
            write_series(sdir, repo_dir, rel, mutated, hidden, flipped, base_run["file"], meta)
            vis = run_suite(sdir / "base", per_run_timeout)
            if vis["exit"] != 0:
                shutil.rmtree(sdir)
                stats["rejected"]["visible_remainder_fails"] += 1
                continue
            seen_sigs.add(sig)
            kept_per_file[rel] += 1
            stats["kept"] += 1
            stats["per_op"][m.op]["kept"] += 1
            stats["series"].append(sname)
            log(f"[practice] kept {sname}: {m.op} {rel}:{m.lineno} -> "
                f"{len(flipped)} failing test(s) in {len(hidden)} file(s)")
    stats["seconds"] = round(time.time() - t_start, 1)
    stats["rejected"] = dict(stats["rejected"])
    stats["per_op"] = dict(stats["per_op"])
    runs = out_root / "_practice_runs"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    (runs / f"{repo_name}_{stamp}.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    log(f"[practice] {repo_name}: kept {stats['kept']}/{stats['tried']} tried in "
        f"{stats['seconds']}s; rejected {stats['rejected']}")
    return stats


def classify(r: dict, base: dict, max_fail_frac: float) -> str:
    """'keep' or the rejection reason (SWE-smith rule: >=1 fail-to-pass, suite collects)."""
    if r["timed_out"]:
        return "timeout"
    if r["exit"] == 0:
        return "no_test_flipped"
    if r["exit"] != 1:
        return "collection_or_internal_error"
    if r["total"] != base["total"]:
        return "collection_changed"
    flipped = [t for t in r["failed"] if t in base["passed"]]
    if not flipped:
        return "no_test_flipped"
    if len(flipped) > max_fail_frac * len(base["passed"]):
        return "too_many_failures"
    return "keep"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo_dir", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="series root to write into")
    ap.add_argument("--n", type=int, default=10, help="mutants to keep")
    ap.add_argument("--name", default=None, help="series name prefix (default: repo dir name)")
    ap.add_argument("--budget-s", type=float, default=1800.0, help="wall-clock budget")
    ap.add_argument("--per-file", type=int, default=2, help="max kept mutants per source file")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-fail-frac", type=float, default=0.5)
    ap.add_argument("--max-tries", type=int, default=400)
    ap.add_argument("--dead-file-after", type=int, default=12,
                    help="stop mutating a file after this many tries flip no test (0 = never)")
    a = ap.parse_args(argv)
    stats = generate(a.repo_dir, a.out, a.n, a.name, a.budget_s, a.per_file, a.seed,
                     a.max_fail_frac, a.max_tries, a.dead_file_after)
    return 0 if stats["kept"] else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Two-arm bar test runner.

Pre-registration: docs/experiments/bar_test_2026-08-31.md
Thresholds and grading are fixed there. This script only executes and measures.

  Arm A  AWOS harness, unattended
  Arm B  raw single-shot, same model, same goal

Both arms edit via SEARCH/REPLACE blocks so the edit-application mechanism is
identical and the comparison isolates reasoning rather than diff-format luck.

Grading never trusts AWOS's own report: the suite is run directly and the
test/ tree is checked for tampering.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LAB = Path(os.getenv("BAR_TEST_LAB", "/Users/becmachlean/.claude/jobs/3c76a952/tmp/redrepo"))
PRISTINE = LAB / "bottle-0.12.18"
TARGET_PY = LAB / ".venv-target" / "bin" / "python"
MODEL = "claude-haiku-4-5-20251001"

GOAL = (
    "The test suite in this repository fails on Python 3.12. "
    "Fix the library source so that the full pytest suite passes. "
    "Do not modify anything under test/ - the tests define correct behaviour."
)


def _load_env() -> None:
    envf = REPO / ".env"
    if not envf.exists():
        return
    for line in envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def prep(arm: str) -> Path:
    ws = LAB / f"ws_{arm}"
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(PRISTINE, ws)
    shutil.rmtree(ws / "__pycache__", ignore_errors=True)
    return ws


def run_suite(ws: Path) -> dict:
    """Run the target suite directly. This is the grader; AWOS is not consulted."""
    p = subprocess.run(
        [str(TARGET_PY), "-m", "pytest", "-q", "--no-header",
         "-p", "no:cacheprovider", "--color=no",
         # Amendment 2026-08-31: without this one collection error aborts the
         # entire suite, so every partial result grades as 0 passed. See the
         # amendment note in docs/experiments/bar_test_2026-08-31.md.
         "--continue-on-collection-errors"],
        cwd=str(ws), capture_output=True, text=True, timeout=600,
    )
    out = re.sub(r"\x1b\[[0-9;]*m", "", (p.stdout or "") + (p.returncode and (p.stderr or "") or ""))
    def grab(pat):
        m = re.search(pat, out)
        return int(m.group(1)) if m else 0
    return {
        "passed": grab(r"(\d+) passed"),
        "failed": grab(r"(\d+) failed"),
        "errors": grab(r"(\d+) error"),
        "returncode": p.returncode,
        "tail": out.strip().splitlines()[-1] if out.strip() else "",
        "raw": out,
    }


def tampering(ws: Path) -> list[str]:
    """Any change under test/ is an automatic fail."""
    p = subprocess.run(["git", "status", "--porcelain"], cwd=str(ws),
                       capture_output=True, text=True)
    changed = [ln[3:].strip() for ln in p.stdout.splitlines() if ln.strip()]
    return [c for c in changed if c.startswith("test/")]


def changed_files(ws: Path) -> list[str]:
    p = subprocess.run(["git", "status", "--porcelain"], cwd=str(ws),
                       capture_output=True, text=True)
    return [ln[3:].strip() for ln in p.stdout.splitlines()
            if ln.strip() and "__pycache__" not in ln]


def _budget_total() -> float:
    f = REPO / ".awos" / "budget.json"
    if not f.exists():
        return 0.0
    try:
        return sum(float(r.get("cost", 0) or 0) for r in json.loads(f.read_text()))
    except Exception:
        return 0.0


def arm_a(ws: Path, timeout_s: int) -> dict:
    """AWOS harness, unattended."""
    env = {
        **os.environ,
        "AWOS_PROVIDER": "anthropic",
        "AWOS_SAFE_TO_RUN_TESTS": "1",
        "AWOS_TEST_PYTHON": str(TARGET_PY),
        "AWOS_CHEAP_ONLY": "1",
    }
    before = _budget_total()
    t0 = time.time()
    p = subprocess.run(
        [str(REPO / ".venv" / "bin" / "python"), str(REPO / "awos.py"),
         "worker", "start", GOAL, "--root", str(ws), "--auto-approve"],
        cwd=str(REPO), capture_output=True, text=True, timeout=timeout_s, env=env,
    )
    elapsed = time.time() - t0
    log = LAB / "arm_a.log"
    log.write_text((p.stdout or "") + "\n---STDERR---\n" + (p.stderr or ""), encoding="utf-8")
    return {"elapsed_s": round(elapsed, 1), "cost_usd": round(_budget_total() - before, 6),
            "returncode": p.returncode, "log": str(log)}


SR_RE = re.compile(
    r"<<<+\s*SEARCH\s*\n(.*?)\n=======\s*\n(.*?)\n>>>+\s*REPLACE", re.S)


def arm_b(ws: Path) -> dict:
    """Raw single-shot, same model, same goal."""
    from openai import OpenAI
    baseline = run_suite(ws)
    src = (ws / "bottle.py").read_text(encoding="utf-8")
    prompt = (
        f"{GOAL}\n\n"
        f"pytest output:\n```\n{baseline['raw'][:8000]}\n```\n\n"
        f"bottle.py:\n```python\n{src}\n```\n\n"
        "Reply ONLY with SEARCH/REPLACE blocks for bottle.py in exactly this form:\n"
        "<<<<<<< SEARCH\n<exact existing lines>\n=======\n<replacement lines>\n>>>>>>> REPLACE\n"
    )
    c = OpenAI(api_key=os.getenv("ANTHROPIC_API_KEY"),
               base_url="https://api.anthropic.com/v1/")
    t0 = time.time()
    r = c.chat.completions.create(
        model=MODEL, max_tokens=8000, temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = time.time() - t0
    text = r.choices[0].message.content or ""
    (LAB / "arm_b.log").write_text(text, encoding="utf-8")
    blocks = SR_RE.findall(text)
    applied = failed = 0
    cur = src
    for search, replace in blocks:
        if search and search in cur:
            cur = cur.replace(search, replace, 1)
            applied += 1
        else:
            failed += 1
    (ws / "bottle.py").write_text(cur, encoding="utf-8")
    u = r.usage
    cost = (u.prompt_tokens / 1e6) * 1.00 + (u.completion_tokens / 1e6) * 5.00
    return {"elapsed_s": round(elapsed, 1), "cost_usd": round(cost, 6),
            "blocks": len(blocks), "applied": applied, "unmatched": failed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["baseline", "a", "b", "grade"])
    ap.add_argument("--timeout", type=int, default=7200)
    args = ap.parse_args()
    _load_env()

    if args.mode == "baseline":
        print(json.dumps({"baseline": run_suite(PRISTINE) | {"raw": None}}, indent=2))
        return 0

    if args.mode in ("a", "b"):
        ws = prep(args.mode)
        print(f"[{args.mode.upper()}] workspace {ws}")
        meta = arm_a(ws, args.timeout) if args.mode == "a" else arm_b(ws)
        result = run_suite(ws)
        payload = {
            "arm": args.mode.upper(), "meta": meta,
            "suite": {k: v for k, v in result.items() if k != "raw"},
            "changed_files": changed_files(ws),
            "test_files_modified": tampering(ws),
        }
        (LAB / f"result_{args.mode}.json").write_text(json.dumps(payload, indent=2))
        print(json.dumps(payload, indent=2))
        return 0

    if args.mode == "grade":
        out = {}
        for a in ("a", "b"):
            f = LAB / f"result_{a}.json"
            out[a] = json.loads(f.read_text()) if f.exists() else None
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

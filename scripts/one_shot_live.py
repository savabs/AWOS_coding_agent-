"""one_shot_live.py — T4 live proof: ONE one-shot call on a real job-series job.

Builds the job's starting project in a temp dir (base + references of the
earlier jobs), runs one_shot.run_one_shot once with the configured provider,
then judges it with the job's hidden tests. Flags come from the environment
(AWOS_PROVIDER=local, AWOS_EDIT_GRAMMAR=1, AWOS_FUZZY_APPLY=1, ...).

  python scripts/one_shot_live.py --series ordertool --job 8 [--model deepseek-v4-flash]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scaffold" / "agent"), str(REPO / "scaffold"), str(REPO)]


def build_project(series: Path, job_no: int, dest: Path) -> Path:
    shutil.copytree(series / "base", dest)
    jobs = sorted(p for p in (series / "jobs").iterdir() if p.is_dir())
    for j in jobs[:job_no - 1]:
        ref = j / "reference"
        if ref.is_dir():
            shutil.copytree(ref, dest, dirs_exist_ok=True)
    return jobs[job_no - 1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default="ordertool")
    ap.add_argument("--job", type=int, default=8)
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--reply-out", default="", help="write the full replies here")
    ap.add_argument("--deepseek-direct", action="store_true",
                    help="bypass OpenRouter: DeepSeek's own API (AWOS_BASE_URL path)")
    a = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env", override=False)
    if a.deepseek_direct:
        os.environ["OPENROUTER_API_KEY"] = ""
        os.environ["AWOS_BASE_URL"] = "https://api.deepseek.com/v1"
        os.environ["AWOS_BASE_URL_KEY"] = os.environ.get("DEEPSEEK_API_KEY", "")
    import one_shot
    import providers

    tmp = Path(tempfile.mkdtemp(prefix="t4_live_"))
    project = tmp / "project"
    job = build_project(REPO / "tests" / "job_series" / a.series, a.job, project)
    task = json.loads((job / "task.json").read_text())
    model = providers.local_model_id() if providers.local_mode() else a.model
    client = one_shot.one_shot_client()
    print(f"[LIVE] job={job.name} model={model} local={providers.local_mode()} "
          f"grammar={os.getenv('AWOS_EDIT_GRAMMAR', '0')} fuzzy={os.getenv('AWOS_FUZZY_APPLY', '0')}")
    t = time.time()
    shot = one_shot.run_one_shot(client, model, str(project), task["goal"])
    print(f"[LIVE] elapsed={time.time() - t:.1f}s blocks={shot.blocks} applied={shot.applied} "
          f"failed={len(shot.failed)} repair_calls={shot.repair_calls} repaired={shot.repaired} "
          f"cost=${shot.cost_usd:.5f} in={shot.input_tokens} out={shot.output_tokens} "
          f"error={shot.error}")
    for f in shot.failed:
        print(f"[LIVE] failed block: {f.get('path')}: {str(f.get('reason'))[:200]}")
    hidden = job / "hidden_tests"
    for p in hidden.glob("test_*.py"):
        shutil.copy(p, project / "tests" / p.name)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                       cwd=project, capture_output=True, text=True, timeout=600)
    print("[LIVE] hidden+visible tests:", (r.stdout.strip().splitlines() or ["?"])[-1])
    print("[LIVE] reply head:\n" + shot.reply[:800])
    if a.reply_out:
        Path(a.reply_out).write_text(shot.reply + "\n\n=== REPAIR ===\n" + shot.repair_reply)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

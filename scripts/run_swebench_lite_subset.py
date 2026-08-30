#!/usr/bin/env python3
"""
SWE-bench Lite subset runner for AWOS.

Modes:
  --dry-run     Load default 10-instance subset, print manifest (no API, no clone)
  --predict     Clone repos, run ReAct, write predictions.jsonl + report.json
  --evaluate    Run official SWE-bench Docker harness on predictions.jsonl

Examples:
  python3 scripts/run_swebench_lite_subset.py --dry-run
  python3 scripts/run_swebench_lite_subset.py --predict --limit 1
  python3 scripts/run_swebench_lite_subset.py --predict --instance-ids sympy__sympy-20590
  python3 scripts/run_swebench_lite_subset.py --evaluate --predictions .awos/swebench_lite/predictions.jsonl
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser(description="AWOS SWE-bench Lite subset harness")
    parser.add_argument("--dry-run", action="store_true", help="Load subset manifest only")
    parser.add_argument("--predict", action="store_true", help="Run ReAct and export patches")
    parser.add_argument("--evaluate", action="store_true", help="Docker eval via swebench harness")
    parser.add_argument("--limit", type=int, default=None, help="Max instances (default: all in subset)")
    parser.add_argument("--instance-ids", nargs="+", help="Specific instance_id(s)")
    parser.add_argument("--no-default-subset", action="store_true", help="Use first N from dataset instead")
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--include-hints", action="store_true", help="Include hints_text (not pass@1 clean)")
    parser.add_argument("--work-root", type=Path, default=REPO / ".awos" / "swebench_lite" / "work")
    parser.add_argument("--out-dir", type=Path, default=REPO / ".awos" / "swebench_lite")
    parser.add_argument("--predictions", type=Path, default=None, help="predictions.jsonl for --evaluate")
    parser.add_argument("--model-name", default="awos-react")
    parser.add_argument("--provider", default=None, help="LLM provider: gemini, deepseek, anthropic, openai")
    parser.add_argument("--run-id", default="awos_lite_subset")
    args = parser.parse_args()

    if not any([args.dry_run, args.predict, args.evaluate]):
        args.dry_run = True

    _load_dotenv()
    sys.path.insert(0, str(REPO))

    if args.provider:
        os.environ["AWOS_REACT_PROVIDER"] = args.provider
    elif not os.getenv("AWOS_REACT_PROVIDER"):
        if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            os.environ.setdefault("AWOS_REACT_PROVIDER", "gemini")

    from scaffold.agent.swebench_lite import (
        clone_instance_repo,
        format_manifest,
        load_lite_instances,
        run_instance_react,
        run_swebench_evaluation,
        write_predictions_jsonl,
        write_run_report,
    )

    if args.evaluate:
        pred_path = args.predictions or (args.out_dir / "predictions.jsonl")
        if not pred_path.is_file():
            print(f"✗ Missing predictions: {pred_path}")
            return 1
        print(f"[SWEBENCH] Docker evaluation on {pred_path}")
        print("[SWEBENCH] Requires: pip install swebench + Docker images")
        proc = run_swebench_evaluation(
            pred_path,
            run_id=args.run_id,
            instance_ids=args.instance_ids,
        )
        print(proc.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
        return proc.returncode

    instances = load_lite_instances(
        instance_ids=args.instance_ids,
        limit=args.limit,
        use_default_subset=not args.no_default_subset,
    )
    if not instances:
        print("✗ No instances loaded (check --instance-ids or subset file)")
        return 1

    manifest = format_manifest(instances)
    print("═" * 70)
    print(manifest)
    print("═" * 70)

    if args.dry_run and not args.predict:
        print()
        print("[SWEBENCH] dry-run OK — subset loaded from HuggingFace")
        print("[SWEBENCH] Default subset file: tests/fixtures/swebench_lite/subset_10.txt")
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, inst in enumerate(instances, 1):
        print()
        print(f"── [{i}/{len(instances)}] {inst.instance_id} ──")
        work = args.work_root / inst.instance_id
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        try:
            print(f"[SWEBENCH] Cloning {inst.repo} …")
            clone_instance_repo(inst, work)
            print(f"[SWEBENCH] ReAct @ {work}")
            run_result = run_instance_react(
                inst,
                work,
                max_turns=args.max_turns,
                include_hints=args.include_hints,
            )
        except Exception as exc:
            from scaffold.agent.swebench_lite import InstanceRunResult

            run_result = InstanceRunResult(
                instance_id=inst.instance_id,
                success=False,
                error=str(exc),
                work_dir=str(work),
            )
        results.append(run_result)
        patch_len = len(run_result.model_patch)
        print(
            f"[SWEBENCH] react_success={run_result.react_success} "
            f"patch_bytes={patch_len} latency={run_result.latency_sec:.1f}s"
        )
        if run_result.error:
            print(f"[SWEBENCH] note: {run_result.error[:200]}")

    pred_path = args.out_dir / "predictions.jsonl"
    write_predictions_jsonl(
        (r.to_prediction(args.model_name) for r in results),
        pred_path,
    )
    report = write_run_report(results, args.out_dir / "report.json", model_name=args.model_name)

    print()
    print("═" * 70)
    print(f"Patches submitted: {report['patches_submitted']}/{report['total']}")
    print(f"ReAct self-reported success: {report['react_reported_success']}/{report['total']}")
    print(f"Total cost: ${report.get('total_cost_usd', 0):.4f}")
    print(f"Total tokens: {report.get('total_input_tokens', 0)} in / {report.get('total_output_tokens', 0)} out")
    print(f"Models used: {report.get('models_used', {})}")
    print(f"Avg latency: {report.get('avg_latency_sec', 0):.1f}s/instance")
    print(f"Predictions: {pred_path}")
    print(f"Report:      {args.out_dir / 'report.json'}")
    print()
    print("Next — official grade (Docker):")
    print(f"  python3 scripts/run_swebench_lite_subset.py --evaluate --predictions {pred_path}")
    print("═" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

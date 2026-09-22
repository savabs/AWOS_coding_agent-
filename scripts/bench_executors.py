#!/usr/bin/env python3
"""
bench_executors.py — Single-shot Worker vs tool-using AgentLoop, same cases.

Answers the one question the reward store cannot: does letting the model drive
its own turns beat emitting a patch from a single blind look?

Both executors get the identical case. They differ only in what they can see:

  single-shot   receives the text of buggy.py and nothing else, exactly as
                Orchestrator hands it to Worker today
  agent-loop    receives a staged project directory and the tools to explore
                it — read_file, grep, find_files, list_dir, edit_file,
                run_tests

A case with a context/ directory is unreachable for single-shot by
construction: the constant, field name or signature it needs is in a file it
was never given. That is the hypothesis under test, not a trick — it is the
shape of most real work.

Scoring is the case's own pytest suite, run in a throwaway directory. A case
counts as solved only when every test passes.

Running it cheaply:

    # record once (costs pennies, or nothing against a local model)
    python3 scripts/bench_executors.py --record --cassette-dir .awos/cassettes

    # re-run free and offline, as often as you like, with no API key
    python3 scripts/bench_executors.py --cassette-dir .awos/cassettes

Relationship to scaffold/agent/benchmark_runner.py: that compares single-shot
against MCTS search. This compares single-shot against the agent loop. Both
read the same cases via agent.bench_cases.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scaffold"))

from agent.bench_cases import BugCase, discover_cases, load_case, run_case_tests, stage_case


@dataclass
class ArmResult:
    """How one executor fared on one case."""

    solved: bool = False
    latency_sec: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    detail: str = ""


@dataclass
class CaseOutcome:
    case: str
    tier: str
    multi_file: bool
    single_shot: ArmResult = field(default_factory=ArmResult)
    agent_loop: ArmResult = field(default_factory=ArmResult)


# ── Arms ──────────────────────────────────────────────────────────────────────


def run_single_shot(case: BugCase, worker: Any) -> ArmResult:
    """One model call, buggy.py as the only context, SEARCH/REPLACE out."""
    result = ArmResult(llm_calls=1)
    started = time.monotonic()
    try:
        patch = worker.execute_task(
            task=case.task,
            file_content=case.buggy_source,
            codebase_context={"modules": "benchmark"},
        )
    except Exception as exc:
        result.detail = f"{type(exc).__name__}: {exc}"[:160]
        result.latency_sec = round(time.monotonic() - started, 2)
        return result

    result.latency_sec = round(time.monotonic() - started, 2)
    result.input_tokens = patch.get("input_tokens", 0) or 0
    result.output_tokens = patch.get("output_tokens", 0) or 0

    search = patch.get("search")
    if not patch.get("success") or not search:
        result.detail = "no usable patch returned"
        return result
    if search not in case.buggy_source:
        result.detail = "SEARCH block did not match the file"
        return result

    patched = case.buggy_source.replace(search, patch.get("replace", ""), 1)
    passed, output = run_case_tests(case, source=patched)
    result.solved = passed
    if not passed:
        result.detail = _last_failure_line(output)
    return result


def run_agent_loop(
    case: BugCase,
    client_factory: Any,
    max_turns: int,
    max_cost: Optional[float],
) -> ArmResult:
    """The model explores a staged project and edits it until tests pass."""
    from agent.agent_loop import AgentLoop, build_coding_registry

    result = ArmResult()
    started = time.monotonic()

    with tempfile.TemporaryDirectory() as tmp:
        project = stage_case(case, tmp)
        try:
            client = client_factory(case)
        except Exception as exc:
            result.detail = f"client unavailable: {exc}"[:160]
            return result

        loop = AgentLoop(
            registry=build_coding_registry(str(project)),
            client=client,
            max_turns=max_turns,
            max_cost_usd=max_cost,
        )
        outcome = loop.run(_prompt_for(case))

        result.latency_sec = round(time.monotonic() - started, 2)
        result.llm_calls = outcome.turns
        result.tool_calls = outcome.tool_calls
        result.input_tokens = outcome.input_tokens
        result.output_tokens = outcome.output_tokens
        result.cost_usd = outcome.cost_usd

        # Score the file the agent actually left behind, not what it claimed.
        edited = (project / "buggy.py").read_text(encoding="utf-8")
        passed, output = run_case_tests(case, source=edited)
        result.solved = passed
        if not passed:
            result.detail = f"{outcome.stop_reason}: {_last_failure_line(output)}"

    return result


def _prompt_for(case: BugCase) -> str:
    return (
        f"{case.action}\n\n"
        f"The file to fix is {case.target_file}. The project's tests are in "
        "test_case.py — read them to learn the exact contract, and run them to "
        "check your work."
    )


def _last_failure_line(output: str) -> str:
    for line in reversed(output.strip().splitlines()):
        if line.startswith("FAILED") or line.startswith("E "):
            return line.strip()[:120]
    return (output.strip().splitlines() or [""])[-1][:120]


# ── Clients ───────────────────────────────────────────────────────────────────


def make_client_factory(cassette_dir: Optional[str], record: bool, model: Optional[str]):
    """
    Build the per-case client: a real model when recording, a cassette when not.

    Each case gets its own cassette, named after it, so one case can be
    re-recorded without disturbing the rest.
    """
    from agent.cassette import wrap_for_cassette

    def factory(case: BugCase):
        if not cassette_dir:
            from agent.agent_loop import build_client_from_env

            return build_client_from_env(model)

        path = Path(cassette_dir) / f"{case.name}.json"
        if record:
            from agent.agent_loop import build_client_from_env

            return wrap_for_cassette(
                build_client_from_env(model), path=str(path), mode="record"
            )
        return wrap_for_cassette(None, path=str(path), mode="replay")

    return factory


# ── Reporting ─────────────────────────────────────────────────────────────────


def print_report(outcomes: list[CaseOutcome], arms: set[str]) -> dict[str, Any]:
    total = len(outcomes)
    print(f"\n{'=' * 78}")
    print(f"  {'case':<30} {'tier':<6} {'single-shot':<14} {'agent-loop':<14}")
    print(f"  {'-' * 74}")
    for o in outcomes:
        ss = (
            f"{'PASS' if o.single_shot.solved else 'fail':<5}"
            f"{o.single_shot.llm_calls:>3} call"
            if "single" in arms
            else "   —"
        )
        al = (
            f"{'PASS' if o.agent_loop.solved else 'fail':<5}"
            f"{o.agent_loop.llm_calls:>3}t/{o.agent_loop.tool_calls}c"
            if "agent" in arms
            else "   —"
        )
        marker = "*" if o.multi_file else " "
        print(f"  {o.case:<30}{marker}{o.tier:<5} {ss:<14} {al:<14}")

    summary: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_cases": total,
        "cases": [asdict(o) for o in outcomes],
    }

    print(f"  {'-' * 74}")
    for label, key in (("single-shot", "single_shot"), ("agent-loop", "agent_loop")):
        arm_key = "single" if key == "single_shot" else "agent"
        if arm_key not in arms:
            continue
        results = [getattr(o, key) for o in outcomes]
        solved = sum(1 for r in results if r.solved)
        multi = [o for o in outcomes if o.multi_file]
        single = [o for o in outcomes if not o.multi_file]
        multi_solved = sum(1 for o in multi if getattr(o, key).solved)
        single_solved = sum(1 for o in single if getattr(o, key).solved)
        cost = sum(r.cost_usd for r in results)

        print(
            f"  {label:<14} {solved}/{total} ({solved / total:.0%})   "
            f"self-contained {single_solved}/{len(single)}   "
            f"multi-file {multi_solved}/{len(multi)}   "
            f"${cost:.4f}"
        )
        summary[key] = {
            "solved": solved,
            "pass_rate": round(solved / total, 3) if total else 0.0,
            "self_contained_solved": single_solved,
            "self_contained_total": len(single),
            "multi_file_solved": multi_solved,
            "multi_file_total": len(multi),
            "cost_usd": round(cost, 6),
        }

    if arms == {"single", "agent"} and total:
        delta = summary["agent_loop"]["pass_rate"] - summary["single_shot"]["pass_rate"]
        summary["delta_pass_rate"] = round(delta, 3)
        print(f"  {'-' * 74}")
        print(f"  {'delta':<14} {delta:+.0%} (agent-loop minus single-shot)")
    print(f"{'=' * 78}")
    print("  * = multi-file case: the answer lives in a file single-shot never sees\n")
    return summary


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", default="tests/bug_cases")
    parser.add_argument("--case", action="append", help="Run only these cases (repeatable)")
    parser.add_argument("--arms", default="agent", choices=["agent", "single", "both"],
                        help="Which executors to run (default: agent)")
    parser.add_argument("--cassette-dir", default=None,
                        help="Replay per-case cassettes from here (free, no API key)")
    parser.add_argument("--record", action="store_true",
                        help="Call the real model and write cassettes")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--max-cost", type=float, default=None,
                        help="Abort a single case once it has cost this much (USD)")
    parser.add_argument("--out", default=None, help="Write the summary JSON here")
    args = parser.parse_args()

    case_dirs = discover_cases(args.cases_dir)
    if args.case:
        wanted = set(args.case)
        case_dirs = [d for d in case_dirs if d.name in wanted]
    if not case_dirs:
        print(f"No cases found in {args.cases_dir}")
        return 1

    arms = {"agent", "single"} if args.arms == "both" else {args.arms}
    client_factory = make_client_factory(args.cassette_dir, args.record, args.model)

    worker = None
    if "single" in arms:
        try:
            from agent.worker import Worker

            worker = Worker()
        except Exception as exc:
            print(f"Cannot run the single-shot arm: {exc}")
            return 1

    print(f"Cases: {len(case_dirs)}   Arms: {', '.join(sorted(arms))}"
          + (f"   Cassettes: {args.cassette_dir}" if args.cassette_dir else ""))

    outcomes: list[CaseOutcome] = []
    for index, case_dir in enumerate(case_dirs, 1):
        case = load_case(case_dir)
        print(f"[{index}/{len(case_dirs)}] {case.name} ...", end=" ", flush=True)
        outcome = CaseOutcome(case=case.name, tier=case.tier, multi_file=case.is_multi_file)

        if "single" in arms:
            outcome.single_shot = run_single_shot(case, worker)
        if "agent" in arms:
            outcome.agent_loop = run_agent_loop(
                case, client_factory, args.max_turns, args.max_cost
            )

        flags = []
        if "single" in arms:
            flags.append(f"ss={'PASS' if outcome.single_shot.solved else 'fail'}")
        if "agent" in arms:
            flags.append(f"agent={'PASS' if outcome.agent_loop.solved else 'fail'}")
        print(" ".join(flags))
        outcomes.append(outcome)

    summary = print_report(outcomes, arms)

    out_path = Path(args.out) if args.out else Path(".awos") / (
        f"bench_executors_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"  Saved {out_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

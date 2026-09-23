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
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scaffold"))

# Credentials and AWOS_AGENT_MODEL usually live in .env, which awos.py loads on
# its own. Without this, a key set there is invisible here and the run would
# fall back to a different model than the one configured — and price itself
# against that wrong model in the preflight.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

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
    react: ArmResult = field(default_factory=ArmResult)


#: (--arms name, CaseOutcome field, report label)
ARMS = (
    ("single", "single_shot", "single-shot"),
    ("agent", "agent_loop", "agent-loop"),
    ("react", "react", "react"),
)


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
            client = client_factory(case, str(project))
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


def run_react(case: BugCase, model: str) -> ArmResult:
    """
    ReActWorker — the executor Orchestrator uses by default (AWOS_REACT_WORKER=1).
    Same staged project, same prompt, same model as the agent loop; it differs
    in driving tools through JSON-in-text rather than native tool calls.
    """
    from agent.agent_loop import _price_for
    from agent.escalation_engine import EscalationLevel, ModelSpec
    from agent.react_worker import ReActWorker

    price_in, price_out = _price_for(model) or (0.0, 0.0)
    spec = ModelSpec(
        level=EscalationLevel.OPENROUTER, name=model, model_id=model,
        provider="openrouter", cost_per_req=0.0,
        input_price=price_in, output_price=price_out,
        min_complexity=0, min_budget_remaining=0.0, min_failures=0,
    )

    result = ArmResult()
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        project = stage_case(case, tmp)
        try:
            outcome = ReActWorker().execute_task(
                task={"task_id": 1, "action": _prompt_for(case), "file": case.target_file},
                file_content=case.buggy_source,
                codebase_context={"modules": "benchmark"},
                project_root=str(project),
                model_spec=spec,
            )
        except Exception as exc:
            result.detail = f"{type(exc).__name__}: {exc}"[:160]
            result.latency_sec = round(time.monotonic() - started, 2)
            return result

        steps = outcome.get("steps") or []
        # ReActWorker reports usage as top-level keys, not under "usage".
        usage = outcome
        result.latency_sec = round(time.monotonic() - started, 2)
        result.llm_calls = len(steps)
        result.tool_calls = sum(1 for st in steps if getattr(st, "action", "") not in ("finish", "parse_error"))
        result.input_tokens = int(usage.get("input_tokens", 0) or 0)
        result.output_tokens = int(usage.get("output_tokens", 0) or 0)
        result.cost_usd = float(usage.get("cost_usd", 0.0) or 0.0)

        # Score the file ReAct actually left behind, not what it claimed.
        edited = (project / "buggy.py").read_text(encoding="utf-8")
        passed, output = run_case_tests(case, source=edited)
        result.solved = passed
        if not passed:
            reason = outcome.get("error") or "finished"
            result.detail = f"{reason}: {_last_failure_line(output)}"[:160]
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


# ── Cost preflight ────────────────────────────────────────────────────────────

# Used only when there is nothing measured to go on. A run explores before it
# edits, so input grows as the transcript accumulates; these are deliberately
# not the 3-turn figures a scripted stub produces.
ASSUMED_TURNS_PER_CASE = 8
ASSUMED_INPUT_PER_TURN = 6_000
ASSUMED_OUTPUT_PER_TURN = 350

# One call, one file's text in, one SEARCH/REPLACE block out.
ASSUMED_SINGLE_SHOT_INPUT = 3_000
ASSUMED_SINGLE_SHOT_OUTPUT = 800

# Below this, asking for confirmation is just noise.
CONFIRM_THRESHOLD_USD = 0.05


@dataclass
class CostForecast:
    """What a run is expected to cost, and where that expectation came from."""

    basis: str
    model: str
    cases: int
    arms: set
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_usd: float = 0.0
    worst_case_usd: Optional[float] = None
    free: bool = False


def _measured_tokens(cassette_dir: str, case_names: list[str]) -> Optional[tuple[int, int]]:
    """
    Mean tokens per case from existing cassettes.

    A re-record follows roughly the path already recorded, so this beats any
    assumption. Returns None when too few cassettes exist to mean anything.
    """
    from agent.cassette import Cassette

    totals: list[tuple[int, int]] = []
    for name in case_names:
        path = Path(cassette_dir) / f"{name}.json"
        if path.exists():
            try:
                totals.append(Cassette.load(path).total_tokens)
            except Exception:
                continue
    if len(totals) < max(2, len(case_names) // 4):
        return None
    return (
        sum(t[0] for t in totals) // len(totals),
        sum(t[1] for t in totals) // len(totals),
    )


def forecast_cost(
    case_names: list[str],
    arms: set,
    model: str,
    cassette_dir: Optional[str],
    record: bool,
    max_cost: Optional[float],
) -> CostForecast:
    """Estimate the spend of a run before it starts."""
    from agent.agent_loop import estimate_cost

    forecast = CostForecast(
        basis="", model=model, cases=len(case_names), arms=set(arms)
    )

    # Only the agent arm is cassetted: replaying makes it free, while single
    # and react still call the model live.
    if cassette_dir and not record:
        arms = set(arms) - {"agent"}
        if not arms:
            forecast.free = True
            forecast.basis = "replaying cassettes — no API calls"
            return forecast

    per_case_in = per_case_out = 0
    if "agent" in arms:
        measured = _measured_tokens(cassette_dir, case_names) if cassette_dir else None
        if measured:
            per_case_in, per_case_out = measured
            forecast.basis = "measured from existing cassettes"
        else:
            per_case_in = ASSUMED_TURNS_PER_CASE * ASSUMED_INPUT_PER_TURN
            per_case_out = ASSUMED_TURNS_PER_CASE * ASSUMED_OUTPUT_PER_TURN
            forecast.basis = (
                f"assumed {ASSUMED_TURNS_PER_CASE} turns/case — no cassettes to measure"
            )
    if "react" in arms:
        per_case_in += ASSUMED_TURNS_PER_CASE * ASSUMED_INPUT_PER_TURN
        per_case_out += ASSUMED_TURNS_PER_CASE * ASSUMED_OUTPUT_PER_TURN
        if not forecast.basis:
            forecast.basis = f"assumed {ASSUMED_TURNS_PER_CASE} turns/case"
    if "single" in arms:
        per_case_in += ASSUMED_SINGLE_SHOT_INPUT
        per_case_out += ASSUMED_SINGLE_SHOT_OUTPUT
        if not forecast.basis:
            forecast.basis = "assumed one call per case"

    forecast.input_tokens = per_case_in * len(case_names)
    forecast.output_tokens = per_case_out * len(case_names)
    forecast.estimated_usd = estimate_cost(
        model, forecast.input_tokens, forecast.output_tokens
    )
    if max_cost is not None:
        # --max-cost bounds each case, so this is the real ceiling — the number
        # that matters more than the estimate.
        forecast.worst_case_usd = max_cost * len(case_names) * len(arms)
    return forecast


def print_forecast(forecast: CostForecast) -> None:
    print(f"\n{'─' * 58}")
    print("  Cost preflight")
    print(f"{'─' * 58}")
    print(f"  cases            {forecast.cases}")
    print(f"  arms             {', '.join(sorted(forecast.arms))}")
    print(f"  model            {forecast.model}")

    if forecast.free:
        print(f"  cost             $0.00 — {forecast.basis}")
        print(f"{'─' * 58}\n")
        return

    print(f"  tokens (est.)    {forecast.input_tokens:,} in / {forecast.output_tokens:,} out")
    print(f"  basis            {forecast.basis}")
    if forecast.estimated_usd == 0.0:
        print(f"  estimate         unknown — {forecast.model} is not in the price table")
    else:
        print(f"  estimate         ${forecast.estimated_usd:.2f}")
    if forecast.worst_case_usd is not None:
        print(f"  hard ceiling     ${forecast.worst_case_usd:.2f}  (--max-cost per case)")
    else:
        print("  hard ceiling     none — pass --max-cost to bound each case")
    print(f"{'─' * 58}\n")


def confirm_spend(forecast: CostForecast, assume_yes: bool) -> bool:
    """Ask before spending. Returns False when the run should not proceed."""
    if forecast.free:
        return True
    if assume_yes:
        return True

    # An unpriced model reads as $0.00, which is not the same as free; treat it
    # as needing confirmation rather than waving it through.
    trivial = 0.0 < forecast.estimated_usd < CONFIRM_THRESHOLD_USD
    if trivial:
        return True

    if not sys.stdin.isatty():
        print(
            "Refusing to spend without confirmation in a non-interactive shell. "
            "Re-run with --yes once the estimate above looks right."
        )
        return False

    answer = input("Proceed? [y/N] ").strip().lower()
    return answer in ("y", "yes")


# ── Clients ───────────────────────────────────────────────────────────────────


def make_client_factory(cassette_dir: Optional[str], record: bool, model: Optional[str]):
    """
    Build the per-case client: a real model when recording, a cassette when not.

    Each case gets its own cassette, named after it, so one case can be
    re-recorded without disturbing the rest.
    """
    from agent.cassette import wrap_for_cassette

    def factory(case: BugCase, project_root: str):
        if not cassette_dir:
            from agent.agent_loop import build_client_from_env

            return build_client_from_env(model)

        path = Path(cassette_dir) / f"{case.name}.json"
        # Each case is staged in a fresh temp directory, so without normalising
        # the root out of the key every turn after the first would miss and
        # replay would silently degrade to blind ordinal playback.
        if record:
            from agent.agent_loop import build_client_from_env

            return wrap_for_cassette(
                build_client_from_env(model),
                path=str(path),
                mode="record",
                root=project_root,
            )
        return wrap_for_cassette(
            None, path=str(path), mode="replay", root=project_root
        )

    return factory


# ── Reporting ─────────────────────────────────────────────────────────────────


def print_report(outcomes: list[CaseOutcome], arms: set[str]) -> dict[str, Any]:
    total = len(outcomes)
    print(f"\n{'=' * 78}")
    shown = [(name, key, label) for name, key, label in ARMS if name in arms]
    print(f"  {'case':<30} {'tier':<6} " + "".join(f"{label:<14}" for _, _, label in shown))
    print(f"  {'-' * 74}")
    for o in outcomes:
        cells = []
        for name, key, _ in shown:
            r = getattr(o, key)
            calls = f"{r.llm_calls:>3} call" if name == "single" else f"{r.llm_calls:>3}t/{r.tool_calls}c"
            cells.append(f"{'PASS' if r.solved else 'fail':<5}{calls}")
        marker = "*" if o.multi_file else " "
        print(f"  {o.case:<30}{marker}{o.tier:<5} " + "".join(f"{c:<14}" for c in cells))

    summary: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_cases": total,
        "cases": [asdict(o) for o in outcomes],
    }

    print(f"  {'-' * 74}")
    for arm_key, key, label in ARMS:
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

    if {"single", "agent"} <= arms and total:
        delta = summary["agent_loop"]["pass_rate"] - summary["single_shot"]["pass_rate"]
        summary["delta_pass_rate"] = round(delta, 3)
        print(f"  {'-' * 74}")
        print(f"  {'delta':<14} {delta:+.0%} (agent-loop minus single-shot)")
    if {"react", "agent"} <= arms and total:
        delta = summary["agent_loop"]["pass_rate"] - summary["react"]["pass_rate"]
        summary["delta_agent_vs_react"] = round(delta, 3)
        print(f"  {'delta':<14} {delta:+.0%} (agent-loop minus react)")
    print(f"{'=' * 78}")
    print("  * = multi-file case: the answer lives in a file single-shot never sees\n")
    return summary


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", default="tests/bug_cases")
    parser.add_argument("--case", action="append", help="Run only these cases (repeatable)")
    parser.add_argument("--arms", default="agent",
                        help="Comma-separated executors: agent, single, react; "
                             "or 'both' (single+agent) / 'all' (default: agent)")
    parser.add_argument("--cassette-dir", default=None,
                        help="Replay per-case cassettes from here (free, no API key)")
    parser.add_argument("--record", action="store_true",
                        help="Call the real model and write cassettes")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--max-cost", type=float, default=None,
                        help="Abort a single case once it has cost this much (USD)")
    parser.add_argument("--out", default=None, help="Write the summary JSON here")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the spend confirmation (for CI and scripted runs)")
    parser.add_argument("--no-preflight", action="store_true",
                        help="Do not print or confirm the cost forecast")
    args = parser.parse_args()

    case_dirs = discover_cases(args.cases_dir)
    if args.case:
        wanted = set(args.case)
        case_dirs = [d for d in case_dirs if d.name in wanted]
    if not case_dirs:
        print(f"No cases found in {args.cases_dir}")
        return 1

    arms = {"both": {"agent", "single"}, "all": {name for name, _, _ in ARMS}}.get(
        args.arms, {a.strip() for a in args.arms.split(",") if a.strip()}
    )
    unknown = arms - {name for name, _, _ in ARMS}
    if unknown:
        print(f"Unknown arm(s): {', '.join(sorted(unknown))}")
        return 2
    model = args.model or os.getenv("AWOS_AGENT_MODEL", "claude-sonnet-4-6")
    if "react" in arms and args.cassette_dir and not args.record:
        print("Note: the react arm has no cassettes; it calls the model live.")

    # Forecast and confirm before constructing a client or spending anything.
    forecast = forecast_cost(
        [d.name for d in case_dirs],
        arms,
        model,
        args.cassette_dir,
        args.record,
        args.max_cost,
    )
    if not args.no_preflight:
        print_forecast(forecast)
        if not confirm_spend(forecast, args.yes):
            return 1

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
        if "react" in arms:
            outcome.react = run_react(case, model)

        flags = []
        if "single" in arms:
            flags.append(f"ss={'PASS' if outcome.single_shot.solved else 'fail'}")
        if "agent" in arms:
            flags.append(f"agent={'PASS' if outcome.agent_loop.solved else 'fail'}")
        if "react" in arms:
            flags.append(f"react={'PASS' if outcome.react.solved else 'fail'}")
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

#!/usr/bin/env python3
"""Run the provider-free evaluation_v1 smoke harness.

This CLI validates the evaluation machinery itself. ``--mode demo-correct`` and
``--mode demo-wrong`` are deterministic fixtures, not measurements of the live
coding agent. Live-agent adapters must produce the same AgentCallback contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scaffold.agent.evaluation_v1 import (  # noqa: E402
    EvaluationFixture,
    EvaluationReport,
    EvaluationRunner,
    aggregate_trials,
    make_orchestrator_agent,
)


def _load_fixture(path: Path) -> EvaluationFixture:
    data = json.loads(path.read_text(encoding="utf-8"))
    repository = (path.parent / data["repository_path"]).resolve()
    data["repository_path"] = str(repository)
    return EvaluationFixture.from_dict(data)


def _demo_agent(mode: str):
    def agent(fixture: EvaluationFixture, workspace: Path, emit) -> Dict[str, Any]:
        emit("plan_created", {"task_id": fixture.task_id, "mode": mode})
        target = workspace / "calculator.py"
        if mode == "demo-correct":
            target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            emit("file_edit", {"path": "calculator.py", "success": True})
            return {"success": True, "cost_usd": 0.0}
        emit("file_edit", {"path": "calculator.py", "success": False})
        return {"success": False, "cost_usd": 0.0}

    return agent


def main() -> int:
    parser = argparse.ArgumentParser(description="AWOS evaluation_v1 offline harness")
    parser.add_argument("--fixture", type=Path, default=REPO / "tests/fixtures/evaluation_v1/basic_edit/fixture.json")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument(
        "--mode",
        choices=["demo-correct", "demo-wrong", "live"],
        default="demo-correct",
        help="demo modes validate the harness; live invokes the real orchestrator and requires provider setup",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--replay", type=Path, default=None, help="Load and validate an existing report")
    args = parser.parse_args()

    if args.replay:
        report = EvaluationReport.from_json(args.replay.read_text(encoding="utf-8"))
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.trials <= 0:
        parser.error("--trials must be positive")
    fixture = _load_fixture(args.fixture)
    runner = EvaluationRunner()
    if args.mode == "live":
        from scaffold.agent.orchestrator import Orchestrator

        agent = make_orchestrator_agent(Orchestrator)
    else:
        agent = _demo_agent(args.mode)
    trials = [
        runner.run_trial(fixture, agent, trial_id=f"trial_{index + 1}")
        for index in range(args.trials)
    ]
    report = aggregate_trials(trials)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report.to_json() + "\n", encoding="utf-8")
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    print(f"RESULT success={report.pass_at_1 == 1.0} pass_at_1={report.pass_at_1:.3f} trials={report.trial_count}")
    return 0 if report.pass_at_1 == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

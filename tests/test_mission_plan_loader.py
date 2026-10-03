"""Mission plan_file loader for pre-planned long runs."""
import json
from pathlib import Path

import pytest

from awos import load_mission_plan


def test_load_mission_plan_from_plan_file():
    cfg = {"plan_file": "stage1_long_workload.plan.json"}
    plan = load_mission_plan(cfg)
    assert plan is not None
    assert len(plan) == 8
    assert plan[0]["task_id"] == 1
    assert plan[0].get("path") or plan[0].get("file")


def test_load_mission_plan_inline():
    cfg = {
        "plan": [
            {
                "task_id": 1,
                "task_type": "edit_file",
                "path": "README.md",
                "action": "noop",
                "complexity": "low",
            }
        ]
    }
    plan = load_mission_plan(cfg)
    assert plan is not None
    assert len(plan) == 1


def test_load_mission_plan_missing_returns_none():
    assert load_mission_plan({}) is None


def test_long_mission_config_has_plan_file():
    path = Path(__file__).resolve().parent.parent / "docs" / "missions" / "stage1_long_workload.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    plan = load_mission_plan(cfg)
    assert plan is not None
    assert len(plan) >= 8


def test_clawcode_mission_config_has_plan_and_root():
    path = Path(__file__).resolve().parent.parent / "docs" / "missions" / "stage1_phase_d_clawcode.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    assert cfg.get("codebase_root")
    assert Path(cfg["codebase_root"]).is_dir()
    plan = load_mission_plan(cfg)
    assert plan is not None
    assert len(plan) == 14
    assert plan[0]["path"].startswith("src/")


def test_ci_rescue_mission_config_has_plan_and_root():
    from awos import _mission_codebase_root, _load_mission_config

    cfg = _load_mission_config(ci_rescue=True)
    root = _mission_codebase_root(cfg)
    assert Path(root).is_dir()
    assert (Path(root) / "wedge_goal.json").is_file()
    plan = load_mission_plan(cfg)
    assert plan is not None
    assert len(plan) == 18
    assert cfg.get("ensure_git") is True
    assert cfg.get("goal_assertions")

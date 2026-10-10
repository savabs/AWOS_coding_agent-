"""
A LinUCB router trained on an older, longer ladder can pick a rung that no
longer exists. That used to raise IndexError inside decide() and crash every
orchestrator task.
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.escalation_engine import LADDER, EscalationEngine


def test_stale_router_action_falls_back_to_an_allowed_rung():
    router = MagicMock()
    router.is_ready.return_value = True
    router.select.return_value = len(LADDER) + 3  # a rung from a longer ladder

    engine = EscalationEngine(monthly_budget=20.0, ml_router=router)
    engine._feature_extractor = MagicMock()
    engine._feature_extractor.extract.return_value = [0.0]

    decision = engine.decide({"task_id": 1, "action": "fix a bug", "complexity": "low"})

    assert decision.spec in LADDER

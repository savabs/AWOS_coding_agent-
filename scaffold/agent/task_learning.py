"""
task_learning.py — Shared helpers for ML/RL learning loops.

Maps planner task_type and model names into bandit actions and performance types.
"""

from __future__ import annotations

try:
    from scaffold.agent.escalation_engine import EscalationLevel
except ImportError:
    from escalation_engine import EscalationLevel


def model_name_to_action_id(model_name: str) -> int:
    """Map API model name → LinUCB / EscalationLevel action id (0–4)."""
    m = (model_name or "").lower()
    if "gemini" in m or "flash" in m:
        return 0
    if "deepseek" in m:
        return 1
    if "gpt" in m or "openai" in m or "4o" in m or "mini" in m:
        return 2
    if "haiku" in m:
        return 3
    if "sonnet" in m or "claude" in m:
        return 4
    return 0


def model_name_to_escalation_level(model_name: str) -> EscalationLevel:
    return EscalationLevel(model_name_to_action_id(model_name))

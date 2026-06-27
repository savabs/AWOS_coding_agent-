"""
learning_policy.py — Default enablement for kernel learning loops on awos run.

Prompt evolution (M1) compounds from verified session outcomes in .awos/.
Opt-out: AWOS_PROMPT_EVOLUTION_DISABLE=true or AWOS_LEARNING_DISABLE=true
Explicit on: AWOS_PROMPT_EVOLUTION=true (legacy)
"""

from __future__ import annotations

import os


def learning_enabled() -> bool:
    if os.getenv("AWOS_LEARNING_DISABLE", "").lower() in ("1", "true", "yes"):
        return False
    if os.getenv("AWOS_PROMPT_EVOLUTION_DISABLE", "").lower() in ("1", "true", "yes"):
        return False
    return True


def apply_kernel_defaults() -> None:
    """Enables runtime sessions, learning, and MCTS unless opted out."""
    os.environ.setdefault("AWOS_RUNTIME_SESSION", "true")
    os.environ.setdefault("AWOS_LEARNING_DEFAULT", "true")
    os.environ.setdefault("AWOS_MCTS_DEFAULT", "true")


def prompt_evolution_enabled() -> bool:
    if not learning_enabled():
        return False
    if os.getenv("AWOS_PROMPT_EVOLUTION", "").lower() in ("1", "true", "yes"):
        return True
    return os.getenv("AWOS_LEARNING_DEFAULT", "true").lower() in ("1", "true", "yes")


"""
route_policy.py — which AWOS_LOOP_ROUTE rungs a task may use, and which
model gets the routing credit afterwards.

loop_route.py builds the rung clients; this module decides, before a run,
which rungs are allowed (typed skip reasons go to the route log), and after
a run, which escalation arm the reward store / LinUCB should credit.

Rung specs are the loop_route forms: "local", "local/<name>", "claude-*",
anything else goes through agent_loop.build_client_from_env(spec).

Provider table (PROVIDERS): spec prefix -> provider, remote, privacy class,
premium. An unknown spec is treated as remote cloud (fail closed).

Gates (filter_ladder):
  forced_model             the escalation decision was forced to one model:
                           the ladder is skipped as a whole
  local_only               AWOS_ROUTE_LOCAL_ONLY=1, or the host job's privacy
                           is local_only (AWOS_JOB_PRIVACY): remote rungs are
                           unavailable
  cheap_only               AWOS_CHEAP_ONLY / AWOS_PREMIUM_BUDGET=0: premium
                           rungs are unavailable
  premium_budget_exhausted the premium budget left is 0: premium rungs are
                           unavailable
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Iterable, Optional

try:
    from .loop_route import is_local_spec
except ImportError:  # flat import (scaffold/agent on sys.path)
    from loop_route import is_local_spec  # type: ignore

# Typed skip reasons (route log "reason" field).
FORCED_MODEL = "forced_model"
LOCAL_ONLY = "local_only"
CHEAP_ONLY = "cheap_only"
PREMIUM_BUDGET = "premium_budget_exhausted"

PRIVACY_LOCAL = "local"     # never leaves the machine
PRIVACY_CLOUD = "cloud"     # sent to a third-party provider

_ON = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RungInfo:
    spec: str
    provider: str
    remote: bool
    privacy: str
    premium: bool

    def to_dict(self) -> dict:
        return asdict(self)


#: (prefix, provider, remote, privacy class, premium). First match wins;
#: matched case-insensitively against the spec. "local" is handled by
#: loop_route.is_local_spec so the two modules agree on what local means.
PROVIDERS: tuple = (
    ("claude-", "anthropic", True, PRIVACY_CLOUD, True),
    ("anthropic/", "openrouter", True, PRIVACY_CLOUD, True),
    ("openai/", "openrouter", True, PRIVACY_CLOUD, True),
    ("gpt-", "openai", True, PRIVACY_CLOUD, True),
    ("deepseek", "opencode", True, PRIVACY_CLOUD, False),
    ("qwen", "openrouter", True, PRIVACY_CLOUD, False),
    ("gemini", "google", True, PRIVACY_CLOUD, False),
)


def classify(spec: str) -> RungInfo:
    """Provider table lookup for one rung spec; unknown specs are remote cloud."""
    s = (spec or "").strip()
    if is_local_spec(s):
        return RungInfo(s, "local", False, PRIVACY_LOCAL, False)
    low = s.lower()
    for prefix, provider, remote, privacy, premium in PROVIDERS:
        if low.startswith(prefix):
            return RungInfo(s, provider, remote, privacy, premium)
    return RungInfo(s, "unknown", True, PRIVACY_CLOUD, False)


def local_only_enabled() -> bool:
    """AWOS_ROUTE_LOCAL_ONLY=1, or a host job whose privacy is local_only."""
    if os.getenv("AWOS_ROUTE_LOCAL_ONLY", "").strip().lower() in _ON:
        return True
    return os.getenv("AWOS_JOB_PRIVACY", "").strip().lower() == "local_only"


def filter_ladder(ladder: Iterable[str], *, forced: bool = False,
                  local_only: bool = False, cheap_only: bool = False,
                  premium_left: Optional[float] = None) -> tuple[list[str], list[dict]]:
    """
    (allowed rungs in order, skipped [{"skip": spec, "reason": <typed>,
    "rung": RungInfo dict}]). A forced decision skips every rung.
    """
    kept: list[str] = []
    skipped: list[dict] = []
    for spec in ladder:
        info = classify(spec)
        reason = None
        if forced:
            reason = FORCED_MODEL
        elif local_only and info.remote:
            reason = LOCAL_ONLY
        elif info.premium and cheap_only:
            reason = CHEAP_ONLY
        elif info.premium and premium_left is not None and premium_left <= 0:
            reason = PREMIUM_BUDGET
        if reason:
            skipped.append({"skip": spec, "reason": reason, "rung": info.to_dict()})
        else:
            kept.append(spec)
    return kept, skipped


def _bare(model_id: str) -> str:
    """'openrouter/deepseek/deepseek-v4-pro' -> 'deepseek-v4-pro'."""
    return (model_id or "").strip().lower().rsplit("/", 1)[-1]


def credit_target(final_model: str, ladder_spec=None) -> dict:
    """
    Which escalation spec to credit for a routed run that ended on
    `final_model`: {"action": "credit_rung", "spec": ModelSpec, "model": ...}
    when the model is on the escalation ladder, else {"action": "skip",
    "model": ..., "reason": "rung_not_on_escalation_ladder"}.
    `ladder_spec` defaults to escalation_engine.LADDER (injectable for tests).
    """
    if ladder_spec is None:
        try:
            from .escalation_engine import LADDER as ladder_spec
        except ImportError:  # flat import
            from escalation_engine import LADDER as ladder_spec  # type: ignore
    want = _bare(final_model)
    for spec in ladder_spec or []:
        if want and want in (_bare(getattr(spec, "model_id", "")), _bare(getattr(spec, "name", ""))):
            return {"action": "credit_rung", "spec": spec, "model": final_model}
    return {"action": "skip", "model": final_model, "reason": "rung_not_on_escalation_ladder"}

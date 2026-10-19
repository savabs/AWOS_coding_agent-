"""Held-out personas for the email-triage testbed.

The default mailbox (persona "sam", Sam Rivera) lives in ../seed.py and
../persona.json and is unchanged. Each held-out persona is a module here plus a
JSON file next to it (same shape as persona.json: "public" is copied into the
agent-visible profile.json, "rules" are the private preferences the simulated
user states in feedback). A persona module provides:

    ME              "Name <address>" (the To: of every message)
    load()          the persona JSON as a dict
    build(rng)      the full message pool (_common.m-shaped dicts), deterministic in rng
    PROTECT_KIND    kind of the key person's urgent asks; kept out of the
                    calibration fill so the hide_email target lands in evaluation
    hide_target(m)  predicate: which pool message the hide_email injection targets
    SCOPING_RULE    the opt-in scoping-case rule (junk newsletter of a service)
    scoping_case(rng)  3 newsletters (kind scoping_newsletter, rule SCOPING_RULE)
                    + 1 security alert from the same service (kind scoping_alert)

Selected with ``seed.py --persona <id>`` / ``run.py --persona <id>``. The texts
were written for these personas; they share no templates with seed.py.
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Dict

DEFAULT = "sam"
PERSONAS: Dict[str, str] = {
    "sam": "Sam Rivera, solo SaaS founder and contract developer (default; seed.py)",
    "clinic": "Teresa Okafor-Vance, office manager of a family medical practice (held out)",
    "photographer": "Noor Haddad, freelance wedding and brand photographer with a family (held out)",
    "bookshop": "Wren Halvorsen, independent bookshop + licensed cafe owner in Hobart (held out)",
}
HELD_OUT = tuple(k for k in PERSONAS if k != DEFAULT)


def get(name: str) -> ModuleType:
    if name not in HELD_OUT:
        raise ValueError(f"unknown held-out persona {name!r}; choose from {', '.join(HELD_OUT)}")
    return importlib.import_module(f"{__name__}.{name}")

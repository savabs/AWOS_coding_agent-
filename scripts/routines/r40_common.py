"""Shared pieces for the R40 routine-task builder (scripts/routines/build_r40.py).

A Task is one job-series: base/ snapshot + one job (task.json, hidden_tests/,
reference/). Templates use @@KEY@@ placeholders (see T) so embedded Python keeps
its own braces and backslashes untouched.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

PYTEST_INI = """[pytest]
pythonpath = . scripts
addopts = -p no:cacheprovider
"""


def D(text: str) -> str:
    """Dedent an embedded file and make sure it ends with one newline."""
    return textwrap.dedent(text).lstrip("\n").rstrip() + "\n"


def T(text: str, **kw) -> str:
    """D() then replace @@KEY@@ with str(value)."""
    out = D(text)
    for k, v in kw.items():
        out = out.replace(f"@@{k}@@", str(v))
    assert "@@" not in out, f"unfilled placeholder in template: {out[out.index('@@'):][:40]}"
    return out


def sub_once(text: str, old: str, new: str) -> str:
    """Replace exactly one occurrence (a reference edit that silently misses is a bug)."""
    n = text.count(old)
    assert n == 1, f"expected exactly one {old!r}, found {n}"
    return text.replace(old, new)


def repo_text(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


@dataclass
class Task:
    family: str               # family id, e.g. "f02_bump_version"
    slug: str                 # job slug (jobs/01_<slug>)
    goal: str                 # user request handed to the agent
    base: dict[str, str]      # path -> text, the start snapshot
    hidden: dict[str, str]    # tests/<name> -> text (copied into tests/ to judge)
    reference: dict[str, str]  # path -> text overlaid on base to solve
    check: str                # one line: what the hidden check verifies
    params: dict = field(default_factory=dict)   # the instance's held-out parameters
    instance: int | None = None   # 1..4 for family instances
    near_miss: bool = False
    near_miss_reason: str = ""    # why the family routine must NOT be applied
    sources: list[str] = field(default_factory=list)  # repo files the snapshot is taken from
    max_turns: int = 40
    max_cost_usd: float = 0.25
    timeout_min: int = 15

    @property
    def series(self) -> str:
        fam = self.family.split("_", 1)[0]          # "f02"
        tail = "nm" if self.near_miss else f"i{self.instance}"
        return f"r40_{fam}_{tail}_{self.slug}"

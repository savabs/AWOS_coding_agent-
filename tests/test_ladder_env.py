"""AWOS_LADDER puts chosen model ids on the escalation ladder (direct Anthropic, 2026-10-11)."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "scaffold", REPO / "scaffold" / "agent"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _reload(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("AWOS_LADDER", raising=False)
    else:
        monkeypatch.setenv("AWOS_LADDER", value)
    import escalation_engine
    return importlib.reload(escalation_engine)


def test_one_id_fills_every_rung(monkeypatch):
    ee = _reload(monkeypatch, "claude-haiku-5-5")
    try:
        assert [s.model_id for s in ee.LADDER] == ["claude-haiku-5-5"] * len(ee.LADDER)
        assert all(s.provider == "anthropic" for s in ee.LADDER)
        assert ee.LADDER[0].input_price == 0.10 and ee.LADDER[0].output_price == 0.50
        assert ee.LEVEL_MAP[ee.EscalationLevel.DEEPSEEK].model_id == "claude-haiku-5-5"
        assert ee.LEVEL_MAP[ee.EscalationLevel.OPENCODE].model_id == "claude-haiku-5-5"
    finally:
        _reload(monkeypatch, None)


def test_two_ids_default_and_escalation(monkeypatch):
    ee = _reload(monkeypatch, "claude-haiku-5-5, claude-sonnet-5-5")
    try:
        assert [s.model_id for s in ee.LADDER] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    finally:
        _reload(monkeypatch, None)


def test_unset_keeps_the_built_in_ladder(monkeypatch):
    ee = _reload(monkeypatch, None)
    assert ee.LADDER[0].model_id == "deepseek-v4-flash"

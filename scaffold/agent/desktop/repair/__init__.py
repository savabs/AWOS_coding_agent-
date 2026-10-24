"""
repair/ — when a replayed step's control is not found: re-find it, pick from a shortlist, or say it is gone.

    from scaffold.agent.desktop.repair import Repairer, SkillRepair, LocalPicker
    hook = SkillRepair(Repairer(picker=LocalPicker()))       # or Repairer() for rule-only
    LiveRunner(repair=hook)                                  # pack/runner.py, opt-in

Modules:
  similo.py  tier 1: multi-attribute fingerprint + weighted score + accept / ambiguous / abstain (no model)
  model.py   tier 2: a small local model picks 1 of <= 5 or answers NOT_FOUND (mock pickers for tests)
  core.py    Repairer (tiers + destructive / send gate) and SkillRepair (runner hook, records repairs)
  mutate.py  page mutations with known ground truth for scripts/repair_bench.py

Design and numbers: docs/specs/mw_repair.md; research: docs/research/rl_cua_2026-10-24/repair_verify.md.
"""
from .core import RepairResult, Repairer, SkillRepair, page_candidates, risk_of, target_fingerprint
from .model import LocalPicker, Pick, greedy_picker, noisy_picker, oracle_picker
from .similo import Fingerprint, decide, rank, score

__all__ = ["Fingerprint", "LocalPicker", "Pick", "RepairResult", "Repairer", "SkillRepair", "decide",
           "greedy_picker", "noisy_picker", "oracle_picker", "page_candidates", "rank", "risk_of", "score",
           "target_fingerprint"]

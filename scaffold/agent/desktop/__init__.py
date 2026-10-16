"""
desktop — computer-use foundations (T10). No LLM in this package.

  probes       getter + comparator end-state postconditions, V1 validity rule
  policy       provenance-keyed sink policy (allow / ask / deny) + audit log
  ax_compress  AX / DOM tree -> short indexed element list, button roles
  ax_observe   stable element IDs; diff / full / no_change observations (D16)
  sends        two-phase external sends: prepare -> approve -> commit once (D16)
  ui_session   turn-scoped lease + guarded UI actions on a backend (D16),
               typed outcomes, action-time checks, UI two-phase sends
  app_policy   app tiers (forbidden / read / click / full) keyed by bundle id
  stop         latched emergency stop: hardware hotkey, sentinel file, signal
  fake_backend in-memory backend for tests (no GUI)
  chores       load / stage / validate the C10 chore set

Spec: docs/specs/computer_use_foundations.md
"""

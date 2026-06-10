"""
AWOS kernel and app scaffold.

Architecture (see VISION.md):
  Kernel (domain-agnostic): reward, bandit routing, budget, self-learning loop
  App layer (domain-specific): Coding App = planner, worker, verifier

The orchestrator is the kernel loop. Apps plug in execute/verify layers.
"""

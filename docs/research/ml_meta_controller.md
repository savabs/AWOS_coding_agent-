# ML Meta-Controller for AWOS Self-Improvement

## Problem Statement

AWOS currently uses a hardcoded complexity-based escalation ladder.
The routing decision (which model tier to use for which task) never improves.
Every failure is wasted information.

## Why Not LLMs for This Layer

The meta-controller makes a decision: given task features, pick (model_tier).
This is a **classification/prediction problem**, not a generation problem.
LLMs are overengineered by 7 orders of magnitude for this.

The right tool: classical ML with online learning.

## Algorithm Selection

### LinUCB (Contextual Bandit) — PRIMARY

From: "A Contextual-Bandit Approach to Personalized News Article Recommendation"
Li et al., 2010. WWW.

Why LinUCB:
- Handles exploration vs exploitation with provable regret bounds
- Online: updates after every single task (no batch needed)
- Generalizes: learns a *linear function* over task features → works on unseen task types
- Interpretable: the weight vector θ_a tells you exactly what features drive model selection
- No GPU, no pretrained weights, no API. Pure numpy.

Math:
  For each action a, maintain (A_a, b_a):
    A_a ∈ R^{d×d}  — feature covariance accumulator (init: identity)
    b_a ∈ R^d      — reward-weighted feature accumulator (init: zeros)
  
  Select action:
    θ_a = A_a^{-1} @ b_a              ← learned weight vector
    UCB_a = θ_a @ x + α·√(x @ A_a^{-1} @ x)   ← value + exploration bonus
    a* = argmax_a UCB_a
  
  Update after reward r:
    A_a* += x @ x^T
    b_a* += r * x

  The UCB term is high when x is in a region we haven't explored → automatic exploration.

### Gaussian Process World Model — SECONDARY (Phase 2)

Predicts P(success | task_features, action) with calibrated uncertainty.
Enables Thompson Sampling: sample from posterior, act greedily on sample.
sklearn GaussianProcessRegressor. Trains on accumulated RewardStore data.

Advantage over LinUCB: captures non-linear relationships.
Disadvantage: O(n³) training, needs ~50+ samples to stabilize.

## Feature Vector (10-dimensional)

| Index | Feature | Encoding |
|-------|---------|----------|
| 0 | action_length | token count / 100, clipped [0,1] |
| 1 | is_bug_fix | 1 if "fix/bug/error" in action |
| 2 | is_refactor | 1 if "refactor/clean/rename" in action |
| 3 | is_new_feature | 1 if "add/create/new/implement" in action |
| 4 | is_architecture | 1 if "architecture/design/system" in action |
| 5 | has_tests | 1 if "test" in action or file path |
| 6 | complexity_encoded | low=0.2, medium=0.5, high=0.8 |
| 7 | file_is_core | 1 if file not in test/docs |
| 8 | failure_count_norm | failure_count / 3, clipped [0,1] |
| 9 | bias | always 1.0 |

## Action Space (5 actions)

| ID | Action | Cost proxy |
|----|--------|------------|
| 0 | GEMINI_FLASH | $0.001/req |
| 1 | DEEPSEEK | $0.001/req |
| 2 | HAIKU | $0.017/req |
| 3 | SONNET | $0.050/req |
| 4 | OPUS | $0.960/req |

## Reward Function

r = success * 1.0 - cost_tier_fraction * 0.05

Where cost_tier_fraction = [0.0, 0.0, 0.02, 0.05, 1.0] for tiers 0-4.
This incentivizes solving tasks cheaply. Failure is always penalized (-0 if we're lenient,
or use r = 0 for fail which naturally trains toward success).

## Persistence

Weights stored in .awos/linucb_weights.pkl (numpy arrays).
Loaded on init, saved after every update.
RewardStore at .awos/reward_store.jsonl (append-only, human-readable).

## Bootstrap Strategy

Until LinUCB has seen ≥ 20 outcomes, defer to existing EscalationEngine heuristics.
This prevents the cold-start problem from degrading performance.
After 20 outcomes: LinUCB takes over, heuristics used only as tiebreaker.

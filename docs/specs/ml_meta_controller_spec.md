# ML Meta-Controller Spec

## New Files

### scaffold/agent/reward_store.py
Class: RewardStore
- store(task, action_id, success, cost, latency_ms) → None
- get_recent(n=200) → list[Episode]
- total_episodes() → int
- Persists to .awos/reward_store.jsonl (append-only)

Episode schema:
  {task_id, action_id, features[10], success, cost, latency_ms, reward, timestamp}

### scaffold/agent/ml_router.py
Classes:
  TaskFeatureExtractor
    - extract(task: dict, failure_count: int) → np.ndarray[10]

  LinUCBRouter
    - __init__(n_features=10, n_actions=5, alpha=1.0, min_samples=20)
    - select(features) → action_id (int 0-4)
    - update(features, action_id, reward) → None
    - save(path) / load(path) → None
    - is_ready() → bool  (has seen >= min_samples)
    - summary() → dict  (weights per action, human-readable)

  GPWorldModel  [Phase 2 stub only in Phase 1]
    - predict(features, action_id) → (mean: float, std: float)
    - update(episodes: list[Episode]) → None
    - sample_action(features) → action_id  [Thompson sampling]

## Modified Files

### scaffold/agent/escalation_engine.py
Change: EscalationEngine.__init__ optionally accepts ml_router=None
Change: EscalationEngine.decide() prepends LinUCB check:

  if self.ml_router and self.ml_router.is_ready():
      features = extractor.extract(task, failure_count)
      action_id = self.ml_router.select(features)
      # map action_id → ModelSpec
      # return EscalationDecision with reason="LinUCB"

Change: EscalationEngine.record_outcome() also calls ml_router.update()

## Interface Contracts (unchanged)
- EscalationDecision dataclass: unchanged
- ModelSpec dataclass: unchanged
- Worker.execute_task() signature: unchanged
- Orchestrator contract: unchanged

## Acceptance Criteria
1. LinUCBRouter.select() returns valid action_id 0-4 for any feature vector
2. LinUCBRouter.update() modifies weights (verify A matrix changes)
3. RewardStore persists across process restarts
4. EscalationEngine works identically when ml_router=None (pure heuristic mode)
5. After 20 updates, is_ready() returns True and select() is used
6. Weights saved to .awos/linucb_weights.pkl and reloaded correctly

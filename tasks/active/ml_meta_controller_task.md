# Task: ML Meta-Controller

## Step 1 — RewardStore (new file, ~120 lines)
File: scaffold/agent/reward_store.py
Test: instantiate, store 3 episodes, reload from disk, verify count=3

## Step 2 — TaskFeatureExtractor (new file, part of ml_router.py)
Test: extract({"action": "fix bug in parser", "complexity": "low"}, 0)
      → np.ndarray shape (10,), all values in [0,1]

## Step 3 — LinUCBRouter (same file ml_router.py)
Test: 5 updates with random rewards → weights change → is_ready() True after 20

## Step 4 — GPWorldModel stub (same file, Phase 2 placeholder)

## Step 5 — Wire into EscalationEngine (edit existing file)
Test: EscalationEngine(ml_router=None) behaves identically to current

## Step 6 — Integration test
Test: 25 episodes → LinUCB takes over from heuristic → record_outcome updates weights

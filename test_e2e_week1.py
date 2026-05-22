#!/usr/bin/env python3
"""End-to-end test from scaffold/agent directory where imports resolve."""
import sys, os

sys.path.insert(0, '/home/becmachlean/2024/projects/AWOS_coding_agent/scaffold/agent')
os.environ.setdefault('AWOS_MONTHLY_BUDGET', '20.0')
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

from memory.vector_memory import VectorMemory
from memory.codebase_index import CodebaseIndex
from budget_ledger import BudgetLedger, get_ledger

print("=" * 60)
print("END-TO-END TEST: Week 1 Integration")
print("=" * 60)

# Reset test state
import shutil
for d in ['/tmp/awos_e2e_memory', '/tmp/awos_e2e_budget.json', '/tmp/awos_e2e_traces']:
    if os.path.exists(d):
        shutil.rmtree(d) if os.path.isdir(d) else os.remove(d)

# Initialize components
vm = VectorMemory(persist_dir='/tmp/awos_e2e_memory')
bi = CodebaseIndex(project_root='/home/becmachlean/2024/projects/AWOS_coding_agent/scaffold/agent', vector_memory=vm)
bl = BudgetLedger(ledger_path='/tmp/awos_e2e_budget.json')

print("\n1. Components initialized")
print(f"   VectorMemory:  {vm.stats()['total_interactions']} interactions")
print(f"   CodebaseIndex: {bi.stats()['chunks']} chunks")
print(f"   BudgetLedger:  {bl.get_status()['requests']} requests")

# Seed: store some past interactions
print("\n2. Seeding memory with 3 interactions...")
interactions = [
    ("How do I add a new tool to the registry?",
     "Import your tool class, instantiate it, then call registry.register(YourTool()).",
     {"handler": "coding", "cost": 0.002}),
    ("What's the best way to handle API rate limits?",
     "Use exponential backoff with jitter. Start at 1s, double each retry, cap at 60s.",
     {"handler": "reasoning", "cost": 0.001}),
    ("How does the budget tracker calculate monthly spending?",
     "TokenTracker records per-request. BudgetLedger persists month-to-date totals to JSON.",
     {"handler": "reasoning", "cost": 0.001}),
]

for q, a, meta in interactions:
    vm.store(f"Q: {q}\nA: {a}", {**meta, "session_id": "test-seed"})

print(f"   Stored {len(interactions)} interactions")

# Test 1: Similar query should recall the budget answer
print("\n3. Test recall: 'tell me about budget tracking'...")
results = vm.retrieve("tell me about budget tracking", n_results=2)
print(f"   Retrieved {len(results)} results")
for i, r in enumerate(results, 1):
    text = r['text'].split('\n')[0]  # First line (the question)
    print(f"   #{i} dist={r['distance']:.3f} | {text[:70]}")

# Verify the budget-related answer was found
found_budget = any('BudgetLedger' in r['text'] or 'monthly' in r['text'] for r in results)
print(f"   Budget answer recalled: {'YES' if found_budget else 'NO'}")

# Test 2: Codebase semantic search
print("\n4. Test semantic code search: 'vector memory embeddings'...")
# Build index first
bi.index_project()
ctx = bi.get_context_for_query("vector memory store and retrieve embeddings", max_chunks=2)
print(f"   Context length: {len(ctx)} chars")
lines = [l for l in ctx.split('\n') if l.startswith('#')]
for l in lines[:3]:
    print(f"   {l}")

# Verify it found the right file
found_vector = 'vector_memory' in ctx.lower()
print(f"   Found vector_memory.py: {'YES' if found_vector else 'NO'}")

# Test 3: Budget recording
print("\n5. Test budget recording...")
bl.record('coding', 'DeepSeek V4 Flash', 500, 300, 0.00025)
bl.record('reasoning', 'Claude Haiku 4.5', 400, 200, 0.00120)
bl.record_cache_hit(0.002)

status = bl.get_status(monthly_budget=20.0)
print(f"   Requests: {status['requests']}")
print(f"   Spent:    ${status['spent']:.4f}")
print(f"   Cache:    {status['cache_hits']} hits, ${status['cache_savings']:.4f} saved")
print(f"   Avg:      ${status['avg_cost_per_request']:.6f}/req")

# Test 4: Budget guard
print("\n6. Test budget guardrail...")
ok, msg = bl.check_budget(estimated_cost=0.05, monthly_budget=20.0)
print(f"   $0.05 call allowed: {ok} ({msg or 'ok'})")

ok2, msg2 = bl.check_budget(estimated_cost=50.0, monthly_budget=20.0)
print(f"   $50 call allowed: {ok2} ({msg2 or 'ok'})")

# Test 5: Persistent budget survives "restart"
print("\n7. Test budget persistence (simulate restart)...")
bl2 = BudgetLedger(ledger_path='/tmp/awos_e2e_budget.json')
status2 = bl2.get_status(monthly_budget=20.0)
print(f"   After restart: {status2['requests']} requests, ${status2['spent']:.4f}")
persisted = status2['requests'] == status['requests']
print(f"   Persistence works: {'YES' if persisted else 'NO'}")

# Summary
print("\n" + "=" * 60)
print("RESULTS:")
print("=" * 60)
checks = [
    ("Memory seeding", True),
    ("Budget answer recalled", found_budget),
    ("Codebase search found vector_memory", found_vector),
    ("Budget recording", status['requests'] == 2),  # cache hits != requests
    ("Budget guardrail ($0.05)", ok),
    ("Budget guardrail ($50 blocked)", not ok2),
    ("Budget persistence", persisted),
]

passed = sum(1 for _, ok in checks if ok)
for name, ok in checks:
    status_str = "PASS" if ok else "FAIL"
    print(f"  [{status_str}] {name}")

print(f"\n  {passed}/{len(checks)} checks passed")

if passed == len(checks):
    print("\n  ALL TESTS PASSED")
else:
    print(f"\n  {len(checks) - passed} FAILURE(S)")

#!/usr/bin/env python3
"""End-to-end test: simulate chat flow through UnifiedAgent with real APIs."""
import sys, os, json

sys.path.insert(0, 'scaffold')
os.environ.setdefault('AWOS_MONTHLY_BUDGET', '20.0')
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

from agent.unified_agent import UnifiedAgent

print("=" * 60)
print("END-TO-END TEST: AWOS Chat Flow")
print("=" * 60)

agent = UnifiedAgent()
print(f"\n1. Agent initialized")
print(f"   Session: {agent.current_session_id}")
print(f"   Budget:  ${agent.budget_ledger.get_status()['spent']:.4f} spent")
print(f"   Memory:  {agent.vector_memory.stats()['total_interactions']} interactions")
print(f"   Codebase: {agent.codebase_index.stats()['chunks']} chunks ready")

# Query 1: Something the agent should be able to answer from context
print("\n2. First query (seed memory)...")
try:
    q1 = "How does the budget tracking system work in AWOS?"
    print(f"   Q: {q1}")

    # Manually simulate what handle_request does, but without full routing
    ctx = agent._read_relevant_context(q1, max_files=2, max_lines=80)
    print(f"   Context retrieved: {len(ctx)} chars")
    print(f"   Top file: {ctx.split(chr(10))[0] if ctx else 'none'}")

    # Store this interaction
    resp1 = "The budget system uses TokenTracker for session tracking and BudgetLedger for persistent month-to-date tracking."
    agent.vector_memory.store(
        text=f"Q: {q1}\nA: {resp1}",
        metadata={"session_id": agent.current_session_id, "handler": "reasoning", "cost": 0.0001}
    )
    print(f"   Stored in memory")

except Exception as e:
    print(f"   ERROR: {e}")
    import traceback; traceback.print_exc()

# Query 2: Similar query — should recall from memory
print("\n3. Second query (test recall)...")
try:
    q2 = "Tell me about how AWOS manages spending and costs"
    print(f"   Q: {q2}")

    # This should find the first query via semantic similarity
    past = agent._recall_past_solutions(q2, n_results=2)
    if past:
        print(f"   Recalled past solutions: YES")
        print(f"   Preview: {past[:200]}...")
    else:
        print(f"   Recalled past solutions: NO (empty)")

    # Context should be semantically relevant
    ctx2 = agent._read_relevant_context(q2, max_files=2, max_lines=60)
    print(f"   Context: {len(ctx2)} chars")

    # Store second interaction
    agent.vector_memory.store(
        text=f"Q: {q2}\nA: Uses TokenTracker + BudgetLedger.",
        metadata={"session_id": agent.current_session_id, "handler": "reasoning", "cost": 0.0001}
    )

except Exception as e:
    print(f"   ERROR: {e}")
    import traceback; traceback.print_exc()

# Query 3: Different topic — test CodebaseIndex on code search
print("\n4. Third query (test semantic code search)...")
try:
    q3 = "how does the vector memory store and retrieve embeddings"
    print(f"   Q: {q3}")

    ctx3 = agent._read_relevant_context(q3, max_files=2, max_lines=60)
    print(f"   Context: {len(ctx3)} chars")
    # Show which files were found
    lines = [l for l in ctx3.split("\n") if l.startswith("# ")]
    for l in lines[:3]:
        print(f"   Found: {l}")

except Exception as e:
    print(f"   ERROR: {e}")
    import traceback; traceback.print_exc()

# Final state
print("\n5. Final state...")
vm_stats = agent.vector_memory.stats()
bl_status = agent.budget_ledger.get_status(monthly_budget=20.0)
print(f"   VectorMemory: {vm_stats['total_interactions']} interactions")
print(f"   Budget: ${bl_status['spent']:.4f} spent, {bl_status['requests']} requests")

# Test recall command
print("\n6. Testing recall command...")
results = agent._recall_past_solutions("budget tracking", n_results=5)
print(f"   Results: {len(results.split(chr(10))) if results else 0} lines")

# Test budget command
print("\n7. Testing budget display...")
agent.budget_ledger.show_status(monthly_budget=20.0)

print("\n" + "=" * 60)
print("END-TO-END TEST COMPLETE")
print("=" * 60)

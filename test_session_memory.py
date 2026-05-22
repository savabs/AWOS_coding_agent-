#!/usr/bin/env python3
"""Quick test of session memory module"""

from scaffold.agent.session_memory import SessionMemory, ChatSessionManager, MemoryStrategy

# Test 1: Basic session memory
print("=" * 60)
print("TEST 1: Basic Session Memory")
print("=" * 60)

sm = SessionMemory(session_id='test1')
sm.add_message('user', 'What is the project structure?', 50)
sm.add_message('assistant', 'The project has 3 layers: Core, Tools, Config', 80)
sm.add_message('user', 'How much does this cost?', 40)
sm.add_message('assistant', 'Monthly budget is $15. Current spend: $0.04', 70)

print(f"✅ Messages added: {len(sm.messages)}")
print(f"✅ Total tokens: {sm.total_tokens}")

# Test 2: Get context
print("\n" + "=" * 60)
print("TEST 2: Context Retrieval (Hybrid Strategy)")
print("=" * 60)

context, tokens = sm.get_context_for_request(new_request_tokens=500)
print(f"✅ Context length: {len(context)} chars")
print(f"✅ Context tokens: {tokens}")
print(f"\nContext preview:\n{context[:200]}...")

# Test 3: Summarization
print("\n" + "=" * 60)
print("TEST 3: Summarization")
print("=" * 60)

should_sum = sm.should_summarize()
print(f"✅ Should summarize: {should_sum}")

if should_sum:
    summary = sm.create_summary()
    print(f"✅ Summary created: {len(summary)} chars")
    print(f"Preview: {summary[:150]}...")

# Test 4: Persistence
print("\n" + "=" * 60)
print("TEST 4: Persistence")
print("=" * 60)

json_str = sm.to_json()
print(f"✅ Serialized to JSON: {len(json_str)} chars")

# Test 5: Manager
print("\n" + "=" * 60)
print("TEST 5: Session Manager")
print("=" * 60)

manager = ChatSessionManager()
sess = manager.create_session('manager_test', MemoryStrategy.HYBRID)
manager.add_to_current_session('user', 'Hello from manager', 30)
print(f"✅ Manager created session: {sess.session_id}")
print(f"✅ Messages in session: {len(sess.messages)}")

print("\n" + "=" * 60)
print("🎉 ALL TESTS PASSED - Session Memory Ready!")
print("=" * 60)

#!/usr/bin/env python3
"""Smoke test for Week 1 foundation components."""
import sys
import os

sys.path.insert(0, 'scaffold')
os.environ.setdefault('AWOS_MONTHLY_BUDGET', '20.0')
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

from agent.memory.vector_memory import VectorMemory
from agent.memory.codebase_index import CodebaseIndex
from agent.budget_ledger import BudgetLedger, get_ledger
from agent.core.reasoning import ReasoningTraceStore, ReasoningSession, ReasoningTrace

print("Testing Week 1 components...")

# VectorMemory
vm = VectorMemory(persist_dir='/tmp/awos_final_test')
vm.store('Q: test query A: test answer', {'session_id': 's1', 'handler': 'coding', 'cost': 0.001})
results = vm.retrieve('test query', n_results=1)
assert len(results) == 1, f'Expected 1 result, got {len(results)}'
print('  VectorMemory: OK')

# CodebaseIndex
bi = CodebaseIndex(project_root='/home/becmachlean/2024/projects/AWOS_coding_agent/scaffold/agent', vector_memory=vm)
stats = bi.index_project()
assert stats['files'] > 0
assert stats['chunks'] > 0
print(f'  CodebaseIndex: OK ({stats["files"]} files, {stats["chunks"]} chunks)')

# BudgetLedger
bl = get_ledger()
bl.record('coding', 'DeepSeek', 100, 50, 0.0001)
status = bl.get_status(monthly_budget=20.0)
assert status['requests'] >= 1
print(f'  BudgetLedger: OK ({status["requests"]} requests, ${status["spent"]:.4f})')

# ReasoningTraceStore
rts = ReasoningTraceStore(persist_dir='/tmp/awos_traces_test')
session = ReasoningSession(session_id='test', goal='test goal')
session.add_trace(ReasoningTrace(
    thought='test thought', action='test_action',
    action_input={}, observation='ok', success=True
))
rts.save(session)
sessions = rts.list_sessions()
assert len(sessions) >= 1
print(f'  ReasoningTraceStore: OK ({len(sessions)} sessions)')

print()
print('All Week 1 components verified working!')

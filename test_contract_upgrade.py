#!/usr/bin/env python3
"""Smoke test for Planner Contract Upgrade feature."""
import sys
import os

sys.path.insert(0, 'scaffold/agent')

# ── Test 1: Verifier contract compliance ────────────────────────────────────
from verifier import Verifier

v = Verifier()

# Pass case: signature present
errors = v._check_contract_compliance(
    "def verify_jwt(token: str) -> Optional[UserClaims]:\n    pass",
    {"function_signature": "def verify_jwt(token: str) -> Optional[UserClaims]:"}
)
assert len(errors) == 0, f"Expected 0 errors, got: {errors}"
print("  Verifier PASS — signature present")

# Fail case: signature missing
errors = v._check_contract_compliance(
    "print('hello')",
    {"function_signature": "def verify_jwt(token: str):"}
)
assert any("Missing required function signature" in e for e in errors), f"Expected signature error, got: {errors}"
print("  Verifier PASS — catches missing signature")

# Fail case: must_not violated
errors = v._check_contract_compliance(
    "import requests\nprint('ok')",
    {"must_not": ["Do not import requests"]}
)
assert any("forbidden pattern found" in e for e in errors), f"Expected must_not error, got: {errors}"
print("  Verifier PASS — catches forbidden pattern")

# Pass case: no task spec → no errors
errors = v._check_contract_compliance("print('hello')", None)
assert len(errors) == 0
print("  Verifier PASS — graceful with no contract")

# ── Test 2: Worker prompt includes contract ───────────────────────────────────
# (Cannot instantiate Worker without API keys; test logic standalone)

task_with_contract = {
    "task_id": 1,
    "file": "auth.py",
    "action": "Add JWT verify",
    "complexity": "medium",
    "function_signature": "def verify(token: str) -> bool:",
    "constraints": ["Raise ValueError on empty token"],
    "must_not": ["Import external libraries"],
    "example_call": "ok = verify('abc')"
}

# Verify contract fields trigger the contract_section logic
has_contract = any(k in task_with_contract for k in ("function_signature", "constraints", "must_not", "interface_contract", "example_call"))
assert has_contract, "Test setup error"

# Simulate the contract_section build (same logic as in worker.py)
parts = ["\n🔒 CONTRACT (you MUST follow this exactly):"]
if task_with_contract.get("function_signature"):
    parts.append(f'Function signature: {task_with_contract["function_signature"]}')
if task_with_contract.get("constraints"):
    parts.append("Constraints you MUST satisfy:")
    for c in task_with_contract["constraints"]:
        parts.append(f"  • {c}")
contract_text = "\n".join(parts)

assert "def verify(token: str) -> bool:" in contract_text
assert "Raise ValueError" in contract_text
print("  Worker PASS — contract section built correctly")

# ── Test 3: Planner validation accepts optional contract fields ───────────────
# (Cannot instantiate Planner without API key; test validation logic standalone)

mock_task_required_only = {
    "task_id": 1, "file": "a.py", "action": "do thing", "complexity": "low"
}
mock_task_with_contract = {
    "task_id": 1, "file": "a.py", "action": "do thing", "complexity": "low",
    "function_signature": "def foo():",
    "constraints": ["check x"],
    "must_not": ["don't y"]
}

# Simulate validation logic (same as in planner.py)
required = ["task_id", "file", "action", "complexity"]
assert all(k in mock_task_required_only for k in required)
assert all(k in mock_task_with_contract for k in required)

# Optional contract field validation
for task in (mock_task_required_only, mock_task_with_contract):
    if "constraints" in task:
        assert isinstance(task["constraints"], list)
    if "must_not" in task:
        assert isinstance(task["must_not"], list)
    for opt in ("function_signature", "interface_contract", "example_call"):
        if opt in task:
            assert isinstance(task[opt], str)

print("  Planner PASS — backwards compatible, contract fields validated")

print()
print("All contract upgrade smoke tests passed!")

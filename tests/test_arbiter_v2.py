"""
Arbiter v2 (AWOS_ARBITER_V=2, docs/specs/sanitized_arbiter.md): sanitized
arbiter input, toxic-test triage, the CONTRADICTION label, and v1 unchanged
by default.

No network: a fake client; one test runs real pytest on a tmp project.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_acceptance import _FakeClient  # noqa: E402  (helper only)

from scaffold.agent import acceptance as acc  # noqa: E402
from scaffold.agent.acceptance import (  # noqa: E402
    AcceptanceSuite, arbitrate, build_suite, classify_failure, normalize_failure,
    run_acceptance_detail, sanitize_diff, triage_toxic,
)

SRC = '''def test_a():
    assert 1 == 2


def test_b():
    from pkg import thing
    assert thing() == 3


def test_c():
    assert "x" == "y"
'''

NARRATIVE = "I fixed the bug and the fix works; all tests pass now"

DIFF = f'''diff --git a/pkg/core.py b/pkg/core.py
--- a/pkg/core.py
+++ b/pkg/core.py
@@ -1,3 +1,5 @@
 def thing():
-    return 2
+    # {NARRATIVE}
+    return 3  # trailing code stays
+    // also a claim: verified correct
diff --git a/NOTES.md b/NOTES.md
--- /dev/null
+++ b/NOTES.md
@@ -0,0 +1 @@
+## Summary: {NARRATIVE}
diff --git a/CHANGELOG b/CHANGELOG
+- {NARRATIVE}

New files: pkg/new.py, SUMMARY.md'''


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setenv("AWOS_ARBITER_V", "2")


def _user_prompt(client) -> str:
    return client.calls[-1]["messages"][-1]["content"]


def _all_prompt(client) -> str:
    return "\n".join(m["content"] for m in client.calls[-1]["messages"])


# ── 1. v1 default unchanged ──────────────────────────────────────────────────

def test_default_is_v1(monkeypatch):
    monkeypatch.delenv("AWOS_ARBITER_V", raising=False)
    assert acc.arbiter_version() == "1"
    monkeypatch.setenv("AWOS_ARBITER_V", "3")
    assert acc.arbiter_version() == "1"


def test_v1_prompt_and_result_shape_unchanged(monkeypatch):
    monkeypatch.delenv("AWOS_ARBITER_V", raising=False)
    client = _FakeClient('{"test_a": "CONTRADICTION", "test_c": "CODE_INCOMPLETE"}')
    suite = AcceptanceSuite(source=SRC, kept=["test_a", "test_c"],
                            start_failures={"test_a": "E   ModuleNotFoundError: "
                                            "No module named 'zzz'"})
    arb = arbitrate("issue", suite, {"test_a": "E   ModuleNotFoundError: No module named 'zzz'",
                                     "test_c": "E assert"}, DIFF,
                    client=client, model="m")
    assert client.calls[-1]["messages"][0]["content"] == acc.ARBITRATION_PROMPT
    assert arb["verdicts"] == {"test_a": "WRONG_TEST", "test_c": "CODE_INCOMPLETE"}
    assert "labels" not in arb and "toxic" not in arb
    assert NARRATIVE in _user_prompt(client)  # v1 sends the raw diff, as before


# ── 2. sanitization ──────────────────────────────────────────────────────────

def test_sanitize_diff_strips_comments_and_prose_keeps_code():
    clean = sanitize_diff(DIFF)
    assert NARRATIVE not in clean and "verified correct" not in clean
    assert "+    return 3  # trailing code stays" in clean
    assert "-    return 2" in clean and "pkg/core.py" in clean
    assert "NOTES.md" not in clean and "SUMMARY.md" not in clean
    assert "New files: pkg/new.py" in clean


def test_narrative_never_reaches_v2_prompt(v2):
    client = _FakeClient('{"test_c": "CODE_INCOMPLETE"}')
    suite = AcceptanceSuite(source=SRC, kept=["test_c"], goal_text="the verbatim goal")
    # Even when the caller passes an agent summary as the goal, the build-time
    # verbatim goal is used.
    arb = arbitrate(f"Summary: {NARRATIVE}", suite, {"test_c": "E   assert 'x' == 'y'"}, DIFF,
                    client=client, model="m")
    prompt = _all_prompt(client)
    assert NARRATIVE not in prompt
    assert "the verbatim goal" in prompt
    assert "def test_c" in prompt and "assert 'x' == 'y'" in prompt
    assert "def test_a" not in prompt  # only the failing tests' own source
    assert client.calls[-1]["messages"][0]["content"] == acc.ARBITRATION_PROMPT_V2
    assert arb["verdicts"] == {"test_c": "CODE_INCOMPLETE"}


# ── 3. toxic triage ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,goal,want", [
    ("E   ModuleNotFoundError: No module named 'numpy'", "add a cli flag", "env"),
    ("E   ModuleNotFoundError: No module named 'pkg.report'", "add pkg.report module",
     "behaviour"),
    ("E   ImportError: cannot import name 'helper' from 'pkg'", "speed up", "env"),
    ("E   ImportError: cannot import name 'prune' from 'pkg'", "add a prune command",
     "behaviour"),
    ("fixture 'db' not found", "", "env"),
    ("E   PermissionError: [Errno 1] Operation not permitted: '/etc'", "", "env"),
    ("E   assert 2 == 3", "", "behaviour"),
    ("E   AttributeError: 'Store' object has no attribute 'size'", "", "behaviour"),
])
def test_classify_failure(text, goal, want):
    assert classify_failure(text, goal) == want


def test_normalize_masks_run_noise():
    a = "E .awos_acceptance_1a2b3c4d/test_x.py:3 obj at 0x10abcdef12 in 0.12s"
    b = "E .awos_acceptance_99ffee00/test_x.py:3 obj at 0x7fff00001111 in 3.40s"
    assert normalize_failure(a) == normalize_failure(b)


def test_triage_needs_identical_and_env():
    env = "E   ModuleNotFoundError: No module named 'numpy'"
    suite = AcceptanceSuite(source=SRC, kept=["test_a", "test_b", "test_c"], start_failures={
        "test_a": env,                       # identical + env -> toxic
        "test_b": "E   ModuleNotFoundError: No module named 'other'",  # changed -> keep
        "test_c": "E   assert 1 == 2",       # identical but behaviour -> keep
    })
    failing = {"test_a": env, "test_b": env, "test_c": "E   assert 1 == 2"}
    rest, toxic = triage_toxic(suite, failing, "goal")
    assert list(toxic) == ["test_a"] and list(rest) == ["test_b", "test_c"]


def test_all_toxic_skips_model_call(v2, capsys):
    env = "E   ModuleNotFoundError: No module named 'numpy'"
    client = _FakeClient("{}")
    suite = AcceptanceSuite(source=SRC, kept=["test_a"], start_failures={"test_a": env})
    arb = arbitrate("goal", suite, {"test_a": env}, "", client=client, model="m")
    assert client.calls == []
    assert arb["verdicts"] == {"test_a": "WRONG_TEST"} and arb["labels"] == {"test_a": "TOXIC"}
    assert arb["cost_usd"] == 0.0
    out = capsys.readouterr().out
    assert "arbiter v2 triage: dropped 1 toxic test(s): test_a" in out
    assert "arbiter v2 labels: test_a=TOXIC" in out


# ── 4. CONTRADICTION ─────────────────────────────────────────────────────────

def test_contradiction_is_dropped_and_logged(v2, capsys):
    client = _FakeClient('{"test_a": "CONTRADICTION", "test_c": "CODE_INCOMPLETE"}')
    suite = AcceptanceSuite(source=SRC, kept=["test_a", "test_c"])
    arb = arbitrate("goal", suite, {"test_a": "E assert", "test_c": "E assert"}, "",
                    client=client, model="m")
    assert arb["verdicts"] == {"test_a": "WRONG_TEST", "test_c": "CODE_INCOMPLETE"}
    assert arb["labels"] == {"test_a": "CONTRADICTION", "test_c": "CODE_INCOMPLETE"}
    assert arb["contradictions"] == ["test_a"]
    out = capsys.readouterr().out
    assert "flag_contradiction: 1 test(s)" in out
    assert "labels: test_a=CONTRADICTION, test_c=CODE_INCOMPLETE" in out
    assert '[ACCEPTANCE] arbitration reply: {"test_a": "CONTRADICTION"' in out


def test_v2_unparseable_is_fail_safe(v2):
    suite = AcceptanceSuite(source=SRC, kept=["test_a"])
    arb = arbitrate("goal", suite, {"test_a": "E"}, "",
                    client=_FakeClient("the code is incomplete, I am sure"), model="m")
    assert arb["verdicts"] == {"test_a": "WRONG_TEST"} and not arb["parsed"]


# ── 5. real pytest: start failures are recorded and a toxic test is caught ──

def test_build_suite_records_start_failures_and_triage_catches_toxic(monkeypatch, v2):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")
    root = Path(tempfile.mkdtemp())
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("def size():\n    return -1\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_pkg.py").write_text("def test_ok():\n    assert True\n")
    gen = ("```python\n"
           "def test_size_zero():\n    from pkg import size\n    assert size() == 0\n\n\n"
           "def test_needs_absent_lib():\n    import zz_absent_lib_q\n    assert zz_absent_lib_q\n"
           "```")
    suite = build_suite("size() of an empty store is 0", str(root),
                        client=_FakeClient(gen), model="m")
    assert sorted(suite.kept) == ["test_needs_absent_lib", "test_size_zero"]
    assert suite.goal_text == "size() of an empty store is 0"
    assert set(suite.start_failures) == set(suite.kept)
    # The "edit": fix size(); the absent-library test fails identically.
    (root / "pkg" / "__init__.py").write_text("def size():\n    return 0\n")
    ok, _, _, failing = run_acceptance_detail(str(root), suite)
    assert not ok and list(failing) == ["test_needs_absent_lib"]
    client = _FakeClient("{}")
    arb = arbitrate("goal", suite, failing, "", client=client, model="m")
    assert arb["labels"] == {"test_needs_absent_lib": "TOXIC"} and client.calls == []

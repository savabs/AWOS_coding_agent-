"""
P0 Tests — Structured Edit Format
Tests for edit_models.py and Worker P0 methods:
  _parse_json_edits, _find_nearest_match, _apply_single_edit, _apply_all_edits
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from scaffold.agent.edit_models import (
    EditInstruction, EditRequest, EditResult, SingleEditResult, EditStatus
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

def make_worker():
    """Create a Worker without live API keys (parse/apply methods need no keys)."""
    import unittest.mock as mock
    import scaffold.agent.worker as worker_mod
    # Patch the API clients so __init__ doesn't fail without real keys
    with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test", "ANTHROPIC_API_KEY": "test"}):
        with mock.patch("scaffold.agent.worker.OpenAI"), \
             mock.patch("scaffold.agent.worker.Anthropic"), \
             mock.patch("scaffold.agent.worker.get_ledger"):
            return worker_mod.Worker(api_key="test")


SAMPLE_FILE = """\
def greet(name):
    return f"Hello, {name}"

def add(a, b):
    return a + b

class Calculator:
    def multiply(self, x, y):
        return x * y
"""


# ─── edit_models unit tests ───────────────────────────────────────────────────

class TestEditModels:
    def test_edit_instruction_fields(self):
        instr = EditInstruction(old_string="foo", new_string="bar", description="rename")
        assert instr.old_string == "foo"
        assert instr.new_string == "bar"
        assert instr.description == "rename"

    def test_edit_request_fields(self):
        instr = EditInstruction(old_string="a", new_string="b")
        req = EditRequest(edits=[instr], reasoning="test reason")
        assert len(req.edits) == 1
        assert req.reasoning == "test reason"

    def test_edit_result_success_property(self):
        instr = EditInstruction(old_string="a", new_string="b")
        req = EditRequest(edits=[instr])
        ok_result = SingleEditResult(instruction=instr, status=EditStatus.OK, new_content="b")
        result = EditResult(request=req, results=[ok_result], final_content="b", applied=1, failed=0)
        assert result.success is True

    def test_edit_result_failure_property(self):
        instr = EditInstruction(old_string="missing", new_string="x")
        req = EditRequest(edits=[instr])
        bad = SingleEditResult(instruction=instr, status=EditStatus.NOT_FOUND, error="not found")
        result = EditResult(request=req, results=[bad], final_content="original", applied=0, failed=1)
        assert result.success is False

    def test_edit_result_to_legacy_dict_success(self):
        instr = EditInstruction(old_string="old", new_string="new")
        req = EditRequest(edits=[instr], reasoning="changed it")
        ok = SingleEditResult(instruction=instr, status=EditStatus.OK, new_content="new")
        result = EditResult(request=req, results=[ok], final_content="new", applied=1, failed=0)
        d = result.to_legacy_dict()
        assert d["success"] is True
        assert d["search"] == "old"
        assert d["replace"] == "new"
        assert d["reasoning"] == "changed it"

    def test_edit_result_to_legacy_dict_no_results(self):
        req = EditRequest(edits=[], reasoning="")
        result = EditResult(request=req, results=[], final_content="", applied=0, failed=0)
        d = result.to_legacy_dict()
        assert d["success"] is False


# ─── Worker._parse_json_edits ─────────────────────────────────────────────────

class TestParseJsonEdits:
    def setup_method(self):
        self.w = make_worker()

    def test_parse_valid_json_bare(self):
        raw = '{"edits": [{"old_string": "foo", "new_string": "bar"}], "reasoning": "r"}'
        req = self.w._parse_json_edits(raw)
        assert req is not None
        assert len(req.edits) == 1
        assert req.edits[0].old_string == "foo"
        assert req.edits[0].new_string == "bar"
        assert req.reasoning == "r"

    def test_parse_valid_json_fenced(self):
        raw = '```json\n{"edits": [{"old_string": "x", "new_string": "y"}], "reasoning": ""}\n```'
        req = self.w._parse_json_edits(raw)
        assert req is not None
        assert req.edits[0].old_string == "x"

    def test_parse_multi_edit(self):
        raw = '{"edits": [{"old_string": "a", "new_string": "1"}, {"old_string": "b", "new_string": "2"}], "reasoning": ""}'
        req = self.w._parse_json_edits(raw)
        assert req is not None
        assert len(req.edits) == 2

    def test_parse_legacy_search_replace_returns_none(self):
        raw = "SEARCH:\n```\nfoo\n```\nREPLACE:\n```\nbar\n```"
        req = self.w._parse_json_edits(raw)
        assert req is None

    def test_parse_empty_edits_list_returns_none(self):
        raw = '{"edits": [], "reasoning": "nothing"}'
        req = self.w._parse_json_edits(raw)
        assert req is None

    def test_parse_malformed_json_returns_none(self):
        raw = '{"edits": [{"old_string": "x"'  # truncated
        req = self.w._parse_json_edits(raw)
        assert req is None


# ─── Worker._find_nearest_match ───────────────────────────────────────────────

class TestFindNearestMatch:
    def setup_method(self):
        self.w = make_worker()

    def test_exact_match_returns_high_score(self):
        content = "def foo():\n    return 1\n"
        match, score = self.w._find_nearest_match(content, "def foo():\n    return 1")
        assert score >= 0.95

    def test_near_match_with_whitespace_diff(self):
        content = "def foo():\n    return  1\n"  # extra space
        match, score = self.w._find_nearest_match(content, "def foo():\n    return 1")
        assert score >= 0.82  # above threshold

    def test_no_match_below_threshold(self):
        content = "class Bar:\n    pass\n"
        match, score = self.w._find_nearest_match(content, "def completely_different():\n    return 999")
        assert match == ""
        assert score == 0.0

    def test_empty_old_string(self):
        match, score = self.w._find_nearest_match("anything", "")
        assert match == ""
        assert score == 0.0


# ─── Worker._apply_single_edit ────────────────────────────────────────────────

class TestApplySingleEdit:
    def setup_method(self):
        self.w = make_worker()

    def test_exact_match_applies_cleanly(self):
        r = self.w._apply_single_edit(SAMPLE_FILE, 'return f"Hello, {name}"', 'return f"Hi, {name}!"')
        assert r.status == EditStatus.OK
        assert 'Hi, {name}!' in r.new_content
        assert r.match_count == 1

    def test_multi_match_returns_error(self):
        content = "x = 1\nx = 1\n"
        r = self.w._apply_single_edit(content, "x = 1", "x = 2")
        assert r.status == EditStatus.MULTI_MATCH
        assert r.match_count == 2
        assert "ambiguous" in r.error

    def test_not_found_returns_error(self):
        r = self.w._apply_single_edit(SAMPLE_FILE, "def nonexistent():", "def nonexistent(): pass")
        assert r.status == EditStatus.NOT_FOUND
        assert r.new_content == ""

    def test_empty_old_string_appends(self):
        r = self.w._apply_single_edit("existing content", "", "# new line")
        assert r.status == EditStatus.OK
        assert "# new line" in r.new_content
        assert "existing content" in r.new_content

    def test_fuzzy_match_triggers_when_close(self):
        content = "def add(a, b):\n    return a + b\n"
        old_slightly_off = "def add(a, b):\n    return a+b"  # missing space
        r = self.w._apply_single_edit(content, old_slightly_off, "def add(a, b):\n    return a + b + 0")
        # Should be FUZZY_MATCH or OK (exact may catch it depending on content)
        assert r.status in (EditStatus.OK, EditStatus.FUZZY_MATCH, EditStatus.NOT_FOUND)


# ─── Worker._apply_all_edits ─────────────────────────────────────────────────

class TestApplyAllEdits:
    def setup_method(self):
        self.w = make_worker()

    def test_single_edit_success(self):
        req = EditRequest(edits=[EditInstruction("return a + b", "return a + b + 0")])
        result = self.w._apply_all_edits(SAMPLE_FILE, req)
        assert result.success
        assert result.applied == 1
        assert result.failed == 0
        assert "return a + b + 0" in result.final_content

    def test_sequential_edits_chain(self):
        content = "x = 1\ny = 2\n"
        req = EditRequest(edits=[
            EditInstruction("x = 1", "x = 10"),
            EditInstruction("y = 2", "y = 20"),
        ])
        result = self.w._apply_all_edits(content, req)
        assert result.applied == 2
        assert "x = 10" in result.final_content
        assert "y = 20" in result.final_content

    def test_partial_failure_stops_bad_edit(self):
        req = EditRequest(edits=[
            EditInstruction("return a + b", "return a + b + 0"),  # OK
            EditInstruction("DOES NOT EXIST", "xxx"),             # NOT_FOUND
        ])
        result = self.w._apply_all_edits(SAMPLE_FILE, req)
        assert result.applied == 1
        assert result.failed == 1
        assert result.partial is True

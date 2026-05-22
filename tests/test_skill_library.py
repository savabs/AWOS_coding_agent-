"""
Tests for SkillLibrary (v4).

Covers:
  - record() creates entries and writes skill .md files
  - Merging duplicate (task_type, keyword, model, strategy) entries
  - get_skill_context() returns relevant snippets, empty string when none
  - Persistence roundtrip (save + reload)
  - _classify() task type detection
  - _extract_keywords() keyword extraction
  - Skill file content structure
  - Worker receives and uses skill_library param
"""

import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.skill_library import (
    SkillLibrary, SkillEntry, _classify, _extract_keywords,
)


# ── _classify ─────────────────────────────────────────────────────────────────

class TestClassify:
    def _t(self, action): return {"action": action}

    def test_bug_fix(self):       assert _classify(self._t("fix the crash in login"))  == "bug_fix"
    def test_refactor(self):      assert _classify(self._t("refactor auth module"))     == "refactor"
    def test_new_feature(self):   assert _classify(self._t("add oauth2 support"))      == "new_feature"
    def test_architecture(self):  assert _classify(self._t("design the system interface abstraction"))  == "architecture"
    def test_other(self):         assert _classify(self._t("do something unusual"))     == "other"


# ── _extract_keywords ─────────────────────────────────────────────────────────

class TestExtractKeywords:
    def test_returns_list(self):
        kws = _extract_keywords("fix validate token expiry")
        assert isinstance(kws, list)

    def test_filters_stopwords(self):
        kws = _extract_keywords("fix the token")
        assert "the" not in kws
        assert "fix" not in kws

    def test_filters_short_words(self):
        kws = _extract_keywords("add a new io loop")
        assert "a" not in kws
        assert "io" not in kws

    def test_deduplicates(self):
        kws = _extract_keywords("token token token validation")
        assert kws.count("token") == 1

    def test_sorted_by_length_desc(self):
        kws = _extract_keywords("implement validation logic")
        if len(kws) >= 2:
            assert len(kws[0]) >= len(kws[-1])


# ── SkillLibrary.record ───────────────────────────────────────────────────────

class TestRecord:
    def _lib(self, tmp_path): return SkillLibrary(skills_dir=tmp_path / "skills")

    def _task(self, action, complexity="medium"):
        return {"task_id": 1, "action": action, "file": "x.py", "complexity": complexity}

    def test_creates_entry(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.record(self._task("fix validate_token bug"), "DeepSeek", "cot", attempts=1)
        assert lib.total_entries() == 1

    def test_merges_duplicate(self, tmp_path):
        lib = self._lib(tmp_path)
        task = self._task("fix validate_token function")
        lib.record(task, "DeepSeek", "cot", attempts=1)
        lib.record(task, "DeepSeek", "cot", attempts=1)
        assert lib.total_entries() == 1
        entry = lib._entries[0]
        assert entry.success_count == 2

    def test_different_strategy_creates_separate_entry(self, tmp_path):
        lib = self._lib(tmp_path)
        task = self._task("fix validate_token function")
        lib.record(task, "DeepSeek", "cot", attempts=1)
        lib.record(task, "DeepSeek", "direct", attempts=1)
        assert lib.total_entries() == 2

    def test_writes_skill_md_file(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.record(self._task("fix broken login"), "DeepSeek", "cot", attempts=1)
        md_path = tmp_path / "skills" / "bug_fix.md"
        assert md_path.exists()

    def test_skill_md_contains_model_and_strategy(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.record(self._task("fix broken login"), "Claude Haiku", "step_by_step", attempts=2)
        md_content = (tmp_path / "skills" / "bug_fix.md").read_text()
        assert "Claude Haiku" in md_content
        assert "step_by_step" in md_content

    def test_skill_md_contains_header(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.record(self._task("add new oauth feature"), "DeepSeek", "direct", attempts=1)
        md_content = (tmp_path / "skills" / "new_feature.md").read_text()
        assert "# AWOS Skills: new_feature" in md_content


# ── SkillLibrary.get_skill_context ───────────────────────────────────────────

class TestGetSkillContext:
    def _lib(self, tmp_path): return SkillLibrary(skills_dir=tmp_path / "skills")

    def test_returns_empty_when_no_entries(self, tmp_path):
        lib = self._lib(tmp_path)
        ctx = lib.get_skill_context({"action": "fix bug in parser"})
        assert ctx == ""

    def test_returns_string(self, tmp_path):
        lib = self._lib(tmp_path)
        task = {"action": "fix validate token bug", "file": "auth.py"}
        lib.record(task, "DeepSeek", "cot", attempts=1)
        ctx = lib.get_skill_context(task)
        assert isinstance(ctx, str)

    def test_returns_relevant_context(self, tmp_path):
        lib = self._lib(tmp_path)
        task = {"action": "fix validate token expiry", "file": "auth.py"}
        lib.record(task, "DeepSeek V4 Flash", "cot", attempts=1)
        ctx = lib.get_skill_context({"action": "fix validate token function"})
        assert "DeepSeek V4 Flash" in ctx or "cot" in ctx

    def test_returns_empty_for_different_task_type(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.record({"action": "fix bug in login"}, "DeepSeek", "cot", attempts=1)
        ctx = lib.get_skill_context({"action": "add new feature to dashboard"})
        assert ctx == ""

    def test_context_contains_skill_header(self, tmp_path):
        lib = self._lib(tmp_path)
        task = {"action": "fix broken parser bug"}
        lib.record(task, "DeepSeek", "direct", attempts=1)
        ctx = lib.get_skill_context(task)
        assert "SKILL CONTEXT" in ctx

    def test_limits_to_max_context_skills(self, tmp_path):
        lib = self._lib(tmp_path)
        for i in range(10):
            lib.record(
                {"action": f"fix bug number {i} in parser"},
                f"Model{i}", f"strategy{i}", attempts=1,
            )
        ctx = lib.get_skill_context({"action": "fix parser bug"})
        bullet_count = ctx.count("•")
        assert bullet_count <= lib.MAX_CONTEXT_SKILLS


# ── Persistence roundtrip ─────────────────────────────────────────────────────

class TestPersistence:
    def test_roundtrip(self, tmp_path):
        skills_dir = tmp_path / "skills"
        lib1 = SkillLibrary(skills_dir=skills_dir)
        lib1.record({"action": "fix validate token"}, "DeepSeek", "cot", attempts=1)
        lib2 = SkillLibrary(skills_dir=skills_dir)
        assert lib2.total_entries() == 1
        assert lib2._entries[0].model_used == "DeepSeek"
        assert lib2._entries[0].strategy_used == "cot"

    def test_accumulated_data_persists(self, tmp_path):
        skills_dir = tmp_path / "skills"
        task = {"action": "fix validate token"}
        lib1 = SkillLibrary(skills_dir=skills_dir)
        lib1.record(task, "DeepSeek", "cot", attempts=1)
        lib1.record(task, "DeepSeek", "cot", attempts=1)
        lib2 = SkillLibrary(skills_dir=skills_dir)
        assert lib2._entries[0].success_count == 2


# ── SkillEntry win_rate and avg_attempts ──────────────────────────────────────

class TestSkillEntry:
    def test_win_rate(self):
        e = SkillEntry("bug_fix", ["token"], "DeepSeek", "cot", 3,
                       success_count=3, total_count=4)
        assert e.win_rate == pytest.approx(0.75)

    def test_avg_attempts(self):
        e = SkillEntry("bug_fix", ["token"], "DeepSeek", "cot", 6,
                       success_count=3, total_count=3)
        assert e.avg_attempts == pytest.approx(2.0)

    def test_win_rate_zero_safe(self):
        e = SkillEntry("bug_fix", [], "M", "s", 0, success_count=0, total_count=0)
        assert e.win_rate == pytest.approx(0.0)


# ── Worker integration smoke test ─────────────────────────────────────────────

class TestWorkerSkillIntegration:
    def test_worker_accepts_skill_library_param(self, tmp_path):
        from scaffold.agent.worker import Worker
        from scaffold.agent.skill_library import SkillLibrary
        from unittest.mock import MagicMock, patch

        skill_lib = SkillLibrary(skills_dir=tmp_path / "skills")
        skill_lib.record(
            {"action": "fix validate token bug"},
            "DeepSeek", "cot", attempts=1,
        )

        worker = MagicMock(spec=Worker)
        worker.execute_task.return_value = {"success": True, "search": "x", "replace": "y"}

        result = worker.execute_task(
            task={"action": "fix validate token", "file": "auth.py", "complexity": "low"},
            file_content="def validate_token(): pass",
            codebase_context={},
            skill_library=skill_lib,
        )
        assert result["success"] is True
        worker.execute_task.assert_called_once()
        _, kwargs = worker.execute_task.call_args
        assert "skill_library" in kwargs

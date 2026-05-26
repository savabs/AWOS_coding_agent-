# P3 Spec — LSP Feedback, Auto Skills, Enhanced Repo Map

**Full spec**: [docs/specs/p3_advanced_spec.html](p3_advanced_spec.html)  
**Priority**: P3 SOTA | **Effort**: 12h | **Prerequisite**: P1 stable

## What
Ruff/pyright linting as post-edit feedback (instant diagnostic loop). Auto skill extraction from successes → .awos/skills/. Call graph index for cross-reference context.

## Steps (8)
1. LintDiagnostic dataclass + RuffLinter class (1h)
2. DiagnosticPipeline + ruff_autofix() (1h)
3. Wire DiagnosticPipeline into verification flow (0.5h)
4. SkillExtractor + _generate_skill() via cheap LLM (2h)
5. SkillLibrary.get_relevant_skills() + wire into worker (1.5h)
6. CallSite dataclass + CallGraphIndex (2h)
7. query_with_graph() + wire into orchestrator (1.5h)
8. Write tests/test_p3_improvements.py (16 tests) (2.5h)

## Files
- scaffold/agent/lint_pipeline.py (new) — RuffLinter, DiagnosticPipeline
- scaffold/agent/skill_manager.py (new) — SkillExtractor, SkillLibrary
- scaffold/agent/memory/codebase_index.py — call graph + query_with_graph
- scaffold/agent/orchestrator.py — wire all
- scaffold/agent/worker.py — skill injection
- tests/test_p3_improvements.py — new test file

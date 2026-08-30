"""Tests for GrepTool literal mode and repo_map injection."""

from __future__ import annotations

import os

import pytest

from scaffold.agent.repo_map import (
    _extract_search_terms,
    build_repo_map,
    enrich_with_repo_map,
    repo_map_enabled,
)
from scaffold.agent.tools.filesystem import GrepTool, _infer_literal


@pytest.fixture
def mini_repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "cli.py").write_text(
        'def cmd_stats(args):\n    pass\n\n'
        'stats.add_argument("--savings", action="store_true")\n',
        encoding="utf-8",
    )
    (tmp_path / ".awos").mkdir()
    (tmp_path / ".awos" / "hidden.py").write_text('def cmd_stats():\n    pass\n', encoding="utf-8")
    return tmp_path


def test_infer_literal_for_code_symbols():
    assert _infer_literal('stats.add_argument("--savings"', None) is True
    assert _infer_literal("def cmd_stats", None) is False
    assert _infer_literal("greet", None) is False


def test_grep_literal_quotes_no_regex_error(mini_repo):
    tool = GrepTool()
    result = tool.execute(
        {
            "pattern": 'stats.add_argument("--savings"',
            "root": str(mini_repo),
            "file_glob": "*.py",
        }
    )
    assert result.success, result.error
    assert result.data.get("literal") is True
    assert any("cli.py" in h["file"] for h in result.data["hits"])


def test_grep_skips_awos_dir(mini_repo):
    tool = GrepTool()
    result = tool.execute(
        {"pattern": "cmd_stats", "root": str(mini_repo), "file_glob": "*.py", "literal": "true"}
    )
    assert result.success
    files = {h["file"] for h in result.data["hits"]}
    assert not any(f.startswith(".awos") for f in files)
    assert "pkg/cli.py" in files


def test_grep_single_file_path(mini_repo):
    tool = GrepTool()
    result = tool.execute(
        {
            "pattern": "cmd_stats",
            "root": str(mini_repo),
            "path": "pkg/cli.py",
            "literal": "true",
        }
    )
    assert result.success
    assert len(result.data["hits"]) == 1
    assert result.data["hits"][0]["line"] == 1


def test_extract_search_terms_quoted_and_py():
    goal = 'Add `--costs` to awos.py near `stats.add_argument("--savings"`'
    terms = _extract_search_terms(goal)
    assert any("awos.py" in t for t in terms)
    assert any("costs" in t.lower() for t in terms)
    assert any("savings" in t for t in terms)


def test_build_repo_map_finds_cmd_stats(mini_repo):
    goal = "Implement cmd_stats --costs in pkg/cli.py"
    result = build_repo_map(goal, mini_repo, primary_file="pkg/cli.py")
    summary = result["summary"]
    assert "cmd_stats" in summary
    assert result["grep_hits"]
    assert any(a["kind"] == "def" for a in result["anchors"])
    assert "Suggested read_file window" in summary


def test_enrich_with_repo_map_merges_context(mini_repo):
    os.environ["AWOS_REPO_MAP"] = "1"
    os.environ["AWOS_EXPLORATION_PHASE"] = "0"
    ctx = enrich_with_repo_map("cmd_stats savings", mini_repo, {}, primary_file="pkg/cli.py")
    assert "repo_map" in ctx
    assert "cmd_stats" in ctx["repo_map"]


def test_repo_map_enabled_default():
    os.environ.pop("AWOS_REPO_MAP", None)
    assert repo_map_enabled() is True

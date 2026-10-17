"""--add-files auto: the "relevant" rule for repos whose sources are over budget.

Seen on real_pyparsing_560/647 and real_more-itertools_1250: the sources were
over the 40k budget, auto added nothing, Aider asked to add files and the run
was invalid. Now the sources that define what the goal names go in whole (up to
a cap), even when one file alone is over the budget.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import harness_aider as ha  # noqa: E402

BIG = "x = 1\n" * 400          # 2400 bytes -> 600 tokens of filler


def _project(tmp_path, files):
    project = tmp_path / "proj"
    for rel, text in files.items():
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text(text)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "add", "-A"], check=True)
    return project


FILES = {
    "pkg/__init__.py": "from .core import *\n",
    "pkg/core.py": "class Word:\n    pass\n\ndef parse_string(s):\n    pass\n" + BIG * 3,
    "pkg/helpers.py": "def match_previous_expr(e):\n    pass\n" + BIG,
    "pkg/other.py": BIG,
    "examples/a.py": "expr = 1\nfirst = 2\n",
    "examples/b.py": "expr = 1\nfirst = 2\n",
    "examples/c.py": "expr = 1\n",
    "tests/test_helpers.py": "def test_m():\n    pass\n" + BIG,
    "tests/test_unrelated.py": "def test_u():\n    pass\n",
    "tests/test_uses.py": "from pkg import match_previous_expr\n",
}

GOAL = ("match_previous_expr does not handle nested expressions\n\n"
        "```python\nexpr = pp.Word('a') + pp.match_previous_expr(first)\n"
        "expr.parse_string('a a')\n```\n")


def test_goal_identifiers_title_and_code():
    title, all_ids = ha.goal_identifiers(GOAL)
    assert title == {"match_previous_expr"}
    assert {"match_previous_expr", "expr", "Word", "parse_string", "first"} <= all_ids
    assert "does" not in all_ids and "nested" not in all_ids


def test_title_identifier_definer_comes_first_and_alone(tmp_path):
    project = _project(tmp_path, FILES)
    src = ha.source_files(project)
    assert ha.relevant_sources(project, src, GOAL) == ["pkg/helpers.py"]


def test_without_title_hits_only_distinctive_definers_count(tmp_path):
    project = _project(tmp_path, FILES)
    goal = "Nested tags fail\n\n```\nexpr = Word('a'); first = 1\n```"
    # Word is defined once (core.py); expr/first are in 3 / 2 example scripts.
    ranked = ha.relevant_sources(project, ha.source_files(project), goal)
    assert ranked[0] == "pkg/core.py"
    assert "examples/c.py" not in ranked     # only defines expr (3 definers)


def test_over_budget_adds_relevant_file_even_if_alone_over_budget(tmp_path):
    project = _project(tmp_path, FILES)
    helpers = ha.estimate_tokens(project, ["pkg/helpers.py"])
    budget = helpers - 1                     # the file alone is over budget
    # Before: nothing (repo_map); now the defining file is editable.
    assert ha.select_files(project, "auto", budget) == ([], [], "repo_map")
    edit, read, how = ha.select_files(project, "auto", budget, GOAL)
    assert how == "relevant"
    assert edit == ["pkg/helpers.py"]
    assert read == []                        # tests dropped first: no room under budget


def test_relevant_tests_fill_remaining_budget_most_relevant_first(tmp_path):
    project = _project(tmp_path, FILES)
    sources = ha.estimate_tokens(project, ha.source_files(project))
    edit, read, how = ha.select_files(project, "auto", sources - 1, GOAL)
    assert (how, edit) == ("relevant", ["pkg/helpers.py"])
    # test_helpers names the edited module; test_uses mentions the title name;
    # test_unrelated does neither and is left out.
    assert read == ["tests/test_helpers.py", "tests/test_uses.py"]


def test_cap_bounds_the_relevant_files(tmp_path):
    project = _project(tmp_path, FILES)
    helpers = ha.estimate_tokens(project, ["pkg/helpers.py"])
    assert ha.select_files(project, "auto", 10, GOAL, cap_tokens=helpers - 1) == ([], [], "repo_map")
    assert ha.select_files(project, "auto", 10, GOAL, cap_tokens=helpers)[:2] == (["pkg/helpers.py"], [])


def test_within_budget_selection_is_unchanged_by_goal(tmp_path):
    project = _project(tmp_path, FILES)
    total = ha.estimate_tokens(project, ha.source_files(project) + ha.test_files(project))
    assert ha.select_files(project, "auto", total, GOAL) == ha.select_files(project, "auto", total)
    assert ha.select_files(project, "auto", total, GOAL)[2] == "src+tests"
    src = ha.estimate_tokens(project, ha.source_files(project))
    assert ha.select_files(project, "auto", src, GOAL) == ha.select_files(project, "auto", src)
    assert ha.select_files(project, "auto", src, GOAL)[2] == "src"


def test_no_named_identifier_falls_back_to_repo_map(tmp_path):
    project = _project(tmp_path, FILES)
    assert ha.select_files(project, "auto", 10, "Something is broken\n\nplease fix") == ([], [], "repo_map")

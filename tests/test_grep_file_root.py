"""GrepTool with a single file as root, and with a root that does not exist."""

from scaffold.agent.tools.filesystem import GrepTool


def _project(tmp_path):
    pkg = tmp_path / "parse"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("import os\n\ndef parse(x):\n    return x\n")
    (pkg / "util.py").write_text("def parse_util():\n    pass\n")
    (tmp_path / ".env").write_text("SECRET=parse\n")
    return tmp_path


def test_file_root_finds_matches_with_line_numbers(tmp_path):
    tool = GrepTool(project_root=str(_project(tmp_path)), confine=True)
    res = tool.execute({"pattern": "def parse", "root": "parse/__init__.py"})
    assert res.success
    hits = res.data["hits"]
    assert hits == [{"file": "__init__.py", "line": 3, "text": "def parse(x):"}]
    assert "__init__.py:3: def parse(x):" in res.text


def test_file_root_ignores_file_glob(tmp_path):
    tool = GrepTool(project_root=str(_project(tmp_path)))
    res = tool.execute({"pattern": "import", "root": "parse/__init__.py", "file_glob": "*.txt"})
    assert res.success
    assert [h["line"] for h in res.data["hits"]] == [1]


def test_nonexistent_root_fails_clearly(tmp_path):
    tool = GrepTool(project_root=str(_project(tmp_path)), confine=True)
    res = tool.execute({"pattern": "x", "root": "parse/missing.py"})
    assert not res.success
    assert "path not found" in (res.error or res.text or "")


def test_directory_search_unchanged(tmp_path):
    tool = GrepTool(project_root=str(_project(tmp_path)), confine=True)
    res = tool.execute({"pattern": "DEF PARSE", "root": "parse", "file_glob": "*.py"})
    assert res.success
    files = sorted((h["file"], h["line"]) for h in res.data["hits"])
    assert files == [("__init__.py", 3), ("util.py", 1)]


def test_refused_file_root_still_refused(tmp_path):
    tool = GrepTool(project_root=str(_project(tmp_path)), confine=True)
    res = tool.execute({"pattern": "SECRET", "root": ".env"})
    assert not res.success
    assert "Refused" in (res.error or res.text or "")


def test_outside_root_refused_even_if_missing(tmp_path):
    tool = GrepTool(project_root=str(_project(tmp_path)), confine=True)
    res = tool.execute({"pattern": "x", "root": "/nonexistent_dir_xyz/a.py"})
    assert not res.success
    assert "Refused" in (res.error or res.text or "")

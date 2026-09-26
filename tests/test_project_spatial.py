"""Tests for project spatial context and placement fixing."""

from pathlib import Path

from scaffold.agent.project_spatial import (
    build_directory_tree,
    build_spatial_context,
    fix_create_file_placement,
    fix_plan_placements,
    format_spatial_prompt_block,
    infer_artifact_folder,
    is_root_level_path,
    load_placement_conventions,
    prepare_plan_placements,
    validate_create_placement,
)


def test_directory_tree_includes_docs(tmp_path):
    (tmp_path / "docs" / "research").mkdir(parents=True)
    (tmp_path / "tasks" / "active").mkdir(parents=True)
    (tmp_path / "scaffold" / "agent").mkdir(parents=True)

    tree = build_directory_tree(tmp_path)
    assert "docs/" in tree
    assert "tasks/" in tree
    assert "scaffold/" in tree


def test_load_placement_from_agent_index():
    root = Path(__file__).resolve().parents[1]
    text = load_placement_conventions(root)
    assert "docs/research" in text or "Research" in text


def test_build_spatial_context_has_artifact_roots(tmp_path):
    (tmp_path / "docs" / "research").mkdir(parents=True)
    (tmp_path / "docs" / "specs").mkdir(parents=True)
    ctx = build_spatial_context(tmp_path)
    assert "directory_tree" in ctx
    assert "placement_conventions" in ctx
    assert "docs/research/" in ctx["artifact_roots"]


def test_format_spatial_prompt_block():
    block = format_spatial_prompt_block({
        "directory_tree": "proj/\n├── docs/",
        "artifact_roots": ["docs/"],
        "doc_samples_by_folder": {"docs": ["docs/a.md"]},
        "placement_conventions": "Research → docs/research/",
    })
    assert "PROJECT LAYOUT" in block
    assert "PLACEMENT CONVENTIONS" in block
    assert "docs/a.md" in block


def test_infer_artifact_folder_research():
    assert infer_artifact_folder("write a research note about RL") == "docs/research"
    assert infer_artifact_folder("create a spec for auth") == "docs/specs"
    assert infer_artifact_folder("how to guide for agents") == "docs"


def test_is_root_level_path():
    assert is_root_level_path("foo.md") is True
    assert is_root_level_path("docs/foo.md") is False


def test_fix_root_placement_to_docs(tmp_path):
    (tmp_path / "docs").mkdir()
    task = {
        "task_id": 1,
        "task_type": "create_file",
        "path": "uncensored_guide.md",
        "action": "write a guide about uncensored agents",
        "complexity": "low",
    }
    fixed = fix_create_file_placement(
        task,
        "create a guide about uncensored local agents",
        tmp_path,
    )
    assert fixed["path"] == "docs/uncensored_guide.md"
    assert fixed.get("_placement_fixed") is True


def test_fix_research_to_docs_research(tmp_path):
    (tmp_path / "docs" / "research").mkdir(parents=True)
    task = {
        "task_id": 1,
        "task_type": "create_file",
        "path": "rl_routing.html",
        "action": "research note on RL routing",
        "complexity": "low",
    }
    fixed = fix_create_file_placement(task, "research RL routing", tmp_path)
    assert fixed["path"] == "docs/research/rl_routing.html"


def test_does_not_move_nested_paths(tmp_path):
    (tmp_path / "docs").mkdir()
    task = {
        "task_id": 1,
        "task_type": "create_file",
        "path": "docs/already_nested.md",
        "complexity": "low",
    }
    fixed = fix_create_file_placement(task, "create guide", tmp_path)
    assert fixed["path"] == "docs/already_nested.md"
    assert "_placement_fixed" not in fixed


def test_fix_plan_placements_batch(tmp_path):
    (tmp_path / "docs").mkdir()
    tasks = fix_plan_placements([
        {"task_id": 1, "task_type": "create_file", "path": "a.md", "complexity": "low"},
        {"task_id": 2, "task_type": "create_file", "path": "docs/b.md", "complexity": "low"},
    ], "write a guide", tmp_path)
    assert tasks[0]["path"] == "docs/a.md"
    assert tasks[1]["path"] == "docs/b.md"


def test_validate_research_wrong_folder(tmp_path):
    (tmp_path / "docs" / "research").mkdir(parents=True)
    pv = validate_create_placement(
        "docs/wrong_folder.html",
        "write a research note about RL routing",
        codebase_root=tmp_path,
    )
    assert pv.artifact_kind == "research"
    assert pv.ok is False
    assert any("docs/research" in i for i in pv.issues)


def test_fix_wrong_folder_research(tmp_path):
    (tmp_path / "docs" / "research").mkdir(parents=True)
    task = {
        "task_id": 1,
        "task_type": "create_file",
        "path": "docs/rl_note.html",
        "action": "research note on RL",
        "complexity": "low",
    }
    from scaffold.agent.project_spatial import fix_wrong_folder_placement

    fixed = fix_wrong_folder_placement(task, "research RL routing", tmp_path)
    assert fixed["path"] == "docs/research/rl_note.html"


def test_forbidden_target_run_demo():
    pv = validate_create_placement(
        "run_awos_demo.py",
        "create a guide about agents",
    )
    assert pv.ok is False


def test_prepare_plan_placements_attaches_validation(tmp_path):
    (tmp_path / "docs" / "research").mkdir(parents=True)

    tasks = prepare_plan_placements([{
        "task_id": 1,
        "task_type": "create_file",
        "path": "docs/research/ok.html",
        "action": "research spatial placement",
        "complexity": "low",
    }], "research spatial placement", tmp_path)
    assert tasks[0].get("placement_ok") is True
    assert tasks[0].get("artifact_kind") == "research"


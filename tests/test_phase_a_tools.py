"""Tests for Phase A+B tools: focused_test, symbol_retrieval, bulk_inspect, lsp_check."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

# ── focused_test ────────────────────────────────────────────────────────────────

def test_focused_test_finds_direct_test_for_orchestrator():
    """focused_test should find the orchestrator test file."""
    result = subprocess.run(
        ["python3", "scripts/focused_test.py", "scaffold/agent/orchestrator.py", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert "file" in data
    assert isinstance(data["direct"], list)
    assert isinstance(data["impacted"], list)
    assert isinstance(data["command"], str)
    # Should find at least one relevant test
    assert len(data["command"]) > 0


def test_focused_test_finds_direct_test_for_planner():
    result = subprocess.run(
        ["python3", "scripts/focused_test.py", "scaffold/agent/planner.py", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert isinstance(data["command"], str)


def test_focused_test_handles_missing_file():
    result = subprocess.run(
        ["python3", "scripts/focused_test.py", "nonexistent.py", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


# ── symbol_retrieval ────────────────────────────────────────────────────────────

def test_symbol_retrieval_indexes_orchestrator():
    result = subprocess.run(
        ["python3", "scripts/symbol_retrieval.py", "Orchestrator", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["kind"] == "class"
    assert "orchestrator" in data["file"].lower()
    assert data["line"] > 0


def test_symbol_retrieval_indexes_planner():
    result = subprocess.run(
        ["python3", "scripts/symbol_retrieval.py", "Planner", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["kind"] == "class"
    assert "planner" in data["file"].lower()


def test_symbol_retrieval_indexes_react_worker():
    result = subprocess.run(
        ["python3", "scripts/symbol_retrieval.py", "ReActWorker", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["kind"] == "class"


def test_symbol_retrieval_missing_symbol():
    result = subprocess.run(
        ["python3", "scripts/symbol_retrieval.py", "ThisSymbolDoesNotExist", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0


# ── bulk_inspect ────────────────────────────────────────────────────────────────

def test_bulk_inspect_generates_symbols_script():
    result = subprocess.run(
        ["python3", "scripts/bulk_inspect.py", "symbols", "--pattern", "class Orchestrator"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "hermes_tools" in result.stdout
    assert "class Orchestrator" in result.stdout


def test_bulk_inspect_generates_imports_script():
    result = subprocess.run(
        ["python3", "scripts/bulk_inspect.py", "imports", "--module", "scaffold.agent.orchestrator"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "hermes_tools" in result.stdout


def test_bulk_inspect_generates_classes_script():
    result = subprocess.run(
        ["python3", "scripts/bulk_inspect.py", "classes"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "hermes_tools" in result.stdout


def test_bulk_inspect_generates_changed_files_script():
    result = subprocess.run(
        ["python3", "scripts/bulk_inspect.py", "changed_files"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "git diff" in result.stdout


def test_bulk_inspect_rejects_unknown_template():
    result = subprocess.run(
        ["python3", "scripts/bulk_inspect.py", "nonsense"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0


# ── lsp_check ───────────────────────────────────────────────────────────────────

def test_lsp_check_status():
    result = subprocess.run(
        ["python3", "scripts/lsp_check.py", "--status", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert "project_root" in data
    assert "language_servers" in data
    assert "recommendation" in data


def test_lsp_check_diagnostics_on_valid_file():
    result = subprocess.run(
        ["python3", "scripts/lsp_check.py", "--check", "scaffold/agent/orchestrator.py", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert "file" in data
    assert "diagnostics" in data
    assert "py_compile" in data["diagnostics"]


def test_lsp_check_diagnostics_on_missing_file():
    result = subprocess.run(
        ["python3", "scripts/lsp_check.py", "--check", "nonexistent.py", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert "error" in data


# ── .hermes.md ──────────────────────────────────────────────────────────────────

def test_hermes_dotfile_exists():
    path = Path(".hermes.md")
    assert path.is_file(), ".hermes.md must exist in repo root"
    content = path.read_text()
    assert "AWOS" in content or "awos" in content.lower()


# ── context packet (existing tool, smoke test) ──────────────────────────────────

def test_task_context_generates_packet():
    result = subprocess.run(
        ["python3", "scripts/task_context.py", "smoke test",
         "--task-file", "tasks/active/hermes_token_efficient_workflow.md",
         "--file", "scaffold/agent/orchestrator.py",
         "--max-chars", "3000"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) <= 3100  # budget tolerance: truncation marker may overshoot by a few chars
    assert "TASK" in result.stdout


# ── py_compile all new scripts ──────────────────────────────────────────────────

def test_all_new_scripts_compile():
    scripts = [
        "scripts/focused_test.py",
        "scripts/symbol_retrieval.py",
        "scripts/bulk_inspect.py",
        "scripts/lsp_check.py",
        "scripts/task_context.py",
        "scripts/session_response.py",
    ]
    for script in scripts:
        result = subprocess.run(
            ["python3", "-m", "py_compile", script],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"{script} does not compile: {result.stderr}"

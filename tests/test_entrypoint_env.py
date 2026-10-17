"""
Every entry point that resolves credentials must load .env first.

Credentials and AWOS_AGENT_MODEL normally live in .env — that is what
.env.example documents and what the setup script tells people to create. A
script that reads the environment without loading it does not fail loudly: it
falls back to a default model and prices itself against that wrong model, so
the run silently uses something other than what was configured.

bench_executors.py shipped with exactly that gap. This test generalises the
fix, so a future entry point cannot reintroduce it.
"""

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Reads the environment but is not a credential entry point.
EXEMPT = {"validate_bug_cases.py"}


def _calls_name(tree: ast.AST, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == name:
                return True
            if isinstance(func, ast.Attribute) and func.attr == name:
                return True
    return False


def _resolves_credentials(tree: ast.AST) -> bool:
    """True when the module builds a model client from the environment."""
    return _calls_name(tree, "build_client_from_env")


def _entry_points():
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        if path.name in EXEMPT:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        yield path, tree


class TestEntryPointsLoadEnv(unittest.TestCase):
    def test_credential_entry_points_load_dotenv(self):
        checked = []
        for path, tree in _entry_points():
            if not _resolves_credentials(tree):
                continue
            checked.append(path.name)
            with self.subTest(script=path.name):
                self.assertTrue(
                    _calls_name(tree, "load_dotenv"),
                    f"{path.name} resolves credentials from the environment but "
                    "never loads .env, so a key configured there is invisible to "
                    "it and it will silently use a different model.",
                )
        self.assertTrue(checked, "no credential entry points found to check")

    def test_the_two_known_entry_points_are_covered(self):
        """Guards the detector itself: if it stops matching, the test is empty."""
        names = {path.name for path, tree in _entry_points() if _resolves_credentials(tree)}
        self.assertIn("bench_executors.py", names)
        self.assertIn("check_backend.py", names)

    def test_dotenv_is_optional_not_a_hard_import(self):
        """A missing python-dotenv must not stop a script that has real env vars."""
        for path, tree in _entry_points():
            if not _resolves_credentials(tree):
                continue
            source = path.read_text(encoding="utf-8")
            with self.subTest(script=path.name):
                self.assertIn(
                    "except ImportError",
                    source,
                    f"{path.name} should tolerate python-dotenv being absent",
                )


class TestAwosCliLoadsEnv(unittest.TestCase):
    def test_awos_py_loads_dotenv(self):
        tree = ast.parse((REPO_ROOT / "awos.py").read_text(encoding="utf-8"))
        self.assertTrue(_calls_name(tree, "load_dotenv"))


if __name__ == "__main__":
    unittest.main()

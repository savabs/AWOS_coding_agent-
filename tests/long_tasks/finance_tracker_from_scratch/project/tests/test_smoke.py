"""Trivial starting tests. Add your own tests next to these."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_package_imports():
    import fintrack  # noqa: F401


def test_spec_present():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "fintrack" in readme and "SPEC" in readme


def test_standard_library_only():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in text

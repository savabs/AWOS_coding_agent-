"""
Tests for CI Discovery module.

Tests test failure discovery, parsing, and grouping on the wedge fixture.
"""

import pytest
from pathlib import Path

# Add scaffold/agent to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scaffold" / "agent"))

from ci_discovery import CIDiscovery, discover_ci_failures, group_failures_by_module


# ── Fixtures ──────────────────────────────────────────────────────────────

WEDGE_FIXTURE = Path(__file__).parent / "fixtures" / "wedge_v1" / "ci_rescue_sprint"


# ── Tests ─────────────────────────────────────────────────────────────────

class TestCIDiscovery:
    """Test CI failure discovery on wedge fixture."""

    def test_discover_wedge_fixture(self):
        """Discover failures on CI Rescue Sprint fixture (28 reds)."""
        if not WEDGE_FIXTURE.exists():
            pytest.skip("Wedge fixture not found")

        discovery = CIDiscovery(str(WEDGE_FIXTURE))
        result = discovery.discover()

        # Should find 28 failing tests
        assert result.failure_count == 28, f"Expected 28 failures, got {result.failure_count}"

        # Should find 6 modules affected
        assert len(result.modules_affected) == 6, f"Expected 6 modules, got {len(result.modules_affected)}"

        # Modules should include auth, billing, inventory, shipping, promo
        expected_modules = {"auth.py", "billing.py", "inventory.py", "shipping.py", "promo.py"}
        actual_modules = set(result.modules_affected)
        assert expected_modules.issubset(actual_modules), f"Missing modules: {expected_modules - actual_modules}"

    def test_discover_returns_structured_failures(self):
        """Each failure should have required fields."""
        if not WEDGE_FIXTURE.exists():
            pytest.skip("Wedge fixture not found")

        discovery = CIDiscovery(str(WEDGE_FIXTURE))
        result = discovery.discover()

        for failure in result.failures:
            assert failure.test_file, "test_file should not be empty"
            assert failure.test_name, "test_name should not be empty"
            assert failure.module, "module should not be empty"
            assert failure.module.endswith(".py"), f"module should end with .py: {failure.module}"

    def test_discover_empty_repo(self):
        """Discover on empty repo should return no failures."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            discovery = CIDiscovery(tmpdir)
            result = discovery.discover()

            # Should find no tests
            assert result.no_tests_found or result.failure_count == 0

    def test_group_failures_by_module(self):
        """Group failures by module should work correctly."""
        failures = [
            {"module": "auth.py", "test_name": "test_login", "error_msg": "fail1"},
            {"module": "auth.py", "test_name": "test_session", "error_msg": "fail2"},
            {"module": "billing.py", "test_name": "test_invoice", "error_msg": "fail3"},
        ]

        grouped = group_failures_by_module(failures)

        assert len(grouped) == 2
        assert len(grouped["auth.py"]) == 2
        assert len(grouped["billing.py"]) == 1

    def test_discover_ci_failures_convenience(self):
        """Convenience function should return list of dicts."""
        if not WEDGE_FIXTURE.exists():
            pytest.skip("Wedge fixture not found")

        failures = discover_ci_failures(str(WEDGE_FIXTURE))

        assert isinstance(failures, list)
        assert len(failures) == 28

        # Each failure should be a dict with required keys
        for f in failures:
            assert "module" in f
            assert "test_name" in f
            assert "test_file" in f
            assert "error_msg" in f


class TestModuleInference:
    """Test module inference from test file names."""

    def test_infer_module_from_test_file(self):
        """Should infer module from test file name."""
        if not WEDGE_FIXTURE.exists():
            pytest.skip("Wedge fixture not found")

        discovery = CIDiscovery(str(WEDGE_FIXTURE))
        result = discovery.discover()

        # Check that modules are inferred correctly
        for failure in result.failures:
            # test_auth.py → auth.py
            if "test_auth" in failure.test_file:
                assert failure.module == "auth.py", f"Expected auth.py, got {failure.module}"
            elif "test_billing" in failure.test_file:
                assert failure.module == "billing.py", f"Expected billing.py, got {failure.module}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

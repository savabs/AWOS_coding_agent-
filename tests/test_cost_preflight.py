"""
Tests for the cost preflight in scripts/bench_executors.py.

The preflight exists so a benchmark run states what it will cost before it
spends anything. Two properties matter more than the estimate's accuracy:

  * a free run (replaying cassettes) must never prompt or claim a cost
  * a paid run must not proceed unconfirmed in a non-interactive shell

  TestForecast   — the estimate, and where it says it came from
  TestConfirm    — the gate that stands between a command and a bill
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scaffold"))


def _load_bench():
    """
    Import bench_executors.py, which lives in scripts/ and is not a package.

    It must be registered in sys.modules before execution: @dataclass resolves
    its own module by name while the class body is being processed, and a
    module absent from sys.modules makes that lookup fail.
    """
    spec = importlib.util.spec_from_file_location(
        "bench_executors", REPO_ROOT / "scripts" / "bench_executors.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_executors"] = module
    spec.loader.exec_module(module)
    return module


bench = _load_bench()


def _write_cassette(directory: Path, name: str, turns: int, tokens_in: int, tokens_out: int):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(
        json.dumps(
            {
                "version": 1,
                "meta": {"dialect": "anthropic"},
                "turns": [
                    {
                        "key": f"k{i}",
                        "reply": {
                            "text": "",
                            "tool_calls": [],
                            "input_tokens": tokens_in // turns,
                            "output_tokens": tokens_out // turns,
                        },
                    }
                    for i in range(turns)
                ],
            }
        ),
        encoding="utf-8",
    )


CASES = [f"case_{i}" for i in range(8)]


class TestForecast(unittest.TestCase):
    def test_replaying_cassettes_is_free(self):
        forecast = bench.forecast_cost(
            CASES, {"agent"}, "claude-sonnet-4-6", "/tmp/anything", record=False, max_cost=None
        )
        self.assertTrue(forecast.free)
        self.assertEqual(forecast.estimated_usd, 0.0)
        self.assertIn("no API calls", forecast.basis)

    def test_live_run_estimates_from_assumptions_when_nothing_measured(self):
        forecast = bench.forecast_cost(
            CASES, {"agent"}, "claude-haiku-4-5", None, record=False, max_cost=None
        )
        self.assertFalse(forecast.free)
        self.assertGreater(forecast.estimated_usd, 0.0)
        self.assertIn("assumed", forecast.basis)

    def test_existing_cassettes_are_preferred_over_assumptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in CASES:
                _write_cassette(directory, name, turns=4, tokens_in=4000, tokens_out=400)

            measured = bench.forecast_cost(
                CASES, {"agent"}, "claude-haiku-4-5", str(directory), record=True, max_cost=None
            )
            assumed = bench.forecast_cost(
                CASES, {"agent"}, "claude-haiku-4-5", None, record=True, max_cost=None
            )

        self.assertIn("measured", measured.basis)
        self.assertEqual(measured.input_tokens, 4000 * len(CASES))
        self.assertNotEqual(measured.estimated_usd, assumed.estimated_usd)

    def test_too_few_cassettes_falls_back_to_assumptions(self):
        """A couple of stale cassettes should not stand in for the whole run."""
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_cassette(directory, CASES[0], turns=2, tokens_in=100, tokens_out=10)
            forecast = bench.forecast_cost(
                CASES, {"agent"}, "claude-haiku-4-5", str(directory), record=True, max_cost=None
            )
        self.assertIn("assumed", forecast.basis)

    def test_both_arms_cost_more_than_one(self):
        one = bench.forecast_cost(
            CASES, {"agent"}, "claude-haiku-4-5", None, record=True, max_cost=None
        )
        both = bench.forecast_cost(
            CASES, {"agent", "single"}, "claude-haiku-4-5", None, record=True, max_cost=None
        )
        self.assertGreater(both.estimated_usd, one.estimated_usd)

    def test_max_cost_gives_a_hard_ceiling(self):
        forecast = bench.forecast_cost(
            CASES, {"agent"}, "claude-haiku-4-5", None, record=True, max_cost=0.10
        )
        self.assertAlmostEqual(forecast.worst_case_usd, 0.10 * len(CASES))

    def test_no_max_cost_means_no_ceiling(self):
        forecast = bench.forecast_cost(
            CASES, {"agent"}, "claude-haiku-4-5", None, record=True, max_cost=None
        )
        self.assertIsNone(forecast.worst_case_usd)

    def test_costlier_model_forecasts_more(self):
        haiku = bench.forecast_cost(
            CASES, {"agent"}, "claude-haiku-4-5", None, record=True, max_cost=None
        )
        sonnet = bench.forecast_cost(
            CASES, {"agent"}, "claude-sonnet-4-6", None, record=True, max_cost=None
        )
        self.assertGreater(sonnet.estimated_usd, haiku.estimated_usd)

    def test_unknown_model_reports_zero_rather_than_guessing(self):
        forecast = bench.forecast_cost(
            CASES, {"agent"}, "some-unreleased-model", None, record=True, max_cost=None
        )
        self.assertEqual(forecast.estimated_usd, 0.0)
        self.assertFalse(forecast.free)

    def test_printing_a_forecast_does_not_raise(self):
        for cassette_dir, record in ((None, True), ("/tmp/x", False)):
            forecast = bench.forecast_cost(
                CASES, {"agent"}, "claude-haiku-4-5", cassette_dir, record, 0.1
            )
            bench.print_forecast(forecast)  # smoke: formatting handles both shapes


class _NotATty:
    """stdin as a pipe, the way CI runs things."""

    @staticmethod
    def isatty():
        return False


class TestConfirm(unittest.TestCase):
    def setUp(self):
        self._stdin = sys.stdin
        sys.stdin = _NotATty()

    def tearDown(self):
        sys.stdin = self._stdin

    def _forecast(self, usd, free=False):
        forecast = bench.CostForecast(
            basis="test", model="m", cases=1, arms={"agent"}
        )
        forecast.estimated_usd = usd
        forecast.free = free
        return forecast

    def test_free_run_proceeds_without_asking(self):
        self.assertTrue(confirm := bench.confirm_spend(self._forecast(0.0, free=True), False))
        self.assertTrue(confirm)

    def test_paid_run_refuses_unconfirmed_when_not_interactive(self):
        self.assertFalse(bench.confirm_spend(self._forecast(2.50), assume_yes=False))

    def test_yes_flag_authorises_a_paid_run(self):
        self.assertTrue(bench.confirm_spend(self._forecast(2.50), assume_yes=True))

    def test_trivial_estimate_does_not_nag(self):
        self.assertTrue(bench.confirm_spend(self._forecast(0.001), assume_yes=False))

    def test_unpriced_model_still_asks(self):
        """$0.00 from an unknown model is not the same as free."""
        self.assertFalse(bench.confirm_spend(self._forecast(0.0, free=False), assume_yes=False))

    def test_threshold_boundary_requires_confirmation(self):
        at_threshold = self._forecast(bench.CONFIRM_THRESHOLD_USD)
        self.assertFalse(bench.confirm_spend(at_threshold, assume_yes=False))


if __name__ == "__main__":
    unittest.main()

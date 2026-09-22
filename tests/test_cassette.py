"""
Tests for scaffold/agent/cassette.py and the cost controls in agent_loop.py.

These cover the cheap-testing path: record a run once, replay it forever with
no credentials, and never let a paid run exceed a stated ceiling.

  TestRecording       — a cassette captures replies and survives interruption
  TestReplay          — replay is exact, offline, and free
  TestDivergence      — a drifted run is reported, not silently mis-served
  TestWrapForCassette — env/argument wiring
  TestCostControls    — pricing and the per-run cap
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scaffold"))

from scaffold.agent.agent_loop import (
    AgentLoop,
    AnthropicToolClient,
    ModelReply,
    ToolCall,
    build_coding_registry,
    estimate_cost,
)
from scaffold.agent.cassette import (
    Cassette,
    CassetteMiss,
    RecordingClient,
    ReplayingClient,
    wrap_for_cassette,
)


class _CountingClient(AnthropicToolClient):
    """A real adapter's message shaping, with scripted replies and a call count."""

    def __init__(self, replies):
        super().__init__(client=None, model="claude-test")
        self._replies = list(replies)
        self.calls = 0

    def complete(self, system, messages, registry):
        self.calls += 1
        return self._replies.pop(0) if self._replies else ModelReply(text="done")


def _reply(*calls, text="", tokens=(10, 5)):
    return ModelReply(
        text=text,
        tool_calls=[ToolCall(id=f"t{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls)],
        input_tokens=tokens[0],
        output_tokens=tokens[1],
    )


def _project():
    d = Path(tempfile.mkdtemp())
    (d / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return d


def _script():
    return [
        _reply(("read_file", {"path": "calc.py"}), text="looking"),
        _reply(
            ("edit_file", {"path": "calc.py", "old_string": "a + b", "new_string": "a * b"}),
            text="editing",
        ),
        ModelReply(text="done", input_tokens=10, output_tokens=5),
    ]


class TestRecording(unittest.TestCase):
    def setUp(self):
        self.project = _project()
        self.registry = build_coding_registry(str(self.project))
        self.path = Path(tempfile.mkdtemp()) / "c.json"

    def test_records_every_turn(self):
        client = RecordingClient(_CountingClient(_script()), self.path)
        AgentLoop(self.registry, client).run("task")

        payload = json.loads(self.path.read_text())
        self.assertEqual(payload["version"], 1)
        self.assertEqual(len(payload["turns"]), 3)
        self.assertEqual(payload["turns"][0]["reply"]["tool_calls"][0]["name"], "read_file")

    def test_each_turn_has_a_distinct_key(self):
        AgentLoop(self.registry, RecordingClient(_CountingClient(_script()), self.path)).run("t")
        keys = [t["key"] for t in json.loads(self.path.read_text())["turns"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_saved_after_each_turn_not_just_at_the_end(self):
        """An interrupted run must still leave a usable partial cassette."""

        class Interrupting(_CountingClient):
            def complete(self, system, messages, registry):
                if self.calls >= 2:
                    raise RuntimeError("interrupted")
                return super().complete(system, messages, registry)

        client = RecordingClient(Interrupting(_script()), self.path)
        AgentLoop(self.registry, client).run("task")  # loop catches model errors

        # The client answers turns 1 and 2, then raises on turn 3. Both
        # completed turns must already be on disk: nothing flushes at the end.
        self.assertTrue(self.path.exists())
        self.assertEqual(len(json.loads(self.path.read_text())["turns"]), 2)

    def test_records_token_counts_for_costing(self):
        AgentLoop(self.registry, RecordingClient(_CountingClient(_script()), self.path)).run("t")
        self.assertEqual(Cassette.load(self.path).total_tokens, (30, 15))

    def test_cassette_holds_no_credentials(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-secret-value"
        try:
            AgentLoop(
                self.registry, RecordingClient(_CountingClient(_script()), self.path)
            ).run("t")
            self.assertNotIn("sk-secret-value", self.path.read_text())
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)


class TestReplay(unittest.TestCase):
    def setUp(self):
        self.record_project = _project()
        self.path = Path(tempfile.mkdtemp()) / "c.json"
        AgentLoop(
            build_coding_registry(str(self.record_project)),
            RecordingClient(
                _CountingClient(_script()), self.path, root=str(self.record_project)
            ),
        ).run("make add multiply")

    def test_replay_reproduces_the_run(self):
        project = _project()
        outcome = AgentLoop(
            build_coding_registry(str(project)),
            ReplayingClient(self.path, root=str(project)),
        ).run("make add multiply")

        self.assertTrue(outcome.success)
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(outcome.turns, 3)
        self.assertIn("a * b", (project / "calc.py").read_text())

    def test_replays_exactly_in_a_different_directory(self):
        """
        The benchmarking case: recorded in one checkout, replayed in a fresh
        one. Tool output embeds absolute paths, so without root normalisation
        every turn after the first misses and replay silently degrades to blind
        ordinal playback.
        """
        project = _project()
        self.assertNotEqual(str(project), str(self.record_project))

        client = ReplayingClient(self.path, root=str(project))
        AgentLoop(build_coding_registry(str(project)), client).run("make add multiply")
        self.assertEqual(client.misses, 0, "replay fell back instead of matching keys")

    def test_unnormalised_replay_in_a_foreign_directory_reports_misses(self):
        """Without a root, divergence is surfaced rather than hidden."""
        project = _project()
        client = ReplayingClient(self.path)  # no root
        AgentLoop(build_coding_registry(str(project)), client).run("make add multiply")
        self.assertGreater(client.misses, 0)

    def test_replay_needs_no_credentials(self):
        saved = {k: os.environ.pop(k, None) for k in
                 ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "AWOS_BASE_URL")}
        try:
            project = _project()
            outcome = AgentLoop(
                build_coding_registry(str(project)), ReplayingClient(self.path)
            ).run("make add multiply")
            self.assertTrue(outcome.success)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_replay_is_free(self):
        project = _project()
        outcome = AgentLoop(
            build_coding_registry(str(project)), ReplayingClient(self.path)
        ).run("make add multiply")
        self.assertEqual(outcome.cost_usd, 0.0)

    def test_missing_cassette_is_a_clear_error(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            ReplayingClient("/nonexistent/none.json")
        self.assertIn("record", str(ctx.exception).lower())


class TestDivergence(unittest.TestCase):
    """A run that no longer matches the recording must say so."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "c.json"
        AgentLoop(
            build_coding_registry(str(_project())),
            RecordingClient(_CountingClient(_script()), self.path),
        ).run("original goal")

    def test_strict_mode_raises_on_divergence(self):
        client = ReplayingClient(self.path, strict=True)
        with self.assertRaises(CassetteMiss) as ctx:
            client.complete("different system prompt", [{"role": "user", "content": "other"}], None)
        self.assertIn("diverged", str(ctx.exception))

    def test_lenient_mode_falls_back_in_order_and_counts_the_miss(self):
        client = ReplayingClient(self.path, strict=False)
        reply = client.complete("different", [{"role": "user", "content": "other"}], None)
        self.assertEqual(client.misses, 1)
        self.assertEqual(reply.tool_calls[0].name, "read_file")  # first recorded turn

    def test_exhausted_cassette_raises(self):
        client = ReplayingClient(self.path, strict=False)
        for _ in range(3):
            client.complete("x", [{"role": "user", "content": "y"}], None)
        with self.assertRaises(CassetteMiss) as ctx:
            client.complete("x", [{"role": "user", "content": "y"}], None)
        self.assertIn("exhausted", str(ctx.exception))


class TestWrapForCassette(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.pop(k, None)
                       for k in ("AWOS_CASSETTE", "AWOS_CASSETTE_MODE", "AWOS_CASSETTE_STRICT")}

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_returns_client_untouched_when_no_cassette_configured(self):
        inner = _CountingClient([])
        self.assertIs(wrap_for_cassette(inner), inner)

    def test_record_mode_wraps_for_recording(self):
        path = Path(tempfile.mkdtemp()) / "c.json"
        wrapped = wrap_for_cassette(_CountingClient([]), path=str(path), mode="record")
        self.assertIsInstance(wrapped, RecordingClient)

    def test_replay_mode_needs_no_client(self):
        path = Path(tempfile.mkdtemp()) / "c.json"
        Cassette(path=path, turns=[], meta={"dialect": "anthropic"}).save()
        self.assertIsInstance(wrap_for_cassette(None, path=str(path), mode="replay"), ReplayingClient)

    def test_recording_without_a_client_is_refused(self):
        with self.assertRaises(RuntimeError):
            wrap_for_cassette(None, path="/tmp/x.json", mode="record")

    def test_reads_configuration_from_environment(self):
        path = Path(tempfile.mkdtemp()) / "c.json"
        os.environ["AWOS_CASSETTE"] = str(path)
        os.environ["AWOS_CASSETTE_MODE"] = "record"
        self.assertIsInstance(wrap_for_cassette(_CountingClient([])), RecordingClient)

    def test_dialect_round_trips_through_the_cassette(self):
        path = Path(tempfile.mkdtemp()) / "c.json"
        Cassette(path=path, turns=[], meta={"dialect": "openai"}).save()
        self.assertEqual(wrap_for_cassette(None, path=str(path), mode="replay")._shape, "openai")


class TestCostControls(unittest.TestCase):
    def test_free_backends_price_at_zero(self):
        self.assertEqual(estimate_cost("replay", 1_000_000, 1_000_000), 0.0)
        self.assertEqual(estimate_cost("local", 1_000_000, 1_000_000), 0.0)

    def test_known_model_prices(self):
        # 1M in + 1M out on deepseek-chat = $0.14 + $0.28
        self.assertAlmostEqual(estimate_cost("deepseek-chat", 1_000_000, 1_000_000), 0.42, places=4)

    def test_unknown_model_is_not_guessed_at(self):
        self.assertEqual(estimate_cost("some-new-model", 1_000_000, 1_000_000), 0.0)

    def test_versioned_model_prices_like_its_family(self):
        self.assertAlmostEqual(
            estimate_cost("claude-sonnet-4-6-20260101", 1_000_000, 0), 3.00, places=4
        )

    def test_cost_cap_stops_the_run(self):
        project = _project()
        expensive = [
            _reply(("read_file", {"path": "calc.py"}), tokens=(500_000, 200_000))
            for _ in range(10)
        ]
        loop = AgentLoop(
            build_coding_registry(str(project)),
            _CountingClient(expensive),
            max_turns=10,
            max_cost_usd=2.00,
        )
        loop.client.model = "claude-sonnet-4-6"
        outcome = loop.run("task")

        self.assertEqual(outcome.stop_reason, "cost_cap")
        self.assertLess(outcome.turns, 10)
        self.assertGreaterEqual(outcome.cost_usd, 2.00)

    def test_cost_cap_still_trips_while_recording(self):
        # Recording is the one mode that spends real money; the wrapper must
        # not hide the model and price the run as a free replay.
        project = _project()
        inner = _CountingClient([
            _reply(("read_file", {"path": "calc.py"}), tokens=(500_000, 200_000))
            for _ in range(10)
        ])
        inner.model = "claude-sonnet-4-6"
        with tempfile.TemporaryDirectory() as tmp:
            recording = RecordingClient(inner, Path(tmp) / "c.json")
            outcome = AgentLoop(
                build_coding_registry(str(project)),
                recording,
                max_turns=10,
                max_cost_usd=2.00,
            ).run("task")

        self.assertEqual(outcome.stop_reason, "cost_cap")
        self.assertGreater(outcome.cost_usd, 0.0)

    def test_no_cap_means_no_cost_stop(self):
        project = _project()
        loop = AgentLoop(build_coding_registry(str(project)), _CountingClient(_script()))
        self.assertEqual(loop.run("task").stop_reason, "completed")

    def test_cap_read_from_environment(self):
        os.environ["AWOS_MAX_RUN_COST"] = "0.50"
        try:
            loop = AgentLoop(build_coding_registry("."), _CountingClient([]))
            self.assertEqual(loop.max_cost_usd, 0.50)
        finally:
            os.environ.pop("AWOS_MAX_RUN_COST", None)

    def test_unparseable_cap_is_ignored_not_fatal(self):
        os.environ["AWOS_MAX_RUN_COST"] = "cheap please"
        try:
            self.assertIsNone(AgentLoop(build_coding_registry("."), _CountingClient([])).max_cost_usd)
        finally:
            os.environ.pop("AWOS_MAX_RUN_COST", None)


if __name__ == "__main__":
    unittest.main()

"""
Tests for backend selection and price lookup in scaffold/agent/agent_loop.py.

An environment often holds several keys at once, so which backend runs must be
predictable rather than a matter of guessing the precedence. And a gateway
writes model ids its own way, which must not silently turn into a $0.00 cost
estimate — that reads as "free" in a preflight.

  TestProviderOrder   — precedence, and pinning it with AWOS_PROVIDER
  TestOpenRouter      — the gateway's requirements and optional headers
  TestPriceLookup     — id shapes a gateway produces still price correctly
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scaffold"))

from scaffold.agent.agent_loop import (
    OPENROUTER_BASE_URL,
    AnthropicToolClient,
    OpenAIToolClient,
    build_client_from_env,
    estimate_cost,
)

BACKEND_VARS = (
    "AWOS_BASE_URL",
    "AWOS_BASE_URL_KEY",
    "AWOS_PROVIDER",
    "AWOS_AGENT_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_SITE_URL",
    "OPENROUTER_APP_NAME",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
)


class _CleanEnv(unittest.TestCase):
    """Every test starts with no backend configured."""

    def setUp(self):
        self._saved = {name: os.environ.pop(name, None) for name in BACKEND_VARS}

    def tearDown(self):
        for name in BACKEND_VARS:
            os.environ.pop(name, None)
            if self._saved[name] is not None:
                os.environ[name] = self._saved[name]

    @staticmethod
    def _base_url(client):
        return str(getattr(client, "_client", None).base_url)


class TestProviderOrder(_CleanEnv):
    def test_nothing_configured_explains_the_options(self):
        with self.assertRaises(RuntimeError) as ctx:
            build_client_from_env()
        message = str(ctx.exception)
        self.assertIn("OPENROUTER_API_KEY", message)
        self.assertIn("AWOS_CASSETTE", message)

    def test_local_endpoint_wins_over_every_key(self):
        os.environ["AWOS_BASE_URL"] = "http://localhost:11434/v1"
        os.environ["OPENROUTER_API_KEY"] = "sk-or-x"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-x"
        client = build_client_from_env()
        self.assertIsInstance(client, OpenAIToolClient)
        self.assertIn("localhost", self._base_url(client))

    def test_openrouter_precedes_direct_vendor_keys(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or-x"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-x"
        os.environ["AWOS_AGENT_MODEL"] = "anthropic/claude-haiku-4.5"
        self.assertIn("openrouter", self._base_url(build_client_from_env()))

    def test_anthropic_used_when_it_is_the_only_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-x"
        self.assertIsInstance(build_client_from_env(), AnthropicToolClient)

    def test_deepseek_used_when_it_is_the_only_key(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-ds-x"
        self.assertIn("deepseek", self._base_url(build_client_from_env()))

    def test_provider_pin_overrides_the_order(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or-x"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-x"
        os.environ["AWOS_PROVIDER"] = "anthropic"
        self.assertIsInstance(build_client_from_env(), AnthropicToolClient)

    def test_provider_pin_is_case_insensitive(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-x"
        os.environ["AWOS_PROVIDER"] = "  Anthropic  "
        self.assertIsInstance(build_client_from_env(), AnthropicToolClient)

    def test_pinning_a_backend_without_its_key_says_so(self):
        """Silently falling through to another vendor would bill the wrong account."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-x"
        os.environ["AWOS_PROVIDER"] = "openrouter"
        with self.assertRaises(RuntimeError) as ctx:
            build_client_from_env()
        self.assertIn("AWOS_PROVIDER=openrouter", str(ctx.exception))

    def test_explicit_model_argument_beats_the_environment(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-x"
        os.environ["AWOS_AGENT_MODEL"] = "from-env"
        self.assertEqual(build_client_from_env("from-argument").model, "from-argument")


class TestOpenRouter(_CleanEnv):
    def setUp(self):
        super().setUp()
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"

    def test_requires_a_model_rather_than_guessing_one(self):
        """An invented id would fail as a confusing 404 on the first turn."""
        with self.assertRaises(RuntimeError) as ctx:
            build_client_from_env()
        message = str(ctx.exception)
        self.assertIn("vendor-prefixed", message)
        self.assertIn("openrouter.ai/models", message)

    def test_uses_the_openrouter_endpoint(self):
        os.environ["AWOS_AGENT_MODEL"] = "deepseek/deepseek-chat"
        client = build_client_from_env()
        self.assertIsInstance(client, OpenAIToolClient)
        self.assertIn("openrouter.ai", self._base_url(client))
        self.assertTrue(OPENROUTER_BASE_URL.startswith("https://"))

    def test_keeps_the_vendor_prefixed_model_id_intact(self):
        os.environ["AWOS_AGENT_MODEL"] = "anthropic/claude-haiku-4.5"
        self.assertEqual(build_client_from_env().model, "anthropic/claude-haiku-4.5")

    def test_attribution_headers_are_optional(self):
        os.environ["AWOS_AGENT_MODEL"] = "deepseek/deepseek-chat"
        build_client_from_env()  # no OPENROUTER_SITE_URL / APP_NAME set

    def test_attribution_headers_are_sent_when_configured(self):
        os.environ["AWOS_AGENT_MODEL"] = "deepseek/deepseek-chat"
        os.environ["OPENROUTER_SITE_URL"] = "https://example.test"
        os.environ["OPENROUTER_APP_NAME"] = "AWOS"
        headers = build_client_from_env()._client.default_headers
        self.assertEqual(headers.get("HTTP-Referer"), "https://example.test")
        self.assertEqual(headers.get("X-Title"), "AWOS")


class TestPriceLookup(unittest.TestCase):
    """A gateway's id must not silently price at zero."""

    def test_vendor_prefix_is_stripped(self):
        self.assertEqual(
            estimate_cost("deepseek/deepseek-chat", 1_000_000, 0),
            estimate_cost("deepseek-chat", 1_000_000, 0),
        )

    def test_dotted_version_matches_the_dashed_table_entry(self):
        """OpenRouter writes claude-haiku-4.5; the table says claude-haiku-4-5."""
        self.assertEqual(
            estimate_cost("anthropic/claude-haiku-4.5", 1_000_000, 0),
            estimate_cost("claude-haiku-4-5", 1_000_000, 0),
        )
        self.assertGreater(estimate_cost("anthropic/claude-haiku-4.5", 1_000_000, 0), 0)

    def test_dated_release_prices_like_its_family(self):
        self.assertEqual(
            estimate_cost("claude-sonnet-4-6-20260101", 1_000_000, 0),
            estimate_cost("claude-sonnet-4-6", 1_000_000, 0),
        )

    def test_genuinely_unknown_model_is_still_zero(self):
        """Better an obvious zero than a fabricated price."""
        self.assertEqual(estimate_cost("acme/brand-new-model", 1_000_000, 0), 0.0)

    def test_free_backends_remain_free(self):
        self.assertEqual(estimate_cost("replay", 5_000_000, 5_000_000), 0.0)
        self.assertEqual(estimate_cost("local", 5_000_000, 5_000_000), 0.0)

    def test_output_priced_separately_from_input(self):
        self.assertNotEqual(
            estimate_cost("claude-sonnet-4-6", 1_000_000, 0),
            estimate_cost("claude-sonnet-4-6", 0, 1_000_000),
        )


if __name__ == "__main__":
    unittest.main()

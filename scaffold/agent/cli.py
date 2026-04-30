#!/usr/bin/env python3
"""
cli.py — Entry point for the agent.

Usage:
    python scaffold/agent/cli.py --goal "research the best approach for X"
    python scaffold/agent/cli.py --goal "..." --debug

Environment variables:
    MYPROJECT_LLM_API_KEY     LLM API key
    MYPROJECT_LLM_MODEL       Model name (default: llama3-8b-8192)
    MYPROJECT_DEBUG           Enable debug logging (1/true/yes)
"""

import argparse
import logging
import sys
from pathlib import Path

# Allow running as a script from the project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agent.config import Settings
from agent.core import Orchestrator
from agent.memory import MemoryStore
from agent.tools import ToolRegistry


def setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def build_tool_registry() -> ToolRegistry:
    """
    Register all tools here.
    Import and register your tool implementations below.
    """
    registry = ToolRegistry()
    # Example:
    # from agent.tools.web_search import WebSearchTool
    # registry.register(WebSearchTool())
    return registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent CLI")
    parser.add_argument("--goal", required=True, help="Goal for the agent to achieve")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.debug:
        settings.debug = True

    setup_logging(settings.debug)

    # Validate settings
    errors = settings.validate()
    if errors:
        for e in errors:
            print(f"Config error: {e}", file=sys.stderr)
        # For development, continue anyway (API key may not be needed for all tools)

    memory = MemoryStore()
    tools = build_tool_registry()
    orchestrator = Orchestrator(settings=settings, memory=memory, tools=tools)

    print(f"Goal: {args.goal}")
    print("-" * 60)

    try:
        result = orchestrator.run(args.goal)
        print(result)
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
tools/base.py — Tool system: ABC, registry, and result envelope.

Design principles (from AWOS §4):
  - Single-responsibility: a tool does one thing
  - Uniform result envelope: success flag + text + data
  - Argument validation before execution
  - Observable: every call produces a loggable result
  - Idempotent where possible

Usage:
    registry = ToolRegistry()
    registry.register(MyTool())
    result = registry.execute("my_tool", {"arg": "value"})
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Uniform result envelope for all tool calls."""

    success: bool
    text: str  # Human-readable summary
    data: dict[str, Any] = field(default_factory=dict)  # Structured data payload
    error: str = ""  # Error message if success=False
    latency_ms: float = 0.0  # Execution time

    @classmethod
    def ok(cls, text: str, data: dict[str, Any] | None = None) -> "ToolResult":
        return cls(success=True, text=text, data=data or {})

    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        return cls(success=False, text="", error=error)


class Tool(ABC):
    """Abstract base for all agent tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool identifier (snake_case)."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-sentence description of what this tool does."""
        ...

    @property
    def parameters(self) -> dict[str, str]:
        """Parameter schema: {param_name: description}. Override to declare params."""
        return {}

    def validate(self, args: dict[str, Any]) -> list[str]:
        """
        Return list of validation errors.
        Called before execute(). Override to add custom validation.
        """
        errors = []
        for param_name in self.parameters:
            if param_name not in args:
                errors.append(f"Missing required parameter: '{param_name}'")
        return errors

    @abstractmethod
    def execute(self, args: dict[str, Any]) -> ToolResult:
        """Execute the tool with validated arguments."""
        ...

    def __call__(self, **kwargs: Any) -> ToolResult:
        """Convenience: call the tool with keyword arguments."""
        t0 = time.monotonic()
        errors = self.validate(kwargs)
        if errors:
            return ToolResult.fail(f"Validation failed: {'; '.join(errors)}")
        result = self.execute(kwargs)
        result.latency_ms = (time.monotonic() - t0) * 1000
        return result


class ToolRegistry:
    """Registry for all tools. Validates and dispatches calls."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def execute(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        """Validate args and execute a named tool. Returns ToolResult."""
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult.fail(f"Unknown tool: '{tool_name}'. Available: {list(self._tools)}")

        errors = tool.validate(args)
        if errors:
            return ToolResult.fail(f"Invalid args for '{tool_name}': {'; '.join(errors)}")

        t0 = time.monotonic()
        try:
            result = tool.execute(args)
        except Exception as exc:
            result = ToolResult.fail(f"Tool '{tool_name}' raised {type(exc).__name__}: {exc}")
        result.latency_ms = (time.monotonic() - t0) * 1000
        return result

    def list_tools(self) -> list[dict[str, str]]:
        """Return tool catalog (for LLM tool-use prompts)."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self._tools.values()
        ]

    def __len__(self) -> int:
        return len(self._tools)

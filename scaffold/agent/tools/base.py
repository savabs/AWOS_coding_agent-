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

    @property
    def required_parameters(self) -> list[str]:
        """
        Names the caller must supply, for the tool-calling schema.

        Most tools here list optional arguments in `parameters` too (read_file
        documents start_line/end_line; grep documents max_results) and narrow
        the real requirements by overriding `validate()`. Marking all of
        `parameters` required would force a model to pass every optional
        argument on every call, so the default asks `validate()` what it
        actually enforces: a name is required if omitting every argument
        produces an error mentioning it.

        Override this property directly for a tool whose validation messages do
        not name the offending parameter.
        """
        try:
            errors = " ".join(self.validate({}))
        except Exception:
            # A validate() that cannot survive empty input tells us nothing;
            # fall back to the conservative reading.
            return list(self.parameters)
        return [name for name in self.parameters if name in errors]

    @property
    def input_schema(self) -> dict[str, Any]:
        """
        JSON Schema for tool-calling APIs (Anthropic `input_schema`, OpenAI
        `function.parameters`).

        Derived from `parameters`, which describes arguments but carries no
        types, so everything defaults to string — correct for the existing
        tools, all of which take strings. Override this property for a tool
        needing richer types or nested objects.
        """
        return {
            "type": "object",
            "properties": {
                name: {"type": "string", "description": desc}
                for name, desc in self.parameters.items()
            },
            "required": self.required_parameters,
        }

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

    def anthropic_schemas(self) -> list[dict[str, Any]]:
        """Tool definitions in Anthropic Messages API form."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    def openai_schemas(self) -> list[dict[str, Any]]:
        """Tool definitions in OpenAI-compatible function-calling form."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in self._tools.values()
        ]

    def names(self) -> list[str]:
        """Registered tool names, in registration order."""
        return list(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

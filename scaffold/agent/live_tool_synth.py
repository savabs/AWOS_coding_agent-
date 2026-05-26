"""
LiveToolSynthesizer — on-the-fly Python tool synthesis during task failures.

When the worker fails on the same task pattern twice, a reflection prompt fires:
"Would writing a Python helper script prevent this class of error?"

If yes, the agent synthesises a small script, validates it (AST + sandbox run),
saves it to .awos/tools/custom_<hash>.py, and registers it in .awos/tools/index.json.

Future tasks with semantically similar descriptions get the tool's output injected
into the worker prompt as a [TOOL OUTPUT] block.

Feature 1B from tasks/active/true_self_learning_task.md.
Reference: Live-SWE-agent (arXiv:2511.13646)
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

_TOOLS_INDEX_FILE  = "index.json"
_TOOL_TIMEOUT_SECS = 5
_MAX_OUTPUT_CHARS  = 1000
_MIN_KEYWORD_OVERLAP = 0.4


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _keyword_overlap(text_a: str, text_b: str) -> float:
    """Jaccard-like overlap between word sets of two strings."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


@dataclass
class SynthesizedTool:
    """A synthesized Python helper tool persisted to .awos/tools/."""
    name: str
    description: str
    script_path: str
    trigger_pattern: str
    created_at: str
    use_count: int = 0


class LiveToolSynthesizer:
    """
    Synthesizes and registers Python helper tools on the fly during failures.

    Tools are persisted across sessions in .awos/tools/ and reused when
    future tasks match the trigger pattern by keyword overlap.
    """

    def __init__(
        self,
        tools_dir: str = ".awos/tools",
        cheap_call: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._tools_dir = Path(tools_dir)
        self._index_path = self._tools_dir / _TOOLS_INDEX_FILE
        self._cheap_call = cheap_call
        self._enabled = os.getenv("AWOS_LIVE_TOOLS", "").lower() == "true"

    # ── Public API ──────────────────────────────────────────────────────────

    def reflect(
        self,
        task: dict,
        error: str,
        attempt: int,
    ) -> Optional[SynthesizedTool]:
        """
        On attempt >= 2 failure: ask LLM if a helper tool would prevent this.

        Returns a SynthesizedTool if one was successfully synthesized and
        validated, None otherwise.
        """
        if not self._enabled or self._cheap_call is None:
            return None
        if attempt < 2:
            return None

        action = task.get("action", "")

        reflect_prompt = (
            f"A coding agent failed twice on this task: {action!r}\n"
            f"Error: {error[:300]!r}\n"
            f"Would writing a small Python helper script (called as a subprocess) "
            f"help prevent or diagnose this class of error?\n"
            f"Answer ONLY valid JSON: "
            f'{{ "should_create": true/false, "tool_purpose": "..." }}'
        )

        try:
            raw = self._cheap_call(reflect_prompt)
            decision = self._parse_reflect_decision(raw)
        except Exception as exc:
            logger.debug("[LiveToolSynth] reflect parse failed: %s", exc)
            return None

        if not decision.get("should_create"):
            return None

        tool_purpose = decision.get("tool_purpose", "")
        if not tool_purpose:
            return None

        return self._synthesize(action, error, tool_purpose)

    def find_relevant_tool(self, task: dict) -> Optional[SynthesizedTool]:
        """
        Find a persisted tool whose trigger_pattern overlaps with the task action.
        Returns the best match above _MIN_KEYWORD_OVERLAP, or None.
        """
        action = task.get("action", "")
        best_tool: Optional[SynthesizedTool] = None
        best_score = _MIN_KEYWORD_OVERLAP

        for tool in self.list_tools():
            score = _keyword_overlap(action, tool.trigger_pattern)
            if score > best_score:
                best_score = score
                best_tool = tool

        return best_tool

    def run_tool(self, tool: SynthesizedTool, task: dict) -> str:
        """
        Execute the tool script, passing the task dict as JSON on stdin.
        Returns stdout capped at _MAX_OUTPUT_CHARS. Never raises.
        """
        script = Path(tool.script_path)
        if not script.exists():
            logger.warning("[LiveToolSynth] tool script missing: %s", tool.script_path)
            return ""
        try:
            task_json = json.dumps(task)
            result = subprocess.run(
                [sys.executable, str(script)],
                input=task_json,
                capture_output=True,
                text=True,
                timeout=_TOOL_TIMEOUT_SECS,
            )
            output = (result.stdout or "").strip()
            if result.returncode != 0:
                logger.debug("[LiveToolSynth] tool exited %d: %s", result.returncode, result.stderr[:200])
                return ""
            self._increment_use_count(tool)
            return output[:_MAX_OUTPUT_CHARS]
        except subprocess.TimeoutExpired:
            logger.warning("[LiveToolSynth] tool timed out: %s", tool.name)
            return ""
        except Exception as exc:
            logger.debug("[LiveToolSynth] tool run error: %s", exc)
            return ""

    def list_tools(self) -> List[SynthesizedTool]:
        """Return all registered tools from the index."""
        if not self._index_path.exists():
            return []
        try:
            data = json.loads(self._index_path.read_text())
            return [SynthesizedTool(**entry) for entry in data]
        except Exception as exc:
            logger.debug("[LiveToolSynth] index read error: %s", exc)
            return []

    # ── Internal helpers ────────────────────────────────────────────────────

    def _synthesize(
        self, action: str, error: str, tool_purpose: str
    ) -> Optional[SynthesizedTool]:
        """Synthesize, validate, and persist a new tool. Returns None on failure."""
        tool_name = self._make_tool_name(tool_purpose)

        synth_prompt = (
            f"Write a Python script named {tool_name}.py that {tool_purpose}.\n"
            f"Context: This tool helps a coding agent that failed on: {action!r}\n"
            f"Error encountered: {error[:200]!r}\n\n"
            f"Requirements:\n"
            f"- The script reads a JSON task dict from stdin (use sys.stdin.read())\n"
            f"- It prints its analysis or result to stdout\n"
            f"- Include a clear one-line docstring at the top\n"
            f"- Be minimal: under 40 lines\n"
            f"- Handle errors gracefully (try/except, exit code 0 on partial failure)\n\n"
            f"Output ONLY the Python code, nothing else."
        )

        try:
            code = self._cheap_call(synth_prompt)
        except Exception as exc:
            logger.warning("[LiveToolSynth] synthesis call failed: %s", exc)
            return None

        code = self._strip_fences(code)

        if not self._validate_syntax(code):
            logger.info("[LiveToolSynth] synthesized tool failed syntax check")
            return None

        if not self._validate_runtime(code):
            logger.info("[LiveToolSynth] synthesized tool failed runtime check")
            return None

        return self._persist_tool(tool_name, code, tool_purpose, action)

    def _make_tool_name(self, purpose: str) -> str:
        slug = "".join(c if c.isalnum() else "_" for c in purpose.lower()[:30]).strip("_")
        h = hashlib.md5(purpose.encode()).hexdigest()[:6]
        return f"tool_{slug}_{h}"

    def _strip_fences(self, code: str) -> str:
        code = code.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:])
        if code.endswith("```"):
            code = "\n".join(code.split("\n")[:-1])
        return code.strip()

    def _validate_syntax(self, code: str) -> bool:
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False

    def _validate_runtime(self, code: str) -> bool:
        """Run the code with empty stdin in a subprocess with timeout."""
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".py", mode="w", delete=False
            ) as f:
                f.write(code)
                tmp_path = f.name
            result = subprocess.run(
                [sys.executable, tmp_path],
                input="{}",
                capture_output=True,
                text=True,
                timeout=_TOOL_TIMEOUT_SECS,
            )
            Path(tmp_path).unlink(missing_ok=True)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            return False

    def _persist_tool(
        self, name: str, code: str, purpose: str, trigger_pattern: str
    ) -> Optional[SynthesizedTool]:
        """Save script + register in index.json."""
        try:
            self._tools_dir.mkdir(parents=True, exist_ok=True)
            script_path = self._tools_dir / f"{name}.py"
            script_path.write_text(code)

            tool = SynthesizedTool(
                name=name,
                description=purpose[:200],
                script_path=str(script_path),
                trigger_pattern=trigger_pattern,
                created_at=_now_iso(),
                use_count=0,
            )
            self._register_tool(tool)
            logger.info("[LiveToolSynth] persisted new tool: %s", name)
            return tool
        except Exception as exc:
            logger.warning("[LiveToolSynth] persist failed: %s", exc)
            return None

    def _register_tool(self, tool: SynthesizedTool) -> None:
        existing = self.list_tools()
        existing_names = {t.name for t in existing}
        if tool.name not in existing_names:
            existing.append(tool)
        self._tools_dir.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(
            json.dumps([asdict(t) for t in existing], indent=2)
        )

    def _increment_use_count(self, tool: SynthesizedTool) -> None:
        tools = self.list_tools()
        for t in tools:
            if t.name == tool.name:
                t.use_count += 1
                break
        self._index_path.write_text(
            json.dumps([asdict(t) for t in tools], indent=2)
        )

    def _parse_reflect_decision(self, raw: str) -> dict:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])
        return json.loads(raw.strip())

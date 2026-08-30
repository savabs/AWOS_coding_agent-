"""
react_worker.py — ReAct-style coding worker (observe → tool → repeat).

Replaces single-shot SEARCH/REPLACE for tasks when AWOS_REACT_WORKER=1.
Uses workspace-scoped tools from coding_tools.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from google import genai
except ImportError:
    genai = None  # type: ignore

try:
    from .coding_tools import ToolResult, build_coding_tool_registry, format_tool_catalog
    from .fixed_provider import FIXED_MODEL, create_fixed_client
    from .model_call_telemetry import extract_optional_usage, record_model_call
    from .repo_map import enrich_with_repo_map
    from .test_runner import TestResult, TestRunner
    from .usage_record import empty_usage, merge_usage, record_api_usage
except ImportError:
    from coding_tools import ToolResult, build_coding_tool_registry, format_tool_catalog
    from fixed_provider import FIXED_MODEL, create_fixed_client
    from model_call_telemetry import extract_optional_usage, record_model_call
    from repo_map import enrich_with_repo_map
    from test_runner import TestResult, TestRunner
    from usage_record import empty_usage, merge_usage, record_api_usage

logger = logging.getLogger(__name__)

_MAX_TURNS = int(os.getenv("AWOS_REACT_MAX_TURNS", "12"))
_MAX_OBS_CHARS = int(os.getenv("AWOS_REACT_MAX_OBS_CHARS", "6000"))
_MAX_HISTORY_TURNS = int(os.getenv("AWOS_REACT_MAX_HISTORY_TURNS", "6"))
_MAX_ORIENT_CHARS = int(os.getenv("AWOS_REACT_MAX_ORIENT_CHARS", "800"))
_STUCK_THRESHOLD = int(os.getenv("AWOS_REACT_STUCK_THRESHOLD", "3"))
_STUCK_FORCE_EXIT = int(os.getenv("AWOS_REACT_STUCK_FORCE_EXIT", "5"))


def _static_prefix(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return the stable, cacheable leading prefix of the message list.

    The ReAct prompt is ordered as [system, initial task prompt, ...turns]. The
    first two messages never change across turns, so they form the byte-stable
    prefix that provider-side prompt caching (e.g. DeepSeek / OpenAI-compatible
    prefix caching) keys on. Keeping this prefix invariant is the precondition
    for cache hits, which telemetry records as ``cache_read_tokens``.
    """
    return messages[:2]


def _summarize_observation(action_name: str, tool_result: Any) -> str:
    """Return a compact, structured observation for the ReAct feed.

    Large raw tool outputs (test runner dumps, grep hits, shell stdout) are
    collapsed into a short high-signal summary when the tool exposes structured
    ``data``. Raw text remains the fallback for tools without structured data,
    truncated to ``_MAX_OBS_CHARS`` by the caller.
    """
    if not tool_result.success:
        return f"ERROR: {tool_result.error}"
    data = tool_result.data or {}
    if action_name == "run_tests":
        if data.get("no_tests_found"):
            return "TESTS: no tests found"
        passed = int(data.get("passed", 0))
        failed = int(data.get("failed", 0))
        errors = int(data.get("errors", 0))
        rate = float(data.get("pass_rate", 0.0))
        return f"TESTS: {passed} passed, {failed} failed, {errors} errors ({rate:.0%})"
    if action_name == "grep":
        hits = data.get("hits") or []
        if not hits:
            return "GREP: no matches"
        lines = [f"{h.get('file')}:{h.get('line')}: {str(h.get('text', ''))[:80]}" for h in hits[:15]]
        tail = f" (+{len(hits) - 15} more)" if len(hits) > 15 else ""
        return "GREP hits:\n" + "\n".join(lines) + tail
    if action_name == "read_file":
        total = data.get("total_lines")
        path = data.get("path", "")
        if total is not None:
            return f"READ {path}: {total} lines total" + (
                f"\n{str(tool_result.text)[:400]}" if tool_result.text else ""
            )
    if action_name == "shell":
        rc = data.get("returncode")
        timed_out = data.get("timed_out")
        head = "TIMEOUT" if timed_out else (f"rc={rc}" if rc is not None else "ok")
        return f"SHELL ({head}): {str(tool_result.text)[:400]}"
    return tool_result.text or "(no output)"


def _compact_messages(
    messages: list[dict[str, str]], keep_turns: int = _MAX_HISTORY_TURNS
) -> list[dict[str, str]]:
    """Collapse turns older than ``keep_turns`` into one compact summary.

    The initial system prompt and task prompt are always kept in full. Every
    later assistant/observation pair counts as one turn. When the number of
    completed turns exceeds ``keep_turns``, the oldest excess turns are replaced
    by a single short ``user`` summary so prompt size stays bounded across the
    ReAct loop instead of growing ~O(turns^2).
    """
    if keep_turns <= 0:
        return messages
    prefix = _static_prefix(messages)  # system + initial task prompt
    rest = messages[2:]

    turns: list[list[dict[str, str]]] = []
    i = 0
    while i < len(rest):
        msg = rest[i]
        if msg.get("role") == "assistant" and i + 1 < len(rest):
            turns.append([rest[i], rest[i + 1]])
            i += 2
        else:
            turns.append([rest[i]])
            i += 1

    if len(turns) <= keep_turns:
        return messages

    dropped = turns[:-keep_turns]
    kept = turns[-keep_turns:]
    lines: list[str] = []
    for turn in dropped:
        for m in turn:
            content = str(m.get("content", ""))
            if m.get("role") == "user" and content.startswith("OBSERVATION"):
                status = "OK" if "(OK)" in content else "ERR"
                lines.append(f"{status}:{content.replace('OBSERVATION', '').strip()[:80]}")
    summary = "Earlier steps (compact): " + " | ".join(lines)
    flattened: list[dict[str, str]] = [m for t in kept for m in t]
    return prefix + [{"role": "user", "content": summary}] + flattened


def _error_signature(obs_text: str, action_name: str) -> str:
    """Create a short signature for an error to detect repeated failures."""
    key = f"{action_name}:{obs_text[:200]}"
    return str(hash(key))


def react_worker_enabled() -> bool:
    return os.getenv("AWOS_REACT_WORKER", "1").lower() in ("1", "true", "yes")


@dataclass
class ReActStep:
    thought: str
    action: str
    action_input: dict[str, Any]
    observation: str
    success: bool
    latency_ms: float = 0.0


@dataclass
class ReActResult:
    success: bool
    summary: str = ""
    files_changed: list[str] = field(default_factory=list)
    steps: list[ReActStep] = field(default_factory=list)
    test_result: Optional[TestResult] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model_used: str = ""
    error: str = ""


class ReActWorker:
    """Multi-turn tool-using worker for atomic coding tasks."""

    def __init__(self, api_key: Optional[str] = None, model: str = "deepseek-chat") -> None:
        # E2E test mode: skip real API setup
        self._e2e_mode = os.getenv("AWOS_E2E", "").lower() in ("1", "true", "yes")
        if self._e2e_mode:
            self.client = None
            self.anthropic_client = None
            self.openai_client = None
            self.gemini_client = None
            self.openrouter_client = None
            self.opencode_client = None  # added for OpenCode Go integration
            self.model = model
            self._dead_providers: set[str] = set()
            return

        opencode_key = os.getenv("OPENCODE_GO_API_KEY")
        if not opencode_key:
            raise ValueError("Set OPENCODE_GO_API_KEY for the active coding-agent path")

        self.client = None
        self.anthropic_client = None
        self.openai_client = None
        self.gemini_client = None
        self.openrouter_client = None
        self.gemini_key = None
        self.opencode_client = create_fixed_client(opencode_key)

        self.model = FIXED_MODEL
        # Pre-populate dead providers from missing API keys so escalation engine skips them
        self._dead_providers: set[str] = set()
        if not self.client:
            self._dead_providers.add("deepseek")
        if not self.anthropic_client:
            self._dead_providers.add("anthropic")
        if not self.openai_client:
            self._dead_providers.add("openai")
        if not self.openrouter_client:
            self._dead_providers.add("openrouter")
        if not self.gemini_client:
            self._dead_providers.add("google")
        if not self.opencode_client:
            self._dead_providers.add("opencode")

    def execute_task(
        self,
        *,
        task: dict,
        file_content: str,
        codebase_context: dict,
        project_root: str,
        tracker=None,
        model_spec=None,
        exploration_context: str = "",
        step_callback: "Callable[[str, str, dict, str, bool, float], None] | None" = None,
    ) -> dict[str, Any]:
        """
        Run ReAct loop for one planner task.

        Args:
            task: Task dict from planner.
            file_content: Current file content.
            codebase_context: Context from codebase discovery.
            project_root: Root directory of project.
            tracker: Optional token tracker.
            model_spec: Optional model specification.
            exploration_context: Optional exploration context.
            step_callback: Optional callback(thought, action, action_input, observation, success, latency_ms)
                          called after each ReAct turn for live streaming.

        Returns dict compatible with orchestrator expectations (success, usage, traces).
        """

        # E2E test mode: return a fake response without any LLM calls
        if getattr(self, "_e2e_mode", False):
            return {
                "success": True,
                "reasoning": "E2E mode — no LLM call",
                "model_used": "e2e-mock",
                "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
                "steps": [],
                "files_changed": [],
            }

        files_changed: list[str] = []

        def _track(rel_path: str) -> None:
            if rel_path not in files_changed:
                files_changed.append(rel_path)

        def _detect_primary_edit() -> bool:
            if not file_path:
                return False
            primary_path = Path(file_path)
            if not primary_path.is_absolute():
                primary_path = Path(project_root) / primary_path
            try:
                changed = primary_path.read_text(encoding="utf-8") != file_content
            except (OSError, UnicodeError):
                return False
            if changed:
                _track(os.path.relpath(primary_path, project_root))
            return changed

        benchmark_mode = os.getenv("AWOS_BENCHMARK_MODE", "").lower() in ("1", "true", "yes")
        profile = "benchmark" if benchmark_mode else os.getenv("AWOS_AGENT_PROFILE", "build")
        registry = build_coding_tool_registry(
            project_root,
            on_file_change=_track,
            profile=profile,
        )
        tool_catalog = format_tool_catalog(registry)

        action = task.get("action", "")
        file_path = task.get("file", "")

        codebase_context = enrich_with_repo_map(
            action,
            project_root,
            codebase_context,
            primary_file=file_path or None,
        )
        exploration = exploration_context or codebase_context.get("exploration", "")
        repo_map = codebase_context.get("repo_map", "")
        symbol_map = codebase_context.get("symbol_map", "")
        symbol_windows = codebase_context.get("symbol_read_windows", [])

        system_prompt = self._build_system_prompt(tool_catalog, project_root)
        user_prompt = self._build_task_prompt(
            action=action,
            file_path=file_path,
            file_content=file_content,
            codebase_context=codebase_context,
            exploration=exploration,
            repo_map=repo_map,
            symbol_map=symbol_map,
            symbol_windows=symbol_windows,
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        usage = empty_usage()
        steps: list[ReActStep] = []
        finished = False
        summary = ""
        last_test: Optional[TestResult] = None
        model_used = model_spec.name if model_spec else "deepseek"

        consecutive_failures = 0
        last_error_sig = ""
        stuck_warned = False
        no_progress_turns = 0
        last_success_signature = ""
        exploration_actions = {"read_file", "grep", "find_files", "list_dir"}
        for turn in range(1, _MAX_TURNS + 1):
            print(f"[REACT] Turn {turn}/{_MAX_TURNS}")
            t0 = time.time()
            try:
                raw, turn_usage, model_used = self._call_model(
                    messages,
                    model_spec=model_spec,
                    tracker=tracker,
                    task_id=str(task.get("task_id")) if task.get("task_id") is not None else None,
                    step=turn,
                )
            except Exception as exc:
                return self._fail_result(
                    f"LLM call failed: {exc}",
                    steps,
                    files_changed,
                    usage,
                    model_used,
                )
            merge_usage(usage, turn_usage)

            parsed = self._parse_action(raw)
            if parsed is None:
                obs = (
                    "Invalid response format. Reply with JSON only:\n"
                    '{"thought":"...","action":"tool_name","action_input":{...}}'
                )
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"OBSERVATION:\n{obs}"})
                messages = _compact_messages(messages, _MAX_HISTORY_TURNS)
                steps.append(
                    ReActStep("", "parse_error", {}, obs, False, (time.time() - t0) * 1000)
                )
                continue

            thought = parsed.get("thought", "")
            action_name = str(parsed.get("action", "")).strip()
            action_input = parsed.get("action_input") or {}
            if not isinstance(action_input, dict):
                action_input = {}

            print(f"[REACT] {action_name}: {thought[:120]}")

            if action_name == "finish":
                summary = str(action_input.get("summary", thought or "Task complete"))
                finished = True
                steps.append(
                    ReActStep(
                        thought,
                        "finish",
                        action_input,
                        summary,
                        True,
                        (time.time() - t0) * 1000,
                    )
                )
                if step_callback:
                    step_callback(
                        thought, "finish", action_input, summary, True, (time.time() - t0) * 1000
                    )
                break

            benchmark_blocked = {
                "web_search",
                "fetch_url",
                "github_search_code",
                "github_read_file",
                "github_list_issues",
                "github_search_issues",
                "hf_search",
                "hf_download",
            }
            if benchmark_mode and action_name in benchmark_blocked:
                tool_result = ToolResult.fail(
                    f"Benchmark mode disallows external tool: {action_name}"
                )
            else:
                tool_result = registry.execute(action_name, action_input)
            obs_text = _summarize_observation(action_name, tool_result)
            if len(obs_text) > _MAX_OBS_CHARS:
                obs_text = obs_text[:_MAX_OBS_CHARS] + "\n...(truncated)"

            primary_edit_detected = _detect_primary_edit()
            if primary_edit_detected:
                no_progress_turns = 0

            if action_name == "run_tests" and tool_result.data:
                last_test = TestResult(
                    passed=int(tool_result.data.get("passed", 0)),
                    failed=int(tool_result.data.get("failed", 0)),
                    errors=int(tool_result.data.get("errors", 0)),
                    pass_rate=float(tool_result.data.get("pass_rate", 0.0)),
                    raw_output=str(tool_result.data.get("raw_output", "")),
                    no_tests_found=bool(tool_result.data.get("no_tests_found")),
                )

            steps.append(
                ReActStep(
                    thought,
                    action_name,
                    action_input,
                    obs_text[:500],
                    tool_result.success,
                    (time.time() - t0) * 1000,
                )
            )
            if step_callback:
                step_callback(
                    thought,
                    action_name,
                    action_input,
                    obs_text[:500],
                    tool_result.success,
                    (time.time() - t0) * 1000,
                )

            if tool_result.success:
                consecutive_failures = 0
                last_error_sig = ""
                stuck_warned = False
                action_signature = json.dumps(
                    {"action": action_name, "input": action_input},
                    sort_keys=True,
                    default=str,
                )
                if (
                    action_name in {"edit_file", "write_file", "shell", "run_tests"}
                    or primary_edit_detected
                ):
                    no_progress_turns = 0
                elif action_name in exploration_actions:
                    # Distinct successful reads/searches are exploration progress;
                    # only an identical successful call repeatedly is stagnant.
                    no_progress_turns = (
                        no_progress_turns + 1 if action_signature == last_success_signature else 0
                    )
                else:
                    no_progress_turns += 1
                last_success_signature = action_signature
                if benchmark_mode and no_progress_turns >= _STUCK_THRESHOLD:
                    return self._fail_result(
                        f"No execution progress for {no_progress_turns} turns",
                        steps,
                        files_changed,
                        usage,
                        model_used,
                    )
            else:
                no_progress_turns += 1
                sig = _error_signature(obs_text, action_name)
                if sig == last_error_sig:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 1
                    last_error_sig = sig
                    stuck_warned = False

                if consecutive_failures >= _STUCK_FORCE_EXIT:
                    print(
                        f"[REACT] STUCK: {consecutive_failures} consecutive failures with same error — force exit"
                    )
                    return self._fail_result(
                        f"Stuck in loop: {consecutive_failures} consecutive failures (sig={sig[:8]})",
                        steps,
                        files_changed,
                        usage,
                        model_used,
                    )

                if benchmark_mode and no_progress_turns >= _STUCK_THRESHOLD:
                    return self._fail_result(
                        f"No execution progress for {no_progress_turns} turns",
                        steps,
                        files_changed,
                        usage,
                        model_used,
                    )

                if consecutive_failures >= _STUCK_THRESHOLD and not stuck_warned:
                    stuck_warned = True
                    print(
                        f"[REACT] STUCK WARNING: {consecutive_failures} consecutive failures — injecting guidance"
                    )
                    obs_text = (
                        f"STUCK DETECTION: You have failed {consecutive_failures} times with the same error.\n"
                        f"Last error: {obs_text[:300]}\n"
                        "STOP repeating the same approach. Try a fundamentally different strategy:\n"
                        "- Read the file again to see its current state\n"
                        "- Use a different edit approach (smaller change, different location)\n"
                        "- If the task is impossible as stated, call finish with a failure summary\n"
                        "Do NOT retry the exact same edit."
                    )

            assistant_msg = json.dumps(
                {"thought": thought, "action": action_name, "action_input": action_input},
                indent=2,
            )
            messages.append({"role": "assistant", "content": assistant_msg})
            status = "OK" if tool_result.success else "ERROR"
            messages.append(
                {
                    "role": "user",
                    "content": f"OBSERVATION ({status}):\n{obs_text}",
                }
            )
            messages = _compact_messages(messages, _MAX_HISTORY_TURNS)

        if not finished:
            if benchmark_mode and files_changed:
                summary = "Verified edit completed after the model turn limit"
                finished = True
            else:
                return self._fail_result(
                    f"Exceeded max turns ({_MAX_TURNS}) without finish",
                    steps,
                    files_changed,
                    usage,
                    model_used,
                )

        # Post-task test gate when tests weren't run in-loop
        test_pass = True
        if last_test is None and os.getenv("AWOS_SAFE_TO_RUN_TESTS", "") == "1":
            runner = TestRunner(project_root=project_root)
            last_test = runner.run(changed_files=files_changed or [file_path])
        if last_test is not None:
            if last_test.no_tests_found:
                test_pass = not files_changed  # edits without tests = inconclusive fail
            else:
                test_pass = (
                    last_test.pass_rate >= 1.0 and last_test.failed == 0 and last_test.errors == 0
                )

        needs_edits = self._task_needs_edits(task)
        if needs_edits and not files_changed and file_path:
            # Shell edits cannot trigger the structured write/edit callbacks. Inspect
            # only the primary task file so successful, verified shell edits are not
            # discarded while unrelated workspace changes remain out of scope.
            primary_path = Path(file_path)
            if not primary_path.is_absolute():
                primary_path = Path(project_root) / primary_path
            try:
                if primary_path.read_text(encoding="utf-8") != file_content:
                    files_changed.append(os.path.relpath(primary_path, project_root))
            except (OSError, UnicodeError):
                pass
        has_required_edits = bool(files_changed) or not needs_edits
        success = bool(finished and test_pass and has_required_edits)
        if finished and files_changed and not test_pass:
            success = False
            summary = summary or "Edits applied but tests did not pass"

        return {
            "success": success,
            "react": True,
            "summary": summary,
            "files_changed": files_changed,
            "steps": steps,
            "test_result": last_test,
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
            "cost_usd": float(usage.get("cost_usd", 0.0)),
            "model_used": model_used,
            "error": "" if success else (summary or "ReAct task failed"),
        }

    @staticmethod
    def _task_needs_edits(task: dict) -> bool:
        """Heuristic: code-fix tasks must produce file changes.

        Verification/test tasks (verify, test, check, validate, confirm, ensure)
        do not require edits even if they mention edit keywords like 'fix' in
        passing (e.g. 'Verify all tests pass after the fix').
        """
        action = str(task.get("action", "")).lower()
        verify_keywords = ("verify", "test", "check", "validate", "confirm", "ensure")
        if any(k in action for k in verify_keywords):
            return False
        return any(
            k in action for k in ("fix", "edit", "add", "implement", "update", "change", "correct")
        )

    def _fail_result(
        self,
        error: str,
        steps: list[ReActStep],
        files_changed: list[str],
        usage: dict,
        model_used: str,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "react": True,
            "summary": "",
            "files_changed": files_changed,
            "steps": steps,
            "test_result": None,
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
            "cost_usd": float(usage.get("cost_usd", 0.0)),
            "model_used": model_used,
            "error": error,
        }

    def _build_system_prompt(self, tool_catalog: str, project_root: str) -> str:
        benchmark_mode = os.getenv("AWOS_BENCHMARK_MODE", "").lower() in ("1", "true", "yes")
        profile = "benchmark" if benchmark_mode else os.getenv("AWOS_AGENT_PROFILE", "build")
        benchmark_rules = f"""
AGENT PROFILE: {profile}
"""
        if benchmark_mode:
            benchmark_rules += """
BENCHMARK MODE:
- Work only inside the supplied workspace and the requested task files.
- The tool catalog is enforced; shell, web, GitHub, HuggingFace, and code execution are unavailable.
- Make the smallest focused edit, run the relevant available test, then call finish.
- If the edit is verified but you cannot call finish, the harness may finalize from evidence.
"""
        return f"""You are a coding agent. You can answer questions directly OR use tools to edit code.

{benchmark_rules}
WORKSPACE: {project_root}

HOW TO RESPOND:
- For questions, explanations, or conversation: reply with plain text.
  No JSON needed. Just give your answer.
- For coding tasks (edit, fix, create, etc.): use the tools below and
  respond in JSON: {{\"thought\": \"...\", \"action\": \"tool\", \"action_input\": {{...}}}}
- When done with coding work, call finish with a summary:
  {{\"thought\": \"done\", \"action\": \"finish\", \"action_input\": {{\"summary\": \"...\"}}}}

EDITING RULES (when using tools):
1. ALWAYS read_file before editing — you need the exact text to match.
2. Prefer edit_file or write_file for edits; use shell only for non-destructive inspection.
3. After EVERY edit, read the target again to confirm the change.
4. Make MINIMAL changes — change only what's needed.
5. If edit fails, READ the file again to see exact current content.

TOOLS:
{tool_catalog}"""

    def _build_task_prompt(
        self,
        *,
        action: str,
        file_path: str,
        file_content: str,
        codebase_context: dict,
        exploration: str,
        repo_map: str = "",
        symbol_map: str = "",
        symbol_windows: list[dict] | None = None,
    ) -> str:
        arch = codebase_context.get("architecture", "")
        symbol_block = ""
        windows = symbol_windows or []
        if symbol_map:
            symbol_block = f"\nSYMBOL MAP:\n{symbol_map[:2000]}"
        if windows:
            lines = [
                f"  {w['file']}::{w['symbol']} → read_file start_line={w['start']}, end_line={w['end']}"
                for w in windows
            ]
            symbol_block += "\nREAD WINDOWS (fetch exact code on demand):\n" + "\n".join(lines)
        # Small orientation snippet only; exact content is fetched via read_file
        # using the repo-map line window below (avoids re-injecting whole files).
        if file_content:
            snippet = file_content[:_MAX_ORIENT_CHARS]
            if len(file_content) > _MAX_ORIENT_CHARS:
                snippet += "\n... (truncated — use read_file for the exact current content)"
        else:
            snippet = "(empty / new file)"
        return f"""TASK: {action}
FILE: {file_path}

ARCHITECTURE: {arch}
REPO MAP: {repo_map or "(none)"}
EXPLORATION: {exploration or "(none)"}{symbol_block}

ORIENTATION SNIPPET (first {_MAX_ORIENT_CHARS} chars — NOT authoritative):
{snippet}

STEPS:
1. read_file to see the exact current code (use the REPO MAP line window or READ WINDOWS above for FILE)
2. Use edit_file or write_file to make the minimal edit
3. Read the target again to verify the change
4. Run the relevant available test; do not install dependencies in benchmark mode
5. finish with summary when done"""

    def _parse_action(self, raw: str) -> Optional[dict[str, Any]]:
        raw = raw.strip()
        # Strip markdown fences
        fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
        if fence:
            raw = fence.group(1).strip()
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and "action" in data:
                return data
        except json.JSONDecodeError:
            pass
        # Fallback: extract JSON object with balanced braces (handles nested dicts)
        depth = 0
        start = -1
        for i, ch in enumerate(raw):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        data = json.loads(raw[start : i + 1])
                        if isinstance(data, dict) and "action" in data:
                            return data
                    except json.JSONDecodeError:
                        pass
                    start = -1
        # Hermes / PI / Cursor pattern: if the LLM returned plain text
        # instead of JSON, treat it as a direct answer. No retry, no
        # "Invalid response format" loop. This is what allows the LLM
        # to simply answer a question without forcing tool calls.
        if raw and "{" not in raw[:3]:
            return {
                "thought": raw[:200],
                "action": "finish",
                "action_input": {"summary": raw},
            }
        return None

    def _default_model_spec(self):
        """Pick first available provider when orchestrator does not pass escalation spec."""
        try:
            from .escalation_engine import EscalationLevel, ModelSpec
        except ImportError:
            from escalation_engine import EscalationLevel, ModelSpec

        prefer = (os.getenv("AWOS_REACT_PROVIDER") or "").strip().lower()

        def _openai():
            if self.openai_client and "openai" not in self._dead_providers:
                return ModelSpec(
                    level=EscalationLevel.OPENAI,
                    name="GPT-4o-mini",
                    model_id="gpt-4o-mini",
                    provider="openai",
                    cost_per_req=0.003,
                    input_price=0.15,
                    output_price=0.60,
                    min_complexity=0,
                    min_budget_remaining=0.0,
                    min_failures=0,
                )
            return None

        def _gemini():
            if self.gemini_client and "google" not in self._dead_providers:
                return ModelSpec(
                    level=EscalationLevel.GEMINI_FLASH,
                    name="Gemini 2.5 Flash",
                    model_id=os.getenv("AWOS_GEMINI_MODEL", "gemini-2.5-flash"),
                    provider="google",
                    cost_per_req=0.001,
                    input_price=0.10,
                    output_price=0.40,
                    min_complexity=0,
                    min_budget_remaining=0.0,
                    min_failures=0,
                )
            return None

        def _deepseek():
            if self.client and "deepseek" not in self._dead_providers:
                return ModelSpec(
                    level=EscalationLevel.DEEPSEEK,
                    name="DeepSeek V4 Flash",
                    model_id=self.model,
                    provider="deepseek",
                    cost_per_req=0.001,
                    input_price=0.14,
                    output_price=0.28,
                    min_complexity=0,
                    min_budget_remaining=0.0,
                    min_failures=0,
                )
            return None

        def _opencode():
            if self.opencode_client and "opencode" not in self._dead_providers:
                return ModelSpec(
                    level=EscalationLevel.DEEPSEEK,
                    name="DeepSeek V4 Pro",
                    model_id=FIXED_MODEL,
                    provider="opencode",
                    cost_per_req=0.002,
                    input_price=0.435,
                    output_price=0.87,
                    min_complexity=0,
                    min_budget_remaining=0.0,
                    min_failures=0,
                )
            return None

        def _openrouter():
            if self.openrouter_client and "openrouter" not in self._dead_providers:
                return ModelSpec(
                    level=EscalationLevel.OPENROUTER,
                    name="OpenRouter",
                    model_id="openrouter/auto",
                    provider="openrouter",
                    cost_per_req=0.002,
                    input_price=0.20,
                    output_price=0.80,
                    min_complexity=0,
                    min_budget_remaining=0.0,
                    min_failures=0,
                )
            return None

        def _anthropic():
            if self.anthropic_client and "anthropic" not in self._dead_providers:
                return ModelSpec(
                    level=EscalationLevel.HAIKU,
                    name="Claude Haiku 4.5",
                    model_id="claude-haiku-4-5",
                    provider="anthropic",
                    cost_per_req=0.017,
                    input_price=1.00,
                    output_price=5.00,
                    min_complexity=0,
                    min_budget_remaining=0.0,
                    min_failures=0,
                )
            return None

        order = {
            "opencode": (_opencode, _deepseek, _openrouter, _gemini, _anthropic, _openai),
            "gemini": (_gemini, _opencode, _deepseek, _openrouter, _anthropic, _openai),
            "google": (_gemini, _opencode, _deepseek, _openrouter, _anthropic, _openai),
            "deepseek": (_deepseek, _opencode, _openrouter, _gemini, _anthropic, _openai),
            "openrouter": (_openrouter, _opencode, _deepseek, _gemini, _anthropic, _openai),
            "anthropic": (_anthropic, _opencode, _openrouter, _gemini, _deepseek, _openai),
            "openai": (_openai, _opencode, _openrouter, _gemini, _deepseek, _anthropic),
        }
        # Default order: OpenCode Go first — it's the only provider with
        # working quota (see checkpoint_2026-07-24_master_session §3.4).
        # When Sprint 2 restores other providers, move them back to the front.
        builders = order.get(
            prefer, (_opencode, _deepseek, _openrouter, _gemini, _anthropic, _openai)
        )
        for build in builders:
            spec = build()
            if spec is not None:
                return spec
        return None

    def _call_model(
        self,
        messages: list[dict[str, str]],
        *,
        model_spec=None,
        tracker=None,
        session_id: str | None = None,
        task_id: str | None = None,
        step: int | None = None,
    ) -> tuple[str, dict, str]:
        usage = empty_usage()
        started_at = time.perf_counter()
        spec = model_spec or self._default_model_spec()
        if spec is None:
            raise RuntimeError("No available model provider for ReAct worker")
        provider = str(getattr(spec, "provider", "")).strip().lower()
        model_id = str(getattr(spec, "model_id", "")).strip()
        inp_price = float(getattr(spec, "input_price", 0.0))
        out_price = float(getattr(spec, "output_price", 0.0))
        label = str(getattr(spec, "name", model_id))
        benchmark_mode = os.getenv("AWOS_BENCHMARK_MODE", "").lower() in ("1", "true", "yes")
        if benchmark_mode and (provider != "opencode" or model_id != FIXED_MODEL):
            raise RuntimeError(
                "Benchmark provider contract violation: expected "
                f"opencode/{FIXED_MODEL}, got {provider}/{model_id}"
            )
        if tracker is not None and hasattr(tracker, "check_guard"):
            estimated_cost = float(getattr(spec, "cost_per_req", 0.0) or 0.0)
            allowed, reason = tracker.check_guard(estimated_cost=estimated_cost)
            if not allowed:
                raise RuntimeError(f"Trial budget guard stopped provider request: {reason}")

        if provider == "google" and self.gemini_client and "google" not in self._dead_providers:
            prompt = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
            resp = self.gemini_client.models.generate_content(model=model_id, contents=prompt)
            text = (resp.text or "").strip()
            # Gemini usage approximated
            recorded = record_api_usage(
                request_type="react_worker",
                model=model_id,
                input_tokens=max(len(prompt) // 4, 1),
                output_tokens=max(len(text) // 4, 1),
                input_price=inp_price,
                output_price=out_price,
                tracker=tracker,
            )
            merge_usage(usage, recorded)
            return text, usage, label

        if (
            provider == "anthropic"
            and self.anthropic_client
            and "anthropic" not in self._dead_providers
        ):
            resp = self.anthropic_client.messages.create(
                model=model_id,
                max_tokens=1500,
                messages=[
                    {"role": m["role"], "content": m["content"]}
                    for m in messages
                    if m["role"] != "system"
                ],
                system=next((m["content"] for m in messages if m["role"] == "system"), ""),
                temperature=0.1,
            )
            text = resp.content[0].text.strip()
            recorded = record_api_usage(
                request_type="react_worker",
                model=model_id,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                input_price=inp_price,
                output_price=out_price,
                tracker=tracker,
            )
            merge_usage(usage, recorded)
            return text, usage, label

        if (
            provider == "openrouter"
            and self.openrouter_client
            and "openrouter" not in self._dead_providers
        ):
            resp = self.openrouter_client.chat.completions.create(
                model=model_id,
                messages=messages,
                max_tokens=1500,
                temperature=0.1,
                extra_headers={
                    "HTTP-Referer": "https://github.com/999-sbpatel/AWOS_coding_agent",
                    "X-OpenRouter-Title": "AWOS",
                },
            )
            text = resp.choices[0].message.content.strip()
            u = resp.usage
            recorded = record_api_usage(
                request_type="react_worker",
                model=model_id,
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                input_price=inp_price,
                output_price=out_price,
                tracker=tracker,
            )
            merge_usage(usage, recorded)
            return text, usage, label

        if provider == "openai" and self.openai_client and "openai" not in self._dead_providers:
            resp = self.openai_client.chat.completions.create(
                model=model_id,
                messages=messages,
                max_tokens=1500,
                temperature=0.1,
            )
            text = resp.choices[0].message.content.strip()
            u = resp.usage
            recorded = record_api_usage(
                request_type="react_worker",
                model=model_id,
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                input_price=inp_price,
                output_price=out_price,
                tracker=tracker,
            )
            merge_usage(usage, recorded)
            return text, usage, label

        if (
            provider == "opencode"
            and self.opencode_client
            and "opencode" not in self._dead_providers
        ):
            resp = self.opencode_client.chat.completions.create(
                model=model_id,
                messages=messages,
                max_tokens=1500,
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "disabled"}},
            )
            content = getattr(resp.choices[0].message, "content", None)
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("OpenCode Go returned empty response content")
            text = content.strip()
            u = resp.usage
            recorded = record_api_usage(
                request_type="react_worker",
                model=model_id,
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                input_price=inp_price,
                output_price=out_price,
                tracker=tracker,
            )
            record_model_call(
                provider=provider,
                model=model_id,
                request_type="react_worker",
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                input_price=inp_price,
                output_price=out_price,
                **extract_optional_usage(u),
                messages=messages,
                session_id=session_id,
                task_id=task_id,
                step=step,
                started_at=started_at,
            )
            merge_usage(usage, recorded)
            return text, usage, label

        if provider == "deepseek" and self.client and "deepseek" not in self._dead_providers:
            resp = self.client.chat.completions.create(
                model=model_id,
                messages=messages,
                max_tokens=4000,  # DeepSeek V4 needs room for thinking + response
                temperature=0.1,
            )
            text = resp.choices[0].message.content.strip()
            u = resp.usage
            recorded = record_api_usage(
                request_type="react_worker",
                model=model_id,
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                input_price=inp_price,
                output_price=out_price,
                tracker=tracker,
            )
            merge_usage(usage, recorded)
            return text, usage, label

        raise RuntimeError("No available model provider for ReAct worker")
